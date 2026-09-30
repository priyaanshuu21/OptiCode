"""The MiniC token model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ..core.spans import SourceSpan

__all__ = ["TokenType", "Token", "KEYWORDS"]


class TokenType(StrEnum):
    """Every lexical category MiniC recognises."""

    # Literals / names
    IDENT = "ident"
    INT = "int"
    FLOAT = "float"
    STRING = "string"

    # Keywords (reserved)
    KW_INT = "int_kw"
    KW_FLOAT = "float_kw"
    KW_VOID = "void_kw"
    KW_IF = "if_kw"
    KW_ELSE = "else_kw"
    KW_WHILE = "while_kw"
    KW_FOR = "for_kw"
    KW_RETURN = "return_kw"
    KW_BREAK = "break_kw"
    KW_CONTINUE = "continue_kw"
    KW_PRINTF = "printf_kw"
    KW_SCANF = "scanf_kw"

    # Operators and punctuation
    PLUS = "+"
    MINUS = "-"
    STAR = "*"
    SLASH = "/"
    PERCENT = "%"
    ASSIGN = "="
    EQ = "=="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="
    ANDAND = "&&"
    OROR = "||"
    BANG = "!"
    INCR = "++"
    DECR = "--"
    PLUS_ASSIGN = "+="
    MINUS_ASSIGN = "-="
    STAR_ASSIGN = "*="
    SLASH_ASSIGN = "/="
    AMP = "&"

    LPAREN = "("
    RPAREN = ")"
    LBRACE = "{"
    RBRACE = "}"
    COMMA = ","
    SEMI = ";"

    EOF = "eof"

    @property
    def is_keyword(self) -> bool:
        return self.value.endswith("_kw")

    @property
    def category(self) -> str:
        """A coarse grouping used for colouring in the editor."""
        if self.is_keyword:
            return "keyword"
        if self in (TokenType.IDENT,):
            return "identifier"
        if self in (TokenType.INT, TokenType.FLOAT, TokenType.STRING):
            return "literal"
        if self is TokenType.EOF:
            return "eof"
        return "punctuation"


KEYWORDS: dict[str, TokenType] = {
    "int": TokenType.KW_INT,
    "float": TokenType.KW_FLOAT,
    "void": TokenType.KW_VOID,
    "if": TokenType.KW_IF,
    "else": TokenType.KW_ELSE,
    "while": TokenType.KW_WHILE,
    "for": TokenType.KW_FOR,
    "return": TokenType.KW_RETURN,
    "break": TokenType.KW_BREAK,
    "continue": TokenType.KW_CONTINUE,
    "printf": TokenType.KW_PRINTF,
    "scanf": TokenType.KW_SCANF,
}


@dataclass(frozen=True, slots=True)
class Token:
    type: TokenType
    lexeme: str
    value: object
    span: SourceSpan

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "type": self.type.value,
            "category": self.type.category,
            "lexeme": self.lexeme,
            "value": self.value,
            "span": self.span.to_dict(),
        }
        return payload

    def describe(self) -> str:
        """A short human description used in parser error messages."""
        if self.type is TokenType.EOF:
            return "end of input"
        return f"'{self.lexeme}'"

    def __str__(self) -> str:  # pragma: no cover - debugging aid
        return f"{self.type.value}({self.lexeme!r})"
