"""The control-flow graph of one function.

A **basic block** is a maximal run of instructions with one entry and one exit. The
**leaders** are the only places a block may start:

1. the first instruction of the function;
2. any instruction named as the target of a ``goto`` or ``if_false``;
3. any instruction immediately after a ``goto``, ``if_false``, or ``return``.

Each block runs from its leader up to, but not including, the next leader.

Three decisions here are load-bearing for later passes:

* **Labels are not instructions.** A ``label`` opcode only ever marks a leader, so it is
  absorbed into the block it names and never appears inside one. That is what makes
  "block layout order" a meaningful thing to linearize.
* **Unreachable blocks are kept.** A block with no path from ENTRY stays in the graph
  with ``reachable=False`` instead of being deleted. Dead-code analysis has to *see*
  unreachable code in order to report it, and a pass that removed it first would have
  nothing left to explain.
* **Back edges point at an ancestor.** MiniC is structured, so its CFGs are reducible and
  each loop is a cycle through exactly one back edge, but nothing here assumes that — it
  simply marks retreating edges, which is what identifies loops.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from ..core.spans import SourceSpan
from ..ir.tac import Instruction, IRFunction, IRProgram, Opcode

__all__ = [
    "ENTRY",
    "EXIT",
    "BasicBlock",
    "CFG",
    "CFGError",
    "Edge",
    "EdgeKind",
    "build_cfg",
    "build_program_cfgs",
]

#: Synthetic node ids for the graph's entry and exit points.
ENTRY = "ENTRY"
EXIT = "EXIT"


class CFGError(Exception):
    """Raised when TAC cannot form a well-defined CFG.

    Every case here is a compiler bug rather than a user error — an unresolvable jump
    target, a duplicate label, a conditional jump with nothing to fall through to — so the
    pipeline reports it as an internal error.
    """


class EdgeKind(StrEnum):
    """Why one block flows into another."""

    FALLTHROUGH = "fallthrough"
    TRUE = "true"
    FALSE = "false"
    JUMP = "jump"
    RETURN = "return"


@dataclass(frozen=True, slots=True)
class BasicBlock:
    """A maximal straight-line run of instructions.

    ``label_instructions`` holds the ``label`` opcodes that mark this block as a leader.
    They are kept out of ``instructions`` because a label never executes, but they are kept
    rather than discarded so :meth:`CFG.linearize` can put them back — a jump in the
    linearized list would otherwise have nothing to land on.
    """

    id: str
    instructions: tuple[Instruction, ...] = ()
    label_instructions: tuple[Instruction, ...] = ()
    #: Why the block starts here, as a short phrase suitable for the UI.
    leader_reason: str = ""
    successors: tuple[str, ...] = ()
    predecessors: tuple[str, ...] = ()
    #: False when no path leads here from ENTRY.
    reachable: bool = True
    span: SourceSpan = field(default_factory=SourceSpan)

    @property
    def labels(self) -> tuple[str, ...]:
        """The labels this block defines, in source order."""
        return tuple(
            item.target for item in self.label_instructions if item.target is not None
        )

    @property
    def is_empty(self) -> bool:
        return not self.instructions

    @property
    def start_id(self) -> str:
        return self.instructions[0].id if self.instructions else ""

    @property
    def last_id(self) -> str:
        return self.instructions[-1].id if self.instructions else ""

    def terminator(self) -> Instruction | None:
        """The last instruction, which is what decides this block's successors."""
        return self.instructions[-1] if self.instructions else None

    def instruction_ids(self) -> tuple[str, ...]:
        return tuple(instruction.id for instruction in self.instructions)

    def to_dict(self) -> dict[str, object]:
        return {
            "id": self.id,
            "labels": list(self.labels),
            "leader_reason": self.leader_reason,
            "instructions": [instruction.to_dict() for instruction in self.instructions],
            "instruction_ids": list(self.instruction_ids()),
            "successors": list(self.successors),
            "predecessors": list(self.predecessors),
            "reachable": self.reachable,
            "span": self.span.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class Edge:
    """One control-flow transfer."""

    src: str
    dst: str
    kind: EdgeKind
    #: True when the destination is an ancestor of the source in a depth-first traversal.
    is_back_edge: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "src": self.src,
            "dst": self.dst,
            "kind": self.kind.value,
            "is_back_edge": self.is_back_edge,
        }


