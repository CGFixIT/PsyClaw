"""Governed harness optimizer scaffold for the out-of-band agentic layer.

Phases 2-8: local data models, fixture-only runner/scoring helpers, proposer
workspace creation, scoped tools, and human-gated candidate artifact records.
This package does not call GitHub, shell commands, or the CyClaw request path.
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
    inspect_candidate_text,
)
from agentic.harness_optimizer.mcp.tools import ProposerWorkspaceTools
from agentic.harness_optimizer.model_adapter import (
    LocalProposerClient,
    LocalProposerResponse,
    invoke_workspace_proposer,
)
from agentic.harness_optimizer.patching import (
    HarnessApplicationProposal,
    apply_candidate_artifact,
    propose_candidate_application,
)
from agentic.harness_optimizer.proposer import ProposerWorkspace, build_proposer_workspace
from agentic.harness_optimizer.runners.base_runner import HarnessRunner, MockHarnessRunner, MockRunnerCase
from agentic.harness_optimizer.scoring import CaseResult, Scorecard, build_run_report, score_cases

__all__ = [
    "CandidateDecision",
    "CaseResult",
    "Experiment",
    "GovernanceFinding",
    "HarnessApplicationProposal",
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
    "apply_candidate_artifact",
    "decide_candidate",
    "detect_visible_case_hardcoding",
    "governance_gate_strings",
    "inspect_candidate_text",
    "invoke_workspace_proposer",
    "propose_candidate_application",
    "score_cases",
]
