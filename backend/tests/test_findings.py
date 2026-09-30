"""Hand-verified dead-code and unreachable-code tests.

Every expected finding in this file is written out by hand from the MiniC source that
produced it, not read back out of the analysis. A test that derives its expectation with
the same walk as the code under test proves only that the code agrees with itself.

The shapes these cover are the ones the rules can get wrong in the two directions that
matter: claiming code is dead when it is not (a false "you can delete this"), and claiming
code is removable when deleting it would be observable (a dropped call, a dropped read, a
dropped division check).
"""

from __future__ import annotations

import json
from pathlib import Path

from opticode.analysis import analyze_findings, analyze_functions
from opticode.analysis.findings import (
    AnalysisReport,
    Finding,
    FindingAction,
    FindingKind,
    diagnostics_for,
)
from opticode.cfg import build_program_cfgs
from opticode.compiler import compile_source
from opticode.ir import lower
from opticode.parser import parse
from opticode.semantic import analyze as semantic_analyze

#: The compiler's own regression corpus, the same one ``test_examples.py`` walks.
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "valid"


# ------------------------------------------------------------------- helpers


def _report(source: str) -> AnalysisReport:
    """Findings for one snippet, from the entry point the pipeline stage calls.

    The AST and source text are passed the way ``_stage_analysis`` passes them, so these
    tests see the same explanations and evidence spans the API returns.
    """
    program, diagnostics = parse(source)
    assert not [d for d in diagnostics if d.severity.value == "error"], diagnostics
    result = semantic_analyze(program)
    assert not [d for d in result.diagnostics if d.severity.value == "error"]
    lowered = lower(result)
    assert lowered.ok, lowered.errors
    graphs = build_program_cfgs(lowered.program)
    return analyze_findings(
        lowered.program,
        cfgs=graphs,
        analyses=analyze_functions(lowered.program, cfgs=graphs),
        ast=program,
        source=source,
    )


def _kinds(source: str) -> set[str]:
    return {item.kind.value for item in _report(source).findings}


def _only(report: AnalysisReport, kind: FindingKind) -> Finding:
    found = report.of_kind(kind)
    assert len(found) == 1, f"expected one {kind.value}, got {[f.message for f in found]}"
    return found[0]


def _codes(source: str) -> list[str]:
    result = compile_source(source)
    return [item.code_str for item in result.diagnostics]


def _analyze_warnings(source: str) -> list[str]:
    return [code for code in _codes(source) if code.startswith("W-ANL")]


# ------------------------------------------------------- structural unreachable


def test_code_after_a_return_is_unreachable():
    report = _report(
        "int f() {\n"
        "    int x = 1;\n"
        "    return x;\n"
        "    int dead = 2;\n"
        "    return dead;\n"
        "}\n"
    )
    found = report.of_kind(FindingKind.UNREACHABLE_CODE)
    assert [item.span.start_line for item in found] == [4, 5]
    for item in found:
        assert item.severity.value == "warning"
        assert item.action is FindingAction.WILL_BE_REMOVED
        assert item.block == "B2"
        assert item.function == "f"
    assert "unreachable" in found[0].message


def test_the_unreachable_explanation_quotes_the_return_that_cut_the_path():
    finding = _only(
        _report("int f() {\n    return 0;\n    int dead = 2;\n}\n"),
        FindingKind.UNREACHABLE_CODE,
    )
    assert "`return 0;`" in finding.explanation
    assert "line 2" in finding.explanation
    evidence = finding.evidence[0]
    assert evidence.span.start_line == 2
    assert "ends the flow" in evidence.note


def test_code_after_a_break_is_unreachable():
    report = _report(
        "int f() {\n"
        "    int i = 0;\n"
        "    while (i < 3) {\n"
        "        i = i + 1;\n"
        "        break;\n"
        "        int dead = 1;\n"
        "    }\n"
        "    return i;\n"
        "}\n"
    )
    assert FindingKind.UNREACHABLE_CODE in {item.kind for item in report.findings}
    finding = next(
        item for item in report.of_kind(FindingKind.UNREACHABLE_CODE) if item.span.start_line == 6
    )
    assert "`break`" in finding.explanation


