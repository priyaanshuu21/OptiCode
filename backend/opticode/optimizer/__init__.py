"""Optimization over TAC, with every change verified by execution.

The optimizer runs five passes — constant propagation, constant folding, branch folding,
unreachable block elimination, and dead assignment elimination — until the program stops
changing, then runs both the original and the optimized IR through the interpreter and
compares the results. A difference discards the optimized program.

The entry point is :func:`optimize`. Everything else is here because the frontend and the
tests need to look at the parts:

.. code-block:: python

    from opticode.optimizer import optimize

    result = optimize(program)
    if result.changed:
        print(result.summary())
        for event in result.events:
            print(event.pass_name, event.before, "->", event.after or "<removed>")

The three modules divide along the lines of *what*, *how*, and *whether*:

* :mod:`.events` — the record: what changed, why, and the verdict.
* :mod:`.passes` — the transformations, each one refusing to break behaviour.
* :mod:`.verify` — the check, by running both programs.
"""

from __future__ import annotations

from .context import PassContext
from .events import (
    MAX_EVENTS,
    EventKind,
    OptimizationEvent,
    OptimizationResult,
    PassDelta,
    PassResult,
    same_program,
)
from .manager import (
    DEFAULT_MAX_ITERATIONS,
    OptimizationError,
    PassRun,
    optimize,
    run_passes,
)
from .passes import (
    DEFAULT_PASSES,
    BranchFolding,
    ConstantFolding,
    ConstantPropagation,
    DeadAssignmentElimination,
    Pass,
    PassError,
    UnreachableBlockElimination,
    select_passes,
)
from .verify import (
    VERIFICATION_INPUTS,
    VERIFICATION_LIMITS,
    VerificationCase,
    VerificationReport,
    verifiable_entries,
    verify_equivalence,
)

__all__ = [
    "DEFAULT_MAX_ITERATIONS",
    "DEFAULT_PASSES",
    "MAX_EVENTS",
    "VERIFICATION_INPUTS",
    "VERIFICATION_LIMITS",
    "BranchFolding",
    "ConstantFolding",
    "ConstantPropagation",
    "DeadAssignmentElimination",
    "EventKind",
    "OptimizationError",
    "OptimizationEvent",
    "OptimizationResult",
    "Pass",
    "PassContext",
    "PassDelta",
    "PassError",
    "PassResult",
    "PassRun",
    "UnreachableBlockElimination",
    "VerificationCase",
    "VerificationReport",
    "optimize",
    "run_passes",
    "same_program",
    "select_passes",
    "verifiable_entries",
    "verify_equivalence",
]
