"""The compile facade: builds and runs the stage pipeline.

Later tasks register additional stages here (parser, semantic, ir, cfg, analysis,
optimizer, codegen). Each stage is pure, returns ``(result, diagnostics)``, and is
skipped once an earlier stage has produced an error — but earlier results are kept.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .analysis import analyze_findings as analyze_dead_code
from .analysis import analyze_functions as analyze_program_functions
from .analysis import diagnostics_for
from .analysis.results import FunctionAnalysis
from .analysis.solver import SolverError
from .cfg.graph import CFG, CFGError, build_program_cfgs
from .core.diagnostics import Diagnostic, DiagnosticBag, DiagnosticCode, Severity, Stage
from .core.pipeline import MAX_SOURCE_SIZE, CompileMode, Pipeline, PipelineState, StageOutcome
from .core.spans import SourceSpan
from .ir.invariants import InvariantViolation, check_program
from .ir.lower import lower
from .ir.serialize import serialize_ir
from .lexer.lexer import Lexer
from .lexer.tokens import Token
from .optimizer.manager import DEFAULT_MAX_ITERATIONS, OptimizationError
from .optimizer.manager import optimize as optimize_program
from .optimizer.passes import PassError
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


def _stage_dataflow(state: PipelineState) -> StageOutcome:
    """Run liveness, reaching definitions, and constant propagation over every function.

    This stage only reads the graphs the CFG stage already built — it passes them in rather
    than rebuilding, so three analyses cost one graph. It is registered under ``Stage.IR``
    because dataflow runs on TAC and reports compiler-level facts, not user-level errors.

    The solved :class:`FunctionAnalysis` objects are kept rather than serialized, so the
    analysis stage below reads them directly instead of paying for three more solver runs.
    Serialization happens once, in :func:`_to_result`.

    The analyses are total over well-formed TAC: a transfer function that cannot converge is
    a compiler bug, so :class:`SolverError` becomes an internal error and stops the pipeline
    rather than hanging the request.
    """
    lowered = state.get("ir")
    cfgs = state.get("cfg")
    if lowered is None or cfgs is None:
        return StageOutcome(name="dataflow", ran=False)
    try:
        results = analyze_program_functions(lowered.program, cfgs=cfgs)
    except SolverError as error:
        state.diagnostics.error(
            DiagnosticCode.E001_INTERNAL_UNEXPECTED,
            f"a data-flow analysis did not converge: {error}",
            stage=Stage.IR,
        )
        return StageOutcome(name="dataflow", ran=False, errored=True)
    return StageOutcome(name="dataflow", value=results)


def _stage_analysis(state: PipelineState) -> StageOutcome:
    """Turn the solved data-flow results into dead-code and unreachable-code findings.

    Every input is already in the pipeline state: the graphs, the solved analyses, the AST
    for operand spans, and the source text so a message can quote the statement instead of
    naming a temporary. Nothing here solves anything new, so the stage is pure
    interpretation of the results above it.

    A failure would be a compiler bug rather than a user error — the findings walk TAC the
    lowering already proved well-formed — so anything unexpected becomes an internal error
    that stops the pipeline instead of a misleading finding.
    """
    lowered = state.get("ir")
    cfgs = state.get("cfg")
    analyses = state.get("dataflow")
    if lowered is None or cfgs is None or analyses is None:
        return StageOutcome(name="analysis", ran=False)
    report = analyze_dead_code(
        lowered.program,
        cfgs=cfgs,
        analyses=analyses,
        ast=state.get("ast"),
        source=state.source,
    )
    state.diagnostics.extend(diagnostics_for(report))
    return StageOutcome(name="analysis", value=report.to_dict())


def _stage_optimizer(state: PipelineState) -> StageOutcome:
    """Run the optimization passes and verify the result by execution.

    Two kinds of failure are handled, and both are compiler bugs rather than anything the
    user wrote:

    * A pass breaking one of its own structural guarantees, or the round cap being reached.
      The optimized program is discarded and ``E-OPT-001`` is reported.
    * The interpreter oracle finding that the optimized program prints something else,
      returns something else, or traps differently. Same outcome, and this is the case the
      oracle exists for: a wrong optimization must never reach the user.

    In both cases ``ir`` keeps holding the *original* program, so the response still has a
    correct TAC to show and codegen still has valid input. Only ``optimization`` goes missing.
    """
    lowered = state.get("ir")
    if lowered is None:
        return StageOutcome(name="optimization", ran=False)
    if state.mode != CompileMode.OPTIMIZE:
        # `analyze` is the default mode and its job is to explain the program as written.
        # Running the optimizer there would rewrite the very TAC the analysis findings are
        # about, and would attach an optimization summary to a request that never asked for
        # one. The stage reports itself as skipped so the client can tell "not requested"
        # apart from "ran and found nothing to do".
        return StageOutcome(name="optimization", ran=False)
    options = state.options or {}
    try:
        result = optimize_program(
            lowered.program,
            passes=options.get("passes"),
            max_iterations=int(options.get("max_iterations") or DEFAULT_MAX_ITERATIONS),
        )
    except OptimizationError as error:
        state.diagnostics.error(
            DiagnosticCode.E500_OPTIMIZER_INTERNAL,
            f"the optimizer produced a program that is not equivalent to the original: "
            f"{error}",
            stage=Stage.OPTIMIZER,
        )
        return StageOutcome(name="optimization", ran=False, errored=True)
    except (PassError, InvariantViolation) as error:
        state.diagnostics.error(
            DiagnosticCode.E500_OPTIMIZER_INTERNAL,
            f"an optimization pass broke a structural guarantee: {error}",
            stage=Stage.OPTIMIZER,
        )
        return StageOutcome(name="optimization", ran=False, errored=True)

    # The oracle could not run anything — a program whose every entry point takes arguments
    # has no callable entry. That is a coverage limitation, not a failure, so it is reported
    # as information and the optimized program is kept.
    report = result.verification
    if result.changed and report is not None and report.skipped:
        state.diagnostics.info(
            DiagnosticCode.E501_OPTIMIZED,
            f"the optimizer {result.summary()}, but no entry point takes no arguments, "
            "so the result could not be verified",
            stage=Stage.OPTIMIZER,
        )
    elif result.changed:
        state.diagnostics.info(
            DiagnosticCode.E501_OPTIMIZED,
            f"the optimizer {result.summary()}",
            stage=Stage.OPTIMIZER,
        )
    return StageOutcome(name="optimization", value=result.to_dict())


# ------------------------------------------------------------------- pipeline


def build_pipeline() -> Pipeline:
    pipeline = Pipeline()
    pipeline.register("tokens", Stage.LEXER, _stage_lex)
    pipeline.register("ast", Stage.PARSER, _stage_parse)
    pipeline.register("semantic", Stage.SEMANTIC, _stage_semantic)
    pipeline.register("ir", Stage.IR, _stage_ir)
    pipeline.register("cfg", Stage.IR, _stage_cfg)
    pipeline.register("dataflow", Stage.IR, _stage_dataflow)
    pipeline.register("analysis", Stage.ANALYSIS, _stage_analysis)
    pipeline.register("optimization", Stage.OPTIMIZER, _stage_optimizer)
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
    dataflow = state.get("dataflow")
    return CompileResult(
        success=success,
        mode=state.mode,
        diagnostics=bag.sorted(),
        tokens=state.get("tokens"),
        ast=serialize_program(program) if program is not None else None,
        semantic=serialize_semantic(semantic) if semantic is not None else None,
        ir=serialize_ir(lowered.program) if lowered is not None else None,
        cfg=_serialize_cfgs(cfgs),
        dataflow=_serialize_dataflow(dataflow),
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


def _serialize_dataflow(analyses: dict[str, FunctionAnalysis] | None) -> dict[str, Any] | None:
    """Serialize the solved analyses, which the pipeline kept in object form."""
    if analyses is None:
        return None
    return {name: analyses[name].to_dict() for name in sorted(analyses)}


def severity_of(diagnostic: Diagnostic) -> str:
    return diagnostic.severity.value if diagnostic.severity is not Severity.ERROR else "error"
