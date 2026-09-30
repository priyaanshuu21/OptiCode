"""Cross-cutting core types: source spans, diagnostics, and pipeline orchestration."""

from __future__ import annotations

from .diagnostics import (
    Diagnostic,
    DiagnosticBag,
    DiagnosticCode,
    RelatedInformation,
    Severity,
    Stage,
)
from .pipeline import (
    MAX_SOURCE_SIZE,
    CompileMode,
    Pipeline,
    PipelineState,
    StageOutcome,
)
from .spans import SourceSpan, Span, span_between, union

__all__ = [
    "CompileMode",
    "Diagnostic",
    "DiagnosticBag",
    "DiagnosticCode",
    "MAX_SOURCE_SIZE",
    "Pipeline",
    "PipelineState",
    "RelatedInformation",
    "Severity",
    "SourceSpan",
    "Span",
    "Stage",
    "StageOutcome",
    "span_between",
    "union",
]
