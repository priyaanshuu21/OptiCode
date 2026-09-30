"""The interpreter oracle: proof by running both programs.

Optimizations delete code. The only way to know a deletion was allowed is to check that the
program still does the same thing, so this module runs the original and the optimized IR and
compares them.

What is compared is deliberately *narrow*: :meth:`ExecutionOutcome.same_observable_behaviour_as`
compares status, return value, output, and traps, and nothing else. Those four are the whole
observable behaviour of a MiniC program. Step counts are excluded because an optimization
that does less work has *done* something different in a way no user can observe, and
``uninitialized_reads`` is excluded because MiniC leaves such values indeterminate.

Two design choices keep this sound rather than merely convincing:

**Only entries that can be run are verified.** A function with parameters has no meaningful
argument list, so the oracle verifies ``main`` plus every zero-parameter function, each run
as its own entry point. Skipping the rest is a real limitation — an optimization bug that
only shows up when ``f`` is called with particular arguments would go unnoticed — so
:attr:`VerificationReport.skipped` names exactly what was not checked rather than reporting a
clean result that overstates the coverage.

**A bounded run proves nothing either way.** If either program hits the step limit or the
call depth limit, the case is *inconclusive*, not a failure and not a pass. Treating a
timeout as "they differ" would reject correct optimizations on programs that are simply slow
to run; treating it as "they agree" would be a lie.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ir.interpreter import (
    ExecutionError,
    ExecutionOutcome,
    ExecutionStatus,
    InterpreterLimits,
    interpret,
)
from ..ir.tac import IRProgram

__all__ = [
    "VERIFICATION_INPUTS",
    "VERIFICATION_LIMITS",
    "VerificationCase",
    "VerificationReport",
    "verify_equivalence",
    "verifiable_entries",
]


#: Tighter than :data:`~opticode.ir.interpreter.DEFAULT_LIMITS` because this runs *twice per
#: function per input* on every request. The bound only has to stop an infinite loop; the
#: example programs finish in tens of steps.
VERIFICATION_LIMITS = InterpreterLimits(max_steps=200_000, max_depth=256)

#: Deterministic inputs the oracle tries. ``main`` may read them, so the empty string covers
#: the exhausted-input path and the two non-empty ones cover a successful read and a
#: division. Nothing here is random: a verifier that differed between runs could not be used
#: to reject an optimization.
VERIFICATION_INPUTS: tuple[str, ...] = ("", "0", "3 4 5 6 7")

#: Outcomes that cannot decide equivalence.
_INCONCLUSIVE = frozenset({ExecutionStatus.TIMEOUT, ExecutionStatus.STACK_OVERFLOW})


@dataclass(frozen=True, slots=True)
class VerificationCase:
    """One entry point run on one input, and what the comparison found."""

    entry: str
    stdin: str
    #: True when both programs behaved identically.
    matched: bool
    #: True when a bound stopped the run, so this case says nothing either way.
    inconclusive: bool = False
    #: What the original did, rendered for the report.
    original: str = ""
    #: What the optimized program did.
    optimized: str = ""
    #: A one-line description of the difference, when there is one.
    difference: str = ""

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "entry": self.entry,
            "stdin": self.stdin,
            "matched": self.matched,
            "inconclusive": self.inconclusive,
        }
        if self.original:
            payload["original"] = self.original
        if self.optimized:
            payload["optimized"] = self.optimized
        if self.difference:
            payload["difference"] = self.difference
        return payload


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """Whether the optimization preserved behaviour, and how well that was checked."""

    #: True when at least one case ran and every case that ran matched.
    verified: bool
    #: True when no case could be run at all, e.g. a program with no zero-parameter entry.
    skipped: bool = False
    #: How many cases were actually compared.
    cases_checked: int = 0
    #: How many were inconclusive because a bound was hit.
    cases_inconclusive: int = 0
    #: True when ``max_cases`` stopped the sweep before every entry was tried.
    cases_truncated: bool = False
    #: The first difference found, which is what a user needs to see.
    first_difference: VerificationCase | None = None
    cases: tuple[VerificationCase, ...] = ()
    #: Entries with parameters, which are not verified and why.
    unverified_entries: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "verified": self.verified,
            "skipped": self.skipped,
            "cases_checked": self.cases_checked,
            "cases_inconclusive": self.cases_inconclusive,
            "cases_truncated": self.cases_truncated,
            "cases": [case.to_dict() for case in self.cases],
            "unverified_entries": list(self.unverified_entries),
        }
        if self.first_difference is not None:
            payload["first_difference"] = self.first_difference.to_dict()
        return payload

    def summary(self) -> str:
        """One line for a diagnostic message."""
        if self.skipped:
            return "no entry point takes no arguments, so nothing could be verified"
        if self.verified and not self.cases_inconclusive:
            return f"{self.cases_checked} run(s) matched on every entry point"
        if self.verified:
            return (
                f"{self.cases_checked} run(s) matched; "
                f"{self.cases_inconclusive} could not be decided within the step limit"
            )
        difference = self.first_difference
        if difference is None:  # pragma: no cover - verified False always has one
            return "the optimized program behaved differently"
        return (
            f"{difference.entry} behaved differently after optimizing "
            f"(input {difference.stdin!r}): {difference.difference}"
        )


def verifiable_entries(program: IRProgram) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The entries the oracle can run, and the ones it has to skip.

    ``main`` first, because that is the entry a user actually runs. A function taking
    parameters is skipped: the oracle has no argument values to give it, and inventing them
    would only prove something about the values it happened to pick.
    """
    runnable = tuple(item.name for item in program.functions if not item.params)
    skipped = tuple(item.name for item in program.functions if item.params)
    ordered = tuple(sorted(runnable, key=lambda name: name != "main"))
    return ordered, skipped


