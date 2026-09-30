"""Rebuilding a copy of the AST with an edit plan applied.

The rewrite is bottom-up and generic: every node is rebuilt from its children unless the plan
says something about the node itself, in which case the plan wins and the children are never
visited. Ids and spans are carried over from the original nodes, which is what lets the change
map keep pointing at the place in the user's file that a change came from.

Two behaviours here are worth stating outright, because both would be silent corruption
without an explanation:

* **The original tree is never mutated.** It is still returned to the client as ``ast``, and
  the analysis findings point into it, so rewriting in place would retroactively change the
  answers to questions that were already answered.
* **A ``Block`` may be unwrapped when a branch resolves.** ``if (c) { a; b; }`` becoming just
  ``a; b;`` is what a person expects from a source-to-source optimizer; nesting a redundant
  pair of braces around the survivors would be correct and useless.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from dataclasses import fields as dataclass_fields
from typing import Final

from ..parser.ast import Block, Node, VarDecl
from ..parser.unparser import unparse
from .edits import EditPlan

__all__ = [
    "rewrite_program",
]


class _Drop:
    """The result of a rewrite that deletes a node."""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "<drop>"


_DROP: Final = _Drop()


@dataclass(frozen=True, slots=True)
class Rewritten:
    """A rewritten program plus the text of everything that was removed."""

    program: Node
    #: Removed statement id to the source text it used to be.
    removed: dict[str, str]


def rewrite_program(
    ast: Node,
    plan: EditPlan,
    *,
    keep_removed: bool = False,
) -> Rewritten:
    """Rebuild a copy of ``ast`` with ``plan`` applied.

    ``keep_removed`` produces the annotated variant: instead of deleting a statement the plan
    removes, it is left in the tree and its text is collected so the caller can print it as a
    comment. Every fold and branch resolution still applies in that mode, because a reviewer
    wants to see the surviving code optimized *and* the deletions marked.
    """
    annotations: dict[str, str] = {}
    rewritten = _rewrite(ast, plan, keep_removed, annotations)
    assert not isinstance(rewritten, _Drop), "the program root can never be dropped"
    return Rewritten(program=rewritten, removed=annotations)


def _rewrite(
    node: Node,
    plan: EditPlan,
    keep_removed: bool,
    annotations: dict[str, str],
) -> Node | list[Node] | _Drop:
    """Rebuild ``node`` bottom-up, applying ``plan``.

    A ``list`` return means "this node became several statements", which is how a resolved
    ``if`` splices the surviving block's statements into the parent's statement list.
    """
    if node.id in plan.replace:
        return plan.replace[node.id]

    if node.id in plan.resolve:
        kept = plan.resolve[node.id]
        if kept is None:
            if not keep_removed:
                return _DROP
            annotations.setdefault(node.id, _source_of(node))
            return node
        # Rewrite the surviving arm too: its own constants are still foldable, and its dead
        # statements still need annotating.
        if isinstance(kept, Block) and _unwrappable(kept):
            spliced: list[Node] = []
            for statement in kept.statements:
                rebuilt = _rewrite(statement, plan, keep_removed, annotations)
                if isinstance(rebuilt, _Drop):
                    continue
                spliced.extend(rebuilt if isinstance(rebuilt, list) else [rebuilt])
            return spliced
        return _rewrite(kept, plan, keep_removed, annotations)

    if node.id in plan.drop:
        annotations.setdefault(node.id, _source_of(node))
        if keep_removed:
            return node
        return _DROP

    updates: dict[str, object] = {}
    for spec in dataclass_fields(node):
        if not spec.init:
            continue
        value = getattr(node, spec.name)
        if isinstance(value, Node):
            rebuilt = _rewrite(value, plan, keep_removed, annotations)
            if isinstance(rebuilt, _Drop):
                return _DROP
            if isinstance(rebuilt, list):
                # A single-child field cannot become several statements. Keeping the child
                # unchanged costs at most an optimization; failing here would cost the whole
                # response.
                continue
            if rebuilt is not value:
                updates[spec.name] = rebuilt
        elif isinstance(value, tuple):
            items: list[object] = []
            for item in value:
                if isinstance(item, Node):
                    rebuilt = _rewrite(item, plan, keep_removed, annotations)
                    if isinstance(rebuilt, _Drop):
                        continue
                    if isinstance(rebuilt, list):
                        items.extend(rebuilt)
                        continue
                    items.append(rebuilt)
                else:
                    items.append(item)
            rebuilt_tuple = tuple(items)
            if rebuilt_tuple != value:
                updates[spec.name] = rebuilt_tuple
    return replace(node, **updates) if updates else node


def _unwrappable(block: Block) -> bool:
    """Whether a block's braces can be dropped when its statements move up a level.

    Not when it declares anything. A block is a scope, so ``if (c) { int t = 1; }`` only works
    because ``t`` is invisible afterwards; lifting that declaration into the enclosing function
    could collide with a ``t`` that is already there, and the self-check would then reject the
    whole file over a cosmetic brace.
    """
    return not any(isinstance(statement, VarDecl) for statement in block.statements)


def _source_of(node: Node) -> str:
    """One node's canonical source text, used as the text of a removal comment."""
    return unparse(node).strip()
