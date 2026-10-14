"""Hand-written CFG tests.

The lowered programs in ``test_ir.py`` prove the builder agrees with the lowering. These
tests use instructions built by hand instead, so a bug that cancels out — a leader missed
in the same place lowering happens to avoid, an off-by-one in block numbering — cannot
hide behind a coincidence.
"""

from __future__ import annotations

import pytest

from opticode.cfg import CFGError, EdgeKind, build_cfg, build_program_cfgs
from opticode.core.spans import SourceSpan
from opticode.ir.lower import lower
from opticode.ir.operands import Operand
from opticode.ir.tac import Instruction, IRFunction, Opcode
from opticode.parser import parse
from opticode.semantic import analyze
from opticode.semantic.types import TypeName

SPAN = SourceSpan(1, 1, 1, 1)


def operand(text: str) -> Operand:
    """A numeric literal for a digit string, otherwise a temporary of that name."""
    if text.lstrip("-").isdigit():
        return Operand.const(int(text))
    return Operand.temp(text, TypeName.INT)


def ins(
    id_: str, opcode: Opcode, /, dest: str | None = None, op: str | None = None,
    target: str | None = None, operands: tuple[str, ...] = (),
) -> Instruction:
    return Instruction(
        id=id_,
        opcode=opcode,
        span=SPAN,
        dest=Operand.temp(dest, TypeName.INT) if dest else None,
        operands=tuple(operand(item) for item in operands),
        target=target,
        op=op,
        ast_node_id="n1",
        operand_origin_ids=("n1",) * len(operands),
    )


def func(instructions: list[Instruction], name: str = "f") -> IRFunction:
    return IRFunction(
        name=name, return_type=TypeName.INT, instructions=tuple(instructions)
    )


# ------------------------------------------------------------------ straight line


def test_straight_line_is_one_block():
    cfg = build_cfg(
        func([ins("i1", Opcode.COPY, dest="t", operands=("1",)),
              ins("i2", Opcode.RETURN, operands=("t",))])
    )
    assert len(cfg.blocks) == 1
    assert cfg.blocks[0].id == "B1"
    assert cfg.blocks[0].leader_reason == "function entry"
    assert cfg.blocks[0].successors == (cfg.exit,)


def test_straight_line_has_no_loops():
    cfg = build_cfg(func([ins("i1", Opcode.RETURN, operands=("0",))]))
    assert not cfg.has_loops()
    assert cfg.back_edges == ()


def test_empty_function_still_has_a_graph():
    """A void function with an empty body lowers to nothing, but still needs a block."""
    cfg = build_cfg(func([]))
    assert len(cfg.blocks) == 1
    assert cfg.blocks[0].is_empty
    assert cfg.blocks[0].reachable
    assert cfg.linearize() == []


# -------------------------------------------------------------------- leaders


def test_a_jump_target_starts_a_block():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.GOTO, target="L2"),
            ins("i2", Opcode.RETURN, operands=("0",)),
            ins("i3", Opcode.LABEL, target="L2"),
            ins("i4", Opcode.RETURN, operands=("1",)),
        ])
    )
    assert [block.id for block in cfg.blocks] == ["B1", "B2", "B3"]
    assert cfg.blocks[1].leader_reason == "follows goto"
    assert cfg.blocks[2].leader_reason == "target of goto L2"
    assert cfg.blocks[2].labels == ("L2",)


def test_the_instruction_after_a_return_starts_a_block():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.RETURN, operands=("0",)),
            ins("i2", Opcode.RETURN, operands=("1",)),
        ])
    )
    assert len(cfg.blocks) == 2
    assert cfg.blocks[1].leader_reason == "follows return"


def test_the_instruction_after_a_goto_starts_a_block():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.GOTO, target="L1"),
            ins("i2", Opcode.LABEL, target="L1"),
            ins("i3", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert len(cfg.blocks) == 2
    assert cfg.blocks[1].leader_reason == "target of goto L1"


def test_the_instruction_after_a_conditional_starts_a_block():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.IF_FALSE, operands=("0",), target="L2"),
            ins("i2", Opcode.RETURN, operands=("0",)),
            ins("i3", Opcode.LABEL, target="L2"),
            ins("i4", Opcode.RETURN, operands=("1",)),
        ])
    )
    assert len(cfg.blocks) == 3
    assert cfg.blocks[2].leader_reason == "target of if_false L2"


