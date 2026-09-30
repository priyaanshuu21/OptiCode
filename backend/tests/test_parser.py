"""Parser, AST, and unparser tests."""

from __future__ import annotations

import random

import pytest

from opticode.core.diagnostics import Severity
from opticode.parser import (
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
    Param,
    Printf,
    Program,
    Return,
    Scanf,
    StringLiteral,
    UnaryExpr,
    VarDecl,
    While,
    node_by_id,
    parse,
    serialize_program,
    unparse,
    walk,
)


def parse_ok(source: str) -> Program:
    program, diags = parse(source)
    errors = [d for d in diags if d.severity is Severity.ERROR]
    assert not errors, [d.message for d in errors]
    assert program is not None
    return program


def parse_err(source: str):
    program, diags = parse(source)
    return program, [d for d in diags if d.severity is Severity.ERROR]


def body_of(program: Program) -> Block:
    return program.functions[0].body


def stmts(program: Program) -> list:
    return list(body_of(program).statements)


def shape(node) -> str:
    """Fully parenthesized structural dump.

    ``expression_text`` prints the canonical source form, which adds only the
    parentheses the precedence rules require. ``shape`` always prints every grouping so
    associativity and nesting are visible in an assertion.
    """
    if isinstance(node, BinaryExpr):
        return f"({shape(node.left)} {node.op} {shape(node.right)})"
    if isinstance(node, UnaryExpr):
        return f"({node.op}{shape(node.operand)})"
    if isinstance(node, Identifier):
        return node.name
    if isinstance(node, IntLiteral):
        return str(node.value)
    if isinstance(node, FloatLiteral):
        return repr(node.value)
    if isinstance(node, StringLiteral):
        return node.value
    if isinstance(node, CallExpr):
        return f"{shape(node.callee)}({', '.join(shape(a) for a in node.arguments)})"
    raise AssertionError(f"no shape rule for {type(node).__name__}")


# ------------------------------------------------------------------- functions


def test_function_with_params():
    program = parse_ok("float scale(int n, float f) { return f; }")
    function = program.functions[0]
    assert isinstance(function, FunctionDecl)
    assert function.name == "scale"
    assert function.return_type == "float"
    assert [p.name for p in function.params] == ["n", "f"]
    assert [p.type_name for p in function.params] == ["int", "float"]
    assert isinstance(function.params[0], Param)


def test_void_return_type_is_allowed():
    program = parse_ok("void nothing() { return; }")
    assert program.functions[0].return_type == "void"
    assert isinstance(stmts(program)[0], Return)
    assert stmts(program)[0].value is None


def test_multiple_functions():
    program = parse_ok("int a() { return 1; } float b() { return 1.0; }")
    assert [f.name for f in program.functions] == ["a", "b"]


# ---------------------------------------------------------------- declarations


def test_declaration_with_and_without_initializer():
    statements = stmts(parse_ok("int f() { int a = 1, b; return a; }"))
    a, b = statements[0], statements[1]
    assert isinstance(a, VarDecl) and a.name == "a" and a.init is not None
    assert isinstance(b, VarDecl) and b.name == "b" and b.init is None
    assert a.type_name == "int" and b.type_name == "int"


def test_multi_declarator_declarations_are_split_with_own_spans():
    program = parse_ok("int f() {\n    int a = 1, b = 2, c;\n    return a;\n}")
    a, b, c, _ = stmts(program)
    assert (a.name, b.name, c.name) == ("a", "b", "c")
    assert a.span.start_line == 2 and b.span.start_line == 2
    assert b.span.start_col > a.span.start_col
    assert c.span.start_col > b.span.start_col


def test_float_declaration():
    declaration = stmts(parse_ok("float f() { float x = 1.5; return x; }"))[0]
    assert isinstance(declaration, VarDecl)
    assert declaration.type_name == "float"
    assert isinstance(declaration.init, FloatLiteral)


# ---------------------------------------------------------------- assignments


def test_simple_assignment():
    statement = stmts(parse_ok("int f() { int x; x = 5; return x; }"))[1]
    assert isinstance(statement, Assign)
    assert statement.target == "x"
    assert statement.sugar is None
    assert isinstance(statement.value, IntLiteral)


