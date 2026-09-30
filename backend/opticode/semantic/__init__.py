"""Semantic analysis: symbols, scopes, and types.

Public surface:

* :func:`analyze` — analyse a parsed :class:`~opticode.parser.ast.Program`.
* :class:`SemanticResult` — annotated tree, symbol table, per-node types, diagnostics.
* :func:`serialize_semantic` — the JSON payload returned by the API.
"""

from __future__ import annotations

from .analyzer import Analyzer, SemanticResult, analyze
from .format import FormatKind, FormatProblem, FormatSpec, parse_format
from .serialize import serialize_semantic
from .symbols import (
    FunctionSignature,
    Scope,
    ScopeKind,
    Symbol,
    SymbolKind,
    SymbolTable,
)
from .types import (
    ARITHMETIC_OPS,
    ASSIGN_OPS,
    COMPARISON_OPS,
    LOGICAL_OPS,
    TypeName,
    binary_result_type,
    is_numeric,
    promote,
)

__all__ = [
    "Analyzer",
    "SemanticResult",
    "analyze",
    "serialize_semantic",
    "FormatKind",
    "FormatProblem",
    "FormatSpec",
    "parse_format",
    "FunctionSignature",
    "Scope",
    "ScopeKind",
    "Symbol",
    "SymbolKind",
    "SymbolTable",
    "ARITHMETIC_OPS",
    "ASSIGN_OPS",
    "COMPARISON_OPS",
    "LOGICAL_OPS",
    "TypeName",
    "binary_result_type",
    "is_numeric",
    "promote",
]
