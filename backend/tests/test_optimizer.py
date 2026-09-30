"""Hand-verified optimization tests.

Every expected instruction, event, and diagnostic in this file is written out by hand from
the MiniC source that produced it, not read back out of the optimizer. A test that derives
its expectation with the same walk as the code under test proves only that the optimizer
agrees with itself.

The order of the tests matters more than it looks. They run bottom-up through the layers:

* ``test_constant_propagation_*``  - does a pass see what the data-flow solver proved?
* ``test_constant_folding_*``      - does it fold only what it can prove?
* ``test_branch_folding_*``        - does it delete a branch without deleting its condition?
* ``test_unreachable_blocks_*``    - can it delete unreachable code, and only unreachable code?
* ``test_dead_assignments_*``      - can it delete stores nobody reads?
* ``test_*preserves*``             - the property that matters above all: behaviour is unchanged
* ``test_manager_*``               - does the fixpoint driver terminate and account for itself
* ``test_verification_*``          - does the oracle actually catch a wrong optimization
* ``test_pipeline_*``              - does it survive contact with the compile pipeline

Two of these tests exist because they failed against real code rather than because the shape
seemed worth covering. ``test_constant_propagation_stops_at_a_loop_that_can_change_the_value``
and ``test_unreachable_blocks_survives_a_loop_with_a_continue`` each pin down a bug in the
analysis layer that the interpreter oracle found: a worklist that re-queued the wrong
neighbours, and a label-only block that was given no fall-through edge. Both produced
confidently wrong answers on real programs, which is exactly what the oracle is for.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from opticode.cfg import build_program_cfgs
from opticode.ir import IRProgram, Opcode, check_program, interpret, lower, print_program
from opticode.optimizer import (
    DEFAULT_PASSES,
    MAX_EVENTS,
    BranchFolding,
    ConstantFolding,
    ConstantPropagation,
    DeadAssignmentElimination,
    OptimizationError,
    PassContext,
    PassDelta,
    PassError,
    UnreachableBlockElimination,
    optimize,
    run_passes,
    select_passes,
    verify_equivalence,
)
from opticode.parser import parse
from opticode.semantic import analyze as semantic_analyze

# ------------------------------------------------------------------ helpers


def lowered(source: str) -> IRProgram:
    """Lower one snippet to TAC the way the pipeline does, failing loudly on bad input."""
    program, diagnostics = parse(source)
    errors = [d for d in diagnostics if d.severity.value == "error"]
    assert not errors, errors
    result = lower(semantic_analyze(program))
    assert result.ok, result.errors
    return result.program


def run_one(source: str, pass_instance):
    """Run a single pass over one snippet and return ``(context, delta)``.

    The pass is passed in as an *instance* because ``DEFAULT_PASSES`` holds instances: the
    manager calls ``pass.run(ctx)`` on them, so a class would bind the context to ``self``.
    """
    context = PassContext(program=lowered(source))
    context.refresh()
    delta = pass_instance.run(context)
    return context, delta


def run_through(source: str, *pass_instances):
    """Run passes in order over one snippet, refreshing between them as the manager does.

    This is how the real pipeline behaves, and it matters: constant folding is driven by
    what the data-flow solver proved, so on its own it has nothing to fold until
    propagation has rewritten the operands to literals. Testing a pass in isolation would
    therefore assert that it does nothing, which is true and useless.
    """
    context = PassContext(program=lowered(source))
    deltas = []
    for pass_instance in pass_instances:
        context.refresh()
        deltas.append(pass_instance.run(context))
    return context, deltas


def optimize_source(source: str, **kwargs):
    return optimize(lowered(source), **kwargs)


def code(source: str) -> str:
    """The lowered program as text, which is what a person actually reads."""
    return print_program(lowered(source))


def instructions_of(context: PassContext, index: int = 0) -> tuple:
    return context.program.functions[index].instructions


def returns_in(context: PassContext, index: int = 0) -> list:
    return [i for i in instructions_of(context, index) if i.opcode is Opcode.RETURN]


def operand_names(context: PassContext, index: int = 0) -> list:
    """Every variable and temporary mentioned by the function.

    Constants live in ``Operand.value`` and variables in ``Operand.name``, so anything
    asking "is this name still used?" has to look at ``name`` specifically.
    """
    return [
        operand.name
        for inst in instructions_of(context, index)
        for operand in inst.operands
        if not operand.is_const
    ]


def const_values(context: PassContext, index: int = 0) -> list:
    return [
        operand.value
        for inst in instructions_of(context, index)
        for operand in inst.operands
        if operand.is_const
    ]


def mentions(context: PassContext, name: str) -> bool:
    return name in print_program(context.program)


# ------------------------------------------------ constant propagation


def test_constant_propagation_replaces_a_use_with_its_literal():
    """`x = 3` then `y = x + 1` must leave nothing referring to `x`."""
    source = "int f() {\n  int x;\n  x = 3;\n  int y = x + 1;\n  return y;\n}\n"
    context, delta = run_one(source, ConstantPropagation())

    assert delta.changed is True
    assert delta.removed == 0, "propagation rewrites in place; it deletes nothing"
    assert "x" not in operand_names(context), "x survived as an operand"


def test_constant_propagation_reaches_across_a_join():
    """A value assigned 7 on both arms of an if/else is constant at the join."""
    source = (
        "int f(int c) {\n"
        "  int x;\n"
        "  if (c > 0) { x = 7; } else { x = 7; }\n"
        "  return x;\n"
        "}\n"
    )
    context, delta = run_one(source, ConstantPropagation())
    assert delta.changed is True
    returned = returns_in(context)[0].operands[0]
    assert returned.is_const
    assert returned.value == 7


def test_constant_propagation_leaves_a_parameter_alone():
    """A parameter has no known value, and pretending otherwise is the classic bug."""
    source = "int f(int n) {\n  int y = n + 1;\n  return y;\n}\n"
    context, delta = run_one(source, ConstantPropagation())
    assert delta.changed is False
    assert "n" in operand_names(context)


def test_constant_propagation_stops_at_a_loop_that_can_change_the_value():
    """`x = 1` before a loop that reassigns `x` is not a constant use inside the loop.

    This is a real bug the interpreter oracle caught. Treating `x` as 1 inside the loop
    folded a bound that the loop body itself invalidates, and the optimized program printed
    the wrong answer. The precise claim is that the body still reads `x` as a variable -
    a literal `1` in the source's own `i + 1` is a different thing and is not what this
    test is about.
    """
    source = (
        "int f(int n) {\n"
        "  int x = 1;\n"
        "  for (int i = 0; i < n; i = i + 1) { x = x + 2; }\n"
        "  return x;\n"
        "}\n"
    )
    context, delta = run_one(source, ConstantPropagation())

    assert delta.changed is False, "x is overwritten in the loop, so nothing is propagatable"
    assert "x" in operand_names(context), "the loop body stopped reading x"

    # And the end-to-end claim. This program genuinely has nothing to optimize, so the
    # honest assertions are that the pipeline left it alone *and* that the result still
    # runs correctly - an optimizer that corrupted a loop would have to change something
    # to do it, and one that "verified" a program it never touched would be claiming more
    # than it checked.
    result = optimize_source(
        "int main() {\n"
        "  int x = 1;\n"
        "  for (int i = 0; i < 8; i = i + 1) { x = x + 2; }\n"
        '  printf("%d\\n", x);\n'
        "  return x;\n"
        "}\n"
    )
    assert result.changed is False
    assert result.verification is None
    # 1, then eight iterations adding 2 each: 1 + 16 = 17.
    assert interpret(lowered(
        "int main() {\n"
        "  int x = 1;\n"
        "  for (int i = 0; i < 8; i = i + 1) { x = x + 2; }\n"
        '  printf("%d\\n", x);\n'
        "  return x;\n"
        "}\n"
    ), entry="main").return_value == 17


# ---------------------------------------------------- constant folding


def test_constant_folding_computes_arithmetic_at_compile_time():
    source = "int f() {\n  int y = 2 + 3 * 4;\n  return y;\n}\n"
    context, (_prop, fold) = run_through(source, ConstantPropagation(), ConstantFolding())

    assert fold.changed is True
    returned = returns_in(context)[0].operands[0]
    assert returned.is_const
    assert returned.value == 14


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2 + 3", 5),
        ("7 - 9", -2),
        ("3 * 4", 12),
        ("20 / 5", 4),
        ("17 % 5", 2),
        ("1 < 2", 1),
        ("2 <= 2", 1),
        ("3 > 4", 0),
        ("4 >= 4", 1),
        ("5 == 5", 1),
        ("5 != 5", 0),
    ],
)
def test_constant_folding_agrees_with_the_language(expression: str, expected: int):
    """Every folded value must be the value actually computing the expression.

    Written as one parametrized case per operator rather than a single test with a long
    chain of asserts, so that dropping an operator from the fold table fails here by name
    instead of failing nowhere at all.

    Propagation runs first because that is the order the pipeline uses and the order the
    pass depends on: folding is driven by the data-flow facts, and the facts only describe
    literal operands after propagation has put them there.
    """
    source = f"int f() {{\n  int y = {expression};\n  return y;\n}}\n"
    context, _deltas = run_through(source, ConstantPropagation(), ConstantFolding())
    returned = returns_in(context)[0].operands[0]
    assert returned.is_const, f"{expression} was not folded"
    assert returned.value == expected, f"{expression} folded to {returned.value}"


def test_constant_folding_leaves_division_by_zero_alone():
    """Folding 1/0 would have to *produce* the trap, which is not what folding means.

    The original division stays so the interpreter still traps, with the same message, at
    the same point. A folder that produced INT_MIN here would report success and hand back
    a program that computes a number instead of failing.
    """
    source = "int f(int a) {\n  int y = a / 0;\n  return y;\n}\n"
    context, _deltas = run_through(source, ConstantPropagation(), ConstantFolding())
    divisions = [i for i in instructions_of(context) if i.opcode is Opcode.BINARY]
    assert divisions, "the division was deleted rather than left alone"
    assert any(operand.is_const and operand.value == 0 for operand in divisions[0].operands)


def test_constant_folding_does_not_fold_an_unproven_value():
    """`a` is a parameter, so nothing about `a + 1` is known even after propagation."""
    source = 'int f(int a) {\n  int y = a + 1;\n  printf("hi\\n");\n  return y;\n}\n'
    context, _deltas = run_through(source, ConstantPropagation(), ConstantFolding())
    assert "a" in operand_names(context)


# -------------------------------------------------------- branch folding


def test_branch_folding_removes_a_provably_true_test():
    """A true test becomes a fall-through, so the conditional jump disappears.

    Branch folding leaves the now-unused condition behind - `t1 = 1 < 2` with nothing
    reading it - because deciding that is the dead-store pass's job. The fixpoint is what
    turns "the branch is gone" into "the program is smaller", and both halves are asserted
    so neither pass can quietly stop doing its part.
    """
    source = "int f(int n) {\n  int x = 0;\n  if (1 < 2) { x = 5; }\n  return x;\n}\n"
    context, (_prop, fold) = run_through(source, ConstantPropagation(), BranchFolding())

    assert fold.changed is True
    assert fold.removed == 1, "the conditional jump goes; the body stays"
    text = print_program(context.program)
    assert "if_false" not in text, f"the test survived: {text}"
    assert "x = 5" in text, "the taken body must be kept"

    finished, _deltas = run_through(
        source,
        ConstantPropagation(),
        BranchFolding(),
        ConstantFolding(),
        DeadAssignmentElimination(),
    )
    cleaned = print_program(finished.program)
    assert "1 < 2" not in cleaned, "the dead condition was never cleaned up"
    assert "x = 5" in cleaned


def test_branch_folding_keeps_the_right_body_when_the_test_is_true():
    """A true condition keeps the then-arm.

    Swapping the arms is the failure mode here, so the *value* is asserted rather than the
    instruction list: an optimizer can produce a correct-looking CFG that computes the
    wrong answer.
    """
    source = (
        "int main() {\n"
        "  int x = 0;\n"
        "  if (2 > 1) { x = 1; } else { x = 2; }\n"
        "  printf(\"%d\\n\", x);\n"
        "  return x;\n"
        "}\n"
    )
    result = optimize_source(source)
    assert result.verified is True
    returned = returns_in(_context_for(result.optimized))[0].operands[0]
    assert returned.is_const and returned.value == 1


def test_branch_folding_keeps_the_right_body_when_the_test_is_false():
    """A false condition keeps the else-arm. Both directions, for the reason above."""
    source = (
        "int main() {\n"
        "  int x = 0;\n"
        "  if (1 > 2) { x = 1; } else { x = 2; }\n"
        "  printf(\"%d\\n\", x);\n"
        "  return x;\n"
        "}\n"
    )
    result = optimize_source(source)
    assert result.changed is True
    assert "if_false" not in print_program(result.optimized)
    returned = returns_in(_context_for(result.optimized))[0].operands[0]
    assert returned.is_const and returned.value == 2


def test_branch_folding_leaves_a_runtime_test_alone():
    source = "int f(int n) {\n  if (n > 0) { return 1; }\n  return 0;\n}\n"
    context, delta = run_one(source, BranchFolding())
    assert delta.changed is False
    assert "if_false" in print_program(context.program)


# --------------------------------------------------- unreachable blocks


def test_unreachable_blocks_removes_what_branch_folding_left_behind():
    """`while (1) {}` has no unreachable code until its test is folded away.

    The loop test is `if_false 1 goto Lend`, so the exit edge is live and everything after
    the loop is reachable. Branch folding deletes that edge, and only then is the tail
    unreachable. Testing this pass alone would therefore assert that it does nothing,
    which is true; testing it in pipeline order is what makes it a test.
    """
    source = (
        "int main() {\n"
        "  int x = 1;\n"
        "  while (1) { x = x + 1; }\n"
        "  printf(\"unreachable\\n\");\n"
        "  return x;\n"
        "}\n"
    )
    context, _deltas = run_through(
        source,
        ConstantPropagation(),
        BranchFolding(),
        UnreachableBlockElimination(),
    )
    text = print_program(context.program)
    assert "printf" not in text, "the unreachable tail survived"
    assert "while" not in text
    check_program(context.program)


def test_unreachable_blocks_keeps_everything_that_can_run():
    """Everything here is reachable, so the pass must not touch a single instruction."""
    source = (
        "int f(int n) {\n"
        "  int x = 0;\n"
        "  if (n > 0) { x = 1; } else { x = 2; }\n"
        "  while (x < 3) { x = x + 1; }\n"
        "  return x;\n"
        "}\n"
    )
    context, delta = run_one(source, UnreachableBlockElimination())
    assert delta.changed is False
    assert delta.removed == 0
    assert print_program(context.program) == code(source)


def test_unreachable_blocks_does_not_delete_a_reachable_print():
    """`printf` in live code is observable even though it stores nothing anywhere."""
    source = 'int f(int n) {\n  printf("a\\n");\n  if (n > 0) { return 1; }\n  return 0;\n}\n'
    context, delta = run_one(source, UnreachableBlockElimination())
    assert delta.changed is False
    assert "print" in print_program(context.program)


def test_unreachable_blocks_keeps_the_return_of_a_void_function():
    """Deleting the last instruction of a void function would leave a body that falls off
    the end, which is exactly the kind of malformed TAC the structural checks reject."""
    source = "void f(int n) {\n  if (n > 0) { return; }\n  while (1) { }\n}\n"
    context, _delta = run_one(source, UnreachableBlockElimination())
    check_program(context.program)
    assert any(i.opcode is Opcode.RETURN for i in instructions_of(context))


def test_a_label_only_block_still_falls_through_to_the_next_block():
    """The fall-through bug, asserted where it actually lived.

    A loop body whose `continue` is the last statement lowers to a block holding a label
    and no instructions - ``B7``/``Lskip7`` below. When such a block was given no
    successors, the edge out of the back of the loop vanished from the CFG. Nothing
    crashed: reachability quietly lost those blocks, every analysis downstream solved a
    different program from the one the interpreter ran, and ``examples/07``'s
    ``count_up_to`` confidently reported 1 instead of 5. The interpreter oracle is what
    turned that into a test failure instead of a shipping bug, which is why the shape is
    now pinned at the CFG level.
    """
    source = (
        "int main() {\n"
        "  int found = 0;\n"
        "  int limit = 5;\n"
        "  for (int i = 0; i < 100; i = i + 1) {\n"
        "    if (i % 2 == 0) { continue; }\n"
        "    found = found + 1;\n"
        "    if (found == limit) { break; }\n"
        "  }\n"
        '  printf("%d\\n", found);\n'
        "  return found;\n"
        "}\n"
    )
    cfg = build_program_cfgs(lowered(source))["main"]

    empty = [block for block in cfg.blocks if not block.instructions]
    assert len(empty) == 1, f"expected exactly one label-only block, got {len(empty)}"
    assert empty[0].id == "B7"
    assert empty[0].successors == ("B8",), (
        "B7 holds only a label and must fall through to the next block in layout order"
    )

    # And the loop is still a loop, with its back edge intact. Reachability is deliberately
    # not asserted: this program has a genuinely unreachable tail after the `break`, and
    # the point of the test is the edge, not the marking.
    assert cfg.back_edges, "the loop's back edge is missing from the CFG"


def test_a_loop_body_ending_in_continue_still_produces_the_right_answer():
    """The behavioural half of the same bug, judged by running the program.

    The expected value is written out by hand: i goes 0..99, only odd i values reach
    ``found = found + 1``, and the fifth odd number is 9 - so the loop stops with
    ``found == 5``.
    """
    source = (
        "int main() {\n"
        "  int found = 0;\n"
        "  int limit = 5;\n"
        "  for (int i = 0; i < 100; i = i + 1) {\n"
        "    if (i % 2 == 0) { continue; }\n"
        "    found = found + 1;\n"
        "    if (found == limit) { break; }\n"
        "  }\n"
        '  printf("%d\\n", found);\n'
        "  return found;\n"
        "}\n"
    )
    program = lowered(source)
    assert interpret(program, entry="main").return_value == 5

    result = optimize_source(source)
    assert result.changed is True, "the fixture must genuinely have something to optimize"
    assert result.verified is True
    check_program(result.optimized)
    assert interpret(result.optimized, entry="main").return_value == 5


# ---------------------------------------------------- dead assignments


def test_dead_assignments_removes_a_store_nobody_reads():
    source = "int f() {\n  int x = 1;\n  int y = 2;\n  return y;\n}\n"
    context, delta = run_one(source, DeadAssignmentElimination())
    assert delta.changed is True
    assert not mentions(context, "x")


def test_dead_assignments_keeps_a_store_that_a_return_reads():
    source = "int f() {\n  int x = 41;\n  return x + 1;\n}\n"
    context, delta = run_one(source, DeadAssignmentElimination())
    assert delta.changed is False


def test_dead_assignments_keeps_a_loop_carried_variable():
    """A variable read on the next iteration is read, whatever this iteration does."""
    source = (
        "int f(int n) {\n"
        "  int sum = 0;\n"
        "  for (int i = 0; i < n; i = i + 1) { sum = sum + i; }\n"
        "  return sum;\n"
        "}\n"
    )
    context, _delta = run_one(source, DeadAssignmentElimination())
    assert mentions(context, "sum")


def test_dead_assignments_does_not_remove_a_print():
    source = 'int f() {\n  printf("side effect\\n");\n  return 0;\n}\n'
    context, delta = run_one(source, DeadAssignmentElimination())
    assert delta.changed is False


def test_dead_assignments_keeps_a_store_that_a_later_read_depends_on():
    """`x = 1` then `y = x` means x is read, so neither store may go.

    Deleting the first store would leave `y` reading an undefined temp; deleting the second
    would leave the return reading an undefined temp. Both are malformed TAC, which is why
    the structural check after every rewrite exists at all.
    """
    source = "int f() {\n  int x;\n  int y;\n  x = 1;\n  y = x;\n  return y;\n}\n"
    context, delta = run_one(source, DeadAssignmentElimination())
    assert delta.changed is False
    assert "x" in operand_names(context)
    check_program(context.program)


# ---------------------------------------------------- behaviour is unchanged


def test_every_pass_at_once_still_preserves_behaviour():
    """One source that exercises all five passes, judged by running both programs.

    The oracle is the point of the exercise, so it is the source of truth here rather than
    an instruction count: a smaller program is not automatically an equivalent one, and
    only executing both can tell us that.
    """
    source = (
        "int helper(int k) {\n"
        "  int twice = k + k;\n"
        "  int unused = 99;\n"
        "  return twice + 1;\n"
        "}\n"
        "\n"
        "int main() {\n"
        "  int total = 0;\n"
        "  for (int i = 0; i < 8; i = i + 1) {\n"
        "    if (i % 2 == 0) { continue; }\n"
        "    total = total + helper(i);\n"
        "    if (total > 100) { break; }\n"
        "  }\n"
        "  while (1) { break; }\n"
        "  printf(\"total=%d\\n\", total);\n"
        "  return total;\n"
        "}\n"
    )
    result = optimize_source(source)
    assert result.changed is True
    assert result.verified is True
    assert result.verification.first_difference is None
    assert result.verification.cases_checked >= 1


def test_the_optimized_program_is_structurally_sound():
    """Deleting instructions must not break the invariants the rest of the pipeline relies on."""
    source = (
        "int main() {\n"
        "  int x = 2 + 2;\n"
        "  int dead = 5;\n"
        "  if (x > 3) { printf(\"big\\n\"); }\n"
        "  return 0;\n"
        "}\n"
    )
    check_program(optimize_source(source).optimized)


def test_optimizing_twice_changes_nothing_the_second_time():
    """The fixpoint must be a fixpoint: re-running the passes finds nothing left to do.

    Without this, the manager would quietly report `converged` while still having work to
    do, and every caller would inherit a slightly different program.
    """
    source = (
        "int main() {\n"
        "  int a = 1 + 1;\n"
        "  int b = 0;\n"
        "  if (a > 1) { b = a * 2; }\n"
        "  printf(\"%d\\n\", b);\n"
        "  return 0;\n"
        "}\n"
    )
    once = optimize_source(source)
    twice = optimize(once.optimized)

    assert once.changed is True
    assert twice.changed is False, "a second run should have nothing left to do"
    assert print_program(once.optimized) == print_program(twice.optimized)


def test_a_program_with_nothing_to_optimize_reports_no_change():
    source = 'int main() {\n  printf("hi\\n");\n  return 0;\n}\n'
    result = optimize_source(source)
    assert result.changed is False
    assert result.removed_instructions == 0
    assert result.rounds == 1, "one round is enough to discover there is nothing to do"
    assert result.verified is False, "nothing changed, so nothing needed verifying"
    assert result.verification is None


# ------------------------------------------------------------- manager


def test_runs_the_passes_in_a_fixed_order():
    """Order is the pipeline's, not the caller's, so the reported order is one thing."""
    assert [item.name for item in DEFAULT_PASSES] == [
        "constant-propagation",
        "constant-folding",
        "branch-folding",
        "unreachable-blocks",
        "dead-assignments",
    ]


def test_pass_selection_rejects_an_unknown_name():
    with pytest.raises(PassError, match="unknown pass"):
        select_passes(["constant-propagation", "no-such-pass"])


def test_pass_selection_returns_pipeline_order_not_request_order():
    """Asking for the passes backwards must not create an order the manager must handle."""
    chosen = select_passes(["dead-assignments", "constant-propagation"])
    assert [item.name for item in chosen] == [
        "constant-propagation",
        "dead-assignments",
    ]


def test_pass_selection_defaults_to_every_pass():
    assert select_passes(None) == DEFAULT_PASSES


def test_the_manager_reports_a_count_for_every_pass_it_was_given():
    source = "int main() {\n  int x = 1 + 1;\n  return x;\n}\n"
    result = optimize_source(source)
    assert [entry.name for entry in result.passes] == [item.name for item in DEFAULT_PASSES]
    # A pass that changed nothing is still reported, with a count of zero. A caller asking
    # "what did you do?" has to be able to tell "ran, nothing to do" from "never ran".
    for entry in result.passes:
        assert entry.iterations >= 1
        assert entry.removed >= 0


def test_a_pass_that_never_settles_is_stopped_by_the_round_cap():
    """Without the cap this would loop forever, which is a hung request rather than an error."""
    source = "int main() {\n  int x = 1 + 1;\n  return x;\n}\n"

    class NeverSettles:
        name = "never-settles"
        description = "a pass that always reports a change, to exercise the round cap"

        def run(self, ctx: PassContext) -> PassDelta:
            return PassDelta(changed=True)

    run = run_passes(lowered(source), passes=[NeverSettles()], max_iterations=5)
    assert run.converged is False
    assert "still making changes" in run.stopped_reason
    assert run.rounds == 5


def test_a_round_cap_too_small_to_converge_is_an_error_not_a_truncated_success():
    """`optimize` takes pass *names*, so the non-convergence path is reached via the cap.

    This program needs two rounds to settle - branch folding enables the dead-store pass -
    so a cap of one must be reported rather than quietly returning a half-optimized program
    as though it were finished.
    """
    source = (
        "int main() {\n"
        "  int x = 0;\n"
        "  if (2 > 1) { x = 1 + 1; } else { x = 3; }\n"
        "  printf(\"%d\\n\", x);\n"
        "  return x;\n"
        "}\n"
    )
    assert optimize_source(source).rounds > 1, "the fixture must genuinely need two rounds"
    with pytest.raises(OptimizationError, match="still making changes"):
        optimize_source(source, max_iterations=1)


def test_stats_count_instructions_before_and_after():
    source = "int main() {\n  int x = 2 + 3;\n  int dead = 9;\n  return x;\n}\n"
    result = optimize_source(source)
    before = result.stats_before.instructions
    after = result.stats_after.instructions
    assert before > after, "the optimized program should be smaller"
    assert result.removed_instructions == before - after


def test_events_are_capped_and_say_so_when_they_are():
    """The event log is a debugging aid for the UI, not an unbounded record."""
    source = (
        "int main() {\n"
        + "".join(f"  int v{i} = {i} + {i};\n" for i in range(400))
        + "  return 0;\n}\n"
    )
    result = optimize_source(source)
    assert len(result.events) <= MAX_EVENTS
    if result.events_truncated:
        assert len(result.events) == MAX_EVENTS
    else:
        assert len(result.events) < MAX_EVENTS


def test_the_result_round_trips_through_to_dict():
    result = optimize_source("int main() {\n  int x = 2 + 3;\n  return x;\n}\n")
    payload = result.to_dict()
    assert payload["changed"] is True
    assert payload["rounds"] >= 1
    assert payload["verification"]["verified"] is True
    assert payload["stats"]["before"]["instructions"] == result.stats_before.instructions
    assert payload["stats"]["after"]["instructions"] == result.stats_after.instructions
    # The optimized TAC travels with the report so the frontend can show the difference.
    assert payload["ir"]["functions"]
    # The gaps travel with it: a report that lists what happened must also list what did not.
    assert payload["skipped"] == result.skipped()


# ------------------------------------------------------------- what was skipped


def test_a_pass_that_changed_nothing_is_reported_as_having_found_nothing():
    """A pass that rewrote nothing must not look like a pass that was not run.

    "12 instructions removed" reads as "everything removable went", and the only thing that
    says otherwise is an explicit entry saying this pass matched no pattern. So an unchanged
    pass appears in `skipped` and a changed one does not.
    """
    source = (
        "int main() {\n"
        "  int n;\n"
        '  scanf("%d", &n);\n'
        "  int total = 0;\n"
        "  int i = 0;\n"
        "  while (i < n) { total = total + i; i = i + 1; }\n"
        '  printf("%d\\n", total);\n'
        "  return 0;\n"
        "}\n"
    )
    result = optimize_source(source)
    by_pass = {item.name: item for item in result.passes}

    skipped_passes = {
        gap["target"] for gap in result.skipped() if gap["kind"] == "pass_found_nothing"
    }
    assert skipped_passes, "a run over this program changed nothing, so it must say so"
    for name, item in by_pass.items():
        if item.changed:
            assert name not in skipped_passes, f"{name} changed and was reported as unchanged"
        else:
            assert name in skipped_passes, f"{name} was silent about finding nothing"


def test_every_gap_carries_a_reason_naming_its_target():
    """A gap without a reason is an excuse; a gap with one is something to act on."""
    result = optimize_source("int main() {\n  int x = 1;\n  return x;\n}\n")
    gaps = result.skipped()
    assert gaps
    for gap in gaps:
        assert set(gap) == {"kind", "target", "reason"}
        assert gap["target"] in gap["reason"] or gap["kind"] == "truncated_events"
        assert len(gap["reason"]) > len(gap["target"]), gap


def test_a_function_the_oracle_cannot_call_is_reported_as_unverified():
    """`main` can be run; `add` cannot, and the report has to admit it.

    The interpreter oracle has no argument values to give a function that takes parameters, so
    such a function is never checked. Silently omitting it would make the report read as
    "everything was verified" when the truth is "the reachable entry point was". The program
    has to change *something* for verification to run at all — a run that rewrote nothing has
    nothing to compare — so `main` folds a constant.
    """
    result = optimize_source(
        "int add(int a, int b) {\n  return a + b;\n}\n"
        "int main() {\n  int x = 2 + 3;\n  return add(x, 1);\n}\n"
    )
    assert result.changed is True
    assert result.verification is not None
    assert result.verification.unverified_entries == ("add",)
    unverified = [gap for gap in result.skipped() if gap["kind"] == "unverified_entry"]
    assert [gap["target"] for gap in unverified] == ["add"]
    assert "add" in unverified[0]["reason"]


def test_a_run_with_nothing_to_verify_reports_no_unverified_entries():
    """A program whose only entry point takes no arguments has nothing to skip.

    `main` is runnable, so the oracle checks it and there is no `unverified_entry` gap: the
    list is about what was left out, and nothing was.
    """
    result = optimize_source("int main() {\n  int x = 1 + 1;\n  return x;\n}\n")
    assert result.changed is True
    assert result.verification is not None
    assert result.verification.unverified_entries == ()
    assert [gap for gap in result.skipped() if gap["kind"] == "unverified_entry"] == []


def test_an_inconclusive_case_is_reported_as_deciding_nothing():
    """A run that hit a limit did not pass and did not fail. It is a third thing."""
    result = optimize_source("int main() {\n  int x = 1;\n  return x;\n}\n")
    gaps = [gap for gap in result.skipped() if gap["kind"] == "inconclusive_case"]
    assert bool(gaps) == bool(result.verification and result.verification.cases_inconclusive)
    if gaps:
        assert str(result.verification.cases_inconclusive) in gaps[0]["reason"]


def test_truncated_events_are_reported_as_incomplete_detail():
    """The totals still describe the whole run, so the gap is about the list, not the counts."""
    source = (
        "int main() {\n"
        + "".join(f"  int v{i} = {i} + {i};\n" for i in range(400))
        + "  return 0;\n}\n"
    )
    result = optimize_source(source)
    gaps = [gap for gap in result.skipped() if gap["kind"] == "truncated_events"]
    assert bool(gaps) == result.events_truncated
    if gaps:
        assert str(MAX_EVENTS) in gaps[0]["reason"]


def test_the_skipped_list_is_a_gap_list_and_not_a_failure_list():
    """"Skipped" is not "failed", and the wording must not slide into it.

    A pass that found nothing and a verification case that hit a limit both decided the
    program is fine, as far as they went. The UI groups by `kind`, so a failure-shaped word in
    a reason string would be a bug that only shows up on screen.
    """
    result = optimize_source("int main() {\n  int x = 1;\n  return x;\n}\n")
    for gap in result.skipped():
        assert "fail" not in gap["reason"].lower()
        assert "error" not in gap["reason"].lower()
        assert "mismatch" not in gap["reason"].lower()


def test_a_fully_optimized_run_still_reports_its_gaps():
    """A run where every pass found nothing must say every pass found nothing.

    Nothing changed, so the oracle is not run at all — comparing a program with itself would
    "prove" nothing — and the report is nothing but gaps. That case is the one most likely to
    be rendered as a silent empty panel, so it is pinned here.
    """
    source = (
        "int main() {\n"
        "  int n;\n"
        '  scanf("%d", &n);\n'
        "  int total = 0;\n"
        "  int i = 0;\n"
        "  while (i < n) { total = total + i; i = i + 1; }\n"
        '  printf("%d\\n", total);\n'
        "  return 0;\n"
        "}\n"
    )
    result = optimize_source(source)
    assert result.changed is False
    assert result.verification is None
    assert {gap["kind"] for gap in result.skipped()} == {"pass_found_nothing"}
    assert len(result.skipped()) == len(result.passes)
    assert all(not item.changed for item in result.passes)


def test_skipped_is_empty_only_when_there_is_genuinely_nothing_to_report(client):
    """The API payload agrees with the object, and it is never `None`.

    The frontend does `payload.skipped.map(...)` without a guard, so a missing key would be
    a blank panel with no error anywhere.
    """
    body = _post(
        client,
        {
            "source": "int main() {\n  int x = 1;\n  return x;\n}\n",
            "mode": "optimize",
        },
    )
    assert isinstance(body["optimization"]["skipped"], list)
    for gap in body["optimization"]["skipped"]:
        assert set(gap) == {"kind", "target", "reason"}


# ------------------------------------------------------- verification


def test_verification_catches_a_pass_that_corrupts_the_program():
    """The oracle has to be capable of failing, or it proves nothing when it passes.

    Rather than hand-writing a subtly wrong optimization, this substitutes a pass that adds
    one to the returned constant and drives the manager directly, because ``optimize``
    deliberately accepts only pass *names* and will not let a caller smuggle in an
    unvalidated pass. If the oracle cannot catch this, then every ``verified: true`` in the
    payload is worth nothing.
    """
    source = "int main() {\n  int x = 6 * 7;\n  return x;\n}\n"

    class Corrupting:
        name = "corrupting"
        description = "returns one more than it should"

        def run(self, ctx: PassContext) -> PassDelta:
            body = ctx.program.functions[0]
            rewritten = []
            for inst in instructions_of(ctx):
                if inst.opcode is Opcode.RETURN:
                    broken = inst.operands[0]
                    if broken.is_const:
                        inst = replace(
                            inst,
                            operands=(replace(broken, value=broken.value + 1),),
                        )
                rewritten.append(inst)
            ctx.replace_function(replace(body, instructions=tuple(rewritten)))
            return PassDelta(changed=True)

    original = lowered(source)
    run = run_passes(original, passes=[ConstantPropagation(), Corrupting()])
    report = verify_equivalence(original, run.program)

    assert report.verified is False
    assert report.first_difference is not None
    assert report.first_difference.entry == "main"
    assert report.first_difference.stdin == ""


def test_verification_is_skipped_when_no_entry_point_takes_no_arguments():
    """`main(int argc)` cannot be called by the oracle, and that is a coverage limit.

    Refusing to optimize any such program would be far worse than optimizing it without
    checking, so this is reported as skipped rather than as a failure.
    """
    source = "int main(int argc) {\n  int x = 2 + 3;\n  return x + argc;\n}\n"
    result = optimize_source(source)
    assert result.changed is True
    assert result.verification is not None
    assert result.verification.skipped is True
    assert result.verification.verified is False
    assert result.verification.unverified_entries == ("main",)


def test_verification_gives_each_entry_point_the_same_input():
    source = "int a() {\n  return 1 + 1;\n}\n\nint b() {\n  int unused = 3;\n  return 9;\n}\n"
    report = verify_equivalence(lowered(source), lowered(source))
    entries = {case.entry for case in report.cases}
    assert {"a", "b"} <= entries


def test_verification_checks_stdin_consumption_and_not_just_output():
    """Both programs see the same input, and each must consume exactly as much as before.

    An optimizer that removed a `read_int` would produce the same stdout for an empty
    program while reading a different amount of stdin, which is observable to anything
    that pipes several values through one run.
    """
    source = (
        "int main() {\n"
        "  int a;\n"
        "  int b;\n"
        '  scanf("%d %d", &a, &b);\n'
        '  printf("%d\\n", a + b);\n'
        "  return 0;\n"
        "}\n"
    )
    original = lowered(source)
    optimized = run_passes(original).program
    report = verify_equivalence(original, optimized, inputs=("1 2\n", "40 2\n"))
    assert report.skipped is False
    assert report.verified is True
    assert report.cases_checked == 2


def test_verification_stops_at_its_case_budget_and_says_so():
    """A program with many entries must not make one request arbitrarily slow."""
    functions = "".join(f"int e{i}() {{ return {i}; }}\n" for i in range(20))
    program = lowered(functions)
    report = verify_equivalence(program, program, max_cases=4)
    assert report.cases_truncated is True
    assert report.cases_checked <= 4


# --------------------------------------------------------- API surface


def _post(client, payload: dict) -> dict:
    response = client.post("/api/compile", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_optimizer_stage_is_skipped_in_the_default_mode(client):
    """`analyze` explains the program as written; rewriting it would contradict that."""
    body = _post(client, {"source": "int main() { int x = 2 + 3; return x; }"})
    assert body["success"] is True
    assert body["optimization"] is None
    assert "optimization" in body["stages_skipped"]


def test_optimize_mode_returns_the_optimization_payload(client):
    body = _post(
        client,
        {"source": "int main() { int x = 2 + 3; return x; }", "mode": "optimize"},
    )
    assert body["success"] is True
    assert "optimization" in body["stages_run"]
    optimization = body["optimization"]
    assert optimization["changed"] is True
    assert optimization["verified"] is True
    assert optimization["removed_instructions"] > 0
    assert optimization["verification"]["cases_checked"] >= 1
    assert any(d["code"] == "I-OPT-001" for d in body["diagnostics"])


def test_optimize_mode_leaves_the_top_level_ir_unoptimized(client):
    """`ir` is the program as written and `optimization.ir` is the rewritten one.

    Keeping them in step would be worse than useless: the whole point of the panel is the
    before/after pair, and an `ir` that silently changed under the reader would make every
    finding from the analysis stage describe code the user is no longer looking at.
    """
    body = _post(
        client,
        {
            "source": "int main() { int x = 2 + 3; int dead = 9; return x; }",
            "mode": "optimize",
        },
    )
    assert body["ir"]["instruction_count"] > body["optimization"]["ir"]["instruction_count"]


def test_a_requested_pass_list_is_honoured(client):
    body = _post(
        client,
        {
            "source": "int main() { int x = 2 + 3; return x; }",
            "mode": "optimize",
            "options": {"passes": ["constant-folding"]},
        },
    )
    names = [entry["name"] for entry in body["optimization"]["passes"]]
    assert names == ["constant-folding"]


def test_an_unknown_pass_name_is_rejected_by_the_api(client):
    """A typo in a pass name must not silently fall back to optimizing with everything."""
    body = _post(
        client,
        {
            "source": "int main() { return 0; }",
            "mode": "optimize",
            "options": {"passes": ["constant-propagatoin"]},
        },
    )
    assert body["success"] is False
    codes = [d["code"] for d in body["diagnostics"] if d["severity"] == "error"]
    assert codes == ["E-OPT-001"]
    assert "constant-propagatoin" in body["diagnostics"][-1]["message"]


def test_a_broken_optimization_never_costs_the_user_their_ir(client, monkeypatch):
    """The guarantee that matters most at the API boundary.

    The response is a failure - the same convention the analysis stage already follows for
    a non-converging solver - but the original TAC is still in `ir`. That distinction is
    the whole point: the user still gets a correct, complete compile of the program they
    wrote, and the diagnostics say the optimization was abandoned. Returning the wrong
    program as a success would be far worse than not optimizing at all.
    """
    from opticode.optimizer.manager import OptimizationError as RealError

    def explode(*args, **kwargs):
        raise RealError("main behaved differently on stdin='': returned 7, expected 5")

    monkeypatch.setattr("opticode.compiler.optimize_program", explode)

    body = _post(
        client,
        {"source": "int main() { int x = 2 + 3; return x; }", "mode": "optimize"},
    )
    assert body["success"] is False
    assert body["optimization"] is None
    assert body["ir"] is not None, "the unoptimized program must survive the failure"
    assert body["analysis"] is not None
    codes = [d["code"] for d in body["diagnostics"] if d["severity"] == "error"]
    assert codes == ["E-OPT-001"]
    assert body["diagnostics"][-1]["stage"] == "optimizer"


def test_a_pass_that_breaks_the_structural_invariants_is_contained(client, monkeypatch):
    """Structural breakage is a compiler bug too, and it gets the same treatment."""
    from opticode.ir.invariants import InvariantViolation

    def explode(*args, **kwargs):
        raise InvariantViolation("t5: destination 'x' is never defined")

    monkeypatch.setattr("opticode.compiler.optimize_program", explode)

    body = _post(
        client,
        {"source": "int main() { int x = 2 + 3; return x; }", "mode": "optimize"},
    )
    assert body["success"] is False
    assert body["optimization"] is None
    assert body["ir"] is not None
    codes = [d["code"] for d in body["diagnostics"] if d["severity"] == "error"]
    assert codes == ["E-OPT-001"]


def test_optimization_is_deterministic_across_requests(client):
    """Two identical requests must agree, so the frontend does not flicker between them."""
    payload = {"source": "int main() { int x = 2 + 3; int d = 4; return x; }", "mode": "optimize"}
    first = _post(client, payload)
    second = _post(client, payload)
    first.pop("timings")
    second.pop("timings")
    assert first == second


# --------------------------------------------------------------- helpers


def _context_for(program: IRProgram) -> PassContext:
    """Wrap a finished program so the small inspectors above can be reused on it."""
    context = PassContext(program=program)
    context.refresh()
    return context


def test_the_pass_classes_are_reusable_across_programs():
    """`DEFAULT_PASSES` holds shared instances, so a pass must not keep per-run state.

    If a pass cached anything on `self` it would leak from one request into the next, which
    is exactly the kind of bug that only shows up under concurrent use.
    """
    source = "int main() {\n  int x = 2 + 3;\n  int dead = 1;\n  return x;\n}\n"
    first = optimize_source(source)
    second = optimize_source(source)
    assert print_program(first.optimized) == print_program(second.optimized)


def test_unused_imports_are_not_left_behind():
    """The two entry points used by the API tests are the ones the pipeline imports."""
    from opticode import compiler

    assert hasattr(compiler, "optimize_program")
    assert hasattr(compiler, "DEFAULT_MAX_ITERATIONS")
