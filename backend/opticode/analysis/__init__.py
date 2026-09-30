"""Data-flow analysis over a compiled program.

Three analyses run over every function and are combined into one report each:

* :mod:`.liveness` — backward, may-liveness, so dead stores become visible.
* :mod:`.reaching` — forward, reaching definitions, which also yields def-use chains.
* :mod:`.constprop` — forward, the ⊤ / CONST / NAC lattice.

All three are the same worklist algorithm from :mod:`.solver` with different problem
definitions, so they share one convergence guarantee, one trace format, and one rule about
unreachable code: only reachable blocks are solved, and unreachable blocks are absent from
every result rather than present-with-a-default.

:func:`analyze` is the single entry point. It runs the three analyses, derives the
per-instruction facts and def-use chains, and returns a plain dict keyed by function name —
the exact shape the API serializes, so the wire format and the Python result are the same
object rather than two things kept in sync by hand.

:mod:`.findings` sits on top and answers the question a user actually asks: which
statements never run, and which assignments write a value nothing reads. It adds no solving,
only interpretation of the results above.
"""

from __future__ import annotations

from typing import Any

from ..cfg import CFG, build_program_cfgs
from ..ir.tac import IRProgram
from .constprop import knowns, propagate
from .findings import (
    AnalysisReport,
    Evidence,
    Finding,
    FindingAction,
    FindingKind,
    analyze_findings,
    diagnostics_for,
)
from .liveness import liveness
from .reaching import reaching
from .results import FunctionAnalysis, build_function_analysis

__all__ = [
    "AnalysisReport",
    "Evidence",
    "Finding",
    "FindingAction",
    "FindingKind",
    "analyze",
    "analyze_cfg",
    "analyze_findings",
    "analyze_functions",
    "diagnostics_for",
]


def analyze_cfg(cfg: CFG) -> FunctionAnalysis:
    """Run all three analyses over one function and combine them."""
    live = liveness(cfg)
    reaching_solution = reaching(cfg)
    constants = propagate(cfg)

    return build_function_analysis(
        cfg,
        live=dict(live.block_in),
        live_out=dict(live.block_out),
        reaching_in=dict(reaching_solution.block_in),
        reaching_out=dict(reaching_solution.block_out),
        states_in=dict(constants.block_in),
        states_out=dict(constants.block_out),
        exit_values=knowns(cfg, constants),
        iterations={
            "liveness": live.iterations,
            "reaching": reaching_solution.iterations,
            "constant_propagation": constants.iterations,
        },
        # The solutions carry the recorded convergence, which the UI's stepper replays. They
        # are handed back here rather than re-solved, so a request pays for three solver
        # runs per function and not six.
        solutions={
            "liveness": live,
            "reaching": reaching_solution,
            "constant_propagation": constants,
        },
    )


def analyze_functions(program: IRProgram, *, cfgs: dict[str, CFG] | None = None) -> dict[str, FunctionAnalysis]:
    """Analyze every function in ``program``, keyed by function name.

    This is :func:`analyze` before serialization, and the pipeline uses it so the findings
    stage reads the solved results instead of paying for three more solver runs per function.
    """
    graphs = cfgs if cfgs is not None else build_program_cfgs(program)
    return {name: analyze_cfg(graph) for name, graph in sorted(graphs.items())}


def analyze(program: IRProgram, *, cfgs: dict[str, CFG] | None = None) -> dict[str, Any]:
    """Analyze every function in ``program``, keyed by function name.

    ``cfgs`` lets a caller pass graphs it already built — the pipeline does exactly this, so
    it does not rebuild one graph per analysis. When omitted, they are built here.
    """
    return {
        name: result.to_dict() for name, result in analyze_functions(program, cfgs=cfgs).items()
    }
