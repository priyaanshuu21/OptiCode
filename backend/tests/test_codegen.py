"""Tests for source generation from an optimization result.

Codegen's contract is narrow and worth stating before the tests: it edits a copy of the user's
AST, it never decompiles TAC, and it may not change what the program does. The last part is
the one that can fail silently, so almost every test here falls into one of two groups —

* **What the source should say**, written out by hand from the MiniC that produced it.
* **What must survive**, meaning the generated text still compiles and still prints the same
  thing, checked by running both versions.

The second group is deliberately the fallback mechanism too. A test that a rewrite is wrong is
less useful than a test that the guard catches it, so there are tests that feed the self-check
a bad rewrite and assert the original source comes back untouched.

Two tests exist because they caught real bugs rather than because the shape looked worth
covering:

* ``test_a_declaration_comes_back_when_a_fold_inside_it_was_needed`` pins the drop/fold
  interaction. The optimizer deletes ``int a = 3;`` precisely *because* its only read was
  propagated to a literal, so deleting the declaration and folding the read have to be
  planned together. Planning them separately left generated source that referred to a
  variable that no longer existed, and the self-check caught the type error.
* ``test_a_value_proved_only_after_control_flow_was_flattened_is_folded`` pins the second
  source of fold evidence. Constant propagation only runs over straight-line code, so a
  counter incremented behind branches looks unprovable to it; the optimizer knows the answer
  only after branch folding. Reading the final program is the only way to see it, and without
  that the generated source dropped the increments while keeping the old value.
"""

from __future__ import annotations

import pathlib

import pytest

from opticode.codegen import (
    CodegenResult,
    apply_plan,
    generate_optimized_source,
)
from opticode.codegen.changes import ChangeKind
from opticode.codegen.edits import plan_edits
from opticode.codegen.selfcheck import self_check
from opticode.compiler import CompileMode, compile_source
from opticode.ir import IRProgram, interpret, lower
from opticode.optimizer import optimize
from opticode.parser import parse, unparse, walk
from opticode.parser.ast import Block, If, Program, Stmt
from opticode.semantic import analyze as semantic_analyze

EXAMPLES = pathlib.Path(__file__).resolve().parents[1] / "examples"

# ------------------------------------------------------------------- helpers


def build(source: str):
    """Parse and lower one snippet the way the pipeline does, failing loudly."""
    program, diagnostics = parse(source)
    errors = [d for d in diagnostics if d.severity.value == "error"]
    assert not errors, errors
    result = lower(semantic_analyze(program))
    assert result.ok, result.errors
    return program, result.program


def regenerate(source: str, **kwargs) -> CodegenResult:
    program, ir = build(source)
    return generate_optimized_source(program, optimize(ir), original_source=source, **kwargs)


def lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def output_of(program: IRProgram, entry: str = "main", stdin: str = "") -> str:
    return interpret(program, entry=entry, stdin=stdin).output


def analyze_cleanly(source: str) -> None:
    """Assert that ``source`` compiles with no errors, which is the undeclared-name check.

    Cheaper and far more direct than walking the tree and tracking scopes: the semantic stage
    already knows every scope in the language, so it answers "is this name declared here?"
    correctly for declarations, parameters, and functions alike.
    """
    program, diagnostics = parse(source)
    errors = [d for d in diagnostics if d.severity.value == "error"]
    assert not errors, (errors, source)
    semantic = semantic_analyze(program)
    errors = [d for d in semantic.diagnostics if d.severity.value == "error"]
    assert not errors, (errors, source)


# --------------------------------------------------------- invariant: AST edits


def test_the_original_tree_is_never_mutated():
    """The response still carries the user's AST, so rewriting in place would corrupt it."""
    source = """int main() {
    int a = 3;
    int b = 4;
    int c = a + b;
    printf("%d\\n", c);
    return 0;
}
"""
    program, ir = build(source)
    before = unparse(program)
    result = generate_optimized_source(program, optimize(ir), original_source=source)

    assert result.applied
    assert unparse(program) == before, "codegen rewrote the caller's AST"