@dataclass(frozen=True, slots=True)
class CFG:
    """The control-flow graph of one function."""

    function: IRFunction
    blocks: tuple[BasicBlock, ...] = ()
    #: Edges between real blocks. The ENTRY and EXIT nodes are implicit.
    edges: tuple[Edge, ...] = ()
    entry: str = ""
    exit: str = EXIT
    #: Reachable block ids in reverse postorder.
    rpo: tuple[str, ...] = ()
    back_edges: tuple[Edge, ...] = ()

    # ------------------------------------------------------------- queries

    def block(self, block_id: str) -> BasicBlock | None:
        for block in self.blocks:
            if block.id == block_id:
                return block
        return None

    @property
    def reachable_blocks(self) -> tuple[BasicBlock, ...]:
        return tuple(block for block in self.blocks if block.reachable)

    @property
    def unreachable_blocks(self) -> tuple[BasicBlock, ...]:
        return tuple(block for block in self.blocks if not block.reachable)

    def block_of_instruction(self, instruction_id: str) -> BasicBlock | None:
        for block in self.blocks:
            if any(item.id == instruction_id for item in block.instructions):
                return block
        return None

    def successors_of(self, block_id: str) -> tuple[str, ...]:
        block = self.block(block_id)
        return block.successors if block is not None else ()

    def predecessors_of(self, block_id: str) -> tuple[str, ...]:
        block = self.block(block_id)
        return block.predecessors if block is not None else ()

    def has_loops(self) -> bool:
        return bool(self.back_edges)

    def loop_headers(self) -> tuple[str, ...]:
        """Distinct destinations of back edges, in first-seen order."""
        seen: list[str] = []
        for edge in self.back_edges:
            if edge.dst not in seen:
                seen.append(edge.dst)
        return tuple(seen)

    # ---------------------------------------------------------- linearize

    def linearize(self) -> list[Instruction]:
        """Re-emit the function as a linear instruction list in block layout order.

        This has to be correct on its own, because later passes delete blocks and
        rebuild the graph: whenever the next block in layout order is not the one control
        would actually reach, an explicit ``goto`` is inserted. Running the result must
        produce the same outcome as running the original function.
        """
        instructions: list[Instruction] = []
        for index, block in enumerate(self.blocks):
            next_id = self.blocks[index + 1].id if index + 1 < len(self.blocks) else None
            # Labels first, so a jump to one lands on the block that defines it.
            instructions.extend(block.label_instructions)
            instructions.extend(block.instructions)
            # The repair jump goes *after* the block it repairs: it stands in for a
            # fall-through that no longer runs into the next block in layout order.
            goto = self._missing_fallthrough(block, next_id)
            if goto is not None:
                instructions.append(goto)
        return instructions

    def _missing_fallthrough(
        self, block: BasicBlock, next_id: str | None
    ) -> Instruction | None:
        """The goto needed because control would skip past ``next_id``, if any.

        A fresh graph numbers blocks so a block's fall-through successor is always the
        next one, and the answer is ``None``. After a pass deletes a block the successor
        can sit further down the layout, and then the jump has to be spelled out.
        """
        terminator = block.terminator()
        if terminator is None or next_id is None:
            return None
        if terminator.opcode in (Opcode.GOTO, Opcode.RETURN):
            return None
        if terminator.opcode is Opcode.IF_FALSE:
            # Both outcomes are already covered by the terminator and the fall-through,
            # which is exactly the block emitted next.
            return None
        if next_id in block.successors:
            return None
        target = self._entry_label(next_id)
        return Instruction(
            id=f"{block.id}_goto",
            opcode=Opcode.GOTO,
            span=terminator.span,
            target=target,
            ast_node_id=terminator.ast_node_id,
            synthetic=True,
        )

    def _entry_label(self, block_id: str) -> str:
        block = self.block(block_id)
        if block is None:  # pragma: no cover - defensive
            raise CFGError(f"unknown block {block_id}")
        if not block.labels:
            raise CFGError(f"block {block_id} defines no label to jump to")
        return block.labels[0]

    def to_dict(self) -> dict[str, object]:
        return {
            "function": self.function.name,
            "return_type": self.function.return_type.value,
            "entry": self.entry,
            "exit": self.exit,
            "blocks": [block.to_dict() for block in self.blocks],
            "edges": [edge.to_dict() for edge in self.edges],
            "back_edges": [edge.to_dict() for edge in self.back_edges],
            "rpo": list(self.rpo),
            "block_count": len(self.blocks),
            "edge_count": len(self.edges),
            "has_loops": self.has_loops(),
        }


def _label_positions(instructions: tuple[Instruction, ...]) -> dict[str, int]:
    """Map each label to the index of the instruction that defines it.

    The label's *own* index is used rather than the one after it, because labels are
    absorbed into the block they mark. Anchoring on the following instruction would let a
    label that happens to sit after a ``return`` split a block in two, leaving an empty
    block holding nothing but the label.

    A label as the very last instruction is legal: it is the target of a jump out of the
    function, which is how ``break`` at the end of a loop body lowers.
    """
    positions: dict[str, int] = {}
    for index, instruction in enumerate(instructions):
        if instruction.opcode is not Opcode.LABEL:
            continue
        label = instruction.target
        if label is None:  # pragma: no cover - lowering always sets a target
            raise CFGError(f"label instruction {instruction.id} has no target")
        if label in positions:
            raise CFGError(f"duplicate label {label}")
        positions[label] = index
    return positions