def test_code_after_a_continue_is_unreachable():
    report = _report(
        "int f() {\n"
        "    int i = 0;\n"
        "    while (i < 3) {\n"
        "        i = i + 1;\n"
        "        continue;\n"
        "        int dead = 2;\n"
        "    }\n"
        "    return i;\n"
        "}\n"
    )
    finding = next(
        item for item in report.of_kind(FindingKind.UNREACHABLE_CODE) if item.span.start_line == 6
    )
    assert "`continue`" in finding.explanation


def test_an_infinite_loop_does_not_hide_code_the_break_can_reach():
    """`while (1)` makes one edge infeasible; the `break` keeps the rest reachable."""
    report = _report(
        "int f() {\n"
        "    int i = 0;\n"
        "    while (1) {\n"
        "        i = i + 1;\n"
        "        if (i > 3) {\n"
        "            break;\n"
        "        }\n"
        "    }\n"
        "    return i;\n"
        "}\n"
    )
    assert FindingKind.CONDITIONALLY_UNREACHABLE not in {item.kind for item in report.findings}
    assert report.unreachable_blocks == ()


def test_a_nested_break_reaches_the_code_after_the_inner_loop():
    """The inner loop's false edge is dead, but the break makes the outer body reachable."""
    report = _report(
        "int f() {\n"
        "    int i = 0;\n"
        "    while (i < 3) {\n"
        "        while (i < 5) {\n"
        "            break;\n"
        "        }\n"
        "        int after = 3;\n"
        "    }\n"
        "    return i;\n"
        "}\n"
    )
    finding = next(
        item
        for item in report.of_kind(FindingKind.DEAD_ASSIGNMENT)
        if item.span.start_line == 7
    )
    assert "`after`" in finding.message


# --------------------------------------------------- conditional unreachability


def test_if_zero_body_is_conditionally_unreachable():
    report = _report("int f() {\n    if (0) {\n        int dead = 1;\n    }\n    return 0;\n}\n")
    finding = _only(report, FindingKind.CONDITIONALLY_UNREACHABLE)
    assert "constant 0" in finding.explanation
    assert "never taken" in finding.explanation
    assert report.unreachable_blocks == ("f:B2",)


def test_while_zero_body_is_conditionally_unreachable():
    report = _report(
        "int f() {\n    int i = 0;\n    while (0) {\n        i = i + 1;\n    }\n    return i;\n}\n"
    )
    finding = _only(report, FindingKind.CONDITIONALLY_UNREACHABLE)
    assert finding.span.start_line == 4
    assert "constant 0" in finding.explanation


def test_code_after_while_one_without_a_break_is_conditionally_unreachable():
    report = _report(
        "int f() {\n"
        "    int i = 0;\n"
        "    while (1) {\n"
        "        i = i + 1;\n"
        "    }\n"
        "    return i;\n"
        "}\n"
    )
    finding = _only(report, FindingKind.CONDITIONALLY_UNREACHABLE)
    assert finding.span.start_line == 6
    assert "constant 1" in finding.explanation
    assert "never taken" in finding.explanation


def test_the_loop_back_edge_itself_is_not_reported():
    """A `while` whose body never runs is still a `while`; only the body is dead.

    The back edge the lowering inserts carries the loop's own AST node. Reporting it would
    put a warning on a loop the user never broke, so a statement whose instructions are all
    branches is dropped. The constant condition itself is still worth an info note.
    """
    report = _report(
        "int f() {\n    int i = 0;\n    while (0) {\n        i = i + 1;\n    }\n    return i;\n}\n"
    )
    flagged = {
        item.span.start_line
        for item in report.findings
        if item.kind
        in (FindingKind.UNREACHABLE_CODE, FindingKind.CONDITIONALLY_UNREACHABLE)
    }
    assert flagged == {4}, "the body is flagged, the loop header is not"
    assert _only(report, FindingKind.CONSTANT_CONDITION).span.start_line == 3