def test_a_block_that_only_follows_a_jump_names_the_jump():
    """When a block is a jump target, that is the more informative reason."""
    cfg = build_cfg(
        func([
            ins("i1", Opcode.GOTO, target="L2"),
            ins("i2", Opcode.LABEL, target="L1"),
            ins("i3", Opcode.RETURN, operands=("0",)),
            ins("i4", Opcode.LABEL, target="L2"),
            ins("i5", Opcode.RETURN, operands=("1",)),
        ])
    )
    assert len(cfg.blocks) == 3
    assert cfg.blocks[1].leader_reason == "follows goto"
    assert cfg.blocks[2].leader_reason == "target of goto L2"


def test_label_opcodes_are_absorbed_into_their_block():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.LABEL, target="L1"),
            ins("i2", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert [item.id for item in cfg.blocks[0].instructions] == ["i2"]
    assert cfg.blocks[0].labels == ("L1",)


def test_consecutive_labels_share_one_block():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.LABEL, target="L1"),
            ins("i2", Opcode.LABEL, target="L2"),
            ins("i3", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert len(cfg.blocks) == 1
    assert set(cfg.blocks[0].labels) == {"L1", "L2"}


def test_consecutive_labels_that_are_leaders_do_not_leave_an_empty_block():
    """Two adjacent labels, each a leader for a different reason, are still one block.

    ``L2`` is a leader simply because it follows the ``goto``; ``L1`` is a leader because
    the ``goto`` targets it. Splitting at both leaves the first block holding a label and
    no instructions, which is not a basic block at all. The labels fold onto the code they
    name, and a jump to either one still lands on that code.
    """
    cfg = build_cfg(
        func([
            ins("i1", Opcode.GOTO, target="L1"),
            ins("i2", Opcode.LABEL, target="L2"),
            ins("i3", Opcode.LABEL, target="L1"),
            ins("i4", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert all(block.instructions for block in cfg.blocks), "no block may be empty"
    assert len(cfg.blocks) == 2
    merged = cfg.blocks[1]
    assert set(merged.labels) == {"L1", "L2"}
    assert [item.id for item in merged.instructions] == ["i4"]
    assert [(edge.src, edge.dst, edge.kind) for edge in cfg.edges] == [
        ("B1", "B2", EdgeKind.JUMP),
        ("B2", cfg.exit, EdgeKind.RETURN),
    ], "the jump must resolve to the merged block, not to a label-only stub"


# ---------------------------------------------------------------------- edges


def test_a_goto_gives_one_jump_edge():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.GOTO, target="L1"),
            ins("i2", Opcode.LABEL, target="L1"),
            ins("i3", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert [(edge.src, edge.dst, edge.kind) for edge in cfg.edges] == [
        ("B1", "B2", EdgeKind.JUMP),
        ("B2", cfg.exit, EdgeKind.RETURN),
    ]


def test_a_conditional_gives_a_true_and_a_false_edge():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.IF_FALSE, operands=("0",), target="L2"),
            ins("i2", Opcode.RETURN, operands=("0",)),
            ins("i3", Opcode.LABEL, target="L2"),
            ins("i4", Opcode.RETURN, operands=("1",)),
        ])
    )
    kinds = {(edge.dst, edge.kind) for edge in cfg.edges if edge.src == "B1"}
    assert kinds == {("B2", EdgeKind.TRUE), ("B3", EdgeKind.FALSE)}


def test_a_return_gives_a_return_edge_to_the_synthetic_exit():
    cfg = build_cfg(func([ins("i1", Opcode.RETURN, operands=("0",))]))
    assert [(edge.dst, edge.kind) for edge in cfg.edges] == [
        (cfg.exit, EdgeKind.RETURN)
    ]
    assert cfg.blocks[0].successors == (cfg.exit,)
    assert cfg.blocks[0].predecessors == ()


def test_predecessors_are_the_mirror_of_successors():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.IF_FALSE, operands=("0",), target="L2"),
            ins("i2", Opcode.RETURN, operands=("0",)),
            ins("i3", Opcode.LABEL, target="L2"),
            ins("i4", Opcode.RETURN, operands=("1",)),
        ])
    )
    for block in cfg.blocks:
        for successor in block.successors:
            if successor == cfg.exit:
                continue
            assert block.id in cfg.predecessors_of(successor)


