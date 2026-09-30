"""Diagnostics: the single channel through which every stage reports problems.

User mistakes never raise exceptions. Each stage returns ``(result, diagnostics)``
and the pipeline keeps whatever earlier stages already produced, so the UI always
has something useful to show.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .spans import SourceSpan

__all__ = [
    "Severity",
    "Stage",
    "RelatedInformation",
    "Diagnostic",
    "DiagnosticBag",
    "DiagnosticCode",
]


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
    HINT = "hint"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.HINT: 0,
    Severity.INFO: 1,
    Severity.WARNING: 2,
    Severity.ERROR: 3,
}


class Stage(StrEnum):
    """The pipeline stage a diagnostic came from."""

    INPUT = "input"
    LEXER = "lexer"
    PARSER = "parser"
    SEMANTIC = "semantic"
    IR = "ir"
    CFG = "cfg"
    ANALYSIS = "analysis"
    OPTIMIZER = "optimizer"
    CODEGEN = "codegen"
    INTERNAL = "internal"


class DiagnosticCode(StrEnum):
    """Stable diagnostic identifiers, formatted as ``<AREA>-<STAGE>-<NNN>``."""

    # Internal
    E001_INTERNAL_UNEXPECTED = "E-INT-001"

    # Input
    E010_SOURCE_TOO_LARGE = "E-INP-001"
    E011_SOURCE_EMPTY = "E-INP-002"

    # Lexer
    E100_UNTERMINATED_STRING = "E-LEX-001"
    E101_UNTERMINATED_COMMENT = "E-LEX-002"
    E102_BAD_ESCAPE = "E-LEX-003"
    E103_INT_LITERAL_RANGE = "E-LEX-004"
    E104_UNEXPECTED_CHARACTER = "E-LEX-005"
    E105_PREPROCESSOR = "E-LEX-006"
    E106_LONE_PIPE = "E-LEX-007"
    E107_FLOAT_LITERAL = "E-LEX-008"

    # Parser
    E200_UNEXPECTED_TOKEN = "E-SYN-001"
    E201_EXPECTED_TOKEN = "E-SYN-002"
    E202_UNEXPECTED_EOF = "E-SYN-003"
    E203_UNSUPPORTED_CONSTRUCT = "E-SYN-004"
    E204_NESTING_TOO_DEEP = "E-SYN-005"
    E205_EMPTY_PROGRAM = "E-SYN-006"
    E206_STRAND = "E-SYN-007"
    E207_TOO_MANY_ERRORS = "E-SYN-008"

    # Semantic
    E300_UNDECLARED = "E-SEM-001"
    E301_REDECLARED = "E-SEM-002"
    E302_SHADOWING = "E-SEM-003"
    E303_TYPE_MISMATCH = "E-SEM-004"
    E304_BAD_OPERAND_TYPES = "E-SEM-005"
    E305_ARITY = "E-SEM-006"
    E306_RETURN_TYPE = "E-SEM-007"
    E307_VOID_VALUE = "E-SEM-008"
    E308_TRUNCATION = "W-SEM-001"
    E309_UNUSED_VARIABLE = "W-SEM-002"
    E310_FORMAT_MISMATCH = "E-SEM-009"
    E311_FORMAT_COUNT = "E-SEM-010"
    E312_NOT_VARIABLE = "E-SEM-011"
    E313_BREAK_OUTSIDE_LOOP = "E-SEM-012"
    E314_CONTINUE_OUTSIDE_LOOP = "E-SEM-013"
    E315_MISSING_RETURN = "W-SEM-003"
    E316_DIVIDE_BY_ZERO = "W-SEM-004"

    # Analysis
    #
    # These are the CFG-precise counterparts of the three semantic warnings above. Where the
    # analysis only restates what the semantic stage already reported, its finding carries
    # `duplicate_of` instead of producing a second warning on the same line.
    E400_UNREACHABLE_CODE = "W-ANL-001"
    E401_CONDITIONALLY_UNREACHABLE = "W-ANL-002"
    E402_DEAD_ASSIGNMENT = "W-ANL-003"
    E403_UNUSED_CALL_RESULT = "I-ANL-001"
    E404_UNUSED_READ_RESULT = "I-ANL-002"
    E405_USE_BEFORE_INIT = "W-ANL-004"
    E406_MISSING_RETURN = "W-ANL-005"
    E407_CONSTANT_CONDITION = "I-ANL-003"
    E408_DIVISION_BY_ZERO = "W-ANL-006"

    # Optimizer
    #
    # The optimizer deletes code, so its failure modes are compiler bugs rather than user
    # mistakes — a pass that breaks its own guarantee, or the interpreter oracle finding that
    # the optimized program does something the original did not. Both are reported here and
    # both leave the original program in place, so a user gets a working unoptimized compile
    # rather than a failed request.
    E500_OPTIMIZER_INTERNAL = "E-OPT-001"
    E501_OPTIMIZED = "I-OPT-001"

    # Codegen
    #
    # Codegen edits the user's source, so its failures are the same shape as the optimizer's:
    # compiler bugs, not user mistakes. The interesting one is a self-check failure — the
    # generated text did not behave like the original, so it is thrown away and the user's own
    # source is returned. That is reported as a warning rather than an error because the
    # request still succeeded and still has everything it needs; the user just does not get
    # the optimization shown back to them, and deserves to know why.
    E600_CODEGEN_INTERNAL = "E-CGN-001"
    E601_REGENERATED = "I-CGN-001"
    E602_REJECTED = "W-CGN-001"


@dataclass(frozen=True, slots=True)
class RelatedInformation:
    """A secondary source location attached to a diagnostic."""

    message: str
    span: SourceSpan

    def to_dict(self) -> dict[str, object]:
        return {"message": self.message, "span": self.span.to_dict()}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    message: str
    code: DiagnosticCode | str
    severity: Severity = Severity.ERROR
    span: SourceSpan | None = None
    stage: Stage = Stage.INTERNAL
    related: tuple[RelatedInformation, ...] = ()

    def to_dict(self) -> dict[str, object]:
        code = self.code.value if isinstance(self.code, DiagnosticCode) else str(self.code)
        payload: dict[str, object] = {
            "severity": self.severity.value,
            "code": code,
            "message": self.message,
            "span": self.span.to_dict() if self.span else None,
            "stage": self.stage.value,
        }
        if self.related:
            payload["related"] = [r.to_dict() for r in self.related]
        return payload

    @property
    def code_str(self) -> str:
        return self.code.value if isinstance(self.code, DiagnosticCode) else str(self.code)


class DiagnosticBag:
    """An ordered, de-duplicated collection of diagnostics.

    Ordering is deterministic: errors first, then by source position, then by code.
    Duplicates (same code, span, and message) are collapsed so a diagnostic produced
    by two cooperating stages is only shown once.
    """

    __slots__ = ("_items",)

    def __init__(self, items: list[Diagnostic] | None = None) -> None:
        self._items: list[Diagnostic] = list(items or [])

    def __iter__(self):
        return iter(self._items)

    def __len__(self) -> int:
        return len(self._items)

    def __bool__(self) -> bool:
        return bool(self._items)

    @property
    def items(self) -> list[Diagnostic]:
        return list(self._items)

    def add(self, diagnostic: Diagnostic) -> None:
        for existing in self._items:
            if (
                existing.code_str == diagnostic.code_str
                and existing.message == diagnostic.message
                and existing.span == diagnostic.span
            ):
                return
        self._items.append(diagnostic)

    def extend(self, diagnostics: list[Diagnostic] | None) -> None:
        for diagnostic in diagnostics or ():
            self.add(diagnostic)

    def error(
        self,
        code: DiagnosticCode | str,
        message: str,
        span: SourceSpan | None = None,
        stage: Stage = Stage.INTERNAL,
        related: list[RelatedInformation] | None = None,
    ) -> Diagnostic:
        diagnostic = Diagnostic(
            message=message,
            code=code,
            severity=Severity.ERROR,
            span=span,
            stage=stage,
            related=tuple(related or ()),
        )
        self.add(diagnostic)
        return diagnostic

    def warn(
        self,
        code: DiagnosticCode | str,
        message: str,
        span: SourceSpan | None = None,
        stage: Stage = Stage.INTERNAL,
        related: list[RelatedInformation] | None = None,
    ) -> Diagnostic:
        diagnostic = Diagnostic(
            message=message,
            code=code,
            severity=Severity.WARNING,
            span=span,
            stage=stage,
            related=tuple(related or ()),
        )
        self.add(diagnostic)
        return diagnostic

    def info(
        self,
        code: DiagnosticCode | str,
        message: str,
        span: SourceSpan | None = None,
        stage: Stage = Stage.INTERNAL,
        related: list[RelatedInformation] | None = None,
    ) -> Diagnostic:
        diagnostic = Diagnostic(
            message=message,
            code=code,
            severity=Severity.INFO,
            span=span,
            stage=stage,
            related=tuple(related or ()),
        )
        self.add(diagnostic)
        return diagnostic

    def has_errors(self) -> bool:
        return any(d.severity is Severity.ERROR for d in self._items)

    def errors(self) -> list[Diagnostic]:
        return [d for d in self._items if d.severity is Severity.ERROR]

    def sorted(self) -> list[Diagnostic]:
        return sorted(
            self._items,
            key=lambda d: (
                -d.severity.rank,
                d.span.start_line if d.span else 0,
                d.span.start_col if d.span else 0,
                d.code_str,
            ),
        )

    def to_list(self) -> list[dict[str, object]]:
        return [d.to_dict() for d in self.sorted()]