# ------------------------------------------------------------ dead assignments


def test_an_overwritten_assignment_is_reported():
    report = _report(
        "int f() {\n"
        "    int x = 10;\n"
        "    x = 20;\n"
        "    printf(\"%d\\n\", x);\n"
        "    return 0;\n"
        "}\n"
    )
    finding = _only(report, FindingKind.DEAD_ASSIGNMENT)
    assert "overwritten before it is read" in finding.message
    assert finding.action is FindingAction.WILL_BE_REMOVED
    assert finding.duplicate_of is None, "the semantic stage does not report this case"
    assert finding.span.start_line == 2
    assert "`x` is reassigned here" in finding.evidence[0].note
    assert finding.evidence[0].span.start_line == 3


def test_a_never_read_assignment_is_reported():
    report = _report("int f() {\n    int only_here = 5;\n    return 0;\n}\n")
    finding = _only(report, FindingKind.DEAD_ASSIGNMENT)
    assert "never read" in finding.message
    assert "no later instruction reads `only_here`" in finding.explanation


def test_a_chain_keeps_the_first_assignment_because_the_second_reads_it():
    """`int y = x;` is dead, but `x` is read by it, so whatever defines `x` is still used."""
    report = _report("int f(int x) {\n    int y = x;\n    return 1;\n}\n")
    finding = _only(report, FindingKind.DEAD_ASSIGNMENT)
    assert "`y`" in finding.message
    assert "`x`" not in finding.message


def test_a_self_assignment_is_not_called_overwritten():
    """`x = x` reads itself, which is not evidence that anything else reads it."""
    report = _report("int f(int x) {\n    x = x;\n    return 1;\n}\n")
    finding = _only(report, FindingKind.DEAD_ASSIGNMENT)
    assert "never read" in finding.message


def test_a_loop_carried_value_is_not_dead():
    report = _report(
        "int f(int n) {\n"
        "    int total = 0;\n"
        "    for (int i = 0; i < n; i = i + 1) {\n"
        "        total = total + i;\n"
        "    }\n"
        "    return total;\n"
        "}\n"
    )
    assert _kinds(
        "int f(int n) {\n"
        "    int total = 0;\n"
        "    for (int i = 0; i < n; i = i + 1) {\n"
        "        total = total + i;\n"
        "    }\n"
        "    return total;\n"
        "}\n"
    ) == set()
    assert report.findings == ()


def test_a_store_used_on_only_one_branch_is_not_dead():
    report = _report(
        "int f(int c) {\n"
        "    int x = 0;\n"
        "    if (c) {\n"
        "        x = 1;\n"
        "    }\n"
        "    return x;\n"
        "}\n"
    )
    assert _kinds(
        "int f(int c) {\n"
        "    int x = 0;\n"
        "    if (c) {\n"
        "        x = 1;\n"
        "    }\n"
        "    return x;\n"
        "}\n"
    ) == set()
    assert report.findings == ()


def test_a_store_written_on_both_branches_only_is_dead():
    report = _report(
        "int f(int c) {\n"
        "    int x;\n"
        "    if (c) {\n"
        "        x = 1;\n"
        "    } else {\n"
        "        x = 2;\n"
        "    }\n"
        "    return 0;\n"
        "}\n"
    )
    messages = {item.span.start_line for item in report.of_kind(FindingKind.DEAD_ASSIGNMENT)}
    assert messages == {4, 6}


def test_a_loop_body_value_nothing_reads_is_dead():
    report = _report(
        "int f(int n) {\n"
        "    int total = 0;\n"
        "    for (int i = 0; i < n; i = i + 1) {\n"
        "        int scratch = i * 2;\n"
        "        total = total + i;\n"
        "    }\n"
        "    return total;\n"
        "}\n"
    )
    finding = _only(report, FindingKind.DEAD_ASSIGNMENT)
    assert "`scratch`" in finding.message
    assert "total" in finding.explanation, "the explanation quotes the live set"
    assert finding.span.start_line == 4


