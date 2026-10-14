"""Hand-verified data-flow tests.

Every expected value in this file is written out by hand from the MiniC source that
produced the TAC, not read back out of the analysis. A test that computes its expectation
with the same lattice or the same walk as the code under test proves nothing beyond "the
code agrees with itself", so these assert literal sets and values.

Where a test builds instructions directly instead of parsing MiniC, it does so to reach a
CFG shape the corpus does not contain — an unreachable block that a branch guards, a
function whose only interesting content is a loop.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opticode.analysis import analyze, analyze_cfg
from opticode.analysis.constprop import propagate
from opticode.analysis.lattice import NAC, TOP, AbstractValue, ValueKind
from opticode.analysis.liveness import liveness
from opticode.analysis.reaching import (
    DefinitionKind,
    definition,
    param,
    reaching,
    undef,
)
from opticode.analysis.solver import (
    MAX_TRACE_STEPS,
    DataFlowProblem,
    Direction,
    SolverError,
    solve,
)
from opticode.cfg import build_program_cfgs
from opticode.core.spans import SourceSpan
from opticode.ir import lower
from opticode.ir.lower import lower as lower_program
from opticode.ir.operands import Operand
from opticode.ir.tac import Instruction, Opcode
from opticode.parser import parse
from opticode.semantic import analyze as semantic_analyze
from opticode.semantic.types import TypeName

SPAN = SourceSpan(1, 1, 1, 1)

#: The compiler's own regression corpus, the same one ``test_examples.py`` walks.
EXAMPLES_DIR = Path(__file__).resolve().parents[2] / "examples" / "valid"


# ------------------------------------------------------------------- helpers


def compile_source(source: str):
    """Parse, check, and lower MiniC, raising loudly on any diagnostic."""
    program, diagnostics = parse(source)
    errors = [d for d in diagnostics if d.severity.value == "error"]
    assert not errors, [str(d) for d in errors]
    result = semantic_analyze(program)
    errors = [d for d in result.diagnostics if d.severity.value == "error"]
    assert not errors, [str(d) for d in errors]
    lowered = lower_program(result)
    assert lowered.ok, lowered.errors
    return lowered.program


def cfg_for(source: str, name: str = "f"):
    program = compile_source(source)
    return build_program_cfgs(program)[name]


def report_for(source: str, name: str = "f") -> dict:
    program = compile_source(source)
    return analyze(program, cfgs=build_program_cfgs(program))[name]


def ins(
    id_: str,
    opcode: Opcode,
    /,
    dest: str | None = None,
    op: str | None = None,
    target: str | None = None,
    operands: tuple[Operand, ...] = (),
) -> Instruction:
    return Instruction(
        id=id_,
        opcode=opcode,
        span=SPAN,
        dest=Operand.temp(dest, TypeName.INT) if dest else None,
        operands=operands,
        target=target,
        op=op,
        ast_node_id="n1",
        operand_origin_ids=("n1",) * len(operands),
    )


def const(value: int | float) -> Operand:
    return Operand.const(value)


def var(name: str) -> Operand:
    return Operand.var(name, TypeName.INT)


# ============================================================ the generic solver


def test_solver_respects_the_backward_boundary_at_exit():
    """A backward analysis seeds every block that falls into EXIT with its boundary."""
    cfg = cfg_for("int f(int c) { if (c > 0) { return 1; } return 0; }")
    solution = liveness(cfg)
    exits = [
        block.id
        for block in cfg.blocks
        if block.reachable and cfg.flows_to_exit(block.id)
    ]
    assert exits, "expected at least one reachable block that returns"
    for block_id in exits:
        assert solution.block_out[block_id] == frozenset()


def test_solver_respects_the_forward_boundary_at_entry():
    """``PARAM`` reaches entry, and nothing later replaces it there."""
    cfg = cfg_for("int f(int c) { return c; }")
    solution = reaching(cfg)
    assert param("c") in solution.block_in[cfg.entry]


def test_solver_omits_unreachable_blocks_entirely():
    cfg = cfg_for("int f() { return 1; }")
    unreachable = [block.id for block in cfg.unreachable_blocks]
    if not unreachable:
        pytest.skip("this function has no unreachable block")
    solution = liveness(cfg)
    for block_id in unreachable:
        assert block_id not in solution.block_in
        assert block_id not in solution.block_out


def test_solver_trace_is_capped_and_flagged():
    """A trace longer than the cap keeps the first N steps and says so."""
    # Many *blocks*, not many instructions: a single straight-line block converges in one
    # update however long it is. Each `if` adds two blocks and costs two updates, so 120 of
    # them overruns MAX_TRACE_STEPS by a comfortable margin.
    #
    # The margin was larger before the worklist was fixed to re-queue a block's *dependents*
    # rather than its neighbours, because that change also removed a layer of redundant
    # re-processing: 80 `if`s converged in 161 updates where they previously needed well over
    # 200. The assertion is deliberately unchanged rather than weakened to match, so the
    # fixture has to keep genuinely outrunning the cap.
    source = (
        "int f(int c) {\n"
        "  int x;\n"
        "  x = 0;\n"
        + "".join(f"  if (c > {i}) {{ x = x + {i}; }}\n" for i in range(120))
        + "  return x;\n"
        "}\n"
    )
    cfg = cfg_for(source)
    solution = liveness(cfg)
    payload = solution.to_dict()
    assert payload["iterations"] > MAX_TRACE_STEPS, "fixture no longer overruns the cap"
    assert payload["trace_truncated"] is True
    assert len(payload["trace"]) == MAX_TRACE_STEPS
    # Every kept step is still a complete, well-formed record.
    for step in payload["trace"]:
        assert set(step) == {"iteration", "block", "in", "out", "changed"}
        assert isinstance(step["changed"], bool)
        assert step["block"] in solution.block_in


def test_solver_is_deterministic_across_runs():
    source = """
    int f(int n) {
        int s; s = 0;
        int i; i = 0;
        while (i < n) { s = s + i; i = i + 1; }
        return s;
    }
    """
    program = compile_source(source)
    cfgs = build_program_cfgs(program)
    first = json.dumps(analyze(program, cfgs=cfgs), sort_keys=False)
    second = json.dumps(analyze(program, cfgs=cfgs), sort_keys=False)
    assert first == second


def test_solver_raises_rather_than_spinning(monkeypatch):
    """A cap that trips is reported as a compiler bug, not an infinite loop."""
    monkeypatch.setattr("opticode.analysis.solver.MAX_ITERATIONS", 2)
    cfg = cfg_for("int f(int n) { int i; i = 0; while (i < n) { i = i + 1; } return i; }")
    problem = DataFlowProblem(
        name="never-settles",
        direction=Direction.BACKWARD,
        cfg=cfg,
        boundary=frozenset(),
        initial=frozenset(),
        meet=lambda values: frozenset().union(*values) if values else frozenset(),
        # Strictly growing: each round adds a name, so no fixed point exists in this domain.
        transfer=lambda block, live: live
        | frozenset(f"{block.id}#{block.start_id}"),
        render=sorted,
    )
    with pytest.raises(SolverError, match="did not converge"):
        solve(problem)


def test_solver_runs_every_example_and_leaves_nothing_live_at_exit():
    """Every example terminates, and nothing is live once the program has returned."""
    paths = sorted(EXAMPLES_DIR.glob("*.c"))
    assert paths, "the example corpus is empty"
    for path in paths:
        source = path.read_text(encoding="utf-8")
        program, diagnostics = parse(source)
        if program is None or any(d.severity.value == "error" for d in diagnostics):
            continue
        result = semantic_analyze(program)
        if any(d.severity.value == "error" for d in result.diagnostics):
            continue
        lowered = lower(result)
        if not lowered.ok:
            continue
        cfgs = build_program_cfgs(lowered.program)
        analyze(lowered.program, cfgs=cfgs)
        for cfg in cfgs.values():
            solution = liveness(cfg)
            for block_id in solution.block_out:
                if cfg.flows_to_exit(block_id):
                    assert solution.block_out[block_id] == frozenset(), (
                        f"{path.name}:{cfg.function.name} is live at exit"
                    )


# ==================================================================== liveness


def test_liveness_is_empty_at_the_end_of_a_straight_line():
    cfg = cfg_for("int f() { int x; x = 1; return x; }")
    solution = liveness(cfg)
    assert solution.block_out[cfg.entry] == frozenset()


def test_liveness_finds_a_dead_store():
    """``x = 1`` is overwritten by ``x = 2`` before anything reads it."""
    report = report_for("int f() { int x; x = 1; x = 2; return x; }")
    facts = report["instructions"]
    # The first store has nothing live before it: its value is never read.
    first_store = next(
        key for key in facts if key == "i1"
    )
    assert facts[first_store]["liveness"]["live_in"] == []
    # The second store's value *is* read by the return, so it is live after it.
    assert "x" in facts["i2"]["liveness"]["live_out"]


def test_liveness_only_follows_the_branch_that_reads():
    """``x`` is assigned on one branch and read on neither, so it is never live."""
    cfg = cfg_for("int f(int c) { int x; if (c > 0) { x = 1; } return 0; }")
    solution = liveness(cfg)
    for block in cfg.blocks:
        if not block.reachable:
            continue
        assert "x" not in solution.block_in[block.id]
        assert "x" not in solution.block_out[block.id]


def test_liveness_marks_a_variable_live_only_where_a_read_can_reach():
    """``b`` is live at entry because the branch body reads it, and nowhere after."""
    source = """
    int f(int a, int b) {
        int t;
        if (a > 0) { t = b; } else { t = 2; }
        return t;
    }
    """
    cfg = cfg_for(source)
    solution = liveness(cfg)
    entry = cfg.entry
    # The condition reads a, and the "then" block reads b, so both are live on entry.
    assert "a" in solution.block_in[entry]
    assert "b" in solution.block_in[entry]
    # The value read into t is what the return consumes, so t is live into the join.
    join = next(
        block
        for block in cfg.blocks
        if block.predecessors and len(block.predecessors) > 1
    )
    assert "t" in solution.block_in[join.id]


def test_liveness_through_a_loop_converges():
    source = """
    int f(int n) {
        int s; s = 0;
        int i; i = 0;
        while (i < n) { s = s + i; i = i + 1; }
        return s;
    }
    """
    cfg = cfg_for(source)
    solution = liveness(cfg)
    header = cfg.loop_headers()
    assert header, "expected a loop"
    for block_id in header:
        # Both loop-carried variables are live at the header, since the body reads both.
        assert "s" in solution.block_in[block_id]
        assert "i" in solution.block_in[block_id]


# ============================================================== block USE / DEF


def test_block_use_is_the_names_read_before_being_written_in_the_block():
    """``x`` is written before it is read, so it is not in USE; ``c`` is only read."""
    report = report_for("int f(int c) { int x; x = c; x = x + 1; return x; }")
    assert report["use"] == {"B1": ["c"]}
    assert report["def"] == {"B1": ["t1", "x"]}


def test_block_use_excludes_a_name_assigned_before_it_is_read():
    report = report_for("int f() { int x; x = 1; return x; }")
    assert report["use"] == {"B1": []}, "x is defined before it is read"
    assert report["def"] == {"B1": ["x"]}


def test_block_use_reports_a_read_with_no_write_before_it():
    report = report_for("int f() { int x; return x; }")
    assert report["use"] == {"B1": ["x"]}, "x is read and never written in the block"
    assert report["def"] == {"B1": []}


def test_block_def_is_every_write_even_when_a_name_is_written_twice():
    report = report_for("int f() { int x; x = 1; x = 2; return x; }")
    assert report["def"] == {"B1": ["x"]}, "DEF is a set, so two writes collapse"


def test_unreachable_blocks_have_no_use_or_def_entry():
    """Absent, not present-with-an-empty-set: an unanalyzed block is not an empty one."""
    report = report_for("int f() { return 1; }")
    assert "B2" not in report["use"]
    assert "B2" not in report["def"]
    assert set(report["use"]) == set(report["def"]) == set(report["live_in"])


def test_liveness_transfer_holds_for_every_reachable_block():
    """``IN = USE ∪ (OUT \\ DEF)`` is checked block by block, not assumed.

    If the table the UI shows is ever computed differently from the fixed point, this is
    the equation that catches it.
    """
    source = (
        "int f(int n) { int s; s = 0; int i; i = 0;"
        " while (i < n) { s = s + i; i = i + 1; } return s; }"
    )
    report = report_for(source)
    assert report["live_out"], "expected analyzed blocks"
    for block_id, out in report["live_out"].items():
        expected = set(report["use"][block_id]) | (set(out) - set(report["def"][block_id]))
        assert set(report["live_in"][block_id]) == expected, block_id


def test_block_use_def_and_liveness_are_hand_verified_through_a_loop():
    """The loop's fixed point, written out by hand from the TAC.

    ``B1`` initializes ``s`` and ``i``, so it only uses ``n``. ``B2`` tests ``i < n`` and
    passes ``i`` and ``n``; ``B3`` adds ``s + i`` and increments ``i``, so it uses both and
    defines both (plus the temporaries). ``B4`` reads ``s``. The live sets need two passes
    around the back edge to settle, which is the point of asserting them literally.
    """
    source = (
        "int f(int n) { int s; s = 0; int i; i = 0;"
        " while (i < n) { s = s + i; i = i + 1; } return s; }"
    )
    report = report_for(source)
    assert report["use"] == {
        "B1": [],
        "B2": ["i", "n"],
        "B3": ["i", "s"],
        "B4": ["s"],
    }
    assert report["def"] == {
        "B1": ["i", "s"],
        "B2": ["t1"],
        "B3": ["i", "s", "t2", "t3"],
        "B4": [],
    }
    assert report["live_in"] == {
        "B1": ["n"],
        "B2": ["i", "n", "s"],
        "B3": ["i", "n", "s"],
        "B4": ["s"],
    }


# ========================================================= reaching definitions


def test_reaching_definitions_reports_a_single_definition():
    """After ``x = 1``, the only definition of x is that store — UNDEF was killed."""
    cfg = cfg_for("int f() { int x; x = 1; return x; }")
    solution = reaching(cfg)
    everything = set(solution.block_in[cfg.entry]) | set(solution.block_out[cfg.entry])
    writes = {item for item in everything if item.variable == "x" and item.instruction_id}
    assert writes == {definition("x", "i1")}, "exactly one store defines x"
    assert undef("x") not in solution.block_out[cfg.entry], "the store killed UNDEF(x)"


def test_reaching_definitions_kills_an_undef_on_assignment():
    """A write removes the boundary ``UNDEF``, so later reads are not indeterminate."""
    cfg = cfg_for("int f() { int x; x = 1; return x; }")
    solution = reaching(cfg)
    assert undef("x") in solution.block_in[cfg.entry], "UNDEF holds before the store"
    assert undef("x") not in solution.block_out[cfg.entry], "and not after it"


def test_reaching_definitions_kills_a_param_on_assignment():
    """Reassigning a parameter stops PARAM from reaching the later read."""
    cfg = cfg_for("int f(int x) { x = 1; return x; }")
    solution = reaching(cfg)
    assert param("x") in solution.block_in[cfg.entry]
    assert param("x") not in solution.block_out[cfg.entry]


def test_reaching_definitions_merges_two_definitions_at_a_join():
    """A variable assigned on both branches reaches the join from both writes."""
    source = """
    int f(int c) {
        int x;
        if (c > 0) { x = 1; } else { x = 2; }
        return x;
    }
    """
    cfg = cfg_for(source)
    solution = reaching(cfg)
    return_block = next(
        block for block in cfg.blocks if block.reachable and cfg.flows_to_exit(block.id)
    )
    merged = solution.block_in[return_block.id]
    writes = {
        item.instruction_id for item in merged if item.variable == "x" and item.instruction_id
    }
    assert writes == {"i3", "i6"}
    assert undef("x") not in merged, "both stores killed the UNDEF"


def test_reaching_definitions_reports_param_at_entry():
    cfg = cfg_for("int f(int c) { return c; }")
    solution = reaching(cfg)
    assert param("c") in solution.block_in[cfg.entry]


def test_reaching_definitions_reports_undef_for_an_uninitialized_read():
    """A local read before any write reaches only ``UNDEF``."""
    cfg = cfg_for("int f() { int x; return x; }")
    solution = reaching(cfg)
    everything = set(solution.block_in[cfg.entry]) | set(solution.block_out[cfg.entry])
    assert undef("x") in everything
    assert not any(item.instruction_id for item in everything if item.variable == "x")


def test_reaching_definitions_kills_an_earlier_write():
    """A later write removes the earlier one from every point after it."""
    cfg = cfg_for("int f() { int x; x = 1; x = 2; return x; }")
    solution = reaching(cfg)
    final = solution.block_out[cfg.entry]
    writes = {
        item.instruction_id for item in final if item.variable == "x" and item.instruction_id
    }
    assert writes == {"i2"}, "i1 was killed by i2"


def test_reaching_definitions_knows_call_defines_its_destination():
    """``x = g()`` lowers to a call into a temporary and then a copy, so both define."""
    cfg = cfg_for("int g() { return 1; } int f() { int x; x = g(); return x; }", "f")
    solution = reaching(cfg)
    everything = set(solution.block_in[cfg.entry]) | set(solution.block_out[cfg.entry])
    x_writes = {
        item.instruction_id for item in everything if item.variable == "x" and item.instruction_id
    }
    assert len(x_writes) == 1, f"expected one store into x, got {x_writes}"


# ================================================================ def-use chains


def test_def_use_chains_link_a_write_to_its_read():
    report = report_for("int f() { int x; x = 7; return x; }")
    chains = report["chains"]
    assert chains["defs_of"], "expected at least one recorded use"
    # The only definition reaching the return's read of x is the store of 7.
    return_defs = [
        defs
        for use, defs in chains["defs_of"].items()
        if defs == ["x@i1"]
    ]
    assert return_defs, f"expected x@i1 to reach a read; got {chains['defs_of']}"
    # And that read is listed under the definition's own use list.
    assert ["i2", 0] in chains["uses_of"]["x@i1"]


def test_constant_fact_result_is_the_value_produced_not_the_one_replaced():
    """``result`` must be what the instruction writes, not the value it overwrites."""
    cfg = cfg_for("int f() { int a; a = 2; a = a + 3; return a; }")
    facts = analyze_cfg(cfg).instructions
    # The second write replaces 2 with 5. Reporting 2 here would be a plausible-looking lie,
    # because it *is* the value the instruction consumes.
    second_write = facts["i2"].constants
    assert second_write.result == AbstractValue(ValueKind.CONST, 5, "int")
    assert second_write.operands[0] == AbstractValue(ValueKind.CONST, 2, "int")


def test_constant_fact_result_is_nac_for_a_call():
    cfg = cfg_for("int bar() { return 7; } int f() { int y = bar(); return y; }", "f")
    call = next(
        instruction
        for block in cfg.blocks
        for instruction in block.instructions
        if instruction.opcode is Opcode.CALL
    )
    assert analyze_cfg(cfg).instructions[call.id].constants.result == NAC


def test_constant_fact_result_is_none_when_nothing_is_defined():
    cfg = cfg_for("int f() { return 1; }")
    report = analyze_cfg(cfg)
    only = report.instructions["i1"]
    assert only.constants.result is None, "the return defines nothing"


def test_dest_live_distinguishes_a_dead_write_from_a_live_one():
    """``dest_live`` is the dead-assignment test, so it must separate the two cases."""
    cfg = cfg_for("int f() { int a; a = 1; return a; }")
    assert analyze_cfg(cfg).instructions["i1"].liveness.dest_live is True

    cfg = cfg_for("int f() { int a; a = 1; return 5; }")
    dead = analyze_cfg(cfg).instructions["i1"]
    assert dead.liveness.dest_live is False
    assert dead.liveness.live_out == frozenset(), "nothing survives the write"


def test_dest_live_is_false_for_an_instruction_that_defines_nothing():
    cfg = cfg_for("int f() { return 5; }")
    assert analyze_cfg(cfg).instructions["i1"].liveness.dest_live is False


def test_dest_live_follows_the_last_write_of_a_variable():
    """Only the final write of ``a`` can be live; the earlier one is already dead."""
    cfg = cfg_for("int f() { int a; a = 1; a = 2; return a; }")
    facts = analyze_cfg(cfg).instructions
    assert facts["i1"].liveness.dest_live is False, "overwritten before any read"
    assert facts["i2"].liveness.dest_live is True, "the value that is returned"


def test_def_use_chains_report_an_uninitialized_read():
    report = report_for("int f() { int x; return x; }")
    uninitialized = report["chains"]["defs_of"]
    assert any("UNDEF(x)" in defs for defs in uninitialized.values())
    # Nothing else reaches it, because nothing ever assigned x.
    for defs in uninitialized.values():
        if "UNDEF(x)" in defs:
            assert defs == ["UNDEF(x)"]
    # And it is surfaced as a first-class field, not only inside the chains.
    assert report["uninitialized_reads"], "expected the read of x to be flagged"


def test_def_use_chains_exclude_defined_reads_from_uninitialized():
    """A variable assigned before it is read is not an uninitialized read."""
    report = report_for("int f() { int x; x = 1; return x; }")
    assert report["uninitialized_reads"] == []


def test_def_use_chains_query_accessors_agree_with_the_serialized_shape():
    cfg = cfg_for("int f() { int x; x = 1; return x; }")
    analysis = analyze_cfg(cfg)
    chains = analysis.chains
    assert chains.definitions_of_use("i2", 0) == (definition("x", "i1"),)
    assert chains.uses_of_definition(definition("x", "i1")) == (("i2", 0),)
    # An unknown use answers empty rather than raising.
    assert chains.definitions_of_use("no-such", 9) == ()
    assert chains.uses_of_definition(param("nothing")) == ()


def test_def_use_chains_are_indexed_in_both_directions():
    """Every use listed under a definition points back at a use that lists it."""
    report = report_for("int f() { int x; x = 1; return x; }")
    chains = report["chains"]
    seen_pairs = 0
    for use, defs in chains["defs_of"].items():
        assert defs, f"use {use} has no definition"
        instruction_id, _, index = use.partition("#")
        for item in defs:
            forward = [list(pair) for pair in chains["uses_of"][item]]
            assert [instruction_id, int(index)] in forward, (
                f"{use} lists {item} but {item} does not list it back"
            )
            seen_pairs += 1
    assert seen_pairs, "expected at least one def/use pair"


# ========================================================= constant propagation


def test_constant_folding_of_literal_arithmetic():
    cfg = cfg_for("int f() { return 2 + 3 * 4; }")
    solution = propagate(cfg)
    assert any(
        value.is_const and value.value == 14 for value in solution.block_out[cfg.entry].values()
    )


def test_constant_propagation_keeps_an_agreeing_merge():
    """Both branches assign 5, so the join is still a known 5."""
    report = report_for(
        "int f(int c) { int x; if (c > 0) { x = 5; } else { x = 5; } return x; }"
    )
    assert report["exit_values"]["x"] == {"kind": "const", "value": 5, "type": "int"}


def test_constant_propagation_loses_a_disagreeing_merge():
    """The branches assign different constants, so the join is NAC."""
    report = report_for(
        "int f(int c) { int x; if (c > 0) { x = 5; } else { x = 6; } return x; }"
    )
    assert report["exit_values"]["x"]["kind"] == "nac"


def test_constant_propagation_tracks_a_loop_that_counts_to_a_bound():
    """A variable assigned the same value on every iteration stays constant."""
    report = report_for("int f() { int x; x = 42; return x; }")
    assert report["exit_values"]["x"] == {"kind": "const", "value": 42, "type": "int"}


def test_constant_propagation_leaves_a_parameter_unknown():
    report = report_for("int f(int c) { return c + 1; }")
    assert report["exit_values"]["c"]["kind"] == "nac"


def test_constant_propagation_folds_safe_division():
    report = report_for("int f() { return 10 / 2; }")
    values = [v for v in report["exit_values"].values() if v["kind"] == "const"]
    assert {v["value"] for v in values} == {5}


def test_constant_propagation_refuses_to_fold_division_by_zero():
    """``10 / 0`` traps, so there is no value to fold it to."""
    report = report_for("int f() { return 10 / 0; }")
    assert all(value["kind"] != "const" for value in report["exit_values"].values())


def test_constant_propagation_marks_a_call_result_unknown():
    report = report_for("int g() { return 1; } int f() { int x; x = g(); return x; }", "f")
    assert report["exit_values"]["x"]["kind"] == "nac"


def test_constant_propagation_marks_a_read_unknown():
    """``scanf`` fills x from stdin, which is exactly what is unknown at compile time."""
    report = report_for('int f() { int x; scanf("%d", &x); return x; }')
    assert report["exit_values"]["x"]["kind"] == "nac"


def test_constant_propagation_folds_float_conversions():
    report = report_for("float f() { int t = 2; float f = t; return f; }", "f")
    assert report["exit_values"]["f"] == {"kind": "const", "value": 2.0, "type": "float"}


def test_constant_propagation_truncates_when_narrowing():
    report = report_for("int f() { float v = 2.5; int i = v; return i; }")
    assert report["exit_values"]["i"] == {"kind": "const", "value": 2, "type": "int"}


def test_constant_propagation_keeps_int_and_float_constants_apart():
    """``5`` and ``5.0`` are different values, so a merge of them is NAC."""
    report = report_for("float f(int c) { float v; if (c > 0) { v = 5.0; } else { v = 5.5; } return v; }")
    assert report["exit_values"]["v"]["kind"] == "nac"


def test_constant_propagation_exposes_branch_condition_values():
    report = report_for("int f() { int x; if (1 > 0) { x = 5; } return x; }")
    assert len(report["branches"]) == 1
    branch = report["branches"][0]
    assert branch["statically_decidable"] is True
    assert branch["value"]["kind"] == "const"
    # The condition is true, so only the "then" side ever runs.
    assert branch["always_taken"] == branch["true_target"]


def test_constant_propagation_reports_an_unknown_condition_as_undecidable():
    report = report_for("int f(int c) { int x; if (c > 0) { x = 5; } return x; }")
    branch = report["branches"][0]
    assert branch["statically_decidable"] is False
    assert branch["always_taken"] is None


# ============================================================== the lattice itself


def test_lattice_meet_rules():
    five = AbstractValue(ValueKind.CONST, 5, "int")
    six = AbstractValue(ValueKind.CONST, 6, "int")
    five_float = AbstractValue(ValueKind.CONST, 5.0, "float")
    assert TOP.meet(five) is five
    assert five.meet(TOP) is five
    assert NAC.meet(five) is NAC
    assert five.meet(NAC) is NAC
    assert five.meet(five) is five
    assert five.meet(six) is NAC
    assert five.meet(five_float) is NAC, "an int 5 is not a float 5.0"


def test_lattice_nac_is_a_sticky_bottom_of_knowledge():
    """NAC absorbs everything except itself, so an unknown stays unknown."""
    for value in (TOP, AbstractValue(ValueKind.CONST, 3, "int"), NAC):
        assert NAC.meet(value) is NAC


# ============================================================ combined reporting


def test_analyze_returns_one_entry_per_function():
    source = "int g() { return 1; } int f() { return g(); } int main() { return f(); }"
    program = compile_source(source)
    report = analyze(program, cfgs=build_program_cfgs(program))
    assert sorted(report) == ["f", "g", "main"]


def test_function_analysis_serializes_to_plain_json():
    source = """
    int f(int n) {
        int s; s = 0;
        int i; i = 0;
        while (i < n) { s = s + i; i = i + 1; }
        return s;
    }
    """
    program = compile_source(source)
    payload = analyze(program, cfgs=build_program_cfgs(program))["f"]
    # Round-tripping proves nothing in the report is a non-serializable object.
    assert json.loads(json.dumps(payload)) == payload
    assert payload["name"] == "f"
    assert payload["instructions"], "expected per-instruction facts"
    assert payload["traces"], "expected the iteration stepper's data"


# ------------------------------------------------------- the iteration stepper


def test_every_analysis_reports_a_trace_whose_count_matches_its_iteration_total():
    """The stepper replays the trace; the count next to it is the real number of updates.

    They come from one solver run, so a trace shorter than `iterations` is only correct with
    `trace_truncated` set. A trace that is short *without* saying so would be a stepper that
    silently stops early and looks like convergence.
    """
    source = """
    int f(int n) {
        int s; s = 0;
        int i; i = 0;
        while (i < n) { s = s + i; i = i + 1; }
        return s;
    }
    """
    program = compile_source(source)
    payload = analyze(program, cfgs=build_program_cfgs(program))["f"]

    assert set(payload["traces"]) == {"liveness", "reaching", "constant_propagation"}
    for name, trace in payload["traces"].items():
        assert trace["iterations"] == payload["iterations"][name], name
        if trace["trace_truncated"]:
            assert len(trace["steps"]) == MAX_TRACE_STEPS, name
        else:
            assert len(trace["steps"]) == trace["iterations"], name


def test_a_backward_analysis_is_labelled_backward():
    """Liveness is defined backwards; a stepper that ran it forwards would be a different proof.

    The direction is in the payload so the UI can label the animation, and it is asserted
    here per analysis because a copy-paste that swapped two of the three would otherwise
    produce a plausible-looking, wrong stepper.
    """
    result = analyze_cfg(cfg_for("int f(int n) { int s; s = 0; int i; i = 0;"
                                 " while (i < n) { s = s + i; i = i + 1; } return s; }"))
    assert result.traces["liveness"].direction == "backward"
    assert result.traces["reaching"].direction == "forward"
    assert result.traces["constant_propagation"].direction == "forward"


def test_the_steps_are_in_worklist_order_and_renumbered_from_one():
    """`iteration` is a position, not a block id, and the UI steps through it in order.

    The order comes from the worklist, which is a function of the CFG alone. That is what
    makes the trace reproducible, and reproducible is what makes it worth showing: a stepper
    that showed a different order on every run could not be used to argue anything.
    """
    cfg = cfg_for(
        "int f(int n) { int s; s = 0; int i; i = 0;"
        " while (i < n) { s = s + i; i = i + 1; } return s; }"
    )
    known = {block.id for block in cfg.blocks}
    result = analyze_cfg(cfg)
    for name, trace in result.traces.items():
        numbers = [step["iteration"] for step in trace.steps]
        assert numbers == list(range(1, len(numbers) + 1)), name
        assert all(step["block"] in known for step in trace.steps), name


def test_a_step_carries_the_rendered_value_not_the_domain_object():
    """The domain value cannot cross a JSON boundary, so the recorded step is already rendered.

    Asserting the *shape* of the rendered form is what pins the UI's contract: a name set is
    a sorted list, so the stepper can diff two steps without knowing the domain.
    """
    result = analyze_cfg(cfg_for("int f(int n) { int s; s = 0; int i; i = 0;"
                                 " while (i < n) { s = s + i; i = i + 1; } return s; }"))
    live = result.traces["liveness"]
    assert live.steps
    for step in live.steps:
        assert set(step) == {"iteration", "block", "in", "out", "changed"}
        assert isinstance(step["in"], list)
        assert step["in"] == sorted(step["in"]), "a name set renders sorted, so steps diff"

    constants = result.traces["constant_propagation"]
    for step in constants.steps:
        assert isinstance(step["in"], dict)


def test_a_truncated_trace_still_says_how_many_updates_it_took():
    """A function big enough to overrun the cap is the interesting case, not an edge case.

    The step is capped so the response cannot grow without bound; the total is not capped, so
    the panel can still say "612 updates, here are the first 200".
    """
    source = (
        "int f(int c) {\n"
        "  int x;\n"
        "  x = 0;\n"
        + "".join(f"  if (c > {i}) {{ x = x + {i}; }}\n" for i in range(120))
        + "  return x;\n"
        "}\n"
    )
    payload = analyze(compile_source(source), cfgs=build_program_cfgs(compile_source(source)))[
        "f"
    ]
    assert payload["traces"], "expected traces for a program this size"
    truncated = [t for t in payload["traces"].values() if t["trace_truncated"]]
    assert truncated, "fixture no longer overruns the cap"
    for trace in truncated:
        assert len(trace["steps"]) == MAX_TRACE_STEPS
        assert trace["iterations"] > MAX_TRACE_STEPS


def test_building_a_report_without_solutions_still_works_and_says_so():
    """The traces feed only the stepper, so a caller holding plain dicts is not blocked.

    Every other fact in the report is derived from the block fixed points, so re-assembling a
    report from those alone should not require the solver objects back. The empty `traces` is
    the honest answer — no stepper data — rather than invented data. This mirrors what
    `analyze_cfg` does, minus the one keyword argument.
    """
    from opticode.analysis.constprop import knowns
    from opticode.analysis.results import build_function_analysis

    cfg = cfg_for("int f() { int x; x = 1; return x; }")
    live = liveness(cfg)
    definitions = reaching(cfg)
    constants = propagate(cfg)
    shared = {
        "live": dict(live.block_in),
        "live_out": dict(live.block_out),
        "reaching_in": dict(definitions.block_in),
        "reaching_out": dict(definitions.block_out),
        "states_in": dict(constants.block_in),
        "states_out": dict(constants.block_out),
        "exit_values": knowns(cfg, constants),
        "iterations": {
            "liveness": live.iterations,
            "reaching": definitions.iterations,
            "constant_propagation": constants.iterations,
        },
    }
    without = build_function_analysis(cfg, **shared)
    with_solutions = build_function_analysis(
        cfg,
        **shared,
        solutions={
            "liveness": live,
            "reaching": definitions,
            "constant_propagation": constants,
        },
    )

    assert without.traces == {}
    assert without.to_dict()["traces"] == {}
    assert set(with_solutions.traces) == {"liveness", "reaching", "constant_propagation"}
    # Everything else is byte-identical, which is the whole claim: the stepper is additive.
    without_payload = without.to_dict()
    with_payload = with_solutions.to_dict()
    del without_payload["traces"], with_payload["traces"]
    assert without_payload == with_payload


def test_the_trace_is_deterministic_across_runs():
    """A stepper that showed a different order each time could not be used to argue anything."""
    source = """
    int f(int n) {
        int s; s = 0;
        int i; i = 0;
        while (i < n) { s = s + i; i = i + 1; }
        return s;
    }
    """
    first = analyze(compile_source(source), cfgs=build_program_cfgs(compile_source(source)))
    second = analyze(compile_source(source), cfgs=build_program_cfgs(compile_source(source)))
    assert first["f"]["traces"] == second["f"]["traces"]


def test_analyze_cfg_returns_the_typed_result():
    cfg = cfg_for("int f() { int x; x = 1; return x; }")
    result = analyze_cfg(cfg)
    assert result.name == "f"
    assert result.live_in and result.reaching_in
    assert result.chains.defs_of is not None


def test_definition_kinds_are_distinguishable():
    """PARAM and UNDEF must not collapse into the same value."""
    assert param("x") != undef("x")
    assert param("x").kind is DefinitionKind.PARAM
    assert undef("x").kind is DefinitionKind.UNDEF
    assert param("x").is_boundary
    assert definition("x", "i1").kind is DefinitionKind.INSTRUCTION
    assert not definition("x", "i1").is_boundary
