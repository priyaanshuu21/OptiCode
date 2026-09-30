r"""The abstract domain for constant propagation.

Every variable's abstract value at a program point is one of three things:

* **⊤ (TOP)** — no information yet. Reached only before the first pass or on a value that
  has not been solved; the transfer rules below make it vanish on all reachable code.
* **CONST(c)** — provably the value ``c``.
* **NAC** — not a compile-time constant. Merging two different constants gives NAC, and so
  does anything whose value depends on a call, on input, or on a possible trap.

The lattice is a three-element flat diamond::

        ⊤
       / \
    CONST(a) NAC
       \ /
        (join of different constants)

with ``CONST(a) ⊓ CONST(b) = NAC`` for ``a ≠ b``. That single rule is what makes
``x = 5`` on one branch and ``x = 6`` on the other come out unknown while two branches that
both assign ``5`` stay known.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from ..semantics.arith import DivisionByZero, arith_binary, arith_unary, truncate_to_int

__all__ = [
    "NAC",
    "TOP",
    "AbstractValue",
    "ValueKind",
    "const",
    "fold_binary",
    "fold_ftoi",
    "fold_itof",
    "fold_unary",
    "render_state",
    "state_meet",
]


class ValueKind(StrEnum):
    TOP = "top"
    CONST = "const"
    NAC = "nac"


@dataclass(frozen=True, slots=True)
class AbstractValue:
    """One variable's abstract value.

    The payload is tagged with the MiniC type, not just its Python type, so that ``CONST(5)``
    (``int``) and ``CONST(5.0)`` (``float``) never meet into a value that is true of
    neither. ``type_name`` is ``""`` for TOP and NAC.
    """

    kind: ValueKind
    value: float | int | None = None
    type_name: str = ""

    def __str__(self) -> str:
        if self.kind is not ValueKind.CONST:
            return self.kind.value.upper()
        if self.type_name == "float" and isinstance(self.value, int):
            return f"{self.value}.0"
        return repr(self.value)

    # ------------------------------------------------------------ predicates

    @property
    def is_const(self) -> bool:
        return self.kind is ValueKind.CONST

    @property
    def is_top(self) -> bool:
        return self.kind is ValueKind.TOP

    @property
    def is_nac(self) -> bool:
        return self.kind is ValueKind.NAC

    # ------------------------------------------------------------------ meet

    def meet(self, other: AbstractValue) -> AbstractValue:
        """The least upper bound of two values at a join point.

        * ``⊤ ⊓ x = x`` — no information meets anything without losing it.
        * ``NAC ⊓ x = NAC`` — once unknown, always unknown.
        * ``CONST(a) ⊓ CONST(a) = CONST(a)``, but only for the same type.
        * ``CONST(a) ⊓ CONST(b) = NAC`` for ``a ≠ b`` or a differing type.
        """
        if self.kind is ValueKind.TOP:
            return other
        if other.kind is ValueKind.TOP:
            return self
        if self.is_nac or other.is_nac:
            return NAC
        if self.type_name == other.type_name and self.value == other.value:
            return self
        return NAC

    # ---------------------------------------------------------------- render

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind.value}
        if self.is_const:
            payload["value"] = self.value
            payload["type"] = self.type_name
        return payload

    def render(self) -> str:
        """A compact string for the iteration trace.

        The trace is read by a human watching rounds go by, so ``CONST(5)`` and ``NAC`` are
        clearer than a nested JSON object — and the trace is capped anyway.
        """
        return f"CONST({self})" if self.is_const else self.kind.value.upper()


TOP: Final[AbstractValue] = AbstractValue(ValueKind.TOP)
NAC: Final[AbstractValue] = AbstractValue(ValueKind.NAC)


def const(value: float | int) -> AbstractValue:
    """Build a CONST from a Python value, tagged with its MiniC type."""
    return (
        AbstractValue(ValueKind.CONST, int(value), "int")
        if isinstance(value, int) and not isinstance(value, bool)
        else AbstractValue(ValueKind.CONST, float(value), "float")
    )


# --------------------------------------------------------------- transfer rules


def _not_a_constant(candidate: float | int) -> AbstractValue | None:
    """Reject a folded result that is not a usable constant, else tag its type."""
    if isinstance(candidate, float) and not math.isfinite(candidate):
        # The interpreter would have produced this, but it is not a value the UI can show as
        # a plain constant, and folding it risks reading it back as an ordinary number.
        return None
    return const(candidate)


def fold_binary(
    op: str, left: AbstractValue, right: AbstractValue, result_is_float: bool = False
) -> AbstractValue:
    """Fold ``left op right`` when that is provably what the program computes.

    Returns ⊤ while an operand is still unknown and NAC when the operation could not be
    performed at compile time:

    * a division or modulo by a constant zero traps, so it has no value to fold to;
    * a float result that is infinite or NaN is not representable as a constant;
    * ``%`` on floats is rejected by MiniC itself, so it stays NAC.
    """
    if left.is_top or right.is_top:
        return TOP
    if left.is_nac or right.is_nac or left.value is None or right.value is None:
        return NAC
    if op in ("/", "%") and right.value == 0:
        return NAC
    try:
        result = arith_binary(op, left.value, right.value)
    except (DivisionByZero, ValueError, OverflowError, ZeroDivisionError):
        return NAC
    folded = _not_a_constant(result)
    if folded is None:
        return NAC
    if result_is_float and folded.type_name == "int":
        # The lowering promoted an operand to float, so the program really does hold a
        # float even though the arithmetic came out integral.
        return AbstractValue(ValueKind.CONST, float(result), "float")
    return folded


def fold_unary(op: str, operand: AbstractValue) -> AbstractValue:
    """Fold a unary operation under the same rules as :func:`fold_binary`."""
    if operand.is_top:
        return TOP
    if operand.is_nac or operand.value is None:
        return NAC
    try:
        result = arith_unary(op, operand.value)
    except (ValueError, OverflowError, DivisionByZero):  # pragma: no cover - defensive
        return NAC
    folded = _not_a_constant(result)
    return folded if folded is not None else NAC


def fold_itof(operand: AbstractValue) -> AbstractValue:
    """``itof``: exact for every int, so always foldable when the operand is known."""
    if operand.is_top:
        return TOP
    if operand.is_nac or operand.value is None:
        return NAC
    return AbstractValue(ValueKind.CONST, float(operand.value), "float")


def fold_ftoi(operand: AbstractValue) -> AbstractValue:
    """``ftoi``: truncate toward zero, which is exactly what the interpreter does."""
    if operand.is_top:
        return TOP
    if operand.is_nac or operand.value is None:
        return NAC
    if isinstance(operand.value, float) and not math.isfinite(operand.value):
        return NAC
    return AbstractValue(ValueKind.CONST, truncate_to_int(float(operand.value)), "int")


def state_meet(
    left: dict[str, AbstractValue], right: dict[str, AbstractValue]
) -> dict[str, AbstractValue]:
    """Meet two whole variable states, pointwise.

    A name missing from one side is treated as ⊤ there, so the present side wins rather than
    collapsing the variable to NAC. Variables only appear on one side transiently, before
    a transfer has introduced them everywhere.
    """
    merged: dict[str, AbstractValue] = {}
    for name in sorted(set(left) | set(right)):
        if name not in left:
            merged[name] = right[name]
        elif name not in right:
            merged[name] = left[name]
        else:
            merged[name] = left[name].meet(right[name])
    return merged


def render_state(state: dict[str, AbstractValue]) -> dict[str, Any]:
    """Serialize a whole variable state, sorted by name."""
    return {name: state[name].to_dict() for name in sorted(state)}
