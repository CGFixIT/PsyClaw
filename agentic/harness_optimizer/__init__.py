"""Governed harness optimizer scaffold for the out-of-band agentic layer.

Phase 2 only: local data models and proposer workspace creation. This package
does not call models, GitHub, MCP, shell commands, or the CyClaw request path.
"""

from __future__ import annotations

from agentic.harness_optimizer.core import (
    CandidateDecision,
    Experiment,
    RunReport,
    Surface,
    SurfaceType,
    Variant,
    decide_candidate,
)
from agentic.harness_optimizer.proposer import ProposerWorkspace, build_proposer_workspace

__all__ = [
    "CandidateDecision",
    "Experiment",
    "ProposerWorkspace",
    "RunReport",
    "Surface",
    "SurfaceType",
    "Variant",
    "build_proposer_workspace",
    "decide_candidate",
]
