"""Liveness: which variables could still be read further down.

A variable is **live at a program point** when some path from that point reaches a read of
it without writing it first. That is the exact precondition for an assignment to that
variable being removable, which is the one optimization this fact buys — so it is computed
now even though no pass consumes it yet.

The equations, with ``IN[B]`` live before ``B`` and ``OUT[B]`` live after it::

    OUT[EXIT] = ∅
    OUT[B]    = ⋃ IN[S]        for every successor S of B
    IN[B]     = USE[B] ∪ (OUT[B] \\ DEF[B])

``USE[B]`` is the set of variables read in ``B`` *before* being written in ``B``, and
``DEF[B]`` is everything ``B`` writes. Both fall out of one backward walk per block, so the
transfer never materializes an intermediate set.

Per-instruction ``live_in`` and ``live_out`` come from that same walk. The block fixed point
is solved first, and walking a converged block backwards then gives every instruction's
answer exactly.

Union is idempotent and commutative, so this lattice's bottom is ∅ and its meet is plain
set union.
"""

from __future__ import annotations

from typing import Final

from ..cfg import CFG, BasicBlock
from ..ir.operands import Operand
from ..ir.tac import Instruction
from .solver import DataFlowProblem, Direction, Solution, solve

__all__ = [
    "NO_LIVE",
    "NameSet",
    "live_after",
    "live_before",
    "liveness",
    "names",
    "transfer",
    "uses_before_def",
]

#: A set of variable names. ``frozenset`` is hashable, so it can be the solver's domain
#: value, and its equality is exactly the fixed-point test the solver needs.
NameSet = frozenset[str]

#: Liveness at EXIT is ∅: nothing is live once the program has returned.
NO_LIVE: Final[NameSet] = frozenset()


def names(operands: tuple[Operand, ...]) -> NameSet:
    """The distinct variable names among non-constant operands."""
    return frozenset(operand.name for operand in operands if not operand.is_const)


def uses_before_def(instructions: tuple[Instruction, ...]) -> NameSet:
    """``USE[B]``: variables read in the block before anything there writes them.

    This is what separates ``x = 1; return x``, where ``x`` is *not* used before definition,
    from ``return x; x = 1``, where it is.
    """
    defined: set[str] = set()
    used: set[str] = set()
    for instruction in instructions:
        for operand in instruction.uses():
            if operand.name not in defined:
                used.add(operand.name)
        defined.update(operand.name for operand in instruction.defs())
    return frozenset(used)


def transfer(block: BasicBlock, live_out: NameSet) -> NameSet:
    """Walk ``block`` backwards, turning ``live_out`` into ``live_in``.

    Walking backwards is what makes the read-before-write distinction fall out for free: an
    instruction kills a name for every earlier point in the block.
    """
    live = set(live_out)
    for instruction in reversed(block.instructions):
        for operand in instruction.defs():
            live.discard(operand.name)
        live.update(operand.name for operand in instruction.uses())
    return frozenset(live)


def _problem(cfg: CFG) -> DataFlowProblem[NameSet]:
    return DataFlowProblem(
        name="liveness",
        direction=Direction.BACKWARD,
        cfg=cfg,
        boundary=NO_LIVE,
        initial=NO_LIVE,
        meet=lambda values: frozenset().union(*values) if values else NO_LIVE,
        transfer=transfer,
        render=lambda live: sorted(live),
    )


def liveness(cfg: CFG) -> Solution[NameSet]:
    """Solve may-liveness over one function's reachable blocks."""
    return solve(_problem(cfg))


def live_before(block: BasicBlock, live_out: NameSet) -> dict[str, NameSet]:
    """Per-instruction ``live_in``, in instruction order rather than walk order.

    Returned sorted so the API response is stable; call sites that want position look the id
    up rather than indexing.
    """
    result: dict[str, NameSet] = {}
    live = set(live_out)
    for instruction in reversed(block.instructions):
        for operand in instruction.defs():
            live.discard(operand.name)
        live.update(operand.name for operand in instruction.uses())
        result[instruction.id] = frozenset(live)
    return {key: result[key] for key in sorted(result)}


def live_after(block: BasicBlock, live_out: NameSet) -> dict[str, NameSet]:
    """Per-instruction ``live_out``: the live set immediately *after* each instruction.

    The same walk as :func:`live_before`, shifted one position along the block so the final
    instruction reports the block's own ``live_out``.
    """
    before = live_before(block, live_out)
    ordered = list(block.instructions)
    result: dict[str, NameSet] = {}
    live = set(live_out)
    for index in range(len(ordered) - 1, -1, -1):
        instruction = ordered[index]
        result[instruction.id] = frozenset(live)
        live = set(before[instruction.id])
    return {key: result[key] for key in sorted(result)}
