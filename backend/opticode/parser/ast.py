"""MiniC abstract syntax tree.

Every node is a frozen dataclass carrying a stable ``id`` (``n1``, ``n2``, …) assigned
in deterministic pre-order, a :class:`SourceSpan`, and a ``semantic_type`` slot that the
semantic analyzer fills in.

The AST keeps only semantically relevant structure: no parentheses nodes, no
punctuation nodes, no type keyword nodes. A ``BinaryExpr`` for ``a + b * c`` nests
exactly one level deep.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from ..core.spans import SourceSpan

__all__ = [
    "Node",
    "Program",
    "FunctionDecl",
    "Param",
    "Block",
    "Stmt",
    "VarDecl",
    "Assign",
    "If",
    "While",
    "For",
    "Return",
    "Break",
    "Continue",
    "ExprStmt",
    "Printf",
    "Scanf",
    "Expr",
    "BinaryExpr",
    "UnaryExpr",
    "IntLiteral",
    "FloatLiteral",
    "StringLiteral",
    "Identifier",
    "CallExpr",
    "ASSIGN_SUGAR",
    "assign_nodes",
    "iter_nodes",
    "walk",
    "walk_mut",
    "node_by_id",
    "iter_statements",
    "iter_expressions",
    "clone",
    "clone_tree",
]


# --------------------------------------------------------------------- options


@dataclass(frozen=True, slots=True)
class Node:
    """Fields shared by every AST node.

    Subclasses use ``slots=True`` dataclasses too, so these are the only three
    instance attributes every node has.
    """

    id: str = ""
    span: SourceSpan = field(default_factory=SourceSpan)
    semantic_type: str | None = None


# ------------------------------------------------------------------ statements


@dataclass(frozen=True, slots=True)
class Program(Node):
    functions: tuple[FunctionDecl, ...] = ()


@dataclass(frozen=True, slots=True)
class Param(Node):
    name: str = ""
    type_name: str = "int"


@dataclass(frozen=True, slots=True)
class FunctionDecl(Node):
    name: str = ""
    return_type: str = "int"
    params: tuple[Param, ...] = ()
    body: Block | None = None

    def param_names(self) -> list[str]:
        return [p.name for p in self.params]


@dataclass(frozen=True, slots=True)
class Block(Node):
    statements: tuple[Stmt, ...] = ()


@dataclass(frozen=True, slots=True)
class VarDecl(Node):
    """A single declarator. ``int a = 1, b;`` becomes two ``VarDecl`` nodes."""

    name: str = ""
    type_name: str = "int"
    init: Expr | None = None


@dataclass(frozen=True, slots=True)
class Assign(Node):
    """``x = e`` and desugared forms such as ``x += e`` and ``x++``.

    ``sugar`` records how the statement was written (``"="``, ``"+="``, ``"++"``) so the
    unparser and the UI can show the original form.
    """

    target: str = ""
    op: str = "="
    value: Expr | None = None
    sugar: str | None = None


@dataclass(frozen=True, slots=True)
class If(Node):
    condition: Expr | None = None
    then_branch: Stmt | None = None
    else_branch: Stmt | None = None


@dataclass(frozen=True, slots=True)
class While(Node):
    condition: Expr | None = None
    body: Stmt | None = None


@dataclass(frozen=True, slots=True)
class For(Node):
    init: Stmt | None = None
    condition: Expr | None = None
    update: Stmt | None = None
    body: Stmt | None = None


@dataclass(frozen=True, slots=True)
class Return(Node):
    value: Expr | None = None


@dataclass(frozen=True, slots=True)
class Break(Node):
    pass


@dataclass(frozen=True, slots=True)
class Continue(Node):
    pass


@dataclass(frozen=True, slots=True)
class ExprStmt(Node):
    expr: Expr | None = None


@dataclass(frozen=True, slots=True)
class Printf(Node):
    format: str = ""
    format_span: SourceSpan | None = None
    arguments: tuple[Expr, ...] = ()


@dataclass(frozen=True, slots=True)
class Scanf(Node):
    format: str = ""
    format_span: SourceSpan | None = None
    arguments: tuple[str, ...] = ()
    argument_spans: tuple[SourceSpan, ...] = ()


# ----------------------------------------------------------------- expressions


@dataclass(frozen=True, slots=True)
class BinaryExpr(Node):
    op: str = "+"
    left: Expr | None = None
    right: Expr | None = None


@dataclass(frozen=True, slots=True)
class UnaryExpr(Node):
    op: str = "-"
    operand: Expr | None = None


@dataclass(frozen=True, slots=True)
class IntLiteral(Node):
    value: int = 0
    lexeme: str = "0"


@dataclass(frozen=True, slots=True)
class FloatLiteral(Node):
    value: float = 0.0
    lexeme: str = "0.0"


@dataclass(frozen=True, slots=True)
class StringLiteral(Node):
    value: str = ""
    lexeme: str = '""'


@dataclass(frozen=True, slots=True)
class Identifier(Node):
    name: str = ""


@dataclass(frozen=True, slots=True)
class CallExpr(Node):
    callee: str = ""
    arguments: tuple[Expr, ...] = ()


# --------------------------------------------------------------------- aliases

#: `Expr` and `Stmt` are PEP 604 unions, so they work both as annotations and as
#: `isinstance` targets. There is deliberately no second tuple of node classes to keep
#: in sync with these.
Expr = (
    BinaryExpr
    | UnaryExpr
    | IntLiteral
    | FloatLiteral
    | StringLiteral
    | Identifier
    | CallExpr
)
Stmt = (
    VarDecl
    | Assign
    | If
    | While
    | For
    | Return
    | Break
    | Continue
    | ExprStmt
    | Printf
    | Scanf
    | Block
)

#: Compound-assignment tokens mapped to the plain binary operator they desugar to.
ASSIGN_SUGAR = {
    "=": "=",
    "+=": "+",
    "-=": "-",
    "*=": "*",
    "/=": "/",
    "++": "+",
    "--": "-",
}


# ------------------------------------------------------------------- traversal

#: ``type name -> ((UI role, dataclass field), ...)``, in source order. This single
#: table drives serialization, traversal, id assignment, and cloning so they can never
#: drift apart.
_CHILD_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "Program": (("function", "functions"),),
    "FunctionDecl": (("param", "params"), ("body", "body")),
    "Block": (("statement", "statements"),),
    "VarDecl": (("init", "init"),),
    "Assign": (("value", "value"),),
    "If": (("condition", "condition"), ("then", "then_branch"), ("else", "else_branch")),
    "While": (("condition", "condition"), ("body", "body")),
    "For": (("init", "init"), ("condition", "condition"), ("update", "update"), ("body", "body")),
    "Return": (("value", "value"),),
    "Break": (),
    "Continue": (),
    "ExprStmt": (("expr", "expr"),),
    "Printf": (("argument", "arguments"),),
    "Scanf": (),
    "BinaryExpr": (("left", "left"), ("right", "right")),
    "UnaryExpr": (("operand", "operand"),),
    "IntLiteral": (),
    "FloatLiteral": (),
    "StringLiteral": (),
    "Identifier": (),
    "CallExpr": (("argument", "arguments"),),
}


def _child_entries(node: Node) -> list[tuple[str, str, Node]]:
    """(role, field_name, child) for every child, in declaration order."""
    out: list[tuple[str, str, Node]] = []
    for role, field_name in _CHILD_FIELDS.get(type(node).__name__, ()):
        value = getattr(node, field_name, None)
        if value is None:
            continue
        if isinstance(value, tuple):
            out.extend((role, field_name, item) for item in value)
        else:
            out.append((role, field_name, value))
    return out


def _children_of(node: Node) -> list[tuple[str, Node]]:
    """(role, child) pairs, used by the API serializer and by traversal."""
    return [(role, child) for role, _field, child in _child_entries(node)]


def _child_field_items(node: Node) -> list[tuple[str, object]]:
    """(field_name, replacement_value) for rebuilding a node with ``dataclasses.replace``.

    The replacement keeps the original field's shape: a tuple field becomes a tuple, a
    single-child field stays a single node.
    """
    order: list[str] = []
    grouped: dict[str, list[Node]] = {}
    for _role, field_name, child in _child_entries(node):
        if field_name not in grouped:
            grouped[field_name] = []
            order.append(field_name)
        grouped[field_name].append(child)
    items: list[tuple[str, object]] = []
    for field_name in order:
        original = getattr(node, field_name)
        if isinstance(original, tuple):
            items.append((field_name, tuple(grouped[field_name])))
        else:
            items.append((field_name, grouped[field_name][0]))
    return items


def walk(root: Node) -> Iterator[Node]:
    """Pre-order traversal, identical ordering to id assignment."""
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        yield node
        stack.extend(child for _role, _field, child in reversed(_child_entries(node)))


def iter_nodes(root: Node) -> Iterator[Node]:
    return walk(root)


def walk_mut(root: Node) -> Iterator[tuple[Node, str | None, Node | None]]:
    """Like :func:`walk` but also yields the field name and parent for each node."""
    stack: list[tuple[Node, Node | None, str | None]] = [(root, None, None)]
    while stack:
        node, parent, parent_field = stack.pop()
        yield node, parent_field, parent
        for _role, field_name, child in reversed(_child_entries(node)):
            stack.append((child, node, field_name))


def node_by_id(root: Node, node_id: str) -> Node | None:
    for node in walk(root):
        if node.id == node_id:
            return node
    return None


def iter_statements(root: Node) -> Iterator[Stmt]:
    for node in walk(root):
        if isinstance(node, Stmt):
            yield node


def iter_expressions(root: Node) -> Iterator[Expr]:
    for node in walk(root):
        if isinstance(node, Expr):
            yield node


def assign_nodes(root: Node) -> Iterator[Assign]:
    for node in walk(root):
        if isinstance(node, Assign):
            yield node


def assign_statement_span(stmt: Assign) -> SourceSpan:
    """The full source range of an assignment, used to report statement-level findings."""
    if stmt.value is None:
        return stmt.span
    return SourceSpan(
        stmt.span.start_line,
        stmt.span.start_col,
        stmt.value.span.end_line,
        stmt.value.span.end_col,
    )


def replace_child(parent: Node, field_name: str, new_child: Node | None) -> Node:
    """Return a copy of ``parent`` with one child field replaced."""
    return replace(parent, **{field_name: new_child})


# --------------------------------------------------------------------- cloning


def clone(node: Node) -> Node:
    """A shallow copy of a node with the same id (used when editing)."""
    return replace(node)


def clone_tree(root: Node) -> Node:
    """A deep copy of a subtree, preserving ids (Task 8 edits a copy of the AST)."""
    mapping: dict[str, Node] = {}

    def rebuild(node: Node) -> Node:
        kwargs: dict[str, object] = {}
        for name in _dataclass_field_names(node):
            value = getattr(node, name)
            if isinstance(value, Node):
                kwargs[name] = rebuild(value)
            elif isinstance(value, tuple):
                kwargs[name] = tuple(
                    rebuild(item) if isinstance(item, Node) else item for item in value
                )
            else:
                kwargs[name] = value
        new_node = replace(node, **kwargs)
        mapping[node.id] = new_node
        return new_node

    return rebuild(root)


def _dataclass_field_names(node: Node) -> list[str]:
    from dataclasses import fields

    return [f.name for f in fields(node)]


def strip_annotations(root: Node) -> Node:
    """Return a copy with every ``semantic_type`` cleared (used by round-trip tests)."""
    mapping: dict[str, Node] = {}

    def rebuild(node: Node) -> Node:
        kwargs: dict[str, object] = {}
        for name in _dataclass_field_names(node):
            value = getattr(node, name)
            if isinstance(value, Node):
                kwargs[name] = rebuild(value)
            elif isinstance(value, tuple):
                kwargs[name] = tuple(
                    rebuild(item) if isinstance(item, Node) else item for item in value
                )
            else:
                kwargs[name] = value
        kwargs["semantic_type"] = None
        new_node = replace(node, **kwargs)
        mapping[node.id] = new_node
        return new_node

    return rebuild(root)
