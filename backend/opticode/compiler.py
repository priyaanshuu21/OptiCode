"""The compile facade: builds and runs the stage pipeline.

Later tasks register additional stages here (parser, semantic, ir, cfg, analysis,
optimizer, codegen). Each stage is pure, returns ``(result, diagnostics)``, and is
skipped once an earlier stage has produced an error — but earlier results are kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cfg.graph import CFG, CFGError, build_program_cfgs
from .core.diagnostics import Diagnostic, DiagnosticBag, DiagnosticCode, Severity, Stage
from .core.pipeline import MAX_SOURCE_SIZE, CompileMode, Pipeline, PipelineState, StageOutcome
from .core.spans import SourceSpan
from .ir.invariants import check_program
from .ir.lower import lower
from .ir.serialize import serialize_ir
from .lexer.lexer import Lexer
from .lexer.tokens import Token
from .parser.parser import Parser
from .parser.serialize import serialize_program
from .semantic.analyzer import analyze
from .semantic.serialize import serialize_semantic

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


def _stage_parse(state: PipelineState) -> StageOutcome:
    tokens = state.get("tokens") or []
    program, diagnostics = Parser(tokens).parse()
    state.diagnostics.extend(diagnostics)
    return StageOutcome(name="ast", value=program)


def _stage_semantic(state: PipelineState) -> StageOutcome:
    program = state.get("ast")
    if program is None:
        return StageOutcome(name="semantic", ran=False)
    result = analyze(program)
    state.diagnostics.extend(result.diagnostics)
    return StageOutcome(name="semantic", value=result)


def _stage_ir(state: PipelineState) -> StageOutcome:
    semantic = state.get("semantic")
    if semantic is None:
        return StageOutcome(name="ir", ran=False)
    lowered = lower(semantic)
    if not lowered.ok:
        # Lowering only fails when the semantic stage left the tree unusable, so report
        # it as an internal error rather than blaming the user's program.
        for message in lowered.errors:
            state.diagnostics.error(
                DiagnosticCode.E001_INTERNAL_UNEXPECTED,
                f"the TAC stage could not lower {message}",
                stage=Stage.IR,
            )
        return StageOutcome(name="ir", ran=False, errored=True)
    check_program(lowered.program)
    return StageOutcome(name="ir", value=lowered)


def _stage_cfg(state: PipelineState) -> StageOutcome:
    """Build a control-flow graph per function.

    The lowering already guarantees every jump resolves, so a :class:`CFGError` here would
    be a compiler bug rather than a user error — it is reported as an internal error and
    the stage stops the pipeline.
    """
    lowered = state.get("ir")
    if lowered is None:
        return StageOutcome(name="cfg", ran=False)
    try:
        cfgs = build_program_cfgs(lowered.program)
    except CFGError as error:
        state.diagnostics.error(
            DiagnosticCode.E001_INTERNAL_UNEXPECTED,
            f"the CFG stage could not build a graph: {error}",
            stage=Stage.IR,
        )
        return StageOutcome(name="cfg", ran=False, errored=True)
    return StageOutcome(name="cfg", value=cfgs)


# ------------------------------------------------------------------- pipeline


def build_pipeline() -> Pipeline:
    pipeline = Pipeline()
    pipeline.register("tokens", Stage.LEXER, _stage_lex)
    pipeline.register("ast", Stage.PARSER, _stage_parse)
    pipeline.register("semantic", Stage.SEMANTIC, _stage_semantic)
    pipeline.register("ir", Stage.IR, _stage_ir)
    pipeline.register("cfg", Stage.IR, _stage_cfg)
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
    program = state.get("ast")
    semantic = state.get("semantic")
    lowered = state.get("ir")
    cfgs = state.get("cfg")
    return CompileResult(
        success=success,
        mode=state.mode,
        diagnostics=bag.sorted(),
        tokens=state.get("tokens"),
        ast=serialize_program(program) if program is not None else None,
        semantic=serialize_semantic(semantic) if semantic is not None else None,
        ir=serialize_ir(lowered.program) if lowered is not None else None,
        cfg=_serialize_cfgs(cfgs),
        dataflow=state.get("dataflow"),
        analysis=state.get("analysis"),
        optimization=state.get("optimization"),
        codegen=state.get("codegen"),
        timings=state.timings,
        stages_run=state.stages_run,
        stages_skipped=state.stages_skipped,
    )


def _serialize_cfgs(cfgs: dict[str, CFG] | None) -> dict[str, Any] | None:
    """Serialize per-function graphs as a name → graph mapping."""
    if cfgs is None:
        return None
    return {name: cfg.to_dict() for name, cfg in cfgs.items()}


def severity_of(diagnostic: Diagnostic) -> str:
    return diagnostic.severity.value if diagnostic.severity is not Severity.ERROR else "error"