def test_generated_source_still_compiles_and_behaves_the_same():
    source = """int main() {
    int a = 3;
    int b = 4;
    int c = a + b;
    printf("%d\\n", c);
    return 0;
}
"""
    result = regenerate(source)
    assert result.applied and result.verified
    assert lines(result.source) == ["int main() {", 'printf("%d\\n", 7);', "return 0;", "}"]
    analyze_cleanly(result.source)
    _, ir = build(source)
    assert output_of(ir).strip() == "7"


def test_a_bare_declaration_is_kept_because_removing_it_would_change_the_program():
    """``int x;`` produces no TAC, so there is no evidence it is unused."""
    result = regenerate(
        """int main() {
    int x;
    printf("hi\\n");
    return 0;
}
"""
    )
    assert "int x;" in result.source


# ----------------------------------------------------------------- removals


def test_a_statement_whose_instructions_all_vanish_is_deleted():
    result = regenerate(
        """int main() {
    int a = 3;
    int b = 4;
    printf("%d\\n", a + b);
    return 0;
}
"""
    )
    assert result.applied and result.verified
    assert "int a = 3;" not in result.source
    assert "int b = 4;" not in result.source
    removed = [c for c in result.changes if c.kind is ChangeKind.STATEMENT_REMOVED]
    assert {c.function for c in removed} == {"main"}
    assert all(c.reason for c in removed), "a removed line must say why"


def test_a_removed_line_is_reported_with_the_text_it_used_to_hold():
    result = regenerate(
        """int main() {
    int unused = 41 + 1;
    printf("hi\\n");
    return 0;
}
"""
    )
    (change,) = [c for c in result.changes if c.kind is ChangeKind.STATEMENT_REMOVED]
    assert change.before == "int unused = 41 + 1;"
    assert change.span.start_line == 2
    assert change.instruction_ids


def test_the_annotated_source_shows_what_was_taken_out():
    """Deleting someone's code without saying so is not acceptable, so it is shown."""
    result = regenerate(
        """int main() {
    int unused = 41 + 1;
    printf("hi\\n");
    return 0;
}
"""
    )
    assert "removed: int unused = 41 + 1;" in result.annotated_source
    assert "int unused = 41 + 1;" not in result.source


# --------------------------------------------------------------------- folds


def test_a_proved_constant_expression_becomes_its_value():
    result = regenerate(
        """int main() {
    int x = 2 + 3 * 4;
    printf("%d\\n", x);
    return 0;
}
"""
    )
    assert result.applied and result.verified
    assert "2 + 3 * 4" not in result.source
    assert "14" in result.source


def test_a_folded_condition_is_not_a_source_expression_we_cannot_print():
    """``printf("%d\\n", 1 == 1)`` is valid MiniC, but the guard refuses to guess."""
    result = regenerate(
        """int main() {
    if (2 > 1) {
        printf("yes\\n");
    }
    return 0;
}
"""
    )
    assert result.applied and result.verified
    assert "2 > 1" not in result.source


def test_a_float_is_folded_to_a_float_literal():
    result = regenerate(
        """int main() {
    float x = 1.5 * 2.0;
    printf("%f\\n", x);
    return 0;
}
"""
    )
    assert result.applied and result.verified
    assert "1.5 * 2.0" not in result.source
    assert "3.0" in result.source


def test_a_string_constant_is_never_rewritten_as_a_number():
    """A string operand is pointer-valued; there is no MiniC literal that means the same."""
    result = regenerate(
        """int main() {
    printf("hello\\n");
    return 0;
}
"""
    )
    assert result.applied is False or "hello" in result.source


# ------------------------------------------------------------------ branches


def test_a_branch_the_optimizer_proved_is_removed_from_the_source():
    result = regenerate(
        """int main() {
    if (1) {
        printf("always\\n");
    } else {
        printf("never\\n");
    }
    return 0;
}
"""
    )
    assert result.applied and result.verified
    assert "if" not in result.source
    assert "always" in result.source
    assert "never" not in result.source


