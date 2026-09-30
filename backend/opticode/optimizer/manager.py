"""Running the passes to a fixed point, and deciding whether to keep the result.

One application of the pass list is not enough, because the passes enable each other:
constant propagation turns a branch condition into a literal, which lets branch folding
resolve it, which makes one side unreachable, which unreachable-block elimination deletes,
which leaves assignments with no readers, which dead store elimination then removes. So the
manager repeats the list until a full round changes nothing — the standard fixed point.

Termination is the part worth being careful about. Three things guarantee it:

* **Every pass is strictly reducing.** A pass either deletes an instruction or rewrites one
  into an equivalent one, and no pass can rewrite an instruction it has already rewritten,
  because each rewrite's precondition is consumed by the rewrite. Constant folding turns
  ``t = 2 + 3`` into ``t = 5``, which is not foldable again; constant propagation substitutes
  a value into an operand, and a literal operand is skipped on the next look. So instruction
  count never rises, and a strictly reducing measure over a finite set terminates.
* **The graphs are rebuilt between rounds.** After a change the CFG and the three analyses
  are re-solved (:meth:`PassContext.refresh`), so the next pass decides on the program as it
  now is rather than on facts that predate its own edits.
* **The round cap is a backstop, not the argument.** ``max_iterations`` exists so a compiler
  bug costs one request instead of hanging the server. Reaching it is treated as a failure,
  not as a result: see :class:`OptimizationError`.

The last stage is the one that matters for trust. Whatever the passes produced goes to
:mod:`~opticode.optimizer.verify`, and a program that behaves differently is thrown away
rather than shipped. That is the difference between an optimizer that is usually right and
one that is checkable.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..ir.invariants import check_program
from ..ir.tac import IRProgram
from .context import PassContext
from .events import (
    MAX_EVENTS,
    OptimizationEvent,
    OptimizationResult,
    PassResult,
    same_program,
)
from .passes import DEFAULT_PASSES, Pass, select_passes
from .verify import VerificationReport, verify_equivalence

__all__ = [
    "DEFAULT_MAX_ITERATIONS",
    "OptimizationError",
    "PassRun",
    "optimize",
    "run_passes",
]

#: Matches ``CompileOptions.max_iterations``. Five passes over a program of any realistic
#: size converge in three or four rounds; twenty leaves room to spare without letting a
#: non-converging pass run long enough to matter.
DEFAULT_MAX_ITERATIONS = 20


class OptimizationError(Exception):
    """A pass produced a program the pipeline must not ship.

    Covers three situations, all of them compiler bugs rather than user errors: a pass
    breaking one of its own structural guarantees, the round cap being reached, and the
    interpreter oracle finding that the optimized program does something the original did
    not. Each is reported as ``E-OPT-001`` with the original program kept, so a user sees a
    working unoptimized compile instead of a failure.
    """

    def __init__(
        self, message: str, *, verification: VerificationReport | None = None
    ) -> None:
        super().__init__(message)
        self.verification = verification


@dataclass(slots=True)
class _Counter:
    """Running totals for one pass across the whole run."""

    iterations: int = 0
    applications: int = 0
    removed: int = 0


@dataclass(slots=True)
class PassRun:
    """The outcome of driving the passes to a fixed point."""

    program: IRProgram
    passes: tuple[PassResult, ...] = ()
    events: tuple[OptimizationEvent, ...] = ()
    rounds: int = 0
    converged: bool = True
    events_truncated: bool = False
    #: Set when the loop stopped because of the cap rather than because it converged.
    stopped_reason: str = ""


def run_passes(
    program: IRProgram,
    passes: tuple[Pass, ...] = DEFAULT_PASSES,
    *,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> PassRun:
    """Apply ``passes`` until a full round changes nothing.

    Raises :class:`~opticode.ir.invariants.InvariantViolation` if a rewrite produces TAC that
    does not satisfy the structural invariants, and
    :class:`~opticode.optimizer.passes.PassError` if a pass detects its own guarantee being
    broken. The caller turns both into an internal diagnostic rather than shipping the
    result.

    The structural check is re-run after *every* individual change rather than once at the
    end. That is deliberate: a pass that deletes a definition someone still reads leaves a
    program that crashes in the interpreter, and finding it at the point of the bad rewrite
    names the pass that caused it.
    """
    ctx = PassContext(program=program)
    ctx.refresh()

    counters = {item.name: _Counter() for item in passes}
    events: list[OptimizationEvent] = []
    truncated = False
    rounds = 0
    converged = False
    reason = ""

    while rounds < max_iterations:
        rounds += 1
        round_changed = False
        for item in passes:
            delta = item.run(ctx)
            counter = counters[item.name]
            counter.iterations += 1
            counter.removed += delta.removed
            if not delta.changed:
                continue
            round_changed = True
            counter.applications += 1
            for event in delta.events:
                if len(events) < MAX_EVENTS:
                    events.append(event)
                else:
                    # Keep counting removals after the detail stops being recorded, so the
                    # totals in the report still describe what actually happened.
                    truncated = True
            # The next pass must see what this one produced, so the facts are re-solved here
            # rather than once per round.
            ctx.refresh()
            check_program(ctx.program)
        if not round_changed:
            converged = True
            break

    if not converged:
        reason = (
            f"the pass pipeline was still making changes after {rounds} rounds, which "
            f"exceeds the limit of {max_iterations}"
        )

    return PassRun(
        program=ctx.program,
        passes=tuple(
            PassResult(
                name=item.name,
                description=item.description,
                iterations=counters[item.name].iterations,
                applications=counters[item.name].applications,
                removed=counters[item.name].removed,
            )
            for item in passes
        ),
        events=tuple(events),
        rounds=rounds,
        converged=converged,
        events_truncated=truncated,
        stopped_reason=reason,
    )


def optimize(
    program: IRProgram,
    *,
    passes: tuple[str, ...] | list[str] | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    verify: bool = True,
) -> OptimizationResult:
    """Optimize ``program`` and return the result, verified.

    ``passes`` names the passes to run; ``None`` means the whole default pipeline. Names come
    from :data:`~opticode.optimizer.passes.DEFAULT_PASSES` and an unknown one is a caller
    error rather than a user error, so it surfaces as :class:`~opticode.optimizer.passes.PassError`.

    Raises :class:`OptimizationError` if the passes do not converge or if verification finds a
    behavioural difference. Both leave it to the caller to keep the original program, which
    is why the original travels inside the result either way.
    """
    selected = select_passes(passes)
    run = run_passes(program, selected, max_iterations=max_iterations)

    if not run.converged:
        raise OptimizationError(run.stopped_reason)

    # Nothing changed, so there is nothing to prove: running the oracle would compare a
    # program with itself and report a match that says nothing.
    report = verify_equivalence(program, run.program) if (verify and _changed(program, run)) else None

    if report is not None and not report.verified and not report.skipped:
        raise OptimizationError(report.summary(), verification=report)

    return OptimizationResult(
        original=program,
        optimized=run.program,
        passes=run.passes,
        events=run.events,
        verification=report,
        rounds=run.rounds,
        events_truncated=run.events_truncated,
    )


def _changed(original: IRProgram, run: PassRun) -> bool:
    """Whether the run produced a different program at all."""
    return not same_program(original, run.program)
