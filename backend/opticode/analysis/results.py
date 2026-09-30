"""Turning three solved analyses into one serializable report per function.

The solver produces three independent fixed points. A caller — the API, the CLI, a future
optimization pass — almost never wants them raw: it wants "is this read live, does it have a
single reaching definition, and what is the condition's value", keyed by instruction.

This module derives those per-instruction answers once and puts them behind small frozen
dataclasses with ``to_dict``, so the API surface is a stable shape rather than three
different solution dicts.

Everything is derived from the *solved block* states by walking each block in order, which
is what makes the per-instruction answers consistent with the block fixed point rather than
a second, possibly disagreeing, computation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, TypeAlias

from ..cfg import CFG, BasicBlock
from ..ir.tac import Instruction, Opcode
from .constprop import State, apply_instruction, read_operand
from .lattice import NAC, AbstractValue
from .liveness import NameSet
from .reaching import Definition, DefinitionKind, definition

__all__ = [
    "BranchCondition",
    "ConstantFact",
    "DefSet",
    "DefUseChains",
    "FunctionAnalysis",
    "InstructionFacts",
    "LivenessFact",
    "ReachingFact",
]

#: The set of definitions in effect at a program point.
DefSet: TypeAlias = frozenset[Definition]


@dataclass(frozen=True, slots=True)
class LivenessFact:
    """Whether an instruction's operands are live, and what is live on either side."""

    #: Names live immediately before this instruction.
    live_in: NameSet
    #: Names live immediately after this instruction.
    live_out: NameSet
    #: True when this instruction defines a name that is live immediately after it.
    #:
    #: This is the dead-assignment test, and it is about the **destination**, not the
    #: operands. "Are the operands live?" would be useless: an instruction that reads a name
    #: has that name live on entry by construction, since ``uses(i) ⊆ live_in(i)``, so the
    #: answer would be True for every instruction that reads anything and False for every
    #: instruction that does not — it would never distinguish two real assignments. What
    #: matters is whether the value written survives to a later read, which is exactly
    #: ``dest ∈ live_out(i)``. False means the write can be deleted, subject to the separate
    #: question of whether the instruction has side effects.
    dest_live: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "live_in": sorted(self.live_in),
            "live_out": sorted(self.live_out),
            "dest_live": self.dest_live,
        }


@dataclass(frozen=True, slots=True)
class ReachingFact:
    """Which definitions reach an instruction, and whether it defines anything."""

    #: Reachable definitions in effect before this instruction.
    reaching: DefSet = frozenset()
    #: The definition this instruction makes, if it makes one.
    defines: Definition | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "reaching": sorted(item.render() for item in self.reaching),
            "defines": self.defines.render() if self.defines else None,
        }


@dataclass(frozen=True, slots=True)
class ConstantFact:
    """The abstract value of an instruction's result and of each operand, before it runs."""

    #: The abstract value this instruction produces, or None when it defines nothing.
    result: AbstractValue | None
    #: One abstract value per operand, positionally aligned with the instruction.
    operands: tuple[AbstractValue, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.to_dict() if self.result is not None else None,
            "operands": [item.to_dict() for item in self.operands],
        }


@dataclass(frozen=True, slots=True)
class BranchCondition:
    """One ``if_false``, and what is statically known about its condition."""

    #: The instruction that branches.
    instruction_id: str
    #: The operand tested for zero.
    operand: str
    #: Its abstract value at the branch.
    value: AbstractValue
    #: The block taken when the condition is false.
    false_target: str
    #: The block taken when the condition is true.
    true_target: str

    @property
    def statically_decidable(self) -> bool:
        """True when the condition is a known constant, so one side is dead."""
        return self.value.is_const

    @property
    def always_taken(self) -> str | None:
        """Which successor is always taken, or None when it cannot be decided."""
        if not self.value.is_const:
            return None
        return self.true_target if self.value.value else self.false_target

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction_id": self.instruction_id,
            "operand": self.operand,
            "value": self.value.to_dict(),
            "false_target": self.false_target,
            "true_target": self.true_target,
            "statically_decidable": self.statically_decidable,
            "always_taken": self.always_taken,
        }


