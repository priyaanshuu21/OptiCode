"""Runtime semantics that are not arithmetic.

Kept beside :mod:`opticode.semantics.arith` so "what does MiniC mean" lives in one
package rather than being split between the interpreter and the optimizer.
"""

from __future__ import annotations

from ..semantic.types import TypeName

__all__ = ["render_format", "format_float", "escape_format"]


def format_float(value: float) -> str:
    """Print a float the way C's ``printf("%f")`` does: six digits after the point.

    Python's ``str`` would print ``2.0`` and ``0.1`` very differently, so using it would
    make interpreted output disagree with real C output that users compare against.
    """
    if value != value:  # NaN
        return "nan"
    if value == float("inf"):
        return "inf"
    if value == float("-inf"):
        return "-inf"
    return f"{value:.6f}"


def _specifier_type(op: str) -> TypeName:
    return TypeName.INT if op == "d" else TypeName.FLOAT


def escape_format(text: str) -> str:
    """Expand the ``\\n`` and ``\\t`` escapes a MiniC string literal may contain."""
    return text.replace("\\n", "\n").replace("\\t", "\t").replace("\\\\", "\\")


def render_format(fmt: str, values: list[object]) -> str:
    """Render ``fmt`` with ``values``, which must already be in order.

    Only ``%d``, ``%f`` and ``%%`` exist, so the walk is a straight scan rather than a
    general format parser. ``%d`` on a ``float`` truncates and ``%f`` on an ``int``
    widens, matching C's default argument promotions.
    """
    out: list[str] = []
    index = 0
    position = 0
    while position < len(fmt):
        char = fmt[position]
        if char != "%":
            out.append(char)
            position += 1
            continue
        if position + 1 >= len(fmt):  # pragma: no cover - rejected by the analyzer
            raise ValueError("format string ends with a lone %")
        op = fmt[position + 1]
        position += 2
        if op == "%":
            out.append("%")
            continue
        if op not in ("d", "f"):  # pragma: no cover - rejected by the analyzer
            raise ValueError(f"unsupported specifier %{op}")
        if index >= len(values):
            raise ValueError("not enough values for the format string")
        value = values[index]
        index += 1
        if _specifier_type(op) is TypeName.INT:
            if isinstance(value, float):
                from .arith import truncate_to_int

                value = truncate_to_int(value)
            out.append(str(int(value)))
        else:
            out.append(format_float(float(value)))  # type: ignore[arg-type]
    if index != len(values):
        raise ValueError("too many values for the format string")
    return "".join(out)
