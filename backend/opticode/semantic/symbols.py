"""Scoped symbol table.

A scope tree with one function scope per function and a nested scope per block. MiniC
allows a name to be reused in *sibling* scopes but forbids shadowing a visible name, so
lookup walks outwards while lookup-in-scope is what detects redeclaration.

Every symbol also carries an ``ir_name``: the source name, or a suffixed variant such
as ``i_2`` when the same source name is declared more than once anywhere in the program.
Two distinct declarations therefore never share an IR name, even when they live in
unrelated functions.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..core.spans import SourceSpan
from .types import TypeName

__all__ = [
    "SymbolKind",
    "ScopeKind",
    "Symbol",
    "Scope",
    "SymbolTable",
    "FunctionSignature",
]


class SymbolKind(StrEnum):
    PARAM = "param"
    LOCAL = "local"


class ScopeKind(StrEnum):
    FUNCTION = "function"
    BLOCK = "block"


@dataclass(slots=True)
class Symbol:
    """One declared variable."""

    name: str
    ir_name: str
    type_name: TypeName
    kind: SymbolKind
    scope_id: int
    span: SourceSpan
    node_id: str
    initialized: bool = False
    #: True once the symbol has been *read*. A variable that is only ever written to is
    #: reported as unused; a variable that is written and never read is exactly the
    #: dead-assignment case handled later by the finding stage.
    read: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ir_name": self.ir_name,
            "type": self.type_name.value,
            "kind": self.kind.value,
            "scope": self.scope_id,
            "declaration_span": self.span.to_dict(),
            "node_id": self.node_id,
            "initialized": self.initialized,
            "read": self.read,
        }


class Scope:
    """A single lexical scope."""

    __slots__ = ("id", "kind", "parent", "depth", "symbols", "children")

    def __init__(self, id: int, kind: ScopeKind, parent: Scope | None, depth: int) -> None:
        self.id = id
        self.kind = kind
        self.parent = parent
        self.depth = depth
        self.symbols: dict[str, Symbol] = {}
        self.children: list[Scope] = []

    def declare_in_scope(self, name: str) -> bool:
        return name in self.symbols

    def find(self, name: str) -> Symbol | None:
        """Look up ``name`` in this scope only."""
        return self.symbols.get(name)

    def lookup(self, name: str) -> Symbol | None:
        """Look up ``name`` here, then in each enclosing scope."""
        scope: Scope | None = self
        while scope is not None:
            found = scope.symbols.get(name)
            if found is not None:
                return found
            scope = scope.parent
        return None

    def function_scope(self) -> Scope:
        scope = self
        while scope.kind is ScopeKind.BLOCK and scope.parent is not None:
            scope = scope.parent
        return scope

    def walk(self):
        """Depth-first over this scope and every nested scope."""
        yield self
        for child in self.children:
            yield from child.walk()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<Scope {self.kind.value}#{self.id} {sorted(self.symbols)}>"


@dataclass(frozen=True, slots=True)
class FunctionSignature:
    """A function's interface, collected before any body is analysed.

    MiniC has no forward declarations, so every signature is known up front. That lets a
    function call be checked even when the callee is defined later in the file.
    """

    name: str
    return_type: TypeName
    params: tuple[tuple[str, TypeName], ...]
    span: SourceSpan
    node_id: str
    ir_name: str


@dataclass(slots=True)
class SymbolTable:
    """The program's scopes, signatures, and the flat list of every declared symbol."""

    functions: dict[str, FunctionSignature] = field(default_factory=dict)
    function_order: list[FunctionSignature] = field(default_factory=list)
    scopes: list[Scope] = field(default_factory=list)
    symbols: list[Symbol] = field(default_factory=list)
    _next_scope_id: int = 0
    _next_ir_names: dict[str, int] = field(default_factory=dict)
    _current: Scope | None = None

    # --------------------------------------------------------------- scopes

    def push(self, kind: ScopeKind) -> Scope:
        parent = self._current
        self._next_scope_id += 1
        depth = 0 if parent is None else parent.depth + 1
        scope = Scope(self._next_scope_id, kind, parent, depth)
        self.scopes.append(scope)
        self._current = scope
        if parent is not None:
            parent.children.append(scope)
        return scope

    def pop(self) -> None:
        assert self._current is not None, "pop without a matching push"
        self._current = self._current.parent

    @property
    def current(self) -> Scope:
        assert self._current is not None, "no scope is open"
        return self._current

    # ------------------------------------------------------------- symbols

    def declare(
        self,
        name: str,
        type_name: TypeName,
        kind: SymbolKind,
        span: SourceSpan,
        node_id: str,
        *,
        initialized: bool = False,
    ) -> Symbol:
        """Add a symbol to the current scope and give it a program-unique ``ir_name``."""
        ir_name = self._allocate_ir_name(name)
        symbol = Symbol(
            name=name,
            ir_name=ir_name,
            type_name=type_name,
            kind=kind,
            scope_id=self.current.id,
            span=span,
            node_id=node_id,
            initialized=initialized,
        )
        self.current.symbols[name] = symbol
        self.symbols.append(symbol)
        return symbol

    def _allocate_ir_name(self, name: str) -> str:
        count = self._next_ir_names.get(name, 0) + 1
        self._next_ir_names[name] = count
        return name if count == 1 else f"{name}_{count}"

    def lookup(self, name: str) -> Symbol | None:
        return self.current.lookup(name) if self._current is not None else None

    def declare_in_current_scope(self, name: str) -> bool:
        return self._current is not None and self._current.declare_in_scope(name)

    def visible(self, name: str) -> Symbol | None:
        """The symbol ``name`` resolves to from the current position, if any."""
        return self.current.lookup(name) if self._current is not None else None

    # ------------------------------------------------------------ signatures

    def add_function(self, signature: FunctionSignature) -> None:
        self.functions[signature.name] = signature
        self.function_order.append(signature)

    def signature(self, name: str) -> FunctionSignature | None:
        return self.functions.get(name)

    # ------------------------------------------------------------ rendering

    def to_dict(self) -> dict[str, object]:
        return {
            "functions": [
                {
                    "name": fn.name,
                    "ir_name": fn.ir_name,
                    "return_type": fn.return_type.value,
                    "params": [
                        {"name": pname, "type": ptype.value} for pname, ptype in fn.params
                    ],
                    "span": fn.span.to_dict(),
                    "node_id": fn.node_id,
                }
                for fn in self.function_order
            ],
            "scopes": [
                {
                    "id": scope.id,
                    "kind": scope.kind.value,
                    "depth": scope.depth,
                    "parent": scope.parent.id if scope.parent is not None else None,
                    "symbols": [scope.symbols[name].name for name in scope.symbols],
                }
                for scope in self.scopes
            ],
            "symbols": [symbol.to_dict() for symbol in self.symbols],
        }
