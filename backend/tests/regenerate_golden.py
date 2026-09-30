"""Regenerate the TAC golden files.

Run deliberately, never as part of the test suite::

    python -m tests.regenerate_golden

The diff that follows is the review record of every lowering change.
"""

from __future__ import annotations

from pathlib import Path

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


def main() -> None:
    GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
    for name, source in sorted(CASES.items()):
        program, _ = parse(source)
        result = analyze(program)
        if not result.ok:
            raise SystemExit(f"{name}: {result.diagnostics}")
        lowered = lower(result)
        if not lowered.ok:
            raise SystemExit(f"{name}: {lowered.errors}")
        path = GOLDEN_DIR / f"{name}.tac"
        # newline="" keeps the bytes exactly as printed, so the checked-in goldens do not
        # pick up CRLF on Windows and differ from what git stores.
        with path.open("w", encoding="utf-8", newline="") as handle:
            handle.write(print_program(lowered.program) + "\n")
        print(f"wrote {path.name}")


if __name__ == "__main__":
    main()