def test_an_if_jumping_to_the_next_block_has_one_successor():
    """`if_false c goto next` is a no-op branch, so both outcomes share a block."""
    cfg = build_cfg(
        func([
            ins("i1", Opcode.IF_FALSE, operands=("0",), target="L1"),
            ins("i2", Opcode.LABEL, target="L1"),
            ins("i3", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert cfg.blocks[0].successors == ("B2",)


# ---------------------------------------------------------------------- loops


def test_a_self_loop_is_a_back_edge():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.LABEL, target="L1"),
            ins("i2", Opcode.IF_FALSE, operands=("0",), target="L2"),
            ins("i3", Opcode.GOTO, target="L1"),
            ins("i4", Opcode.LABEL, target="L2"),
            ins("i5", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert cfg.has_loops()
    assert [(edge.src, edge.dst) for edge in cfg.back_edges] == [("B2", "B1")]
    assert cfg.loop_headers() == ("B1",)


def test_nested_loops_each_produce_a_back_edge():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.LABEL, target="LOOP"),
            ins("i2", Opcode.IF_FALSE, operands=("0",), target="DONE"),
            ins("i3", Opcode.LABEL, target="INNER"),
            ins("i4", Opcode.IF_FALSE, operands=("0",), target="AFTER"),
            ins("i5", Opcode.GOTO, target="INNER"),
            ins("i6", Opcode.LABEL, target="AFTER"),
            ins("i7", Opcode.GOTO, target="LOOP"),
            ins("i8", Opcode.LABEL, target="DONE"),
            ins("i9", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert len(cfg.back_edges) == 2
    # B1 is the outer loop header and B2 the inner one.
    assert set(cfg.loop_headers()) == {"B1", "B2"}


def test_a_forward_jump_is_not_a_back_edge():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.GOTO, target="L1"),
            ins("i2", Opcode.RETURN, operands=("0",)),
            ins("i3", Opcode.LABEL, target="L1"),
            ins("i4", Opcode.RETURN, operands=("1",)),
        ])
    )
    assert not cfg.has_loops()


# --------------------------------------------------------- unreachable blocks


def test_code_after_a_return_is_unreachable_but_retained():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.RETURN, operands=("0",)),
            ins("i2", Opcode.RETURN, operands=("1",)),
        ])
    )
    assert len(cfg.blocks) == 2
    assert cfg.blocks[0].reachable
    assert not cfg.blocks[1].reachable
    assert [block.id for block in cfg.unreachable_blocks] == ["B2"]
    assert cfg.blocks[1].instructions[0].id == "i2"


def test_unreachable_blocks_are_excluded_from_reverse_postorder():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.RETURN, operands=("0",)),
            ins("i2", Opcode.RETURN, operands=("1",)),
        ])
    )
    assert cfg.rpo == ("B1",)


def test_a_reachable_block_after_an_unreachable_one_is_still_found():
    """Entry, dead code, then code jumped back to: the last block is reachable."""
    cfg = build_cfg(
        func([
            ins("i1", Opcode.GOTO, target="L1"),
            ins("i2", Opcode.RETURN, operands=("9",)),
            ins("i3", Opcode.LABEL, target="L1"),
            ins("i4", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert [block.id for block in cfg.unreachable_blocks] == ["B2"]
    assert "B3" in cfg.rpo


# ----------------------------------------------------------- reverse postorder


def test_reverse_postorder_visits_blocks_in_a_usable_order():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.LABEL, target="LOOP"),
            ins("i2", Opcode.IF_FALSE, operands=("0",), target="DONE"),
            ins("i3", Opcode.GOTO, target="LOOP"),
            ins("i4", Opcode.LABEL, target="DONE"),
            ins("i5", Opcode.RETURN, operands=("0",)),
        ])
    )
    assert cfg.rpo == ("B1", "B2", "B3")
    # Every non-back edge must point forward in RPO, which is what makes the worklist
    # algorithm converge quickly.
    position = {block_id: index for index, block_id in enumerate(cfg.rpo)}
    for edge in cfg.edges:
        if not edge.is_back_edge and edge.dst in position:
            assert position[edge.src] < position[edge.dst]


def test_reverse_postorder_of_a_straight_line_function_is_source_order():
    cfg = build_cfg(
        func([
            ins("i1", Opcode.IF_FALSE, operands=("0",), target="L2"),
            ins("i2", Opcode.RETURN, operands=("0",)),
            ins("i3", Opcode.LABEL, target="L2"),
            ins("i4", Opcode.RETURN, operands=("1",)),
        ])
    )
    assert cfg.rpo == ("B1", "B2", "B3")


# -------------------------------------------------------------------- errors


