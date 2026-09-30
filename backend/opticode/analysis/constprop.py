"""Constant propagation: which values are known before the program runs.

The domain is the three-point lattice described in :mod:`.lattice` — ⊤, ``CONST(c)``, and
NAC — and the equations are the textbook forward ones with meet = ⊔::

    IN[ENTRY]  = { v ↦ NAC : v ∈ variables }      -- nothing is known at entry
    IN[B]      = ⋃ IN[P]                          -- for every predecessor P of B
    OUT[B]     = IN[B][GEN ops]                    -- each instruction rewrites what it knows
    OUT[ENTRY] = IN[ENTRY]

The interesting work is in the instruction transfer, not the framework:

* ``copy``, ``binary``, ``unary``, ``itof``, ``ftoi`` fold when their operands are known.
* ``call`` produces NAC. A function's return value depends on its arguments, on global
  state, and on anything it calls, so folding it would be a guess.
* ``read`` produces NAC. Input is exactly the thing that is not known at compile time.
* a store to a variable that is *later* overwritten kills nothing on its own; the lattice
  handles overwrite naturally because the new value simply replaces the old.

The abstract value of every branch condition is read out of the solved state and reported,
because "is this ``if`` statically decidable" is the first thing a user asks.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..cfg import CFG, BasicBlock
from ..ir.operands import Operand
from ..ir.tac import Instruction, Opcode
from ..semantic.types import TypeName
from .lattice import (
    NAC,
    AbstractValue,
    const,
    fold_binary,
    fold_ftoi,
    fold_itof,
    fold_unary,
    state_meet,
)
from .solver import DataFlowProblem, Direction, Solution, solve

__all__ = [
    "State",
    "apply_instruction",
    "exit_states",
    "known_before",
    "knowns",
    "propagate",
    "read_operand",
    "transfer",
]

#: A whole variable state, mapping each name to its abstract value.
State = dict[str, AbstractValue]


def _operand_value(operand: Operand, state: State) -> AbstractValue:
    """The abstract value of one operand, from a literal or from the state.

    A name absent from the state is NAC rather than ⊤: the state already seeds every tracked
    name to NAC at entry, so an absent name is one nothing has reached yet, and NAC is the
    conservative reading.
    """
    if operand.is_const:
        if operand.type is TypeName.STRING:
            return NAC
        return const(operand.value)  # type: ignore[arg-type]
    return state.get(operand.name, NAC)


def transfer(block: BasicBlock, incoming: State) -> State:
    """Apply a block's instructions to its incoming state, in order.

    The state is threaded forward through the block; each instruction that defines a name
    replaces that name's abstract value. Instructions that cannot produce a known value
    (calls, reads) drive their destination to NAC.
    """
    state: State = dict(incoming)
    for instruction in block.instructions:
        apply_instruction(state, instruction)
    return state


def apply_instruction(state: State, instruction: Instruction) -> None:
    """Rewrite ``state`` for one instruction, in place.

    Public because deriving per-instruction facts means replaying the same forward walk one
    instruction at a time, and a second implementation of these rules would be free to drift
    from the one the solver uses.
    """
    opcode = instruction.opcode
    destination = instruction.dest

    if opcode is Opcode.COPY:
        if destination is not None and instruction.operands:
            state[destination.name] = _operand_value(instruction.operands[0], state)
        return

    if opcode is Opcode.BINARY:
        if destination is None:  # pragma: no cover - lowering always sets a dest
            return
        left = _operand_value(instruction.operands[0], state)
        right = _operand_value(instruction.operands[1], state)
        # A float result is only produced when an operand is a float literal or a float
        # variable; the instruction's own type records the real result type.
        result_float = instruction.type is TypeName.FLOAT
        state[destination.name] = fold_binary(
            instruction.op or "", left, right, result_float
        )
        return

    if opcode is Opcode.UNARY:
        if destination is None:  # pragma: no cover - defensive
            return
        state[destination.name] = fold_unary(
            instruction.op or "", _operand_value(instruction.operands[0], state)
        )
        return

    if opcode is Opcode.ITOF:
        if destination is not None and instruction.operands:
            state[destination.name] = fold_itof(_operand_value(instruction.operands[0], state))
        return

    if opcode is Opcode.FTOI:
        if destination is not None and instruction.operands:
            state[destination.name] = fold_ftoi(_operand_value(instruction.operands[0], state))
        return

    if opcode is Opcode.CALL:
        if destination is not None:
            state[destination.name] = NAC
        return

    if opcode is Opcode.READ:
        if destination is not None:
            state[destination.name] = NAC
        return

    # PRINT, RETURN, GOTO, IF_FALSE, LABEL define nothing and leave the state alone.


def read_operand(state: State, operand: Operand) -> AbstractValue:
    """Public wrapper for reading an operand's abstract value out of a state."""
    return _operand_value(operand, state)


