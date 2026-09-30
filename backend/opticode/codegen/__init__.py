"""Source generation: turn optimized TAC back into source the user can read.

The invariant this package exists to uphold is that optimized source is produced by *editing
the AST*, never by decompiling TAC. Printing three-address code would be easy and wrong: it
would throw away the user's names, their formatting, and their control flow. Editing the tree
keeps every part of the program the optimizer had no opinion about exactly as they wrote it.

The flow is four steps, and the last one is the important one:

1. :func:`~opticode.codegen.edits.plan_edits` works out which nodes the optimization allows us
   to touch. Pure; no rewriting yet, though it does rewrite speculatively to check its own
   decisions against each other.
2. :func:`apply_plan` rebuilds a **copy** of the tree with those edits. The original tree is
   never mutated, because the API still returns the user's AST and the analysis findings
   still point into it.
3. The tree is unparsed twice: once clean, and once with a comment standing in for every
   removed line, so a reviewer can see what disappeared instead of wondering.
4. :func:`~opticode.codegen.selfcheck.self_check` re-compiles the generated text and proves it
   behaves identically to the original. If it does not, the generated text is discarded and
   the user's own source is returned unchanged.

That last step is what makes this stage safe rather than merely plausible. Every rule in
:mod:`~opticode.codegen.edits` is a guess about what a rewrite means for the source, and
guesses about control flow are exactly where optimizers are wrong. Because the guess is checked
before it reaches anyone, a wrong guess costs a missed optimization rather than a broken
program.

The change map is the other half of the value. Deleting a line of someone's code is not
something a tool should do quietly, so every edit carries the span it touched, the text before
and after, and the pass that justified it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ..optimizer.events import OptimizationResult
from ..parser.ast import Program
from ..parser.unparser import UnparseOptions, unparse
from .changes import ChangeKind, ChangeMap
from .edits import EditPlan, plan_edits
from .rewrite import rewrite_program
from .selfcheck import SelfCheck, self_check

__all__ = [
    "CodegenResult",
    "apply_plan",
    "generate_optimized_source",
]


@dataclass(frozen=True, slots=True)
class CodegenResult:
    """What the codegen stage produced.

    ``source`` is the deliverable: either the regenerated program or, when nothing was
    applied, the user's own text byte for byte. ``applied`` distinguishes the two, and
    ``reason`` says why in words the frontend can show without inventing an explanation.
    """

    source: str = ""
    annotated_source: str = ""
    changes: ChangeMap = field(default_factory=ChangeMap)
    applied: bool = False
    verified: bool = False
    reason: str = ""
    check: SelfCheck | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "annotated_source": self.annotated_source,
            "changes": self.changes.to_dict(),
            "applied": self.applied,
            "verified": self.verified,
            "reason": self.reason,
            "self_check": self.check.to_dict() if self.check is not None else None,
        }


def generate_optimized_source(
    ast: Program,
    result: OptimizationResult,
    *,
    original_source: str = "",
    verify: bool = True,
) -> CodegenResult:
    """Regenerate ``ast`` from what the optimizer learned.

    ``original_source`` is the text the user sent. It is what gets returned whenever the
    projection is refused, so a user never gets back a reformatting of their own program when
    the tool decided to change nothing.
    """
    baseline = original_source or unparse(ast)
    plan = plan_edits(ast, result)
    if plan.empty:
        return CodegenResult(
            source=baseline,
            annotated_source=baseline,
            reason="the optimizer made no change the source could show",
        )

    rewritten = rewrite_program(ast, plan)
    generated = unparse(rewritten.program)
    if generated == unparse(ast):
        # The edits cancelled out. Comparing against the user's own bytes instead would
        # report a change for every reformatting, which is how a "nothing to do" result turns
        # into a spurious diff.
        return CodegenResult(
            source=baseline,
            annotated_source=baseline,
            changes=plan.changes,
            reason="the edits left the generated source identical to the original",
        )

    check = self_check(generated, result.original) if verify else None
    if check is not None and not check.ok:
        return CodegenResult(
            source=baseline,
            annotated_source=baseline,
            changes=plan.changes,
            reason=check.describe(),
            check=check,
        )

    return CodegenResult(
        source=generated,
        annotated_source=_annotated_source(ast, plan),
        changes=plan.changes,
        applied=True,
        verified=check is not None and check.ok,
        reason=check.describe() if check is not None else "the generated source was not verified",
        check=check,
    )


def _annotated_source(ast: Program, plan: EditPlan) -> str:
    """The generated source with a comment standing in for every removed line.

    Removed statements are deliberately left in the tree and handed to the unparser as
    annotations instead of being deleted, so the reader sees ``/* removed: x = 1; */`` where
    the line used to be. That answers "what did you take out?" without a side-by-side diff,
    which is the question a user actually has when a tool edits their code.
    """
    rewritten = rewrite_program(ast, plan, keep_removed=True)
    return unparse(
        rewritten.program,
        UnparseOptions(
            annotate=True,
            removed_annotations=rewritten.removed,
            change_notes=_notes(plan.changes),
        ),
    )


def _notes(changes: ChangeMap) -> dict[str, str]:
    """Inline notes for statements that survived but were simplified."""
    return {
        change.node_id: f"was: {change.before}"
        for change in changes.sorted_by_position()
        if change.kind is ChangeKind.BRANCH_RESOLVED
    }


def apply_plan(ast: Program, plan: EditPlan, *, keep_removed: bool = False) -> Program:
    """Rebuild a copy of ``ast`` with ``plan`` applied.

    The original tree is left alone on purpose. It is still in the response as ``ast``, and the
    analysis findings point into it, so mutating it would change the answers to questions that
    were already answered.
    """
    rewritten = rewrite_program(ast, plan, keep_removed=keep_removed)
    assert isinstance(rewritten.program, Program), "rewriting a program yields a program"
    return rewritten.program