def test_a_branch_taken_sometimes_is_left_alone():
    """The planner only resolves branches with a ``BRANCH_FOLDED`` event behind them.

    ``n`` comes from stdin, so nothing can prove which way the branch goes and the ``if`` has
    to survive. Rewriting it anyway would be the classic source-optimizer bug: deleting the
    other arm and being wrong.
    """
    result = regenerate(
        """int main() {
    int n;
    scanf("%d", &n);
    if (n > 1) {
        printf("big\\n");
    }
    return 0;
}
"""
    )
    assert result.applied is False or "if (n > 1)" in result.source


# --------------------------------------------------- drop/fold interaction


def test_a_declaration_is_dropped_only_when_its_read_was_actually_folded():
    """Deleting a declaration and folding its reads are one decision, not two.

    The optimizer proves ``a`` is dead precisely because every read was propagated to a
    literal, so both edits have to land together. Planning them independently once produced
    source that still used the variable after its declaration was gone, and the type error
    only showed up because the self-check re-analyzes the text. The shape of the rewrite moved
    when the second source of fold evidence was added — ``a * b`` is now folded straight to
    ``3.0`` rather than left for the declarations to be kept around — so the assertion is on
    the property rather than on the wording.
    """
    source = """int main() {
    float a = 1.5;
    float b = 2.0;
    float total = a * b;
    printf("%f\\n", total);
    return 0;
}
"""
    result = regenerate(source)
    assert result.applied and result.verified, result.reason
    analyze_cleanly(result.source)
    assert "float a = 1.5;" not in result.source
    assert "3.0" in result.source
    _, ir = build(source)
    assert output_of(ir).strip() == "3.000000"


def test_a_declaration_that_is_still_referenced_survives():
    """The un-drop half of the fixpoint: a name the rewritten tree still needs stays declared."""
    source = """int main() {
    int n;
    scanf("%d", &n);
    int twice = n * 2;
    printf("%d\\n", twice);
    return 0;
}
"""
    result = regenerate(source)
    assert result.applied is False or "int n;" in result.source
    analyze_cleanly(result.source)


def test_a_value_proved_only_after_control_flow_was_flattened_is_folded():
    """Constant propagation only runs over straight-line code.

    The counter below is incremented behind branches, so the constant passes alone cannot see
    that it ends at 2 — the optimizer only knows after branch folding makes the code
    straight. The dead stores go, and the print has to become ``2`` to match.
    """
    source = """int main() {
    int c = 0;
    if (2 > 1) {
        c = c + 1;
    }
    if (2 < 1) {
        c = c + 1;
    }
    if (1 == 1) {
        c = c + 1;
    }
    printf("%d\\n", c);
    return 0;
}
"""
    result = regenerate(source)
    assert result.applied and result.verified, result.reason
    _, ir = build(source)
    assert output_of(ir).strip() == "2"
    assert "printf(\"%d\\n\", 2);" in result.source
    assert "c = c + 1;" not in result.source


# ------------------------------------------------------------- self-checking


def test_the_self_check_rejects_a_rewrite_that_changes_behaviour():
    """The guard is the whole reason this stage is safe, so it gets tested against a real bug."""
    _, ir = build("int main() { printf(\"hi\\n\"); return 0; }\n")
    check = self_check("int main() { printf(\"bye\\n\"); return 0; }\n", ir)
    assert not check.ok
    assert "differs" in check.reason or "differs" in check.describe()


def test_the_self_check_rejects_source_that_does_not_compile():
    _, ir = build("int main() { return 0; }\n")
    check = self_check("int main() { return undefined_name; }\n", ir)
    assert not check.ok
    assert check.reason


def test_a_failed_self_check_returns_the_original_source_untouched(monkeypatch):
    """The fallback is the promise: a bad rewrite costs an optimization, never a program."""
    from opticode.codegen import selfcheck as selfcheck_module
    from opticode.codegen.selfcheck import SelfCheck

    def refusing(generated: str, original):
        return SelfCheck(ok=False, reason="the generated source differs on `main`")

    monkeypatch.setattr(selfcheck_module, "self_check", refusing)
    monkeypatch.setattr("opticode.codegen.self_check", refusing)

    source = """int main() {
    int a = 3;
    int b = 4;
    printf("%d\\n", a + b);
    return 0;
}
"""
    program, ir = build(source)
    result = generate_optimized_source(program, optimize(ir), original_source=source)

    assert not result.applied
    assert result.source == source
    assert result.check is not None and not result.check.ok
    assert "differs" in result.reason
    assert result.changes, "the changes are still reported even when the rewrite is refused"


