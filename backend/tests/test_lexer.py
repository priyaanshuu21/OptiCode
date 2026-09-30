"""Lexer unit tests: token kinds, maximal munch, spans, and error recovery."""

from __future__ import annotations

import pytest

from opticode.core.diagnostics import Severity
from opticode.lexer import INT_LITERAL_MAX, Lexer, TokenType


def scan(source: str):
    return Lexer(source).scan()


def types(source: str) -> list[TokenType]:
    tokens, _ = scan(source)
    return [t.type for t in tokens]


def lexemes(source: str) -> list[str]:
    tokens, _ = scan(source)
    return [t.lexeme for t in tokens]


def errors(source: str):
    _, diags = scan(source)
    return [d for d in diags if d.severity is Severity.ERROR]


# ------------------------------------------------------------- every token kind


def test_every_token_kind_is_produced():
    source = (
        "int float void if else while for return break continue printf scanf\n"
        "abc _x x1 42 3.14 \"hi\"\n"
        "+ - * / % = == != < <= > >= && || ! ++ -- += -= *= /= &\n"
        "( ) { } , ;"
    )
    tokens, diags = scan(source)
    assert not [d for d in diags if d.severity is Severity.ERROR]
    seen = {t.type for t in tokens}
    for expected in (
        TokenType.KW_INT,
        TokenType.KW_FLOAT,
        TokenType.KW_VOID,
        TokenType.KW_IF,
        TokenType.KW_ELSE,
        TokenType.KW_WHILE,
        TokenType.KW_FOR,
        TokenType.KW_RETURN,
        TokenType.KW_BREAK,
        TokenType.KW_CONTINUE,
        TokenType.KW_PRINTF,
        TokenType.KW_SCANF,
        TokenType.IDENT,
        TokenType.INT,
        TokenType.FLOAT,
        TokenType.STRING,
        TokenType.PLUS,
        TokenType.MINUS,
        TokenType.STAR,
        TokenType.SLASH,
        TokenType.PERCENT,
        TokenType.ASSIGN,
        TokenType.EQ,
        TokenType.NE,
        TokenType.LT,
        TokenType.LE,
        TokenType.GT,
        TokenType.GE,
        TokenType.ANDAND,
        TokenType.OROR,
        TokenType.BANG,
        TokenType.INCR,
        TokenType.DECR,
        TokenType.PLUS_ASSIGN,
        TokenType.MINUS_ASSIGN,
        TokenType.STAR_ASSIGN,
        TokenType.SLASH_ASSIGN,
        TokenType.AMP,
        TokenType.LPAREN,
        TokenType.RPAREN,
        TokenType.LBRACE,
        TokenType.RBRACE,
        TokenType.COMMA,
        TokenType.SEMI,
        TokenType.EOF,
    ):
        assert expected in seen, f"missing {expected}"
    assert lexemes(source)[:12] == [
        "int", "float", "void", "if", "else", "while", "for", "return", "break", "continue",
        "printf", "scanf",
    ]


# ----------------------------------------------------------------- maximal munch


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("<=", TokenType.LE),
        ("<", TokenType.LT),
        (">=", TokenType.GE),
        (">", TokenType.GT),
        ("==", TokenType.EQ),
        ("=", TokenType.ASSIGN),
        ("!=", TokenType.NE),
        ("!", TokenType.BANG),
        ("++", TokenType.INCR),
        ("+", TokenType.PLUS),
        ("--", TokenType.DECR),
        ("-", TokenType.MINUS),
        ("&&", TokenType.ANDAND),
        ("||", TokenType.OROR),
        ("+=", TokenType.PLUS_ASSIGN),
        ("*=", TokenType.STAR_ASSIGN),
        ("/=", TokenType.SLASH_ASSIGN),
        ("-=", TokenType.MINUS_ASSIGN),
    ],
)
def test_maximal_munch(source, expected):
    assert types(source)[0] is expected


def test_assignment_vs_equality_sequence():
    assert types("a=b==c") == [TokenType.IDENT, TokenType.ASSIGN, TokenType.IDENT, TokenType.EQ, TokenType.IDENT, TokenType.EOF]


def test_lone_ampersand_is_a_token():
    assert types("&") == [TokenType.AMP, TokenType.EOF]


def test_lone_pipe_is_an_error():
    found = errors("a | b")
    assert len(found) == 1
    assert found[0].code.value == "E-LEX-007"
    assert types("a | b")[1] is TokenType.IDENT


# ----------------------------------------------------- keyword/identifier boundary


@pytest.mark.parametrize("word", ["int", "int1", "integer", "iffy", "iff", "forx", "printfx", "_int"])
def test_keyword_versus_identifier(word: str):
    token = types(word)[0]
    if word in {"int", "printf"}:
        assert token.is_keyword
    else:
        assert token is TokenType.IDENT


# --------------------------------------------------------------------- literals


def test_int_and_float_values():
    tokens, diags = scan("0 7 2147483647 0.0 3.14159")
    assert not [d for d in diags if d.severity is Severity.ERROR]
    assert [t.value for t in tokens[:3]] == [0, 7, INT_LITERAL_MAX]
    assert [t.value for t in tokens[3:5]] == [0.0, pytest.approx(3.14159)]


def test_leading_dot_float():
    assert types(".5")[0] is TokenType.FLOAT