@dataclass(frozen=True, slots=True)
class InstructionFacts:
    """All three analyses' answers for one instruction, keyed by instruction id."""

    liveness: LivenessFact
    reaching: ReachingFact
    constants: ConstantFact

    def to_dict(self) -> dict[str, Any]:
        return {
            "liveness": self.liveness.to_dict(),
            "reaching": self.reaching.to_dict(),
            "constants": self.constants.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class DefUseChains:
    """The relation both directions, indexed for lookup in either direction.

    ``uses_of`` answers "where is this definition read?" and ``defs_of`` answers "where does
    this read get its value from?". A read whose only definition is ``UNDEF(v)`` appears in
    ``defs_of`` with that boundary marker, which is how an uninitialized read becomes
    visible without running anything.
    """

    #: Definition → the (instruction id, operand index) pairs that read it.
    uses_of: dict[Definition, tuple[tuple[str, int], ...]] = field(default_factory=dict)
    #: (instruction id, operand index) → the definitions reaching that read.
    defs_of: dict[tuple[str, int], tuple[Definition, ...]] = field(default_factory=dict)

    def uses_of_definition(self, item: Definition) -> tuple[tuple[str, int], ...]:
        """Every ``(instruction id, operand index)`` reading this definition."""
        return self.uses_of.get(item, ())

    def definitions_of_use(self, instruction_id: str, operand_index: int) -> tuple[Definition, ...]:
        """Every definition reaching this particular read."""
        return self.defs_of.get((instruction_id, operand_index), ())

    def uninitialized_reads(self) -> tuple[tuple[str, int], ...]:
        """Every read whose only reaching definition is UNDEF, sorted.

        This is the static counterpart to the interpreter's ``uninitialized_reads``: the same
        program facts, found without running anything. A read that reaches both ``UNDEF(v)``
        and a real write is *not* listed — on that path the value is defined, it is only
        indeterminate on the other.
        """
        found = [
            use
            for use, defs in self.defs_of.items()
            if defs and all(item.kind is DefinitionKind.UNDEF for item in defs)
        ]
        return tuple(sorted(found))

    def to_dict(self) -> dict[str, Any]:
        return {
            "uses_of": {
                item.render(): [list(use) for use in sorted(uses)]
                for item, uses in sorted(self.uses_of.items(), key=lambda pair: pair[0].render())
            },
            "defs_of": {
                f"{use[0]}#{use[1]}": [item.render() for item in sorted(defs)]
                for use, defs in sorted(self.defs_of.items())
            },
        }


@dataclass(frozen=True, slots=True)
class FunctionAnalysis:
    """Everything the three analyses know about one function."""

    #: Function name.
    name: str
    #: Instruction id → its combined facts. Only reachable instructions appear.
    instructions: dict[str, InstructionFacts]
    #: Block id → what is live before the block's first instruction.
    live_in: dict[str, NameSet]
    #: Block id → what is live after the block's last instruction.
    live_out: dict[str, NameSet]
    #: Block id → definitions reaching the block's first instruction.
    reaching_in: dict[str, DefSet]
    #: Block id → definitions leaving the block.
    reaching_out: dict[str, DefSet]
    #: Every branch, with its condition's abstract value.
    branches: tuple[BranchCondition, ...]
    #: The def-use relation for the whole function.
    chains: DefUseChains
    #: Reads whose only reaching definition is UNDEF, as ``"instruction#operand"`` keys.
    uninitialized_reads: tuple[str, ...]
    #: Values known at every exit, merged. A CONST here is a real compile-time constant.
    exit_values: dict[str, AbstractValue]
    #: How many block updates each analysis needed, for the UI.
    iterations: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "instructions": {
                key: self.instructions[key].to_dict() for key in sorted(self.instructions)
            },
            "live_in": {key: sorted(value) for key, value in sorted(self.live_in.items())},
            "live_out": {key: sorted(value) for key, value in sorted(self.live_out.items())},
            "reaching_in": {
                key: sorted(item.render() for item in value)
                for key, value in sorted(self.reaching_in.items())
            },
            "reaching_out": {
                key: sorted(item.render() for item in value)
                for key, value in sorted(self.reaching_out.items())
            },
            "branches": [branch.to_dict() for branch in self.branches],
            "chains": self.chains.to_dict(),
            "uninitialized_reads": list(self.uninitialized_reads),
            "exit_values": {
                key: self.exit_values[key].to_dict() for key in sorted(self.exit_values)
            },
            "iterations": dict(sorted(self.iterations.items())),
        }


def derive_chains(cfg: CFG, block_in: dict[str, DefSet]) -> DefUseChains:
    """Walk every reachable block forward, threading definitions to their uses.

    The state threading here is the same forward walk a reaching analysis performs inside a
    block, applied on top of the solved ``block_in``. That keeps the chains exactly
    consistent with the block fixed point — a use can never be given a definition that the
    block-level analysis had already killed.
    """
    uses_of: dict[Definition, list[tuple[str, int]]] = {}
    defs_of: dict[tuple[str, int], tuple[Definition, ...]] = {}

    for block in cfg.blocks:
        if not block.reachable or block.id not in block_in:
            continue
        # Name → every definition of it in effect right now.
        current: dict[str, list[Definition]] = {}
        for item in block_in[block.id]:
            current.setdefault(item.variable, []).append(item)

        for instruction in block.instructions:
            for index, operand in enumerate(instruction.uses()):
                reached = tuple(sorted(current.get(operand.name, ())))
                defs_of[(instruction.id, index)] = reached
                for item in reached:
                    uses_of.setdefault(item, []).append((instruction.id, index))
            for operand in instruction.defs():
                made = definition(operand.name, instruction.id)
                current[operand.name] = [made]

    return DefUseChains(
        uses_of={key: tuple(sorted(value)) for key, value in uses_of.items()},
        defs_of=dict(sorted(defs_of.items())),
    )


