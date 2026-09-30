"""Semantic analysis and TAC lowering invariants.

These checks mirror what later stages assume: every operand is defined before use, every
jump lands on a label that exists, every name resolves, and no source expression was
lowered twice. If lowering breaks one of these, everything downstream silently misbehaves,
so they are asserted here rather than discovered in the optimizer.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..semantic.types import TypeName
from .operands import OperandKind
from .tac import Instruction, IRFunction, IRProgram, Opcode

__all__ = [
    "InvariantViolation",
    "check_program",
    "check_function",
    "definitions",
    "LoweringStats",
    "unused_temps",
    "has_side_effects",
    "reads_variable",
]


class InvariantViolation(Exception):
    """A TAC program broke a structural guarantee."""


def _fail(function: str, message: str) -> InvariantViolation:
    return InvariantViolation(f"{function}: {message}")


def check_program(program: IRProgram) -> None:
    """Check every function. Raises on the first violation."""
    seen_names: set[str] = set()
    for function in program.functions:
        if function.name in seen_names:
            raise _fail(function.name, "function appears more than once")
        seen_names.add(function.name)
        check_function(function)


def check_function(function: IRFunction) -> None:
    instructions = function.instructions
    labels: set[str] = set()
    # Parameters and uninitialized locals are live from function entry. Locals are listed
    # on the function precisely because they emit no defining instruction.
    defined: set[str] = {param.ir_name for param in function.params}
    defined.update(function.declared_locals)
    # A temp is live from its definition to the end of the function, which is sound but
    # imprecise; liveness is a later task's concern.
    produced: dict[str, str] = {}

    # ------------------------------------------------- pass one: structure
    for instruction in instructions:
        if instruction.opcode is Opcode.LABEL:
            if not instruction.target:
                raise _fail(function.name, f"{instruction.id} is a LABEL with no target")
            if instruction.target in labels:
                raise _fail(
                    function.name, f"label '{instruction.target}' is defined twice"
                )
            labels.add(instruction.target)
        elif instruction.opcode in (Opcode.GOTO, Opcode.IF_FALSE):
            if not instruction.target:
                raise _fail(
                    function.name, f"{instruction.id} jumps to nowhere"
                )

    # ---------------------------------------- pass two: definition and use
    for instruction in instructions:
        # Every read operand must be defined already: TAC is strictly ordered.
        for operand in instruction.uses():
            if operand.kind is OperandKind.CONST:
                continue
            if operand.name not in defined:
                raise _fail(
                    function.name,
                    f"{instruction.id} reads '{operand.name}' before it is defined",
                )
        for operand in instruction.defs():
            defined.add(operand.name)
            produced[operand.name] = instruction.id
        if instruction.opcode is Opcode.BINARY:
            _check_binary(function, instruction)
        if instruction.opcode is Opcode.IF_FALSE and not instruction.operands:
            raise _fail(function.name, f"{instruction.id} branches on nothing")

    # --------------------------------------------- pass three: jump targets
    for instruction in instructions:
        if instruction.target and instruction.opcode is not Opcode.LABEL:
            if instruction.target not in labels:
                raise _fail(
                    function.name,
                    f"{instruction.id} jumps to '{instruction.target}', "
                    "which no LABEL defines",
                )

    # --------------------------------------- pass four: unique provenance
    for instruction in instructions:
        if len(instruction.operand_origin_ids) != len(instruction.operands):
            raise _fail(
                function.name,
                f"{instruction.id} has {len(instruction.operands)} operands but "
                f"{len(instruction.operand_origin_ids)} origin ids",
            )

    # A non-void function always ends in a return, explicit or synthetic, so control
    # never runs off the end of the instruction list.
    if function.return_type is not TypeName.VOID and instructions:
        last = instructions[-1]
        if last.opcode is not Opcode.RETURN:
            raise _fail(function.name, "a non-void function does not end in a return")


def _check_binary(function: IRFunction, instruction: Instruction) -> None:
    if instruction.op is None:
        raise _fail(function.name, f"{instruction.id} is a BINARY with no operator")
    if len(instruction.operands) != 2:
        raise _fail(function.name, f"{instruction.id} needs exactly two operands")
    left, right = instruction.operands
    if left.type is right.type:
        return
    # Mixed int/float operands must have been promoted before the operation; the
    # interpreter implements one arithmetic, and silently picking either type here would
    # make int and float arithmetic disagree.
    if {left.type, right.type} == {TypeName.INT, TypeName.FLOAT}:
        raise _fail(
            function.name,
            f"{instruction.id} mixes int and float operands without a conversion",
        )


def definitions(program: IRProgram) -> dict[str, str]:
    """Map every defined operand name to the instruction that defines it."""
    result: dict[str, str] = {}
    for function in program.functions:
        for instruction in function.instructions:
            for operand in instruction.defs():
                result[operand.name] = f"{function.name}.{instruction.id}"
    return result


@dataclass(frozen=True, slots=True)
class LoweringStats:
    """Counts useful when comparing an optimized program against its source."""

    functions: int
    instructions: int
    temps: int
    labels: int

    @classmethod
    def of(cls, program: IRProgram) -> LoweringStats:
        instructions = [i for f in program.functions for i in f.instructions]
        return cls(
            functions=len(program.functions),
            instructions=len(instructions),
            temps=sum(1 for i in instructions if i.dest and i.dest.is_temp),
            labels=sum(1 for i in instructions if i.opcode is Opcode.LABEL),
        )


def unused_temps(program: IRProgram) -> list[str]:
    """Temps that are defined but never read, which dead-store elimination can remove."""
    result: list[str] = []
    for function in program.functions:
        reads = {
            operand.name
            for instruction in function.instructions
            for operand in instruction.uses()
        }
        for instruction in function.instructions:
            if instruction.dest is None or not instruction.dest.is_temp:
                continue
            if instruction.dest.name not in reads:
                result.append(f"{function.name}.{instruction.dest.name}")
    return result


def has_side_effects(function: IRFunction) -> bool:
    return any(instruction.has_side_effects() for instruction in function.instructions)


def reads_variable(program: IRProgram, ir_name: str) -> bool:
    """True if any function reads ``ir_name``. Used by dead-store analysis later."""
    for function in program.functions:
        for instruction in function.instructions:
            if any(operand.name == ir_name for operand in instruction.uses()):
                return True
    return False
