"""Three-address code: the last compiler stage before optimization.

Public surface:

* :func:`lower` — turn a :class:`~opticode.semantic.analyzer.SemanticResult` into TAC.
* :class:`IRProgram` / :class:`IRFunction` / :class:`Instruction` — the IR itself.
* :func:`print_program` — a human-readable dump for tests and the frontend.
* :func:`serialize_ir` — the API payload.
"""

from __future__ import annotations

from .invariants import (
    InvariantViolation,
    LoweringStats,
    check_function,
    check_program,
    unused_temps,
)
from .lower import LowerError, LowerResult, lower
from .operands import Operand, OperandKind
from .printer import print_function, print_instruction, print_program, render_program
from .serialize import serialize_ir
from .tac import Instruction, IRFunction, IRParam, IRProgram, Opcode

__all__ = [
    "lower",
    "LowerError",
    "LowerResult",
    "check_program",
    "check_function",
    "InvariantViolation",
    "LoweringStats",
    "unused_temps",
    "Operand",
    "OperandKind",
    "IRFunction",
    "IRParam",
    "IRProgram",
    "Instruction",
    "Opcode",
    "print_instruction",
    "print_function",
    "print_program",
    "render_program",
    "serialize_ir",
]
