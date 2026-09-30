"""FastAPI application.

The API is stateless: every request compiles the submitted source from scratch and
returns all stages in a single JSON document. There is no session store and no
database.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .. import __version__
from ..compiler import build_pipeline, compile_source
from ..core.diagnostics import Diagnostic, DiagnosticCode, Severity, Stage
from .schemas import CompileRequest, CompileResponse, HealthResponse

__all__ = ["app", "create_app"]

logger = logging.getLogger("opticode.api")

DESCRIPTION = """
OptiCode compiles MiniC — a small C-like language — through a real pipeline and returns
every stage's output in one response. All visualizations in the UI are rendered from
this data.
"""


def _serialize_tokens(tokens: list[Any] | None) -> list[dict[str, Any]] | None:
    if tokens is None:
        return None
    return [token.to_dict() for token in tokens]


def create_app() -> FastAPI:
    application = FastAPI(
        title="OptiCode",
        version=__version__,
        description=DESCRIPTION,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    @application.get("/api/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__, stages=build_pipeline().stage_names)

    @application.post("/api/compile", response_model=CompileResponse, tags=["compile"])
    def compile_endpoint(request: CompileRequest) -> dict[str, Any]:
        try:
            result = compile_source(
                source=request.source,
                mode=request.mode,
                options=request.options.model_dump(),
                stdin=request.stdin or "",
            )
            payload = result.to_dict()
            payload["tokens"] = _serialize_tokens(result.tokens)
        except Exception as exc:  # noqa: BLE001 - last-resort containment
            logger.exception("unexpected failure handling compile request")
            diagnostic = Diagnostic(
                message=(
                    f"Internal compiler error ({type(exc).__name__}). "
                    "The compiler could not process this request."
                ),
                code=DiagnosticCode.E001_INTERNAL_UNEXPECTED,
                severity=Severity.ERROR,
                stage=Stage.INTERNAL,
            )
            payload = {
                "success": False,
                "mode": request.mode,
                "diagnostics": [diagnostic.to_dict()],
                "timings": {},
                "stages_run": [],
                "stages_skipped": [],
            }
        return payload

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(_request: Any, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error", exc_info=exc)
        diagnostic = Diagnostic(
            message="Internal server error. No stack trace is exposed.",
            code=DiagnosticCode.E001_INTERNAL_UNEXPECTED,
            severity=Severity.ERROR,
            stage=Stage.INTERNAL,
        )
        return JSONResponse(
            status_code=500,
            content={"success": False, "mode": "analyze", "diagnostics": [diagnostic.to_dict()]},
        )

    return application


app = create_app()
