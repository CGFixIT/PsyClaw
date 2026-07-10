"""Governed harness optimizer scaffold for the out-of-band agentic layer.

Phases 2-4: local data models, deterministic runner/scoring helpers, proposer
workspace creation, and dependency-free proposer workspace tools. This package
does not call GitHub, shell commands, or the CyClaw request path; local model
invocation is available only through an explicitly constructed adapter.
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
from agentic.harness_optimizer.governance import (
    GovernanceFinding,
    detect_visible_case_hardcoding,
    governance_gate_strings,
)
from agentic.harness_optimizer.mcp.tools import ProposerWorkspaceTools
from agentic.harness_optimizer.model_adapter import (
    LocalProposerClient,
    LocalProposerResponse,
    invoke_workspace_proposer,
)
from agentic.harness_optimizer.proposer import ProposerWorkspace, build_proposer_workspace
from agentic.harness_optimizer.runners.base_runner import HarnessRunner, MockHarnessRunner, MockRunnerCase
from agentic.harness_optimizer.scoring import CaseResult, Scorecard, build_run_report, score_cases

__all__ = [
    "CandidateDecision",
    "CaseResult",
    "Experiment",
    "GovernanceFinding",
    "HarnessRunner",
    "LocalProposerClient",
    "LocalProposerResponse",
    "MockHarnessRunner",
    "MockRunnerCase",
    "ProposerWorkspace",
    "ProposerWorkspaceTools",
    "RunReport",
    "Scorecard",
    "Surface",
    "SurfaceType",
    "Variant",
    "build_proposer_workspace",
    "build_run_report",
    "decide_candidate",
    "detect_visible_case_hardcoding",
    "governance_gate_strings",
    "invoke_workspace_proposer",
    "score_cases",
]