@pytest.mark.parametrize(
    ("sugar", "expected_op"),
    [("+=", "+"), ("-=", "-"), ("*=", "*"), ("/=", "/")],
)
def test_compound_assignment_is_desugared(sugar, expected_op):
    statement = stmts(parse_ok(f"int f() {{ int x = 1, y = 2; x {sugar} y; return x; }}"))[2]
    assert isinstance(statement, Assign)
    assert statement.sugar == sugar
    assert statement.op == expected_op
    assert isinstance(statement.value, BinaryExpr)
    assert statement.value.op == expected_op
    assert isinstance(statement.value.left, Identifier)
    assert statement.value.left.name == "x"
    assert statement.value.right.span.start_col > 0


@pytest.mark.parametrize(("sugar", "op"), [("++", "+"), ("--", "-")])
def test_increment_is_desugared(sugar, op):
    """``x--`` desugars to ``x = x - 1``; the operator must match the written sugar."""
    statement = stmts(parse_ok(f"int f() {{ int x = 1; x{sugar}; return x; }}"))[1]
    assert isinstance(statement, Assign)
    assert statement.sugar == sugar
    assert statement.op == op
    assert isinstance(statement.value, BinaryExpr)
    assert statement.value.op == op
    assert isinstance(statement.value.left, Identifier)
    assert isinstance(statement.value.right, IntLiteral)
    assert statement.value.right.value == 1


def test_call_statement():
    statement = stmts(parse_ok("int f() { g(); return 0; }"))[0]
    assert isinstance(statement, ExprStmt)
    assert isinstance(statement.expr, CallExpr)
    assert statement.expr.callee == "g"


# -------------------------------------------------------------- control flow


def test_if_else():
    statement = stmts(parse_ok("int f() { if (1) g(); else h(); return 0; }"))[0]
    assert isinstance(statement, If)
    assert statement.else_branch is not None
    assert isinstance(statement.then_branch, ExprStmt)
    assert isinstance(statement.else_branch, ExprStmt)


def test_if_without_else():
    statement = stmts(parse_ok("int f() { if (1) g(); return 0; }"))[0]
    assert isinstance(statement, If)
    assert statement.else_branch is None


def test_dangling_else_binds_to_nearest_if():
    program = parse_ok("int f() {\n  if (1)\n    if (2)\n      g();\n    else\n      h();\n  return 0;\n}")
    outer = stmts(program)[0]
    assert isinstance(outer, If)
    assert outer.else_branch is None
    assert isinstance(outer.then_branch, If)
    assert isinstance(outer.then_branch.else_branch, ExprStmt)
    assert outer.then_branch.else_branch.expr.callee == "h"


def test_while_and_break_continue():
    program = parse_ok("int f() { while (1) { break; } return 0; }")
    loop = stmts(program)[0]
    assert isinstance(loop, While)
    assert isinstance(loop.body, Block)
    assert isinstance(loop.body.statements[0], Break)

    program = parse_ok("int f() { while (1) { continue; } return 0; }")
    assert isinstance(stmts(program)[0].body.statements[0], Continue)


def test_for_with_all_parts():
    program = parse_ok("int f() { for (int i = 0; i < 3; i++) g(i); return 0; }")
    loop = stmts(program)[0]
    assert isinstance(loop, For)
    assert isinstance(loop.init, VarDecl)
    assert isinstance(loop.condition, BinaryExpr)
    assert isinstance(loop.update, Assign)
    assert isinstance(loop.body, ExprStmt)


@pytest.mark.parametrize(
    "header",
    ["for (;;) g();", "for (; i < 3;) g();", "for (i = 0; ; i++) g();", "for (i = 0; i < 3;) g();"],
)
def test_for_with_omitted_parts(header):
    loop = stmts(parse_ok(f"int f() {{ int i = 0; {header} return 0; }}"))[1]
    assert isinstance(loop, For)
    assert isinstance(loop.body, ExprStmt)


def test_nested_loops():
    program = parse_ok(
        "int f() {\n"
        "  for (int i = 0; i < 3; i++) {\n"
        "    for (int j = 0; j < 3; j++) {\n"
        "      if (i == j) break;\n"
        "      continue;\n"
        "    }\n"
        "  }\n"
        "  return 0;\n"
        "}"
    )
    outer = stmts(program)[0]
    assert isinstance(outer, For)
    assert isinstance(outer.body, Block)
    inner = outer.body.statements[0]
    assert isinstance(inner, For)
    assert isinstance(inner.body, Block)
    assert isinstance(inner.body.statements[0], If)


