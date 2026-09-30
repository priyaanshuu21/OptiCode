"""Reaching definitions, and the def-use chains they produce.

A *definition* reaches a program point when that definition is guaranteed to be the one in
effect there — every path to the point passes through it, and no later definition of the
same name has overwritten it. Reaching definitions answer "which write is this read getting
its value from?", which is what makes a read removable when every one of its reaching
definitions is the same constant.

The equations, with ``IN[B]`` reaching ``B`` and ``OUT[B]`` leaving it::

    OUT[ENTRY] = { PARAM(v) : v ∈ params } ∪ { UNDEF(v) : v ∈ locals }
    IN[B]      = ⋃ OUT[P]         for every predecessor P of B
    OUT[B]     = GEN[B] ∪ (IN[B] \\ KILL[B])

``GEN[B]`` is every definition ``B`` makes; ``KILL[B]`` is every *other* definition of those
same names anywhere in the function. Union is the meet, so this lattice's bottom is ∅.

Two boundary definitions matter, and neither corresponds to a real instruction:

* ``PARAM(v)`` — ``v`` arrived from the caller, so no write in this body created it.
* ``UNDEF(v)`` — ``v`` is a local nothing has assigned, so a read reaching only ``UNDEF``
  reads an indeterminate value.

Both are reported rather than dropped, because "this read has no definition" and "this read
has exactly one" are very different things to a user reading a report.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from ..cfg import CFG, BasicBlock
from .solver import DataFlowProblem, Direction, Solution, solve

__all__ = [
    "NO_DEFS",
    "Definition",
    "DefinitionKind",
    "definition",
    "param",
    "reaching",
    "render_reaching",
    "undef",
]


class DefinitionKind(StrEnum):
    """Where a definition came from."""

    #: Written by a TAC instruction; the instruction id names it.
    INSTRUCTION = "instruction"
    #: A parameter, defined by the caller's act of calling.
    PARAM = "param"
    #: A local that nothing has assigned on this path.
    UNDEF = "undef"


@dataclass(frozen=True, slots=True, order=True)
class Definition:
    """One reaching definition.

    A value object rather than a bare tuple, because ``PARAM(v)`` and ``UNDEF(v)`` are both
    "no instruction behind this" and a tuple of two strings could not tell them apart. The
    kind travels with the definition so serialization and the UI can name it correctly.
    """

    #: The variable this defines.
    variable: str
    #: The defining instruction's id, or "" for a boundary definition.
    instruction_id: str
    kind: DefinitionKind

    @property
    def is_boundary(self) -> bool:
        """True for PARAM and UNDEF, which no instruction in this function produced."""
        return self.kind is not DefinitionKind.INSTRUCTION

    def render(self) -> str:
        """A stable, human-readable form used in traces and API payloads."""
        if self.kind is DefinitionKind.PARAM:
            return f"PARAM({self.variable})"
        if self.kind is DefinitionKind.UNDEF:
            return f"UNDEF({self.variable})"
        return f"{self.variable}@{self.instruction_id}"

    def to_dict(self) -> dict[str, str]:
        return {
            "variable": self.variable,
            "instruction_id": self.instruction_id,
            "kind": self.kind.value,
        }


#: The empty reaching set — this lattice's bottom.
NO_DEFS: Final[frozenset[Definition]] = frozenset()


def definition(variable: str, instruction_id: str) -> Definition:
    """A definition made by instruction ``instruction_id``."""
    return Definition(variable, instruction_id, DefinitionKind.INSTRUCTION)


def param(variable: str) -> Definition:
    """The pseudo-definition marking a parameter as arriving from the caller."""
    return Definition(variable, "", DefinitionKind.PARAM)


def undef(variable: str) -> Definition:
    """The pseudo-definition marking a local that nothing has assigned."""
    return Definition(variable, "", DefinitionKind.UNDEF)


def _boundary(cfg: CFG) -> frozenset[Definition]:
    """``OUT[ENTRY]``: parameters arrive defined; locals start out undefined."""
    function = cfg.function
    incoming = {param(item.ir_name) for item in function.params}
    # A local declared without an initializer holds nothing the program can rely on, so at
    # entry it is UNDEF until an assignment defines it. This matches the interpreter, which
    # seeds such locals to 0 but records a read that happens before any write.
    incoming |= {undef(name) for name in function.declared_locals}
    return frozenset(incoming)


def _build_gen_kill(cfg: CFG) -> dict[str, tuple[frozenset[Definition], frozenset[Definition]]]:
    """``GEN`` and ``KILL`` per reachable block, computed once.

    ``GEN[B]`` keeps only the **last** definition of each name in ``B``. A block that writes
    ``x`` twice generates just the second write, because the first is overwritten inside the
    same block and is already gone before the block's exit — keeping both would make ``x``
    appear to have two reaching definitions where only one can ever be in effect.

    ``KILL[B]`` names every definition of those names *anywhere*, including earlier writes in
    ``B`` itself, so it cannot be derived from ``B`` alone. Precomputing keeps the transfer
    function a pure set operation instead of a repeated whole-function scan.

    Boundary definitions are killable too, and that is the subtle part: ``PARAM(v)`` and
    ``UNDEF(v)`` are not written by any instruction, so a scan of instruction definitions
    alone would leave them reaching every later point forever. Once a block assigns ``v``,
    neither is the value in effect any more, so both join ``KILL``. They remain in ``IN`` at
    the entry boundary, which is what makes a read *before* the first write still report
    ``UNDEF``.
    """
    # Every definition made by an instruction, across reachable code.
    all_definitions: list[Definition] = []
    for block in cfg.blocks:
        if not block.reachable:
            continue
        for instruction in block.instructions:
            for operand in instruction.defs():
                all_definitions.append(definition(operand.name, instruction.id))

    gen_kill: dict[str, tuple[frozenset[Definition], frozenset[Definition]]] = {}
    for block in cfg.blocks:
        if not block.reachable:
            continue
        # Last write per name wins, so dict assignment order does the work.
        last_write: dict[str, Definition] = {}
        for instruction in block.instructions:
            for operand in instruction.defs():
                last_write[operand.name] = definition(operand.name, instruction.id)
        if not last_write:
            gen_kill[block.id] = (NO_DEFS, NO_DEFS)
            continue
        killed = {item for item in all_definitions if item.variable in last_write}
        for name in last_write:
            killed.add(param(name))
            killed.add(undef(name))
        gen_kill[block.id] = (frozenset(last_write.values()), frozenset(killed))
    return gen_kill


def transfer(
    block: BasicBlock,
    incoming: frozenset[Definition],
    gen_kill: dict[str, tuple[frozenset[Definition], frozenset[Definition]]],
) -> frozenset[Definition]:
    """``OUT[B] = GEN[B] ∪ (IN[B] \\ KILL[B])`` for one block."""
    gen, kill = gen_kill[block.id]
    if not gen:
        return incoming
    return gen | (incoming - kill)


def _meet(values: Iterable[frozenset[Definition]]) -> frozenset[Definition]:
    combined: set[Definition] = set()
    for value in values:
        combined |= value
    return frozenset(combined)


def render_reaching(state: frozenset[Definition]) -> list[str]:
    """Serialize a reaching set as a sorted list of rendered definitions."""
    return sorted(item.render() for item in state)


def _problem(cfg: CFG) -> DataFlowProblem[frozenset[Definition]]:
    gen_kill = _build_gen_kill(cfg)
    return DataFlowProblem(
        name="reaching",
        direction=Direction.FORWARD,
        cfg=cfg,
        boundary=_boundary(cfg),
        # Forward bottom: nothing reaches a block nothing flows into. The entry boundary,
        # not this seed, is what actually starts the propagation.
        initial=NO_DEFS,
        meet=_meet,
        transfer=lambda block, incoming: transfer(block, incoming, gen_kill),
        render=render_reaching,
    )


def reaching(cfg: CFG) -> Solution[frozenset[Definition]]:
    """Solve reaching definitions over one function's reachable blocks."""
    return solve(_problem(cfg))