def test_int_literal_overflow_is_an_error():
    found = errors("2147483648")
    assert len(found) == 1
    assert found[0].code.value == "E-LEX-004"
    assert found[0].span is not None and found[0].span.start_line == 1


def test_float_with_no_fraction_digits_is_an_error():
    found = errors("1.")
    assert [d.code.value for d in found] == ["E-LEX-008"]


def test_string_value_is_decoded():
    tokens, diags = scan(r'"a\nb\tc\"d\\e"')
    assert not [d for d in diags if d.severity is Severity.ERROR]
    assert tokens[0].value == 'a\nb\tc"d\\e'
    assert tokens[0].lexeme == r'"a\nb\tc\"d\\e"'


def test_unterminated_string():
    found = errors('int a = "hello;\nint b;')
    assert [d.code.value for d in found] == ["E-LEX-001"]
    # scanning continues on the next line after the bad string
    assert lexemes('int a = "hello;\nint b;') == ["int", "a", "=", '"hello;', "int", "b", ";", ""]


def test_bad_escape_is_reported_but_recovered():
    found = errors(r'"\q"')
    assert [d.code.value for d in found] == ["E-LEX-003"]
    tokens, _ = scan(r'"\q"')
    assert tokens[0].value == "q"


# ----------------------------------------------------------------- comments


def test_line_comment_is_discarded():
    tokens, diags = scan("a // comment with ; { nonsense\nb")
    assert not [d for d in diags if d.severity is Severity.ERROR]
    assert lexemes("a // comment with ; { nonsense\nb") == ["a", "b", ""]


def test_block_comment_is_discarded():
    assert lexemes("a /* x\ny */ b") == ["a", "b", ""]


def test_unterminated_block_comment():
    found = errors("a /* never closed")
    assert [d.code.value for d in found] == ["E-LEX-002"]


def test_block_comments_do_not_nest():
    assert lexemes("/* /* nested */ x") == ["x", ""]


# ------------------------------------------------------------------- positions


def test_spans_across_lines_and_columns():
    tokens, _ = scan("int a;\n  int b;\n")
    first_int, a_tok, semi, second_int, b_tok, last_semi = tokens[:6]
    assert (first_int.span.start_line, first_int.span.start_col) == (1, 1)
    assert (a_tok.span.start_col, a_tok.span.end_col) == (5, 6)
    assert (semi.span.start_line, semi.span.start_col, semi.span.end_col) == (1, 6, 7)
    assert (second_int.span.start_line, second_int.span.start_col) == (2, 3)
    assert (b_tok.span.start_line, b_tok.span.start_col) == (2, 7)
    assert (last_semi.span.start_line, last_semi.span.start_col) == (2, 8)


def test_crlf_input_counts_as_one_newline():
    tokens, diags = scan("int a;\r\nint b;\r\n")
    assert not [d for d in diags if d.severity is Severity.ERROR]
    assert [t.span.start_line for t in tokens[:6]] == [1, 1, 1, 2, 2, 2]
    assert [t.span.start_col for t in tokens[:6]] == [1, 5, 6, 1, 5, 6]


def test_lone_carriage_return_is_a_newline():
    tokens, _ = scan("a\rb")
    assert [t.span.start_line for t in tokens[:2]] == [1, 2]

def test_tab_counts_as_one_column():
    tokens, _ = scan("\tint a;")
    assert tokens[0].span.start_col == 2


def test_eof_token_span_is_at_end():
    tokens, _ = scan("a")
    assert tokens[-1].type is TokenType.EOF
    assert (tokens[-1].span.start_line, tokens[-1].span.start_col) == (1, 2)


# --------------------------------------------------------------- bad characters


def test_include_gives_a_friendly_error():
    found = errors('#include <stdio.h>\nint main() { return 0; }')
    assert len(found) == 1
    assert found[0].code.value == "E-LEX-006"
    assert "printf" in found[0].message
    assert "scanf" in found[0].message


def test_unknown_character_is_reported():
    found = errors("int a = 1 $ 2;")
    assert [d.code.value for d in found] == ["E-LEX-005"]


def test_multiple_errors_in_one_file():
    found = errors("int a = 1 $ 2;\nint b = 3 ` 4;\n#include <x>\n|")
    codes = [d.code.value for d in found]
    assert codes.count("E-LEX-005") == 2
    assert "E-LEX-006" in codes
    assert "E-LEX-007" in codes
    lines = [d.span.start_line for d in found]
    assert lines == sorted(lines)


# ----------------------------------------------------------------- empty inputs


def test_empty_source_yields_only_eof():
    tokens, diags = scan("")
    assert [t.type for t in tokens] == [TokenType.EOF]
    assert not [d for d in diags if d.severity is Severity.ERROR]


def test_whitespace_only_source():
    tokens, diags = scan("   \n\t\r\n  ")
    assert [t.type for t in tokens] == [TokenType.EOF]
    assert not [d for d in diags if d.severity is Severity.ERROR]


def test_eof_is_emitted_exactly_once():
    tokens, _ = scan("int a;")
    assert [t.type for t in tokens].count(TokenType.EOF) == 1


def test_lexer_never_hangs_on_pathological_input():
    tokens, diags = scan("/*" + "a" * 5000 + " $" * 100)
    assert tokens[-1].type is TokenType.EOF
    assert any(d.severity is Severity.ERROR for d in diags)