def test_while_with_nested_block_as_body():
    loop = stmts(parse_ok("int f() { while (1) { int y = 0; } return 0; }"))[0]
    assert isinstance(loop, While)
    assert isinstance(loop.body, Block)


# ------------------------------------------------------------- printf / scanf


def test_printf():
    statement = stmts(parse_ok('int f() { printf("a=%d b=%f\\n", 1, 2.0); return 0; }'))[0]
    assert isinstance(statement, Printf)
    assert statement.format == "a=%d b=%f\n"
    assert len(statement.arguments) == 2
    assert isinstance(statement.arguments[0], IntLiteral)
    assert isinstance(statement.arguments[1], FloatLiteral)


def test_printf_without_arguments():
    statement = stmts(parse_ok('int f() { printf("hi"); return 0; }'))[0]
    assert isinstance(statement, Printf)
    assert statement.arguments == ()


def test_scanf():
    statement = stmts(parse_ok('int f() { int a; float b; scanf("%d %f", &a, &b); return 0; }'))[2]
    assert isinstance(statement, Scanf)
    assert statement.format == "%d %f"
    assert statement.arguments == ("a", "b")
    assert len(statement.argument_spans) == 2


# --------------------------------------------------------------- expressions


def test_precedence_and_associativity():
    node = stmts(parse_ok("int f() { return 1 + 2 * 3; }"))[0].value
    assert isinstance(node, BinaryExpr) and node.op == "+"
    assert isinstance(node.right, BinaryExpr) and node.right.op == "*"

    # `*` binds tighter, so the root is `+` and the product is on its left.
    node = stmts(parse_ok("int f() { return 1 * 2 + 3; }"))[0].value
    assert node.op == "+"
    assert isinstance(node.left, BinaryExpr) and node.left.op == "*"

    # Subtraction is left-associative: `(a - b) - c`, not `a - (b - c)`.
    assert shape(stmts(parse_ok("int f() { int a, b, c; return a - b - c; }"))[-1].value) == (
        "((a - b) - c)"
    )
    assert shape(stmts(parse_ok("int f() { int a, b, c; return a - (b - c); }"))[-1].value) == (
        "(a - (b - c))"
    )


@pytest.mark.parametrize(
    ("source", "shape_string"),
    [
        ("a - b - c", "((a - b) - c)"),
        ("a - (b - c)", "(a - (b - c))"),
        ("a + b * c - d", "((a + (b * c)) - d)"),
        ("!a && b || c", "(((!a) && b) || c)"),
        ("a || b && c", "(a || (b && c))"),
        # MiniC ranks the relational operators above the equality operators, so this
        # groups as `a == (b < c)` rather than `(a == b) < c`.
        ("a == b < c", "(a == (b < c))"),
        ("a < b == c", "((a < b) == c)"),
        ("-a * b", "((-a) * b)"),
        ("-(a * b)", "(-(a * b))"),
        ("!a == b", "((!a) == b)"),
        ("a % b * c", "((a % b) * c)"),
    ],
)
def test_precedence_shapes(source, shape_string):
    program = parse_ok("int f() { int a, b, c, d; return " + source + "; }")
    returned = stmts(program)[-1]
    assert isinstance(returned, Return)
    assert shape(returned.value) == shape_string


def test_unary_operators():
    returned = stmts(parse_ok("int f() { return -5; }"))[0]
    assert isinstance(returned, Return)
    node = returned.value
    assert isinstance(node, UnaryExpr) and node.op == "-"
    assert isinstance(node.operand, IntLiteral) and node.operand.value == 5
    returned = stmts(parse_ok("int f() { int a; return !a; }"))[1]
    assert isinstance(returned.value, UnaryExpr) and returned.value.op == "!"


def test_literals_and_identifiers():
    node = stmts(parse_ok("int f() { return 3.25; }"))[0].value
    assert isinstance(node, FloatLiteral) and node.value == 3.25
    node = stmts(parse_ok('int f() { return "s"; }'))[0].value
    assert isinstance(node, StringLiteral) and node.value == "s"
    node = stmts(parse_ok("int f() { int q; return q; }"))[1].value
    assert isinstance(node, Identifier) and node.name == "q"


def test_nested_calls():
    node = stmts(parse_ok("int f() { return g(h(1), 2); }"))[0].value
    assert isinstance(node, CallExpr)
    assert node.callee == "g"
    assert isinstance(node.arguments[0], CallExpr)
    assert node.arguments[0].callee == "h"


