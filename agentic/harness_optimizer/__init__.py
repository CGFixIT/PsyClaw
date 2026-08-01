"""Governed harness optimizer scaffold for the out-of-band agentic layer.

Phases 2-8: local data models, fixture-only runner/scoring helpers, proposer
workspace creation, scoped tools, and human-gated candidate artifact records.
This package does not call GitHub, shell commands, or the CyClaw request path.

``GitHubCodingRunner`` (``runners/github_coding_runner.py``), the loop driver
(``loop_driver.py``), and their supporting types are deliberately NOT
re-exported here, even though every other public name in this package is.
``github_coding_runner.py`` imports ``agentic.deepagent_github.builder``, and
``agentic.deepagent_github.tools`` imports ``agentic.harness_optimizer.mcp.tools``
-- so the moment this ``__init__.py`` imports anything from
``github_coding_runner``/``loop_driver`` at module scope, loading
``agentic.deepagent_github`` (which many callers, including ``agentic.cli``,
do routinely) forces this file to run, which in turn tries to finish loading
``agentic.deepagent_github.builder`` before it has, a genuine circular import
(confirmed by triggering it: ``ImportError: cannot import name
'DeepAgentBuildResult' from partially initialized module``). Import both
directly from their own modules instead --
``agentic.harness_optimizer.runners.github_coding_runner`` and
``agentic.harness_optimizer.loop_driver`` -- exactly how
``tests/test_agentic_harness_phase679.py`` already did before either of
those modules existed.
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
