"""The TAC interpreter: the execution oracle for the whole compiler.

Every later correctness question is answered by running a program twice — once on the
original IR and once on the optimized IR — and comparing the two. That only means
something if this interpreter is right, so it is deliberately small, explicit, and
bounded:

* **Deterministic.** No clocks, no randomness, no I/O beyond the supplied input string.
  The same program and input always produce the same outcome.
* **Bounded.** A step limit stops infinite loops and a depth limit stops runaway
  recursion, both reported as statuses rather than hanging the request.
* **Total.** Traps, exhausted input, and stack overflow are all *outcomes*, not
  exceptions. A caller can always get an :class:`ExecutionOutcome` back.

Arithmetic is not implemented here. Every operator goes through
:mod:`opticode.semantics.arith`, the same module the constant folder will use, so an
interpreted program and an optimized one cannot disagree about what ``-7 / 2`` means.

A variable read before it is written is **not** a trap: it yields ``0`` and is recorded in
``uninitialized_reads``. MiniC leaves such values indeterminate, and refusing to run would
be stricter than the language itself.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass, field
from enum import StrEnum

from ..semantic.types import TypeName
from ..semantics import (
    DivisionByZero,
    arith_binary,
    arith_unary,
    escape_format,
    render_format,
    truncate_to_int,
    truthy,
)
from .operands import Operand
from .tac import Instruction, IRFunction, IRProgram, Opcode

__all__ = [
    "DEFAULT_LIMITS",
    "ExecutionError",
    "ExecutionOutcome",
    "ExecutionStatus",
    "Interpreter",
    "InterpreterLimits",
    "interpret",
    "interpret_cfg",
]

#: Generous enough for any example program, low enough to fail fast on an infinite loop.
DEFAULT_STEP_LIMIT = 2_000_000
DEFAULT_MAX_DEPTH = 512


class ExecutionError(Exception):
    """Raised when a program cannot be run at all, as opposed to running badly."""


class ExecutionStatus(StrEnum):
    """How an execution ended."""

    OK = "ok"
    TRAP = "trap"
    TIMEOUT = "timeout"
    INPUT_EXHAUSTED = "input_exhausted"
    STACK_OVERFLOW = "stack_overflow"


@dataclass(frozen=True, slots=True)
class InterpreterLimits:
    """Bounds that keep a bad program from hanging the process."""

    max_steps: int = DEFAULT_STEP_LIMIT
    max_depth: int = DEFAULT_MAX_DEPTH


DEFAULT_LIMITS = InterpreterLimits()


class _Signal(StrEnum):
    """What executing one instruction told the run loop to do next."""

    NEXT = "next"
    RETURN = "return"


class _Trap(Exception):
    """Division by zero. Carries the instruction that caused it."""

    def __init__(self, instruction_id: str, message: str) -> None:
        super().__init__(message)
        self.instruction_id = instruction_id
        self.message = message


class _StepLimit(Exception):
    """The program ran longer than the step limit allows."""


class _DepthLimit(Exception):
    """The program recursed deeper than the depth limit allows."""


class _InputExhausted(Exception):
    """A ``read`` had no input left, or the token did not parse."""

    def __init__(self, message: str, instruction_id: str = "") -> None:
        super().__init__(message)
        self.instruction_id = instruction_id


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """Everything an observer of a run can see.

    Status, return value, output, and traps together are the complete observable behaviour
    of a program. Comparing them between two IRs is how an optimization proves it changed
    nothing that matters.
    """

    status: ExecutionStatus
    return_value: float | int | None = None
    output: str = ""
    steps: int = 0
    #: Variables read before any write, in first-read order.
    uninitialized_reads: tuple[str, ...] = ()
    #: Why the run did not finish normally.
    message: str = ""
    #: The instruction the run stopped at, when it did not finish normally.
    stopped_at: str = ""
    #: The entry function, and the deepest call nesting reached.
    entry_function: str = ""
    max_depth_reached: int = 0
    #: Every trap hit, as ``"i7: division by zero"``. A run stops at the first.
    traps: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.status is ExecutionStatus.OK

    def same_observable_behaviour_as(self, other: ExecutionOutcome) -> bool:
        """True when two runs are indistinguishable from outside the compiler.

        ``steps`` is excluded because an optimization may legitimately do less work, and
        ``uninitialized_reads`` is excluded because reading an unset variable is not
        something a user can see in the output. Traps *are* compared: a program that
        starts trapping is a different program.
        """
        return (
            self.status is other.status
            and self.return_value == other.return_value
            and self.output == other.output
            and self.traps == other.traps
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status.value,
            "ok": self.ok,
            "return_value": self.return_value,
            "output": self.output,
            "steps": self.steps,
            "uninitialized_reads": list(self.uninitialized_reads),
            "message": self.message,
            "stopped_at": self.stopped_at,
            "entry_function": self.entry_function,
            "max_depth_reached": self.max_depth_reached,
            "traps": list(self.traps),
        }


@dataclass(slots=True)
class _Frame:
    """One activation record."""

    function: IRFunction
    instructions: tuple[Instruction, ...]
    depth: int = 0
    #: The program counter, as an index into ``instructions``.
    pc: int = 0
    #: label → index in ``instructions``, built once per call.
    labels: dict[str, int] = field(default_factory=dict)
    #: ir_name → value. An absent key means "never written on this path".
    variables: dict[str, float | int] = field(default_factory=dict)
    #: Names that have been seeded but not yet assigned. Reading one is an uninitialized
    #: read; assigning to one clears it, so only a genuine read-before-write is flagged.
    unwritten: set[str] = field(default_factory=set)


#: Python frames consumed per level of MiniC call nesting. ``_call`` → ``_execute`` →
#: ``_do_call`` → ``_call`` is four, plus whatever the caller itself is doing.
_PYTHON_FRAMES_PER_CALL = 4


class Interpreter:
    """Runs one lowered program."""

    def __init__(
        self,
        program: IRProgram,
        *,
        stdin: str = "",
        limits: InterpreterLimits = DEFAULT_LIMITS,
    ) -> None:
        self.program = program
        self.input = stdin
        self.limits = limits
        self._output: list[str] = []
        self._uninitialized: list[str] = []
        self._steps = 0
        self._max_depth = 0
        self._traps: list[str] = []
        self._input_pos = 0
        self._entry_name = ""

    # ------------------------------------------------------------------ public

    def run(self, entry: str = "main") -> ExecutionOutcome:
        """Run ``entry`` and return everything observable about the run."""
        function = self.program.function(entry)
        if function is None:
            available = ", ".join(item.name for item in self.program.functions) or "none"
            raise ExecutionError(f"no function named {entry!r}; available: {available}")
        self._output = []
        self._uninitialized = []
        self._steps = 0
        self._max_depth = 0
        self._traps = []
        self._input_pos = 0
        self._entry_name = entry
        try:
            with self._python_stack_room():
                value = self._call(function, [], 0)
        except _Trap as trap:
            self._traps.append(f"{trap.instruction_id}: {trap.message}")
            return self._finish(ExecutionStatus.TRAP, trap.message, trap.instruction_id)
        except _StepLimit:
            return self._finish(ExecutionStatus.TIMEOUT, "step limit exceeded")
        except _DepthLimit:
            return self._finish(ExecutionStatus.STACK_OVERFLOW, "call depth exceeded")
        except _InputExhausted as exhausted:
            return self._finish(
                ExecutionStatus.INPUT_EXHAUSTED,
                str(exhausted),
                exhausted.instruction_id,
            )
        return self._finish(ExecutionStatus.OK, return_value=value)

    @contextmanager
    def _python_stack_room(self):
        """Make Python's own recursion limit high enough for the configured call depth.

        MiniC call nesting is enforced by ``max_depth``, but the interpreter implements
        calls by recursing in Python, so Python's limit would otherwise fire first and
        surface as a ``RecursionError`` rather than as a clean ``STACK_OVERFLOW`` outcome.
        """
        needed = self.limits.max_depth * _PYTHON_FRAMES_PER_CALL + 1000
        previous = sys.getrecursionlimit()
        if needed > previous:
            sys.setrecursionlimit(needed)
        try:
            yield
        finally:
            sys.setrecursionlimit(previous)

    def _finish(
        self,
        status: ExecutionStatus,
        message: str = "",
        stopped_at: str = "",
        return_value: float | int | None = None,
    ) -> ExecutionOutcome:
        return ExecutionOutcome(
            status=status,
            return_value=return_value,
            output="".join(self._output),
            steps=self._steps,
            # dict.fromkeys deduplicates while keeping first-read order.
            uninitialized_reads=tuple(dict.fromkeys(self._uninitialized)),
            message=message,
            stopped_at=stopped_at,
            entry_function=self._entry_name,
            max_depth_reached=self._max_depth,
            traps=tuple(self._traps),
        )

    # ------------------------------------------------------------- the machine

    def _call(
        self, function: IRFunction, arguments: list[float | int], depth: int
    ) -> float | int | None:
        if depth > self.limits.max_depth:
            raise _DepthLimit
        self._max_depth = max(self._max_depth, depth)

        frame = _Frame(
            function=function,
            instructions=function.instructions,
            depth=depth,
            labels=_label_index(function),
        )
        # A local declared without an initializer is live from function entry and reads as
        # 0, so it is seeded rather than left absent. It is *not* immediately flagged: being
        # declared is not the same as being read, and a program that assigns before reading
        # never observes the indeterminate value.
        for name in function.declared_locals:
            frame.variables[name] = 0
            frame.unwritten.add(name)
        for param, value in zip(function.params, arguments, strict=False):
            frame.variables[param.ir_name] = value

        while frame.pc < len(frame.instructions):
            self._steps += 1
            if self._steps > self.limits.max_steps:
                raise _StepLimit
            instruction = frame.instructions[frame.pc]
            signal = self._execute(frame, instruction)
            if signal is _Signal.RETURN:
                if not instruction.operands:
                    return None
                return self._read(frame, instruction.operands[0], instruction)
            if signal is _Signal.NEXT:
                continue
            return signal  # pragma: no cover - defensive

        # Falling off the end returns the zero of a non-void function's synthetic return.
        return None if function.return_type is TypeName.VOID else 0

    def _execute(self, frame: _Frame, instruction: Instruction) -> _Signal:
        """Run one instruction, advancing or redirecting the program counter."""
        opcode = instruction.opcode

        # Control transfers first, because they replace the counter rather than
        # advancing it.
        if opcode is Opcode.RETURN:
            return _Signal.RETURN
        if opcode is Opcode.GOTO:
            frame.pc = self._label_index(frame, instruction)
            return _Signal.NEXT
        if opcode is Opcode.IF_FALSE:
            condition = self._read(frame, instruction.operands[0], instruction)
            frame.pc = (
                self._label_index(frame, instruction)
                if not truthy(condition)
                else frame.pc + 1
            )
            return _Signal.NEXT

        # Everything else runs and falls through to the next instruction.
        frame.pc += 1

        if opcode is Opcode.LABEL:
            # Labels only exist in the linear form; a CFG absorbs them into their block.
            # Landing on one does nothing.
            return _Signal.NEXT
        if opcode is Opcode.COPY:
            self._write(
                frame, instruction, self._read(frame, instruction.operands[0], instruction)
            )
        elif opcode is Opcode.BINARY:
            left = self._read(frame, instruction.operands[0], instruction)
            right = self._read(frame, instruction.operands[1], instruction)
            self._write(frame, instruction, self._binary(instruction, left, right))
        elif opcode is Opcode.UNARY:
            operand = self._read(frame, instruction.operands[0], instruction)
            self._write(frame, instruction, arith_unary(instruction.op or "", operand))
        elif opcode is Opcode.ITOF:
            operand = self._read(frame, instruction.operands[0], instruction)
            self._write(frame, instruction, float(operand))
        elif opcode is Opcode.FTOI:
            operand = self._read(frame, instruction.operands[0], instruction)
            self._write(frame, instruction, truncate_to_int(float(operand)))
        elif opcode is Opcode.PRINT:
            self._print(frame, instruction)
        elif opcode is Opcode.READ:
            self._read_input(frame, instruction)
        elif opcode is Opcode.CALL:
            result = self._do_call(frame, instruction)
            if instruction.dest is not None:
                self._write(frame, instruction, result)
        else:  # pragma: no cover - the Opcode enum is closed
            raise ExecutionError(f"{instruction.id}: unknown opcode {opcode}")
        return _Signal.NEXT

    # ------------------------------------------------------------- operations

    def _binary(
        self, instruction: Instruction, left: float | int, right: float | int
    ) -> float | int:
        try:
            return arith_binary(instruction.op or "", left, right)
        except DivisionByZero as error:
            raise _Trap(instruction.id, str(error)) from error
        except ValueError as error:  # pragma: no cover - the analyzer rejects these
            raise ExecutionError(f"{instruction.id}: {error}") from error

    def _print(self, frame: _Frame, instruction: Instruction) -> None:
        values = [
            self._read(frame, operand, instruction) for operand in instruction.operands
        ]
        try:
            rendered = render_format(escape_format(instruction.format or ""), values)
        except ValueError as error:  # pragma: no cover - the analyzer checks arity
            raise ExecutionError(f"{instruction.id}: {error}") from error
        self._output.append(rendered)

    def _read_input(self, frame: _Frame, instruction: Instruction) -> None:
        destination = instruction.dest
        if destination is None:  # pragma: no cover - lowering always sets a dest
            raise ExecutionError(f"{instruction.id}: read without a destination")
        token = self._next_token(instruction.id)
        if destination.type is TypeName.FLOAT:
            try:
                value: float | int = float(token)
            except ValueError as error:
                raise _InputExhausted(
                    f"{instruction.id}: {token!r} is not a float", instruction.id
                ) from error
        else:
            try:
                value = int(token)
            except ValueError:
                try:
                    # C's scanf accepts a real number for an int and truncates it.
                    value = truncate_to_int(float(token))
                except ValueError as error:
                    raise _InputExhausted(
                        f"{instruction.id}: {token!r} is not an int", instruction.id
                    ) from error
        self._write(frame, instruction, value)

    def _next_token(self, instruction_id: str) -> str:
        """Pop the next whitespace-separated token, or report that input ran out."""
        text = self.input
        index = self._input_pos
        while index < len(text) and text[index].isspace():
            index += 1
        if index >= len(text):
            raise _InputExhausted("input exhausted", instruction_id)
        start = index
        while index < len(text) and not text[index].isspace():
            index += 1
        self._input_pos = index
        return text[start:index]

    def _do_call(self, frame: _Frame, instruction: Instruction) -> float | int | None:
        callee = self.program.function(instruction.callee or "")
        if callee is None:
            raise ExecutionError(
                f"{instruction.id}: call to unknown {instruction.callee!r}"
            )
        if len(instruction.operands) != len(callee.params):
            raise ExecutionError(
                f"{instruction.id}: {callee.name} takes {len(callee.params)} arguments "
                f"but got {len(instruction.operands)}"
            )
        arguments = [
            self._read(frame, operand, instruction) for operand in instruction.operands
        ]
        return self._call(callee, arguments, frame.depth + 1)

    # -------------------------------------------------------------- operands

    def _read(
        self, frame: _Frame, operand: Operand, instruction: Instruction
    ) -> float | int:
        if operand.is_const:
            if operand.type is TypeName.STRING:  # pragma: no cover - defensive
                raise ExecutionError(f"{instruction.id}: string used as a value")
            return operand.value  # type: ignore[return-value]
        if operand.name in frame.variables:
            if operand.name in frame.unwritten:
                # Seeded at declaration but read before any assignment. C-like languages
                # leave this indeterminate, so the value stands as 0 and the read is
                # recorded rather than treated as an error.
                frame.unwritten.discard(operand.name)
                self._uninitialized.append(operand.name)
            return frame.variables[operand.name]
        # Not even declared on this path, which only happens for a name a pass invented.
        self._uninitialized.append(operand.name)
        frame.variables[operand.name] = 0
        return 0

    @staticmethod
    def _write(frame: _Frame, instruction: Instruction, value: float | int) -> None:
        destination = instruction.dest
        if destination is None or destination.is_const:  # pragma: no cover - defensive
            raise ExecutionError(f"{instruction.id} writes nothing")
        frame.unwritten.discard(destination.name)
        frame.variables[destination.name] = value

    @staticmethod
    def _label_index(frame: _Frame, instruction: Instruction) -> int:
        target = instruction.target
        if target is None or target not in frame.labels:
            raise ExecutionError(f"{instruction.id} jumps to unknown label {target!r}")
        return frame.labels[target]


def _label_index(function: IRFunction) -> dict[str, int]:
    """Map each label to the index of the instruction that follows it."""
    positions: dict[str, int] = {}
    for index, instruction in enumerate(function.instructions):
        if instruction.opcode is Opcode.LABEL and instruction.target:
            positions[instruction.target] = index + 1
    return positions


def interpret(
    program: IRProgram,
    *,
    entry: str = "main",
    stdin: str = "",
    limits: InterpreterLimits = DEFAULT_LIMITS,
) -> ExecutionOutcome:
    """Run ``entry`` on ``program`` and return the outcome."""
    return Interpreter(program, stdin=stdin, limits=limits).run(entry)


def interpret_cfg(
    program: IRProgram,
    name: str,
    *,
    stdin: str = "",
    limits: InterpreterLimits = DEFAULT_LIMITS,
) -> ExecutionOutcome:
    """Run one function of a program through its CFG's linearization instead.

    This is how :meth:`opticode.cfg.CFG.linearize` is proved correct: the same program run
    through the raw instruction list and through the linearized one must produce identical
    observable behaviour. Only ``name``'s instruction list is rebuilt, so calls to the
    program's other functions still resolve.
    """
    from ..cfg import build_cfg

    function = program.function(name)
    if function is None:
        raise ExecutionError(f"no function named {name!r}")
    rebuilt = function.with_instructions(tuple(build_cfg(function).linearize()))
    functions = tuple(
        rebuilt if item.name == name else item for item in program.functions
    )
    return interpret(
        IRProgram(functions=functions), entry=name, stdin=stdin, limits=limits
    )
