"""Control-flow analysis: basic blocks, the CFG, and linearization.

:mod:`opticode.cfg.graph` holds the graph model and the builder. Everything a later pass
needs to *ask* about a graph — loops, dominance-free properties, dataflow ordering — is
derived there, so this package stays one concern: turning a flat instruction list into
blocks it can reason about.
"""

from __future__ import annotations

from .graph import (
    CFG,
    ENTRY,
    EXIT,
    BasicBlock,
    CFGError,
    Edge,
    EdgeKind,
    build_cfg,
    build_program_cfgs,
)

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