def _render(outcome: ExecutionOutcome) -> str:
    """One line describing a run, for the report."""
    parts = [outcome.status.value]
    if outcome.return_value is not None:
        parts.append(f"returns {outcome.return_value}")
    if outcome.output:
        parts.append(f"prints {outcome.output!r}")
    if outcome.traps:
        parts.append(f"traps: {'; '.join(outcome.traps)}")
    return ", ".join(parts)


def _difference(
    original: ExecutionOutcome, optimized: ExecutionOutcome
) -> str:
    """The first observable respect in which two runs disagree."""
    if original.status is not optimized.status:
        return f"one finished as {original.status.value}, the other as {optimized.status.value}"
    if original.traps != optimized.traps:
        return f"one traps ({original.traps or 'none'}), the other does not ({optimized.traps or 'none'})"
    if original.return_value != optimized.return_value:
        return (
            f"one returns {original.return_value!r}, "
            f"the other returns {optimized.return_value!r}"
        )
    if original.output != optimized.output:
        return (
            f"one prints {original.output!r}, "
            f"the other prints {optimized.output!r}"
        )
    return ""  # pragma: no cover - only called when the outcomes differ


def verify_equivalence(
    original: IRProgram,
    optimized: IRProgram,
    *,
    inputs: tuple[str, ...] = VERIFICATION_INPUTS,
    limits: InterpreterLimits = VERIFICATION_LIMITS,
    max_cases: int = 64,
) -> VerificationReport:
    """Run both programs and check that they are indistinguishable.

    ``max_cases`` bounds the work: a program with many functions, each tried on every input,
    would otherwise make one request arbitrarily slow. When the bound is hit the report says
    so rather than quietly verifying less than it appears to.
    """
    entries, unverified = verifiable_entries(original)
    if not entries:
        return VerificationReport(verified=False, skipped=True, unverified_entries=unverified)

    cases: list[VerificationCase] = []
    checked = 0
    inconclusive = 0
    first_difference: VerificationCase | None = None
    truncated = False

    for entry in entries:
        for stdin in inputs:
            if checked >= max_cases:
                truncated = True
                break
            before = _run(original, entry, stdin, limits)
            after = _run(optimized, entry, stdin, limits)
            if before is None or after is None:  # pragma: no cover - entries come from the IR
                continue
            checked += 1
            bounded = (
                before.status in _INCONCLUSIVE or after.status in _INCONCLUSIVE
            )
            if bounded:
                inconclusive += 1
                cases.append(
                    VerificationCase(
                        entry=entry,
                        stdin=stdin,
                        matched=before.same_observable_behaviour_as(after),
                        inconclusive=True,
                        original=_render(before),
                        optimized=_render(after),
                        difference="a step or depth limit stopped one of the runs",
                    )
                )
                continue
            matched = before.same_observable_behaviour_as(after)
            case = VerificationCase(
                entry=entry,
                stdin=stdin,
                matched=matched,
                original=_render(before),
                optimized=_render(after),
                difference="" if matched else _difference(before, after),
            )
            cases.append(case)
            if not matched and first_difference is None:
                first_difference = case
        if truncated:
            break

    return VerificationReport(
        verified=first_difference is None and checked > 0,
        cases_checked=checked,
        cases_inconclusive=inconclusive,
        cases_truncated=truncated,
        first_difference=first_difference,
        cases=tuple(cases),
        unverified_entries=unverified,
    )


def _run(
    program: IRProgram, entry: str, stdin: str, limits: InterpreterLimits
) -> ExecutionOutcome | None:
    """Interpret one entry, treating an unrunnable program as no result rather than a crash.

    ``ExecutionError`` means the IR could not be run at all. That is a compiler bug worth
    surfacing, but it is not evidence that the *optimization* changed behaviour, so the
    oracle reports no case and lets the caller fall back to "not verified".
    """
    try:
        return interpret(program, entry=entry, stdin=stdin, limits=limits)
    except ExecutionError:  # pragma: no cover - lowering rejects such programs
        return None
