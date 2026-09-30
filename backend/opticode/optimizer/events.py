"""What an optimization run did, recorded for the frontend.

An optimizer that reports only "the program got smaller" cannot be reviewed. Every change
records *where* it happened, *what* it replaced, and *why* it was allowed, so the optimized
output is a review artifact rather than a black box. That matters here more than usual:
these passes delete user-visible code, and "which line did it delete and what proved that
line was dead" is the question a user will ask.

Three levels are recorded, and each answers a different question:

* :class:`OptimizationEvent` — one change, at one place, with before and after text.
* :class:`PassResult` — how often one pass ran and how much it removed.
* :class:`OptimizationResult` — the whole run, with both programs and the verdict of the
  interpreter oracle that checked it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..core.spans import SourceSpan
from ..ir.invariants import LoweringStats
from ..ir.serialize import serialize_ir
from ..ir.tac import IRProgram

__all__ = [
    "MAX_EVENTS",
    "EventKind",
    "OptimizationEvent",
    "OptimizationResult",
    "PassDelta",
    "PassResult",
]

#: An upper bound on recorded events. A large program can produce tens of thousands of
#: constant substitutions, and a diagnostic payload that has to be parsed by a browser is
#: not a useful artifact. Truncation is reported rather than silent, because a truncated
#: event list must never be mistaken for a complete one.
MAX_EVENTS = 2000


class EventKind(StrEnum):
    """What kind of rewrite produced an event."""

    #: A read of a provably constant value became a literal.
    CONSTANT_PROPAGATED = "constant_propagated"
    #: An arithmetic instruction whose operands were literal became a literal.
    CONSTANT_FOLDED = "constant_folded"
    #: A conditional branch with a constant condition became unconditional, or vanished.
    BRANCH_FOLDED = "branch_folded"
    #: A basic block with no path from the function entry was deleted.
    BLOCK_REMOVED = "block_removed"
    #: An assignment whose destination was never read again was deleted.
    STORE_REMOVED = "store_removed"


@dataclass(frozen=True, slots=True)
class OptimizationEvent:
    """One rewrite, with enough context to review or undo it."""

    #: The pass that made the change.
    pass_name: str
    #: Which function it happened in.
    function: str
    kind: EventKind
    #: A one-line explanation of why the rewrite was allowed.
    detail: str
    #: The printed form before the change, as the TAC printer would show it.
    before: str
    #: The printed form after. Empty when the instruction was deleted.
    after: str = ""
    #: The instruction ids involved, sorted.
    instruction_ids: tuple[str, ...] = ()
    span: SourceSpan | None = None
    #: Block id, when the change was block-level.
    block: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "pass": self.pass_name,
            "function": self.function,
            "kind": self.kind.value,
            "detail": self.detail,
            "before": self.before,
            "instruction_ids": list(self.instruction_ids),
        }
        if self.after:
            payload["after"] = self.after
        if self.span is not None:
            payload["span"] = self.span.to_dict()
        if self.block is not None:
            payload["block"] = self.block
        return payload


@dataclass(slots=True)
class PassDelta:
    """What one application of one pass did.

    ``changed`` is what drives the manager's fixpoint loop, so it must mean "the program is
    different", not "this pass looked at something". A pass that rewrites an instruction
    into an identical one has not changed anything and must not report that it has,
    otherwise the loop spins until it hits the round cap.
    """

    changed: bool = False
    #: Instructions deleted by this application.
    removed: int = 0
    events: list[OptimizationEvent] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PassResult:
    """How one pass behaved across the whole run."""

    name: str
    description: str
    #: How many times it was applied.
    iterations: int = 0
    #: How many times it actually changed the program.
    applications: int = 0
    #: Instructions it deleted in total.
    removed: int = 0

    @property
    def changed(self) -> bool:
        return self.applications > 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "iterations": self.iterations,
            "applications": self.applications,
            "removed": self.removed,
            "changed": self.changed,
        }


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """One optimization run: what it produced, what it changed, and whether it was safe."""

    original: IRProgram
    optimized: IRProgram
    passes: tuple[PassResult, ...] = ()
    events: tuple[OptimizationEvent, ...] = ()
    #: The interpreter oracle's verdict and per-case detail.
    verification: Any = None
    #: Fixpoint rounds executed.
    rounds: int = 0
    #: True when the recorded events hit :data:`MAX_EVENTS`.
    events_truncated: bool = False

    # -------------------------------------------------------------- derived

    @property
    def changed(self) -> bool:
        return not same_program(self.original, self.optimized)

    @property
    def verified(self) -> bool:
        return bool(getattr(self.verification, "verified", False))

    @property
    def removed_instructions(self) -> int:
        return self.stats_before.instructions - self.stats_after.instructions

    #: :class:`~opticode.ir.invariants.LoweringStats` is reused rather than copied: it
    #: already counts exactly what the frontend shows next to an optimized program.
    @property
    def stats_before(self) -> LoweringStats:
        return LoweringStats.of(self.original)

    @property
    def stats_after(self) -> LoweringStats:
        return LoweringStats.of(self.optimized)

    # ---------------------------------------------------------------- output

    def to_dict(self) -> dict[str, Any]:
        verification = self.verification
        return {
            "changed": self.changed,
            "verified": self.verified,
            "rounds": self.rounds,
            "removed_instructions": self.removed_instructions,
            "passes": [item.to_dict() for item in self.passes],
            "events": [item.to_dict() for item in self.events],
            "events_truncated": self.events_truncated,
            "stats": {
                "before": _stats_dict(self.stats_before),
                "after": _stats_dict(self.stats_after),
            },
            "verification": verification.to_dict() if verification is not None else None,
            # The optimized TAC travels with the report: the frontend's job is to show the
            # difference, and a separate top-level field would be one more thing to keep in
            # sync with `ir`, which is always the *unoptimized* program.
            "ir": serialize_ir(self.optimized),
        }

    def summary(self) -> str:
        """One line for a diagnostic message."""
        if not self.changed:
            return "the optimizer found nothing to change"
        applied = [item.name for item in self.passes if item.changed]
        return (
            f"removed {self.removed_instructions} of "
            f"{self.stats_before.instructions} instructions with "
            f"{len(applied)} pass{'' if len(applied) == 1 else 'es'} "
            f"in {self.rounds} round{'' if self.rounds == 1 else 's'}"
        )


def _stats_dict(stats: LoweringStats) -> dict[str, int]:
    """The four counts, as JSON."""
    return {
        "functions": stats.functions,
        "instructions": stats.instructions,
        "temps": stats.temps,
        "labels": stats.labels,
    }


def same_program(left: IRProgram, right: IRProgram) -> bool:
    """Whether two programs hold the same functions and the same instructions.

    Compared by the parts a pass may change rather than by whole-program equality, because
    the symbol table is shared by reference between an input program and its optimized form
    and comparing it would say nothing about the instructions.
    """
    if len(left.functions) != len(right.functions):
        return False
    return all(
        before.name == after.name
        and before.declared_locals == after.declared_locals
        and before.instructions == after.instructions
        for before, after in zip(left.functions, right.functions, strict=True)
    )
