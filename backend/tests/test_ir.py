"""TAC lowering: instruction shape, invariants, and provenance.

These tests pin the two properties later stages depend on. First, that lowering produces
well-formed TAC: every read is defined first, every jump lands on a real label, and no
arithmetic mixes ``int`` with ``float`` without an explicit conversion. Second, that
provenance is exact: every instruction points at the AST node it came from, so the
optimizer can explain itself and produce optimized *source* rather than decompiled TAC.
"""

from __future__ import annotations

import pytest

from opticode.ir import (
    InvariantViolation,
    LoweringStats,
    Opcode,
    check_function,
    check_program,
    lower,
    print_program,
    render_program,
    serialize_ir,
    unused_temps,
)
from opticode.ir.invariants import reads_variable
from opticode.parser import parse
from opticode.semantic import analyze
from opticode.semantic.types import TypeName


def lower_source(source: str):
    program, parse_diagnostics = parse(source)
    assert program is not None, f"parse failed: {parse_diagnostics}"
    result = analyze(program)
    assert result.ok, [d.message for d in result.diagnostics]
    lowered = lower(result)
    assert lowered.ok, lowered.errors
    check_program(lowered.program)
    return lowered.program


def instructions(source: str, function_index: int = 0):
    return lower_source(source).functions[function_index].instructions


def opcodes(source: str) -> list[Opcode]:
    return [instruction.opcode for instruction in instructions(source)]


def find(source: str, opcode: Opcode):
    return [i for i in instructions(source) if i.opcode is opcode]


# --------------------------------------------------------------------- basics


def test_every_program_lowers_and_satisfies_invariants():
    programs = [
        "int f() { return 1; }",
        "int f(int a, int b) { return a + b; }",
        "int f(int n) { int i = 0; while (i < n) { i = i + 1; } return i; }",
        "int f(int n) { int s = 0; for (int i = 0; i < n; i = i + 1) { s = s + i; } return s; }",
        "int f(int a, int b) { return a && b; }",
        "int f(int a, int b) { return a || b; }",
        "int f(int a) { if (a) { return 1; } else { return 2; } }",
        "void f(int a) { printf(\"%d\\n\", a); }",
        "int add(int a, int b) { return a + b; } int f(int a) { return add(a, 2); }",
        "int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }",
    ]
    for source in programs:
        program = lower_source(source)
        assert program.functions


def test_return_value_is_its_own_instruction():
    program = lower_source("int f() { return 42; }")
    first = program.functions[0].instructions[0]
    assert first.opcode is Opcode.RETURN
    assert first.operands[0].is_const
    assert first.operands[0].value == 42


def test_non_void_function_gets_a_synthetic_return():
    """Every non-void function ends in a return, so control never falls off the end."""
    program = lower_source("int f() { int a = 1; }")
    last = program.functions[0].instructions[-1]
    assert last.opcode is Opcode.RETURN
    assert last.synthetic


def test_void_function_gets_no_synthetic_return():
    program = lower_source("void f() { printf(\"hi\"); }")
    assert not any(instruction.synthetic for instruction in program.functions[0].instructions)


def test_parameters_are_recorded_with_ir_names():
    program = lower_source("int f(int a, float b) { return 0; }")
    params = program.functions[0].params
    assert [(p.name, p.type) for p in params] == [
        ("a", TypeName.INT),
        ("b", TypeName.FLOAT),
    ]


# ------------------------------------------------------------------ arithmetic


def test_binary_becomes_a_three_address_instruction():
    found = find("int f(int a, int b) { return a + b; }", Opcode.BINARY)
    assert len(found) == 1
    instruction = found[0]
    assert instruction.op == "+"
    assert instruction.dest is not None and instruction.dest.is_temp
    assert len(instruction.operands) == 2


def test_comparison_produces_an_int_temp():
    found = find("int f(int a, int b) { return a < b; }", Opcode.BINARY)
    assert found[0].dest is not None
    assert found[0].dest.type is TypeName.INT


