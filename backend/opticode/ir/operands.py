"""TAC operands.

An operand is a typed value: a program variable, a compiler temporary, or a literal.
Every operand carries its type, so later passes never need the symbol table to know
whether a value is an ``int`` or a ``float``.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..semantic.types import TypeName

__all__ = ["OperandKind", "Operand", "format_float", "quote"]


class OperandKind(StrEnum):
    VAR = "var"
    TEMP = "temp"
    CONST = "const"


@dataclass(frozen=True, slots=True)
class Operand:
    """One typed TAC value.

    ``name`` is set for variables and temporaries. ``value`` is set for constants only,
    and its Python type determines the constant's type: ``int`` → ``int``, ``float`` →
    ``float``, ``str`` → ``string`` (the format operand of a ``print``).
    """

    kind: OperandKind
    type: TypeName
    name: str = ""
    value: int | float | str | None = None

    # ----------------------------------------------------------- constructors

    @classmethod
    def var(cls, name: str, type_name: TypeName) -> Operand:
        return cls(OperandKind.VAR, type_name, name=name)

    @classmethod
    def temp(cls, name: str, type_name: TypeName) -> Operand:
        return cls(OperandKind.TEMP, type_name, name=name)

    @classmethod
    def const(cls, value: int | float | str) -> Operand:
        if isinstance(value, bool):  # pragma: no cover - defensive
            raise TypeError("bool is not a MiniC constant")
        if isinstance(value, int):
            return cls(OperandKind.CONST, TypeName.INT, value=value)
        if isinstance(value, float):
            return cls(OperandKind.CONST, TypeName.FLOAT, value=value)
        return cls(OperandKind.CONST, TypeName.STRING, value=value)

    # ------------------------------------------------------------ predicates

    @property
    def is_const(self) -> bool:
        return self.kind is OperandKind.CONST

    @property
    def is_var(self) -> bool:
        return self.kind is OperandKind.VAR

    @property
    def is_temp(self) -> bool:
        return self.kind is OperandKind.TEMP

    @property
    def is_zero(self) -> bool:
        """True for a literal zero of either numeric type."""
        return self.is_const and self.type is not TypeName.STRING and self.value == 0

    @property
    def display(self) -> str:
        """The textual form used by the TAC printer."""
        if self.is_const:
            if self.type is TypeName.STRING:
                return quote(str(self.value))
            if self.type is TypeName.FLOAT:
                return format_float(float(self.value))
            return str(int(self.value))  # type: ignore[arg-type]
        return self.name

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"kind": self.kind.value, "type": self.type.value}
        if self.is_const:
            payload["value"] = self.value
        else:
            payload["name"] = self.name
        return payload


def format_float(value: float) -> str:
    """Render a float the way the printer and the interpreter both expect.

    ``2.0`` stays ``2.0`` rather than collapsing to ``2``, so a float constant is always
    visibly a float in TAC dumps.
    """
    text = repr(float(value))
    if "e" in text or "E" in text or "." in text:
        return text
    return text + ".0"


def quote(text: str) -> str:
    """Render a string literal the way the TAC printer shows format strings."""
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'
