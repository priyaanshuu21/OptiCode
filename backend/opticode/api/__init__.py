"""HTTP API layer.

The module is named ``main`` rather than ``app`` so that ``opticode.api.app`` is not
shadowed by the FastAPI instance exported below.
"""

from __future__ import annotations

from .main import app, create_app
from .schemas import CompileOptions, CompileRequest, CompileResponse, HealthResponse

__all__ = [
    "CompileOptions",
    "CompileRequest",
    "CompileResponse",
    "HealthResponse",
    "app",
    "create_app",
]