# ----------------------------------------------------------------------- spans


def test_spans_are_nested_correctly():
    program = parse_ok("int f() {\n    int x = 1 + 2;\n    return x;\n}")
    declaration = stmts(program)[0]
    assert declaration.span.start_line == 2
    assert declaration.init.span.start_col == 13
    assert declaration.span.contains(declaration.init.span)
    assert declaration.init.span.contains(declaration.init.right.span)
    for node in walk(program):
        for _, child in _children(node):
            assert node.span.contains(child.span), f"{node.id} does not contain {child.id}"


def _children(node):
    from opticode.parser.ast import _children_of

    return _children_of(node)


def test_function_span_covers_body():
    program = parse_ok("int f() {\n  return 0;\n}")
    assert program.functions[0].span.start_line == 1
    assert program.functions[0].span.end_line == 3


# ------------------------------------------------------------------------- ids


def test_ids_are_unique_and_deterministic():
    source = "int f(int a) { int b = a; return b; }"
    first = parse_ok(source)
    second = parse_ok(source)
    ids = [n.id for n in walk(first)]
    assert len(ids) == len(set(ids))
    assert [n.id for n in walk(second)] == ids
    assert ids[0] == "n1"
    assert ids == [f"n{i + 1}" for i in range(len(ids))]


def test_ids_follow_preorder():
    program = parse_ok("int f() { return 1; }")
    kinds = [(n.id, type(n).__name__) for n in walk(program)]
    assert kinds == [
        ("n1", "Program"),
        ("n2", "FunctionDecl"),
        ("n3", "Block"),
        ("n4", "Return"),
        ("n5", "IntLiteral"),
    ]
    assert node_by_id(program, "n5") is not None
    assert node_by_id(program, "n99") is None


def test_desugared_nodes_also_get_ids():
    program = parse_ok("int f() { int a = 1; a++; return a; }")
    kinds = [type(n).__name__ for n in walk(program)]
    # `a++` is stored as `a = a + 1`, so the tree is
    #   Program, FunctionDecl, Block, VarDecl, IntLiteral, Assign, BinaryExpr,
    #   Identifier, IntLiteral, Return, Identifier
    # The desugared target is a plain name on `Assign`, not an Identifier node, so there
    # are two Identifier nodes: the `a` inside `a + 1` and the one being returned.
    assert kinds.count("BinaryExpr") == 1
    assert kinds.count("Identifier") == 2
    assert kinds.count("Assign") == 1
    assert len([n.id for n in walk(program)]) == len(list(walk(program)))


# ---------------------------------------------------------------- serialization


def test_serialization_is_generic():
    payload = serialize_program(parse_ok("int f() { int x = 1; return x; }"))
    assert payload["kind"] == "Program"
    function = payload["children"][0]["node"]
    assert function["kind"] == "FunctionDecl"
    assert function["attrs"]["name"] == "f"
    assert function["attrs"]["return_type"] == "int"
    assert [c["role"] for c in function["children"]] == ["body"]
    body = function["children"][0]
    assert body["role"] == "body"
    decl = body["node"]["children"][0]["node"]
    assert decl["kind"] == "VarDecl"
    assert decl["attrs"] == {"name": "x", "type_name": "int"}
    assert decl["children"][0]["role"] == "init"
    assert decl["children"][0]["node"]["kind"] == "IntLiteral"
    assert decl["children"][0]["node"]["attrs"]["value"] == 1
    assert set(decl["span"]) == {"start_line", "start_col", "end_line", "end_col"}


def test_serialization_includes_params():
    payload = serialize_program(parse_ok("int f(int a) { return a; }"))
    function = payload["children"][0]["node"]
    assert [c["role"] for c in function["children"]] == ["param", "body"]
    assert function["children"][0]["node"]["attrs"] == {"name": "a", "type_name": "int"}


def test_serialization_children_follows_traversal_order():
    payload = serialize_program(parse_ok("int f() { if (1) return 2; return 3; }"))
    function = payload["children"][0]["node"]
    node = function["children"][0]["node"]["children"][0]["node"]
    assert node["kind"] == "If"
    roles = [c["role"] for c in node["children"]]
    assert roles == ["condition", "then"]


# -------------------------------------------------------------------- unparser


