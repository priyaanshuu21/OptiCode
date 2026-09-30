"""A generic iterative data-flow solver.

A data-flow analysis computes, for every program point, some facts drawn from a lattice.
Nothing about liveness or constant propagation actually lives here — this module only
provides the *engine*, and each analysis supplies the five things that define its problem:

============================  ===========================================================
ingredient                    what it means
============================  ===========================================================
``direction``                 forward (facts flow with control) or backward (against it)
``boundary``                  the value at ENTRY for forward, or at EXIT for backward
``initial``                   the value everywhere else, and for unreachable blocks
``meet``                      how to combine several incoming values
``transfer``                  how a block's own instructions move the value
============================  ===========================================================

The engine is a **worklist algorithm**. It seeds the list with the blocks in a
fixed order — reverse postorder going forward, postorder going backward, because
approaching a definition from the front converges in far fewer passes — then re-adds a
block whenever one of its neighbours changes, until nothing moves.

Two properties make this safe rather than merely fast:

* **Only reachable blocks are solved.** An unreachable block keeps the initial value and
  is never a neighbour of a reachable one, so dead code can never invent a fact.
* **Termination is asserted, not assumed.** The engine stops at
  :data:`MAX_ITERATIONS` and raises rather than spinning forever. Reaching that bound
  means a transfer function is not monotone, which is a compiler bug worth surfacing
  instead of hanging on.

Every collection that leaves this module is sorted, so two runs over the same program
produce byte-identical traces.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Generic, TypeVar

from ..cfg import CFG, BasicBlock

__all__ = [
    "MAX_ITERATIONS",
    "MAX_TRACE_STEPS",
    "DataFlowProblem",
    "Direction",
    "IterationStep",
    "SolverError",
    "Solution",
    "solve",
]

#: A hard ceiling on block updates. Monotone transfer functions converge long before this;
#: hitting it means the transfer function is not monotone and the fixpoint does not exist.
MAX_ITERATIONS = 100_000

#: How many steps the iteration trace keeps. The trace exists to drive a future stepper
#: UI, which only ever shows the first few rounds; keeping every update of a large function
#: would bloat the API response for no benefit.
MAX_TRACE_STEPS = 200

T = TypeVar("T")


class Direction(StrEnum):
    """Which way information flows relative to control."""

    FORWARD = "forward"
    BACKWARD = "backward"


class SolverError(Exception):
    """The fixpoint did not exist, or the problem was set up inconsistently."""


@dataclass(frozen=True, slots=True)
class IterationStep:
    """One block update, recorded for the stepper UI."""

    #: Monotonic count of block updates so far, 1-based.
    iteration: int
    block: str
    #: Rendered domain value at the block's entry, *after* this update.
    block_in: Any
    #: Rendered domain value at the block's exit, *after* this update.
    block_out: Any
    #: False when the update produced exactly the previous value.
    changed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "iteration": self.iteration,
            "block": self.block,
            "in": self.block_in,
            "out": self.block_out,
            "changed": self.changed,
        }


@dataclass(frozen=True, slots=True)
class DataFlowProblem(Generic[T]):
    """The five ingredients that fully specify one analysis."""

    name: str
    direction: Direction
    cfg: CFG
    #: Value at ENTRY (forward) or at EXIT (backward).
    boundary: T
    #: Value for every other block, and for unreachable ones.
    initial: T
    #: Combine the values flowing in along several edges. An empty iterable means the block
    #: has no such neighbours, and the result is ``initial``.
    meet: Callable[[Iterable[T]], T]
    #: Apply a block's own instructions to its incoming value.
    transfer: Callable[[BasicBlock, T], T]
    #: Turn a domain value into JSON for the trace.
    render: Callable[[T], Any]


@dataclass(frozen=True, slots=True)
class Solution(Generic[T]):
    """The fixed point, plus what it took to get there."""

    name: str
    direction: Direction
    #: Reachable block id → value before the block's first instruction.
    block_in: dict[str, T]
    #: Reachable block id → value after the block's last instruction.
    block_out: dict[str, T]
    #: Total block updates performed.
    iterations: int
    #: Up to :data:`MAX_TRACE_STEPS` updates, in the order they happened.
    trace: tuple[IterationStep, ...]
    #: True when the trace was cut short. ``iterations`` is still the true count.
    trace_truncated: bool
    #: Render a domain value as JSON, carried from the problem so this stays serializable.
    render: Callable[[T], Any] = staticmethod(lambda value: value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "direction": self.direction.value,
            "block_in": {key: self.render(value) for key, value in self.block_in.items()},
            "block_out": {key: self.render(value) for key, value in self.block_out.items()},
            "iterations": self.iterations,
            "trace": [step.to_dict() for step in self.trace],
            "trace_truncated": self.trace_truncated,
        }


def solve(problem: DataFlowProblem[T]) -> Solution[T]:
    """Run the worklist algorithm to a fixed point.

    Returns the IN/OUT value of every **reachable** block. Unreachable blocks are absent
    from the result rather than present-with-initial-value, so a caller cannot mistake dead
    code for a real answer.
    """
    cfg = problem.cfg
    blocks = {block.id: block for block in cfg.blocks if block.reachable}
    order = _seed_order(problem, blocks)
    if not order:
        return Solution(
            name=problem.name,
            direction=problem.direction,
            block_in={},
            block_out={},
            iterations=0,
            trace=(),
            trace_truncated=False,
            render=problem.render,
        )

    block_in: dict[str, T] = {block_id: problem.initial for block_id in order}
    block_out: dict[str, T] = {block_id: problem.initial for block_id in order}

    # Position in the seed order, used to re-queue neighbours in a stable sequence.
    rank = {block_id: index for index, block_id in enumerate(order)}
    worklist: deque[str] = deque(order)
    queued: set[str] = set(order)
    trace: list[IterationStep] = []
    trace_truncated = False
    iterations = 0

    while worklist:
        block_id = worklist.popleft()
        queued.discard(block_id)
        block = blocks[block_id]
        iterations += 1
        if iterations > MAX_ITERATIONS:
            raise SolverError(
                f"{problem.name} on {cfg.function.name} did not converge within "
                f"{MAX_ITERATIONS} block updates; the transfer function is not monotone"
            )

        new_in, new_out = _recompute(problem, block, block_in, block_out, blocks)
        changed = new_in != block_in[block_id] or new_out != block_out[block_id]
        block_in[block_id] = new_in
        block_out[block_id] = new_out

        if len(trace) < MAX_TRACE_STEPS:
            trace.append(
                IterationStep(
                    iteration=iterations,
                    block=block_id,
                    block_in=problem.render(new_in),
                    block_out=problem.render(new_out),
                    changed=changed,
                )
            )
        elif not trace_truncated:
            trace_truncated = True

        if changed:
            # Re-queued neighbours go to the back of the queue in seed order, so the
            # traversal order stays a function of the CFG alone and the trace is
            # reproducible run to run.
            fresh = [
                neighbour
                for neighbour in _neighbours(problem, block, blocks)
                if neighbour not in queued
            ]
            for neighbour in sorted(set(fresh), key=lambda item: rank.get(item, len(rank))):
                queued.add(neighbour)
                worklist.append(neighbour)

    return Solution(
        name=problem.name,
        direction=problem.direction,
        block_in=block_in,
        block_out=block_out,
        iterations=iterations,
        trace=tuple(trace),
        trace_truncated=trace_truncated,
        render=problem.render,
    )


def _seed_order(
    problem: DataFlowProblem[T], blocks: dict[str, BasicBlock]
) -> list[str]:
    """The blocks to solve, in the order that converges fastest."""
    if problem.direction is Direction.FORWARD:
        # Reverse postorder: successors tend to be solved before their predecessors.
        seed = list(problem.cfg.rpo)
    else:
        # Postorder: predecessors solved first, since liveness flows against control.
        seed = list(reversed(problem.cfg.rpo))
    reachable = [block_id for block_id in seed if block_id in blocks]
    if reachable:
        return reachable
    # A function with no reachable block at all still has an entry block; an empty
    # instruction list produces one, and it is reachable.
    return [block.id for block in blocks][:1]


def _recompute(
    problem: DataFlowProblem[T],
    block: BasicBlock,
    block_in: dict[str, T],
    block_out: dict[str, T],
    blocks: dict[str, BasicBlock],
) -> tuple[T, T]:
    """One block's new IN and OUT, in the direction the analysis runs.

    A forward analysis reads its predecessors' OUT and seeds its entry block with the
    boundary. A backward analysis reads its successors' IN; the blocks that fall off the
    end have no reachable successor (a ``return`` block's only successor is the implicit
    EXIT), so their OUT is the boundary — which is how ``OUT[EXIT]`` reaches the fixpoint.
    """
    forward = problem.direction is Direction.FORWARD
    neighbours = _neighbours(problem, block, blocks)

    if forward:
        if block.id == problem.cfg.entry or not neighbours:
            incoming = problem.boundary
        else:
            incoming = problem.meet(block_out[other] for other in neighbours)
        return incoming, problem.transfer(block, incoming)

    if not neighbours:
        outgoing = problem.boundary
    else:
        outgoing = problem.meet(block_in[other] for other in neighbours)
    return problem.transfer(block, outgoing), outgoing


def _neighbours(
    problem: DataFlowProblem[T], block: BasicBlock, blocks: dict[str, BasicBlock]
) -> Iterator[str]:
    """The blocks whose values this one depends on, restricted to reachable blocks.

    A forward analysis reads its predecessors' OUT; a backward analysis reads its
    successors' IN. Either way an unreachable neighbour is skipped, which is what keeps
    dead code out of reachable results.
    """
    if problem.direction is Direction.FORWARD:
        source = block.predecessors
    else:
        source = block.successors
    for other in source:
        if other in blocks:
            yield other
