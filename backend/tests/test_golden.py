"""Golden TAC dumps.

Optimizations rewrite TAC, and the optimizer's promise is that it *explains itself* by
pointing back at source. These files lock down the exact instruction sequence produced
today, so any change to lowering is a deliberate, reviewable diff rather than a silent
shift in output that later passes were tuned against.

Regenerate after an intentional lowering change with::

    python -m tests.regenerate_golden
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opticode.ir import lower, print_program
from opticode.parser import parse
from opticode.semantic import analyze

GOLDEN_DIR = Path(__file__).resolve().parent / "golden"

CASES = {
    "return": "int f() { return 1; }",
    "locals": "int f() { int a = 1; int b = 2; return a + b; }",
    "promotion": "float f(int a, float b) { return a + b; }",
    "narrowing": "int f(float a) { int b = a; return b; }",
    "if_else": "int f(int a) { if (a) { return 1; } else { return 2; } }",
    "if_no_else": "int f(int a) { if (a) { return 1; } return 0; }",
    "while": "int f(int n) { int i = 0; while (i < n) { i = i + 1; } return i; }",
    "for": (
        "int f(int n) { int s = 0;"
        " for (int i = 0; i < n; i = i + 1) { s = s + i; } return s; }"
    ),
    "break_continue": (
        "int f(int n) { int s = 0;"
        " for (int i = 0; i < n; i = i + 1) {"
        " if (i == 1) { continue; }"
        " if (i == 7) { break; }"
        " s = s + i; } return s; }"
    ),
    "logical_and": "int f(int a, int b) { return a && b; }",
    "logical_or": "int f(int a, int b) { return a || b; }",
    "logical_nested": "int f(int a, int b, int c) { return a && (b || c); }",
    "call": "int add(int a, int b) { return a + b; } int f(int a) { return add(a, 2); }",
    "void_call": "void g() { } int f() { g(); return 0; }",
    "printf": 'int f() { printf("%d %f\\n", 1, 2.0); return 0; }',
    "scanf": 'int f() { int a; float b; scanf("%d %f", &a, &b); return 0; }',
    "sugar": "int f() { int a = 1; a += 2; a++; return a; }",
    "recursion": "int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }",
    "unary": "int f(int a) { return -a; }",
    "trap": "int f(int a, int b) { int c = a / b; int d = a % b; return c + d; }",
}


def dump(source: str) -> str:
    program, _ = parse(source)
    result = analyze(program)
    assert result.ok, [d.message for d in result.diagnostics]
    lowered = lower(result)
    assert lowered.ok, lowered.errors
    return print_program(lowered.program)


def golden_path(name: str) -> Path:
    return GOLDEN_DIR / f"{name}.tac"


@pytest.mark.parametrize("name", sorted(CASES), ids=sorted(CASES))
def test_tac_matches_golden(name: str):
    expected_path = golden_path(name)
    if not expected_path.exists():
        pytest.skip("no golden file yet; run python -m tests.regenerate_golden")
    # Universal newlines so a CRLF checkout compares equal to LF-generated output.
    expected = expected_path.read_text(encoding="utf-8").strip()
    assert dump(CASES[name]).strip() == expected


def test_goldens_are_stored_with_unix_line_endings():
    """CRLF in the goldens would make every comparison depend on the checkout style."""
    for path in GOLDEN_DIR.glob("*.tac"):
        raw = path.read_bytes()
        assert b"\r\n" not in raw, f"{path.name} contains CRLF"


def test_golden_directory_is_not_empty():
    """A missing golden set would turn every case above into a skip."""
    existing = list(GOLDEN_DIR.glob("*.tac"))
    assert len(existing) >= len(CASES)


def test_every_golden_file_is_referenced_by_a_case():
    """No orphaned goldens, so a renamed case cannot leave a stale file behind."""
    referenced = {golden_path(name).name for name in CASES}
    for path in GOLDEN_DIR.glob("*.tac"):
        assert path.name in referenced, f"{path.name} is not used by any case"


def test_golden_dumps_are_stable():
    assert dump(CASES["while"]) == dump(CASES["while"])