def test_unparse_canonical_formatting():
    source = "int f(int a,float b){int x=a+b;if(x>0){return x;}else{return 0;}}"
    expected = (
        "int f(int a, float b) {\n"
        "    int x = a + b;\n"
        "    if (x > 0) {\n"
        "        return x;\n"
        "    } else {\n"
        "        return 0;\n"
        "    }\n"
        "}\n"
    )
    assert unparse(parse_ok(source)) == expected


def test_unparse_for_while_printf_scanf():
    source = (
        "int f() {\n"
        '  int a;\n'
        '  scanf("%d", &a);\n'
        "  for (int i = 0; i < a; i++) {\n"
        '    printf("%d\\n", i);\n'
        "  }\n"
        "  while (a > 0) {\n"
        "    a--;\n"
        "  }\n"
        "  return a;\n"
        "}\n"
    )
    text = unparse(parse_ok(source))
    assert 'scanf("%d", &a);' in text
    assert "for (int i = 0; i < a; i++) {" in text
    assert 'printf("%d\\n", i);' in text
    assert "a--;" in text
    assert text.count("{") == text.count("}")


ROUND_TRIP_PROGRAMS = [
    "int main() { return 0; }",
    "int f(int a, float b) { return a; }",
    "int f() { int a = 1, b = 2; return a + b; }",
    "int f() { a += 1; a -= 2; a *= 3; a /= 4; return a; }",
    "int f() { int a; a++; a--; return a; }",
    "int f() { if (a) g(); else if (b) h(); else k(); return 0; }",
    "int f() { while (a) { b(); } return 0; }",
    "int f() { for (;;) { break; } return 0; }",
    "int f() { for (i = 0; i < 3; i++) continue; return 0; }",
    "int f() { for (int i = 0, j = 1; i < j; i++) g(i, j); return 0; }",
    'int f() { printf("%d %f %%\\n", 1, 2.0); return 0; }',
    'int f() { int a; scanf("%d", &a); return a; }',
    "int f() { return !a && b || c == d < e + f * g % h; }",
    "int f() { return -(a + b) * -c; }",
    "int f() { { int inner; inner = 1; } return 0; }",
    "void f() { return; }",
    "float f() { return 1.5; }",
    "int f() { return 1 - 2 - 3 - 4; }",
    "int f() { g(h(i), j(k(l))); return 0; }",
]


@pytest.mark.parametrize("source", ROUND_TRIP_PROGRAMS)
def test_round_trip_structure_is_stable(source):
    original = parse_ok(source)
    once = unparse(original)
    reparsed = parse_ok(once)
    assert _shape(reparsed) == _shape(original)
    # Unparsing is idempotent, which is what makes the "no change" diff honest.
    assert unparse(reparsed) == once


def _shape(node) -> tuple:
    """A structural fingerprint: kinds and scalar fields, but no ids or spans."""
    from dataclasses import fields

    from opticode.parser.ast import _children_of

    attrs = tuple(
        (f.name, _freeze(getattr(node, f.name)))
        for f in sorted(fields(node), key=lambda f: f.name)
        if f.name not in ("id", "span", "semantic_type")
    )
    return (type(node).__name__, attrs, tuple(_shape(c) for _, c in _children_of(node)))


def _freeze(value):
    from opticode.core.spans import SourceSpan

    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, SourceSpan):
        return None  # positions legitimately move when the source is reformatted
    if hasattr(value, "id"):
        return None  # a child node: its shape is captured recursively
    return value


def test_round_trip_over_generated_programs():
    """Many small random programs must survive unparse/parse unchanged."""
    import random

    rng = random.Random(20240917)
    for _ in range(120):
        program = _random_program(rng)
        original = parse_ok(program)
        text = unparse(original)
        assert _shape(parse_ok(text)) == _shape(original), program
        assert unparse(parse_ok(text)) == text


def _random_program(rng: random.Random) -> str:
    names = ["a", "b", "c"]
    lines = ["int f() {"]
    for index in range(rng.randint(1, 4)):
        choice = rng.random()
        if choice < 0.4:
            lines.append(f"    int {names[index % 3]} = {rng.randint(0, 9)};")
        else:
            lines.append(
                f"    {names[index % 3]} = {rng.randint(0, 9)} "
                f"{rng.choice(['+', '-', '*', '==', '<', '&&', '||'])} "
                f"{names[(index + 1) % 3]};"
            )
    if rng.random() < 0.4:
        lines.append(f"    if ({names[0]} > {rng.randint(0, 5)}) {{ g({names[0]}); }}")
    if rng.random() < 0.3:
        lines.append(f"    while ({names[1]} < 3) {{ {names[1]}++; }}")
    lines.append("    return 0;")
    lines.append("}")
    return "\n".join(lines)


