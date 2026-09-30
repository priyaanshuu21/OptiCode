"""Three-address code.

TAC is one flat list of instructions per function. Every instruction has at most one
operator, so analysis and optimization never have to reason about expression trees. The
forms are::

    x = y                    t = a op b        t = op a
    t = itof a               t = ftoi a
    L:                       goto L            if_false a goto L
    t = call f(a, b)         call f(a, b)      print "fmt", a, b      read x
    return a                 return

``BINARY`` and ``UNARY`` carry their operator in :attr:`Instruction.op`; ``CALL`` carries
the callee in :attr:`Instruction.callee`; ``PRINT`` carries its format string in
:attr:`Instruction.format`. Keeping one opcode per *form* rather than per operator means
every later pass has exactly one case per behaviour to handle.

Provenance is explicit: :attr:`Instruction.ast_node_id` is the AST node an instruction
came from, and :attr:`Instruction.operand_origin_ids` holds the AST node id behind each
operand, positionally. Combined with the guarantee that lowering visits every source
expression exactly once, this is what lets optimized source be produced by editing the
AST instead of decompiling TAC.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from ..core.spans import SourceSpan
from ..semantic.symbols import SymbolTable
from ..semantic.types import TypeName
from .operands import Operand, OperandKind

__all__ = [
    "Opcode",
    "Instruction",
    "IRParam",
    "IRFunction",
    "IRProgram",
    "TRAPPING_OPS",
]


class Opcode(StrEnum):
    COPY = "copy"
    BINARY = "binary"
    UNARY = "unary"
    ITOF = "itof"
    FTOI = "ftoi"
    LABEL = "label"
    GOTO = "goto"
    IF_FALSE = "if_false"
    CALL = "call"
    PRINT = "print"
    READ = "read"
    RETURN = "return"


#: Operators whose second operand is a divisor and can therefore trap.
TRAPPING_OPS = frozenset({"/", "%"})

#: Opcodes whose execution is observable and may not be deleted or reordered.
_SIDE_EFFECT_OPS = frozenset(
    {
        Opcode.CALL,
        Opcode.PRINT,
        Opcode.READ,
        Opcode.RETURN,
        Opcode.GOTO,
        Opcode.IF_FALSE,
    }
)

#: Opcodes that transfer control.
_JUMP_OPS = frozenset({Opcode.GOTO, Opcode.IF_FALSE, Opcode.LABEL, Opcode.RETURN})


@dataclass(frozen=True, slots=True)
class Instruction:
    """One TAC instruction."""

    id: str
    opcode: Opcode
    span: SourceSpan = field(default_factory=SourceSpan)
    dest: Operand | None = None
    operands: tuple[Operand, ...] = ()
    #: Jump target for GOTO/IF_FALSE, and the label defined by LABEL.
    target: str | None = None
    #: Operator for BINARY and UNARY.
    op: str | None = None
    #: Callee name for CALL.
    callee: str | None = None
    #: Format string for PRINT.
    format: str | None = None
    #: The AST node this instruction was lowered from.
    ast_node_id: str = ""
    #: The AST node behind each operand, positionally aligned with ``operands``.
    operand_origin_ids: tuple[str, ...] = ()
    #: True for instructions the compiler inserted, such as the implicit final return.
    synthetic: bool = False
    #: Type of the value produced, when the instruction produces one.
    type: TypeName | None = None

    def __post_init__(self) -> None:
        if len(self.operand_origin_ids) != len(self.operands):
            raise ValueError(
                f"instruction {self.id}: {len(self.operands)} operands but "
                f"{len(self.operand_origin_ids)} origin ids"
            )

    # ------------------------------------------------------------- queries

    def uses(self) -> tuple[Operand, ...]:
        """Variables and temporaries this instruction reads. Constants are excluded."""
        return tuple(operand for operand in self.operands if not operand.is_const)

    def defs(self) -> tuple[Operand, ...]:
        """Variables and temporaries this instruction writes."""
        return (self.dest,) if self.dest is not None else ()

    def has_side_effects(self) -> bool:
        """True when execution is observable.

        Calls, printing, reading, returning, and jumps are all observable, so an
        optimizer may never delete or move them.
        """
        return self.opcode in _SIDE_EFFECT_OPS

    def may_trap(self) -> bool:
        """True when this instruction can abort the program.

        Division and modulo trap unless the divisor is a literal non-zero value. An
        unknown divisor *may* be zero, so it traps conservatively.
        """
        if self.opcode is not Opcode.BINARY or self.op not in TRAPPING_OPS:
            return False
        if len(self.operands) != 2:  # pragma: no cover - defensive
            return True
        divisor = self.operands[1]
        if not divisor.is_const:
            # The divisor is only known at run time, so it may be zero.
            return True
        # A literal non-zero divisor cannot trap.
        return divisor.value == 0

    def is_jump(self) -> bool:
        return self.opcode in _JUMP_OPS

    def is_temp_def(self) -> bool:
        return self.dest is not None and self.dest.kind is OperandKind.TEMP

    # -------------------------------------------------------- construction

    def with_id(self, new_id: str) -> Instruction:
        """A copy with a different id, used when a pass renumbers instructions."""
        return replace(self, id=new_id)

    def with_dest(self, new_dest: Operand | None) -> Instruction:
        return replace(self, dest=new_dest)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "opcode": self.opcode.value,
            "span": self.span.to_dict(),
            "ast_node_id": self.ast_node_id,
            "synthetic": self.synthetic,
            "type": self.type.value if self.type is not None else None,
            "dest": self.dest.to_dict() if self.dest is not None else None,
            "operands": [operand.to_dict() for operand in self.operands],
        }
        if self.target is not None:
            payload["target"] = self.target
        if self.op is not None:
            payload["op"] = self.op
        if self.callee is not None:
            payload["callee"] = self.callee
        if self.format is not None:
            payload["format"] = self.format
        if self.operand_origin_ids:
            payload["operand_origin_ids"] = list(self.operand_origin_ids)
        return payload


@dataclass(frozen=True, slots=True)
class IRParam:
    """A lowered parameter."""

    name: str
    ir_name: str
    type: TypeName

    def to_dict(self) -> dict[str, object]:
        return {"name": self.name, "ir_name": self.ir_name, "type": self.type.value}


@dataclass(frozen=True, slots=True)
class IRFunction:
    """One function's flat instruction list."""

    name: str
    return_type: TypeName
    params: tuple[IRParam, ...] = ()
    instructions: tuple[Instruction, ...] = ()
    #: Locals declared without an initializer. They produce no instruction but are live
    #: from function entry, so dataflow seeds them as defined rather than deriving that
    #: from the instruction list.
    declared_locals: tuple[str, ...] = ()
    span: SourceSpan = field(default_factory=SourceSpan)
    node_id: str = ""

    def with_instructions(self, instructions: tuple[Instruction, ...]) -> IRFunction:
        return replace(self, instructions=instructions)

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "return_type": self.return_type.value,
            "span": self.span.to_dict(),
            "node_id": self.node_id,
            "params": [param.to_dict() for param in self.params],
            "instructions": [instruction.to_dict() for instruction in self.instructions],
            "instruction_count": len(self.instructions),
            "declared_locals": list(self.declared_locals),
        }


@dataclass(frozen=True, slots=True)
class IRProgram:
    """Every lowered function, plus the symbol table that produced them."""

    functions: tuple[IRFunction, ...] = ()
    symbols: SymbolTable | None = None

    def function(self, name: str) -> IRFunction | None:
        for function in self.functions:
            if function.name == name:
                return function
        return None

    def to_dict(self) -> dict[str, object]:
        return {
            "functions": [function.to_dict() for function in self.functions],
            "function_count": len(self.functions),
            "instruction_count": sum(len(f.instructions) for f in self.functions),
        }