def test_a_jump_to_a_missing_label_is_an_error():
    with pytest.raises(CFGError, match="unknown label"):
        build_cfg(func([ins("i1", Opcode.GOTO, target="NOPE"),
                        ins("i2", Opcode.RETURN, operands=("0",))]))


def test_a_duplicate_label_is_an_error():
    with pytest.raises(CFGError, match="duplicate label"):
        build_cfg(func([
            ins("i1", Opcode.LABEL, target="L1"),
            ins("i2", Opcode.RETURN, operands=("0",)),
            ins("i3", Opcode.LABEL, target="L1"),
            ins("i4", Opcode.RETURN, operands=("0",)),
        ]))


def test_a_trailing_conditional_with_no_fallthrough_is_an_error():
    with pytest.raises(CFGError, match="nowhere to fall through"):
        build_cfg(func([
            ins("i1", Opcode.GOTO, target="L1"),
            ins("i2", Opcode.LABEL, target="L1"),
            ins("i3", Opcode.IF_FALSE, operands=("0",), target="L1"),
        ]))


def test_a_label_with_no_instruction_after_it_is_accepted():
    """This is what `break` at the end of a loop body lowers to."""
    cfg = build_cfg(func([
        ins("i1", Opcode.LABEL, target="L1"),
        ins("i2", Opcode.GOTO, target="L2"),
        ins("i3", Opcode.LABEL, target="L2"),
    ]))
    assert cfg.blocks[-1].labels == ("L2",)


# ------------------------------------------------------- serialization, lookup


def test_cfg_serializes_for_the_api():
    cfg = build_cfg(func([ins("i1", Opcode.RETURN, operands=("0",))]))
    payload = cfg.to_dict()
    assert payload["function"] == "f"
    assert payload["block_count"] == 1
    assert payload["has_loops"] is False
    assert payload["blocks"][0]["instruction_ids"] == ["i1"]
    assert payload["exit"] == "EXIT"


def test_block_of_instruction_finds_the_owner():
    cfg = build_cfg(func([
        ins("i1", Opcode.IF_FALSE, operands=("0",), target="L1"),
        ins("i2", Opcode.RETURN, operands=("0",)),
        ins("i3", Opcode.LABEL, target="L1"),
        ins("i4", Opcode.RETURN, operands=("1",)),
    ]))
    assert cfg.block_of_instruction("i2").id == "B2"
    assert cfg.block_of_instruction("i4").id == "B3"
    assert cfg.block_of_instruction("nope") is None


# ------------------------------------------------------------------- linearize


def test_a_fresh_graph_needs_no_extra_jumps():
    """Blocks are numbered so a fall-through successor is always the next one."""
    cfg = build_cfg(func([
        ins("i1", Opcode.IF_FALSE, operands=("0",), target="L1"),
        ins("i2", Opcode.RETURN, operands=("0",)),
        ins("i3", Opcode.LABEL, target="L1"),
        ins("i4", Opcode.RETURN, operands=("1",)),
    ]))
    linear = cfg.linearize()
    assert [item.id for item in linear] == ["i1", "i2", "i3", "i4"]
    assert not [item for item in linear if item.synthetic]


def test_linearize_reproduces_the_original_instruction_list():
    """A fresh graph must linearize back to exactly what came out of lowering.

    Every later pass edits a function and re-linearizes it, so if this round trip is not
    the identity then every one of them inherits the difference.
    """
    cfg = build_cfg(func([
        ins("i1", Opcode.LABEL, target="L1"),
        ins("i2", Opcode.COPY, dest="t", operands=("1",)),
        ins("i3", Opcode.IF_FALSE, operands=("t",), target="L2"),
        ins("i4", Opcode.PRINT, operands=("t",)),
        ins("i5", Opcode.LABEL, target="L2"),
        ins("i6", Opcode.RETURN, operands=("0",)),
    ]))
    assert cfg.linearize() == list(cfg.function.instructions)


def test_linearize_keeps_unreachable_instructions():
    """Deleting unreachable code is a pass's job, not linearization's."""
    cfg = build_cfg(func([
        ins("i1", Opcode.RETURN, operands=("0",)),
        ins("i2", Opcode.RETURN, operands=("1",)),
    ]))
    assert "i2" in {item.id for item in cfg.linearize()}


def _splice(cfg, drop_id: str, unlabel_id: str = ""):
    """Remove one block, optionally stripping another's labels, as a later pass might."""
    from dataclasses import replace as dataclass_replace

    blocks = []
    for block in cfg.blocks:
        if block.id == drop_id:
            continue
        if block.id == unlabel_id:
            block = dataclass_replace(block, label_instructions=())
        blocks.append(block)
    return dataclass_replace(cfg, blocks=tuple(blocks))


