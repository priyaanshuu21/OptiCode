"""The MiniC lexer.

A hand-written scanner is equivalent to a DFA: it consumes the longest valid token at
each position (*maximal munch*), so ``<=`` is one token rather than ``<`` followed by
``=``. Keywords are recognised only after an identifier has been scanned, which keeps
``int1`` and ``integer`` identifiers while ``int`` is reserved.

Lexical errors never stop the scan. The offending input is reported and skipped so a
single file can produce several located diagnostics.
"""

from __future__ import annotations

from ..core.diagnostics import DiagnosticBag, DiagnosticCode, Stage
from ..core.spans import SourceSpan
from .tokens import KEYWORDS, Token, TokenType

__all__ = ["Lexer", "tokenize", "INT_LITERAL_MAX"]

INT_LITERAL_MAX = 2147483647

# Longest-match table for two-character operators, checked before the one-character set.
_TWO_CHAR: dict[str, TokenType] = {
    "==": TokenType.EQ,
    "!=": TokenType.NE,
    "<=": TokenType.LE,
    ">=": TokenType.GE,
    "&&": TokenType.ANDAND,
    "||": TokenType.OROR,
    "++": TokenType.INCR,
    "--": TokenType.DECR,
    "+=": TokenType.PLUS_ASSIGN,
    "-=": TokenType.MINUS_ASSIGN,
    "*=": TokenType.STAR_ASSIGN,
    "/=": TokenType.SLASH_ASSIGN,
}
_ONE_CHAR: dict[str, TokenType] = {
    "+": TokenType.PLUS,
    "-": TokenType.MINUS,
    "*": TokenType.STAR,
    "/": TokenType.SLASH,
    "%": TokenType.PERCENT,
    "=": TokenType.ASSIGN,
    "<": TokenType.LT,
    ">": TokenType.GT,
    "!": TokenType.BANG,
    "&": TokenType.AMP,
    "(": TokenType.LPAREN,
    ")": TokenType.RPAREN,
    "{": TokenType.LBRACE,
    "}": TokenType.RBRACE,
    ",": TokenType.COMMA,
    ";": TokenType.SEMI,
}

_STRING_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


