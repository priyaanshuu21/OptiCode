"""Serialization of the semantic stage for the API.

The payload is the symbol table plus per-function detail the UI needs. Each symbol
carries its source name, its program-unique IR name, its type, and the scope it was
declared in, which is everything later stages and the frontend need to point at a
variable without consulting the AST again.
"""

from __future__ import annotations

from .analyzer import SemanticResult

__all__ = ["serialize_semantic"]


def serialize_semantic(result: SemanticResult) -> dict[str, object]:
    """Turn a :class:`SemanticResult` into the API's ``semantic`` payload."""
    table = result.symbols
    table_payload = table.to_dict()

    functions: list[dict[str, object]] = [
        {
            "name": signature.name,
            "ir_name": signature.ir_name,
            "return_type": signature.return_type.value,
            "span": signature.span.to_dict(),
            "node_id": signature.node_id,
            "params": [
                {"name": name, "type": type_name.value}
                for name, type_name in signature.params
            ],
        }
        for signature in table.function_order
    ]

    return {
        "functions": functions,
        "scopes": table_payload["scopes"],
        "symbols": table_payload["symbols"],
        "summary": {
            "function_count": len(table.function_order),
            "symbol_count": len(table.symbols),
            "scope_count": len(table.scopes),
            "unused_symbols": [symbol.name for symbol in table.symbols if not symbol.read],
        },
    }