def test_linearize_inserts_a_jump_when_a_block_is_removed():
    """Later passes delete blocks, so linearization has to repair the fall-through.

    B1 is straight-line and falls into B2. Deleting B2 leaves control to continue into
    B3, further down the layout, which only an explicit goto can express.
    """
    cfg = build_cfg(func([
        ins("i1", Opcode.LABEL, target="Ltop"),
        ins("i2", Opcode.COPY, dest="t", operands=("1",)),
        ins("i3", Opcode.LABEL, target="Lmid"),
        ins("i4", Opcode.RETURN, operands=("t",)),
        ins("i5", Opcode.LABEL, target="Lnext"),
        ins("i6", Opcode.GOTO, target="Lmid"),
        ins("i7", Opcode.RETURN, operands=("2",)),
    ]))
    assert [block.id for block in cfg.blocks] == ["B1", "B2", "B3", "B4"]
    assert cfg.blocks[0].successors == ("B2",)

    linear = _splice(cfg, "B2").linearize()
    inserted = [item for item in linear if item.id == "B1_goto"]
    assert len(inserted) == 1
    assert inserted[0].opcode is Opcode.GOTO
    assert inserted[0].target == "Lnext"
    assert inserted[0].synthetic is True
    # The jump stands in for B1's own fall-through, so it belongs after B1's instructions
    # and before B3's -- running it later would let B3's code execute first.
    assert linear.index(inserted[0]) > linear.index(
        next(item for item in linear if item.id == "i2")
    )
    assert linear.index(inserted[0]) < linear.index(
        next(item for item in linear if item.id == "i6")
    )


def test_linearize_refuses_to_jump_to_an_unlabelled_block():
    """A jump can only name a label, so an unlabelled target is reported, not guessed."""
    cfg = build_cfg(func([
        ins("i1", Opcode.LABEL, target="Ltop"),
        ins("i2", Opcode.COPY, dest="t", operands=("1",)),
        ins("i3", Opcode.LABEL, target="Lmid"),
        ins("i4", Opcode.RETURN, operands=("t",)),
        ins("i5", Opcode.LABEL, target="Lnext"),
        ins("i6", Opcode.GOTO, target="Lmid"),
        ins("i7", Opcode.RETURN, operands=("2",)),
    ]))
    with pytest.raises(CFGError, match="defines no label"):
        _splice(cfg, "B2", unlabel_id="B3").linearize()


def test_linearize_of_a_void_empty_function_is_empty():
    cfg = build_cfg(func([]))
    assert cfg.linearize() == []


def lower_cfg(source: str):
    program, diagnostics = parse(source)
    assert not [d for d in diagnostics if d.severity.value == "error"], diagnostics
    result = analyze(program)
    assert result.ok, [d.message for d in result.diagnostics]
    lowered = lower(result)
    assert lowered.ok, lowered.errors
    return lowered.program


def test_lowered_if_else_has_two_branches():
    cfg = build_cfg(
        lower_cfg("int f(int a) { if (a) { return 1; } else { return 2; } }").function("f")
    )
    # Lowering emits `return 1; goto Lcont` for the then-branch. The goto follows a
    # return, so it lands in unreachable code — which the graph keeps and flags rather
    # than deleting.
    live = [block for block in cfg.blocks if block.reachable]
    assert [block.id for block in live] == ["B1", "B2", "B4"]
    assert live[0].successors == ("B4", "B2")
    assert cfg.unreachable_blocks
    assert not cfg.has_loops()


def test_lowered_while_has_exactly_one_back_edge():
    cfg = build_cfg(lower_cfg("int f(int n) { int i = 0; while (i < n) { i = i + 1; } return i; }").function("f"))
    assert cfg.has_loops()
    assert len(cfg.back_edges) == 1


def test_every_example_builds_a_consistent_cfg():
    from pathlib import Path

    examples = sorted(
        (Path(__file__).resolve().parents[2] / "examples" / "valid").glob("*.c")
    )
    assert examples
    for path in examples:
        for name, cfg in build_program_cfgs(lower_cfg(path.read_text(encoding="utf-8"))).items():
            known = {block.id for block in cfg.blocks}
            for edge in cfg.edges:
                assert edge.src in known, f"{path.name}:{name} edge from {edge.src}"
                assert edge.dst == cfg.exit or edge.dst in known
            assert len(cfg.rpo) == len(cfg.reachable_blocks)