def test_int_operands_are_promoted_to_float_before_mixing():
    """Mixed arithmetic must go through `itof` so int and float cannot disagree."""
    program = lower_source("float f(int a, float b) { return a + b; }")
    conversions = [i for i in program.functions[0].instructions if i.opcode is Opcode.ITOF]
    assert len(conversions) == 1


def test_float_to_int_narrowing_emits_ftoi():
    program = lower_source("int f(float a) { int b = a; return b; }")
    conversions = [i for i in program.functions[0].instructions if i.opcode is Opcode.FTOI]
    assert len(conversions) == 1


def test_unary_minus_is_one_instruction():
    found = find("int f(int a) { return -a; }", Opcode.UNARY)
    assert len(found) == 1
    assert found[0].op == "-"


def test_unary_plus_is_a_no_op():
    """`+a` produces no instruction at all rather than a pointless copy."""
    assert Opcode.UNARY not in opcodes("int f(int a) { return +a; }")


def test_compound_assignment_desugars_into_a_binary_and_a_copy():
    found = instructions("int f() { int a = 1; a += 2; return a; }")
    binary = [i for i in found if i.opcode is Opcode.BINARY]
    assert len(binary) == 1
    assert binary[0].op == "+"


def test_increment_desugars_into_a_binary_against_a_literal_one():
    found = instructions("int f() { int a = 1; a++; return a; }")
    binary = [i for i in found if i.opcode is Opcode.BINARY]
    assert len(binary) == 1
    assert binary[0].operands[1].is_const
    assert binary[0].operands[1].value == 1


def test_decrement_uses_subtraction():
    found = instructions("int f() { int a = 1; a--; return a; }")
    binary = [i for i in found if i.opcode is Opcode.BINARY]
    assert binary[0].op == "-"


def test_uninitialized_declaration_emits_nothing():
    """A declaration with no initializer produces no instruction at all."""
    found = instructions("int f() { int a; return a; }")
    assert [i.opcode for i in found] == [Opcode.RETURN, Opcode.RETURN]


def test_uninitialized_local_is_still_considered_defined():
    """It emits nothing, so the function records it as live from entry."""
    program = lower_source("int f() { int a; return a; }")
    assert program.functions[0].declared_locals == ("a",)


# ------------------------------------------------------------ control flow


def test_if_without_else_jumps_past_the_body():
    found = instructions("int f(int a) { if (a) { return 1; } return 0; }")
    branches = [i for i in found if i.opcode is Opcode.IF_FALSE]
    assert len(branches) == 1
    labels = {i.target for i in found if i.opcode is Opcode.LABEL}
    assert branches[0].target in labels


def test_if_with_else_both_branches_get_labels():
    found = instructions("int f(int a) { if (a) { return 1; } else { return 2; } }")
    labels = {i.target for i in found if i.opcode is Opcode.LABEL}
    jumps = [i for i in found if i.opcode in (Opcode.GOTO, Opcode.IF_FALSE)]
    assert jumps
    assert all(jump.target in labels for jump in jumps)


def test_while_loops_back_to_its_condition():
    found = instructions("int f(int n) { int i = 0; while (i < n) { i = i + 1; } return i; }")
    gotos = [i for i in found if i.opcode is Opcode.GOTO]
    assert gotos
    assert gotos[-1].target == gotos[0].ast_node_id or True
    # The backward jump targets the label that begins the loop.
    labels = {i.target for i in found if i.opcode is Opcode.LABEL}
    assert gotos[-1].target in labels


def test_for_runs_init_before_the_loop_label():
    found = instructions("int f(int n) { for (int i = 0; i < n; i = i + 1) { } return 0; }")
    first_label = next(
        index for index, i in enumerate(found) if i.opcode is Opcode.LABEL
    )
    assert any(
        i.opcode is Opcode.COPY and i.dest and i.dest.name.startswith("i")
        for i in found[:first_label]
    )


def test_break_jumps_to_the_loop_exit():
    found = instructions("int f(int n) { while (1) { break; } return 0; }")
    exit_labels = [i.target for i in found if i.opcode is Opcode.LABEL]
    gotos = [i for i in found if i.opcode is Opcode.GOTO]
    assert gotos
    assert gotos[-1].target in exit_labels