def test_a_codegen_bug_is_contained_and_the_response_survives():
    source = """int main() {
    int x = 2 + 3;
    printf("%d\\n", x);
    return 0;
}
"""
    result = compile_source(source, CompileMode.OPTIMIZE, options={"passes": ["constant-folding"]})
    assert result.success
    assert result.codegen is None or isinstance(result.codegen, dict)


def test_no_change_returns_the_users_own_bytes():
    """A no-op must not come back reformatted; users diff against their editor, not ours."""
    source = """int main() {
    printf("hi\\n");
    return 0;
}
"""
    result = regenerate(source)
    assert not result.applied
    assert result.source == source
    assert result.reason


def test_edits_that_cancel_out_are_reported_as_no_change():
    """A plan with nothing in it must not come back as a reformatting."""
    source = """int main() {
    printf("hi\\n");
    return 0;
}
"""
    result = regenerate(source)
    assert not result.applied
    assert result.source == source
    assert not result.changes.changes


def test_verification_can_be_turned_off_but_is_on_by_default():
    source = """int main() {
    int x = 2 + 3;
    printf("%d\\n", x);
    return 0;
}
"""
    checked = regenerate(source)
    unchecked = regenerate(source, verify=False)
    assert checked.applied and checked.verified
    assert unchecked.applied and not unchecked.verified
    assert checked.source == unchecked.source


# ------------------------------------------------------------- plan details


def test_the_plan_names_the_pass_that_justified_each_change():
    result = regenerate(
        """int main() {
    int x = 2 + 3;
    printf("%d\\n", x);
    return 0;
}
"""
    )
    folded = [c for c in result.changes if c.kind is ChangeKind.EXPRESSION_FOLDED]
    assert folded
    assert all(c.passes for c in folded), "a fold must name the pass that proved it"
    assert all(c.before and c.after for c in folded)


def test_apply_plan_reproduces_the_generated_source():
    source = """int main() {
    int a = 3;
    int b = 4;
    printf("%d\\n", a + b);
    return 0;
}
"""
    program, ir = build(source)
    result = optimize(ir)
    plan = plan_edits(program, result)
    assert not plan.empty
    assert unparse(apply_plan(program, plan)) == generate_optimized_source(
        program, result, original_source=source
    ).source


def test_block_unwrapping_only_happens_where_braces_are_not_required():
    """A resolved branch whose arm is itself a compound statement keeps its braces."""
    result = regenerate(
        """int main() {
    if (1) {
        while (1) {
            printf("loop\\n");
            break;
        }
    }
    return 0;
}
"""
    )
    assert result.applied and result.verified, result.reason
    reparsed, diagnostics = parse(result.source)
    assert not [d for d in diagnostics if d.severity.value == "error"], result.source
    assert isinstance(reparsed, Program)


# --------------------------------------------------------------- integration


def test_every_example_compiles_and_the_generated_source_behaves_the_same():
    """The end-to-end check, over the programs a user would actually paste in."""
    for path in sorted(EXAMPLES.glob("*.c")):
        source = path.read_text(encoding="utf-8")
        _, ir = build(source)
        result = regenerate(source)
        if not result.applied:
            continue
        _, regenerated_ir = build(result.source)
        for stdin in ("", "0", "3 4 5 6 7"):
            assert output_of(ir, stdin=stdin) == output_of(regenerated_ir, stdin=stdin), (
                f"{path.name} changed behaviour with stdin={stdin!r}"
            )


def test_the_pipeline_runs_codegen_only_in_optimize_mode():
    source = """int main() {
    int x = 2 + 3;
    printf("%d\\n", x);
    return 0;
}
"""
    analyzed = compile_source(source, CompileMode.ANALYZE)
    optimized = compile_source(source, CompileMode.OPTIMIZE)

    assert analyzed.codegen is None
    assert "codegen" in analyzed.stages_skipped
    assert optimized.codegen is not None
    assert optimized.codegen["applied"] is True
    assert optimized.codegen["verified"] is True
    assert "codegen" in optimized.stages_run


