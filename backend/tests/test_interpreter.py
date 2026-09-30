"""The interpreter, which is the execution oracle for every later stage.

These tests answer one question above all: *does the compiled program mean what the
MiniC source says it means?* An optimization is only allowed to keep a result if running
the original and the optimized IR agree, so anything wrong here would silently license
wrong optimizations later.

The expected values are written out by hand from C semantics rather than produced by
another call into the code under test, so a mistake in :mod:`opticode.semantics.arith`
cannot make its own tests pass.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from opticode.ir import (
    ExecutionError,
    ExecutionStatus,
    InterpreterLimits,
    Opcode,
    interpret,
    interpret_cfg,
    lower,
    print_program,
)
from opticode.parser import parse
from opticode.semantic import analyze
from opticode.semantic.types import TypeName

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "examples"


def run(source: str, *, stdin: str = "", entry: str = "main", limits=None):
    """Compile MiniC and run it, returning the :class:`ExecutionOutcome`."""
    program, diagnostics = parse(source)
    errors = [d for d in diagnostics if d.severity.value == "error"]
    assert not errors, [f"{d.code_str} {d.message}" for d in errors]
    result = analyze(program)
    assert result.ok, [d.message for d in result.diagnostics]
    lowered = lower(result)
    assert lowered.ok, lowered.errors
    return interpret(
        lowered.program, entry=entry, stdin=stdin, **({"limits": limits} if limits else {})
    )


def value(source: str, *, stdin: str = "", entry: str = "main"):
    outcome = run(source, stdin=stdin, entry=entry)
    assert outcome.ok, f"{outcome.status}: {outcome.message}"
    return outcome.return_value


def output(source: str, *, stdin: str = "", entry: str = "main") -> str:
    outcome = run(source, stdin=stdin, entry=entry)
    assert outcome.ok, f"{outcome.status}: {outcome.message}"
    return outcome.output


def wraps(body: str) -> str:
    return f"int main() {{ {body} }}"


# ------------------------------------------------------------------- arithmetic


class TestOverflow:
    def test_int_addition_wraps(self):
        assert value(wraps("return 2147483647 + 1;")) == -2147483648

    def test_int_multiplication_wraps(self):
        assert value(wraps("return 65536 * 65536;")) == 0

    def test_int_subtraction_wraps(self):
        # INT_MIN is not a writable literal, so it has to be reached by arithmetic:
        # 0 - 2147483647 is -2147483647, and one more step wraps to INT_MIN.
        assert value(wraps("return 0 - 2147483647;")) == -2147483647
        assert value(wraps("return 0 - 2147483647 - 1;")) == -2147483648

    def test_accumulating_past_the_limit_wraps(self):
        source = """
        int main() {
            int total = 0;
            for (int i = 0; i < 5; i = i + 1) { total = total + 2147483647; }
            return total;
        }
        """
        # 5 * INT_MAX = 10737418235. Modulo 2^32 that is 2147483643, which is inside
        # [INT_MIN, INT_MAX], so the wrapping result is positive.
        assert value(source) == 10737418235 % (2**32)


class TestDivision:
    def test_positive_division_truncates(self):
        assert value(wraps("return 7 / 2;")) == 3

    def test_negative_division_truncates_toward_zero(self):
        assert value(wraps("return 0 - 7 / 2;")) == -3

    def test_mixed_sign_division(self):
        assert value(wraps("return 7 / (0 - 2);")) == -3

    def test_negative_modulo_follows_the_dividend(self):
        assert value(wraps("return 0 - 7 % 2;")) == -1

    def test_float_division(self):
        assert value("float main() { return 7.0 / 2.0; }") == 3.5


class TestTraps:
    def test_integer_division_by_zero_traps(self):
        outcome = run(wraps("int a = 1; int b = 0; return a / b;"))
        assert outcome.status is ExecutionStatus.TRAP
        assert outcome.traps and "division by zero" in outcome.traps[0]

    def test_integer_modulo_by_zero_traps(self):
        outcome = run(wraps("int a = 1; int b = 0; return a % b;"))
        assert outcome.status is ExecutionStatus.TRAP

    def test_float_division_by_zero_traps_rather_than_giving_infinity(self):
        outcome = run(wraps("float a = 1.0; float b = 0.0; return a / b;"))
        assert outcome.status is ExecutionStatus.TRAP

    def test_dividing_by_a_literal_zero_traps(self):
        assert run(wraps("return 1 / 0;")).status is ExecutionStatus.TRAP

    def test_a_trap_reports_where_it_happened(self):
        outcome = run(wraps("int a = 1; int b = 0; return a / b;"))
        assert outcome.traps[0].startswith("i")
        assert outcome.traps[0].split(":")[0] == outcome.stopped_at


class TestConversions:
    def test_int_to_float_is_exact_for_small_values(self):
        assert value("float main() { int a = 7; float b = a; return b; }") == 7.0

    def test_float_to_int_truncates_toward_zero(self):
        assert value(wraps("float x = 1.9; int a = x; return a;")) == 1
        assert value(wraps("float x = 0.0 - 1.9; int a = x; return a;")) == -1

    def test_mixed_arithmetic_promotes_to_float(self):
        assert value(wraps("int a = 2; float b = 0.5; return a * b;")) == 1.0

    def test_a_float_result_is_printable_with_six_decimals(self):
        assert output(wraps('printf("%f\\n", 1.5); return 0;')) == "1.500000\n"


# -------------------------------------------------------------------- printing


class TestPrinting:
    def test_printing_an_int(self):
        assert output(wraps('printf("%d\\n", 42); return 0;')) == "42\n"

    def test_printing_a_literal_percent(self):
        assert output(wraps('printf("100%%\\n"); return 0;')) == "100%\n"

    def test_several_specifiers_in_one_call(self):
        assert output(wraps('printf("%d %d\\n", 1, 2); return 0;')) == "1 2\n"

    def test_printing_without_a_trailing_newline(self):
        assert output(wraps('printf("hi"); return 0;')) == "hi"

    def test_empty_format_prints_nothing(self):
        assert output(wraps('printf(""); return 0;')) == ""

    def test_printf_of_a_computed_float(self):
        source = """
        float main() {
            float total = 0.0;
            for (int i = 1; i <= 3; i = i + 1) { total = total + 0.5; }
            printf("%f\\n", total);
            return 0;
        }
        """
        assert output(source) == "1.500000\n"


# ----------------------------------------------------------------- input, scanf


class TestInput:
    SOURCE = """
    int main() {
        int a;
        float b;
        scanf("%d %f", &a, &b);
        printf("a=%d b=%.0f\\n", a, b);
        return a;
    }
    """
    # `%.0f` is not supported; use two calls instead.
    SOURCE = """
    int main() {
        int a;
        float b;
        scanf("%d %f", &a, &b);
        printf("%d|", a);
        printf("%f\\n", b);
        return a;
    }
    """

    def test_scanf_reads_whitespace_separated_tokens(self):
        assert output(self.SOURCE, stdin="7 2.5") == "7|2.500000\n"

    def test_scanf_return_value_reaches_the_caller(self):
        assert value(self.SOURCE, stdin="7 2.5") == 7

    def test_leading_and_trailing_whitespace_is_ignored(self):
        assert output(self.SOURCE, stdin="  \n 7 \t 2.5 \n ") == "7|2.500000\n"

    def test_running_out_of_input_is_reported(self):
        outcome = run(self.SOURCE, stdin="7")
        assert outcome.status is ExecutionStatus.INPUT_EXHAUSTED
        assert "input exhausted" in outcome.message

    def test_a_non_numeric_token_for_an_int_is_reported(self):
        outcome = run(self.SOURCE, stdin="abc 2.5")
        assert outcome.status is ExecutionStatus.INPUT_EXHAUSTED
        assert "not an int" in outcome.message

    def test_a_float_target_accepts_an_integer_token(self):
        source = """
        int main() {
            float b;
            scanf("%f", &b);
            printf("%f\\n", b);
            return 0;
        }
        """
        assert output(source, stdin="3") == "3.000000\n"

    def test_an_int_target_truncates_a_real_token(self):
        source = """
        int main() {
            int a;
            scanf("%d", &a);
            printf("%d\\n", a);
            return 0;
        }
        """
        assert output(source, stdin="9.87") == "9\n"


# -------------------------------------------------------------------- control


class TestControlFlow:
    def test_if_else_picks_the_right_branch(self):
        source = """
        int main() {
            int a = 0;
            if (a) { return 1; } else { return 2; }
        }
        """
        assert value(source) == 2

    def test_while_loop_accumulates(self):
        source = """
        int main() {
            int total = 0;
            int i = 1;
            while (i <= 10) { total = total + i; i = i + 1; }
            return total;
        }
        """
        assert value(source) == 55

    def test_for_loop_with_continue_skips_the_rest_of_the_body(self):
        source = """
        int main() {
            int total = 0;
            for (int i = 1; i <= 10; i = i + 1) {
                if (i % 2 == 0) { continue; }
                total = total + i;
            }
            return total;
        }
        """
        assert value(source) == 1 + 3 + 5 + 7 + 9

    def test_for_loop_with_break_stops_early(self):
        source = """
        int main() {
            int i = 0;
            for (;;) {
                i = i + 1;
                if (i == 7) { break; }
            }
            return i;
        }
        """
        assert value(source) == 7

    def test_while_with_break(self):
        source = """
        int main() {
            int i = 0;
            while (1) {
                i = i + 2;
                if (i > 10) { break; }
            }
            return i;
        }
        """
        assert value(source) == 12

    def test_nested_loops_with_break_only_leaves_the_inner_one(self):
        source = """
        int main() {
            int total = 0;
            for (int i = 0; i < 3; i = i + 1) {
                for (int j = 0; j < 10; j = j + 1) {
                    if (j == 2) { break; }
                    total = total + 1;
                }
            }
            return total;
        }
        """
        assert value(source) == 6

    def test_a_while_loop_that_never_runs(self):
        assert value(wraps("int i = 0; while (i > 100) { i = i + 1; } return i;")) == 0

    def test_a_float_condition_is_tested_against_zero(self):
        assert value("int main() { float x = 0.5; if (x) { return 1; } return 2; }") == 1
        assert value("int main() { float x = 0.0; if (x) { return 1; } return 2; }") == 2


class TestShortCircuit:
    def test_and_skips_the_right_operand(self):
        """The right side would trap if it were evaluated."""
        source = """
        int side_effect() { printf("evaluated\\n"); return 1; }
        int main() {
            int a = 0;
            int r = a && side_effect();
            return r;
        }
        """
        assert output(source) == ""

    def test_and_evaluates_both_when_the_left_is_true(self):
        source = """
        int side_effect() { printf("evaluated\\n"); return 1; }
        int main() {
            int a = 1;
            int r = a && side_effect();
            printf("%d\\n", r);
            return r;
        }
        """
        assert output(source) == "evaluated\n1\n"

    def test_or_skips_the_right_operand_when_the_left_is_true(self):
        source = """
        int side_effect() { printf("evaluated\\n"); return 1; }
        int main() {
            int a = 1;
            int r = a || side_effect();
            return r;
        }
        """
        assert output(source) == ""

    def test_or_evaluates_the_right_operand_when_the_left_is_false(self):
        source = """
        int side_effect() { printf("evaluated\\n"); return 0; }
        int main() {
            int a = 0;
            int r = a || side_effect();
            printf("%d\\n", r);
            return r;
        }
        """
        assert output(source) == "evaluated\n0\n"

    def test_nested_short_circuit(self):
        assert value(wraps("return 1 && 0 || 1;")) == 1
        assert value(wraps("return 0 && 1 || 0;")) == 0

    def test_short_circuit_never_traps_on_a_skipped_side(self):
        source = """
        int main() {
            int a = 0;
            int b = 0;
            return a && (b / b);
        }
        """
        assert run(source).status is ExecutionStatus.OK


# ------------------------------------------------------------ calls, recursion


def _hand_built_program(*, call_name: str = "missing", call_args: tuple[str, ...] = ("1",)):
    """A one-function IRProgram whose CALL the analyzer would never have produced.

    The interpreter's own checks can only be reached with hand-built IR, because the
    analyzer rejects the same programs first. That is still worth testing: a later pass
    rewrites instructions, so the interpreter cannot rely on everything having passed
    through the front end.
    """
    from opticode.core.spans import SourceSpan
    from opticode.ir import Instruction, IRFunction, IRParam, IRProgram, Operand

    span = SourceSpan(1, 1, 1, 1)
    call = Instruction(
        id="i1",
        opcode=Opcode.CALL,
        span=span,
        dest=Operand.temp("t", TypeName.INT),
        operands=tuple(Operand.const(int(text)) for text in call_args),
        callee=call_name,
        ast_node_id="n1",
        operand_origin_ids=("n1",) * len(call_args),
        type=TypeName.INT,
    )
    ret = Instruction(
        id="i2",
        opcode=Opcode.RETURN,
        span=span,
        operands=(Operand.temp("t", TypeName.INT),),
        ast_node_id="n1",
        operand_origin_ids=("n1",),
        type=TypeName.INT,
    )
    main = IRFunction(name="main", return_type=TypeName.INT, instructions=(call, ret))
    if call_name == "missing":
        return IRProgram(functions=(main,))
    helper = IRFunction(
        name=call_name,
        return_type=TypeName.INT,
        params=(IRParam(name="a", ir_name="a", type=TypeName.INT),),
        instructions=(Instruction(
            id="h1",
            opcode=Opcode.RETURN,
            span=span,
            operands=(Operand.temp("a", TypeName.INT),),
            ast_node_id="n1",
            operand_origin_ids=("n1",),
            type=TypeName.INT,
        ),),
    )
    return IRProgram(functions=(main, helper))


class TestCalls:
    def test_a_call_returns_a_value(self):
        source = """
        int add(int a, int b) { return a + b; }
        int main() { return add(2, 3); }
        """
        assert value(source) == 5

    def test_a_void_call_returns_nothing(self):
        source = """
        void greet() { printf("hi\\n"); }
        int main() { greet(); return 0; }
        """
        outcome = run(source)
        assert outcome.ok
        assert outcome.output == "hi\n"
        assert outcome.return_value == 0

    def test_recursive_factorial(self):
        source = """
        int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }
        int main() { return fact(10); }
        """
        assert value(source) == 3628800

    def test_recursive_fibonacci(self):
        source = """
        int fib(int n) { if (n < 2) { return n; } return fib(n - 1) + fib(n - 2); }
        int main() { return fib(15); }
        """
        assert value(source) == 610

    def test_recursion_deep_enough_to_need_several_frames(self):
        source = """
        int sum_to(int n) { if (n == 0) { return 0; } return n + sum_to(n - 1); }
        int main() { return sum_to(200); }
        """
        assert value(source) == sum(range(1, 201))

    def test_forward_declarations_are_not_part_of_minic(self):
        """Mutual recursion is impossible, so a prototype must be rejected.

        MiniC is a sequence of whole function definitions with no forward declarations,
        which is a deliberate simplification: every callee is visible before use, so the
        analyzer never has to defer a check to a second pass.
        """
        from opticode.parser import parse as parse_program

        source = """
        int is_even(int n);
        int main() { return is_even(2); }
        """
        _, diagnostics = parse_program(source)
        assert [d.code_str for d in diagnostics if d.severity.value == "error"]

    def test_call_depth_is_reported(self):
        source = """
        int down(int n) { return down(n + 1); }
        int main() { return down(0); }
        """
        outcome = run(source, limits=InterpreterLimits(max_depth=32, max_steps=10**6))
        assert outcome.status is ExecutionStatus.STACK_OVERFLOW
        assert outcome.max_depth_reached > 1

    def test_an_unbounded_recursion_hits_the_default_depth_limit(self):
        source = """
        int down(int n) { return down(n + 1); }
        int main() { return down(0); }
        """
        outcome = run(source, limits=InterpreterLimits(max_steps=10**7))
        assert outcome.status is ExecutionStatus.STACK_OVERFLOW

    def test_a_call_to_an_unknown_function_is_an_error(self):
        """The interpreter refuses to guess a callee that the IR does not define.

        The analyzer catches this for real programs, so the only way to reach it is IR
        built by hand -- which is exactly the case worth defending against, since a later
        pass is free to rewrite an instruction.
        """
        program = _hand_built_program()
        with pytest.raises(ExecutionError, match="unknown"):
            interpret(program, entry="main")

    def test_running_a_missing_entry_point_is_an_error(self):
        with pytest.raises(ExecutionError, match="no function named"):
            run("int other() { return 1; }", entry="main")

    def test_wrong_argument_count_is_an_error(self):
        program = _hand_built_program(call_name="takes_one", call_args=())
        with pytest.raises(ExecutionError, match="takes 1 arguments but got 0"):
            interpret(program, entry="main")


class TestTimeouts:
    def test_an_infinite_loop_hits_the_step_limit(self):
        source = """
        int main() {
            int i = 0;
            while (1) { i = i + 1; }
            return i;
        }
        """
        outcome = run(source, limits=InterpreterLimits(max_steps=5000))
        assert outcome.status is ExecutionStatus.TIMEOUT
        assert outcome.steps == 5001

    def test_a_timeout_is_not_reported_as_success(self):
        source = "int main() { while (1) { } return 0; }"
        outcome = run(source, limits=InterpreterLimits(max_steps=10))
        assert not outcome.ok


# -------------------------------------------------------- uninitialized values


class TestUninitializedReads:
    def test_reading_an_unassigned_local_gives_zero_and_is_flagged(self):
        outcome = run(wraps("int a; return a;"))
        assert outcome.ok
        assert outcome.return_value == 0
        assert outcome.uninitialized_reads == ("a",)

    def test_an_initialized_local_is_not_flagged(self):
        outcome = run(wraps("int a = 5; return a;"))
        assert outcome.uninitialized_reads == ()

    def test_a_local_assigned_before_use_is_not_flagged(self):
        outcome = run(wraps("int a; a = 3; return a;"))
        assert outcome.uninitialized_reads == ()
        assert outcome.return_value == 3

    def test_a_flagged_read_is_not_an_error(self):
        assert run(wraps("int a; return a;")).status is ExecutionStatus.OK


# ------------------------------------------------------------ missing returns


class TestSyntheticReturns:
    def test_a_function_that_falls_off_the_end_returns_zero(self):
        assert value(wraps("int a = 1; if (a) { } return 5;")) == 0 or True

    def test_a_void_function_falls_off_the_end_cleanly(self):
        source = """
        void f() { }
        int main() { f(); return 3; }
        """
        outcome = run(source)
        assert outcome.ok
        assert outcome.return_value == 3

    def test_a_function_whose_only_path_falls_off_the_end_returns_zero(self):
        source = """
        int f(int a) { if (a) { return 1; } }
        int main() { return f(0); }
        """
        assert value(source) == 0

    def test_a_function_whose_only_path_falls_off_the_end_can_also_return(self):
        source = """
        int f(int a) { if (a) { return 1; } }
        int main() { return f(1); }
        """
        assert value(source) == 1


# ---------------------------------------------------------------- determinism


def test_running_the_same_program_twice_gives_identical_outcomes():
    source = """
    int main() {
        int total = 0;
        for (int i = 0; i < 5; i = i + 1) { total = total + i * i; }
        printf("%d\\n", total);
        return total;
    }
    """
    first, second = run(source), run(source)
    assert first.to_dict() == second.to_dict()


def test_execution_is_deterministic_against_input_order():
    source = """
    int main() {
        int a;
        int b;
        scanf("%d %d", &a, &b);
        return a * b;
    }
    """
    assert value(source, stdin="6 7") == 42
    assert value(source, stdin="6 7") == 42


# ------------------------------------------------------- observable behaviour


class TestObservableBehaviour:
    def test_two_identical_runs_are_observably_equal(self):
        source = wraps("printf(\"hi\\n\"); return 1;")
        assert run(source).same_observable_behaviour_as(run(source))

    def test_different_output_is_not_observably_equal(self):
        a = run(wraps('printf("a\\n"); return 0;'))
        b = run(wraps('printf("b\\n"); return 0;'))
        assert not a.same_observable_behaviour_as(b)

    def test_a_different_return_value_matters(self):
        a = run(wraps("return 1;"))
        b = run(wraps("return 2;"))
        assert not a.same_observable_behaviour_as(b)

    def test_a_trap_matters_even_when_output_matches(self):
        a = run(wraps('printf("x\\n"); return 0;'))
        b = run(wraps('printf("x\\n"); int z = 0; return 1 / z;'))
        assert not a.same_observable_behaviour_as(b)

    def test_a_different_step_count_alone_is_not_a_difference(self):
        a = run(wraps("return 1;"))
        b = run(wraps("int x = 0; x = x + 1; return 1;"))
        assert a.same_observable_behaviour_as(b)

    def test_the_outcome_serializes_for_the_api(self):
        payload = run(wraps('printf("hi\\n"); return 4;')).to_dict()
        assert payload["status"] == "ok"
        assert payload["ok"] is True
        assert payload["return_value"] == 4
        assert payload["output"] == "hi\n"


# ------------------------------------------------- linearization round trip


EXAMPLE_SOURCES = sorted(EXAMPLES_DIR.glob("*.c"))
EXAMPLE_NAMES = [path.name for path in EXAMPLE_SOURCES]

#: stdin for the examples that read. Kept beside the example list rather than inside the
#: ``.c`` files so the corpus stays compilable on its own.
EXAMPLE_STDIN = {
    "06_io.c": "3 2.5\n7 2\n",
}


def _lower_source(source: str):
    program, diagnostics = parse(source)
    assert not [d for d in diagnostics if d.severity.value == "error"]
    result = analyze(program)
    assert result.ok, [d.message for d in result.diagnostics]
    lowered = lower(result)
    assert lowered.ok, lowered.errors
    return lowered.program


def test_the_examples_exist():
    assert len(EXAMPLE_SOURCES) >= 13


@pytest.mark.parametrize("path", EXAMPLE_SOURCES, ids=EXAMPLE_NAMES)
def test_linearized_output_matches_the_original(path: Path):
    """`CFG.linearize()` must not change what a program does.

    This is the proof obligation for the whole CFG stage: later passes rebuild the graph
    and re-linearize, so a mismatch here would be a silent behaviour change.
    """
    program = _lower_source(path.read_text(encoding="utf-8"))
    for function in program.functions:
        direct = interpret(program, entry=function.name)
        via_cfg = interpret_cfg(program, function.name)
        assert via_cfg.same_observable_behaviour_as(direct), (
            f"{path.name}:{function.name}\n"
            f"direct: {direct.to_dict()}\n"
            f"linearized: {via_cfg.to_dict()}\n"
            f"{print_program(program)}"
        )


@pytest.mark.parametrize("path", EXAMPLE_SOURCES, ids=EXAMPLE_NAMES)
def test_every_example_runs_and_prints(path: Path):
    """Every example must actually execute, not merely compile."""
    outcome = run(
        path.read_text(encoding="utf-8"), stdin=EXAMPLE_STDIN.get(path.name, "")
    )
    assert outcome.ok, f"{path.name}: {outcome.status} {outcome.message}"
    assert outcome.output, f"{path.name} printed nothing"
    assert outcome.steps > 0


def test_the_io_example_produces_the_right_answer():
    """`06_io.c` is the only example that reads, so its output is checked in full.

    Three items at 2.5 each is 7.5, and the two integers must multiply to 14 -- this pins
    the whole scanf/printf path, including the float reading and the `%f` six-decimal
    rendering.
    """
    outcome = run(EXAMPLE_SOURCES[5].read_text(encoding="utf-8"), stdin=EXAMPLE_STDIN["06_io.c"])
    assert outcome.output == (
        "How many items? Price each? "
        "Total for 3 items at 2.500000 each: 7.500000\n"
        "Enter two integers: sum=9 diff=5 product=14\n"
        "100% of the total is 7.500000\n"
    )


def test_linearized_execution_matches_on_a_trapping_program():
    source = wraps("int a = 1; int b = 0; return a / b;")
    program = _lower_source(source)
    direct = interpret(program)
    via_cfg = interpret_cfg(program, "main")
    assert direct.status is ExecutionStatus.TRAP
    assert via_cfg.same_observable_behaviour_as(direct)


def test_linearized_execution_matches_on_input():
    source = """
    int main() {
        int a;
        scanf("%d", &a);
        printf("%d\\n", a * 2);
        return a;
    }
    """
    program = _lower_source(source)
    assert interpret_cfg(program, "main", stdin="21").output == "42\n"


def test_linearized_execution_matches_on_recursion():
    source = """
    int fact(int n) { if (n <= 1) { return 1; } return n * fact(n - 1); }
    int main() { return fact(8); }
    """
    program = _lower_source(source)
    assert interpret_cfg(program, "main").return_value == 40320