def test_no_dead_assignment_inside_unreachable_code():
    """The statement is reported once, as unreachable, not twice."""
    report = _report("int f() {\n    return 0;\n    int dead = 2;\n    return dead;\n}\n")
    assert len(report.of_kind(FindingKind.DEAD_ASSIGNMENT)) == 0
    assert len(report.of_kind(FindingKind.UNREACHABLE_CODE)) == 2


def test_an_infinite_loop_tail_does_not_produce_a_missing_return():
    report = _report("int f() {\n    while (1) {\n    }\n}\n")
    assert report.of_kind(FindingKind.MISSING_RETURN) == ()


# ------------------------------------------------------- kept, not removable


def test_an_unused_call_result_is_kept():
    report = _report("int bar() {\n    return 2;\n}\nint f() {\n    int q = bar();\n    return 1;\n}\n")
    finding = _only(report, FindingKind.UNUSED_CALL_RESULT)
    assert finding.action is FindingAction.KEPT
    assert finding.severity.value == "info"
    assert "side effects" in (finding.kept_reason or "")
    assert report.of_kind(FindingKind.DEAD_ASSIGNMENT) == (), (
        "the unused result is not reported as a dead assignment"
    )


def test_an_unused_read_result_is_kept():
    report = _report("int f() {\n    int s;\n    scanf(\"%d\", &s);\n    return 1;\n}\n")
    finding = _only(report, FindingKind.UNUSED_READ_RESULT)
    assert finding.action is FindingAction.KEPT
    assert "input" in (finding.kept_reason or "")
    assert "consumes input" in finding.explanation


def test_an_assignment_whose_right_side_can_trap_is_kept():
    report = _report("int f(int d) {\n    int z = 3 / d;\n    return 1;\n}\n")
    finding = _only(report, FindingKind.DEAD_ASSIGNMENT)
    assert finding.action is FindingAction.KEPT
    assert "may fail at runtime" in (finding.kept_reason or "")
    assert "trap" in finding.explanation
    assert finding.evidence[0].span.start_line == 2


def test_a_trap_outranks_a_call_in_the_same_statement():
    report = _report("int bar() {\n    return 2;\n}\nint f(int d) {\n    int z = bar() / d;\n    return 1;\n}\n")
    finding = _only(report, FindingKind.DEAD_ASSIGNMENT)
    assert finding.action is FindingAction.KEPT
    assert "may fail at runtime" in (finding.kept_reason or "")


def test_a_division_by_a_nonzero_literal_can_be_removed():
    report = _report("int f() {\n    int z = 8 / 2;\n    return 1;\n}\n")
    finding = _only(report, FindingKind.DEAD_ASSIGNMENT)
    assert finding.action is FindingAction.WILL_BE_REMOVED


# ------------------------------------------------------ other findings


def test_use_before_initialization_is_reported():
    report = _report("int f() {\n    int x;\n    printf(\"%d\\n\", x);\n    return 0;\n}\n")
    finding = _only(report, FindingKind.USE_BEFORE_INITIALIZATION)
    assert "`x` is read before it has a value" == finding.message
    assert "UNDEF(x)" in finding.explanation
    assert finding.span.start_line == 3


def test_missing_return_is_reported_for_a_non_main_function():
    finding = _only(
        _report("int f() {\n    int x = 1;\n}\n"), FindingKind.MISSING_RETURN
    )
    assert "can reach the end without returning int" in finding.message
    assert "undefined" in finding.explanation


def test_main_is_exempt_from_missing_return():
    report = _report("int main() {\n    int x = 1;\n}\n")
    assert report.of_kind(FindingKind.MISSING_RETURN) == ()


def test_a_void_function_is_exempt_from_missing_return():
    report = _report("void f() {\n    int x = 1;\n}\n")
    assert report.of_kind(FindingKind.MISSING_RETURN) == ()


def test_a_fully_returning_function_has_no_missing_return():
    report = _report("int f(int c) {\n    if (c) {\n        return 1;\n    }\n    return 2;\n}\n")
    assert report.of_kind(FindingKind.MISSING_RETURN) == ()