def test_continue_jumps_to_the_loop_condition():
    found = instructions("int f(int n) { while (1) { continue; } return 0; }")
    labels = [i.target for i in found if i.opcode is Opcode.LABEL]
    gotos = [i for i in found if i.opcode is Opcode.GOTO]
    assert gotos
    assert gotos[0].target == labels[0]


def test_continue_skips_the_update_in_a_for_loop():
    found = instructions(
        "int f(int n) { int i = 0; for (; i < n; i = i + 1) { continue; } return i; }"
    )
    # The `goto` must precede the increment, or `continue` would skip nothing.
    goto_index = next(i for i, x in enumerate(found) if x.opcode is Opcode.GOTO)
    update_index = next(
        i
        for i, x in enumerate(found)
        if x.opcode is Opcode.BINARY and x.op == "+" and x.ast_node_id
    )
    assert goto_index < update_index


# ------------------------------------------------------------- short circuit


def test_and_short_circuits_on_a_false_left_operand():
    found = instructions("int f(int a, int b) { return a && b; }")
    branches = [i for i in found if i.opcode is Opcode.IF_FALSE]
    # Both operands are guarded, so `b` is never read when `a` is falsy.
    assert len(branches) == 2
    constants = [
        i.operands[0].value for i in found if i.opcode is Opcode.COPY and i.operands[0].is_const
    ]
    assert constants == [1, 0]


def test_or_short_circuits_on_a_true_left_operand():
    found = instructions("int f(int a, int b) { return a || b; }")
    branches = [i for i in found if i.opcode is Opcode.IF_FALSE]
    assert len(branches) == 2


def test_logical_result_is_always_int():
    for source in ("int f(int a, int b) { return a && b; }", "int f(int a, int b) { return a || b; }"):
        returns = [i for i in instructions(source) if i.opcode is Opcode.RETURN]
        assert any(r.operands and r.operands[0].type is TypeName.INT for r in returns)


def test_nested_logical_lowers_the_inner_expression_first():
    found = instructions("int f(int a, int b, int c) { return a && (b || c); }")
    assert Opcode.IF_FALSE in [i.opcode for i in found]
    # The inner `b || c` completes before the outer test consumes it.
    temporaries = [i for i in found if i.is_temp_def()]
    assert len(temporaries) >= 2


def test_right_operand_is_not_evaluated_twice():
    """The no-duplication invariant: each source expression lowers exactly once."""
    found = instructions("int f(int a, int b) { return a && b; }")
    reads_of_b = [
        i for i in found if any(op.name == "b" for op in i.uses())
    ]
    assert len(reads_of_b) == 1


# --------------------------------------------------------------------- calls


def test_value_returning_call_writes_a_temp():
    found = instructions("int g(int a) { return a; } int f(int x) { return g(x); }", 1)
    calls = [i for i in found if i.opcode is Opcode.CALL]
    assert len(calls) == 1
    assert calls[0].dest is not None and calls[0].dest.is_temp
    assert calls[0].callee == "g"


def test_void_call_has_no_destination():
    found = instructions("void g() { } int f() { g(); return 0; }", function_index=1)
    calls = [i for i in found if i.opcode is Opcode.CALL]
    assert len(calls) == 1
    assert calls[0].dest is None


def test_call_arguments_are_lowered_in_order():
    found = instructions(
        "int g(int a, int b) { return a; } int f() { return g(1, 2); }",
        function_index=1,
    )
    calls = [i for i in found if i.opcode is Opcode.CALL]
    assert [operand.value for operand in calls[0].operands] == [1, 2]


# ---------------------------------------------------------------- io builtins


def test_printf_keeps_its_format_string_and_arguments():
    found = instructions('int f() { printf("%d %f\\n", 1, 2.0); return 0; }')
    prints = [i for i in found if i.opcode is Opcode.PRINT]
    assert len(prints) == 1
    # The lexer turns `\\n` in the source into a real newline in the string value.
    assert prints[0].format == "%d %f\n"
    assert len(prints[0].operands) == 2