def _constants_fact(state: State, instruction: Instruction) -> ConstantFact:
    """One instruction's operand and result abstract values, read from ``state``.

    ``operands`` are the values on entry, which is what the instruction consumes.
    ``result`` is the value it *produces*: the transfer is applied to a copy of the state
    rather than read out of it directly. Reusing ``apply_instruction`` is deliberate — a
    second hand-written copy of these rules here would be free to drift from the ones the
    solver runs, and the stepper would then show a value the analysis never concluded.
    """
    operands = tuple(read_operand(state, operand) for operand in instruction.operands)
    result: AbstractValue | None = None
    if instruction.dest is not None:
        after = dict(state)
        apply_instruction(after, instruction)
        result = after.get(instruction.dest.name, NAC)
    return ConstantFact(result=result, operands=operands)


def _branch(
    block: BasicBlock, instruction: Instruction, state: State
) -> BranchCondition | None:
    """The ``BranchCondition`` for an ``if_false``, or None for any other instruction."""
    if instruction.opcode is not Opcode.IF_FALSE or not instruction.operands:
        return None
    operand = instruction.operands[0]
    # `block.successors` is [false_target, true_target] by construction: the conditional
    # transfer lists the jump first and the fall-through second.
    false_target = block.successors[0] if block.successors else ""
    true_target = block.successors[1] if len(block.successors) > 1 else ""
    return BranchCondition(
        instruction_id=instruction.id,
        operand=operand.name,
        value=read_operand(state, operand),
        false_target=false_target,
        true_target=true_target,
    )


def build_function_analysis(
    cfg: CFG,
    *,
    live: dict[str, NameSet],
    live_out: dict[str, NameSet],
    reaching_in: dict[str, NameSet],
    reaching_out: dict[str, NameSet],
    states_in: dict[str, State],
    states_out: dict[str, State],
    exit_values: dict[str, AbstractValue],
    iterations: dict[str, int],
) -> FunctionAnalysis:
    """Assemble a :class:`FunctionAnalysis` from the three solved fixed points.

    Each block is walked once, in order, carrying the three states forward. Liveness walks
    backwards (it is defined backwards), the other two walk forwards, so the loop below runs
    the forward walk once and the backward walk once per block.
    """
    from .liveness import live_after, live_before

    instructions: dict[str, InstructionFacts] = {}
    branches: list[BranchCondition] = []

    for block in cfg.blocks:
        if not block.reachable or block.id not in live_out:
            continue
        block_live_out = live_out[block.id]
        per_instruction_in = live_before(block, block_live_out)
        per_instruction_out = live_after(block, block_live_out)

        # Forward walk: thread reaching definitions and the constant state instruction by
        # instruction, so every fact below is read at the point it actually holds.
        reaching_state: dict[str, list[Definition]] = {}
        for item in reaching_in.get(block.id, ()):
            reaching_state.setdefault(item.variable, []).append(item)
        constant_state: State = dict(states_in.get(block.id, {}))

        for instruction in block.instructions:
            reached = frozenset(
                item
                for operand in instruction.uses()
                for item in reaching_state.get(operand.name, ())
            )
            defines = (
                definition(instruction.dest.name, instruction.id)
                if instruction.dest is not None
                else None
            )
            operand_values = _constants_fact(constant_state, instruction)
            instructions[instruction.id] = InstructionFacts(
                liveness=LivenessFact(
                    live_in=per_instruction_in.get(instruction.id, frozenset()),
                    live_out=per_instruction_out.get(instruction.id, frozenset()),
                    dest_live=instruction.dest is not None
                    and instruction.dest.name
                    in per_instruction_out.get(instruction.id, frozenset()),
                ),
                reaching=ReachingFact(reaching=reached, defines=defines),
                constants=operand_values,
            )
            branch = _branch(block, instruction, constant_state)
            if branch is not None:
                branches.append(branch)
            for operand in instruction.defs():
                reaching_state[operand.name] = [definition(operand.name, instruction.id)]
            # Advance the constant state past this instruction, so the next instruction's
            # operands are read against values that actually hold at that point.
            apply_instruction(constant_state, instruction)

    chains = derive_chains(cfg, reaching_in)
    return FunctionAnalysis(
        name=cfg.function.name,
        instructions=instructions,
        live_in=dict(live),
        live_out=dict(live_out),
        reaching_in=dict(reaching_in),
        reaching_out=dict(reaching_out),
        branches=tuple(branches),
        chains=chains,
        uninitialized_reads=tuple(
            f"{use[0]}#{use[1]}" for use in chains.uninitialized_reads()
        ),
        exit_values=dict(exit_values),
        iterations=dict(iterations),
    )
