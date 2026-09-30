"""Generic AST serialization for the API.

One uniform shape for every node, so the frontend never needs per-node-type knowledge:

```json
{
  "id": "n7",
  "kind": "BinaryExpr",
  "span": { "start_line": 3, "start_col": 13, "end_line": 3, "end_col": 19 },
  "semantic_type": "int",
  "attrs": { "op": "+" },
  "children": [ { "role": "left", "node": { "id": "n6", "...": "..." } } ]
}
```

``attrs`` holds only the node's own scalar fields; children are always nested objects so
the UI can collapse subtrees generically.
"""

from __future__ import annotations

from .ast import Node, _children_of

__all__ = ["serialize_node", "serialize_program", "node_kind", "ATTR_ORDER"]

#: The scalar fields shown in the UI's node label, in a stable order.
ATTR_ORDER: dict[str, tuple[str, ...]] = {
    "Program": (),
    "FunctionDecl": ("name", "return_type"),
    "Param": ("name", "type_name"),
    "Block": (),
    "VarDecl": ("name", "type_name"),
    "Assign": ("target", "sugar", "op"),
    "If": (),
    "While": (),
    "For": (),
    "Return": (),
    "Break": (),
    "Continue": (),
    "ExprStmt": (),
    "Printf": ("format",),
    "Scanf": ("format", "arguments"),
    "BinaryExpr": ("op",),
    "UnaryExpr": ("op",),
    "IntLiteral": ("value", "lexeme"),
    "FloatLiteral": ("value", "lexeme"),
    "StringLiteral": ("value", "lexeme"),
    "Identifier": ("name",),
    "CallExpr": ("callee",),
}

_SKIP_FIELDS = frozenset({"id", "span", "semantic_type"})


def node_kind(node: Node) -> str:
    return type(node).__name__


def serialize_node(node: Node) -> dict[str, object]:
    kind = node_kind(node)
    attrs: dict[str, object] = {}
    for name in ATTR_ORDER.get(kind, ()):
        if name in _SKIP_FIELDS:
            continue
        value = getattr(node, name, None)
        attrs[name] = list(value) if isinstance(value, tuple) else value
    children = [
        {"role": role, "node": serialize_node(child)} for role, child in _children_of(node)
    ]
    return {
        "id": node.id,
        "kind": kind,
        "span": node.span.to_dict(),
        "semantic_type": node.semantic_type,
        "attrs": attrs,
        "children": children,
    }


def serialize_program(program: Node) -> dict[str, object]:
    return serialize_node(program)