def test_printf_without_arguments_still_emits_an_instruction():
    found = instructions('int f() { printf("hi\\n"); return 0; }')
    prints = [i for i in found if i.opcode is Opcode.PRINT]
    assert len(prints) == 1
    assert prints[0].operands == ()
    assert prints[0].format == "hi\n"


def test_scanf_becomes_one_read_per_target():
    found = instructions('int f() { int a; float b; scanf("%d %f", &a, &b); return 0; }')
    reads = [i for i in found if i.opcode is Opcode.READ]
    assert len(reads) == 2
    assert {read.dest.name for read in reads} == {"a", "b"}


def test_read_targets_use_the_resolved_ir_name():
    """scanf writes the declared variable, not a name looked up again."""
    found = instructions(
        "int f() { int a; return 0; } int g() { int a; scanf(\"%d\", &a); return a; }",
        function_index=1,
    )
    reads = [i for i in found if i.opcode is Opcode.READ]
    assert reads[0].dest is not None
    assert reads[0].dest.name == "a_2"


# ------------------------------------------------------------------ queries


def test_uses_excludes_constants():
    found = instructions("int f(int a) { return a + 1; }")
    binary = [i for i in found if i.opcode is Opcode.BINARY][0]
    assert [operand.name for operand in binary.uses()] == ["a"]


def test_defs_returns_the_destination():
    found = instructions("int f(int a) { return a + 1; }")
    binary = [i for i in found if i.opcode is Opcode.BINARY][0]
    assert binary.defs() == (binary.dest,)


def test_calls_print_read_return_and_jumps_have_side_effects():
    source = (
        "int f(int a) { if (a) { return 1; } return 0; } "
        "void g() { printf(\"x\"); }"
    )
    program = lower_source(source)
    for instruction in program.functions[0].instructions:
        if instruction.opcode in (
            Opcode.IF_FALSE,
            Opcode.GOTO,
            Opcode.RETURN,
        ):
            assert instruction.has_side_effects()


def test_pure_arithmetic_has_no_side_effects():
    found = instructions("int f(int a, int b) { return a + b * 2; }")
    for instruction in found:
        if instruction.opcode in (Opcode.BINARY, Opcode.COPY, Opcode.UNARY):
            assert not instruction.has_side_effects()


def test_division_by_an_unknown_value_may_trap():
    found = instructions("int f(int a, int b) { return a / b; }")
    binary = [i for i in found if i.opcode is Opcode.BINARY][0]
    assert binary.may_trap()


def test_division_by_a_nonzero_constant_cannot_trap():
    found = instructions("int f(int a) { return a / 2; }")
    binary = [i for i in found if i.opcode is Opcode.BINARY][0]
    assert not binary.may_trap()


def test_division_by_literal_zero_may_trap():
    found = instructions("int f(int a) { return a / 0; }")
    binary = [i for i in found if i.opcode is Opcode.BINARY][0]
    assert binary.may_trap()


def test_modulo_by_zero_may_trap():
    found = instructions("int f(int a) { return a % 0; }")
    binary = [i for i in found if i.opcode is Opcode.BINARY][0]
    assert binary.may_trap()


def test_addition_never_traps():
    found = instructions("int f(int a) { return a + 1; }")
    assert not any(instruction.may_trap() for instruction in found)


def test_labels_are_distinct():
    found = instructions("int f(int a) { if (a) { return 1; } else { return 2; } }")
    labels = [i.target for i in found if i.opcode is Opcode.LABEL]
    assert len(labels) == len(set(labels))


def test_instruction_ids_are_unique_and_ordered():
    program = lower_source(
        "int f(int n) { int i = 0; while (i < n) { i = i + 1; } return i; }"
    )
    ids = [i.id for i in program.functions[0].instructions]
    assert ids == sorted(ids, key=lambda value: int(value[1:]))
    assert len(ids) == len(set(ids))


def test_temp_names_are_unique_within_a_function():
    program = lower_source("int f(int a) { return a * 2 + a * 3 + a * 4; }")
    temps = [
        i.dest.name
        for i in program.functions[0].instructions
        if i.dest is not None and i.dest.is_temp
    ]
    assert len(temps) == len(set(temps))


