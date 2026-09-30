"""The change map: what codegen did to the user's source, and why.

The optimizer records rewrites at the TAC level because that is where it reasons about
them. That is the wrong granularity to show a person. Nobody writes three-address code by
hand, so an event like ``store_removed`` on ``t3 = i`` reads as noise when what actually
happened is that a whole line of their program was deleted.

This module is the translation layer. One :class:`SourceChange` is one edit a person can
review: it names the AST node that was touched, the span in *their* file, the source text
before and after, and the pass that justified it. The frontend needs exactly these to render
a diff, an inline explanation, or an undo affordance.

Three kinds, because the three are genuinely different to undo:

* :attr:`ChangeKind.STATEMENT_REMOVED` — a line is gone. Undo means putting a line back.
* :attr:`ChangeKind.EXPRESSION_FOLDED` — an expression became a literal. Undo means
  restoring the expression, and it is the only kind that can make the source *shorter
  without losing a statement*.
* :attr:`ChangeKind.BRANCH_RESOLVED` — one arm of an ``if`` was kept because the condition
  was a constant. Undo means restoring the whole ``if``, so it records the original
  condition text explicitly rather than relying on the folded literal.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..core.spans import SourceSpan

__all__ = [
    "ChangeKind",
    "ChangeMap",
    "SourceChange",
]


class ChangeKind(StrEnum):
    """What codegen did to one AST node."""

    #: A whole statement was deleted from the tree.
    STATEMENT_REMOVED = "statement_removed"
    #: An expression was replaced by a literal, or an identifier by its known value.
    EXPRESSION_FOLDED = "expression_folded"
    #: An ``if`` with a constant condition was replaced by the arm that survives.
    BRANCH_RESOLVED = "branch_resolved"


@dataclass(frozen=True, slots=True)
class SourceChange:
    """One reviewable edit to the source tree.

    ``node_id`` is the id of the node that was edited. For a removal that is the statement
    that disappeared; for a fold it is the expression that was replaced. Both keep pointing
    at a node that still exists in the edited tree — removals point at the node that *was*
    there, which is what lets the frontend line the change up with the original file even
    though the text is gone from the output.
    """

    kind: ChangeKind
    node_id: str
    span: SourceSpan = field(default_factory=SourceSpan)
    #: The enclosing function, or ``""`` for top-level statements.
    function: str = ""
    #: Why the pass was allowed to do this, in one sentence.
    reason: str = ""
    #: The source text of the affected node before the edit.
    before: str = ""
    #: The replacement text. Empty for a removal.
    after: str = ""
    #: The passes that contributed, in application order.
    passes: tuple[str, ...] = ()
    #: The TAC instructions behind this edit, for cross-referencing the optimizer log.
    instruction_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind.value,
            "node_id": self.node_id,
            "span": self.span.to_dict(),
            "function": self.function,
            "reason": self.reason,
            "before": self.before,
            "passes": list(self.passes),
            "instruction_ids": list(self.instruction_ids),
        }
        if self.after:
            payload["after"] = self.after
        return payload

    def annotation(self) -> str:
        """The comment that stands in for a removed statement in the annotated output."""
        return f"removed: {self.before}"


@dataclass(slots=True)
class ChangeMap:
    """The changes from one codegen run, in source order.

    Ordering is by span rather than by discovery, because the frontend renders these
    top-down and a change list that jumps around the file reads as noise. Two changes to the
    same node are possible in principle (a statement both deleted and folded) but do not
    occur in practice, because a node is either removed or rewritten, never both; the map
    keys on node id anyway so a bug upstream cannot silently produce duplicates.
    """

    changes: list[SourceChange] = field(default_factory=list)

    def __iter__(self) -> Iterator[SourceChange]:
        return iter(self.changes)

    def __len__(self) -> int:
        return len(self.changes)

    def __bool__(self) -> bool:
        return bool(self.changes)

    def add(self, change: SourceChange) -> None:
        self.changes.append(change)

    def extend(self, changes: Iterable[SourceChange]) -> None:
        self.changes.extend(changes)

    def by_kind(self, kind: ChangeKind) -> tuple[SourceChange, ...]:
        return tuple(change for change in self.changes if change.kind is kind)

    @property
    def removed_statements(self) -> int:
        return len(self.by_kind(ChangeKind.STATEMENT_REMOVED))

    @property
    def folded_expressions(self) -> int:
        return len(self.by_kind(ChangeKind.EXPRESSION_FOLDED))

    @property
    def resolved_branches(self) -> int:
        return len(self.by_kind(ChangeKind.BRANCH_RESOLVED))

    def sorted_by_position(self) -> tuple[SourceChange, ...]:
        return tuple(sorted(self.changes, key=lambda change: (change.span.start_line,
                                                              change.span.start_col)))

    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.changes),
            "statements_removed": self.removed_statements,
            "expressions_folded": self.folded_expressions,
            "branches_resolved": self.resolved_branches,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "counts": self.counts(),
            "changes": [change.to_dict() for change in self.sorted_by_position()],
        }
