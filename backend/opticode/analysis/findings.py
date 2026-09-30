"""Dead-code and unreachable-code findings, explained from the solved data-flow results.

Liveness, reaching definitions, and constant propagation already run over every function.
This module turns what they know into *findings*: one object per thing the user could act
on, carrying a span, an action, and an explanation that quotes the data that decided it.

The three facts the rules are built from:

* **Unreachable code.** A block with no path from the function's entry block can never run.
  That is a fact about the graph, and :mod:`..cfg` already computes it.
* **Conditionally unreachable code.** A branch on a known constant takes exactly one of its
  two edges, so the other is infeasible. Removing every infeasible edge and walking again
  from the entry gives the set of blocks that are in the graph but cannot be entered.
  Constants come from a fixed point over *all* edges, which is sound rather than maximally
  precise — and soundness is what a warning needs.
* **Dead assignments.** ``dest ∉ live_out(i)`` is the dead-store test, and it is about the
  destination, not the operands. See :class:`~.results.LivenessFact` for why the other
  question is the useless one.

Every rule here is one-sided. A finding may only say "this can be deleted" when something
observed proves it, and "this is kept" whenever the deletion could be observable. Deleting a
statement deletes *all* the instructions lowered from it, so the side-effect question is
answered for the whole set: :func:`_producer_closure` walks reaching definitions backwards
from the dead instruction, and a call, a read, or a trapping division anywhere in that
closure keeps the statement.

Two distinctions are load-bearing and are kept apart everywhere below:

*Structural* versus *conditional* unreachability. Code after a ``return`` is unreachable for
good; code after ``while (1)`` is reachable only through an edge that is never taken. The
first is a bug in the program, the second is usually deliberate, and the explanations say so.

*Dead value* versus *dead statement*. ``x = 10;`` followed by ``x = 20;`` writes a value
nothing reads, but the statement is still removable. ``int q = bar();`` with ``q`` unused is
not removable, because the call is the point. The first is ``will_be_removed``; the second is
``kept``, with the reason naming the effect that saved it.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..cfg import CFG, BasicBlock, build_program_cfgs
from ..core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    RelatedInformation,
    Severity,
    Stage,
)
from ..core.spans import SourceSpan, union
from ..ir.tac import TRAPPING_OPS, Instruction, IRProgram, Opcode
from ..parser.ast import Node, statement_index, walk
from ..semantic.types import TypeName
from .reaching import DefinitionKind
from .results import BranchCondition, FunctionAnalysis

__all__ = [
    "AnalysisReport",
    "Evidence",
    "Finding",
    "FindingAction",
    "FindingKind",
    "analyze_findings",
    "diagnostics_for",
]


class FindingKind(StrEnum):
    """What a finding is about. The kind is the stable API key; the message is not."""

    #: No path from the function's entry block reaches the statement.
    UNREACHABLE_CODE = "unreachable_code"
    #: The block is in the graph, but every path into it needs an infeasible edge.
    CONDITIONALLY_UNREACHABLE = "conditionally_unreachable"
    #: An assignment whose value is never read before it is overwritten or the path ends.
    DEAD_ASSIGNMENT = "dead_assignment"
    #: A call whose result is unused. The result is dead; the call is not.
    UNUSED_CALL_RESULT = "unused_call_result"
    #: A ``read`` whose destination is never used. The input is consumed either way.
    UNUSED_READ_RESULT = "unused_read_result"
    #: A read whose only reaching definition is UNDEF.
    USE_BEFORE_INITIALIZATION = "use_before_initialization"
    #: A non-void, non-``main`` function with a feasible path to the implicit return.
    MISSING_RETURN = "missing_return"
    #: A branch on a constant, so one of its edges is never taken.
    CONSTANT_CONDITION = "constant_condition"
    #: A division or modulo by a literal zero.
    CONSTANT_DIVISION_BY_ZERO = "constant_division_by_zero"


#: The finding kinds whose instruction ids are dead values, whether or not the statement
#: that produced them survives. The other kinds describe code that runs.
_DEAD_KINDS = frozenset(
    {
        FindingKind.DEAD_ASSIGNMENT,
        FindingKind.UNUSED_CALL_RESULT,
        FindingKind.UNUSED_READ_RESULT,
    }
)


class FindingAction(StrEnum):
    """What an optimizer would do with the flagged code, if one ran."""

    #: The value is dead and deleting it is not observable.
    WILL_BE_REMOVED = "will_be_removed"
    #: The value is dead, but something about the statement must survive.
    KEPT = "kept"
    #: Nothing is dead; this is context about code that stays as it is.
    INFORMATIONAL = "informational"


@dataclass(frozen=True, slots=True)
class Evidence:
    """A secondary location that made the decision, with why it matters."""

    span: SourceSpan
    note: str

    def to_dict(self) -> dict[str, Any]:
        return {"span": self.span.to_dict(), "note": self.note}


@dataclass(frozen=True, slots=True)
class Finding:
    """One actionable fact about one function, with the reasoning attached.

    ``action`` says what an optimizer would do and ``kept_reason`` says why not, when the
    answer is that the code stays. ``duplicate_of`` names the diagnostic code of an earlier,
    coarser stage that already told the user this same thing, so this stage can leave it out
    of the diagnostics list while keeping the precise version here.
    """

    id: str
    kind: FindingKind
    severity: Severity
    function: str
    message: str
    explanation: str
    action: FindingAction
    span: SourceSpan = field(default_factory=SourceSpan)
    evidence: tuple[Evidence, ...] = ()
    instruction_ids: tuple[str, ...] = ()
    ast_node_id: str = ""
    block: str = ""
    kept_reason: str | None = None
    duplicate_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind.value,
            "severity": self.severity.value,
            "function": self.function,
            "block": self.block,
            "instruction_ids": list(self.instruction_ids),
            "ast_node_id": self.ast_node_id,
            "span": self.span.to_dict(),
            "message": self.message,
            "explanation": self.explanation,
            "evidence": [item.to_dict() for item in self.evidence],
            "action": self.action.value,
        }
        if self.kept_reason is not None:
            payload["kept_reason"] = self.kept_reason
        if self.duplicate_of is not None:
            payload["duplicate_of"] = self.duplicate_of
        return payload


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Every finding in the program, plus the two lists a later pass needs."""

    #: Findings sorted by position, then kind, then id, so the payload is deterministic.
    findings: tuple[Finding, ...] = ()
    #: Finding count per kind. Kinds with no findings are absent.
    summary: Mapping[str, int] = field(default_factory=dict)
    #: Unreachable blocks as ``"function:block"``, structural and conditional alike.
    unreachable_blocks: tuple[str, ...] = ()
    #: Instructions whose assigned value is dead, whether or not the statement is kept.
    dead_instruction_ids: tuple[str, ...] = ()

    def of_kind(self, kind: FindingKind) -> tuple[Finding, ...]:
        """Every finding of one kind."""
        return tuple(item for item in self.findings if item.kind is kind)

    def to_dict(self) -> dict[str, Any]:
        return {
            "findings": [item.to_dict() for item in self.findings],
            "summary": dict(sorted(self.summary.items())),
            "unreachable_blocks": list(self.unreachable_blocks),
            "dead_instruction_ids": list(self.dead_instruction_ids),
            "finding_count": len(self.findings),
        }


