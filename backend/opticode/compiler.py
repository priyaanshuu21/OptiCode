"""The compile facade: builds and runs the stage pipeline.

Later tasks register additional stages here (parser, semantic, ir, cfg, analysis,
optimizer, codegen). Each stage is pure, returns ``(result, diagnostics)``, and is
skipped once an earlier stage has produced an error — but earlier results are kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .core.diagnostics import Diagnostic, DiagnosticBag, DiagnosticCode, Severity, Stage
from .core.pipeline import MAX_SOURCE_SIZE, CompileMode, Pipeline, PipelineState, StageOutcome
from .core.spans import SourceSpan
from .lexer.lexer import Lexer
from .lexer.tokens import Token

__all__ = ["CompileResult", "build_pipeline", "compile_source", "MAX_SOURCE_SIZE"]


@dataclass(slots=True)
class CompileResult:
    """Everything the API needs for one compile request."""

    success: bool = False
    mode: str = CompileMode.ANALYZE
    diagnostics: list[Diagnostic] = field(default_factory=list)
    tokens: list[Token] | None = None
    ast: Any = None
    semantic: Any = None
    ir: Any = None
    cfg: Any = None
    dataflow: Any = None
    analysis: Any = None
    optimization: Any = None
    codegen: Any = None
    timings: dict[str, float] = field(default_factory=dict)
    stages_run: list[str] = field(default_factory=list)
    stages_skipped: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize the result. Absent stages are simply omitted."""
        payload: dict[str, Any] = {
            "success": self.success,
            "mode": self.mode,
            "diagnostics": [d.to_dict() for d in self.diagnostics],
        }
        for name in ("tokens", "ast", "semantic", "ir", "cfg", "dataflow", "analysis", "optimization"):
            value = getattr(self, name)
            if value is not None:
                payload[name] = value
        if self.codegen is not None:
            payload["codegen"] = self.codegen
        payload["timings"] = {k: round(v * 1000.0, 3) for k, v in self.timings.items()}
        payload["stages_run"] = self.stages_run
        payload["stages_skipped"] = self.stages_skipped
        return payload


# --------------------------------------------------------------------- stages


def _stage_lex(state: PipelineState) -> StageOutcome:
    tokens, diagnostics = Lexer(state.source).scan()
    state.diagnostics.extend(diagnostics)
    return StageOutcome(name="tokens", value=tokens)


# ------------------------------------------------------------------- pipeline


def build_pipeline() -> Pipeline:
    pipeline = Pipeline()
    pipeline.register("tokens", Stage.LEXER, _stage_lex)
    return pipeline


# ------------------------------------------------------------------ entrypoint


def compile_source(
    source: str,
    mode: str = CompileMode.ANALYZE,
    options: dict[str, Any] | None = None,
    stdin: str = "",
) -> CompileResult:
    """Run the pipeline over ``source``. Never raises for user input."""
    if mode not in CompileMode.ALL:
        mode = CompileMode.ANALYZE
    state = PipelineState(
        source=source,
        mode=mode,
        stdin=stdin,
        options=options or {},
    )

    if len(source) > MAX_SOURCE_SIZE:
        state.diagnostics.error(
            DiagnosticCode.E010_SOURCE_TOO_LARGE,
            f"source is {len(source)} bytes; the limit is {MAX_SOURCE_SIZE} bytes (100 KB)",
            stage=Stage.INPUT,
        )
        return _to_result(state)
    if not source.strip():
        state.diagnostics.info(
            DiagnosticCode.E011_SOURCE_EMPTY,
            "the program is empty",
            span=SourceSpan.at(1, 1),
            stage=Stage.INPUT,
        )

    build_pipeline().run(state)
    return _to_result(state)


def _to_result(state: PipelineState) -> CompileResult:
    bag = DiagnosticBag(state.diagnostics.sorted())
    success = not bag.has_errors()
    return CompileResult(
        success=success,
        mode=state.mode,
        diagnostics=bag.sorted(),
        tokens=state.get("tokens"),
        ast=state.get("ast"),
        semantic=state.get("semantic"),
        ir=state.get("ir"),
        cfg=state.get("cfg"),
        dataflow=state.get("dataflow"),
        analysis=state.get("analysis"),
        optimization=state.get("optimization"),
        codegen=state.get("codegen"),
        timings=state.timings,
        stages_run=state.stages_run,
        stages_skipped=state.stages_skipped,
    )


def severity_of(diagnostic: Diagnostic) -> str:
    return diagnostic.severity.value if diagnostic.severity is not Severity.ERROR else "error"