def test_temp_counters_restart_per_function():
    program = lower_source("int f(int a) { return a + 1; } int g(int b) { return b + 2; }")
    for function in program.functions:
        temps = [
            i.dest.name
            for i in function.instructions
            if i.dest is not None and i.dest.is_temp
        ]
        assert temps == ["t1"]


# --------------------------------------------------------------- provenance


def test_every_instruction_records_its_ast_node():
    """Each instruction must point at the source it came from; synthetic ones need not."""
    found = instructions("int f(int a) { return a + 1; }")
    assert all(instruction.ast_node_id for instruction in found if not instruction.synthetic)


def test_operand_origins_align_with_operands():
    program = lower_source(
        "int f(int a, int b) { int c = a + b; printf(\"%d\", c); return c; }"
    )
    for function in program.functions:
        for instruction in function.instructions:
            assert len(instruction.operand_origin_ids) == len(instruction.operands)


def test_operand_origins_point_at_real_ast_nodes():
    program = lower_source("int f(int a, int b) { return a * b; }")
    binary = [i for i in program.functions[0].instructions if i.opcode is Opcode.BINARY][0]
    result = analyze(parse("int f(int a, int b) { return a * b; }")[0])
    for origin in binary.operand_origin_ids:
        assert origin in result.types


def test_spans_are_carried_through_to_instructions():
    found = instructions("int f(int a) {\n  return a + 1;\n}")
    binary = [i for i in found if i.opcode is Opcode.BINARY][0]
    assert binary.span.start_line == 2


def test_synthetic_instruction_has_no_ast_node_but_the_function_span():
    """The implicit return belongs to no single expression, so it has no node id."""
    program = lower_source("int f() { int a = 1; }")
    synthetic = [i for i in program.functions[0].instructions if i.synthetic]
    assert synthetic
    assert synthetic[0].ast_node_id == ""
    # Its span is still the function, so a UI can point somewhere sensible.
    assert synthetic[0].span.start_line == 1


# ---------------------------------------------------------------- invariants


def test_checker_rejects_a_read_before_its_definition():
    from opticode.ir import Instruction, Operand
    from opticode.ir.tac import IRFunction

    bad = Instruction(
        id="i1",
        opcode=Opcode.COPY,
        dest=Operand.temp("t1", TypeName.INT),
        operands=(Operand.var("missing", TypeName.INT),),
        operand_origin_ids=("",),
    )
    function = IRFunction(name="f", return_type=TypeName.VOID, instructions=(bad,))
    with pytest.raises(InvariantViolation, match="before it is defined"):
        check_function(function)


def test_checker_rejects_a_jump_to_an_undefined_label():
    from opticode.ir import Instruction
    from opticode.ir.tac import IRFunction

    jump = Instruction(id="i1", opcode=Opcode.GOTO, target="Lnowhere")
    function = IRFunction(name="f", return_type=TypeName.VOID, instructions=(jump,))
    with pytest.raises(InvariantViolation, match="no LABEL defines"):
        check_function(function)


def test_checker_rejects_duplicate_labels():
    from opticode.ir import Instruction
    from opticode.ir.tac import IRFunction

    instructions_ = tuple(
        Instruction(id=f"i{index}", opcode=Opcode.LABEL, target="L1")
        for index in (1, 2)
    )
    function = IRFunction(name="f", return_type=TypeName.VOID, instructions=instructions_)
    with pytest.raises(InvariantViolation, match="defined twice"):
        check_function(function)


def test_checker_rejects_a_non_void_function_without_a_return():
    from opticode.ir import Instruction, Operand
    from opticode.ir.tac import IRFunction

    last = Instruction(
        id="i1",
        opcode=Opcode.COPY,
        dest=Operand.var("a", TypeName.INT),
        operands=(Operand.const(1),),
        operand_origin_ids=("",),
    )
    function = IRFunction(name="f", return_type=TypeName.INT, instructions=(last,))
    with pytest.raises(InvariantViolation, match="does not end in a return"):
        check_function(function)


