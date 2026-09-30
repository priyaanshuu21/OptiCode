"""Lowering from the annotated AST to three-address code.

One function becomes one flat list of :class:`~opticode.ir.tac.Instruction` objects. There
is no expression tree left in the output: every source expression is visited exactly once
and replaced by the instructions that compute its value, so lowering never duplicates
work. That property is what makes the optimization pass able to edit the AST and the
provenance fields able to explain any instruction back to source.

Types are taken from the semantic stage, never re-derived here. Conversions the language
implies â€” promoting an ``int`` into a ``float`` arithmetic operand, narrowing a ``float``
into an ``int`` target â€” become explicit ``itof``/``ftoi`` instructions so later passes
and the debugger never have to guess.

``&&`` and ``||`` lower to the branch sequence that preserves C short-circuiting, using
the canonical ``t = 0`` / ``if_false`` / ``goto`` shape rather than a dedicated opcode.
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    Return,
    Scanf,
    Stmt,
    StringLiteral,
    UnaryExpr,
    VarDecl,
    While,
)
from ..semantic.analyzer import SemanticResult
from ..semantic.symbols import Symbol
from ..semantic.types import COMPARISON_OPS, LOGICAL_OPS, TypeName
from .operands import Operand
from .tac import Instruction, IRFunction, IRParam, IRProgram, Opcode

__all__ = ["LowerError", "LowerResult", "lower"]

#: Comparison operators that MiniC spells as C keywords.
_WORD_COMPARISONS = {
    "==": "==",
    "!=": "!=",
    "<": "<",
    "<=": "<=",
    ">": ">",
    ">=": ">=",
}


class LowerError(Exception):
    """Raised when the tree cannot be lowered, which only happens for a broken AST."""


class _LoopContext:
    """The break and continue targets of the innermost enclosing loop."""

    __slots__ = ("break_label", "continue_label")

    def __init__(self, break_label: str, continue_label: str) -> None:
        self.break_label = break_label
        self.continue_label = continue_label


class _FunctionLowerer:
    """Lowers one function. Reused per function so counters restart at 1."""

    def __init__(self, result: SemanticResult) -> None:
        # Bindings come from the semantic stage rather than a fresh scope walk: the
        # analyzer has already closed every scope, and re-resolving a name here could
        # disagree with the declaration it actually found.
        self.result = result
        self.symbols = result.symbols
        self.types = result.types
        self.bindings = result.bindings
        self.scanf_targets = result.scanf_targets
        self.instructions: list[Instruction] = []
        #: Locals declared without an initializer. They emit no instruction but are still
        #: live for the whole function, so the invariant checker seeds them as defined.
        self.declared_locals: list[str] = []
        self._next_instruction = 1
        self._next_temp = 1
        self._next_label = 1
        self._loops: list[_LoopContext] = []

    # ------------------------------------------------------------- emission

    def _emit(self, instruction: Instruction) -> Instruction:
        self.instructions.append(instruction)
        return instruction

    def _new_temp(self, type_name: TypeName) -> Operand:
        name = f"t{self._next_temp}"
        self._next_temp += 1
        return Operand.temp(name, type_name)

    def _new_label(self, hint: str = "L") -> str:
        label = f"{hint}{self._next_label}"
        self._next_label += 1
        return label

    def _build(
        self,
        opcode: Opcode,
        span,
        *,
        node: Node | None = None,
        dest: Operand | None = None,
        operands: tuple[Operand, ...] = (),
        origins: tuple[str, ...] = (),
        target: str | None = None,
        op: str | None = None,
        callee: str | None = None,
        format_string: str | None = None,
        synthetic: bool = False,
        type_name: TypeName | None = None,
    ) -> Instruction:
        instruction_id = f"i{self._next_instruction}"
        self._next_instruction += 1
        return self._emit(
            Instruction(
                id=instruction_id,
                opcode=opcode,
                span=span if span is not None else _NO_SPAN,
                dest=dest,
                operands=operands,
                target=target,
                op=op,
                callee=callee,
                format=format_string,
                ast_node_id=node.id if node is not None else "",
                operand_origin_ids=origins,
                synthetic=synthetic,
                type=type_name if type_name is not None else (dest.type if dest else None),
            )
        )

    # -------------------------------------------------------------- lookups

    def _type_of(self, node: Node | None, fallback: TypeName = TypeName.INT) -> TypeName:
        if node is None:
            return fallback
        return self.types.get(node.id, fallback)

    def _symbol_for(self, node: Node, name: str) -> Symbol:
        """The symbol the semantic stage bound ``node`` to."""
        symbol = self.bindings.get(node.id)
        if symbol is None:
            raise LowerError(f"'{name}' is unresolved; the semantic stage must run first")
        return symbol

    def _var_operand(self, node: Node, name: str) -> Operand:
        """Resolve a source name to its program-unique IR variable operand."""
        symbol = self._symbol_for(node, name)
        return Operand.var(symbol.ir_name, symbol.type_name)

    # ---------------------------------------------------------- expressions

    def lower_expr(self, node: Expr | None) -> Operand | None:
        """Lower an expression and return the operand holding its value.

        Every path emits at most one value-producing instruction per source node, which is
        the no-duplication invariant.
        """
        if node is None:
            return None
        if isinstance(node, IntLiteral):
            return Operand.const(node.value)
        if isinstance(node, FloatLiteral):
            return Operand.const(node.value)
        if isinstance(node, StringLiteral):
            return Operand.const(node.value)
        if isinstance(node, Identifier):
            return self._var_operand(node, node.name)
        if isinstance(node, UnaryExpr):
            return self._lower_unary(node)
        if isinstance(node, BinaryExpr):
            return self._lower_binary(node)
        if isinstance(node, CallExpr):
            return self._lower_call(node)
        raise LowerError(f"cannot lower expression node {type(node).__name__}")

    def _lower_unary(self, node: UnaryExpr) -> Operand:
        operand = self.lower_expr(node.operand)
        if operand is None:
            raise LowerError("unary expression has no operand")
        result_type = self._type_of(node, operand.type)
        if node.op == "+":
            # Unary plus is a no-op; returning the operand directly avoids a COPY.
            return operand
        dest = self._new_temp(result_type)
        self._build(
            Opcode.UNARY,
            node.span,
            node=node,
            dest=dest,
            operands=(operand,),
            origins=(node.operand.id if node.operand else node.id,),
            op=node.op,
            type_name=result_type,
        )
        return dest

    def _promote(self, operand: Operand, target: TypeName, node: Expr) -> Operand:
        """Insert an explicit conversion when an operand's type must change.

        The analyzer has already accepted the program, so a mismatch here is always a
        widening or the narrowing it warned about.
        """
        if operand.type is target or target is TypeName.STRING:
            return operand
        if operand.type is TypeName.INT and target is TypeName.FLOAT:
            dest = self._new_temp(TypeName.FLOAT)
            self._build(
                Opcode.ITOF,
                node.span,
                node=node,
                dest=dest,
                operands=(operand,),
                origins=(node.id,),
                type_name=TypeName.FLOAT,
            )
            return dest
        if operand.type is TypeName.FLOAT and target is TypeName.INT:
            dest = self._new_temp(TypeName.INT)
            self._build(
                Opcode.FTOI,
                node.span,
                node=node,
                dest=dest,
                operands=(operand,),
                origins=(node.id,),
                type_name=TypeName.INT,
            )
            return dest
        return operand

    def _lower_binary(self, node: BinaryExpr) -> Operand:
        if node.op in LOGICAL_OPS:
            return self._lower_logical(node)

        left = self.lower_expr(node.left)
        right = self.lower_expr(node.right)
        if left is None or right is None:
            raise LowerError("binary expression has a missing operand")

        if node.op in COMPARISON_OPS:
            # Comparisons yield int 0 or 1 regardless of operand type.
            result_type = TypeName.INT
            operand_type = TypeName.FLOAT if TypeName.FLOAT in (left.type, right.type) else TypeName.INT
            left = self._promote(left, operand_type, node.left or node)
            right = self._promote(right, operand_type, node.right or node)
        else:
            result_type = self._type_of(node, left.type)
            left = self._promote(left, result_type, node.left or node)
            right = self._promote(right, result_type, node.right or node)

        dest = self._new_temp(result_type)
        self._build(
            Opcode.BINARY,
            node.span,
            node=node,
            dest=dest,
            operands=(left, right),
            origins=(
                node.left.id if node.left else node.id,
                node.right.id if node.right else node.id,
            ),
            op=_WORD_COMPARISONS.get(node.op, node.op),
            type_name=result_type,
        )
        return dest

    def _lower_logical(self, node: BinaryExpr) -> Operand:
        """Lower ``&&``/``||`` with branches so the right operand is conditionally read.

        Shape for ``a && b``::

            t = <a>
            if_false t goto Lshort
            t = <b>
            if_false t goto Lshort
            t = 1
            goto Lend
        Lshort:
            t = 0
        Lend:

        ``a || b`` is the mirror image, testing for truth instead of falsehood. Both use
        one opcode per step so no pass has to know about a combined short-circuit form.
        """
        result = self._new_temp(TypeName.INT)
        end = self._new_label("Lend")

        left = self.lower_expr(node.left)
        if left is None:
            raise LowerError("logical expression has a missing left operand")
        # As with a branch condition, an operand is tested for truth rather than
        # truncated, so a float of 0.5 counts as true here as it does in `if`.
        left_origin = (node.left.id if node.left else node.id,)

        right = self.lower_expr
        if node.op == "&&":
            # `a && b`: stop as soon as one operand is false.
            short_circuit = self._new_label("Lshort")
            self._build(
                Opcode.IF_FALSE, node.span, node=node,
                operands=(left,), origins=left_origin, target=short_circuit,
            )
            value = right(node.right)
            if value is None:
                raise LowerError("logical expression has a missing right operand")
            self._build(
                Opcode.IF_FALSE, node.span, node=node,
                operands=(value,),
                origins=(node.right.id if node.right else node.id,),
                target=short_circuit,
            )
        else:
            # `a || b`: stop as soon as one operand is true. There is no `if_true`
            # opcode, so a false left operand jumps *into* the right operand and an
            # unconditional `goto` leaves early. Either way the right operand is only
            # evaluated when the left one is false.
            check_right = self._new_label("Lright")
            is_true = self._new_label("Ltrue")
            is_false = self._new_label("Lfalse")
            self._build(
                Opcode.IF_FALSE, node.span, node=node,
                operands=(left,), origins=left_origin, target=check_right,
            )
            self._build(Opcode.GOTO, node.span, node=node, target=is_true)
            self._build(Opcode.LABEL, node.span, node=node, target=check_right)
            value = right(node.right)
            if value is None:
                raise LowerError("logical expression has a missing right operand")
            self._build(
                Opcode.IF_FALSE, node.span, node=node,
                operands=(value,),
                origins=(node.right.id if node.right else node.id,),
                target=is_false,
            )
            self._build(Opcode.LABEL, node.span, node=node, target=is_true)
            self._emit_constant(result, 1, node)
            self._build(Opcode.GOTO, node.span, node=node, target=end)
            self._build(Opcode.LABEL, node.span, node=node, target=is_false)
            self._emit_constant(result, 0, node)
            self._build(Opcode.LABEL, node.span, node=node, target=end)
            return result

        # Reached only when every operand was truthy.
        self._emit_constant(result, 1, node)
        self._build(Opcode.GOTO, node.span, node=node, target=end)
        # Reached when some operand was falsy: the value is 0.
        self._build(Opcode.LABEL, node.span, node=node, target=short_circuit)
        self._emit_constant(result, 0, node)
        self._build(Opcode.LABEL, node.span, node=node, target=end)
        return result

    def _emit_constant(self, dest: Operand, value: int, node: Node | None) -> None:
        self._build(
            Opcode.COPY,
            node.span if node is not None else _NO_SPAN,
            node=node,
            dest=dest,
            operands=(Operand.const(value),),
            origins=(node.id if node is not None else "",),
            type_name=TypeName.INT,
        )

    def _lower_call(self, node: CallExpr) -> Operand:
        arguments = tuple(self.lower_expr(argument) for argument in node.arguments)
        if any(argument is None for argument in arguments):
            raise LowerError("call has a missing argument")
        operands = tuple(argument for argument in arguments if argument is not None)
        origins = tuple(argument.id for argument in node.arguments)

        return_type = self.symbols.signature(node.callee)
        result_type = return_type.return_type if return_type is not None else TypeName.INT

        if result_type is TypeName.VOID:
            self._build(
                Opcode.CALL,
                node.span,
                node=node,
                operands=operands,
                origins=origins,
                callee=node.callee,
            )
            return None

        dest = self._new_temp(result_type)
        self._build(
            Opcode.CALL,
            node.span,
            node=node,
            dest=dest,
            operands=operands,
            origins=origins,
            callee=node.callee,
            type_name=result_type,
        )
        return dest

    # ---------------------------------------------------------- statements

    def lower_block(self, block: Block | None) -> None:
        if block is None:
            return
        for statement in block.statements:
            self.lower_stmt(statement)

    def lower_stmt(self, node: Stmt | None) -> None:
        if node is None:
            return
        if isinstance(node, VarDecl):
            self._lower_vardecl(node)
        elif isinstance(node, Assign):
            self._lower_assign(node)
        elif isinstance(node, ExprStmt):
            operand = self.lower_expr(node.expr)
            if operand is None:
                return
            # A statement-position call to a void function produces no value; anything
            # else is already emitted, so there is nothing to add here.
        elif isinstance(node, Block):
            self.lower_block(node)
        elif isinstance(node, If):
            self._lower_if(node)
        elif isinstance(node, While):
            self._lower_while(node)
        elif isinstance(node, For):
            self._lower_for(node)
        elif isinstance(node, Return):
            self._lower_return(node)
        elif isinstance(node, Break):
            self._lower_break(node)
        elif isinstance(node, Continue):
            self._lower_continue(node)
        elif isinstance(node, Printf):
            self._lower_printf(node)
        elif isinstance(node, Scanf):
            self._lower_scanf(node)
        else:  # pragma: no cover - defensive
            raise LowerError(f"cannot lower statement node {type(node).__name__}")

    def _lower_vardecl(self, node: VarDecl) -> None:
        symbol = self._symbol_for(node, node.name)
        target = Operand.var(symbol.ir_name, symbol.type_name)
        if node.init is None:
            # A declaration without an initializer emits nothing: the variable is still
            # live from function entry, which the function records so the invariant
            # checker does not read its absence here as "never defined".
            self.declared_locals.append(symbol.ir_name)
            return
        value = self.lower_expr(node.init)
        if value is None:
            return
        value = self._promote(value, symbol.type_name, node.init)
        self._build(
            Opcode.COPY,
            node.span,
            node=node,
            dest=target,
            operands=(value,),
            origins=(node.init.id,),
            type_name=symbol.type_name,
        )

    def _lower_assign(self, node: Assign) -> None:
        """Lower any assignment form to one value-producing step plus a copy.

        The parser already desugared ``x += e`` and ``x++`` into
        ``x = x + e`` and ``x = x + 1``, so by this point every assignment is just "compute
        the value, store it in the target". Lowering the value uniformly keeps one code
        path and means the copy appears exactly once per assignment.
        """
        symbol = self._symbol_for(node, node.target)
        target = Operand.var(symbol.ir_name, symbol.type_name)

        value = self.lower_expr(node.value)
        if value is None:
            return
        value = self._promote(value, symbol.type_name, node.value or node)
        self._build(
            Opcode.COPY,
            node.span,
            node=node,
            dest=target,
            operands=(value,),
            origins=(node.value.id if node.value else node.id,),
            type_name=symbol.type_name,
        )

    def _lower_condition(self, node: Expr | None) -> Operand:
        """Lower a condition to the operand that ``if_false`` tests.

        The operand is *not* narrowed to ``int``: a float condition is compared against
        zero, so truncating ``0.5`` to ``0`` first would invert the test and silently
        change which branch runs.
        """
        operand = self.lower_expr(node)
        if operand is None:
            raise LowerError("condition has no expression")
        return operand

    def _lower_if(self, node: If) -> None:
        condition = self._lower_condition(node.condition)
        continue_target = self._new_label("Lcont")
        else_label = self._new_label("Lelse") if node.else_branch is not None else None

        if else_label is None:
            # No else branch: a false condition skips the then-branch entirely.
            skip = self._new_label("Lskip")
            self._build(
                Opcode.IF_FALSE,
                node.span,
                node=node,
                operands=(condition,),
                origins=(node.condition.id if node.condition else node.id,),
                target=skip,
            )
            self.lower_stmt(node.then_branch)
            self._build(Opcode.LABEL, node.span, node=node, target=skip)
            return

        self._build(
            Opcode.IF_FALSE,
            node.span,
            node=node,
            operands=(condition,),
            origins=(node.condition.id if node.condition else node.id,),
            target=else_label,
        )
        self.lower_stmt(node.then_branch)
        self._build(Opcode.GOTO, node.span, node=node, target=continue_target)
        self._build(Opcode.LABEL, node.span, node=node, target=else_label)
        self.lower_stmt(node.else_branch)
        self._build(Opcode.LABEL, node.span, node=node, target=continue_target)

    def _lower_while(self, node: While) -> None:
        start = self._new_label("Lstart")
        end = self._new_label("Lend")
        self._build(Opcode.LABEL, node.span, node=node, target=start)
        condition = self._lower_condition(node.condition)
        self._build(
            Opcode.IF_FALSE,
            node.span,
            node=node,
            operands=(condition,),
            origins=(node.condition.id if node.condition else node.id,),
            target=end,
        )
        self._loops.append(_LoopContext(break_label=end, continue_label=start))
        try:
            self.lower_stmt(node.body)
        finally:
            self._loops.pop()
        self._build(Opcode.GOTO, node.span, node=node, target=start)
        self._build(Opcode.LABEL, node.span, node=node, target=end)

    def _lower_for(self, node: For) -> None:
        self.lower_stmt(node.init)
        start = self._new_label("Lstart")
        end = self._new_label("Lend")
        self._build(Opcode.LABEL, node.span, node=node, target=start)
        if node.condition is not None:
            condition = self._lower_condition(node.condition)
            self._build(
                Opcode.IF_FALSE,
                node.span,
                node=node,
                operands=(condition,),
                origins=(node.condition.id,),
                target=end,
            )
        # 'continue' runs the update, so it must land *before* it rather than back at the
        # condition; jumping to the condition would re-test without ever incrementing and
        # so spin forever.
        cont = self._new_label("Lcont") if node.update is not None else start
        self._loops.append(_LoopContext(break_label=end, continue_label=cont))
        try:
            self.lower_stmt(node.body)
        finally:
            self._loops.pop()
        if cont != start:
            self._build(Opcode.LABEL, node.span, node=node, target=cont)
        self.lower_stmt(node.update)
        self._build(Opcode.GOTO, node.span, node=node, target=start)
        self._build(Opcode.LABEL, node.span, node=node, target=end)

    def _lower_return(self, node: Return) -> None:
        value = self.lower_expr(node.value)
        if value is None:
            self._build(
                Opcode.RETURN,
                node.span,
                node=node,
                type_name=TypeName.VOID,
            )
            return
        self._build(
            Opcode.RETURN,
            node.span,
            node=node,
            operands=(value,),
            origins=(node.value.id if node.value else node.id,),
            type_name=value.type,
        )

    def _lower_break(self, node: Break) -> None:
        if not self._loops:
            raise LowerError("'break' outside a loop")
        self._build(Opcode.GOTO, node.span, node=node, target=self._loops[-1].break_label)

    def _lower_continue(self, node: Continue) -> None:
        if not self._loops:
            raise LowerError("'continue' outside a loop")
        self._build(Opcode.GOTO, node.span, node=node, target=self._loops[-1].continue_label)

    def _lower_printf(self, node: Printf) -> None:
        arguments = []
        origins = []
        for argument in node.arguments:
            operand = self.lower_expr(argument)
            if operand is None:
                continue
            arguments.append(operand)
            origins.append(argument.id)
        self._build(
            Opcode.PRINT,
            node.span,
            node=node,
            operands=tuple(arguments),
            origins=tuple(origins),
            format_string=node.format,
        )

    def _lower_scanf(self, node: Scanf) -> None:
        targets = self.scanf_targets.get(node.id, ())
        for index, name in enumerate(node.arguments):
            symbol = targets[index] if index < len(targets) else None
            if symbol is None:
                raise LowerError(f"scanf target '&{name}' is unresolved")
            span = (
                node.argument_spans[index]
                if index < len(node.argument_spans)
                else node.span
            )
            self._build(
                Opcode.READ,
                span,
                node=node,
                dest=Operand.var(symbol.ir_name, symbol.type_name),
                operands=(),
                origins=(),
                type_name=symbol.type_name,
            )


#: Span used for instructions that no single source construct owns, such as the implicit
#: return at the end of a function.
_NO_SPAN = SourceSpan()


@dataclass(frozen=True, slots=True)
class LowerResult:
    """The lowered program plus any functions that could not be lowered."""

    program: IRProgram
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def lower(result: SemanticResult) -> LowerResult:
    """Lower every function in a semantically analysed program."""
    errors: list[str] = []
    functions: list[IRFunction] = []
    program = result.program

    for declaration in program.functions:
        try:
            functions.append(_lower_function(declaration, result))
        except LowerError as error:
            errors.append(f"{declaration.name}: {error}")

    return LowerResult(IRProgram(tuple(functions), result.symbols), errors)


def _lower_function(declaration: FunctionDecl, result: SemanticResult) -> IRFunction:
    signature = result.symbols.signature(declaration.name)
    return_type = signature.return_type if signature is not None else TypeName.INT

    lowerer = _FunctionLowerer(result)

    params: list[IRParam] = []
    seen: set[str] = set()
    for param in declaration.params:
        symbol = result.bindings.get(param.id)
        if symbol is None or symbol.ir_name in seen:
            # A duplicated parameter is a semantic error; skip it so lowering still
            # produces whatever is well-formed.
            continue
        seen.add(symbol.ir_name)
        params.append(
            IRParam(name=param.name, ir_name=symbol.ir_name, type=symbol.type_name)
        )

    lowerer.lower_block(declaration.body)

    if return_type is not TypeName.VOID:
        # Implicit synthetic return so TAC is total: a caller never falls off the end.
        lowerer._build(
            Opcode.RETURN,
            declaration.span,
            node=None,
            operands=(Operand.const(0),),
            origins=(declaration.id,),
            synthetic=True,
            type_name=return_type,
        )

    return IRFunction(
        name=declaration.name,
        return_type=return_type,
        params=tuple(params),
        instructions=tuple(lowerer.instructions),
        declared_locals=tuple(lowerer.declared_locals),
        span=declaration.span,
        node_id=declaration.id,
    )