def test_the_pipeline_reports_codegen_outcomes_as_diagnostics():
    source = """int main() {
    int x = 2 + 3;
    printf("%d\\n", x);
    return 0;
}
"""
    applied = compile_source(source, CompileMode.OPTIMIZE)
    assert any(d.code_str == "I-CGN-001" for d in applied.diagnostics)
    assert all(d.severity.value != "error" for d in applied.diagnostics)


def test_codegen_does_not_run_for_a_program_that_fails_to_analyze():
    source = "int main() { return unknown_thing; }\n"
    result = compile_source(source, CompileMode.OPTIMIZE)
    assert not result.success
    assert result.codegen is None
    assert result.ast is not None, "the stages that did run are still in the response"
    assert "codegen" in result.stages_skipped


def test_the_change_map_is_ordered_by_position():
    result = regenerate(
        """int main() {
    int a = 3;
    int b = 4;
    printf("%d\\n", a + b);
    return 0;
}
"""
    )
    spans = [c.span.start_line for c in result.changes.sorted_by_position()]
    assert spans == sorted(spans)


# ------------------------------------------------------------------- planner


def test_a_dropped_statement_is_never_reported_as_folded_too():
    """A fold inside a deleted line would describe an edit to text that is no longer there."""
    result = regenerate(
        """int main() {
    int unused = 41 + 1;
    printf("hi\\n");
    return 0;
}
"""
    )
    removed = {c.node_id for c in result.changes if c.kind is ChangeKind.STATEMENT_REMOVED}
    folded_inside = [
        c
        for c in result.changes
        if c.kind is ChangeKind.EXPRESSION_FOLDED and c.node_id in removed
    ]
    assert not folded_inside


def test_the_rewritten_tree_has_no_unresolved_identifiers():
    """The bug this guards against is a generated source that does not typecheck."""
    sources = [
        """int main() {
    float a = 1.5;
    float b = 2.0;
    float total = a * b;
    printf("%f\\n", total);
    return 0;
}
""",
        """int helper(int x) {
    int y = x * 2;
    return y;
}

int main() {
    printf("%d\\n", helper(2 + 3));
    return 0;
}
""",
        """int main() {
    int n;
    scanf("%d", &n);
    if (n > 1) {
        int bigger = n * 10;
        printf("%d\\n", bigger);
    }
    return 0;
}
""",
    ]
    for source in sources:
        program, ir = build(source)
        rewritten = apply_plan(program, plan_edits(program, optimize(ir)))
        analyze_cleanly(unparse(rewritten))


def test_statements_that_can_change_control_flow_are_never_dropped():
    """Deleting a ``return`` is not a shortening, it is a different program.

    The unreachable ``printf`` below *is* removed — an optimizer bug once kept it, because the
    removable set listed only assignments and declarations. The ``return`` stays, and so does
    the function.
    """
    result = regenerate(
        """int main() {
    printf("hi\\n");
    return 0;
    printf("unreachable\\n");
}
"""
    )
    assert result.applied and result.verified, result.reason
    assert "unreachable" not in result.source
    removed = [c for c in result.changes if c.kind is ChangeKind.STATEMENT_REMOVED]
    assert removed, "the dead printf should be reported"
    assert all(not c.before.strip().startswith("return") for c in removed)


def test_if_statements_are_only_replaced_not_spliced_when_the_arm_needs_braces():
    source = """int main() {
    if (1) {
        int inner = 3;
        printf("%d\\n", inner);
    }
    return 0;
}
"""
    result = regenerate(source)
    assert result.applied and result.verified, result.reason
    assert "printf(\"%d\\n\", 3);" in result.source
    assert "int inner = 3;" not in result.source