# ------------------------------------------------------------------ source text


@dataclass(frozen=True, slots=True)
class _SourceText:
    """Quotes the exact source behind a span, so explanations cite real code.

    Explanations are only trustworthy if they quote what the user wrote, so this slices the
    span out of the original text rather than re-rendering TAC — a temporary named ``t3``
    would be true of the IR and useless to a reader. Single-line spans slice exactly; a
    multi-line statement falls back to the rest of its first line, which is enough to
    locate it.
    """

    lines: tuple[str, ...] = ()

    @classmethod
    def of(cls, source: str) -> _SourceText:
        return cls(tuple(source.splitlines()))

    def quote(self, span: SourceSpan) -> str | None:
        """The source text of ``span``, or None when it cannot be sliced."""
        if not 1 <= span.start_line <= len(self.lines):
            return None
        line = self.lines[span.start_line - 1]
        if span.start_line != span.end_line:
            return line[span.start_col - 1 :].strip() or None
        return line[span.start_col - 1 : span.end_col - 1].strip() or None

    def render(self, span: SourceSpan, fallback: str) -> str:
        return self.quote(span) or fallback


# ------------------------------------------------------------------- context


@dataclass(frozen=True, slots=True)
class _Kill:
    """Why a block became unreachable: the branch whose infeasible edge it depended on."""

    branch: BranchCondition
    source: str


@dataclass(frozen=True, slots=True)
class _Context:
    """Everything the rules need about one function, indexed once."""

    cfg: CFG
    analysis: FunctionAnalysis
    text: _SourceText
    nodes: Mapping[str, Node]
    #: AST node id → the id of the statement containing it.
    statements: Mapping[str, str]
    #: Instruction id → instruction, over every block including unreachable ones.
    index: Mapping[str, Instruction]
    #: Instruction id → the block holding it.
    owner: Mapping[str, str]
    #: Every instruction in block layout order.
    order: tuple[Instruction, ...]
    #: Blocks reachable from the entry using only edges that can actually be taken.
    feasible: frozenset[str]
    #: Block id → the branch that made it infeasible. Empty for structural unreachability.
    killed_by: Mapping[str, _Kill]

    @property
    def name(self) -> str:
        return self.cfg.function.name

    def statement_of(self, instruction: Instruction) -> str:
        """The id of the source statement an instruction was lowered from."""
        return self.statements.get(instruction.ast_node_id, instruction.ast_node_id)

    def statement_span(self, members: tuple[Instruction, ...]) -> SourceSpan:
        return union(members[0].span, members[-1].span)

    def describe(self, instruction: Instruction, fallback: str) -> str:
        """The source text of an instruction, or a structural description of it."""
        return self.text.render(instruction.span, fallback)

    def at(self, instruction: Instruction) -> str:
        return f"line {instruction.span.start_line}"

    def runnable(self) -> tuple[Instruction, ...]:
        """Instructions that can actually run: feasible block, not compiler-inserted."""
        return tuple(
            instruction
            for instruction in self.order
            if self.owner[instruction.id] in self.feasible and not instruction.synthetic
        )


def _build_context(
    cfg: CFG,
    analysis: FunctionAnalysis,
    text: _SourceText,
    nodes: Mapping[str, Node],
    statements: Mapping[str, str],
) -> _Context:
    index: dict[str, Instruction] = {}
    owner: dict[str, str] = {}
    order: list[Instruction] = []
    for block in cfg.blocks:
        for instruction in block.instructions:
            index[instruction.id] = instruction
            owner[instruction.id] = block.id
            order.append(instruction)

    infeasible = _infeasible_edges(analysis, owner, {block.id for block in cfg.blocks})
    feasible = _feasible_blocks(cfg, infeasible)
    return _Context(
        cfg=cfg,
        analysis=analysis,
        text=text,
        nodes=nodes,
        statements=statements,
        index=index,
        owner=owner,
        order=tuple(order),
        feasible=feasible,
        killed_by=_attribute_kills(cfg, infeasible, feasible),
    )


