"""Arithmetic semantics: the single source of truth for what an operator *means*.

This module is deliberately tiny and free of compiler knowledge. Two very different parts
of the compiler need identical answers to "what is ``-7 / 2``":

* the **interpreter**, which runs TAC and is the execution oracle for correctness testing;
* the **constant folder**, which folds arithmetic on literal operands at compile time.

If they each had their own copy of the rules they would eventually disagree, an
optimization would change behaviour, and the bug would show up as a wrong answer rather
than a failed assertion. So both call into here.

Rules, in the order they are applied:

* ``int`` is 32-bit two's complement and **wraps** on overflow, exactly like C on a
  machine with 32-bit ints. Overflow is not an error.
* ``float`` is an IEEE-754 double, so overflow to infinity and division by zero to
  infinity follow IEEE rather than trapping.
* ``/`` and ``%`` **truncate toward zero**, so ``-7 / 2`` is ``-3``, and the sign of a
  ``%`` result follows the **dividend**: ``-7 % 2`` is ``-1``.
* ``/`` and ``%`` by zero is a **trap** for both ``int`` and ``float``. MiniC has no
  infinity for ``int``, so there is nothing sensible to return.
* ``itof`` is exact up to double precision; ``ftoi`` truncates toward zero.
* Comparisons and logical operators produce ``int`` 0 or 1.
"""

from __future__ import annotations

from typing import Final

from ..semantic.types import TypeName

__all__ = [
    "INT_MIN",
    "INT_MAX",
    "DivisionByZero",
    "arith_binary",
    "arith_unary",
    "truncate_to_int",
    "wrap_int",
    "truthy",
]

#: 32-bit signed range. Wraparound is defined against these, not against Python's ints.
INT_MIN: Final = -(2**31)
INT_MAX: Final = 2**31 - 1


class DivisionByZero(ArithmeticError):
    """Raised by ``/`` and ``%`` when the divisor is zero.

    The interpreter turns this into a ``trap`` status; the constant folder treats it as
    "not foldable" and leaves the operation in place.
    """


def wrap_int(value: int) -> int:
    """Reduce ``value`` to the signed 32-bit range.

    Python integers are unbounded, so every ``int`` result has to pass through here for
    the interpreter and the folder to agree on overflow.
    """
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def truncate_to_int(value: float) -> int:
    """Convert a float to an ``int`` by truncating toward zero, then wrapping.

    C leaves a float outside the ``int`` range undefined. Wrapping is a defined,
    testable choice, and it keeps behaviour deterministic instead of implementation
    defined.
    """
    if value != value:  # NaN
        return 0
    return wrap_int(int(value))


def truthy(value: float | int) -> bool:
    """C truthiness: zero is false, everything else — including NaN — is true."""
    return value != 0


def _as_float(value: float | int) -> float:
    return value if isinstance(value, float) else float(value)


def _check_zero(divisor: float | int) -> None:
    # Both int and float divide-by-zero trap in MiniC; Python's float division would
    # silently produce inf/nan, which would hide the bug this oracle exists to catch.
    if divisor == 0:
        raise DivisionByZero("division by zero")


def arith_binary(op: str, left: float | int, right: float | int) -> float | int:
    """Evaluate ``left op right`` under MiniC's arithmetic rules.

    The result is a ``float`` when either operand is a ``float``, and an ``int``
    otherwise. Comparisons return ``int`` 0 or 1.

    Raises :class:`ValueError` for an unknown operator and :class:`DivisionByZero` for
    ``/`` and ``%`` by zero.
    """
    if isinstance(left, float) or isinstance(right, float):
        return _float_binary(op, _as_float(left), _as_float(right))
    if isinstance(left, int) and isinstance(right, int):
        return _int_binary(op, left, right)
    raise ValueError(f"cannot combine {type(left).__name__} and {type(right).__name__}")


def _int_binary(op: str, left: int, right: int) -> float | int:
    match op:
        case "+":
            return wrap_int(left + right)
        case "-":
            return wrap_int(left - right)
        case "*":
            return wrap_int(left * right)
        case "/":
            _check_zero(right)
            # Python floors; C truncates toward zero. The difference shows up whenever the
            # operands have different signs.
            quotient = abs(left) // abs(right)
            return wrap_int(-quotient if (left < 0) != (right < 0) else quotient)
        case "%":
            _check_zero(right)
            remainder = abs(left) % abs(right)
            # The sign of a modulo result follows the dividend, not the divisor.
            return -remainder if left < 0 else remainder
        case "==":
            return int(left == right)
        case "!=":
            return int(left != right)
        case "<":
            return int(left < right)
        case "<=":
            return int(left <= right)
        case ">":
            return int(left > right)
        case ">=":
            return int(left >= right)
        case "&&":
            return int(truthy(left) and truthy(right))
        case "||":
            return int(truthy(left) or truthy(right))
    raise ValueError(f"unknown operator {op!r}")


def _float_binary(op: str, left: float, right: float) -> float | int:
    match op:
        case "+":
            return left + right
        case "-":
            return left - right
        case "*":
            return left * right
        case "/":
            _check_zero(right)
            return left / right
        # Modulo is an int-only operator in MiniC, so it is rejected here rather than
        # silently succeeding with Python's float remainder.
        case "%":
            raise ValueError("% requires int operands")
        case "==":
            return int(left == right)
        case "!=":
            return int(left != right)
        case "<":
            return int(left < right)
        case "<=":
            return int(left <= right)
        case ">":
            return int(left > right)
        case ">=":
            return int(left >= right)
        case "&&":
            return int(truthy(left) and truthy(right))
        case "||":
            return int(truthy(left) or truthy(right))
    raise ValueError(f"unknown operator {op!r}")


def arith_unary(op: str, operand: float | int) -> float | int:
    """Evaluate a unary operator.

    ``-`` negates (wrapping for ``int``, so ``-INT_MIN`` is ``INT_MIN``), ``!`` produces
    ``int`` 0 or 1, and ``+`` is the identity.
    """
    match op:
        case "+":
            return operand
        case "-":
            if isinstance(operand, float):
                return -operand
            return wrap_int(-operand)
        case "!":
            return int(not truthy(operand))
    raise ValueError(f"unknown unary operator {op!r}")


def result_type(op: str, operand_type: TypeName) -> TypeName:
    """The type :func:`arith_unary` produces for ``op`` applied to ``operand_type``."""
    return TypeName.INT if op == "!" else operand_type
