"""Serialization of the TAC stage for the API.

The JSON mirrors what the frontend needs to draw the graph and step through it:
instructions with ids, resolved operands, source spans, and the AST node each came from.
"""

from __future__ import annotations

from ..semantic.types import TypeName
from .printer import print_program
from .tac import Instruction, IRProgram, Opcode

__all__ = ["serialize_ir", "serialize_instruction"]


def serialize_instruction(instruction: Instruction) -> dict[str, object]:
    payload = instruction.to_dict()
    # The frontend needs the queries without reimplementing them, so the derived facts
    # travel with the instruction rather than being recomputed on every render.
    payload["uses"] = [operand.to_dict() for operand in instruction.uses()]
    payload["defs"] = [operand.to_dict() for operand in instruction.defs()]
    payload["has_side_effects"] = instruction.has_side_effects()
    payload["may_trap"] = instruction.may_trap()
    return payload


def serialize_ir(program: IRProgram) -> dict[str, object]:
    functions = []
    for function in program.functions:
        payload = function.to_dict()
        payload["instructions"] = [
            serialize_instruction(instruction) for instruction in function.instructions
        ]
        payload["labels"] = [
            instruction.target
            for instruction in function.instructions
            if instruction.opcode is Opcode.LABEL
        ]
        payload["temp_count"] = sum(
            1 for instruction in function.instructions if instruction.is_temp_def()
        )
        functions.append(payload)

    return {
        "functions": functions,
        "function_count": len(program.functions),
        "instruction_count": sum(len(f.instructions) for f in program.functions),
        "types": [type_name.value for type_name in TypeName],
        # The same text the printer produces, so the UI can show TAC verbatim without
        # reimplementing the format and risking a mismatch with the golden dumps.
        "text": print_program(program),
    }
