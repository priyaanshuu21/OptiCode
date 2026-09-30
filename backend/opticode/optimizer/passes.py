"""The optimization passes.

Five passes, one job each, and they are composed by the manager's fixpoint loop rather than
by being able to call each other:

=================================== =========================================================
Pass                                What it does
=================================== =========================================================
:class:`ConstantPropagation`       replaces a read of a provably constant value with a literal
:class:`ConstantFolding`           replaces an instruction whose operands are all literals
                                    with the literal it computes
:class:`BranchFolding`             turns a conditional branch on a literal into an
                                    unconditional one, or deletes it
:class:`UnreachableBlockElimination` deletes blocks no path from the entry reaches
:class:`DeadAssignmentElimination` deletes assignments nothing reads afterwards
=================================== =========================================================

No pass removes or reorders anything that may print, read, call, return, jump, or trap.
That is not a stylistic rule — it is the definition of correctness for this language, where
the observable behaviour of a program is its output, its input, its return value, and its
traps.

Three invariants are load-bearing and are enforced in code rather than assumed:

**Instruction ids are never reassigned.** A pass deletes and rewrites instructions; it does
not renumber. A trap records the instruction that caused it, so keeping ids stable means an
optimized program reports the same trap location as the program it came from, and the
frontend can diff the two TAC dumps by eye.

**The last instruction of a non-void function is never deleted.** A non-void function must
end in a `return`, and the synthetic trailing `return 0` can itself sit in an unreachable
block. Deleting it would leave a function that runs off the end of its instruction list,
which the TAC invariants reject. :func:`_removable` is the single place that rule lives.

**A jump never loses its label.** Deleting an unreachable block also deletes the labels it
defines. Nothing reachable can target them — a jump from a reachable block would make its
target's block reachable — and :class:`UnreachableBlockElimination` asserts that rather
than relying on the argument, so a violation is a loud failure instead of broken TAC.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import replace

from ..analysis.lattice import AbstractValue
from ..analysis.results import FunctionAnalysis
from ..cfg import CFG
from ..core.spans import SourceSpan
from ..ir.operands import Operand
from ..ir.printer import print_instruction
from ..ir.tac import Instruction, IRFunction, IRProgram, Opcode
from ..lexer.lexer import INT_LITERAL_MAX
from ..semantic.types import TypeName
from ..semantics.arith import INT_MIN
from .context import PassContext
from .events import EventKind, OptimizationEvent, PassDelta

__all__ = [
    "BranchFolding",
    "ConstantFolding",
    "ConstantPropagation",
    "DeadAssignmentElimination",
    "Pass",
    "PassError",
    "UnreachableBlockElimination",
    "DEFAULT_PASSES",
]


class PassError(Exception):
    """A pass produced TAC it should not have.

    Every case here is a compiler bug rather than a user error, so the pipeline turns it into
    an internal-error diagnostic and discards the optimized output instead of shipping it.
    """


class Pass(ABC):
    """One optimization pass."""

    #: The name used by ``CompileOptions.passes`` and reported in the payload.
    name: str = ""
    #: One line, shown in the UI, saying what the pass is for.
    description: str = ""

    @abstractmethod
    def run(self, ctx: PassContext) -> PassDelta:
        """Rewrite the program in ``ctx`` in place, returning what changed."""


# ------------------------------------------------------------------- helpers


#: Opcodes that produce a value the constant folder may replace with a literal. Every other
#: opcode either produces nothing (``print``, ``goto``) or produces something no static
#: analysis can know (``call``, ``read``).
_FOLDABLE = frozenset(
    {Opcode.COPY, Opcode.BINARY, Opcode.UNARY, Opcode.ITOF, Opcode.FTOI}
)


def _literal(value: AbstractValue) -> Operand | None:
    """Turn a known abstract value into a literal operand, or None if it cannot be one.

    Two results are refused even though constant propagation concluded them:

    * a non-finite float, which is not a value the frontend can render as a constant and
      which :mod:`opticode.analysis.lattice` already declines to produce;
    * ``INT_MIN``. It is a legal *result* — MiniC arithmetic wraps to 32 bits — but printing
      it yields ``-2147483648``, which the lexer rejects as an out-of-range literal because
      it reads the magnitude. Folding to it would emit TAC that cannot be read back.
    """
    if value.type_name == "int":
        number = int(value.value)  # type: ignore[arg-type]
        if not INT_MIN < number <= INT_LITERAL_MAX:
            return None
        return Operand.const(number)
    if value.type_name == "float":
        number = float(value.value)  # type: ignore[arg-type]
        if not math.isfinite(number):
            return None
        return Operand.const(number)
    return None


def _foldable_value(
    instruction: Instruction, analysis: FunctionAnalysis
) -> Operand | None:
    """The literal this instruction computes, if constant propagation knows it.

    Returns None unless *all* of these hold, which is the whole safety argument for
    replacing an instruction with its value:

    * the opcode produces a value, and no static analysis can predict a call's or a read's;
    * constant propagation concluded a specific value for this exact instruction;
    * the instruction cannot trap — a trapping division has no value to substitute, and its
      side effect of aborting the program would be lost;
    * the value's MiniC type matches what the instruction said it produced;
    * the value survives :func:`_literal`.
    """
    if instruction.opcode not in _FOLDABLE or instruction.dest is None:
        return None
    if instruction.may_trap():
        return None
    facts = analysis.instructions.get(instruction.id)
    if facts is None or facts.constants.result is None:
        return None
    result = facts.constants.result
    if not result.is_const:
        return None
    if instruction.type is not None and instruction.type.value != result.type_name:
        return None
    return _literal(result)


def _as_literal_copy(instruction: Instruction, literal: Operand) -> Instruction:
    """Rewrite an instruction into ``dest = literal``.

    Only the value-producing fields change. The span, the synthetic flag, and the operand
    origin ids are kept, because they are what tie this instruction back to the source
    expression it replaced.
    """
    origins = instruction.operand_origin_ids[:1]
    return replace(
        instruction,
        opcode=Opcode.COPY,
        operands=(literal,),
        operand_origin_ids=origins,
        target=None,
        op=None,
        callee=None,
        format=None,
        type=literal.type,
    )


def _replace_operand(instruction: Instruction, index: int, operand: Operand) -> Instruction:
    """Swap one operand, keeping ``operand_origin_ids`` positionally aligned.

    The origin id stays: it records which AST node produced that operand position, and the
    substitution did not change which node that was. Codegen reads those ids to decide what
    it may delete from the source tree, so dropping them here would lose the link.
    """
    operands = list(instruction.operands)
    operands[index] = operand
    return replace(instruction, operands=tuple(operands))


def _removable(function: IRFunction, instruction: Instruction, *, is_last: bool) -> bool:
    """Whether deleting this instruction is allowed at all, before liveness is consulted.

    Three separate reasons come together here, and each one has bitten an optimizer before:

    * nothing observable may be deleted — a call, a ``print``, a ``read``, a ``return``, and
      a jump all change what the program does;
    * a possibly-trapping instruction must stay, because "nobody reads the result" does not
      mean "the program still finishes";
    * the final instruction of a non-void function stays, because a function that runs off
      the end of its own instruction list is not a function.
    """
    if instruction.dest is None:
        return False
    if instruction.has_side_effects():
        return False
    if instruction.may_trap():
        return False
    return not (is_last and function.return_type is not TypeName.VOID)


def _removable_unreachable(
    function: IRFunction, instruction: Instruction, *, is_last: bool
) -> bool:
    """Whether an instruction in a statically unreachable block may be deleted.

    Unreachable code is held to a weaker rule than reachable code. A ``printf`` or a
    ``read`` sitting in a block no path enters never executes, so deleting it cannot change
    the output, the input consumed, or the return value — the three things the interpreter
    oracle compares. :func:`_removable` would refuse, and the effect would be that "delete
    dead code" only ever removed arithmetic while leaving every unreachable ``printf``
    behind, which is not what the analysis reported when it called that block dead.

    The one exception is the one that applies everywhere else: a non-void function must end
    in a return, and its synthetic trailing return can sit in a dead block.
    """
    return not (is_last and function.return_type is not TypeName.VOID)


def _record(
    ctx: PassContext,
    delta: PassDelta,
    pass_name: str,
    function: IRFunction,
    kind: EventKind,
    detail: str,
    before: Instruction,
    ids: tuple[str, ...],
    after: Instruction | None = None,
    block: str | None = None,
) -> None:
    delta.events.append(
        OptimizationEvent(
            pass_name=pass_name,
            function=function.name,
            kind=kind,
            detail=detail,
            before=print_instruction(before),
            after=print_instruction(after) if after is not None else "",
            instruction_ids=tuple(sorted(ids)),
            # An instruction the compiler invented has a default 1:1 span, which points at
            # line 1 of the file rather than anywhere the change came from.
            span=None if before.span == SourceSpan() else before.span,
            block=block,
        )
    )


def _block_of(cfg: CFG) -> dict[str, str]:
    """Instruction id → block id, for attributing an event to a block.

    Built once per function because every event in a pass needs it and
    :meth:`CFG.block_of_instruction` rescans the block list each call.
    """
    return {
        instruction.id: block.id for block in cfg.blocks for instruction in block.instructions
    }


# ------------------------------------------------------- constant propagation


class ConstantPropagation(Pass):
    """Replace reads of provably constant values with literals.

    This pass changes no control flow and deletes nothing. It only substitutes operands, so
    it is safe to run first and it is what makes the other four able to see anything: a
    branch on a literal, an instruction with only literal operands, and a store whose only
    reader has become literal-free all depend on the uses being literal already.

    Substitution is refused unless the abstract value's MiniC type matches the operand's, so
    a float can never be written into an int operand even if the values happen to be equal.
    """

    name = "constant-propagation"
    description = "replace reads of provably constant values with literals"

    def run(self, ctx: PassContext) -> PassDelta:
        delta = PassDelta()
        for function in ctx.program.functions:
            analysis = ctx.analyses.get(function.name)
            if analysis is None:  # pragma: no cover - refresh covers every function
                continue
            rewritten: list[Instruction] = []
            changed_here = False
            for instruction in function.instructions:
                replacement = self._rewrite(instruction, analysis)
                if replacement != instruction:
                    changed_here = True
                    _record(
                        ctx,
                        delta,
                        self.name,
                        function,
                        EventKind.CONSTANT_PROPAGATED,
                        "every path reaching this instruction proves the value",
                        instruction,
                        (instruction.id,),
                        after=replacement,
                    )
                rewritten.append(replacement)
            if changed_here:
                delta.changed = True
                ctx.replace_function(function.with_instructions(tuple(rewritten)))
        return delta

    def _rewrite(self, instruction: Instruction, analysis: FunctionAnalysis) -> Instruction:
        facts = analysis.instructions.get(instruction.id)
        if facts is None:
            # Unreachable code has no solved facts, and propagating into it could not
            # produce a value it actually takes.
            return instruction
        replacement = instruction
        for index, operand in enumerate(instruction.operands):
            if operand.is_const or index >= len(facts.constants.operands):
                continue
            value = facts.constants.operands[index]
            if not value.is_const or value.type_name != operand.type.value:
                continue
            literal = _literal(value)
            if literal is None or literal == operand:
                continue
            replacement = _replace_operand(replacement, index, literal)
        return replacement


# ------------------------------------------------------------ constant folding


class ConstantFolding(Pass):
    """Replace an instruction whose operands are all literals with the literal it computes.

    Folding goes through :mod:`opticode.analysis.lattice`, the same module the interpreter's
    arithmetic comes from, so "this folds to 5" and "this evaluates to 5" cannot drift apart.

    The rewritten form is ``dest = literal``, not the removal of the instruction. That looks
    like leaving work for the next round, and it is deliberate: the store is removed by
    constant propagation substituting its uses and by dead store elimination, each of which
    can prove it is safe. Folding only has to prove the *value*.
    """

    name = "constant-folding"
    description = "compute instructions with literal operands at compile time"

    def run(self, ctx: PassContext) -> PassDelta:
        delta = PassDelta()
        for function in ctx.program.functions:
            analysis = ctx.analyses.get(function.name)
            if analysis is None:  # pragma: no cover - refresh covers every function
                continue
            rewritten: list[Instruction] = []
            changed_here = False
            for instruction in function.instructions:
                literal = _foldable_value(instruction, analysis)
                if literal is None:
                    rewritten.append(instruction)
                    continue
                folded = _as_literal_copy(instruction, literal)
                if folded == instruction:
                    # Already ``dest = <this literal>``. Reporting a change here would make
                    # the manager loop until it hit the round cap.
                    rewritten.append(instruction)
                    continue
                changed_here = True
                _record(
                    ctx,
                    delta,
                    self.name,
                    function,
                    EventKind.CONSTANT_FOLDED,
                    f"both operands are known, so this is always {literal.display}",
                    instruction,
                    (instruction.id,),
                    after=folded,
                )
                rewritten.append(folded)
            if changed_here:
                delta.changed = True
                ctx.replace_function(function.with_instructions(tuple(rewritten)))
        return delta


# -------------------------------------------------------------- branch folding


class BranchFolding(Pass):
    """Resolve a branch whose condition is a literal.

    ``if_false <literal> goto L`` has only one possible outcome, so it becomes either a
    ``goto L`` (the literal is zero, so the branch is taken) or nothing at all (the literal
    is non-zero, so control simply falls through). Both rewrites are what make the untaken
    side of a constant condition unreachable, which is what
    :class:`UnreachableBlockElimination` then deletes.

    The literal comes from the *operand*, not from the analysis's branch fact: by the time
    this pass runs, constant propagation has already written the value into the operand if
    the condition was provably constant, so reading the operand means this pass works on
    whatever the previous passes actually produced rather than on a fact that may predate
    them.
    """

    name = "branch-folding"
    description = "resolve branches on a constant condition"

    def run(self, ctx: PassContext) -> PassDelta:
        delta = PassDelta()
        for function in ctx.program.functions:
            cfg = ctx.cfgs.get(function.name)
            if cfg is None:  # pragma: no cover - refresh covers every function
                continue
            positions = _block_of(cfg)
            kept: list[Instruction] = []
            changed_here = False
            for instruction in function.instructions:
                if not self._decidable(instruction):
                    kept.append(instruction)
                    continue
                condition = instruction.operands[0]
                taken = truthy_literal(condition)
                detail = (
                    f"the condition is the literal {condition.display}, so the branch is "
                    "always taken" if not taken
                    else f"the condition is the literal {condition.display}, so control "
                    "always falls through"
                )
                if taken:
                    replacement: Instruction | None = None
                    removed = True
                else:
                    replacement = replace(
                        instruction,
                        opcode=Opcode.GOTO,
                        operands=(),
                        operand_origin_ids=(),
                    )
                    removed = False
                changed_here = True
                if removed:
                    delta.removed += 1
                _record(
                    ctx,
                    delta,
                    self.name,
                    function,
                    EventKind.BRANCH_FOLDED,
                    detail,
                    instruction,
                    (instruction.id,),
                    after=replacement,
                    block=positions.get(instruction.id),
                )
                if replacement is not None:
                    kept.append(replacement)
            if changed_here:
                delta.changed = True
                ctx.replace_function(function.with_instructions(tuple(kept)))
        return delta

    @staticmethod
    def _decidable(instruction: Instruction) -> bool:
        return instruction.opcode is Opcode.IF_FALSE and bool(instruction.operands) and (
            instruction.operands[0].is_const
            and instruction.operands[0].type is not TypeName.STRING
        )


def truthy_literal(operand: Operand) -> bool:
    """C truthiness of a literal: zero is false, everything else is true."""
    return operand.value != 0


# ------------------------------------------------- unreachable block elimination


class UnreachableBlockElimination(Pass):
    """Delete blocks that no path from the function entry reaches.

    The CFG keeps unreachable blocks on purpose: dead-code analysis has to *see* them to
    report them. This is the pass that finally removes them, which it can do precisely
    because the analysis stage already ran and said what they were.

    Deleting a block also deletes the labels it defines, which is the only way this pass
    could break a jump. It cannot: a jump from a reachable block would make its target's
    block reachable. The check below asserts that rather than trusting the argument, so a
    pass that changes the reasoning breaks here instead of emitting TAC that jumps nowhere.
    """

    name = "unreachable-blocks"
    description = "delete blocks no path from the function entry reaches"

    def run(self, ctx: PassContext) -> PassDelta:
        delta = PassDelta()
        for function in ctx.program.functions:
            cfg = ctx.cfgs.get(function.name)
            if cfg is None:  # pragma: no cover - refresh covers every function
                continue
            dead = [block for block in cfg.blocks if not block.reachable]
            if not dead:
                continue
            dead_ids = {
                instruction.id
                for block in dead
                for instruction in (*block.instructions, *block.label_instructions)
            }
            dead_labels = {label for block in dead for label in block.labels}
            self._assert_no_dangling_jump(function, dead_ids, dead_labels)

            total = len(function.instructions)
            removed_ids = tuple(
                instruction.id
                for index, instruction in enumerate(function.instructions)
                if instruction.id in dead_ids
                and _removable_unreachable(function, instruction, is_last=index == total - 1)
            )
            # A non-void function's synthetic trailing return can sit in an unreachable
            # block. Deleting it would leave the function running off the end of its own
            # instruction list, so it is the one thing here that survives.
            if removed_ids:
                delta.changed = True
                delta.removed += len(removed_ids)
                doomed = set(removed_ids)
                for block in dead:
                    gone = tuple(i for i in block.instructions if i.id in doomed)
                    if not gone:
                        continue
                    _record(
                        ctx,
                        delta,
                        self.name,
                        function,
                        EventKind.BLOCK_REMOVED,
                        f"no path from the entry of {function.name} reaches {block.id}",
                        block.instructions[0],
                        tuple(i.id for i in gone),
                        block=block.id,
                    )
                ctx.replace_function(
                    function.with_instructions(
                        tuple(
                            instruction
                            for instruction in function.instructions
                            if instruction.id not in doomed
                        )
                    )
                )
        return delta

    @staticmethod
    def _assert_no_dangling_jump(
        function: IRFunction, dead_ids: set[str], dead_labels: set[str]
    ) -> None:
        for instruction in function.instructions:
            if instruction.id in dead_ids:
                continue
            if instruction.target and instruction.target in dead_labels:
                raise PassError(
                    f"{function.name}: {instruction.id} jumps to '{instruction.target}', "
                    "which is defined in an unreachable block that is being deleted"
                )


# ------------------------------------------------ dead assignment elimination


class DeadAssignmentElimination(Pass):
    """Delete assignments whose destination is never read afterwards.

    Liveness decides it, and the decision is already solved: every instruction's
    ``dest_live`` fact says whether the value it writes survives to a later read. This pass
    reads that fact rather than re-deriving liveness, because a second backward sweep here
    would be free to disagree with the ``live_in``/``live_out`` the frontend displays.

    ``dest_live`` answers only half the question. Liveness is about *values*, and a dead
    value can still be produced by an instruction that must not be deleted — a division
    that may trap, or a call. :func:`_removable` supplies that half, and the two together
    are the complete test.

    A chain of dead stores collapses over several rounds of the manager's fixpoint rather
    than within one application::

        t = a + b        z is dead, so `z = t` goes and t loses its reader
        z = t            next round: t is no longer live, so it goes too

    That is the manager's job rather than this pass's, and the reason the passes are
    ordered rather than chained: each one only has to be correct about its own step.
    """

    name = "dead-assignments"
    description = "delete assignments whose value is never read afterwards"

    def run(self, ctx: PassContext) -> PassDelta:
        delta = PassDelta()
        for function in ctx.program.functions:
            analysis = ctx.analyses.get(function.name)
            if analysis is None:  # pragma: no cover - refresh covers every function
                continue
            doomed = self._dead_stores(function, analysis)
            if not doomed:
                continue
            total = len(function.instructions)
            kept = tuple(
                instruction
                for index, instruction in enumerate(function.instructions)
                if instruction.id not in doomed
            )
            removed = total - len(kept)
            if removed == 0:  # pragma: no cover - _dead_stores applies _removable
                continue
            delta.changed = True
            delta.removed += removed
            positions = _block_of(ctx.cfgs[function.name])
            for instruction in function.instructions:
                if instruction.id not in doomed:
                    continue
                name = instruction.dest.display if instruction.dest else "?"
                _record(
                    ctx,
                    delta,
                    self.name,
                    function,
                    EventKind.STORE_REMOVED,
                    f"nothing reads `{name}` after this point",
                    instruction,
                    (instruction.id,),
                    block=positions.get(instruction.id),
                )
            ctx.replace_function(function.with_instructions(kept))
        return delta

    def _dead_stores(
        self, function: IRFunction, analysis: FunctionAnalysis
    ) -> set[str]:
        """Which instructions both analyses agree are removable.

        Instructions with no facts are skipped: the analysis solves reachable code only, and
        unreachable instructions are :class:`UnreachableBlockElimination`'s business.
        """
        total = len(function.instructions)
        doomed: set[str] = set()
        for index, instruction in enumerate(function.instructions):
            facts = analysis.instructions.get(instruction.id)
            if facts is None or facts.liveness.dest_live:
                continue
            if _removable(function, instruction, is_last=index == total - 1):
                doomed.add(instruction.id)
        return doomed


#: The pass pipeline. Constant propagation comes first because the other four can only see
#: what it exposes; dead store elimination comes last because it is the only one that needs
#: the others to have finished folding.
DEFAULT_PASSES: tuple[Pass, ...] = (
    ConstantPropagation(),
    ConstantFolding(),
    BranchFolding(),
    UnreachableBlockElimination(),
    DeadAssignmentElimination(),
)


def select_passes(names: list[str] | tuple[str, ...] | None) -> tuple[Pass, ...]:
    """The passes named in ``names``, in pipeline order, or the default set.

    Order comes from :data:`DEFAULT_PASSES` rather than from the request, so a caller cannot
    create a sequence the manager would have to special-case — asking for
    ``["dead-assignments", "constant-propagation"]`` runs constant propagation first.
    """
    if not names:
        return DEFAULT_PASSES
    by_name = {item.name: item for item in DEFAULT_PASSES}
    unknown = [name for name in names if name not in by_name]
    if unknown:
        raise PassError(
            f"unknown pass(es) {', '.join(sorted(unknown))}; available: "
            f"{', '.join(item.name for item in DEFAULT_PASSES)}"
        )
    wanted = set(names)
    return tuple(item for item in DEFAULT_PASSES if item.name in wanted)


def program_instructions(program: IRProgram) -> int:
    """Total instruction count across a program, for the pass manager's stop condition."""
    return sum(len(function.instructions) for function in program.functions)
