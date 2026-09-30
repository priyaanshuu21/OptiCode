"""Pipeline orchestration.

A pipeline is an ordered list of named stages. Each stage receives the accumulated
state and returns a :class:`StageOutcome`. The orchestrator:

* runs stages in order,
* stops after a stage that produced errors (later stages are skipped, **earlier
  results are still returned** so the UI keeps partial results),
* converts any unexpected exception into a structured internal-error diagnostic
  without leaking a stack trace,
* records per-stage timings.
"""

from __future__ import annotations

import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .diagnostics import Diagnostic, DiagnosticBag, DiagnosticCode, Severity, Stage

__all__ = [
    "StageOutcome",
    "StageRunner",
    "Pipeline",
    "PipelineState",
    "MAX_SOURCE_SIZE",
    "MIN_SOURCE_SIZE",
    "CompileMode",
]

MAX_SOURCE_SIZE = 100 * 1024  # 100 KB
MIN_SOURCE_SIZE = 1


class CompileMode(str):
    ANALYZE = "analyze"
    OPTIMIZE = "optimize"

    ALL = (ANALYZE, OPTIMIZE)


@dataclass(slots=True)
class StageOutcome:
    """The result of one stage: a named payload plus its diagnostics."""

    name: str
    value: Any = None
    ran: bool = True
    errored: bool = False

    def __bool__(self) -> bool:
        return self.ran and not self.errored


StageRunner = Callable[["PipelineState"], "StageOutcome"]


@dataclass(slots=True)
class PipelineState:
    """Everything a stage may need, and everything a stage has produced so far."""

    source: str
    mode: str = CompileMode.ANALYZE
    stdin: str = ""
    options: dict[str, Any] = field(default_factory=dict)
    diagnostics: DiagnosticBag = field(default_factory=DiagnosticBag)
    results: dict[str, Any] = field(default_factory=dict)
    timings: dict[str, float] = field(default_factory=dict)
    stages_run: list[str] = field(default_factory=list)
    stages_skipped: list[str] = field(default_factory=list)

    def get(self, name: str) -> Any:
        return self.results.get(name)

    def put(self, name: str, value: Any) -> None:
        self.results[name] = value

    def has_errors(self) -> bool:
        return self.diagnostics.has_errors()

    def skipped_reason(self, stage: Stage) -> str | None:
        """A human readable explanation for why a stage did not run."""
        for name in self.stages_skipped:
            if name.startswith(stage.value):
                return "skipped because an earlier stage reported errors"
        return None


class Pipeline:
    """An ordered, fault-tolerant sequence of compiler stages."""

    def __init__(self) -> None:
        self._stages: list[tuple[str, Stage, StageRunner]] = []

    def register(self, name: str, stage: Stage, runner: StageRunner) -> None:
        self._stages.append((name, stage, runner))

    @property
    def stage_names(self) -> list[str]:
        return [name for name, _, _ in self._stages]

    def run(self, state: PipelineState) -> PipelineState:
        halted = False
        for index, (name, stage, runner) in enumerate(self._stages):
            if halted:
                state.stages_skipped.extend(n for n, _, _ in self._stages[index:])
                break
            started = time.perf_counter()
            try:
                outcome = runner(state)
            except RecursionError:
                elapsed = time.perf_counter() - started
                state.timings[name] = elapsed
                state.diagnostics.error(
                    DiagnosticCode.E001_INTERNAL_UNEXPECTED,
                    f"The {stage.value} stage exceeded the maximum nesting depth.",
                    stage=stage,
                )
                state.stages_run.append(name)
                halted = True
                continue
            except Exception as exc:  # noqa: BLE001 - internal bug containment
                elapsed = time.perf_counter() - started
                state.timings[name] = elapsed
                state.diagnostics.add(
                    _internal_diagnostic(stage, exc, traceback.format_exc()),
                )
                state.stages_run.append(name)
                halted = True
                continue

            elapsed = time.perf_counter() - started
            state.timings[name] = elapsed
            state.stages_run.append(name)

            if outcome is None:
                outcome = StageOutcome(name=name)
            if outcome.errored:
                halted = True
                continue
            if outcome.value is not None:
                state.put(name, outcome.value)
            if state.has_errors():
                halted = True
        return state


def _internal_diagnostic(stage: Stage, exc: Exception, tb: str) -> Diagnostic:
    """Turn an unexpected exception into a diagnostic with no stack trace attached."""
    detail = f"{type(exc).__name__}: {exc}"
    diagnostic = Diagnostic(
        message=(
            f"Internal compiler error in the {stage.value} stage ({detail}). "
            "The compiler could not continue; please report this program."
        ),
        code=DiagnosticCode.E001_INTERNAL_UNEXPECTED,
        severity=Severity.ERROR,
        span=None,
        stage=Stage.INTERNAL,
    )
    # The traceback is only surfaced when explicitly enabled for local debugging;
    # it is never serialized to the client.
    if _DEBUG:
        diagnostic = Diagnostic(
            message=diagnostic.message + "\n" + tb,
            code=diagnostic.code,
            severity=diagnostic.severity,
            span=None,
            stage=Stage.INTERNAL,
        )
    return diagnostic


_DEBUG = False


def set_debug(enabled: bool) -> None:
    """Include the internal traceback in internal-error diagnostics (local only)."""
    global _DEBUG
    _DEBUG = enabled