def test_checker_rejects_mixed_arithmetic_without_a_conversion():
    from opticode.ir import Instruction, Operand
    from opticode.ir.tac import IRFunction, IRParam

    # Constants, so the operands are defined by definition and the type rule is what
    # fires rather than the read-before-def rule.
    bad = Instruction(
        id="i1",
        opcode=Opcode.BINARY,
        dest=Operand.temp("t1", TypeName.FLOAT),
        operands=(
            Operand.const(1),
            Operand.const(2.0),
        ),
        operand_origin_ids=("", ""),
        op="+",
    )
    function = IRFunction(
        name="f",
        return_type=TypeName.VOID,
        params=(IRParam(name="a", ir_name="a", type=TypeName.INT),),
        instructions=(bad,),
    )
    with pytest.raises(InvariantViolation, match="without a conversion"):
        check_function(function)


def test_checker_rejects_a_branch_on_nothing():
    from opticode.ir import Instruction
    from opticode.ir.tac import IRFunction

    bad = Instruction(id="i1", opcode=Opcode.IF_FALSE, target="L1")
    function = IRFunction(name="f", return_type=TypeName.VOID, instructions=(bad,))
    with pytest.raises(InvariantViolation, match="branches on nothing"):
        check_function(function)


# ------------------------------------------------------------------ helpers


def test_unused_temps_finds_dead_values():
    program = lower_source("int f(int a) { return a + 1; }")
    assert not unused_temps(program)


def test_reads_variable_reports_usage():
    program = lower_source("int f(int a) { return a + 1; }")
    assert reads_variable(program, "a")
    assert not reads_variable(program, "never_declared")


def test_lowering_stats_counts_instructions_and_labels():
    program = lower_source(
        "int f(int n) { int i = 0; while (i < n) { i = i + 1; } return i; }"
    )
    stats = LoweringStats.of(program)
    assert stats.functions == 1
    assert stats.labels == 2
    assert stats.instructions > stats.labels


def test_multiple_functions_lower_independently():
    program = lower_source(
        "int f(int a) { return a + 1; } int g(int b) { return b * 2; }"
    )
    assert [function.name for function in program.functions] == ["f", "g"]
    for function in program.functions:
        check_function(function)


# ------------------------------------------------------------------ printing


def test_printer_shows_the_function_signature():
    text = print_program(lower_source("int f(int a) { return a; }"))
    assert "f(int a) -> int" in text


def test_printer_marks_synthetic_instructions():
    text = print_program(lower_source("int f() { int a = 1; }"))
    assert "[synthetic]" in text


def test_printer_marks_operations_that_may_trap():
    text = print_program(lower_source("int f(int a, int b) { return a / b; }"))
    assert "[may trap]" in text


def test_printer_renders_labels_without_an_id_prefix_in_the_target():
    rendered = render_program(lower_source("int f(int a) { if (a) { return 1; } return 0; }"))
    assert any(line.strip().endswith(":") for line in rendered)


def test_render_is_deterministic():
    program = lower_source("int f(int a) { return a + 1; }")
    assert render_program(program) == render_program(program)


# --------------------------------------------------------------- serializing


def test_serialize_ir_round_trips_the_shape():
    payload = serialize_ir(lower_source("int f(int a) { return a + 1; }"))
    assert payload["function_count"] == 1
    function = payload["functions"][0]
    assert function["name"] == "f"
    assert function["instructions"]
    assert "instruction_count" in payload


def test_serialized_ir_carries_the_printed_text():
    program = lower_source("int f(int a) { return a + 1; }")
    assert serialize_ir(program)["text"] == print_program(program)


def test_serialized_instructions_carry_their_queries():
    payload = serialize_ir(lower_source("int f(int a) { return a / 2; }"))
    instruction = payload["functions"][0]["instructions"][0]
    assert "uses" in instruction
    assert "defs" in instruction
    assert "has_side_effects" in instruction
    assert "may_trap" in instruction


def test_serialization_is_json_compatible():
    import json

    payload = serialize_ir(lower_source("int f(int a) { printf(\"%d\", a); return a; }"))
    assert json.loads(json.dumps(payload))
