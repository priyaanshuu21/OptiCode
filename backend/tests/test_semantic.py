"""Semantic analysis: scopes, types, diagnostics, and format strings."""

from __future__ import annotations

import pytest

from opticode.core.diagnostics import DiagnosticCode
from opticode.parser import parse
from opticode.semantic import analyze
from opticode.semantic.format import FormatKind, parse_format
from opticode.semantic.symbols import SymbolKind
from opticode.semantic.types import TypeName


def analyse(source: str):
    program, diagnostics = parse(source)
    assert program is not None, f"parse failed: {diagnostics}"
    return analyze(program)


def codes(result) -> list[str]:
    return [diagnostic.code_str for diagnostic in result.diagnostics]


def errors(result) -> list[str]:
    return [
        diagnostic.code_str
        for diagnostic in result.diagnostics
        if diagnostic.severity.value == "error"
    ]


# ------------------------------------------------------------------- symbols


def test_parameter_is_declared_and_typed():
    result = analyse("int f(int a, float b) { return a; }")
    assert result.ok
    symbols = {symbol.name: symbol for symbol in result.symbols.symbols}
    assert symbols["a"].kind is SymbolKind.PARAM
    assert symbols["a"].type_name is TypeName.INT
    assert symbols["b"].type_name is TypeName.FLOAT


def test_local_scope_contains_declarations():
    result = analyse("int f() { int a = 1; return a; }")
    assert result.ok
    local = next(s for s in result.symbols.symbols if s.name == "a")
    assert local.kind is SymbolKind.LOCAL
    assert local.initialized


def test_function_scope_is_nested_under_nothing():
    result = analyse("int f() { return 0; }")
    root = result.symbols.scopes[0]
    assert root.kind.value == "function"
    assert root.parent is None


def test_undeclared_identifier_is_an_error():
    result = analyse("int f() { return b; }")
    assert DiagnosticCode.E300_UNDECLARED.value in codes(result)


def test_redeclaration_in_same_scope_is_an_error():
    result = analyse("int f() { int a; int a; return 0; }")
    assert DiagnosticCode.E301_REDECLARED.value in codes(result)


def test_shadowing_an_outer_local_is_an_error():
    result = analyse("int f() { int a; { int a; } return 0; }")
    assert DiagnosticCode.E302_SHADOWING.value in codes(result)


def test_sibling_scopes_may_reuse_a_name():
    """MiniC allows reuse in unrelated scopes; only shadowing a *visible* name is an error."""
    result = analyse("int f() { { int a; } { int a; } return 0; }")
    assert result.ok, codes(result)
    symbols = [s for s in result.symbols.symbols if s.name == "a"]
    assert len(symbols) == 2
    assert symbols[0].scope_id != symbols[1].scope_id


def test_same_name_in_two_functions_gets_unique_ir_names():
    result = analyse("int f() { int i; return i; } int g() { int i; return i; }")
    names = [symbol.ir_name for symbol in result.symbols.symbols]
    assert names == ["i", "i_2"]


def test_ir_name_is_source_name_when_unique():
    result = analyse("int f() { int count = 0; return count; }")
    assert result.symbols.symbols[0].ir_name == "count"


def test_bindings_record_which_symbol_each_name_resolved_to():
    """Lowering reads bindings instead of re-walking scopes, so they must be recorded."""
    result = analyse("int f(int a) { int b = a; return b; }")
    body = result.program.functions[0].body
    assert body is not None
    declaration, assignment = body.statements[0], body.statements[1]

    declared = result.bindings[declaration.id]
    assert declared.name == "b"

    # The assignment's target and the `return b` identifier both bind to that symbol.
    returned = assignment.value
    assert returned is not None
    assert result.bindings[returned.id] is declared

    # The declaration's initializer, `a`, binds to the parameter instead.
    assert declared.name == "b"
    assert result.bindings[declaration.init.id].name == "a"


# --------------------------------------------------------------------- types


def test_int_operand_promotes_to_float():
    result = analyse("float f(int a, float b) { float c = a + b; return c; }")
    assert result.ok, codes(result)


def test_float_to_int_truncation_warns():
    result = analyse("int f(float a) { int b = a; return b; }")
    assert DiagnosticCode.E308_TRUNCATION.value in codes(result)


def test_comparison_yields_int_even_for_floats():
    result = analyse("int f(float a, float b) { return a < b; }")
    assert result.ok
    binary = result.program.functions[0].body.statements[0].value
    assert result.types[binary.id] is TypeName.INT


def test_logical_operators_yield_int():
    result = analyse("int f(int a, int b) { return a && b; }")
    assert result.ok
    binary = result.program.functions[0].body.statements[0].value
    assert result.types[binary.id] is TypeName.INT