def _leader_reasons(
    instructions: tuple[Instruction, ...], labels: dict[str, int]
) -> dict[int, str]:
    """Map each leader index to a short explanation of why it starts a block."""
    if not instructions:
        return {}
    reasons: dict[int, str] = {0: "function entry"}

    for instruction in instructions:
        if instruction.opcode not in (Opcode.GOTO, Opcode.IF_FALSE):
            continue
        target = instruction.target
        if target is None or target not in labels:
            raise CFGError(f"{instruction.id} jumps to unknown label {target!r}")
        kind = "goto" if instruction.opcode is Opcode.GOTO else "if_false"
        # The first jump to a label gets the credit; a loop header is usually both a jump
        # target and the instruction after another jump.
        reasons.setdefault(labels[target], f"target of {kind} {target}")

    # Found by walking rather than by arithmetic on ids, because instruction ids are
    # identifiers and not array positions.
    reason_for = {
        Opcode.RETURN: "follows return",
        Opcode.IF_FALSE: "follows conditional jump",
        Opcode.GOTO: "follows goto",
    }
    for index, instruction in enumerate(instructions):
        reason = reason_for.get(instruction.opcode)
        if reason is None or index + 1 >= len(instructions):
            continue
        reasons.setdefault(index + 1, reason)

    return reasons


def _build_blocks(
    function: IRFunction, labels: dict[str, int], reasons: dict[int, str]
) -> tuple[tuple[BasicBlock, ...], dict[str, str]]:
    """Split the instruction list at each leader, numbering blocks B1, B2, …"""
    instructions = function.instructions
    if not instructions:
        # A void function with an empty body lowers to no instructions at all. It still
        # gets one block, because ENTRY points at the first block of every function and
        # callers must never have to special-case a missing graph.
        return (BasicBlock(id="B1", leader_reason="function entry"),), {}

    starts = sorted(reasons)
    # Pair each leader with the next one, the last running to the end of the function.
    bounds = list(zip(starts, starts[1:] + [len(instructions)], strict=True))
    label_to_block: dict[str, str] = {}
    blocks: list[BasicBlock] = []

    for number, (start, stop) in enumerate(bounds, start=1):
        block_id = f"B{number}"
        members = instructions[start:stop]
        # Labels mark the leader and never execute, so they live beside the block rather
        # than inside it -- but they are kept, because linearize has to re-emit them.
        real = tuple(item for item in members if item.opcode is not Opcode.LABEL)
        defining = tuple(item for item in members if item.opcode is Opcode.LABEL)
        for label_instruction in defining:
            label_to_block[label_instruction.target or ""] = block_id
        blocks.append(
            BasicBlock(
                id=block_id,
                instructions=real,
                label_instructions=defining,
                leader_reason=reasons[start],
                span=SourceSpan(
                    start_line=members[0].span.start_line,
                    start_col=members[0].span.start_col,
                    end_line=members[-1].span.end_line,
                    end_col=members[-1].span.end_col,
                ),
            )
        )

    return tuple(blocks), label_to_block


def _dedupe(values: list[str]) -> tuple[str, ...]:
    seen: list[str] = []
    for value in values:
        if value not in seen:
            seen.append(value)
    return tuple(seen)


def _connect(
    blocks: tuple[BasicBlock, ...], label_to_block: dict[str, str]
) -> tuple[tuple[Edge, ...], tuple[BasicBlock, ...]]:
    """Compute successors, predecessors, and one edge per control transfer."""
    edges: list[Edge] = []
    successors: dict[str, list[str]] = {}
    predecessors: dict[str, list[str]] = {block.id: [] for block in blocks}

    for index, block in enumerate(blocks):
        next_id = blocks[index + 1].id if index + 1 < len(blocks) else None
        transfers: list[tuple[str, EdgeKind]] = []
        terminator = block.terminator()

        if terminator is not None:
            if terminator.opcode is Opcode.GOTO:
                target = label_to_block.get(terminator.target or "")
                if target is None:
                    raise CFGError(f"{terminator.id} jumps to unknown label")
                transfers.append((target, EdgeKind.JUMP))
            elif terminator.opcode is Opcode.IF_FALSE:
                target = label_to_block.get(terminator.target or "")
                if target is None:
                    raise CFGError(f"{terminator.id} jumps to unknown label")
                transfers.append((target, EdgeKind.FALSE))
                if next_id is None:
                    raise CFGError(
                        f"{terminator.id} is the last instruction of {block.id}, "
                        f"so its condition has nowhere to fall through"
                    )
                transfers.append((next_id, EdgeKind.TRUE))
            elif terminator.opcode is Opcode.RETURN:
                transfers.append((EXIT, EdgeKind.RETURN))
            elif next_id is not None:
                transfers.append((next_id, EdgeKind.FALLTHROUGH))

        successors[block.id] = _dedupe([target for target, _ in transfers])
        for target, kind in transfers:
            if kind is not EdgeKind.RETURN and target in predecessors:
                predecessors[target].append(block.id)
            edges.append(Edge(src=block.id, dst=target, kind=kind))

    connected = tuple(
        replace(
            block,
            successors=successors[block.id],
            predecessors=_dedupe(predecessors[block.id]),
        )
        for block in blocks
    )
    return tuple(edges), connected