def _infeasible_edges(
    analysis: FunctionAnalysis,
    owner: Mapping[str, str],
    known: set[str],
) -> dict[tuple[str, str], BranchCondition]:
    """Edges a constant condition proves are never taken.

    A branch on a known constant takes exactly one successor, so the *other* successor edge
    cannot be traversed. The edge is recorded per branch rather than per target, so two
    constant branches sharing a target stay distinguishable for the explanation.
    """
    infeasible: dict[tuple[str, str], BranchCondition] = {}
    for branch in analysis.branches:
        taken = branch.always_taken
        source = owner.get(branch.instruction_id)
        if taken is None or source is None:
            continue
        for target in (branch.false_target, branch.true_target):
            if target and target != taken and target in known:
                infeasible.setdefault((source, target), branch)
    return infeasible


def _feasible_blocks(
    cfg: CFG, infeasible: Mapping[tuple[str, str], BranchCondition]
) -> frozenset[str]:
    """Blocks reachable from the entry when infeasible edges are treated as absent.

    This is what separates structural from conditional unreachability: the walk starts from
    the entry over the *pruned* graph, so a block reached only through an edge that can never
    be taken never arrives, while a block the CFG called reachable and that still is — the
    ``return`` after ``while (1)`` when the loop has a ``break`` — stays.
    """
    known = {block.id for block in cfg.blocks}
    if cfg.entry not in known:
        return frozenset()
    seen = {cfg.entry}
    queue = deque([cfg.entry])
    while queue:
        node = queue.popleft()
        for successor in cfg.successors_of(node):
            if successor not in known or successor in seen:
                continue
            if (node, successor) in infeasible:
                continue
            seen.add(successor)
            queue.append(successor)
    return frozenset(seen)


def _attribute_kills(
    cfg: CFG,
    infeasible: Mapping[tuple[str, str], BranchCondition],
    feasible: frozenset[str],
) -> dict[str, _Kill]:
    """Give every conditionally unreachable block the branch responsible for it.

    A block can sit downstream of several infeasible edges, so the forward walk skips blocks
    that are still feasible and the first branch to reach a block keeps it. Branches are
    visited in sorted order so the attribution is the same on every run.
    """
    kills: dict[str, _Kill] = {}
    known = {block.id for block in cfg.blocks}
    for (source, target), branch in sorted(infeasible.items()):
        queue = deque([target])
        seen: set[str] = set()
        while queue:
            node = queue.popleft()
            if node in seen:
                continue
            seen.add(node)
            if node not in feasible:
                kills.setdefault(node, _Kill(branch=branch, source=source))
            for successor in cfg.successors_of(node):
                if successor in known and successor not in seen:
                    queue.append(successor)
    return kills


# ---------------------------------------------------------------- provenance


def _operand_origin(instruction: Instruction, use_index: int) -> str:
    """The AST node behind the ``use_index``-th non-constant operand, or "".

    :meth:`Instruction.uses` skips constants while :attr:`Instruction.operand_origin_ids` is
    aligned with every operand, so the two have to be walked together to line up.
    """
    seen = 0
    for position, operand in enumerate(instruction.operands):
        if operand.is_const:
            continue
        if seen == use_index:
            origins = instruction.operand_origin_ids
            return origins[position] if position < len(origins) else ""
        seen += 1
    return ""


def _origin_span(ctx: _Context, instruction: Instruction, use_index: int) -> SourceSpan:
    """The span of one operand, from the AST node behind it when that node is known."""
    node = ctx.nodes.get(_operand_origin(instruction, use_index))
    return node.span if node is not None else instruction.span


#: Opcodes that are pure control-flow glue rather than something the user wrote as work.
#: A statement whose instructions are *only* these is not dead code in the source sense: the
#: ``while`` in ``while (0) { ... }`` is still a real loop, it is the back edge that can
#: never be taken, and the constant-condition finding already says exactly that. Reporting
#: the jump as unreachable would put a warning on a loop the user never broke.
_CONTROL_ONLY_OPS = frozenset({Opcode.GOTO, Opcode.IF_FALSE})


def _statements(ctx: _Context, block: BasicBlock) -> list[tuple[str, tuple[Instruction, ...]]]:
    """The block's source statements, as ``(statement id, instructions)`` pairs.

    Instructions that no source statement produced are dropped: the compiler's implicit
    final return has no AST node and must never be reported as code the user wrote. The rest
    are grouped by the statement they came from and sorted into source order, so one
    statement yields exactly one finding no matter how many instructions it lowered to.
    Groups left with nothing but branches are dropped for the reason given above.
    """
    groups: dict[str, list[Instruction]] = {}
    for instruction in block.instructions:
        if instruction.synthetic:
            continue
        key = ctx.statement_of(instruction)
        groups.setdefault(key or f"@{instruction.id}", []).append(instruction)
    return sorted(
        (
            (key, tuple(members))
            for key, members in groups.items()
            if any(item.opcode not in _CONTROL_ONLY_OPS for item in members)
        ),
        key=lambda pair: (pair[1][0].span.start_line, pair[1][0].span.start_col),
    )


def _statement_index(ast: Node) -> dict[str, str]:
    """Map every AST node id to the id of the statement that contains it.

    Without an AST the map is empty and callers fall back to the raw node id, which is the
    right degradation: the analysis still runs, it just cannot merge instructions the tree
    would have tied together.
    """
    return statement_index(ast)


def _position(ctx: _Context, instruction_id: str) -> int:
    """Where an instruction sits in block layout order, or -1."""
    for index, instruction in enumerate(ctx.order):
        if instruction.id == instruction_id:
            return index
    return -1


