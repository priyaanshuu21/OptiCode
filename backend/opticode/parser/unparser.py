"""AST unparser: canonical MiniC text from the tree.

The output uses a single canonical style — 4-space indent, braces always present, one
statement per line — because Task 8 regenerates optimized source through AST edits and
unparses the result. Two programs with the same AST therefore produce byte-identical
text, which is what makes the "no change" diff honest.

Comments are not preserved: they are not in the AST.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .ast import (
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
    Printf,
    Program,
    Return,
    Scanf,
    StringLiteral,
    UnaryExpr,
    VarDecl,
    While,
)

__all__ = ["unparse", "UnparseOptions", "expression_text", "PRECEDENCE"]

INDENT = "    "

#: Binding power per operator. Higher binds tighter.
PRECEDENCE: dict[str, int] = {
    "||": 1,
    "&&": 2,
    "==": 3,
    "!=": 3,
    "<": 4,
    "<=": 4,
    ">": 4,
    ">=": 4,
    "+": 5,
    "-": 5,
    "*": 6,
    "/": 6,
    "%": 6,
}
_UNARY_PRECEDENCE = 7
_PRIMARY_PRECEDENCE = 8

#: Statements that read well on the same line as their ``if``/``while``/``for`` header.
_SINGLE_LINE: tuple[type, ...] = (
    Assign,
    VarDecl,
    ExprStmt,
    Return,
    Break,
    Continue,
    Printf,
    Scanf,
)


@dataclass(slots=True)
class UnparseOptions:
    """Controls canonical formatting and the annotated variant used by Task 8."""

    indent: str = INDENT
    #: node id -> text placed on its own line where the statement used to be.
    removed_annotations: dict[str, str] = field(default_factory=dict)
    #: When true, produce the annotated variant with removal comments.
    annotate: bool = False
    #: Statement ids that were simplified, recorded inline for the UI.
    change_notes: dict[str, str] = field(default_factory=dict)


# ------------------------------------------------------------------ expressions


def _float_text(value: float) -> str:
    if value != value:  # NaN cannot be written as a literal in MiniC
        return "0.0"
    text = repr(float(value))
    if "e" in text or "E" in text or "." in text:
        return text
    return text + ".0"


def _string_text(value: str) -> str:
    out = ['"']
    for char in value:
        if char == '"':
            out.append('\\"')
        elif char == "\\":
            out.append("\\\\")
        elif char == "\n":
            out.append("\\n")
        elif char == "\t":
            out.append("\\t")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def expression_text(node: Node | None, parent_precedence: int = 0) -> str:
    """Render an expression, adding parentheses only where precedence requires them."""
    if node is None:
        return ""
    if isinstance(node, IntLiteral):
        return node.lexeme or str(node.value)
    if isinstance(node, FloatLiteral):
        # The original lexeme is preserved so `1.50` does not become `1.5` on a
        # round trip; synthesized nodes (from a future AST edit) fall back to a
        # canonical rendering.
        return node.lexeme or _float_text(node.value)
    if isinstance(node, StringLiteral):
        return _string_text(node.value)
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, CallExpr):
        arguments = ", ".join(expression_text(a) for a in node.arguments)
        return f"{node.callee}({arguments})"
    if isinstance(node, UnaryExpr):
        text = f"{node.op}{expression_text(node.operand, _UNARY_PRECEDENCE)}"
        return f"({text})" if _UNARY_PRECEDENCE < parent_precedence else text
    if isinstance(node, BinaryExpr):
        precedence = PRECEDENCE.get(node.op, 0)
        text = (
            f"{expression_text(node.left, precedence)} {node.op} "
            f"{expression_text(node.right, precedence + 1)}"
        )
        return f"({text})" if precedence < parent_precedence else text
    return ""


# ------------------------------------------------------------------- statements



class _Writer:
    """Emits canonical text one line at a time.

    Braces are written **exactly when the AST node is a ``Block``**. The parser only
    creates a ``Block`` when the source had braces, so this rule is what makes
    ``parse(unparse(parse(src)))`` structurally identical to ``parse(src)`` — which in
    turn is what makes a "nothing changed" diff honest.
    """

    def __init__(self, options: UnparseOptions) -> None:
        self.options = options
        self.lines: list[str] = []
        self.level = 0

    # ------------------------------------------------------------- line output

    def line(self, text: str = "") -> None:
        if text:
            self.lines.append(self.options.indent * self.level + text)
        else:
            self.lines.append("")

    def append(self, text: str) -> None:
        """Extend the line just written, so ``} else {`` stays on one line."""
        if self.lines:
            self.lines[-1] += text
        else:
            self.line(text)

    def push(self) -> None:
        self.level += 1

    def pop(self) -> None:
        self.level -= 1

    def open_block(self, block: Block) -> None:
        """Write a block whose opening ``{`` is already on the current line."""
        self.push()
        self.block_body(block)
        self.pop()
        self.line("}")

    def render(self) -> str:
        return "\n".join(self.lines)

    def note(self, node: Node) -> None:
        note = self.options.change_notes.get(node.id)
        if note and self.options.annotate:
            self.line(f"/* {note} */")

    def annotation(self, node: Node) -> str | None:
        return self.options.removed_annotations.get(node.id)

    # ---------------------------------------------------------------- program

    def program(self, program: Program) -> None:
        for index, function in enumerate(program.functions):
            if index:
                self.line()
            self.function(function)

    def function(self, function: FunctionDecl) -> None:
        params = ", ".join(f"{p.type_name} {p.name}" for p in function.params)
        self.line(f"{function.return_type} {function.name}({params}) {{")
        self.push()
        if function.body is not None:
            self.block_body(function.body)
        else:
            self.line("return;")
        self.pop()
        self.line("}")

    def block_body(self, block: Block) -> None:
        for statement in block.statements:
            self.statement(statement)

    # -------------------------------------------------------------- statements

    def statement(self, node: Node, prefix: str = "") -> None:
        removed = self.annotation(node)
        if removed is not None:
            self.line(f"{prefix}/* removed: {removed} */")
            return
        if prefix:
            self.note(node)
        if isinstance(node, Block):
            self.line(f"{prefix}{{")
            self.push()
            self.block_body(node)
            self.pop()
            self.line("}")
        elif isinstance(node, VarDecl):
            self.line(prefix + self.var_decl(node))
        elif isinstance(node, Assign):
            self.line(prefix + self.assign(node))
        elif isinstance(node, If):
            self.if_statement(node, prefix=prefix)
        elif isinstance(node, While):
            self.body(f"{prefix}while ({expression_text(node.condition)})", node.body)
        elif isinstance(node, For):
            self.for_statement(node, prefix=prefix)
        elif isinstance(node, Return):
            value = expression_text(node.value)
            self.line(f"{prefix}return {value};" if value else f"{prefix}return;")
        elif isinstance(node, Break):
            self.line(f"{prefix}break;")
        elif isinstance(node, Continue):
            self.line(f"{prefix}continue;")
        elif isinstance(node, ExprStmt):
            self.line(f"{prefix}{expression_text(node.expr)};")
        elif isinstance(node, Printf):
            arguments = "".join(f", {expression_text(a)}" for a in node.arguments)
            self.line(f"{prefix}printf({_string_text(node.format)}{arguments});")
        elif isinstance(node, Scanf):
            arguments = "".join(f", &{name}" for name in node.arguments)
            self.line(f"{prefix}scanf({_string_text(node.format)}{arguments});")
        else:  # pragma: no cover - defensive
            self.line(f"{prefix}/* unsupported {type(node).__name__} */")

    def body(self, header: str, node: Node | None, close_suffix: str = "") -> bool:
        """Write ``header`` followed by a body, bracketing it only if it is a Block.

        ``close_suffix`` is appended to the closing ``}`` so that an ``if``/``else``
        pair lands on one line the way it is normally written. Returns ``True`` when a
        closing ``}`` was written and the caller may keep extending that line.
        """
        if node is None:
            self.line(f"{header};")
            return False
        if isinstance(node, Block):
            self.line(f"{header} {{")
            self.push()
            self.block_body(node)
            self.pop()
            self.line(f"}}{close_suffix}")
            return True
        if isinstance(node, _SINGLE_LINE):
            self.statement(node, prefix=f"{header} ")
            return False
        self.line(header)
        self.push()
        self.statement(node)
        self.pop()
        return False

    def if_statement(self, node: If, prefix: str = "") -> None:
        condition = f"{prefix}if ({expression_text(node.condition)})"
        then_branch, else_branch = node.then_branch, node.else_branch
        if else_branch is None:
            self.body(condition, then_branch)
            return

        if isinstance(then_branch, _SINGLE_LINE):
            self.statement(then_branch, prefix=f"{condition} ")
            if isinstance(else_branch, If):
                self.if_statement(else_branch, prefix="else ")
            else:
                self.statement(else_branch, prefix="else ")
            return

        if not self.body(condition, then_branch, close_suffix=" else"):
            # A compound then-branch has no closing brace to hang `else` on.
            self.line("else")
        if isinstance(else_branch, If):
            self.if_statement(else_branch)
        elif isinstance(else_branch, _SINGLE_LINE):
            self.inline_statement(else_branch)
        elif isinstance(else_branch, Block):
            self.append(" {")
            self.open_block(else_branch)
        else:
            self.line()
            self.statement(else_branch)

    def for_statement(self, node: For, prefix: str = "") -> None:
        header = (
            f"{prefix}for ({self.for_header_part(node.init)}; "
            f"{expression_text(node.condition)}; {self.for_header_part(node.update)})"
        )
        self.body(header, node.body)

    def for_header_part(self, node: Node | None) -> str:
        """Render a ``for`` init or update. The header's own ``;`` supplies the
        terminator, so the statement's usual trailing ``;`` is dropped."""
        if node is None:
            return ""
        if isinstance(node, Block):
            # Only reachable for a multi-declarator `for` init: one shared type.
            declarators = [s for s in node.statements if isinstance(s, VarDecl)]
            parts = [self.var_decl(declarators[0], terminator=False, with_type=True)]
            parts += [self.var_decl(d, terminator=False, with_type=False) for d in declarators[1:]]
            return ", ".join(parts)
        if isinstance(node, VarDecl):
            return self.var_decl(node, terminator=False)
        if isinstance(node, Assign):
            return self.assign(node, terminator=False)
        return expression_text(node)

    # ------------------------------------------------------- statement text

    def statement_text(self, node: Node) -> str:
        """Single-line form of a statement, terminator included."""
        if isinstance(node, VarDecl):
            return self.var_decl(node)
        if isinstance(node, Assign):
            return self.assign(node)
        if isinstance(node, Return):
            value = expression_text(node.value)
            return f"return {value};" if value else "return;"
        if isinstance(node, Break):
            return "break;"
        if isinstance(node, Continue):
            return "continue;"
        if isinstance(node, ExprStmt):
            return f"{expression_text(node.expr)};"
        if isinstance(node, Printf):
            arguments = "".join(f", {expression_text(a)}" for a in node.arguments)
            return f"printf({_string_text(node.format)}{arguments});"
        if isinstance(node, Scanf):
            arguments = "".join(f", &{name}" for name in node.arguments)
            return f"scanf({_string_text(node.format)}{arguments});"
        raise TypeError(f"not a single-line statement: {type(node).__name__}")

    def inline_statement(self, node: Node) -> None:
        """Append a single-line statement to the line just written."""
        removed = self.annotation(node)
        if removed is not None:
            self.append(f" /* removed: {removed} */")
            return
        self.append(self.statement_text(node))

    @staticmethod
    def var_decl(node: VarDecl, terminator: bool = True, with_type: bool = True) -> str:
        tail = ";" if terminator else ""
        head = f"{node.type_name} " if with_type else ""
        if node.init is None:
            return f"{head}{node.name}{tail}"
        return f"{head}{node.name} = {expression_text(node.init)}{tail}"

    @staticmethod
    def assign(node: Assign, terminator: bool = True) -> str:
        """Re-emit the original surface form.

        ``x += e`` is stored as ``x = x + e`` with ``sugar="+="``, so the original
        right-hand side is the desugared node's *right* operand. Printing the desugared
        value here would be both wrong and unable to round-trip.
        """
        tail = ";" if terminator else ""
        sugar = node.sugar
        if sugar in ("++", "--"):
            return f"{node.target}{sugar}{tail}"
        if sugar and sugar != "=":
            original = node.value.right if isinstance(node.value, BinaryExpr) else node.value
            return f"{node.target} {sugar} {expression_text(original)}{tail}"
        return f"{node.target} = {expression_text(node.value)}{tail}"


def unparse(program: Program | Node, options: UnparseOptions | None = None) -> str:
    """Render an AST as canonical MiniC source text."""
    options = options or UnparseOptions()
    writer = _Writer(options)
    if isinstance(program, Program):
        writer.program(program)
    else:
        writer.line()
        writer.statement(program)
    return writer.render() + "\n"