def _mark_reachability(
    blocks: tuple[BasicBlock, ...], edges: tuple[Edge, ...]
) -> tuple[tuple[BasicBlock, ...], tuple[str, ...]]:
    """Flag blocks with no path from the entry block, and compute reverse postorder."""
    if not blocks:  # pragma: no cover - defensive
        return blocks, ()

    adjacency = _adjacency(blocks, edges)
    entry_id = blocks[0].id
    postorder: list[str] = []
    seen = {entry_id}
    # Iterative depth-first: deep TAC would otherwise run into Python's recursion limit.
    stack: list[tuple[str, int]] = [(entry_id, 0)]
    while stack:
        node, child_index = stack[-1]
        children = adjacency.get(node, ())
        if child_index < len(children):
            stack[-1] = (node, child_index + 1)
            child = children[child_index]
            if child not in seen:
                seen.add(child)
                stack.append((child, 0))
        else:
            postorder.append(node)
            stack.pop()

    marked = tuple(replace(block, reachable=block.id in seen) for block in blocks)
    return marked, tuple(reversed(postorder))


def _adjacency(
    blocks: tuple[BasicBlock, ...], edges: tuple[Edge, ...]
) -> dict[str, tuple[str, ...]]:
    """Successor ids per block, in edge order and without duplicates."""
    known = {block.id for block in blocks}
    outgoing: dict[str, list[str]] = {block.id: [] for block in blocks}
    for edge in edges:
        if edge.src in known and edge.dst in known:
            outgoing[edge.src].append(edge.dst)
    return {key: _dedupe(value) for key, value in outgoing.items()}


def _mark_back_edges(
    blocks: tuple[BasicBlock, ...], edges: tuple[Edge, ...]
) -> tuple[tuple[Edge, ...], tuple[Edge, ...]]:
    """Mark every edge whose destination is an ancestor of its source.

    One depth-first traversal from the entry block is enough: an edge to a node still on
    the stack is a retreating edge by definition.
    """
    if not blocks:  # pragma: no cover - defensive
        return edges, ()

    adjacency = _adjacency(blocks, edges)
    entry_id = blocks[0].id
    on_stack: set[str] = {entry_id}
    done: set[str] = set()
    back: list[Edge] = []
    stack: list[tuple[str, int]] = [(entry_id, 0)]

    while stack:
        node, child_index = stack[-1]
        children = adjacency.get(node, ())
        if child_index < len(children):
            stack[-1] = (node, child_index + 1)
            child = children[child_index]
            retreating = child in on_stack
            if retreating:
                back.extend(
                    edge
                    for edge in edges
                    if edge.src == node and edge.dst == child and not edge.is_back_edge
                )
            if child not in on_stack and child not in done:
                on_stack.add(child)
                stack.append((child, 0))
        else:
            on_stack.discard(node)
            done.add(node)
            stack.pop()

    marked = tuple(
        replace(edge, is_back_edge=edge in back) if not edge.is_back_edge else edge
        for edge in edges
    )
    return marked, tuple(edge for edge in marked if edge.is_back_edge)


def build_cfg(function: IRFunction) -> CFG:
    """Build the control-flow graph of one function.

    Raises :class:`CFGError` for an unresolvable jump target, a duplicate label, or a
    conditional jump with no fall-through. Those are compiler bugs, so the pipeline turns
    them into internal-error diagnostics rather than blaming the user's program.
    """
    labels = _label_positions(function.instructions)
    reasons = _leader_reasons(function.instructions, labels)
    blocks, label_to_block = _build_blocks(function, labels, reasons)
    edges, connected = _connect(blocks, label_to_block)
    connected, rpo = _mark_reachability(connected, edges)
    marked_edges, back_edges = _mark_back_edges(connected, edges)
    return CFG(
        function=function,
        blocks=connected,
        edges=marked_edges,
        entry=connected[0].id if connected else "",
        exit=EXIT,
        rpo=rpo,
        back_edges=back_edges,
    )


def build_program_cfgs(program: IRProgram) -> dict[str, CFG]:
    """Build a CFG for every function in a lowered program, keyed by function name."""
    return {function.name: build_cfg(function) for function in program.functions}