# ------------------------------------------------------------- error recovery


@pytest.mark.parametrize(
    ("source", "code"),
    [
        ("int f() { int a = 1 }", "E-SYN-002"),
        ("int f() { int a = (1 + 2; return a; }", "E-SYN-002"),
        ("int f() { int a = 1; return a;", "E-SYN-003"),
        ("int f() { return 1; ", "E-SYN-003"),
        ("int f( { return 1; }", "E-SYN-002"),
    ],
)
def test_missing_tokens_are_reported(source, code):
    _program, errors = parse_err(source)
    assert code in {d.code_str for d in errors}


def test_empty_program_is_an_error():
    _program, errors = parse_err("")
    assert [d.code_str for d in errors] == ["E-SYN-006"]


def test_whitespace_only_program_is_an_error():
    _program, errors = parse_err("   \n\t\n")
    assert "E-SYN-006" in {d.code_str for d in errors}


def test_stray_tokens_at_top_level():
    _program, errors = parse_err("int f() { return 1; } 42\n")
    assert errors
    assert "42" in errors[0].message or "42" in str([r.span for r in errors[0].related])


def test_multiple_errors_with_recovery():
    program, errors = parse_err(
        "int f() {\n"
        "    int a = 1\n"       # missing ';'
        "    int b = 2\n"       # missing ';'
        "    return a\n"
        "}\n"
        "int g() { return 0; }"
    )
    assert len(errors) >= 2
    assert program is not None, "the second function should still parse"
    assert [f.name for f in program.functions] == ["f", "g"]


def test_bare_expression_statement_is_rejected():
    _program, errors = parse_err("int f() { int a; a + 1; return 0; }")
    assert any("must be a declaration" in d.message for d in errors)


def test_empty_statement_is_rejected():
    _program, errors = parse_err("int f() { ; return 0; }")
    assert any("empty statement" in d.message for d in errors)


def test_ampersand_outside_scanf_is_rejected():
    _program, errors = parse_err("int f() { int a; return &a; }")
    assert any("only allowed in a scanf argument" in d.message for d in errors)


@pytest.mark.parametrize(
    ("source", "word"),
    [
        ("int f() { struct S s; return 0; }", "struct"),
        ("int f() { double d; return 0; }", "double"),
        ("int f() { char c; return 0; }", "char"),
        ("int f() { do { g(); } while (1); return 0; }", "do"),
        ("int f() { switch (1) { } return 0; }", "switch"),
        ("int f() { goto done; return 0; }", "goto"),
        ("int f() { int *p; return 0; }", "pointer"),
    ],
)
def test_unsupported_constructs_are_reported(source, word):
    _program, errors = parse_err(source)
    assert any(word in d.message for d in errors), [d.message for d in errors]


def test_arrays_are_reported_by_the_lexer():
    from opticode.core.diagnostics import DiagnosticBag
    from opticode.lexer import Lexer

    _tokens, diags = Lexer("int f() { int a[3]; return 0; }").scan()
    bag = DiagnosticBag(diags)
    assert any("arrays" in d.message for d in bag)


def test_deep_nesting_is_reported_not_crashed():
    source = "int f() { int a; return " + "(" * 400 + "1" + ")" * 400 + "; }"
    _program, errors = parse_err(source)
    assert "E-SYN-005" in {d.code_str for d in errors}


def test_deeply_nested_blocks_do_not_crash():
    source = "int f() {" + "{" * 500 + "}" * 500 + "return 0; }"
    _program, _errors = parse_err(source)  # must not raise
    assert True


def test_error_budget_is_capped():
    source = "int f() {" + "int a = 1" * 200 + "return 0; }"
    _program, errors = parse_err(source)
    assert any(d.code_str == "E-SYN-008" for d in errors)
    assert len(errors) <= 60


def test_missing_semicolon_message_points_at_previous_token():
    _program, errors = parse_err("int f() { int a = 1 return a; }")
    missing = next(d for d in errors if "expected ';'" in d.message)
    assert "'return'" in missing.message
    assert missing.related  # the offending token is described separately