def test_modulo_requires_int_operands():
    result = analyse("int f(int a, float b) { return a % b; }")
    assert DiagnosticCode.E304_BAD_OPERAND_TYPES.value in errors(result)


def test_division_by_literal_zero_warns():
    result = analyse("int f(int a) { return a / 0; }")
    assert DiagnosticCode.E316_DIVIDE_BY_ZERO.value in codes(result)


def test_division_by_variable_does_not_warn():
    result = analyse("int f(int a, int b) { return a / b; }")
    assert DiagnosticCode.E316_DIVIDE_BY_ZERO.value not in codes(result)


def test_assigning_void_value_is_an_error():
    result = analyse("void g() { } int f() { int x = g(); return x; }")
    assert DiagnosticCode.E307_VOID_VALUE.value in errors(result)


def test_string_literal_outside_format_is_an_error():
    result = analyse('int f() { return "hi"; }')
    assert errors(result)


def test_unused_variable_warns():
    result = analyse("int f() { int unused = 1; return 0; }")
    assert DiagnosticCode.E309_UNUSED_VARIABLE.value in codes(result)


def test_unused_parameter_warns():
    result = analyse("int f(int unused) { return 0; }")
    assert DiagnosticCode.E309_UNUSED_VARIABLE.value in codes(result)


def test_written_but_never_read_variable_warns():
    result = analyse("int f(int a) { int b = 1; b = 2; return a; }")
    messages = [d.message for d in result.diagnostics]
    assert any("assigned but never read" in message for message in messages)


def test_reading_a_variable_clears_the_unused_warning():
    result = analyse("int f() { int used = 1; return used; }")
    assert DiagnosticCode.E309_UNUSED_VARIABLE.value not in codes(result)


def test_writing_a_symbol_is_not_reading_it():
    result = analyse("int f(int a) { a = 1; return 0; }")
    messages = [d.message for d in result.diagnostics]
    assert any("parameter that is never used" in message for message in messages)


# ------------------------------------------------------------- control flow


def test_missing_return_warns():
    result = analyse("int f() { int a = 1; }")
    assert DiagnosticCode.E315_MISSING_RETURN.value in codes(result)


def test_explicit_return_suppresses_the_warning():
    result = analyse("int f() { return 1; }")
    assert DiagnosticCode.E315_MISSING_RETURN.value not in codes(result)


def test_if_else_where_both_branches_return_does_not_warn():
    result = analyse("int f(int a) { if (a) { return 1; } else { return 2; } }")
    assert DiagnosticCode.E315_MISSING_RETURN.value not in codes(result)


def test_if_without_else_can_fall_off_the_end():
    result = analyse("int f(int a) { if (a) { return 1; } }")
    assert DiagnosticCode.E315_MISSING_RETURN.value in codes(result)


def test_void_function_needs_no_return():
    result = analyse("void f() { }")
    assert DiagnosticCode.E315_MISSING_RETURN.value not in codes(result)


def test_returning_a_value_from_void_is_an_error():
    result = analyse("void f() { return 1; }")
    assert DiagnosticCode.E306_RETURN_TYPE.value in errors(result)


def test_bare_return_from_int_is_an_error():
    result = analyse("int f() { return; }")
    assert DiagnosticCode.E306_RETURN_TYPE.value in errors(result)


def test_break_outside_a_loop_is_an_error():
    result = analyse("int f() { break; return 0; }")
    assert DiagnosticCode.E313_BREAK_OUTSIDE_LOOP.value in errors(result)


def test_continue_outside_a_loop_is_an_error():
    result = analyse("int f() { continue; return 0; }")
    assert DiagnosticCode.E314_CONTINUE_OUTSIDE_LOOP.value in errors(result)


def test_continue_inside_a_loop_is_allowed():
    result = analyse("int f() { while (1) { continue; } return 0; }")
    assert result.ok


def test_break_inside_a_loop_is_allowed():
    result = analyse("int f() { while (1) { break; } return 0; }")
    assert result.ok


# --------------------------------------------------------------------- calls


def test_call_arity_is_checked():
    result = analyse("int g(int a, int b) { return a; } int f() { return g(1); }")
    assert DiagnosticCode.E305_ARITY.value in errors(result)


def test_call_to_undeclared_function_is_an_error():
    result = analyse("int f() { return nope(1); }")
    assert DiagnosticCode.E300_UNDECLARED.value in errors(result)


def test_call_is_checked_against_a_later_definition():
    """MiniC has no forward declarations, so every signature is known up front."""
    result = analyse("int f() { return later(1); } int later(int a) { return a; }")
    assert result.ok, codes(result)


