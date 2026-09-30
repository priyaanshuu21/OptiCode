"""Source locations shared by every compiler stage.

Every token, AST node, and instruction carries a :class:`SourceSpan` so that any
finding or optimization can be traced back to an exact line and column in the
original program.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

__all__ = ["SourceSpan", "Span", "span_between", "union"]


@dataclass(frozen=True, slots=True, order=True)
class SourceSpan:
    """A half-open region of source text, using 1-based line/column positions.

    ``start_line``/``start_col`` point at the first character, ``end_line``/``end_col``
    point one position past the last character. Columns are counted in characters,
    with a tab counting as a single column.
    """

    start_line: int = 1
    start_col: int = 1
    end_line: int = 1
    end_col: int = 1

    def to_dict(self) -> dict[str, int]:
        return {
            "start_line": self.start_line,
            "start_col": self.start_col,
            "end_line": self.end_line,
            "end_col": self.end_col,
        }

    @classmethod
    def at(cls, line: int, col: int) -> SourceSpan:
        """A zero-width span, used for "here" style diagnostics."""
        return cls(line, col, line, col)

    @property
    def is_empty(self) -> bool:
        return self.start_line == self.end_line and self.start_col == self.end_col

    def contains(self, other: SourceSpan) -> bool:
        return (
            self.start_line,
            self.start_col,
        ) <= (other.start_line, other.start_col) and (self.end_line, self.end_col) >= (
            other.end_line,
            other.end_col,
        )

    def overlaps(self, other: SourceSpan) -> bool:
        return self.contains(other) or other.contains(self)

    def extend(self, other: SourceSpan) -> SourceSpan:
        return union(self, other)


# Shorter alias used inside the compiler packages.
Span = SourceSpan


def union(a: SourceSpan, b: SourceSpan) -> SourceSpan:
    """The smallest span covering both inputs."""
    if (a.start_line, a.start_col) <= (b.start_line, b.start_col):
        start_line, start_col = a.start_line, a.start_col
    else:
        start_line, start_col = b.start_line, b.start_col
    if (a.end_line, a.end_col) >= (b.end_line, b.end_col):
        end_line, end_col = a.end_line, a.end_col
    else:
        end_line, end_col = b.end_line, b.end_col
    return SourceSpan(start_line, start_col, end_line, end_col)


def span_between(start: SourceSpan, end: SourceSpan) -> SourceSpan:
    """A span running from ``start``'s beginning to ``end``'s end."""
    return SourceSpan(start.start_line, start.start_col, end.end_line, end.end_col)


def shift(span: SourceSpan, *, line: int = 0, col: int = 0) -> SourceSpan:
    return replace(span, start_line=span.start_line + line, end_line=span.end_line + line)
