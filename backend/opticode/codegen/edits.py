"""Turning TAC rewrites into edits a person can read in their own source.

The optimizer works on three-address code, but the user wrote MiniC. Showing them the TAC
diff would be showing them the wrong program, so every rewrite has to be projected back
onto the AST. This module does that projection, and it is where the care has to go, because
the projection is lossy in ways that are easy to get wrong:

* **Instruction ids are only unique within a function.** Ten functions all number their
  instructions from ``i1``, so every lookup here is keyed on ``(function name, id)``. Keying
  on the bare id silently merges ten unrelated programs into one, which is how a change map
  ends up pointing at the wrong function's line.
* **A removed instruction is not a removed statement.** ``t = i + 1;`` lowers to two
  instructions; deleting one of them means nothing at the source level, and deleting both
  means one line disappears. A statement may only go when *every* instruction it produced
  is gone.
* **A folded instruction is not a folded expression.** The instruction records which AST
  node produced each operand, so the substitution is recoverable — but the whole-expression
  fold rewrites a different node than an operand substitution does.
* **A folded branch is not always a removable ``if``.** Resolving ``if (0) { ... }`` by
  deleting the whole statement also deletes the condition, and a condition can have side
  effects. That case is refused rather than assumed safe.

Nothing here executes the program. The plan is a pure function of the AST plus the
optimization result, which is what makes the self-check in :mod:`opticode.codegen.selfcheck`
a meaningful last line of defence instead of the only one.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from ..ir.invariants import InvariantViolation
from ..ir.operands import Operand
from ..ir.tac import Instruction, IRProgram, Opcode
from ..optimizer.events import EventKind, OptimizationEvent, OptimizationResult
from ..optimizer.manager import run_passes
from ..optimizer.passes import PassError, select_passes
from ..parser.ast import (
    Assign,
    Block,
    CallExpr,
    Expr,
    ExprStmt,
    FloatLiteral,
    FunctionDecl,
    Identifier,
    If,
    IntLiteral,
    Node,
    Printf,
    Program,
    Scanf,
    Stmt,
    VarDecl,
    While,
    statement_index,
    walk,
    walk_mut,
)
from ..parser.unparser import expression_text, unparse
from ..semantic.types import TypeName
from .changes import ChangeKind, ChangeMap, SourceChange

__all__ = [
    "EditPlan",
    "plan_edits",
    "unparse_node",
]


#: An instruction is identified by its function *and* its id. Ids restart at ``i1`` in every
#: function, so ``("main", "i1")`` and ``("helper", "i1")`` are different instructions that
#: happen to share a name.
Key = tuple[str, str]

#: Statements whose removal cannot change control flow, because they have none of their own.
#: A ``return`` or a loop header may be provably dead, but deleting one changes the shape of
#: the surrounding code, and a shorter file is not worth deleting the statement that made a
#: block a block. ``printf`` and ``scanf`` are in the list despite their side effects: those
#: side effects are what the instruction does, and the statement is only removable when the
#: optimizer deleted that instruction, which it does for input and output nobody executes.
SIMPLE_STATEMENTS = (Assign, VarDecl, ExprStmt, Printf, Scanf)

#: Opcodes that branch on a condition. Only these carry a decidable truth value. There is
#: only one branch opcode, ``if_false``: a false condition jumps to the target and a true
#: condition falls through.
_BRANCH_OPCODES = (Opcode.IF_FALSE,)

#: Opcodes that compute a value from their operands. When one of these collapses into a
#: single ``copy`` of a literal, the *instruction itself* was the source expression.
_COMPUTING_OPCODES = frozenset({Opcode.BINARY, Opcode.UNARY})

#: Opcodes whose operand slots line up one-to-one between the original and the optimized
#: program, so a slot that changed from one operand to a literal is a substitution the
#: optimizer performed and not an accident of the shape of the instruction. Arithmetic and
#: calls and prints all qualify; control flow does not, because a rewritten branch is a
#: different instruction rather than the same one with a nicer operand. The operand-count
#: check below is the second half of this guard, since even a ``call`` can come back with a
#: different number of arguments.
_SUBSTITUTABLE_OPCODES = frozenset({Opcode.BINARY, Opcode.UNARY, Opcode.CALL, Opcode.PRINT})

#: Arms that can be spliced into a statement list without needing braces of their own.
_BLOCK_ARMS = (Block, If)


#: How many times the planner will un-drop a declaration and try again before giving up and
#: shipping whatever it has. Each round strictly removes a declaration, so this is a safety
#: bound rather than a convergence argument.
_MAX_PLANNING_ROUNDS = 8


@dataclass(frozen=True, slots=True)
class _PlanContext:
    """Everything the three planners read, indexed once."""

    ast: Program
    owners: Mapping[Key, Instruction]
    surviving: set[Key]
    current: Mapping[Key, Instruction]
    folded: Mapping[Key, Instruction]
    events: Sequence[OptimizationEvent]
    attribution: Mapping[Key, list[tuple[str, str]]]
    nodes: Mapping[str, Node]
    parents: Mapping[str, str]
    statements: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class EditPlan:
    """The edits to apply to a copy of the AST, plus the map that explains them."""

    #: Statement node ids to delete outright.
    drop: frozenset[str] = frozenset()
    #: Node id to the literal that replaces it.
    replace: Mapping[str, Expr] = field(default_factory=dict)
    #: ``if`` node id to the arm that survives, or to ``None`` when the ``if`` goes away.
    resolve: Mapping[str, Stmt | None] = field(default_factory=dict)
    changes: ChangeMap = field(default_factory=ChangeMap)

    @property
    def empty(self) -> bool:
        return not (self.drop or self.replace or self.resolve)


def plan_edits(ast: Program, result: OptimizationResult) -> EditPlan:
    """Work out which AST nodes the optimization lets us rewrite.

    Three lookups drive everything: which instructions no longer exist, which instructions the
    constant passes turned into literals, and which branches became unconditional. All three
    are read out of the programs rather than out of the event log, so an event that was
    recorded but never applied cannot delete a line of someone's code.

    **Why this iterates.** Deleting a declaration and folding an expression are not
    independent decisions. The optimizer proves a store dead precisely *because* every read
    was propagated to a literal, so the declaration usually goes too — but only if codegen
    actually rewrote every one of those reads. When one is missed, the declaration has to stay,
    and staying brings back references that need folding, which needs another pass over the
    removals. The loop settles that: build the plan, see which names the rewritten tree still
    uses, un-drop the declarations that declare them, repeat. Each round removes at least one
    declaration, so it terminates, and it is bounded anyway.
    """
    original, optimized = result.original, result.optimized
    owners = _instructions(original)
    surviving = set(_instructions(optimized))
    current = _instructions(optimized)
    folded = _folded_constants(original)
    attribution = _attribution(result.events)
    nodes = {node.id: node for node in walk(ast)}
    parents = _parent_map(ast)
    statements = statement_index(ast)

    context = _PlanContext(
        ast=ast,
        owners=owners,
        surviving=surviving,
        current=current,
        folded=folded,
        events=result.events,
        attribution=attribution,
        nodes=nodes,
        parents=parents,
        statements=statements,
    )
    drop = _removable_statements(context)

    plan = _build_plan(context, drop)
    for _round in range(_MAX_PLANNING_ROUNDS):
        referenced = _referenced_names(context, plan)
        revive = {
            node_id
            for node_id in drop
            if isinstance(nodes.get(node_id), VarDecl) and nodes[node_id].name in referenced
        }
        if not revive:
            return plan
        drop -= revive
        plan = _build_plan(context, drop)
    return plan


def _build_plan(context: _PlanContext, drop: set[str]) -> EditPlan:
    changes = ChangeMap()
    _plan_removals(context, drop, changes)
    replace_map: dict[str, Expr] = {}
    _plan_folds(context, drop, replace_map, changes)
    resolve = _plan_branches(context, changes)
    return EditPlan(
        drop=frozenset(drop),
        replace=replace_map,
        resolve=resolve,
        changes=changes,
    )


def _referenced_names(context: _PlanContext, plan: EditPlan) -> set[str]:
    """Every identifier name still present after the plan is applied."""
    from .rewrite import rewrite_program

    rewritten = rewrite_program(context.ast, plan)
    return {
        node.name
        for node in walk(rewritten.program)
        if isinstance(node, Identifier) and node.name
    }


# --------------------------------------------------------------------- indexing


def _instructions(program: IRProgram) -> dict[Key, Instruction]:
    """Index every instruction by ``(function name, id)``.

    Instruction ids are per-function, so an index keyed on the bare id would let the tenth
    function's ``i1`` overwrite the first's.
    """
    return {
        (function.name, item.id): item
        for function in program.functions
        for item in function.instructions
    }


def _parent_map(ast: Node) -> dict[str, str]:
    return {node.id: parent.id for node, _field, parent in walk_mut(ast) if parent is not None}


def _folded_constants(program: IRProgram) -> dict[Key, Instruction]:
    """The instruction state after the constant passes ran to a fixpoint.

    Constant propagation and constant folding are the only passes that replace operands with
    literals, and those are the passes that make the TAC carry the value a folded expression
    needs in order to be printed. The full optimizer often deletes the instruction afterwards
    — folding ``x > 2`` makes the comparison dead the moment branch folding deletes the
    branch that read it — but the origin ids survive and the value at the moment of folding
    is exactly what the source should show.

    Running the constant subset therefore separates the two questions the final program
    conflates: *what did we prove* (this snapshot) and *what did we delete* (the full result).
    """
    try:
        run = run_passes(program, select_passes(["constant-propagation", "constant-folding"]))
        return _instructions(run.program)
    except (PassError, InvariantViolation):
        # A constant pass tripping here is an optimizer bug, and the full run already
        # reported it. Falling back to the untouched program means codegen proposes no folds
        # at all, which degrades to "no source changes" instead of guessing.
        return _instructions(program)


def _substitutions(
    owners: Mapping[Key, Instruction], current: Mapping[Key, Instruction]
) -> list[tuple[Key, Instruction, str, Operand]]:
    """Operand substitutions visible in the final program.

    The constant snapshot cannot see every value the optimizer proves. Constant propagation
    only runs over straight-line code, so a value that depends on which branch a program takes
    stays unknown to it — ``int c = 0; if (...) c = c + 1; if (...) c = c + 1;`` is three
    blocks until branch folding flattens it, and only after flattening does the optimizer know
    ``c`` is 2. That knowledge is real and it is in the final program, where the dead stores
    are gone and the ``printf`` reads ``2``.

    So the final program is consulted as well, for the one thing it can answer exactly: an
    instruction that is still there, still the same opcode, and whose operand slot now holds a
    literal. The interpreter has already run that program, so the substitution is as verified
    as anything here gets.
    """
    found: list[tuple[Key, Instruction, str, Operand]] = []
    for key, before in owners.items():
        after = current.get(key)
        if after is None or after.opcode is not before.opcode:
            continue
        if before.opcode not in _SUBSTITUTABLE_OPCODES:
            continue
        if len(before.operands) != len(after.operands):
            continue
        for index, operand in enumerate(after.operands):
            if index >= len(before.operand_origin_ids):
                break
            origin_id = before.operand_origin_ids[index]
            if not origin_id or not operand.is_const:
                continue
            if index < len(before.operands) and before.operands[index].display == operand.display:
                continue
            found.append((key, before, origin_id, operand))
    return found


def _attribution(events: Iterable[OptimizationEvent]) -> dict[Key, list[tuple[str, str]]]:
    """Instruction key to the ``(pass, explanation)`` pairs recorded against it.

    Built from the event log so the explanation a user reads is the same sentence the
    optimizer wrote when it decided to make the change, rather than a reconstruction that
    could drift from it.
    """
    out: dict[Key, list[tuple[str, str]]] = {}
    for event in events:
        for instruction_id in event.instruction_ids:
            out.setdefault((event.function, instruction_id), []).append((event.pass_name, event.detail))
    return out


def _why(attribution: Mapping[Key, list[tuple[str, str]]], keys: Sequence[Key]) -> tuple[str, ...]:
    """Pass names that touched any of these instructions, in application order."""
    names: list[str] = []
    for key in keys:
        for pass_name, _detail in attribution.get(key, ()):
            if pass_name not in names:
                names.append(pass_name)
    return tuple(names)


def _explanation(
    attribution: Mapping[Key, list[tuple[str, str]]],
    keys: Sequence[Key],
    fallback: str,
) -> str:
    for key in keys:
        entries = attribution.get(key)
        if entries:
            return entries[-1][1]
    return fallback


# --------------------------------------------------------------------- removals


def _removable_statements(context: _PlanContext) -> set[str]:
    """Statement ids whose every instruction was removed.

    The rule is deliberately one-directional. Rather than asking which events say "deleted"
    and trusting them, this asks which instructions no longer exist and works forward to
    statements, so an event that was recorded but not actually applied cannot delete a line of
    someone's code.
    """
    owned: dict[str, list[Key]] = {}
    for key, item in context.owners.items():
        statement = context.statements.get(item.ast_node_id)
        if statement is not None:
            owned.setdefault(statement, []).append(key)

    removable: set[str] = set()
    for node in walk(context.ast):
        if not isinstance(node, Stmt) or not isinstance(node, SIMPLE_STATEMENTS):
            continue
        produced = owned.get(node.id, ())
        if not produced:
            # No instruction means no runtime behaviour to remove. A bare `int x;` is the
            # case in point: deleting the declaration would change what the program accepts.
            continue
        if not any(key in context.surviving for key in produced):
            removable.add(node.id)
    return removable


def _plan_removals(context: _PlanContext, drop: set[str], changes: ChangeMap) -> None:
    """Record a :class:`SourceChange` for every statement the plan drops."""
    owned: dict[str, list[Key]] = {}
    for key, item in context.owners.items():
        statement = context.statements.get(item.ast_node_id)
        if statement is not None:
            owned.setdefault(statement, []).append(key)

    for node_id in sorted(drop, key=lambda value: (
        context.nodes[value].span.start_line,
        context.nodes[value].span.start_col,
    )):
        node = context.nodes.get(node_id)
        if not isinstance(node, Stmt):
            continue
        produced = owned.get(node_id, ())
        changes.add(
            SourceChange(
                kind=ChangeKind.STATEMENT_REMOVED,
                node_id=node_id,
                span=node.span,
                function=produced[0][0] if produced else "",
                reason=_explanation(
                    context.attribution,
                    produced,
                    "the optimizer removed every instruction this statement produced",
                ),
                before=unparse_node(node),
                passes=_why(context.attribution, produced),
                instruction_ids=tuple(sorted(key[1] for key in produced)),
            )
        )


# ----------------------------------------------------------------------- folds


def _plan_folds(
    context: _PlanContext,
    drop: frozenset[str] | set[str],
    replace: dict[str, Expr],
    changes: ChangeMap,
) -> None:
    """Replace expressions the optimizer proved constant with the literal value.

    Two shapes have to be told apart, because they rewrite different nodes:

    * **Operand substitution.** Constant propagation writes a literal into one operand, e.g.
      ``printf("%d", x)`` where ``x`` is provably 3. The node to rewrite is the operand's
      origin, so the *identifier* becomes ``3``.
    * **Whole-expression folding.** Constant folding collapses ``x > 2`` into a single copy
      of ``1``. The node to rewrite is the instruction's own ``ast_node_id``, which is the
      *comparison*, so the condition becomes ``1``.

    Getting these backwards produces source that is either unchanged or wrong, which is why
    the opcode is what decides: a folded arithmetic instruction becomes a ``copy`` of one
    operand, while a substitution leaves the opcode alone.

    Two sources of evidence feed this, because neither tells the whole story. The constant
    snapshot knows what the constant passes proved, including expressions they collapsed
    outright and then deleted. The final program knows what the optimizer knows after branch
    folding has made control flow straight again — values that were never provable inside a
    single block. Both are consulted, the snapshot first, and the first proof of any given
    node wins.

    Folds are planned even inside statements that are about to be dropped. The rewriter never
    visits a dropped statement's children, so the edit goes unused — but if the planner later
    un-drops that statement because something still references one of its names, the fold is
    already there and the declaration comes back without exposing an undeclared variable.
    """
    claimed: set[str] = set()
    evidence: list[tuple[Key, Instruction, str, Operand]] = []

    for key, before in context.owners.items():
        after = context.folded.get(key)
        if after is None:
            continue
        collapsed = after.opcode is Opcode.COPY and len(after.operands) == 1
        if collapsed and before.opcode in _COMPUTING_OPCODES:
            # The instruction *was* the expression, so its own node is what collapses.
            evidence.append((key, before, before.ast_node_id, after.operands[0]))
        else:
            evidence.extend(
                (key, before, origin_id, operand)
                for index, operand in enumerate(after.operands)
                if index < len(before.operand_origin_ids)
                and (origin_id := before.operand_origin_ids[index])
                and index < len(before.operands)
                and before.operands[index].display != operand.display
            )
    evidence.extend(_substitutions(context.owners, context.current))

    for key, _before, origin_id, operand in evidence:
        # Lowering visits every source expression exactly once, so one origin id belongs
        # to exactly one operand. Claiming it makes that assumption checkable instead of
        # silently letting a later instruction overwrite an earlier rewrite.
        if not origin_id or origin_id in claimed:
            continue
        node = context.nodes.get(origin_id)
        if not isinstance(node, Expr) or isinstance(node, Scanf):
            continue
        literal = _literal_node(operand, node)
        if literal is None or expression_text(node) == expression_text(literal):
            continue
        claimed.add(origin_id)
        replace[origin_id] = literal
        changes.add(
            SourceChange(
                kind=ChangeKind.EXPRESSION_FOLDED,
                node_id=origin_id,
                span=node.span,
                function=key[0],
                reason=_explanation(
                    context.attribution,
                    (key,),
                    f"the optimizer proved this is always {operand.display}",
                ),
                before=expression_text(node),
                after=expression_text(literal),
                passes=_why(context.attribution, (key,)),
                instruction_ids=(key[1],),
            )
        )


def _literal_node(operand: Operand, original: Expr) -> Expr | None:
    """An AST literal for a TAC literal, or ``None`` when there is no source form for it."""
    if not operand.is_const or operand.value is None:
        return None
    if operand.type is TypeName.INT and isinstance(operand.value, int):
        return IntLiteral(span=original.span, value=operand.value, lexeme=operand.display)
    if operand.type is TypeName.FLOAT and isinstance(operand.value, float):
        return FloatLiteral(span=original.span, value=operand.value, lexeme=operand.display)
    # A string has no MiniC literal that means the same thing as a pointer-valued constant,
    # and guessing would be worse than not rewriting at all.
    return None


# --------------------------------------------------------------------- branches


def _plan_branches(context: _PlanContext, changes: ChangeMap) -> dict[str, Stmt | None]:
    """Resolve ``if`` statements whose condition became a literal.

    Three things must hold before an ``if`` is collapsed: the optimizer recorded a fold for
    *this* branch, the condition is free of side effects, and the arm can be spliced into the
    parent's statement list. A ``while`` is left alone even when its condition is constant — an
    empty ``while (0) { }`` is already correct, and rebuilding loops from a value is where
    source-level optimizers usually go wrong.

    Requiring the recorded fold is what keeps this from guessing. An ``if`` whose condition
    mentions parameters leaves a perfectly ordinary ``if_false`` in the optimized IR, and
    reading a truth value off a non-literal operand would resolve every such branch to its
    then-arm. That mistake is invisible in the TAC and obvious in the output, which is exactly
    why the branch has to be matched against the event log rather than against its shape.
    """
    folded_branches = {
        (event.function, instruction_id)
        for event in context.events
        if event.kind is EventKind.BRANCH_FOLDED
        for instruction_id in event.instruction_ids
    }
    branch_of: dict[str, tuple[Key, Instruction]] = {}
    for key, item in context.owners.items():
        if item.opcode in _BRANCH_OPCODES and item.ast_node_id:
            branch_of.setdefault(item.ast_node_id, (key, item))

    resolved: dict[str, Stmt | None] = {}
    for node in walk(context.ast):
        if isinstance(node, While):
            found = _never_entered(node, context, folded_branches)
            if found is None or not _pure_condition(node.condition):
                continue
            key, branch = found
            resolved[node.id] = None
            changes.add(
                SourceChange(
                    kind=ChangeKind.BRANCH_RESOLVED,
                    node_id=node.id,
                    span=node.span,
                    function=key[0],
                    reason=_explanation(
                        context.attribution,
                        (key,),
                        "the condition is always false, so this loop never runs",
                    ),
                    before=f"while ({expression_text(node.condition)})",
                    after="",
                    passes=_why(context.attribution, (key,)),
                    instruction_ids=(branch.id,),
                )
            )
            continue
        if not isinstance(node, If):
            continue
        found = branch_of.get(node.id)
        if found is None or node.condition is None:
            continue
        key, branch = found
        if key not in folded_branches:
            continue
        if not _can_hold_arm(context.nodes, context.parents.get(node.id)):
            continue
        truth = _branch_truth(branch, context.current, key)
        if truth is None or not _pure_condition(node.condition):
            continue
        kept = node.then_branch if truth else node.else_branch
        if kept is not None and not isinstance(kept, _BLOCK_ARMS):
            # A bare `if (c) x = 1;` would splice the assignment into the parent list with no
            # braces, which changes what a following `else` binds to.
            continue
        resolved[node.id] = kept
        changes.add(
            SourceChange(
                kind=ChangeKind.BRANCH_RESOLVED,
                node_id=node.id,
                span=node.span,
                function=key[0],
                reason=_explanation(
                    context.attribution,
                    (key,),
                    f"the condition is always {'true' if truth else 'false'}, so only one "
                    "arm can run",
                ),
                before=f"if ({expression_text(node.condition)})",
                after="" if kept is None else unparse_node(kept).splitlines()[0],
                passes=_why(context.attribution, (key,)),
                instruction_ids=(branch.id,),
            )
        )
    return resolved


def _never_entered(
    node: While,
    context: _PlanContext,
    folded_branches: set[Key],
) -> tuple[Key, Instruction] | None:
    """The header branch of a loop the optimizer proved can never be entered.

    ``while (c) body`` lowers to ``if_false c goto exit`` in the header, so a header branch
    that branch folding replaced with an unconditional jump means ``c`` is always false and the
    loop is dead code. The opposite outcome — the branch deleted because ``c`` is always true —
    is an infinite loop, and deleting that would not be shortening the file, it would be
    changing what the program does.

    A ``for`` is deliberately not handled here. Its body can be removed just as safely, but its
    initializer still has to run, and lifting that initializer out of the loop would move the
    variables it declares into the enclosing scope. Leaving the emptied ``for`` in place costs
    one line of output and avoids a scoping question that has no cheap answer.
    """
    if not isinstance(node.condition, Expr):
        return None
    condition_ids = {child.id for child in walk(node.condition)}
    for key, item in context.owners.items():
        if item.opcode not in _BRANCH_OPCODES or key not in folded_branches:
            continue
        if not condition_ids.intersection(item.operand_origin_ids):
            continue
        after = context.current.get(key)
        if after is not None and after.opcode is Opcode.GOTO:
            return key, item
    return None


def _branch_truth(
    branch: Instruction,
    current: Mapping[Key, Instruction],
    key: Key,
) -> bool | None:
    """Whether the ``if`` body always runs, or ``None`` when that cannot be established.

    Branch folding leaves two shapes behind and they read differently in the optimized IR: a
    true condition deletes the branch outright, while a false one leaves an unconditional jump
    in its place. Neither says anything on its own — the caller only asks here for a branch
    the event log records as folded, so an absent branch means "it fell through" rather than
    "somebody deleted a control-flow instruction".
    """
    after = current.get(key)
    if after is None:
        return True
    if after.opcode is Opcode.GOTO:
        return False
    return None


def _pure_condition(condition: Expr) -> bool:
    """Whether evaluating the condition fewer times, or not at all, would be safe."""
    return not any(isinstance(node, (CallExpr, Scanf)) for node in walk(condition))


def _can_hold_arm(nodes: Mapping[str, Node], parent_id: str | None) -> bool:
    """Whether this node's children form a statement list an arm can be spliced into.

    ``Block`` and ``FunctionDecl`` bodies qualify. A loop body does not: ``while (c) if (d)
    x = 1;`` has the inner ``if`` as the loop's single body statement, and replacing it with
    a braced block would silently stop the ``break`` and ``continue`` that referred to the
    outer loop.
    """
    if parent_id is None:
        return False
    return isinstance(nodes.get(parent_id), (Block, FunctionDecl))


def unparse_node(node: Node) -> str:
    """The canonical source text of a node, used for a change's before/after text."""
    return unparse(node).strip()