def _next_definition(
    ctx: _Context, name: str, after: str, runnable: frozenset[str]
) -> Instruction | None:
    """The next instruction that redefines ``name`` in layout order, if any."""
    for instruction in ctx.order[_position(ctx, after) + 1 :]:
        if instruction.id not in runnable:
            continue
        if instruction.dest is not None and instruction.dest.name == name:
            return instruction
    return None


def _producer_closure(ctx: _Context, roots: frozenset[str]) -> frozenset[str]:
    """Every instruction that would be deleted along with ``roots``.

    Deleting a source statement deletes every instruction lowered from it, so the
    side-effect question cannot be answered from the dead instruction alone: ``int q =
    bar();`` has a dead *copy* but a live *call*, and the call is why the statement stays.
    Walking reaching definitions backwards from the roots collects exactly the instructions
    whose values feed them, which is the set that would disappear.
    """
    found = set(roots)
    queue = deque(roots)
    while queue:
        instruction = ctx.index[queue.popleft()]
        for index in range(len(instruction.uses())):
            for item in ctx.analysis.chains.definitions_of_use(instruction.id, index):
                if item.kind is not DefinitionKind.INSTRUCTION:
                    continue
                if item.instruction_id in found or item.instruction_id not in ctx.index:
                    continue
                found.add(item.instruction_id)
                queue.append(item.instruction_id)
    return frozenset(found)


# --------------------------------------------------------------- explanations


def _live_after(ctx: _Context, instruction: Instruction) -> str:
    """The live set after an instruction, rendered for a message."""
    facts = ctx.analysis.instructions.get(instruction.id)
    if facts is None:
        return "the live set after this instruction is not known"
    live = sorted(facts.liveness.live_out)
    if not live:
        return "nothing is live after this instruction"
    return "the live set after this instruction is {" + ", ".join(live) + "}"


def _condition_phrase(instruction: Instruction | None, branch: BranchCondition) -> str:
    """A clause naming the branch condition and the constant it holds.

    A literal or a variable names itself; a temporary is compiler-internal, so the
    expression behind it is described instead of leaking ``t2`` into a user-facing message.
    """
    operands = instruction.operands if instruction is not None else ()
    value = branch.value.value
    if not operands:
        return f"the condition `{branch.operand}` is the constant {value}"
    operand = operands[0]
    if operand.is_const:
        return f"the condition is the constant {operand.display}"
    if operand.is_var:
        return f"the condition `{operand.name}` is the constant {value}"
    return f"the condition expression is the constant {value}"


def _fallback_terminator(instruction: Instruction) -> str:
    """A structural description of a terminator whose source text is unavailable."""
    if instruction.opcode is Opcode.RETURN:
        return "return"
    if instruction.opcode is Opcode.GOTO:
        return f"goto {instruction.target}"
    return f"conditional jump on {instruction.target}"


# -------------------------------------------------------------------- rules


def _structural_unreachable(ctx: _Context) -> list[Finding]:
    """Statements in blocks with no path from the function's entry block.

    The CFG already decided these blocks are unreachable; what this adds is *why*, by naming
    the terminator that cut the path. That is the part a user cannot see by reading the
    source, and the part that tells them which statement to delete.
    """
    findings: list[Finding] = []
    for index, block in enumerate(ctx.cfg.blocks):
        if block.reachable:
            continue
        cut_block, cut = _cut_point(ctx.cfg, index)
        for ast_id, members in _statements(ctx, block):
            evidence: tuple[Evidence, ...] = ()
            if cut is not None and cut_block is not None:
                text = ctx.describe(cut, _fallback_terminator(cut))
                reason = (
                    f"control flow ends at `{text}` on {ctx.at(cut)} in block "
                    f"{cut_block.id}, so {block.id} is never entered"
                )
                evidence = (Evidence(cut.span, f"`{text}` ends the flow here"),)
            else:
                reason = (
                    f"no path leads from the entry block {ctx.cfg.entry} to {block.id}, "
                    f"so nothing in it ever runs"
                )
            findings.append(
                Finding(
                    id=f"{ctx.name}:{FindingKind.UNREACHABLE_CODE.value}:{ast_id}",
                    kind=FindingKind.UNREACHABLE_CODE,
                    severity=Severity.WARNING,
                    function=ctx.name,
                    message="unreachable code",
                    explanation=f"{reason}, and this statement can never run.",
                    action=FindingAction.WILL_BE_REMOVED,
                    span=ctx.statement_span(members),
                    evidence=evidence,
                    instruction_ids=tuple(item.id for item in members),
                    ast_node_id=_reportable_id(ast_id),
                    block=block.id,
                )
            )
    return findings


def _cut_point(cfg: CFG, target: int) -> tuple[BasicBlock | None, Instruction | None]:
    """The nearest earlier reachable block whose terminator stops the flow.

    Scanning backwards in layout order finds the terminator the user wrote *just above* the
    dead code, which is what makes the explanation actionable. A block that ends in a plain
    fall-through is skipped: it cannot be why anything after it is unreachable.
    """
    for block in reversed(cfg.blocks[:target]):
        if not block.reachable:
            continue
        terminator = block.terminator()
        if terminator is None or terminator.opcode not in (
            Opcode.RETURN,
            Opcode.GOTO,
            Opcode.IF_FALSE,
        ):
            continue
        return block, terminator
    return None, None


def _reportable_id(ast_id: str) -> str:
    """The AST node id, or "" for the synthetic key used when one instruction has none."""
    return "" if ast_id.startswith("@") else ast_id


