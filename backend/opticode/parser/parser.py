"""Recursive-descent parser for MiniC.

One function per grammar nonterminal, and one function per precedence level, so the
grammar is literally the code. Operator associativity comes from loops: ``a - b - c``
parses as ``(a - b) - c``. A dangling ``else`` binds to the nearest unmatched ``if``
because the ``else`` is consumed inside :meth:`Parser.parse_if`.

Errors use **panic-mode recovery**: report the problem, then skip tokens until a
synchronization point (``;``, ``}``, or a statement-starting keyword) and continue, so
one bad line does not hide the rest of the file. Progress is guaranteed and the number
of reported errors is capped.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from ..core.diagnostics import DiagnosticBag, DiagnosticCode, RelatedInformation, Stage
from ..core.spans import SourceSpan, span_between
from ..lexer.tokens import Token, TokenType
from .ast import (
    ASSIGN_SUGAR,
    Assign,
    BinaryExpr,
    Block,
    Break,
    CallExpr,
    Continue,
    ExprStmt,
    FloatLiteral,
    For,
    FunctionDecl,
    Identifier,
    If,
    IntLiteral,
    Node,
    Param,
    Printf,
    Program,
    Return,
    Scanf,
    StringLiteral,
    UnaryExpr,
    VarDecl,
    While,
    _child_field_items,
)

__all__ = ["Parser", "parse", "parse_tokens", "assign_ids", "MAX_DEPTH", "MAX_ERRORS"]

#: A recursive-descent parser consumes roughly a dozen Python frames per grammar level,
#: so this budget is what keeps pathological input from hitting Python's own recursion
#: limit. 50 levels is far deeper than any readable MiniC program.
MAX_DEPTH = 50
MAX_ERRORS = 50

#: Token types that may begin a statement; also used as recovery points.
_STMT_START: frozenset[TokenType] = frozenset(
    {
        TokenType.KW_INT,
        TokenType.KW_FLOAT,
        TokenType.KW_IF,
        TokenType.KW_WHILE,
        TokenType.KW_FOR,
        TokenType.KW_RETURN,
        TokenType.KW_BREAK,
        TokenType.KW_CONTINUE,
        TokenType.KW_PRINTF,
        TokenType.KW_SCANF,
        TokenType.LBRACE,
    }
)

#: Return types a function definition may use.
_RETURN_TYPE_START: frozenset[TokenType] = frozenset(
    {TokenType.KW_INT, TokenType.KW_FLOAT, TokenType.KW_VOID}
)

#: Types a local or parameter may use.
_VALUE_TYPE_START: frozenset[TokenType] = frozenset({TokenType.KW_INT, TokenType.KW_FLOAT})

#: C keywords MiniC deliberately does not have, recognised only to give a clear
#: "not supported in MiniC" message instead of a confusing cascade of syntax errors.
_UNSUPPORTED_C_KEYWORDS: frozenset[str] = frozenset(
    {
        "struct",
        "union",
        "enum",
        "typedef",
        "sizeof",
        "do",
        "switch",
        "case",
        "default",
        "goto",
        "double",
        "char",
        "long",
        "short",
        "unsigned",
        "signed",
        "static",
        "const",
        "extern",
        "register",
        "volatile",
        "inline",
        "restrict",
        "auto",
    }
)

_UNSUPPORTED_HINT = (
    "MiniC has no pointers, arrays, structs, switch, do-while, goto, or type "
    "keywords other than int, float, and void"
)

_ASSIGN_OPS: dict[TokenType, str] = {
    TokenType.ASSIGN: "=",
    TokenType.PLUS_ASSIGN: "+=",
    TokenType.MINUS_ASSIGN: "-=",
    TokenType.STAR_ASSIGN: "*=",
    TokenType.SLASH_ASSIGN: "/=",
}

_INCDEC_OPS: frozenset[TokenType] = frozenset({TokenType.INCR, TokenType.DECR})
_EQUALITY_OPS: tuple[TokenType, ...] = (TokenType.EQ, TokenType.NE)
_RELATIONAL_OPS: tuple[TokenType, ...] = (TokenType.LT, TokenType.LE, TokenType.GT, TokenType.GE)


@dataclass(frozen=True, slots=True)
class _DeclGroup:
    """Internal carrier for ``int a = 1, b;`` before the declarators are split.

    The grammar allows several declarators per declaration but the AST models one
    ``VarDecl`` per declarator, so the parser uses this transient node to fill a single
    statement slot and then splices the declarations into the enclosing block.
    """

    declarations: tuple[VarDecl, ...]


class Parser:
    def __init__(
        self,
        tokens: list[Token],
        *,
        max_errors: int = MAX_ERRORS,
        max_depth: int = MAX_DEPTH,
    ) -> None:
        self.tokens = tokens
        self.pos = 0
        self.diags = DiagnosticBag()
        self.max_errors = max_errors
        self.max_depth = max_depth
        self._depth = 0
        self._error_count = 0
        self._budget_exhausted = False

    # ------------------------------------------------------------- token access

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset: int = 0) -> Token:
        return self.tokens[min(self.pos + offset, len(self.tokens) - 1)]

    def at_end(self) -> bool:
        return self.current.type is TokenType.EOF

    def check(self, *types: TokenType) -> bool:
        return self.current.type in types

    def match(self, *types: TokenType) -> Token | None:
        if self.current.type in types:
            return self.advance()
        return None

    def advance(self) -> Token:
        if not self.at_end():
            self.pos += 1
        return self.previous()

    def previous(self) -> Token:
        return self.tokens[max(self.pos - 1, 0)]

    # ------------------------------------------------------------------ errors

    def _take_budget(self) -> bool:
        """False once the error cap is reached, so the UI never shows 500 rows."""
        if self._error_count >= self.max_errors:
            if not self._budget_exhausted:
                self._budget_exhausted = True
                self.diags.error(
                    DiagnosticCode.E207_TOO_MANY_ERRORS,
                    f"stopping after {self.max_errors} syntax errors; "
                    "fix these and compile again for more",
                    span=self.current.span,
                    stage=Stage.PARSER,
                )
            return False
        self._error_count += 1
        return True

    def error_at(
        self,
        code: DiagnosticCode,
        message: str,
        span: SourceSpan,
        related: list[RelatedInformation] | None = None,
    ) -> None:
        if self._take_budget():
            self.diags.error(code, message, span=span, stage=Stage.PARSER, related=related)

    def expected(self, what: str) -> None:
        """Report that ``what`` was expected, naming what was found instead."""
        found = self.current
        if found.type is TokenType.EOF:
            self.error_at(
                DiagnosticCode.E202_UNEXPECTED_EOF,
                f"unexpected end of input: expected {what}",
                self.previous().span,
            )
        else:
            self.error_at(
                DiagnosticCode.E201_EXPECTED_TOKEN,
                f"expected {what}, found {found.describe()}",
                found.span,
            )

    def _enter(self) -> bool:
        self._depth += 1
        if self._depth > self.max_depth:
            self.error_at(
                DiagnosticCode.E204_NESTING_TOO_DEEP,
                f"nested more than {self.max_depth} levels deep; "
                "MiniC's recursive-descent parser cannot go deeper than this",
                span=self.current.span,
            )
            self._depth -= 1
            return False
        return True

    def _exit(self) -> None:
        self._depth -= 1

    def synchronize(self) -> None:
        """Skip to the next statement boundary so parsing can continue."""
        while not self.at_end():
            if self.current.type is TokenType.SEMI:
                self.advance()
                return
            if self.current.type is TokenType.RBRACE:
                return
            if self.current.type in _STMT_START:
                return
            if self.current.type is TokenType.IDENT:
                return
            self.advance()

    # ----------------------------------------------------------------- program

    def parse(self) -> tuple[Program | None, DiagnosticBag]:
        if self.at_end():
            self.error_at(
                DiagnosticCode.E205_EMPTY_PROGRAM,
                "the program is empty: MiniC requires at least one function definition",
                span=self.current.span,
            )
            return None, self.diags

        functions: list[FunctionDecl] = []
        while not self.at_end():
            before = self.pos
            function = self.parse_function()
            if function is not None:
                functions.append(function)
            if self.pos == before:  # guarantee progress
                self.advance()

        if not functions:
            return None, self.diags

        span = SourceSpan(1, 1, functions[-1].span.end_line, functions[-1].span.end_col)
        program = Program(span=span, functions=tuple(functions))
        return assign_ids(program), self.diags
    def parse_function(self) -> FunctionDecl | None:
        if self.current.type is TokenType.IDENT and self.current.lexeme in _UNSUPPORTED_C_KEYWORDS:
            self.reject_unsupported()
            return None
        if not self.check(*_RETURN_TYPE_START):
            self.error_at(
                DiagnosticCode.E200_UNEXPECTED_TOKEN,
                "expected a function definition starting with 'int', 'float', or 'void', "
                f"found {self.current.describe()}",
                self.current.span,
            )
            self.synchronize()
            return None

        type_token = self.advance()
        return_type = type_token.lexeme

        if self.current.type is not TokenType.IDENT:
            self.expected("a function name")
            self.synchronize()
            return None
        name_token = self.advance()

        params = self.parse_param_list()
        if not self._enter():
            return None
        body = self.parse_block()
        self._exit()
        if body is None:
            self.synchronize()
            return None

        return FunctionDecl(
            span=span_between(type_token.span, body.span),
            name=name_token.lexeme,
            return_type=return_type,
            params=tuple(params),
            body=body,
        )

    def parse_param_list(self) -> list[Param]:
        if not self.check(TokenType.LPAREN):
            self.expected("'(' after the function name")
            return []
        self.advance()
        params: list[Param] = []
        if self.check(TokenType.RPAREN):
            self.advance()
            return params

        while True:
            if self.current.type is TokenType.IDENT and self.current.lexeme in _UNSUPPORTED_C_KEYWORDS:
                self.reject_unsupported()
                return params
            if not self.check(*_VALUE_TYPE_START):
                self.expected("a parameter type ('int' or 'float')")
                self.synchronize()
                return params
            type_token = self.advance()
            if self.match(TokenType.STAR) is not None:
                self.error_at(
                    DiagnosticCode.E203_UNSUPPORTED_CONSTRUCT,
                    f"pointer parameters are not supported in MiniC. {_UNSUPPORTED_HINT}",
                    span_between(type_token.span, self.previous().span),
                )
                self.synchronize()
                return params
            if self.current.type is not TokenType.IDENT:
                self.expected("a parameter name")
                self.synchronize()
                return params
            name_token = self.advance()
            params.append(
                Param(
                    span=span_between(type_token.span, name_token.span),
                    name=name_token.lexeme,
                    type_name=type_token.lexeme,
                )
            )
            if not self.match(TokenType.COMMA):
                break

        if not self.check(TokenType.RPAREN):
            self.expected("')' to close the parameter list")
            self.synchronize()
        else:
            self.advance()
        return params

    def reject_unsupported(self) -> None:
        word = self.current.lexeme
        self.error_at(
            DiagnosticCode.E203_UNSUPPORTED_CONSTRUCT,
            f"'{word}' is not supported in MiniC. {_UNSUPPORTED_HINT}",
            self.current.span,
        )
        self.synchronize()

    # ------------------------------------------------------------------ blocks

    def parse_block(self) -> Block | None:
        if not self.check(TokenType.LBRACE):
            self.expected("'{' to start a block")
            return None
        if not self._enter():
            return None
        open_token = self.advance()
        statements: list[Node] = []
        while not self.check(TokenType.RBRACE) and not self.at_end():
            before = self.pos
            statement = self.parse_statement()
            if isinstance(statement, _DeclGroup):
                statements.extend(statement.declarations)
            elif statement is not None:
                statements.append(statement)
            if self.pos == before:  # guarantee progress
                self.advance()

        if not self.check(TokenType.RBRACE):
            self.error_at(
                DiagnosticCode.E202_UNEXPECTED_EOF,
                "unexpected end of input: expected '}' to close this block",
                open_token.span,
            )
            self._exit()
            return None
        self.advance()
        span = span_between(open_token.span, self.previous().span)
        self._exit()
        return Block(span=span, statements=tuple(statements))

    # -------------------------------------------------------------- statements

    def parse_statement(self) -> Node | _DeclGroup | None:
        if not self._enter():
            return None
        try:
            return self._parse_statement_inner()
        finally:
            self._exit()

    @staticmethod
    def _as_body(statement: Node | _DeclGroup | None) -> Node | None:
        """Normalize a body statement; a declaration becomes a one-statement block."""
        if isinstance(statement, _DeclGroup):
            return Block(span=statement.declarations[0].span, statements=statement.declarations)
        return statement

    def _parse_statement_inner(self) -> Node | _DeclGroup | None:
        token = self.current

        if token.type is TokenType.LBRACE:
            return self.parse_block()
        if token.type is TokenType.SEMI:
            self.error_at(
                DiagnosticCode.E200_UNEXPECTED_TOKEN,
                "empty statement: a bare ';' is not allowed in MiniC",
                token.span,
            )
            self.advance()
            return None
        if token.type is TokenType.KW_VOID:
            self.error_at(
                DiagnosticCode.E203_UNSUPPORTED_CONSTRUCT,
                "'void' cannot be used for a variable; only functions may return void",
                token.span,
            )
            self.advance()
            self.synchronize()
            return None
        if token.type in _VALUE_TYPE_START:
            return self.parse_declaration()
        if token.type is TokenType.KW_IF:
            return self.parse_if()
        if token.type is TokenType.KW_WHILE:
            return self.parse_while()
        if token.type is TokenType.KW_FOR:
            return self.parse_for()
        if token.type is TokenType.KW_RETURN:
            return self.parse_return()
        if token.type is TokenType.KW_BREAK:
            self.advance()
            self._expect_semi()
            return Break(span=token.span)
        if token.type is TokenType.KW_CONTINUE:
            self.advance()
            self._expect_semi()
            return Continue(span=token.span)
        if token.type is TokenType.KW_PRINTF:
            return self.parse_printf()
        if token.type is TokenType.KW_SCANF:
            return self.parse_scanf()
        if token.type is TokenType.IDENT:
            if token.lexeme in _UNSUPPORTED_C_KEYWORDS:
                self.reject_unsupported()
                return None
            following = self.peek(1).type
            if following in _ASSIGN_OPS:
                return self.parse_assignment()
            if following in _INCDEC_OPS:
                return self.parse_increment()
            expression = self.parse_expression()
            self._expect_semi()
            if not isinstance(expression, CallExpr):
                self.error_at(
                    DiagnosticCode.E200_UNEXPECTED_TOKEN,
                    "MiniC statements must be a declaration, assignment, control-flow "
                    "statement, break, continue, return, printf, scanf, or function call; "
                    f"'{token.lexeme}' starts an expression that is not a call",
                    token.span,
                )
            return ExprStmt(span=expression.span, expr=expression)

        self.error_at(
            DiagnosticCode.E200_UNEXPECTED_TOKEN,
            f"expected a statement, found {token.describe()}",
            token.span,
        )
        self.synchronize()
        return None

    def _expect_semi(self) -> None:
        if self.match(TokenType.SEMI) is not None:
            return
        found = self.current
        self.error_at(
            DiagnosticCode.E201_EXPECTED_TOKEN,
            f"expected ';' to end this statement, found {found.describe()}",
            self.previous().span,
            related=[RelatedInformation(message=f"{found.describe()} appears here", span=found.span)],
        )

    def parse_declarator_list(
        self, type_token: Token, *, terminator: TokenType, declare: bool
    ) -> list[VarDecl]:
        """Parse ``a [= e] {, b [= e]}`` after a type keyword has been consumed."""
        declarations: list[VarDecl] = []
        first = True
        while True:
            if self.current.type is TokenType.STAR:
                self.advance()
                self.error_at(
                    DiagnosticCode.E203_UNSUPPORTED_CONSTRUCT,
                    f"pointer declarations are not supported in MiniC. {_UNSUPPORTED_HINT}",
                    span_between(type_token.span, self.previous().span),
                )
                self.synchronize()
                break
            if self.current.type is not TokenType.IDENT:
                self.expected(f"a variable name in the '{type_token.lexeme}' declaration")
                self.synchronize()
                break
            name_token = self.advance()
            initializer: Node | None = None
            end = name_token.span
            if self.match(TokenType.ASSIGN) is not None:
                initializer = self.parse_expression()
                end = initializer.span
            declarations.append(
                VarDecl(
                    span=SourceSpan(
                        type_token.span.start_line if first else name_token.span.start_line,
                        type_token.span.start_col if first else name_token.span.start_col,
                        end.end_line,
                        end.end_col,
                    ),
                    name=name_token.lexeme,
                    type_name=type_token.lexeme,
                    init=initializer,
                )
            )
            first = False
            if not self.match(TokenType.COMMA):
                break
        if declare:
            self._expect_semi()
        elif not self.check(terminator):
            self.expected(f"'{terminator.value}' after the declaration")
        return declarations

    def parse_declaration(self) -> _DeclGroup:
        type_token = self.advance()
        declarations = self.parse_declarator_list(
            type_token, terminator=TokenType.SEMI, declare=True
        )
        return _DeclGroup(tuple(declarations))

    def parse_assignment(self, *, semicolon: bool = True) -> Node:
        target = self.advance()
        op_token = self.advance()
        sugar = _ASSIGN_OPS[op_token.type]
        value = self.parse_expression()
        if semicolon:
            self._expect_semi()
        span = span_between(target.span, value.span)
        if sugar == "=":
            return Assign(span=span, target=target.lexeme, op="=", value=value, sugar=None)
        binary_op = ASSIGN_SUGAR[sugar]
        desugared = BinaryExpr(
            span=span,
            op=binary_op,
            left=Identifier(span=target.span, name=target.lexeme),
            right=value,
        )
        return Assign(span=span, target=target.lexeme, op=binary_op, value=desugared, sugar=sugar)

    def parse_increment(self, *, semicolon: bool = True) -> Node:
        target = self.advance()
        op_token = self.advance()
        if semicolon:
            self._expect_semi()
        span = span_between(target.span, op_token.span)
        sugar = "++" if op_token.type is TokenType.INCR else "--"
        desugared = BinaryExpr(
            span=span,
            op="+",
            left=Identifier(span=target.span, name=target.lexeme),
            right=IntLiteral(span=op_token.span, value=1, lexeme="1"),
        )
        return Assign(span=span, target=target.lexeme, op="+", value=desugared, sugar=sugar)

    def parse_if(self) -> Node | None:
        keyword = self.advance()
        if not self.match(TokenType.LPAREN):
            self.expected("'(' after 'if'")
            self.synchronize()
            return None
        condition = self.parse_expression()
        if not self.match(TokenType.RPAREN):
            self.expected("')' after the 'if' condition")
            self.synchronize()
            return None
        then_branch = self._as_body(self.parse_statement())
        else_branch: Node | None = None
        if self.check(TokenType.KW_ELSE):
            self.advance()
            else_branch = self._as_body(self.parse_statement())
        last = else_branch or then_branch
        if last is None:
            return None
        return If(
            span=span_between(keyword.span, last.span),
            condition=condition,
            then_branch=then_branch,
            else_branch=else_branch,
        )

    def parse_while(self) -> Node | None:
        keyword = self.advance()
        if not self.match(TokenType.LPAREN):
            self.expected("'(' after 'while'")
            self.synchronize()
            return None
        condition = self.parse_expression()
        if not self.match(TokenType.RPAREN):
            self.expected("')' after the 'while' condition")
            self.synchronize()
            return None
        body = self._as_body(self.parse_statement())
        if body is None:
            return None
        return While(span=span_between(keyword.span, body.span), condition=condition, body=body)

    def parse_for(self) -> Node | None:
        keyword = self.advance()
        if not self.match(TokenType.LPAREN):
            self.expected("'(' after 'for'")
            self.synchronize()
            return None

        init: Node | None
        if self.check(TokenType.SEMI):
            init = None
        elif self.check(*_VALUE_TYPE_START):
            type_token = self.advance()
            declarations = self.parse_declarator_list(
                type_token, terminator=TokenType.SEMI, declare=False
            )
            if len(declarations) == 1:
                init = declarations[0]
            elif declarations:
                init = Block(
                    span=span_between(declarations[0].span, declarations[-1].span),
                    statements=tuple(declarations),
                )
            else:
                init = None
        elif self.check(TokenType.IDENT) and self.peek(1).type in _ASSIGN_OPS:
            init = self.parse_assignment(semicolon=False)
        elif self.check(TokenType.IDENT) and self.peek(1).type in _INCDEC_OPS:
            init = self.parse_increment(semicolon=False)
        else:
            self.expected("a declaration, an assignment, an increment, or ';' in the 'for' init")
            self.synchronize()
            return None

        if not self.match(TokenType.SEMI):
            self.expected("';' after the 'for' init")
            self.synchronize()
            return None

        condition: Node | None = None
        if not self.check(TokenType.SEMI):
            condition = self.parse_expression()
        if not self.match(TokenType.SEMI):
            self.expected("';' after the 'for' condition")
            self.synchronize()
            return None

        update: Node | None = None
        if not self.check(TokenType.RPAREN):
            if self.check(TokenType.IDENT) and self.peek(1).type in _ASSIGN_OPS:
                update = self.parse_assignment(semicolon=False)
            elif self.check(TokenType.IDENT) and self.peek(1).type in _INCDEC_OPS:
                update = self.parse_increment(semicolon=False)
            else:
                self.expected("an assignment, an increment, or ')' in the 'for' update")
                self.synchronize()
                return None

        if not self.match(TokenType.RPAREN):
            self.expected("')' to close the 'for' header")
            self.synchronize()
            return None

        body = self._as_body(self.parse_statement())
        if body is None:
            return None
        return For(
            span=span_between(keyword.span, body.span),
            init=init,
            condition=condition,
            update=update,
            body=body,
        )

    def parse_return(self) -> Node:
        keyword = self.advance()
        value: Node | None = None
        if not self.check(TokenType.SEMI):
            value = self.parse_expression()
        self._expect_semi()
        span = span_between(keyword.span, self.previous().span)
        return Return(span=span, value=value)

    def parse_printf(self) -> Node:
        keyword = self.advance()
        if not self.check(TokenType.LPAREN):
            self.expected("'(' after 'printf'")
            self.synchronize()
            return Printf(span=keyword.span, format="", format_span=keyword.span)
        self.advance()
        if not self.check(TokenType.STRING):
            self.expected('a string literal format, for example "hello\\n"')
            self.synchronize()
            return Printf(span=keyword.span, format="", format_span=keyword.span)
        format_token = self.advance()
        arguments: list[Node] = []
        while self.match(TokenType.COMMA):
            arguments.append(self.parse_expression())
        if not self.check(TokenType.RPAREN):
            self.expected("')' to close the printf argument list")
            self.synchronize()
        else:
            self.advance()
        self._expect_semi()
        return Printf(
            span=span_between(keyword.span, self.previous().span),
            format=format_token.value,
            format_span=format_token.span,
            arguments=tuple(arguments),
        )

    def parse_scanf(self) -> Node:
        keyword = self.advance()
        if not self.check(TokenType.LPAREN):
            self.expected("'(' after 'scanf'")
            self.synchronize()
            return Scanf(span=keyword.span, format="", format_span=keyword.span)
        self.advance()
        if not self.check(TokenType.STRING):
            self.expected('a string literal format, for example "%d"')
            self.synchronize()
            return Scanf(span=keyword.span, format="", format_span=keyword.span)
        format_token = self.advance()
        names: list[str] = []
        name_spans: list[SourceSpan] = []
        while self.match(TokenType.COMMA):
            if self.match(TokenType.AMP) is None:
                self.expected("'&' before each scanf target, for example &x")
                self.synchronize()
                break
            if self.current.type is not TokenType.IDENT:
                self.expected("a variable name after '&'")
                self.synchronize()
                break
            name_token = self.advance()
            names.append(name_token.lexeme)
            name_spans.append(name_token.span)
        if not self.check(TokenType.RPAREN):
            self.expected("')' to close the scanf argument list")
            self.synchronize()
        else:
            self.advance()
        self._expect_semi()
        return Scanf(
            span=span_between(keyword.span, self.previous().span),
            format=format_token.value,
            format_span=format_token.span,
            arguments=tuple(names),
            argument_spans=tuple(name_spans),
        )

    # ------------------------------------------------------------- expressions

    def parse_expression(self) -> Node:
        return self.parse_logical_or()

    def _binary(self, operator: Token, left: Node, right: Node) -> BinaryExpr:
        return BinaryExpr(
            span=span_between(left.span, right.span), op=operator.lexeme, left=left, right=right
        )

    def parse_logical_or(self) -> Node:
        node = self.parse_logical_and()
        while self.check(TokenType.OROR):
            node = self._binary(self.advance(), node, self.parse_logical_and())
        return node

    def parse_logical_and(self) -> Node:
        node = self.parse_equality()
        while self.check(TokenType.ANDAND):
            node = self._binary(self.advance(), node, self.parse_equality())
        return node

    def parse_equality(self) -> Node:
        node = self.parse_relational()
        while self.check(*_EQUALITY_OPS):
            node = self._binary(self.advance(), node, self.parse_relational())
        return node

    def parse_relational(self) -> Node:
        node = self.parse_additive()
        while self.check(*_RELATIONAL_OPS):
            node = self._binary(self.advance(), node, self.parse_additive())
        return node

    def parse_additive(self) -> Node:
        node = self.parse_multiplicative()
        while self.check(TokenType.PLUS, TokenType.MINUS):
            node = self._binary(self.advance(), node, self.parse_multiplicative())
        return node

    def parse_multiplicative(self) -> Node:
        node = self.parse_unary()
        while self.check(TokenType.STAR, TokenType.SLASH, TokenType.PERCENT):
            node = self._binary(self.advance(), node, self.parse_unary())
        return node

    def parse_unary(self) -> Node:
        if self.check(TokenType.MINUS, TokenType.BANG):
            operator = self.advance()
            if not self._enter():
                return IntLiteral(span=operator.span, value=0, lexeme="0")
            operand = self.parse_unary()
            self._exit()
            return UnaryExpr(
                span=span_between(operator.span, operand.span),
                op=operator.lexeme,
                operand=operand,
            )
        if self.check(TokenType.PLUS):
            token = self.advance()
            self.error_at(
                DiagnosticCode.E200_UNEXPECTED_TOKEN,
                "unary '+' is not supported in MiniC; only unary '-' and '!' exist",
                token.span,
            )
            return self.parse_unary()
        return self.parse_primary()

    def parse_primary(self) -> Node:
        token = self.current
        if token.type is TokenType.INT:
            self.advance()
            return IntLiteral(span=token.span, value=token.value, lexeme=token.lexeme)
        if token.type is TokenType.FLOAT:
            self.advance()
            return FloatLiteral(span=token.span, value=token.value, lexeme=token.lexeme)
        if token.type is TokenType.STRING:
            self.advance()
            return StringLiteral(span=token.span, value=token.value, lexeme=token.lexeme)
        if token.type is TokenType.IDENT:
            self.advance()
            if not self.check(TokenType.LPAREN):
                return Identifier(span=token.span, name=token.lexeme)
            self.advance()
            arguments: list[Node] = []
            if not self.check(TokenType.RPAREN):
                while True:
                    arguments.append(self.parse_expression())
                    if not self.match(TokenType.COMMA):
                        break
            if not self.check(TokenType.RPAREN):
                self.expected("')' to close the argument list")
                self.synchronize()
            else:
                self.advance()
            return CallExpr(
                span=span_between(token.span, self.previous().span),
                callee=token.lexeme,
                arguments=tuple(arguments),
            )
        if token.type is TokenType.LPAREN:
            self.advance()
            if not self._enter():
                return IntLiteral(span=token.span, value=0, lexeme="0")
            inner = self.parse_expression()
            self._exit()
            if not self.check(TokenType.RPAREN):
                self.expected("')' to close this group")
                self.synchronize()
            else:
                self.advance()
            return inner
        if token.type is TokenType.AMP:
            self.advance()
            self.error_at(
                DiagnosticCode.E200_UNEXPECTED_TOKEN,
                "'&' is only allowed in a scanf argument, as in scanf(\"%d\", &x)",
                token.span,
            )
            return self.parse_primary()

        if token.type is TokenType.EOF:
            self.error_at(
                DiagnosticCode.E202_UNEXPECTED_EOF,
                "unexpected end of input: expected an expression",
                self.previous().span,
            )
        else:
            self.error_at(
                DiagnosticCode.E200_UNEXPECTED_TOKEN,
                f"expected an expression, found {token.describe()}",
                token.span,
            )
        self.advance()
        return IntLiteral(span=token.span, value=0, lexeme="0")


# ------------------------------------------------------------------ utilities


def assign_ids(root: Program) -> Program:
    """Assign deterministic ``n1``, ``n2``, … ids in pre-order."""
    counter = [0]

    def rebuild(node: Node) -> Node:
        counter[0] += 1
        rebuilt = replace(node, id=f"n{counter[0]}")
        for field, value in _child_field_items(rebuilt):
            rebuilt = replace(
                rebuilt,
                **{
                    field: tuple(rebuild(c) for c in value)
                    if isinstance(value, tuple)
                    else rebuild(value)
                },
            )
        return rebuilt

    return rebuild(root)  # type: ignore[return-value]


def parse_tokens(tokens: list[Token]) -> tuple[Program | None, DiagnosticBag]:
    """Parse a token stream produced by the lexer."""
    return Parser(tokens).parse()


def parse(source: str) -> tuple[Program | None, DiagnosticBag]:
    """Lex then parse. Used by tests and by the codegen self-check in Task 8."""
    from ..lexer.lexer import Lexer

    tokens, lex_diags = Lexer(source).scan()
    parser = Parser(tokens)
    parser.diags.extend(lex_diags)
    return parser.parse()
