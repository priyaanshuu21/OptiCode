"""The MiniC type system.

MiniC has exactly three value types plus one non-value type used only for the format
string operand of a ``print`` instruction:

``int``
    32-bit two's complement, wraps on overflow.
``float``
    IEEE-754 double. ``int`` promotes to ``float`` implicitly.
``void``
    Function return type only. A ``void`` value can never be used.
``string``
    Never a value type. It exists solely so a ``print`` instruction can carry its format
    string as a typed constant operand. Arithmetic rejects it explicitly.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "TypeName",
    "COMPARISON_OPS",
    "LOGICAL_OPS",
    "ARITHMETIC_OPS",
    "ASSIGN_OPS",
    "promote",
    "binary_result_type",
    "is_numeric",
]


class TypeName(StrEnum):
    INT = "int"
    FLOAT = "float"
    VOID = "void"
    STRING = "string"


#: Operator groups, keyed by the set that belongs to each group.
COMPARISON_OPS = frozenset({"==", "!=", "<", "<=", ">", ">="})
LOGICAL_OPS = frozenset({"&&", "||"})
ARITHMETIC_OPS = frozenset({"+", "-", "*", "/", "%"})
ASSIGN_OPS = frozenset({"=", "+=", "-=", "*=", "/="})

#: The value types a variable or expression may have.
NUMERIC_TYPES = frozenset({TypeName.INT, TypeName.FLOAT})


def is_numeric(type_name: TypeName) -> bool:
    """True for the types that may hold a runtime value."""
    return type_name in NUMERIC_TYPES


def promote(left: TypeName, right: TypeName) -> TypeName:
    """The type an operation on ``left`` and ``right`` is performed in.

    Any ``float`` operand promotes the whole operation to ``float``. ``void`` and
    ``string`` never promote; they are rejected before this is called.
    """
    if TypeName.FLOAT in (left, right):
        return TypeName.FLOAT
    if TypeName.INT in (left, right):
        return TypeName.INT
    raise ValueError(f"cannot combine {left} and {right}")


def binary_result_type(op: str, left: TypeName, right: TypeName) -> TypeName:
    """The result type of ``left op right``.

    Comparisons and logical operators always yield ``int`` (0 or 1), even when they
    compare two ``float`` values. Arithmetic and modulo promote.
    """
    if op in COMPARISON_OPS or op in LOGICAL_OPS:
        return TypeName.INT
    if op in ARITHMETIC_OPS:
        return promote(left, right)
    raise ValueError(f"not a binary operator: {op!r}")