def _initial_state(cfg: CFG) -> State:
    """``IN[ENTRY]``: every tracked name starts NAC — no value is known before execution."""
    names: set[str] = set()
    for param_item in cfg.function.params:
        names.add(param_item.ir_name)
    names.update(cfg.function.declared_locals)
    for block in cfg.blocks:
        if not block.reachable:
            continue
        for instruction in block.instructions:
            for operand in instruction.defs():
                names.add(operand.name)
            for operand in instruction.uses():
                if not operand.is_const:
                    names.add(operand.name)
    return {name: NAC for name in sorted(names)}


def _problem(cfg: CFG) -> DataFlowProblem[State]:
    return DataFlowProblem(
        name="constant-propagation",
        direction=Direction.FORWARD,
        cfg=cfg,
        boundary=_initial_state(cfg),
        # Forward bottom: an empty state contributes nothing, so an unsolved block does not
        # drag every variable to NAC. The entry boundary seeds all variables to NAC anyway.
        initial={},
        meet=lambda values: _meet_states(values),
        transfer=transfer,
        render=_render_state_trace,
    )


def _meet_states(states: Iterable[State]) -> State:
    """Meet a whole block's incoming states.

    Iterative rather than recursive so a block with many predecessors cannot exhaust Python's
    stack. ``state_meet`` already treats a name missing from one side as ⊤ there, so
    merging pairwise is associative in the way that matters.
    """
    merged: State | None = None
    for state in states:
        merged = state if merged is None else state_meet(merged, state)
    return merged if merged is not None else {}


def _render_state_trace(state: State) -> dict[str, str]:
    """Render a state compactly for the trace: ``{"x": "CONST(5)", "y": "NAC"}``."""
    return {name: state[name].render() for name in sorted(state)}


def propagate(cfg: CFG) -> Solution[State]:
    """Solve constant propagation over one function's reachable blocks."""
    return solve(_problem(cfg))


def known_before(solution: Solution[State], block_id: str) -> State:
    """The state in effect before ``block_id``'s first instruction."""
    return dict(solution.block_in.get(block_id, {}))


def exit_states(cfg: CFG, solution: Solution[State]) -> list[State]:
    """The state leaving each block that ends the function.

    Delegating the "reaches EXIT" test to the CFG matters: a ``return`` block's successors
    are ``("EXIT",)``, not empty, so testing for an empty successor list silently matches
    nothing and every exit value comes back empty.
    """
    return [
        solution.block_out[block.id]
        for block in cfg.blocks
        if block.reachable
        and block.id in solution.block_out
        and cfg.flows_to_exit(block.id)
    ]


def knowns(cfg: CFG, solution: Solution[State]) -> dict[str, AbstractValue]:
    """The values that hold at the function's exits, merged.

    Merging *only* the exit states matters. Merging every block's OUT would also fold in
    values that held on interior paths and got overwritten before the function returned,
    which would report a constant the program never actually ends up with.
    """
    merged: State | None = None
    for state in exit_states(cfg, solution):
        merged = state if merged is None else state_meet(merged, state)
    return merged if merged is not None else {}
