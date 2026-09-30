"""Human-readable TAC dumps.

The printed form is what the frontend's TAC panel and the golden-file tests both use, so
it is deliberately plain: one instruction per line, ids and spans in trailing columns, and
no attempt to be re-parsable MiniC. Comments above each function give the type context a
reader needs to decode the bare `t3`, `i7`, `L2` names.
"""

from __future__ import annotations

from .operands import Operand, quote
from .tac import Instruction, IRFunction, IRProgram, Opcode

__all__ = ["print_instruction", "print_function", "print_program", "render_program"]


def print_instruction(instruction: Instruction) -> str:
    """Render one instruction in its canonical form."""
    prefix = f"{instruction.id}: "
    body = _body(instruction)
    suffix = ""
    if instruction.synthetic:
        suffix += "  [synthetic]"
    if instruction.may_trap():
        suffix += "  [may trap]"
    return f"{prefix}{body}{suffix}"


def _body(instruction: Instruction) -> str:
    opcode = instruction.opcode
    match opcode:
        case Opcode.COPY:
            return f"{_dest(instruction)} = {_operand(instruction.operands[0])}"
        case Opcode.BINARY:
            return (
                f"{_dest(instruction)} = {_operand(instruction.operands[0])} "
                f"{instruction.op} {_operand(instruction.operands[1])}"
            )
        case Opcode.UNARY:
            return f"{_dest(instruction)} = {instruction.op} {_operand(instruction.operands[0])}"
        case Opcode.ITOF:
            return f"{_dest(instruction)} = itof {_operand(instruction.operands[0])}"
        case Opcode.FTOI:
            return f"{_dest(instruction)} = ftoi {_operand(instruction.operands[0])}"
        case Opcode.LABEL:
            return f"{instruction.target}:"
        case Opcode.GOTO:
            return f"goto {instruction.target}"
        case Opcode.IF_FALSE:
            return f"if_false {_operand(instruction.operands[0])} goto {instruction.target}"
        case Opcode.CALL:
            arguments = ", ".join(_operand(operand) for operand in instruction.operands)
            call = f"{instruction.callee}({arguments})"
            return f"{_dest(instruction)} = {call}" if instruction.dest else call
        case Opcode.PRINT:
            arguments = ", ".join(_operand(operand) for operand in instruction.operands)
            printed = f"print {quote(instruction.format or '')}"
            return f"{printed}, {arguments}" if arguments else printed
        case Opcode.READ:
            return f"read {_dest(instruction)}"
        case Opcode.RETURN:
            if instruction.operands:
                return f"return {_operand(instruction.operands[0])}"
            return "return"
    return f"<unknown {opcode}>"  # pragma: no cover - defensive


def _dest(instruction: Instruction) -> str:
    return instruction.dest.display if instruction.dest is not None else "?"


def _operand(operand: Operand) -> str:
    return operand.display


def print_function(function: IRFunction) -> list[str]:
    """Render one function, including a header comment with its signature."""
    params = ", ".join(f"{param.type.value} {param.name}" for param in function.params)
    lines = [
        f"# {function.name}({params}) -> {function.return_type.value}",
        "#   params: " + ", ".join(param.ir_name for param in function.params),
    ]
    for instruction in function.instructions:
        lines.append("    " + print_instruction(instruction))
    if not function.instructions:
        lines.append("    <empty>")
    return lines


def print_program(program: IRProgram) -> str:
    return "\n".join(render_program(program))


def render_program(program: IRProgram) -> list[str]:
    lines: list[str] = []
    for index, function in enumerate(program.functions):
        if index:
            lines.append("")
        lines.extend(print_function(function))
    if not program.functions:
        lines.append("<no functions>")
    return lines
