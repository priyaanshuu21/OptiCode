"""The codegen self-check: parse the generated source and prove it still does the same thing.

Everything upstream of this module works on trees, and a tree can be wrong in ways that only
show up when someone tries to compile it. Codegen is where that risk is highest, because it
is the one stage that *writes* something a user will compile next: a source-to-source
transformation that emits a file the parser rejects is worse than one that emits nothing.

So the generated text is treated as a compiler *input*, not as an output to be trusted:

1. **It has to parse.** A tree that does not round-trip through the lexer and parser is not
   source code. This alone catches brace and semicolon mistakes, which are the characteristic
   way a tree rewrite goes wrong.
2. **It has to typecheck and lower.** A rewrite that produces a tree the parser accepts but
   the semantic stage rejects is equally useless, and far more likely to be subtle.
3. **It has to behave identically.** The same interpreter oracle the optimizer uses checks
   the generated source against the original IR. This is what makes the whole stage safe to
   ship: an earlier stage may propose an unsound edit, but the edit is discarded before it
   reaches the user.

A failure at any of the three is not an error in the user's request. It means the projection
was unsound, so the generated text is thrown away and the *original* source is returned
unchanged, with the reason attached. Reporting it as a compile failure would blame the user
for our bug; returning unsound source would blame them for something worse.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..ir.lower import lower
from ..ir.tac import IRProgram
from ..optimizer.verify import VerificationReport, verify_equivalence
from ..parser.parser import parse
from ..semantic.analyzer import analyze

__all__ = [
    "SelfCheck",
    "self_check",
]


@dataclass(frozen=True, slots=True)
class SelfCheck:
    """Whether the generated source survived a full parse, check, and equivalence run."""

    #: Whether the generated text is safe to hand back as source code.
    ok: bool = False
    #: The re-lowered program, when the check got far enough to build one.
    program: IRProgram | None = None
    #: Why the check failed, in one sentence, for the diagnostic and the UI.
    reason: str = ""
    #: The interpreter oracle's verdict, when equivalence was actually attempted.
    verification: VerificationReport | None = None
    #: Diagnostics raised by re-analysing the generated source.
    diagnostics: tuple[str, ...] = ()

    def describe(self) -> str:
        return self.reason or "the generated source re-compiled and behaved identically"

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"ok": self.ok, "reason": self.reason}
        if self.verification is not None:
            payload["verification"] = self.verification.to_dict()
        if self.diagnostics:
            payload["diagnostics"] = list(self.diagnostics)
        return payload


def self_check(generated: str, original: IRProgram) -> SelfCheck:
    """Compile the generated text from scratch and check it against ``original``."""
    if not generated.strip():
        return SelfCheck(ok=False, reason="the generated source is empty")

    program, failures = _parse(generated)
    if program is None:
        return SelfCheck(ok=False, reason=failures[0], diagnostics=failures)

    lowered = lower(analyze(program))
    if not lowered.ok:
        return SelfCheck(
            ok=False,
            reason="the generated source did not typecheck",
            diagnostics=tuple(lowered.errors),
        )

    report = verify_equivalence(original, lowered.program)
    if not report.verified:
        return SelfCheck(
            ok=False,
            program=lowered.program,
            reason=_difference(report),
            verification=report,
        )
    return SelfCheck(ok=True, program=lowered.program, verification=report)


def _parse(text: str) -> tuple[Any, tuple[str, ...]]:
    """Lex and parse the generated text, returning the tree or why there is not one."""
    program, bag = parse(text)
    errors = tuple(diagnostic.message for diagnostic in bag)
    if program is None or errors:
        return None, errors or ("the generated source did not parse",)
    return program, ()


def _difference(report: VerificationReport) -> str:
    """Describe the first behavioural difference the oracle found, if it found one."""
    case = report.first_difference
    if case is None:
        return "the generated source did not behave identically on any checked entry point"
    return (
        f"the generated source differs on `{case.entry}` with input {case.stdin!r}: "
        f"{case.difference or 'the two programs did not match'}"
    )