def _conditional_unreachable(ctx: _Context) -> list[Finding]:
    """Blocks the graph contains but no run can enter, because a constant edge is missing.

    This is the ``while (1)`` family. The block is genuinely in the CFG — the false edge of
    the loop condition points at it — so only the constant fact separates "reachable" from
    "reachable in principle". The explanation quotes the condition, because that is what the
    user has to change if they meant to reach the code.
    """
    findings: list[Finding] = []
    for block in ctx.cfg.reachable_blocks:
        if block.id in ctx.feasible:
            continue
        kill = ctx.killed_by.get(block.id)
        for ast_id, members in _statements(ctx, block):
            evidence: tuple[Evidence, ...] = ()
            if kill is None:
                reason = (
                    f"{block.id} is in the graph but cannot be entered from "
                    f"{ctx.cfg.entry}"
                )
            else:
                branch = kill.branch
                instruction = ctx.index.get(branch.instruction_id)
                taken = branch.always_taken or "the other successor"
                value = branch.value.value
                where = ctx.at(instruction) if instruction is not None else "this branch"
                reason = (
                    f"{_condition_phrase(instruction, branch)}, so the branch on {where} "
                    f"always transfers to {taken}; {block.id} is reached only through the "
                    f"edge from {kill.source} that is never taken"
                )
                if instruction is not None:
                    evidence = (
                        Evidence(
                            instruction.span,
                            f"the condition is the constant {value} at this branch",
                        ),
                    )
            findings.append(
                Finding(
                    id=f"{ctx.name}:{FindingKind.CONDITIONALLY_UNREACHABLE.value}:{ast_id}",
                    kind=FindingKind.CONDITIONALLY_UNREACHABLE,
                    severity=Severity.WARNING,
                    function=ctx.name,
                    message="conditionally unreachable code",
                    explanation=f"{reason}, so this statement never runs.",
                    action=FindingAction.WILL_BE_REMOVED,
                    span=ctx.statement_span(members),
                    evidence=evidence,
                    instruction_ids=tuple(item.id for item in members),
                    ast_node_id=_reportable_id(ast_id),
                    block=block.id,
                )
            )
    return findings


def _constant_conditions(ctx: _Context) -> list[Finding]:
    """Branches whose condition constant propagation proved, one info finding each.

    The condition is not a bug, so these are informational. They are still worth surfacing:
    a condition that never varies is either a mistake or a loop that should be written as
    one, and it is the same fact the conditional-unreachability rule acts on.
    """
    findings: list[Finding] = []
    for branch in ctx.analysis.branches:
        if not branch.statically_decidable:
            continue
        instruction = ctx.index.get(branch.instruction_id)
        if instruction is None:
            continue
        truth = "true" if branch.value.value else "false"
        skipped = branch.false_target if truth == "true" else branch.true_target
        findings.append(
            Finding(
                id=f"{ctx.name}:{FindingKind.CONSTANT_CONDITION.value}:{branch.instruction_id}",
                kind=FindingKind.CONSTANT_CONDITION,
                severity=Severity.INFO,
                function=ctx.name,
                message=f"condition is always {truth}",
                explanation=(
                    f"{_condition_phrase(instruction, branch)} at the branch on "
                    f"{ctx.at(instruction)}, so {skipped} is never entered from here."
                ),
                action=FindingAction.INFORMATIONAL,
                span=_origin_span(ctx, instruction, 0),
                evidence=(Evidence(instruction.span, f"the condition is {truth} here"),),
                instruction_ids=(branch.instruction_id,),
                ast_node_id=ctx.statement_of(instruction),
                block=ctx.owner[branch.instruction_id],
            )
        )
    return findings


def _dead_assignments(ctx: _Context) -> list[Finding]:
    """Assignments whose value no read ever observes, grouped by source statement.

    Liveness decides the candidate set: ``dest ∉ live_out(i)``. Everything after that is
    about not being wrong:

    * the statement is the unit of deletion, so its instructions are grouped together;
    * the producer closure decides whether deleting it would remove a call, a read, or a
      division that can trap, and if so the finding becomes ``kept`` with that reason;
    * an assignment is only called "overwritten" when a read of the name exists that is not
      one of its own. With no such read the honest description is "never read", and the
      evidence says which of the two it is rather than asserting one.
    """
    runnable = frozenset(item.id for item in ctx.runnable())
    readers: dict[str, set[str]] = {}
    for instruction in ctx.runnable():
        for operand in instruction.uses():
            readers.setdefault(operand.name, set()).add(instruction.id)

    candidates: dict[tuple[str, str], list[Instruction]] = {}
    for block in ctx.cfg.blocks:
        if block.id not in ctx.feasible:
            continue
        for instruction in block.instructions:
            if instruction.synthetic or instruction.dest is None:
                continue
            facts = ctx.analysis.instructions.get(instruction.id)
            if facts is None or facts.liveness.dest_live:
                continue
            key = (instruction.ast_node_id or f"@{instruction.id}", block.id)
            candidates.setdefault(key, []).append(instruction)

    findings: list[Finding] = []
    for (ast_id, block_id), members in sorted(
        candidates.items(),
        key=lambda pair: (pair[1][0].span.start_line, pair[1][0].span.start_col),
    ):
        members.sort(key=lambda item: _position(ctx, item.id))
        findings.append(
            _dead_assignment_finding(ctx, members, ast_id, block_id, readers, runnable)
        )
    return findings