def test_a_constant_condition_is_reported_as_information():
    report = _report("int f(int c) {\n    if (1) {\n        return 0;\n    }\n    return 1;\n}\n")
    finding = _only(report, FindingKind.CONSTANT_CONDITION)
    assert finding.severity.value == "info"
    assert finding.action is FindingAction.INFORMATIONAL
    assert "always true" in finding.message


def test_constant_division_by_zero_is_reported():
    finding = _only(
        _report("int f() {\n    int z = 3 / 0;\n    return z;\n}\n"),
        FindingKind.CONSTANT_DIVISION_BY_ZERO,
    )
    assert "3 / 0" in finding.message
    assert finding.span.start_line == 2


def test_a_division_by_a_runtime_zero_is_not_a_constant_division():
    report = _report("int f(int d) {\n    int z = 3 / d;\n    return z;\n}\n")
    assert report.of_kind(FindingKind.CONSTANT_DIVISION_BY_ZERO) == ()


# --------------------------------------------------------------- diagnostics


def test_unreachable_code_and_overwrites_reach_the_diagnostics_list():
    assert "W-ANL-001" in _analyze_warnings(
        "int f() {\n    return 0;\n    int dead = 2;\n    return dead;\n}\n"
    )
    assert "W-ANL-003" in _analyze_warnings(
        "int f() {\n    int x = 10;\n    x = 20;\n    printf(\"%d\\n\", x);\n    return 0;\n}\n"
    )


def test_conditional_unreachability_and_use_before_init_reach_the_diagnostics_list():
    assert "W-ANL-002" in _analyze_warnings(
        "int f() {\n    if (0) {\n        int dead = 1;\n    }\n    return 0;\n}\n"
    )
    assert "W-ANL-004" in _analyze_warnings(
        "int f() {\n    int x;\n    printf(\"%d\\n\", x);\n    return 0;\n}\n"
    )


def test_the_kept_cases_are_informational_not_warnings():
    result = compile_source("int f() {\n    int s;\n    scanf(\"%d\", &s);\n    return 1;\n}\n")
    codes = [(item.code_str, item.severity.value) for item in result.diagnostics]
    assert ("I-ANL-002", "info") in codes


def test_a_fact_the_semantic_stage_already_reported_is_not_repeated():
    """A never-read variable is W-SEM-002's job; the analysis keeps the precise version."""
    source = "int f() {\n    int only_here = 5;\n    return 0;\n}\n"
    codes = _codes(source)
    assert codes.count("W-SEM-002") == 1
    assert "W-ANL-003" not in codes
    finding = _only(_report(source), FindingKind.DEAD_ASSIGNMENT)
    assert finding.duplicate_of == "W-SEM-002"
    assert finding.explanation


def test_missing_return_is_not_repeated_either():
    codes = _codes("int f() {\n    int x = 1;\n}\n")
    assert codes.count("W-SEM-003") == 1
    assert "W-ANL-005" not in codes


def test_division_by_zero_is_not_repeated_either():
    codes = _codes("int f() {\n    int z = 3 / 0;\n    return z;\n}\n")
    assert codes.count("W-SEM-004") == 1
    assert "W-ANL-006" not in codes


def test_analysis_diagnostics_carry_the_analysis_stage_and_evidence():
    result = compile_source(
        "int f() {\n    int x = 10;\n    x = 20;\n    printf(\"%d\\n\", x);\n    return 0;\n}\n"
    )
    produced = next(
        item for item in result.diagnostics if item.code_str == "W-ANL-003"
    )
    assert produced.stage.value == "analysis"
    assert produced.related, "the reassignment is attached as related information"
    assert "`x` is reassigned here" in produced.related[0].message


def test_diagnostics_for_skips_findings_the_semantic_stage_owns():
    report = _report("int f() {\n    int only_here = 5;\n    return 0;\n}\n")
    assert report.findings
    assert diagnostics_for(report) == []


# ------------------------------------------------------------ report shape


def test_the_payload_round_trips_through_json():
    payload = compile_source("int f() {\n    return 0;\n    int dead = 2;\n}\n").analysis
    assert json.loads(json.dumps(payload)) == payload


