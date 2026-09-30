"""Pydantic request/response schemas for the HTTP API.

These are the wire contract. Response models are deliberately loose (``Any``) for stage
payloads: the stage modules own their own serialization so the schema never drifts from
the compiler output.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

__all__ = [
    "CompileRequest",
    "CompileOptions",
    "CompileResponse",
    "HealthResponse",
    "SpanModel",
    "DiagnosticModel",
]


class SpanModel(BaseModel):
    start_line: int = Field(ge=1, description="1-based start line")
    start_col: int = Field(ge=1, description="1-based start column")
    end_line: int = Field(ge=1, description="1-based end line (exclusive)")
    end_col: int = Field(ge=1, description="1-based end column (exclusive)")


class RelatedModel(BaseModel):
    message: str
    span: SpanModel


class DiagnosticModel(BaseModel):
    severity: Literal["error", "warning", "info", "hint"]
    code: str
    message: str
    span: SpanModel | None = None
    stage: str
    related: list[RelatedModel] | None = None


class CompileOptions(BaseModel):
    passes: list[str] | None = Field(
        default=None,
        description="Optimization passes to enable; null uses the default set.",
    )
    max_iterations: int = Field(default=20, ge=1, le=100)
    include_unreachable: bool = Field(
        default=True, description="Keep unreachable blocks in the CFG output."
    )


class CompileRequest(BaseModel):
    source: str = Field(description="MiniC program text")
    mode: Literal["analyze", "optimize"] = "analyze"
    options: CompileOptions = Field(default_factory=CompileOptions)
    stdin: str | None = Field(default=None, description="Program input for interpretation")


class CompileResponse(BaseModel):
    success: bool
    mode: str
    diagnostics: list[DiagnosticModel] = Field(default_factory=list)
    tokens: list[dict[str, Any]] | None = None
    ast: Any | None = None
    semantic: Any | None = None
    ir: Any | None = None
    cfg: Any | None = None
    dataflow: Any | None = None
    analysis: Any | None = None
    optimization: Any | None = None
    codegen: Any | None = None
    timings: dict[str, float] = Field(default_factory=dict)
    stages_run: list[str] = Field(default_factory=list)
    stages_skipped: list[str] = Field(default_factory=list)


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    version: str
    stages: list[str]