def _dead_assignment_finding(
    ctx: _Context,
    members: list[Instruction],
    ast_id: str,
    block_id: str,
    readers: Mapping[str, set[str]],
    runnable: frozenset[str],
) -> Finding:
    """Turn one group of dead instructions into one statement-level finding."""
    dest = members[0].dest
    if dest is None:  # pragma: no cover - candidates only hold instructions with a dest
        raise AssertionError("dead assignment candidates always define a destination")
    name = dest.name
    span = ctx.statement_span(tuple(members))
    instruction_ids = tuple(item.id for item in members)
    own = set(instruction_ids)
    # A read by this very group — `x = x` — is not evidence that anything else reads it.
    is_read_later = any(
        reader not in own for reader in readers.get(name, ())
    )

    if blocking := _blocking_effect(ctx, members):
        kind, reason, note, closure = blocking
        return Finding(
            id=f"{ctx.name}:{kind.value}:{ast_id}",
            kind=kind,
            severity=Severity.INFO,
            function=ctx.name,
            message=_kept_message(kind, name),
            explanation=(
                f"{_dead_explanation(ctx, name, block_id, members[0], is_read_later)}, but "
                f"the statement is kept: {note}."
            ),
            action=FindingAction.KEPT,
            span=span,
            evidence=_blocking_evidence(ctx, kind, closure),
            instruction_ids=instruction_ids,
            ast_node_id=_reportable_id(ast_id),
            block=block_id,
            kept_reason=reason,
        )

    if is_read_later:
        return Finding(
            id=f"{ctx.name}:{FindingKind.DEAD_ASSIGNMENT.value}:{ast_id}",
            kind=FindingKind.DEAD_ASSIGNMENT,
            severity=Severity.WARNING,
            function=ctx.name,
            message=f"the value assigned to `{name}` is overwritten before it is read",
            explanation=(
                f"{_dead_explanation(ctx, name, block_id, members[0], True)}; the value "
                f"written here is discarded, so the assignment can be deleted."
            ),
            action=FindingAction.WILL_BE_REMOVED,
            span=span,
            evidence=_overwrite_evidence(ctx, name, members, runnable),
            instruction_ids=instruction_ids,
            ast_node_id=_reportable_id(ast_id),
            block=block_id,
        )

    return Finding(
        id=f"{ctx.name}:{FindingKind.DEAD_ASSIGNMENT.value}:{ast_id}",
        kind=FindingKind.DEAD_ASSIGNMENT,
        severity=Severity.WARNING,
        function=ctx.name,
        message=f"the value assigned to `{name}` is never read",
        explanation=(
            f"{_dead_explanation(ctx, name, block_id, members[0], False)}; the whole "
            f"assignment can be deleted."
        ),
        action=FindingAction.WILL_BE_REMOVED,
        span=span,
        evidence=(Evidence(span, f"nothing reads `{name}` after this statement on any path"),),
        instruction_ids=instruction_ids,
        ast_node_id=_reportable_id(ast_id),
        block=block_id,
        # A named variable that is never read anywhere is exactly what W-SEM-002 reports, so
        # this finding stays in the report but is not repeated as a diagnostic.
        duplicate_of="W-SEM-002" if dest.is_var else None,
    )


def _dead_explanation(
    ctx: _Context, name: str, block_id: str, instruction: Instruction, is_read_later: bool
) -> str:
    """The shared first sentence of every dead-assignment explanation."""
    head = (
        f"`{name}` is written in {block_id} on {ctx.at(instruction)} and "
        f"{_live_after(ctx, instruction)}"
    )
    if is_read_later:
        return (
            f"{head}, so every later read of `{name}` is reached only after another "
            f"assignment to it"
        )
    return f"{head}, and no later instruction reads `{name}` on any path"


#: Checked in order, so a trapping division outranks a call and a call outranks a read: the
#: strongest reason is the one worth putting in the message.
_BLOCKING_PRIORITY = (
    ("trap", FindingKind.DEAD_ASSIGNMENT, "the right-hand side may fail at runtime"),
    (Opcode.CALL, FindingKind.UNUSED_CALL_RESULT, "the call is kept because it has side effects"),
    (Opcode.READ, FindingKind.UNUSED_READ_RESULT, "the read is kept because it consumes input"),
)


def _blocking_effect(
    ctx: _Context, members: list[Instruction]
) -> tuple[FindingKind, str, str, frozenset[str]] | None:
    """The strongest reason the statement must survive, or None if it need not.

    Returns the closure it searched alongside the answer, so the evidence lookup does not
    have to walk the def-use relation a second time.
    """
    closure = _producer_closure(ctx, frozenset(item.id for item in members))
    for opcode, kind, reason in _BLOCKING_PRIORITY:
        for instruction_id in sorted(closure):
            instruction = ctx.index[instruction_id]
            if instruction.synthetic:
                continue
            matches = instruction.may_trap() if opcode == "trap" else instruction.opcode is opcode
            if matches:
                return kind, reason, _blocking_note(instruction, kind), closure
    return None


def _is_call(instruction: Instruction) -> bool:
    return instruction.opcode is Opcode.CALL


def _is_read(instruction: Instruction) -> bool:
    return instruction.opcode is Opcode.READ


def _blocking_note(instruction: Instruction, kind: FindingKind) -> str:
    if kind is FindingKind.UNUSED_CALL_RESULT:
        return f"this call to `{instruction.callee}` runs for its effect, not its result"
    if kind is FindingKind.UNUSED_READ_RESULT:
        destination = instruction.dest.name if instruction.dest is not None else "?"
        return f"this read into `{destination}` consumes input whether or not it is used"
    return (
        f"this `{instruction.op}` can trap, so deleting the statement would remove a "
        f"runtime check"
    )


