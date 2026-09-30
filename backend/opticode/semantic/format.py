"""``printf`` and ``scanf`` format-string checking.

MiniC supports exactly ``%d``, ``%f``, and ``%%``. Each ``%d``/``%f`` consumes one
argument; ``%%`` is a literal percent sign and consumes nothing. Anything else is an
error, so a pasted ``%s`` or ``%n`` is caught at compile time rather than at run time.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .types import TypeName

__all__ = ["FormatKind", "FormatSpec", "parse_format", "check_printf", "check_scanf"]


class FormatKind(StrEnum):
    INT = "int"
    FLOAT = "float"
    PERCENT = "percent"
    UNSUPPORTED = "unsupported"


#: The one-letter specifier and the type it requires.
_SPECS: dict[str, FormatKind] = {"d": FormatKind.INT, "f": FormatKind.FLOAT}


@dataclass(frozen=True, slots=True)
class FormatSpec:
    """One ``%`` directive found in a format string."""

    kind: FormatKind
    letter: str
    offset: int
    #: Zero-based index into the argument list for ``d``/``f`` specs, else ``None``.
    argument_index: int | None


@dataclass(frozen=True, slots=True)
class FormatProblem:
    """A format-string problem, described independently of the diagnostic."""

    message: str
    code: str
    #: Zero-based index of the offending argument, when the problem is about one.
    argument_index: int | None = None
    #: Offset of the specifier inside the format string, for the span.
    offset: int | None = None


def parse_format(format_string: str) -> list[FormatSpec]:
    """Split a format string into its directives.

    A ``%`` that is not followed by a recognised letter is reported as ``UNSUPPORTED``
    rather than silently consuming an argument, so the checker can explain it.
    """
    specs: list[FormatSpec] = []
    next_argument = 0
    index = 0
    length = len(format_string)
    while index < length:
        char = format_string[index]
        if char != "%":
            index += 1
            continue
        if index + 1 >= length:
            specs.append(FormatSpec(FormatKind.UNSUPPORTED, "", index, None))
            break
        letter = format_string[index + 1]
        if letter == "%":
            specs.append(FormatSpec(FormatKind.PERCENT, "%", index, None))
            index += 2
            continue
        kind = _SPECS.get(letter, FormatKind.UNSUPPORTED)
        if kind is FormatKind.UNSUPPORTED:
            specs.append(FormatSpec(kind, letter, index, None))
            index += 2
            continue
        specs.append(FormatSpec(kind, letter, index, next_argument))
        next_argument += 1
        index += 2
    return specs


def _expected_problems(
    specs: list[FormatSpec], argument_types: list[TypeName | None], verb: str
) -> list[FormatProblem]:
    """Problems shared by printf and scanf: bad specifiers, count, and types."""
    problems: list[FormatProblem] = []
    consuming = [spec for spec in specs if spec.kind in (FormatKind.INT, FormatKind.FLOAT)]
    unsupported = [spec for spec in specs if spec.kind is FormatKind.UNSUPPORTED]

    for spec in unsupported:
        shown = f"%{spec.letter}" if spec.letter else "%"
        detail = f"'{shown}' at the end of the string" if not spec.letter else f"'{shown}'"
        problems.append(
            FormatProblem(
                message=(
                    f"unsupported format specifier {detail}; MiniC supports "
                    "%d, %f, and %%"
                ),
                code="E-SEM-009",
                offset=spec.offset,
            )
        )

    if len(argument_types) != len(consuming):
        problems.append(
            FormatProblem(
                message=(
                    f"the format string expects {len(consuming)} "
                    f"{'argument' if len(consuming) == 1 else 'arguments'} but "
                    f"{len(argument_types)} "
                    f"{'was' if len(argument_types) == 1 else 'were'} given to {verb}"
                ),
                code="E-SEM-010",
            )
        )

    for spec, actual in zip(consuming, argument_types, strict=False):
        if actual is None:
            continue
        required = TypeName.INT if spec.kind is FormatKind.INT else TypeName.FLOAT
        if actual is not required:
            noun = "value" if verb == "printf" else "variable"
            article = "an" if required is TypeName.INT else "a"
            problems.append(
                FormatProblem(
                    message=(
                        f"format specifier '%{spec.letter}' needs {article} "
                        f"{required.value} {noun} but got {actual.value}"
                    ),
                    code="E-SEM-009",
                    argument_index=spec.argument_index,
                    offset=spec.offset,
                )
            )
    return problems


def check_printf(format_string: str, argument_types: list[TypeName | None]) -> list[FormatProblem]:
    """Check a ``printf`` call's format string against its argument types."""
    return _expected_problems(parse_format(format_string), argument_types, "printf")


def check_scanf(format_string: str, argument_types: list[TypeName | None]) -> list[FormatProblem]:
    """Check a ``scanf`` call's format string against its target variable types."""
    return _expected_problems(parse_format(format_string), argument_types, "scanf")
