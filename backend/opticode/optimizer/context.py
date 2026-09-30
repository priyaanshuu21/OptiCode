"""The state one pass sees: the program, its graphs, and its solved data-flow facts.

Passes do not solve anything themselves. Constant propagation, branch folding, and dead
store elimination all read facts that :mod:`opticode.analysis` already computed, and a pass
that re-derived them would be free to disagree with the analyses the frontend displays. So
the context builds the CFGs and the three solved analyses once and hands them to every pass
that asks.

The context is also where a pass's mutation lands. A pass returns a new
:class:`PassContext`'s program rather than mutating instructions in place — TAC objects are
frozen dataclasses, and building a new list is what makes "what the pass did" a value that
can be compared against what it did before.

:meth:`PassContext.refresh` must be called after any pass that reports a change, because the
next pass needs facts about the *rewritten* program, not the one it was handed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..analysis import analyze_functions
from ..analysis.results import FunctionAnalysis
from ..cfg import CFG, build_program_cfgs
from ..ir.tac import IRFunction, IRProgram

__all__ = ["PassContext"]


@dataclass(slots=True)
class PassContext:
    """The program under optimization plus the facts computed about it."""

    program: IRProgram
    cfgs: dict[str, CFG] = field(default_factory=dict)
    analyses: dict[str, FunctionAnalysis] = field(default_factory=dict)

    def refresh(self) -> None:
        """Rebuild the graphs and re-solve the analyses for the current program.

        Raises :class:`~opticode.cfg.CFGError` if the program is not well-formed TAC. That
        would be a bug in the pass that produced it rather than anything the user did, so
        the pipeline reports it as an internal error instead of shipping broken code.
        """
        self.cfgs = build_program_cfgs(self.program)
        self.analyses = analyze_functions(self.program, cfgs=self.cfgs)

    def replace_function(self, function: IRFunction) -> None:
        """Swap in a rewritten function, leaving the rest of the program alone."""
        self.program = IRProgram(
            functions=tuple(
                function if item.name == function.name else item
                for item in self.program.functions
            ),
            symbols=self.program.symbols,
        )

    def cfg_for(self, function: IRFunction) -> CFG:
        cfg = self.cfgs.get(function.name)
        if cfg is None:  # pragma: no cover - refresh always covers every function
            raise KeyError(f"no CFG for {function.name}")
        return cfg

    def analysis_for(self, function: IRFunction) -> FunctionAnalysis:
        return self.analyses[function.name]