def _blocking_evidence(
    ctx: _Context, kind: FindingKind, closure: frozenset[str]
) -> tuple[Evidence, ...]:
    """Evidence pointing at the instruction that keeps the statement alive."""
    predicate = {
        FindingKind.DEAD_ASSIGNMENT: Instruction.may_trap,
        FindingKind.UNUSED_CALL_RESULT: _is_call,
        FindingKind.UNUSED_READ_RESULT: _is_read,
    }[kind]
    for instruction_id in sorted(closure):
        instruction = ctx.index[instruction_id]
        if not instruction.synthetic and predicate(instruction):
            return (Evidence(instruction.span, _blocking_note(instruction, kind)),)
    return ()


def _kept_message(kind: FindingKind, name: str) -> str:
    if kind is FindingKind.UNUSED_CALL_RESULT:
        return "the result of this call is never used, but the call is kept"
    if kind is FindingKind.UNUSED_READ_RESULT:
        return f"the value read into `{name}` is never used, but the read is kept"
    return f"the value assigned to `{name}` is never read, but the statement is kept"


def _overwrite_evidence(
    ctx: _Context, name: str, members: list[Instruction], runnable: frozenset[str]
) -> tuple[Evidence, ...]:
    """Evidence for the overwritten case: where the value written here is replaced."""
    following = _next_definition(ctx, name, members[-1].id, runnable)
    if following is not None:
        return (
            Evidence(
                following.span,
                f"`{name}` is reassigned here, before any read can observe the value "
                f"written on {ctx.at(members[0])}",
            ),
        )
    return (
        Evidence(
            ctx.statement_span(tuple(members)),
            f"the next read of `{name}` is reached only after a later assignment",
        ),
    )


def _use_before_initialization(ctx: _Context) -> list[Finding]:
    """Reads whose only reaching definition is UNDEF, from the def-use chains.

    Reaching definitions already separate "no write reached here" from "a write reached here
    on some path", so this needs no extra solving: a read listed by
    :meth:`DefUseChains.uninitialized_reads` is provably reading whatever the memory held
    before the function started.
    """
    findings: list[Finding] = []
    for instruction_id, use_index in ctx.analysis.chains.uninitialized_reads():
        instruction = ctx.index.get(instruction_id)
        if instruction is None or instruction.synthetic:
            continue
        uses = instruction.uses()
        if use_index >= len(uses):
            continue
        name = uses[use_index].name
        findings.append(
            Finding(
                id=(
                    f"{ctx.name}:{FindingKind.USE_BEFORE_INITIALIZATION.value}"
                    f":{instruction_id}#{use_index}"
                ),
                kind=FindingKind.USE_BEFORE_INITIALIZATION,
                severity=Severity.WARNING,
                function=ctx.name,
                message=f"`{name}` is read before it has a value",
                explanation=(
                    f"no assignment of `{name}` reaches this read: its only reaching "
                    f"definition is UNDEF({name}), so the value is whatever the memory held "
                    f"before {ctx.name} was called."
                ),
                action=FindingAction.INFORMATIONAL,
                span=_origin_span(ctx, instruction, use_index),
                evidence=(
                    Evidence(
                        instruction.span,
                        f"the read of `{name}` on {ctx.at(instruction)} has no definition",
                    ),
                ),
                instruction_ids=(instruction_id,),
                ast_node_id=_operand_origin(instruction, use_index),
                block=ctx.owner[instruction_id],
            )
        )
    return findings


def _missing_return(ctx: _Context) -> list[Finding]:
    """Non-void functions with a feasible path to the compiler's implicit return.

    ``main`` is exempt because reaching the end of ``main`` is the normal way to leave a
    program. A function that never returns is exempt for a different reason: ``while (1)``
    with no ``break`` leaves the implicit return unreachable, and warning about the result
    of a return that can never happen would be noise.
    """
    function = ctx.cfg.function
    if function.return_type is TypeName.VOID or function.name == "main":
        return []
    for block in ctx.cfg.blocks:
        if block.id not in ctx.feasible:
            continue
        terminator = block.terminator()
        if terminator is None or terminator.opcode is not Opcode.RETURN:
            continue
        if not terminator.synthetic:
            continue
        return [
            Finding(
                id=f"{ctx.name}:{FindingKind.MISSING_RETURN.value}:{terminator.id}",
                kind=FindingKind.MISSING_RETURN,
                severity=Severity.WARNING,
                function=ctx.name,
                message=(
                    f"function '{ctx.name}' can reach the end without returning "
                    f"{function.return_type.value}"
                ),
                explanation=(
                    f"control reaches the implicit return at the end of {block.id} without "
                    f"an explicit return, so the {function.return_type.value} result is "
                    f"undefined."
                ),
                action=FindingAction.INFORMATIONAL,
                span=terminator.span,
                evidence=(
                    Evidence(terminator.span, "control falls off the end of the function here"),
                ),
                instruction_ids=(terminator.id,),
                block=block.id,
                duplicate_of="W-SEM-003",
            )
        ]
    return []


