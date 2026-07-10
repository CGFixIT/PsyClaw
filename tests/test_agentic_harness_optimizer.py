"""Tests for the phase-2 harness optimizer scaffold."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentic.harness_optimizer import (
    Experiment,
    RunReport,
    Surface,
    SurfaceType,
    build_proposer_workspace,
    decide_candidate,
)
from utils.errors import AgenticError


def _experiment() -> Experiment:
    return Experiment(
        experiment_id="github_prompt_trial",
        target_workspace="data/agentic/workspaces/example",
        surfaces=(
            Surface(
                surface_id="planner_prompt",
                surface_type=SurfaceType.GITHUB_CODING_PROMPT,
                path="prompts/planner.md",
            ),
        ),
        train_visible=("case-1",),
        holdout_hidden=("case-h1",),
    )


def test_candidate_acceptance_requires_score_improvement_and_passed_suites() -> None:
    baseline = RunReport("baseline", train_passed=True, holdout_passed=True, score=0.70)
    candidate = RunReport(
        "candidate",
        train_passed=True,
        holdout_passed=True,
        score=0.80,
        changed_surfaces=("planner_prompt",),
    )

    decision = decide_candidate(
        baseline,
        candidate,
        allowed_surface_ids={"planner_prompt"},
        proposal_present=True,
    )

    assert decision.accepted is True
    assert decision.rejected_gates == ()


def test_candidate_rejects_no_improvement() -> None:
    baseline = RunReport("baseline", train_passed=True, holdout_passed=True, score=0.80)
    candidate = RunReport("candidate", train_passed=True, holdout_passed=True, score=0.80)

    decision = decide_candidate(
        baseline,
        candidate,
        allowed_surface_ids=set(),
        proposal_present=True,
    )

    assert decision.accepted is False
    assert "score_not_improved" in decision.rejected_gates


def test_candidate_rejects_unallowed_surface_and_visible_case_hardcoding() -> None:
    baseline = RunReport("baseline", train_passed=True, holdout_passed=True, score=0.40)
    candidate = RunReport(
        "candidate",
        train_passed=True,
        holdout_passed=True,
        score=0.90,
        changed_surfaces=("outside_policy",),
    )

    decision = decide_candidate(
        baseline,
        candidate,
        allowed_surface_ids={"planner_prompt"},
        proposal_present=True,
        visible_case_hardcoding_detected=True,
    )

    assert decision.accepted is False
    assert "unallowed_surface_changed" in decision.rejected_gates
    assert "visible_case_hardcoding" in decision.rejected_gates


def test_candidate_rejects_critical_governance_finding() -> None:
    baseline = RunReport("baseline", train_passed=True, holdout_passed=True, score=0.40)
    candidate = RunReport(
        "candidate",
        train_passed=True,
        holdout_passed=True,
        score=0.90,
        governance_findings=("critical: prompt injection",),
    )

    decision = decide_candidate(
        baseline,
        candidate,
        allowed_surface_ids=set(),
        proposal_present=True,
    )

    assert decision.accepted is False
    assert "critical_governance_finding" in decision.rejected_gates


def test_proposer_workspace_builder_creates_local_artifacts(tmp_path: Path) -> None:
    audit_file = tmp_path / "audit.jsonl"
    cfg = {"logging": {"audit_file": str(audit_file), "audit_fields": {}}, "policy": {"privacy": {}}}

    workspace = build_proposer_workspace(
        tmp_path / "runs",
        _experiment(),
        "variant_1",
        cfg=cfg,
    )

    assert workspace.current_dir.is_dir()
    assert workspace.history_dir.is_dir()
    assert workspace.train_visible_dir.is_dir()
    assert workspace.holdout_hidden_dir.is_dir()
    assert workspace.proposal_path.read_text(encoding="utf-8").startswith("# Proposal")

    manifest = json.loads(workspace.manifest_path.read_text(encoding="utf-8"))
    assert manifest["experiment_id"] == "github_prompt_trial"
    assert manifest["surfaces"][0]["surface_id"] == "planner_prompt"

    events = [json.loads(line) for line in audit_file.read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "agentic_harness_proposer_workspace_created"


def test_proposer_workspace_rejects_pathlike_variant_id(tmp_path: Path) -> None:
    with pytest.raises(AgenticError):
        build_proposer_workspace(tmp_path / "runs", _experiment(), "../escape", audit=False)


def test_experiment_rejects_duplicate_surface_ids() -> None:
    surface = Surface("dup", SurfaceType.REGISTRY_SKILL, "skills/one.md")
    with pytest.raises(AgenticError):
        Experiment("exp", "workspace", (surface, surface))


def test_require_human_confirm_flag_is_config_only__not_enforced() -> None:
    """Tripwire: agentic.harness_optimizer.require_human_confirm_for_accept is
    parsed and validated (agentic/config.py) but consulted by NO code path —
    decide_candidate() returns accepted=True with no human-confirm hook, the
    same "decorative flag" hazard CLAUDE.md documents for security.require_env.
    If you wire enforcement in (a legitimate hardening), update this test and
    the config.yaml comment deliberately — do not silently delete the tripwire.
    """
    import inspect

    assert "require_human_confirm" not in str(inspect.signature(decide_candidate))

    baseline = RunReport(variant_id="baseline", train_passed=True, holdout_passed=True, score=0.1)
    candidate = RunReport(variant_id="candidate", train_passed=True, holdout_passed=True, score=0.9)
    decision = decide_candidate(
        baseline,
        candidate,
        allowed_surface_ids=frozenset(),
        proposal_present=True,
    )
    assert decision.accepted is True
