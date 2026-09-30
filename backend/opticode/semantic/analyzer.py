"""Semantic analysis: symbol tables, scopes, and types.

The analyzer walks a parsed :class:`~opticode.parser.ast.Program` once and produces:

* a :class:`SymbolTable` with function signatures and every declared variable,
* a type for every expression node, recorded by node id and applied as a ``semantic_type``
  annotation on a rebuilt copy of the tree,
* diagnostics. Analysis never stops at the first problem: every function is walked and
  every error in it is reported.

Because the parser recovers from syntax errors, the tree may be incomplete. Every
optional child is therefore treated as absent rather than assumed present.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from ..core.diagnostics import Diagnostic, DiagnosticBag, DiagnosticCode, Stage
from ..core.spans import SourceSpan
from ..parser.ast import (
    Assign,
    BinaryExpr,
    Block,
    Break,
    CallExpr,
    Continue,
    Expr,
    ExprStmt,
    FloatLiteral,
    For,
    FunctionDecl,
    Identifier,
    If,
    IntLiteral,
    Node,
    Printf,
    Program,
    Return,
    Scanf,
    Stmt,
    StringLiteral,
    UnaryExpr,
    VarDecl,
    While,
    _child_field_items,
)
from .format import check_printf, check_scanf
from .symbols import FunctionSignature, ScopeKind, Symbol, SymbolKind, SymbolTable
from .types import (
    TypeName,
    binary_result_type,
    is_numeric,
)

__all__ = ["SemanticResult", "Analyzer", "analyze"]

#: Code used when a value of the wrong type reaches a declaration, assignment, argument,
#: or return.
_TYPE_ERROR = DiagnosticCode.E303_TYPE_MISMATCH
_BAD_OPERANDS = DiagnosticCode.E304_BAD_OPERAND_TYPES
_TRUNCATION = DiagnosticCode.E308_TRUNCATION


@dataclass(slots=True)
class SemanticResult:
    """Everything the semantic stage produces."""

    program: Program
    symbols: SymbolTable
    types: dict[str, TypeName] = field(default_factory=dict)
    diagnostics: list[Diagnostic] = field(default_factory=list)
    #: The symbol each name resolved to, keyed by the AST node that named it. Lowering
    #: reads these instead of re-walking scopes: by then the analyzer has already closed
    #: every scope, and re-resolving names could disagree with what it decided.
    bindings: dict[str, Symbol] = field(default_factory=dict)
    #: scanf targets per ``Scanf`` node, positionally aligned with its arguments.
    scanf_targets: dict[str, tuple[Symbol | None, ...]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(d.severity.value == "error" for d in self.diagnostics)

    def binding(self, node: Node) -> Symbol | None:
        """The symbol ``node`` names, or ``None`` if it does not name one."""
        return self.bindings.get(node.id)


class Analyzer:
    """Single-pass semantic analysis of one program."""

    def __init__(self) -> None:
        self.bag = DiagnosticBag()
        self.symbols = SymbolTable()
        self._types: dict[str, TypeName] = {}
        self._bindings: dict[str, Symbol] = {}
        self._scanf_targets: dict[str, tuple[Symbol | None, ...]] = {}
        self._function: FunctionSignature | None = None
        self._loop_depth = 0

    # ------------------------------------------------------------- utilities

    def _error(
        self,
        code: DiagnosticCode,
        message: str,
        span: SourceSpan | None,
        related: list | None = None,
    ) -> None:
        self.bag.error(code, message, span=span, stage=Stage.SEMANTIC, related=related)

    def _warn(
        self,
        code: DiagnosticCode,
        message: str,
        span: SourceSpan | None,
        related: list | None = None,
    ) -> None:
        self.bag.warn(code, message, span=span, stage=Stage.SEMANTIC, related=related)

    def _annotate(self, node: Node | None, type_name: TypeName) -> None:
        if node is not None:
            self._types[node.id] = type_name

    # -------------------------------------------------------------- analysis

    def analyze(self, program: Program) -> SemanticResult:
        self._collect_signatures(program)

        for function in program.functions:
            self._analyze_function(function)

        self._report_unused()

        annotated = self._apply_types(program)
        return SemanticResult(
            program=annotated,
            symbols=self.symbols,
            types=dict(self._types),
            bindings=dict(self._bindings),
            scanf_targets=dict(self._scanf_targets),
            diagnostics=self.bag.sorted(),
        )

    # ------------------------------------------------- signatures (pass one)

    def _collect_signatures(self, program: Program) -> None:
        seen: dict[str, FunctionSignature] = {}
        for function in program.functions:
            params = tuple((p.name, _as_type(p.type_name)) for p in function.params)
            # Parameter names collide inside one parameter list, so give each a unique IR
            # name by declaring them for real in a throwaway scope below. The signature
            # itself only needs the source names.
            signature = FunctionSignature(
                name=function.name,
                return_type=_as_type(function.return_type),
                params=params,
                span=function.span,
                node_id=function.id,
                ir_name=function.name,
            )
            if function.name in seen:
                first = seen[function.name]
                self._error(
                    DiagnosticCode.E301_REDECLARED,
                    f"function '{function.name}' is already defined at line "
                    f"{first.span.start_line}",
                    function.span,
                )
                continue
            seen[function.name] = signature
            self.symbols.add_function(signature)

    # ----------------------------------------------- functions (pass two)

    def _analyze_function(self, function: FunctionDecl) -> None:
        signature = self.symbols.signature(function.name)
        if signature is None:
            return
        self._function = signature

        self.symbols.push(ScopeKind.FUNCTION)
        try:
            declared_params: set[str] = set()
            for param in function.params:
                type_name = _as_type(param.type_name)
                if param.name in declared_params:
                    self._error(
                        DiagnosticCode.E301_REDECLARED,
                        f"parameter '{param.name}' is declared more than once",
                        param.span,
                    )
                    continue
                declared_params.add(param.name)
                self._bindings[param.id] = self.symbols.declare(
                    param.name,
                    type_name,
                    SymbolKind.PARAM,
                    param.span,
                    param.id,
                    initialized=True,
                )

            if function.body is not None:
                self._check_statement(function.body, new_scope=False)

            if signature.return_type is not TypeName.VOID and not _ends_with_return(function.body):
                expected_type = signature.return_type
                self._warn(
                    DiagnosticCode.E315_MISSING_RETURN,
                    f"function '{function.name}' can reach the end without returning "
                    f"{_article(expected_type.value)} {expected_type.value}; the result is "
                    "undefined",
                    function.span,
                )
        finally:
            self.symbols.pop()
            self._function = None

    # --------------------------------------------------------- statements

    def _check_statement(self, node: Stmt | None, *, new_scope: bool = True) -> None:
        if node is None:
            return
        if new_scope and isinstance(node, Block):
            self.symbols.push(ScopeKind.BLOCK)
            try:
                for statement in node.statements:
                    self._check_statement(statement)
            finally:
                self.symbols.pop()
            return

        if isinstance(node, VarDecl):
            self._check_declaration(node)
        elif isinstance(node, Assign):
            self._check_assignment(node)
        elif isinstance(node, ExprStmt):
            self._check_expression(node.expr)
        elif isinstance(node, Block):
            for statement in node.statements:
                self._check_statement(statement)
        elif isinstance(node, If):
            self._check_condition(node.condition, "if")
            self._check_statement(node.then_branch)
            self._check_statement(node.else_branch)
        elif isinstance(node, While):
            self._check_condition(node.condition, "while")
            self._with_loop(lambda: self._check_statement(node.body))
        elif isinstance(node, For):
            self._check_for(node)
        elif isinstance(node, Return):
            self._check_return(node)
        elif isinstance(node, (Break, Continue)):
            self._check_loop_jump(node)
        elif isinstance(node, Printf):
            self._check_printf(node)
        elif isinstance(node, Scanf):
            self._check_scanf(node)

    def _with_loop(self, body) -> None:
        self._loop_depth += 1
        try:
            body()
        finally:
            self._loop_depth -= 1

    def _check_loop_jump(self, node: Break | Continue) -> None:
        if self._loop_depth:
            return
        word = "break" if isinstance(node, Break) else "continue"
        code = (
            DiagnosticCode.E313_BREAK_OUTSIDE_LOOP
            if isinstance(node, Break)
            else DiagnosticCode.E314_CONTINUE_OUTSIDE_LOOP
        )
        self._error(code, f"'{word}' is only allowed inside a loop", node.span)

    def _check_declaration(self, node: VarDecl) -> None:
        type_name = _as_type(node.type_name)
        self._check_redeclaration(node.name, node.span)

        init_type = self._check_expression(node.init) if node.init is not None else None
        if init_type is not None:
            self._check_assignable(type_name, init_type, node.init.span, "the initializer")

        symbol = self.symbols.declare(
            node.name,
            type_name,
            SymbolKind.LOCAL,
            node.span,
            node.id,
            initialized=node.init is not None,
        )
        self._bindings[node.id] = symbol
        self._annotate(node, type_name)

    def _check_redeclaration(self, name: str, span: SourceSpan) -> None:
        if self.symbols.declare_in_current_scope(name):
            self._error(
                DiagnosticCode.E301_REDECLARED,
                f"'{name}' is already declared in this scope",
                span,
            )
            return
        visible = self.symbols.visible(name)
        if visible is not None:
            self._error(
                DiagnosticCode.E302_SHADOWING,
                f"'{name}' shadows the {visible.kind.value} declared at line "
                f"{visible.span.start_line}; rename one of them",
                span,
            )

    def _check_assignment(self, node: Assign) -> None:
        target = self.symbols.visible(node.target)
        value_type = self._check_expression(node.value)
        if target is None:
            if self.symbols.declare_in_current_scope(node.target):
                return
            self._error(
                DiagnosticCode.E300_UNDECLARED,
                f"'{node.target}' is not declared",
                node.span,
            )
        else:
            self._bindings[node.id] = target
            if value_type is not None:
                # Writing a symbol is not reading it, so `read` stays untouched here.
                self._check_assignable(
                    target.type_name, value_type, node.value.span, "the right-hand side"
                )
        self._annotate(node, target.type_name if target is not None else TypeName.INT)

    def _check_for(self, node: For) -> None:
        self.symbols.push(ScopeKind.BLOCK)
        try:
            # The init may declare a variable that the condition, update, and body see.
            self._check_statement(node.init, new_scope=False)
            self._check_condition(node.condition, "for")
            self._check_statement(node.update, new_scope=False)
            self._with_loop(lambda: self._check_statement(node.body, new_scope=False))
        finally:
            self.symbols.pop()

    def _check_return(self, node: Return) -> None:
        expected = self._function.return_type if self._function else TypeName.INT
        if node.value is None:
            self._annotate(node, TypeName.VOID)
            if expected is not TypeName.VOID:
                self._error(
                    DiagnosticCode.E306_RETURN_TYPE,
                    f"this function returns {expected.value}, so 'return' needs a value",
                    node.span,
                )
            return

        actual = self._check_expression(node.value)
        self._annotate(node, actual or expected)
        if expected is TypeName.VOID:
            self._error(
                DiagnosticCode.E306_RETURN_TYPE,
                "this function returns void, so it cannot return a value",
                node.span,
            )
            return
        if actual is None:
            return
        if actual is TypeName.VOID:
            self._error(
                DiagnosticCode.E307_VOID_VALUE,
                "a void value cannot be returned",
                node.span,
            )
            return
        self._check_assignable(expected, actual, node.value.span, "the returned value")

    def _check_condition(self, node: Expr | None, keyword: str) -> None:
        condition_type = self._check_expression(node)
        if condition_type is None:
            return
        if condition_type is TypeName.VOID:
            self._error(
                DiagnosticCode.E307_VOID_VALUE,
                f"a void value cannot be used as the '{keyword}' condition",
                node.span if node is not None else None,
            )
            return
        if not is_numeric(condition_type):
            self._error(
                _TYPE_ERROR,
                f"the '{keyword}' condition must be int or float, not "
                f"{condition_type.value}",
                node.span if node is not None else None,
            )

    def _check_printf(self, node: Printf) -> None:
        types: list[TypeName | None] = []
        for argument in node.arguments:
            argument_type = self._check_expression(argument)
            if argument_type is TypeName.VOID:
                self._error(
                    DiagnosticCode.E307_VOID_VALUE,
                    "a void value cannot be printed",
                    argument.span,
                )
            types.append(argument_type)

        for problem in check_printf(node.format, types):
            span = _specifier_span(node, problem.offset)
            if problem.code == "E-SEM-010":
                self._error(DiagnosticCode.E311_FORMAT_COUNT, problem.message, span)
            else:
                self._error(DiagnosticCode.E310_FORMAT_MISMATCH, problem.message, span)
        self._annotate(node, TypeName.VOID)

    def _check_scanf(self, node: Scanf) -> None:
        types: list[TypeName | None] = []
        targets: list[Symbol | None] = []
        for index, name in enumerate(node.arguments):
            span = (
                node.argument_spans[index]
                if index < len(node.argument_spans)
                else node.span
            )
            symbol = self.symbols.visible(name)
            targets.append(symbol)
            if symbol is None:
                types.append(None)
                if not self.symbols.declare_in_current_scope(name):
                    self._error(
                        DiagnosticCode.E312_NOT_VARIABLE,
                        f"scanf target '&{name}' is not a declared variable in scope",
                        span,
                    )
                continue
            symbol.read = True
            types.append(symbol.type_name)
        self._scanf_targets[node.id] = tuple(targets)

        for problem in check_scanf(node.format, types):
            span = _specifier_span(node, problem.offset)
            if problem.code == "E-SEM-010":
                self._error(DiagnosticCode.E311_FORMAT_COUNT, problem.message, span)
            else:
                self._error(DiagnosticCode.E310_FORMAT_MISMATCH, problem.message, span)
        self._annotate(node, TypeName.VOID)

    # -------------------------------------------------------- expressions

    def _check_expression(self, node: Expr | None) -> TypeName | None:
        """Type an expression, annotate it, and return its type.

        ``None`` means the node was missing or already failed to type, so callers must
        not compound the failure with a second diagnostic about a null type.
        """
        if node is None:
            return None

        result = self._type_of(node)
        self._annotate(node, result)
        return result

    def _type_of(self, node: Expr) -> TypeName:
        if isinstance(node, IntLiteral):
            return TypeName.INT
        if isinstance(node, FloatLiteral):
            return TypeName.FLOAT
        if isinstance(node, StringLiteral):
            self._error(
                _TYPE_ERROR,
                "a string literal is only allowed as a printf or scanf format",
                node.span,
            )
            return TypeName.STRING
        if isinstance(node, Identifier):
            return self._type_of_identifier(node)
        if isinstance(node, UnaryExpr):
            return self._type_of_unary(node)
        if isinstance(node, BinaryExpr):
            return self._type_of_binary(node)
        if isinstance(node, CallExpr):
            return self._type_of_call(node)
        raise AssertionError(f"unhandled expression {type(node).__name__}")

    def _type_of_identifier(self, node: Identifier) -> TypeName:
        symbol = self.symbols.visible(node.name)
        if symbol is None:
            self._error(
                DiagnosticCode.E300_UNDECLARED,
                f"'{node.name}' is not declared",
                node.span,
            )
            return TypeName.INT
        symbol.read = True
        self._bindings[node.id] = symbol
        return symbol.type_name

    def _type_of_unary(self, node: UnaryExpr) -> TypeName:
        operand_type = self._check_expression(node.operand)
        if node.op == "!":
            if operand_type is TypeName.VOID:
                self._error(
                    DiagnosticCode.E307_VOID_VALUE,
                    "a void value cannot be negated",
                    node.operand.span if node.operand is not None else node.span,
                )
                return TypeName.INT
            if operand_type is TypeName.STRING:
                return TypeName.INT
            return TypeName.INT
        # Unary minus keeps the operand's numeric type.
        if operand_type is None:
            return TypeName.INT
        if not is_numeric(operand_type):
            if operand_type is TypeName.VOID:
                self._error(
                    DiagnosticCode.E307_VOID_VALUE,
                    "a void value cannot be negated",
                    node.operand.span if node.operand is not None else node.span,
                )
            return TypeName.INT
        return operand_type

    def _type_of_binary(self, node: BinaryExpr) -> TypeName:
        left_type = self._check_expression(node.left)
        right_type = self._check_expression(node.right)
        left_span = node.left.span if node.left is not None else node.span
        right_span = node.right.span if node.right is not None else node.span

        for operand_type, span, side in (
            (left_type, left_span, "left"),
            (right_type, right_span, "right"),
        ):
            if operand_type is None:
                continue
            if operand_type is TypeName.VOID:
                self._error(
                    DiagnosticCode.E307_VOID_VALUE,
                    f"a void value cannot be the {side} operand of '{node.op}'",
                    span,
                )
                return TypeName.INT
            if operand_type is TypeName.STRING:
                self._error(
                    _BAD_OPERANDS,
                    f"a string cannot be the {side} operand of '{node.op}'",
                    span,
                )
                return TypeName.INT

        if left_type is None or right_type is None:
            return TypeName.INT

        if node.op == "%" and TypeName.FLOAT in (left_type, right_type):
            self._error(
                _BAD_OPERANDS,
                "'%' requires int operands, but one side is float; use int division '/'",
                node.span,
            )
            return TypeName.INT

        self._check_constant_divisor(node, left_type, right_type)

        try:
            return binary_result_type(node.op, left_type, right_type)
        except ValueError:
            self._error(_BAD_OPERANDS, f"unsupported operator '{node.op}'", node.span)
            return TypeName.INT

    def _check_constant_divisor(
        self, node: BinaryExpr, left_type: TypeName, right_type: TypeName
    ) -> None:
        if node.op not in ("/", "%"):
            return
        right = node.right
        if not isinstance(right, IntLiteral) and not isinstance(right, FloatLiteral):
            return
        if right.value != 0:
            return
        self._warn(
            DiagnosticCode.E316_DIVIDE_BY_ZERO,
            "division by the constant zero always traps at run time",
            node.span,
        )

    def _type_of_call(self, node: CallExpr) -> TypeName:
        signature = self.symbols.signature(node.callee)
        if signature is None:
            for argument in node.arguments:
                self._check_expression(argument)
            self._error(
                DiagnosticCode.E300_UNDECLARED,
                f"call to undeclared function '{node.callee}'",
                node.span,
            )
            return TypeName.INT

        argument_types = [self._check_expression(a) for a in node.arguments]
        if len(node.arguments) != len(signature.params):
            expected = len(signature.params)
            self._error(
                DiagnosticCode.E305_ARITY,
                f"'{node.callee}' takes {expected} "
                f"{'argument' if expected == 1 else 'arguments'} but "
                f"{len(node.arguments)} "
                f"{'was' if len(node.arguments) == 1 else 'were'} given",
                node.span,
            )
        else:
            for index, ((_pname, ptype), actual, argument) in enumerate(
                zip(
                    signature.params, argument_types, node.arguments, strict=True
                ),
                start=1,
            ):
                if actual is None or actual is ptype:
                    continue
                if actual is TypeName.VOID:
                    self._error(
                        DiagnosticCode.E307_VOID_VALUE,
                        f"a void value cannot be argument {index} of '{node.callee}'",
                        argument.span,
                    )
                    continue
                self._check_assignable(ptype, actual, argument.span, f"argument {index}")

        if signature.return_type is TypeName.VOID:
            return TypeName.VOID
        return signature.return_type

    # ------------------------------------------------ assignment conversion

    def _check_assignable(
        self,
        target: TypeName,
        source: TypeName,
        span: SourceSpan,
        what: str,
    ) -> None:
        """Check that ``source`` may be stored in a ``target``.

        ``int`` widens to ``float`` silently. ``float`` narrows to ``int`` with a
        truncation warning. Anything else is an error.
        """
        if target is source:
            return
        if source is TypeName.VOID:
            self._error(
                DiagnosticCode.E307_VOID_VALUE,
                f"{what} is a void value, which cannot be used",
                span,
            )
            return
        if target is TypeName.FLOAT and source is TypeName.INT:
            return
        if target is TypeName.INT and source is TypeName.FLOAT:
            self._warn(
                _TRUNCATION,
                f"{what} is float but the target is int; the fractional part is "
                "discarded",
                span,
            )
            return
        self._error(
            _TYPE_ERROR,
            f"{what} is {source.value}, which cannot be assigned to {target.value}",
            span,
        )

    # ------------------------------------------------------------- unused

    def _report_unused(self) -> None:
        for symbol in self.symbols.symbols:
            if symbol.read:
                continue
            if symbol.kind is SymbolKind.PARAM:
                detail = "is a parameter that is never used"
            elif symbol.initialized:
                detail = "is assigned but never read"
            else:
                detail = "is never used"
            self._warn(
                DiagnosticCode.E309_UNUSED_VARIABLE,
                f"'{symbol.name}' {detail}",
                symbol.span,
            )

    # ------------------------------------------------------ type application

    def _apply_types(self, program: Program) -> Program:
        """Rebuild the tree with ``semantic_type`` set on every node we typed."""

        def rebuild(node: Node) -> Node:
            changes: dict[str, object] = {}
            if node.id in self._types:
                changes["semantic_type"] = self._types[node.id].value
            for field_name, value in _child_field_items(node):
                if isinstance(value, tuple):
                    changes[field_name] = tuple(rebuild(item) for item in value)
                elif value is not None:
                    changes[field_name] = rebuild(value)
            return replace(node, **changes)

        return rebuild(program)  # type: ignore[return-value]


def _as_type(name: str) -> TypeName:
    try:
        return TypeName(name)
    except ValueError:
        return TypeName.INT


def _specifier_span(node: Printf | Scanf, offset: int | None) -> SourceSpan:
    """The span of the format-string directive at ``offset``, else the whole call."""
    format_span = getattr(node, "format_span", None)
    if format_span is None:
        return node.span
    if offset is None:
        return format_span
    return SourceSpan(
        format_span.start_line,
        format_span.start_col + offset,
        format_span.start_line,
        format_span.start_col + offset + 2,
    )


def _article(type_name: str) -> str:
    """"a" or "an" for a type name, so messages read naturally.

    ``int`` is pronounced "eye-nt", so it takes "an"; everything else takes "a".
    """
    return "an" if type_name in ("int",) else "a"


def _ends_with_return(node: Stmt | None) -> bool:
    """True when control cannot fall off the end of ``node``.

    Deliberately conservative and structural: it looks at the tail of the statement
    tree rather than building a control-flow graph. An over-approximation here would
    mean a *missing* warning, never a spurious one.
    """
    if node is None:
        return False
    if isinstance(node, Return):
        return True
    if isinstance(node, Block):
        return _ends_with_return(node.statements[-1]) if node.statements else False
    if isinstance(node, If):
        # Only "both branches return" proves the if cannot fall through.
        return (
            node.else_branch is not None
            and _ends_with_return(node.then_branch)
            and _ends_with_return(node.else_branch)
        )
    return False


def analyze(program: Program) -> SemanticResult:
    """Analyse ``program``, returning the annotated tree, symbols, and diagnostics."""
    return Analyzer().analyze(program)
