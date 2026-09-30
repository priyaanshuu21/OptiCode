"""Execution semantics shared by the interpreter and the optimizer.

:mod:`opticode.semantics.arith` owns arithmetic; :mod:`opticode.semantics.format` owns
value rendering. Neither module knows anything about instructions, functions, or the
pipeline, which keeps them usable from both the interpreter and the constant folder.
"""

from __future__ import annotations

from .arith import (
    INT_MAX,
    INT_MIN,
    DivisionByZero,
    arith_binary,
    arith_unary,
    truncate_to_int,
    truthy,
    wrap_int,
)
from .format import escape_format, format_float, render_format

__all__ = [
    "INT_MAX",
    "INT_MIN",
    "DivisionByZero",
    "arith_binary",
    "arith_unary",
    "escape_format",
    "format_float",
    "render_format",
    "truncate_to_int",
    "truthy",
    "wrap_int",
]