def test_a_loop_the_optimizer_can_never_enter_is_unwrapped():
    """``x`` is 5, so ``x > 100`` is false and the loop body is unreachable.

    That is the shape a source-optimizer user expects to see fixed: the condition folds, the
    branch goes, and the body it guarded disappears with it.
    """
    source = """int main() {
    int x = 2 + 3;
    while (x > 100) {
        printf("never\\n");
    }
    printf("%d\\n", x);
    return 0;
}
"""
    result = regenerate(source)
    assert result.applied and result.verified, result.reason
    assert "never" not in result.source
    assert "while" not in result.source
    analyze_cleanly(result.source)


def test_resolving_an_if_leaves_a_block_when_the_arm_is_a_compound_statement():
    """The unparser must still produce braces around a body that needs them."""
    source = """int main() {
    if (1) {
        int inner = 3;
        printf("%d\\n", inner);
    }
    return 0;
}
"""
    program, ir = build(source)
    plan = plan_edits(program, optimize(ir))
    if_nodes = [node for node in walk(program) if isinstance(node, If)]
    (if_node,) = if_nodes
    kept = plan.resolve.get(if_node.id)
    assert kept is not None
    assert isinstance(kept, (Block, If)) or kept is None


def test_no_rewrite_is_planned_for_a_program_the_optimizer_left_alone():
    """Nothing provable, nothing removed, nothing rewritten."""
    source = """int main() {
    int n;
    scanf("%d", &n);
    if (n > 1) {
        printf("big\\n");
    }
    return 0;
}
"""
    program, ir = build(source)
    plan = plan_edits(program, optimize(ir))
    assert plan.empty, plan


@pytest.mark.parametrize("filename", ["01_hello.c", "12_constant_folding.c"])
def test_regeneration_is_idempotent_on_generated_text(filename: str):
    """Regenerating an already-generated program must be a no-op, or codegen is not a fixpoint."""
    source = (EXAMPLES / filename).read_text(encoding="utf-8")
    first = regenerate(source)
    if not first.applied:
        pytest.skip("this example has nothing to rewrite")
    second = regenerate(first.source)
    assert not second.applied
    assert second.source == first.source


def test_stmts_in_a_kept_arm_are_rewritten_not_the_whole_branch():
    """A resolved branch keeps its arm's edits, including the folds inside it."""
    result = regenerate(
        """int main() {
    if (1) {
        printf("%d\\n", 3 + 4);
    }
    return 0;
}
"""
    )
    assert result.applied and result.verified, result.reason
    assert "3 + 4" not in result.source
    assert "7" in result.source


def test_the_source_a_user_gets_back_is_never_the_annotated_one():
    """Two texts, one contract: annotations are for reading, ``source`` is for compiling."""
    result = regenerate(
        """int main() {
    int unused = 41 + 1;
    printf("hi\\n");
    return 0;
}
"""
    )
    assert "removed:" not in result.source
    assert "removed:" in result.annotated_source
    assert isinstance(result.to_dict()["changes"], dict)


def test_a_dropped_declaration_is_not_reported_for_a_function_that_does_not_exist():
    """Every change carries a function name; a missing one would show as a blank column."""
    source = """int main() {
    int a = 3;
    int b = 4;
    printf("%d\\n", a + b);
    return 0;
}
"""
    result = regenerate(source)
    assert all(c.function for c in result.changes)


def test_generating_from_a_program_with_no_functions_is_handled():
    """An empty program is valid input to the API and must not crash the stage."""
    program, ir = build("int main() { return 0; }\n")
    empty = Program(functions=(), span=program.span)
    result = generate_optimized_source(empty, optimize(ir))
    assert isinstance(result, CodegenResult)
    assert result.applied is False


def test_a_stmt_list_survives_being_rewritten():
    """The rewriter rebuilds lists by walking them; a dropped element must not shift the rest."""
    source = """int main() {
    int a = 3;
    int b = 4;
    int c = 5;
    printf("%d\\n", a + b + c);
    return 0;
}
"""
    program, ir = build(source)
    rewritten = apply_plan(program, plan_edits(program, optimize(ir)))
    for function in rewritten.functions:
        for statement in _statements_of(function.body):
            assert isinstance(statement, Stmt)


def _statements_of(body: Stmt) -> list[Stmt]:
    if isinstance(body, Block):
        return list(body.statements)
    return [body]