def test_float_argument_to_int_parameter_warns_about_truncation():
    """Narrowing is allowed but always reported, in argument position too."""
    result = analyse("int g(int a) { return a; } int f(float b) { return g(b); }")
    assert DiagnosticCode.E308_TRUNCATION.value in codes(result)


def test_void_argument_is_an_error():
    result = analyse("void g() { } int f() { return g(); }")
    assert DiagnosticCode.E307_VOID_VALUE.value in errors(result)


def test_int_argument_promotes_for_a_float_parameter():
    result = analyse("float g(float a) { return a; } int f(int b) { return g(b); }")
    assert result.ok, codes(result)


def test_void_call_in_statement_position_is_allowed():
    result = analyse("void g() { } int f() { g(); return 0; }")
    assert result.ok, codes(result)


def test_recursive_call_is_allowed():
    result = analyse("int f(int n) { if (n) { return f(n - 1); } return 0; }")
    assert result.ok, codes(result)


# ---------------------------------------------------------------- diagnostics


def test_multiple_errors_are_all_reported():
    """Analysis never stops at the first problem."""
    result = analyse(
        "int f() { return a; }\nint g() { return b; }\nint h() { return c; }"
    )
    assert len(errors(result)) == 3


def test_errors_are_sorted_by_position():
    result = analyse("int f() {\n  return zz;\n}\nint g() {\n  return yy;\n}")
    lines = [
        diagnostic.span.start_line
        for diagnostic in result.diagnostics
        if diagnostic.span is not None
    ]
    assert lines == sorted(lines)


def test_diagnostics_carry_the_semantic_stage():
    result = analyse("int f() { return a; }")
    assert all(d.stage.value == "semantic" for d in result.diagnostics)


# ------------------------------------------------------------ format strings


def test_printf_accepts_matching_arguments():
    result = analyse('int f() { printf("%d %f\\n", 1, 2.0); return 0; }')
    assert result.ok, codes(result)


def test_printf_escape_percent_needs_no_argument():
    result = analyse('int f() { printf("100%%\\n"); return 0; }')
    assert result.ok, codes(result)


def test_printf_argument_count_mismatch_is_an_error():
    result = analyse('int f() { printf("%d %d", 1); return 0; }')
    assert DiagnosticCode.E311_FORMAT_COUNT.value in errors(result)


def test_printf_wrong_argument_type_is_an_error():
    result = analyse('int f() { printf("%f", 1); return 0; }')
    assert DiagnosticCode.E310_FORMAT_MISMATCH.value in errors(result)


def test_printf_unsupported_specifier_is_an_error():
    result = analyse('int f() { printf("%s", 1); return 0; }')
    assert DiagnosticCode.E310_FORMAT_MISMATCH.value in errors(result)


def test_scanf_accepts_addresses_of_matching_type():
    result = analyse('int f() { int a; float b; scanf("%d %f", &a, &b); return 0; }')
    assert result.ok, codes(result)


def test_scanf_target_must_be_declared():
    result = analyse('int f() { scanf("%d", &missing); return 0; }')
    assert DiagnosticCode.E312_NOT_VARIABLE.value in errors(result)


def test_scanf_target_type_must_match():
    result = analyse('int f() { float b; scanf("%d", &b); return 0; }')
    assert DiagnosticCode.E310_FORMAT_MISMATCH.value in errors(result)


def test_scanf_can_write_a_variable_never_read_again():
    result = analyse('int f() { int a; scanf("%d", &a); return 0; }')
    assert DiagnosticCode.E309_UNUSED_VARIABLE.value not in codes(result)


# ---------------------------------------------------------- format internals


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("%d", 1),
        ("%f", 1),
        ("%%", 0),
        ("plain text", 0),
        ("%d and %f", 2),
        ("100%% done %d", 1),
    ],
)
def test_format_argument_counts(text: str, expected: int):
    consuming = [
        spec
        for spec in parse_format(text)
        if spec.kind in (FormatKind.INT, FormatKind.FLOAT)
    ]
    assert len(consuming) == expected


def test_parse_format_records_the_specifier_offset():
    numeric = [
        spec for spec in parse_format("a %d b %f") if spec.argument_index is not None
    ]
    assert numeric[0].offset == 2
    assert numeric[1].offset == 7


def test_parse_format_indexes_arguments_in_order():
    numeric = [
        spec for spec in parse_format("%d %f %d") if spec.argument_index is not None
    ]
    assert [spec.argument_index for spec in numeric] == [0, 1, 2]


def test_parse_format_flags_an_unsupported_specifier():
    specs = parse_format("%s")
    assert specs[0].kind is FormatKind.UNSUPPORTED


def test_empty_format_has_no_specifiers():
    assert parse_format("") == []