def _constant_division_by_zero(ctx: _Context) -> list[Finding]:
    """Divisions and moduli by a literal zero, which abort on every run."""
    findings: list[Finding] = []
    for block in ctx.cfg.blocks:
        if block.id not in ctx.feasible:
            continue
        for instruction in block.instructions:
            if instruction.synthetic or instruction.opcode is not Opcode.BINARY:
                continue
            if instruction.op not in TRAPPING_OPS or len(instruction.operands) != 2:
                continue
            left, right = instruction.operands
            if not right.is_zero:
                continue
            rendered = f"{left.display} {instruction.op} {right.display}"
            findings.append(
                Finding(
                    id=(
                        f"{ctx.name}:{FindingKind.CONSTANT_DIVISION_BY_ZERO.value}"
                        f":{instruction.id}"
                    ),
                    kind=FindingKind.CONSTANT_DIVISION_BY_ZERO,
                    severity=Severity.WARNING,
                    function=ctx.name,
                    message=f"division by zero: `{rendered}` always fails at runtime",
                    explanation=(
                        f"the divisor is the literal 0, so `{rendered}` aborts every time it "
                        f"runs; deleting the statement would remove a guaranteed failure."
                    ),
                    action=FindingAction.KEPT,
                    span=instruction.span,
                    evidence=(
                        Evidence(instruction.span, "the divisor is the constant 0 here"),
                    ),
                    instruction_ids=(instruction.id,),
                    ast_node_id=ctx.statement_of(instruction),
                    block=block.id,
                    kept_reason="removing it would remove a guaranteed runtime error",
                    duplicate_of="W-SEM-004",
                )
            )
    return findings


# ---------------------------------------------------------------- entry points


def analyze_findings(
    program: IRProgram,
    *,
    cfgs: Mapping[str, CFG] | None = None,
    analyses: Mapping[str, FunctionAnalysis] | None = None,
    ast: Node | None = None,
    source: str = "",
) -> AnalysisReport:
    """Every dead-code and unreachable-code finding in ``program``.

    ``analyses`` lets a caller pass the :class:`FunctionAnalysis` objects the data-flow stage
    already built — the pipeline does exactly this, so the findings cost no extra solving.
    ``ast`` and ``source`` are optional and only make explanations better: the AST resolves
    operand spans and the source lets a message quote the statement rather than describe it
    in terms of temporaries.
    """
    from . import analyze_cfg

    graphs = dict(cfgs) if cfgs is not None else build_program_cfgs(program)
    solved = dict(analyses) if analyses is not None else {}
    nodes = {node.id: node for node in walk(ast)} if ast is not None else {}
    statements = _statement_index(ast) if ast is not None else {}
    text = _SourceText.of(source)

    findings: list[Finding] = []
    unreachable: set[str] = set()
    dead_instructions: set[str] = set()

    for name in sorted(graphs):
        analysis = solved.get(name)
        if analysis is None:
            analysis = analyze_cfg(graphs[name])
        ctx = _build_context(graphs[name], analysis, text, nodes, statements)

        found = [
            *_structural_unreachable(ctx),
            *_conditional_unreachable(ctx),
            *_constant_conditions(ctx),
            *_dead_assignments(ctx),
            *_use_before_initialization(ctx),
            *_missing_return(ctx),
            *_constant_division_by_zero(ctx),
        ]
        findings.extend(found)
        for item in found:
            if item.kind in (FindingKind.UNREACHABLE_CODE, FindingKind.CONDITIONALLY_UNREACHABLE):
                unreachable.add(f"{name}:{item.block}")
            elif item.kind in _DEAD_KINDS:
                dead_instructions.update(item.instruction_ids)

    ordered = tuple(
        sorted(
            findings,
            key=lambda item: (
                item.span.start_line,
                item.span.start_col,
                item.kind.value,
                item.id,
            ),
        )
    )
    summary: dict[str, int] = {}
    for item in ordered:
        summary[item.kind.value] = summary.get(item.kind.value, 0) + 1
    return AnalysisReport(
        findings=ordered,
        summary=summary,
        unreachable_blocks=tuple(sorted(unreachable)),
        dead_instruction_ids=tuple(sorted(dead_instructions)),
    )


#: One diagnostic code per finding kind. The ``I-ANL`` codes are informational, so an editor
#: shows them without adding to the warning count.
_DIAGNOSTIC_CODES: Mapping[FindingKind, DiagnosticCode] = {
    FindingKind.UNREACHABLE_CODE: DiagnosticCode.E400_UNREACHABLE_CODE,
    FindingKind.CONDITIONALLY_UNREACHABLE: DiagnosticCode.E401_CONDITIONALLY_UNREACHABLE,
    FindingKind.DEAD_ASSIGNMENT: DiagnosticCode.E402_DEAD_ASSIGNMENT,
    FindingKind.UNUSED_CALL_RESULT: DiagnosticCode.E403_UNUSED_CALL_RESULT,
    FindingKind.UNUSED_READ_RESULT: DiagnosticCode.E404_UNUSED_READ_RESULT,
    FindingKind.USE_BEFORE_INITIALIZATION: DiagnosticCode.E405_USE_BEFORE_INIT,
    FindingKind.MISSING_RETURN: DiagnosticCode.E406_MISSING_RETURN,
    FindingKind.CONSTANT_CONDITION: DiagnosticCode.E407_CONSTANT_CONDITION,
    FindingKind.CONSTANT_DIVISION_BY_ZERO: DiagnosticCode.E408_DIVISION_BY_ZERO,
}


def diagnostics_for(report: AnalysisReport) -> list[Diagnostic]:
    """Turn findings into diagnostics, one per finding.

    A finding carrying ``duplicate_of`` is left out: an earlier, coarser stage already told
    the user the same thing, and repeating it would put two warnings on one line. The
    precise version is still in the ``analysis`` payload, which is where a UI that wants the
    evidence looks.
    """
    produced: list[Diagnostic] = []
    for finding in report.findings:
        if finding.duplicate_of is not None:
            continue
        produced.append(
            Diagnostic(
                message=finding.message,
                code=_DIAGNOSTIC_CODES[finding.kind],
                severity=finding.severity,
                span=finding.span,
                stage=Stage.ANALYSIS,
                related=tuple(
                    RelatedInformation(message=item.note, span=item.span)
                    for item in finding.evidence
                ),
            )
        )
    return produced