def test_the_summary_counts_every_kind_once_per_finding():
    report = _report(
        "int f() {\n"
        "    int x = 10;\n"
        "    x = 20;\n"
        "    printf(\"%d\\n\", x);\n"
        "    return 0;\n"
        "    int dead = 3;\n"
        "    return dead;\n"
        "}\n"
    )
    assert sum(report.summary.values()) == len(report.findings)
    assert report.summary["dead_assignment"] == 1
    assert report.summary["unreachable_code"] == 2


def test_findings_are_sorted_by_position():
    report = _report(
        "int f() {\n"
        "    int a = 1;\n"
        "    int b = 2;\n"
        "    int c = 3;\n"
        "    return 0;\n"
        "}\n"
    )
    starts = [item.span.start_line for item in report.findings]
    assert starts == sorted(starts)


def test_two_runs_agree():
    source = "int f(int c) {\n    int x = 1;\n    if (c) {\n        x = 2;\n    }\n    return x;\n}\n"
    first = compile_source(source).analysis
    second = compile_source(source).analysis
    assert first == second


def test_dead_instruction_ids_cover_every_dead_assignment():
    report = _report("int f() {\n    int x = 10;\n    x = 20;\n    printf(\"%d\\n\", x);\n    return 0;\n}\n")
    finding = _only(report, FindingKind.DEAD_ASSIGNMENT)
    assert report.dead_instruction_ids == finding.instruction_ids


def test_the_examples_report_the_dead_writes_they_documented():
    """`11_dead_assignments.c` exists to be found by a dead-store pass."""
    source = (EXAMPLES_DIR / "11_dead_assignments.c").read_text(encoding="utf-8")
    dead = _report(source).of_kind(FindingKind.DEAD_ASSIGNMENT)
    assert {item.span.start_line for item in dead} == {8, 9, 15, 17, 28}
    assert all(item.action is FindingAction.WILL_BE_REMOVED for item in dead)


def test_the_examples_report_the_unreachable_code_they_documented():
    """`10_unreachable.c` returns from inside `while (1)`; the tail cannot run."""
    source = (EXAMPLES_DIR / "10_unreachable.c").read_text(encoding="utf-8")
    report = _report(source)
    unreachable = report.of_kind(FindingKind.UNREACHABLE_CODE)
    assert {item.span.start_line for item in unreachable} == {9, 10}
    # The `return 0;` after `while (1)` is reachable only if the loop ever exits.
    assert [item.span.start_line for item in report.of_kind(FindingKind.CONDITIONALLY_UNREACHABLE)] == [29]


def test_analyze_findings_runs_without_an_ast_or_source():
    """The optional inputs only make explanations better; the analysis still runs."""
    program, diagnostics = parse("int f() {\n    return 0;\n    int dead = 1;\n    return dead;\n}\n")
    assert not [d for d in diagnostics if d.severity.value == "error"]
    lowered = lower(semantic_analyze(program))
    assert lowered.ok, lowered.errors
    graphs = build_program_cfgs(lowered.program)
    report = analyze_findings(
        lowered.program, cfgs=graphs, analyses=analyze_functions(lowered.program, cfgs=graphs)
    )
    assert len(report.of_kind(FindingKind.UNREACHABLE_CODE)) == 2
    assert all(item.explanation for item in report.findings)


def test_analyze_findings_runs_without_precomputed_analyses():
    """`analyses` is an optimisation; the report must not depend on it being there."""
    program, diagnostics = parse("int f(int c) {\n    int x = 1;\n    if (c) {\n        x = 2;\n    }\n    return x;\n}\n")
    assert not [d for d in diagnostics if d.severity.value == "error"]
    lowered = lower(semantic_analyze(program))
    assert lowered.ok, lowered.errors
    graphs = build_program_cfgs(lowered.program)
    report = analyze_findings(lowered.program, cfgs=graphs)
    assert report.of_kind(FindingKind.DEAD_ASSIGNMENT) == ()