class Lexer:
    """Scans MiniC source text into a token list plus diagnostics."""

    def __init__(self, source: str) -> None:
        self.src = source
        self.length = len(source)
        self.pos = 0
        self.line = 1
        self.col = 1
        self.diagnostics = DiagnosticBag()
        self._tokens: list[Token] = []

    # ---------------------------------------------------------------- scanning

    def scan(self) -> tuple[list[Token], list]:
        """Return every token, ending with a single EOF token."""
        while self.pos < self.length:
            if self._skip_trivia():
                continue
            self._scan_token()
        self._tokens.append(
            Token(
                type=TokenType.EOF,
                lexeme="",
                value=None,
                span=SourceSpan.at(self.line, self.col),
            )
        )
        return self._tokens, self.diagnostics.sorted()

    # ------------------------------------------------------------- positioning

    def _peek(self, offset: int = 0) -> str:
        index = self.pos + offset
        if index < self.length:
            return self.src[index]
        return ""

    def _advance(self) -> str:
        char = self.src[self.pos]
        self.pos += 1
        if char == "\n":
            self.line += 1
            self.col = 1
        elif char == "\r":
            if self.pos < self.length and self.src[self.pos] == "\n":
                self.pos += 1
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return char

    def _span_from(self, line: int, col: int) -> SourceSpan:
        return SourceSpan(line, col, self.line, self.col)

    def _at(self) -> SourceSpan:
        return SourceSpan.at(self.line, self.col)

    # ----------------------------------------------------------------- trivia

    def _skip_trivia(self) -> bool:
        """Consume whitespace and comments. Returns True when something was skipped."""
        skipped = False
        while self.pos < self.length:
            char = self._peek()
            if char in " \t\r\n\f\v":
                self._advance()
                skipped = True
                continue
            if char == "/" and self._peek(1) == "/":
                while self.pos < self.length and self._peek() != "\n" and self._peek() != "\r":
                    self._advance()
                skipped = True
                continue
            if char == "/" and self._peek(1) == "*":
                self._skip_block_comment()
                skipped = True
                continue
            break
        return skipped

    def _skip_block_comment(self) -> None:
        start_line, start_col = self.line, self.col
        self._advance()  # '/'
        self._advance()  # '*'
        while self.pos < self.length:
            if self._peek() == "*" and self._peek(1) == "/":
                self._advance()
                self._advance()
                return
            self._advance()
        self.diagnostics.error(
            DiagnosticCode.E101_UNTERMINATED_COMMENT,
            "unterminated block comment: expected a closing '*/'",
            span=SourceSpan(start_line, start_col, self.line, self.col),
            stage=Stage.LEXER,
        )

    # ----------------------------------------------------------------- tokens

    def _emit(self, type_: TokenType, lexeme: str, value: object, line: int, col: int) -> None:
        self._tokens.append(Token(type_, lexeme, value, self._span_from(line, col)))

    def _scan_token(self) -> None:
        line, col = self.line, self.col
        char = self._peek()

        if char == "#":
            self._scan_preprocessor(line, col)
            return
        if char.isdigit() or (char == "." and self._peek(1).isdigit()):
            self._scan_number(line, col)
            return
        if char == '"':
            self._scan_string(line, col)
            return
        if char.isalpha() or char == "_":
            self._scan_identifier(line, col)
            return

        two = self._peek(0) + self._peek(1)
        if two in _TWO_CHAR:
            self._advance()
            self._advance()
            self._emit(_TWO_CHAR[two], two, None, line, col)
            return
        if char == "|":
            self._advance()
            self.diagnostics.error(
                DiagnosticCode.E106_LONE_PIPE,
                "unexpected character '|': MiniC uses '||' for logical or",
                span=SourceSpan(line, col, self.line, self.col),
                stage=Stage.LEXER,
            )
            return
        if char in _ONE_CHAR:
            self._advance()
            self._emit(_ONE_CHAR[char], char, None, line, col)
            return

        self._advance()
        self.diagnostics.error(
            DiagnosticCode.E104_UNEXPECTED_CHARACTER,
            f"unexpected character {char!r} in source",
            span=SourceSpan(line, col, self.line, self.col),
            stage=Stage.LEXER,
        )

    def _scan_preprocessor(self, line: int, col: int) -> None:
        """Report a friendly error for ``#include`` and friends."""
        start = self.pos
        while self.pos < self.length and self._peek() not in "\r\n":
            self._advance()
        directive = self.src[start : self.pos].strip()
        span = SourceSpan(line, col, self.line, self.col)
        if directive.startswith("#include") or directive == "#":
            message = (
                "preprocessor directives are not supported; MiniC has no headers. "
                "printf and scanf are built in and need no #include."
            )
        else:
            message = (
                f"preprocessor directive {directive!r} is not supported; "
                "MiniC has no preprocessor"
            )
        self.diagnostics.error(DiagnosticCode.E105_PREPROCESSOR, message, span=span, stage=Stage.LEXER)

    def _scan_number(self, line: int, col: int) -> None:
        """Scan an INT or FLOAT literal (decimal only, no sign, no exponent)."""
        digits: list[str] = []
        while self.pos < self.length and self._peek().isdigit():
            digits.append(self._advance())

        is_float = False
        if self._peek() == ".":
            is_float = True
            digits.append(self._advance())
            fraction: list[str] = []
            while self.pos < self.length and self._peek().isdigit():
                fraction.append(self._advance())
            if not fraction:
                self.diagnostics.error(
                    DiagnosticCode.E107_FLOAT_LITERAL,
                    "malformed floating point literal: expected digits after '.'",
                    span=SourceSpan(line, col, self.line, self.col),
                    stage=Stage.LEXER,
                )
                self._emit(TokenType.FLOAT, "".join(digits), float("".join(digits) or "0"), line, col)
                return
            digits.extend(fraction)

        text = "".join(digits)
        if is_float:
            self._emit(TokenType.FLOAT, text, float(text), line, col)
            return

        value = int(text)
        if value > INT_LITERAL_MAX:
            self.diagnostics.error(
                DiagnosticCode.E103_INT_LITERAL_RANGE,
                f"integer literal {text} is out of range; "
                f"the maximum is {INT_LITERAL_MAX} (negate instead of writing a larger literal)",
                span=SourceSpan(line, col, self.line, self.col),
                stage=Stage.LEXER,
            )
            value = INT_LITERAL_MAX
        self._emit(TokenType.INT, text, value, line, col)

    def _scan_string(self, line: int, col: int) -> None:
        self._advance()  # opening quote
        raw: list[str] = []
        decoded: list[str] = []
        terminated = False
        while self.pos < self.length:
            char = self._peek()
            if char in "\r\n":
                break
            if char == '"':
                self._advance()
                terminated = True
                break
            if char == "\\":
                esc_line, esc_col = self.line, self.col
                self._advance()
                if self.pos >= self.length:
                    break
                esc_char = self._advance()
                raw.append("\\" + esc_char)
                if esc_char in _STRING_ESCAPES:
                    decoded.append(_STRING_ESCAPES[esc_char])
                else:
                    decoded.append(esc_char)
                    shown = {"r": "\\r", "0": "\\0"}.get(esc_char, f"\\{esc_char}")
                    self.diagnostics.error(
                        DiagnosticCode.E102_BAD_ESCAPE,
                        f"unsupported escape sequence {shown} in string literal; "
                        "MiniC supports \\n, \\t, \\\" and \\\\",
                        span=self._span_from(esc_line, esc_col),
                        stage=Stage.LEXER,
                    )
                continue
            self._advance()
            raw.append(char)
            decoded.append(char)

        span = SourceSpan(line, col, self.line, self.col)
        lexeme = '"' + "".join(raw) + ('"' if terminated else "")
        if not terminated:
            self.diagnostics.error(
                DiagnosticCode.E100_UNTERMINATED_STRING,
                "unterminated string literal: expected a closing '\"' before end of line",
                span=span,
                stage=Stage.LEXER,
            )
        self._emit(TokenType.STRING, lexeme, "".join(decoded), line, col)

    def _scan_identifier(self, line: int, col: int) -> None:
        chars: list[str] = []
        while self.pos < self.length:
            char = self._peek()
            if char.isalnum() or char == "_":
                chars.append(self._advance())
            else:
                break
        lexeme = "".join(chars)
        if lexeme in KEYWORDS:
            self._emit(KEYWORDS[lexeme], lexeme, lexeme, line, col)
        else:
            self._emit(TokenType.IDENT, lexeme, lexeme, line, col)


def tokenize(source: str) -> tuple[list[Token], DiagnosticBag]:
    """Convenience wrapper returning the token list and a diagnostic bag."""
    lexer = Lexer(source)
    tokens, diagnostics = lexer.scan()
    return tokens, DiagnosticBag(diagnostics)
