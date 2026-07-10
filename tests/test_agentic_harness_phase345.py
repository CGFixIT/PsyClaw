"""Phase 3-5 tests for governed harness optimizer and Deep Agents skeleton."""

from __future__ import annotations

import builtins
import json
import os
from pathlib import Path

import httpx
import pytest

from agentic.config import AgenticConfig
from agentic.deepagent_github import build_deepagent_github, default_subagents, default_tool_specs
from agentic.deepagent_github.permissions import DeepAgentPermissionPolicy
from agentic.harness_optimizer import (
    CaseResult,
    Experiment,
    LocalProposerClient,
    MockHarnessRunner,
    MockRunnerCase,
    ProposerWorkspaceTools,
    Scorecard,
    Surface,
    SurfaceType,
    Variant,
    build_proposer_workspace,
    decide_candidate,
    detect_visible_case_hardcoding,
    invoke_workspace_proposer,
    score_cases,
)
from utils.errors import AgenticError, AgenticWriteRefused
from utils.logger import close_audit_handles


@pytest.fixture(autouse=True)
def _close_audit_handles():
    yield
    close_audit_handles()


def _audit_cfg(tmp_path: Path) -> dict:
    return {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}}


def _experiment() -> Experiment:
    return Experiment(
        experiment_id="github_prompt_trial",
        target_workspace="data/agentic/workspaces/example",
        surfaces=(Surface("planner_prompt", SurfaceType.GITHUB_CODING_PROMPT, "prompts/planner.md"),),
        train_visible=("case-1",),
        holdout_hidden=("case-h1",),
    )


def _variant(variant_id: str = "candidate") -> Variant:
    return Variant(
        variant_id=variant_id,
        changed_surfaces=("planner_prompt",),
        proposal_path="proposal.md",
        artifact_dir="artifacts",
    )


def test_mock_runner_scorecard_and_acceptance_gate() -> None:
    baseline = MockHarnessRunner(
        (
            MockRunnerCase("case-1", "train_visible", True, 0.50),
            MockRunnerCase("case-h1", "holdout_hidden", True, 0.50),
        )
    ).run(_experiment(), _variant("baseline"))
    candidate = MockHarnessRunner(
        (
            MockRunnerCase("case-1", "train_visible", True, 0.90),
            MockRunnerCase("case-h1", "holdout_hidden", True, 0.80),
        )
    ).run(_experiment(), _variant("candidate"))

    decision = decide_candidate(
        baseline,
        candidate,
        allowed_surface_ids=_experiment().editable_surface_ids,
        proposal_present=True,
    )
    card = Scorecard(baseline, candidate, decision).to_markdown()

    assert decision.accepted is True
    assert "candidate_score: 0.8500" in card
    assert score_cases((CaseResult("x", True, 0.25), CaseResult("y", True, 0.75))) == 0.5


def test_mock_runner_rejects_undeclared_cases() -> None:
    runner = MockHarnessRunner((MockRunnerCase("unexpected", "train_visible", True, 1.0),))

    with pytest.raises(AgenticError):
        runner.run(_experiment(), _variant())


def test_visible_case_hardcoding_detector() -> None:
    assert detect_visible_case_hardcoding("Special handling for CASE-1", ("case-1",)) is True
    assert detect_visible_case_hardcoding("General planner instruction", ("case-1",)) is False


def test_workspace_tools_scope_writes_reads_and_audit(tmp_path: Path) -> None:
    cfg = _audit_cfg(tmp_path)
    workspace = build_proposer_workspace(tmp_path / "runs", _experiment(), "variant_1", cfg=cfg)
    (workspace.train_visible_dir / "failure.md").write_text("case-1 failed", encoding="utf-8")
    (workspace.history_dir / "prior.md").write_text("prior proposal", encoding="utf-8")
    (workspace.holdout_hidden_dir / "secret.md").write_text("hidden", encoding="utf-8")
    tools = ProposerWorkspaceTools(workspace, cfg=cfg)

    listed = tools.list_workspace()
    assert "holdout_hidden" not in {entry["name"] for entry in listed}
    assert tools.read_surface_manifest()["experiment_id"] == "github_prompt_trial"
    assert tools.read_train_failures()["failure.md"] == "case-1 failed"
    assert tools.write_current_file("planner.md", "new prompt")["sha256"]
    assert (workspace.current_dir / "planner.md").read_text(encoding="utf-8") == "new prompt"
    assert tools.finish_proposal("# Proposal\n\nBody")["bytes"] > 0

    with pytest.raises(AgenticError):
        tools.read_file("holdout_hidden/secret.md")
    with pytest.raises(AgenticError):
        tools.write_current_file("../escape.md", "x")

    events = [json.loads(line) for line in Path(cfg["logging"]["audit_file"]).read_text(encoding="utf-8").splitlines()]
    assert any(event["event"] == "agentic_harness_workspace_tool_allowed" for event in events)
    assert any(event["event"] == "agentic_harness_workspace_tool_denied" for event in events)


def test_workspace_tools_reject_symlink_escape(tmp_path: Path) -> None:
    cfg = _audit_cfg(tmp_path)
    workspace = build_proposer_workspace(tmp_path / "runs", _experiment(), "variant_1", cfg=cfg)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret", encoding="utf-8")
    link = workspace.current_dir / "link"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink creation unavailable on this host: {exc}")

    tools = ProposerWorkspaceTools(workspace, cfg=cfg)
    with pytest.raises(AgenticError):
        tools.read_file("current/link/secret.txt")


def test_local_lmstudio_proposer_uses_fake_transport(tmp_path: Path) -> None:
    cfg = _audit_cfg(tmp_path)
    workspace = build_proposer_workspace(tmp_path / "runs", _experiment(), "variant_1", cfg=cfg)
    (workspace.train_visible_dir / "failure.md").write_text("case-1 failed", encoding="utf-8")
    tools = ProposerWorkspaceTools(workspace, cfg=cfg)
    seen: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(200, json={"choices": [{"message": {"content": "# Proposal\n\nUse stricter plan."}}]})

    client = LocalProposerClient(
        base_url="http://localhost:1234/v1",
        model="local-test-model",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = invoke_workspace_proposer(client=client, tools=tools, instruction="Improve the planner", cfg=cfg)
    finally:
        client.close()

    assert seen["url"] == "http://localhost:1234/v1/chat/completions"
    assert result["proposal"]["sha256"]
    assert workspace.proposal_path.read_text(encoding="utf-8").startswith("# Proposal")


def _agentic_config(*, enabled: bool, deepagent: dict) -> AgenticConfig:
    cfg = AgenticConfig(deepagent_github=deepagent)
    cfg.enabled = enabled  # type: ignore[attr-defined]
    return cfg


def test_deepagent_disabled_builder_does_not_import_deepagents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    imported: list[str] = []
    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object):
        if name == "deepagents" or name.startswith("deepagents."):
            imported.append(name)
            raise AssertionError("deepagents must not be imported when disabled")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = build_deepagent_github(
        _agentic_config(enabled=False, deepagent={"enabled": True, "allow_deepagents_dependency": True}),
        cfg=_audit_cfg(tmp_path),
    )

    assert result.created is False
    assert result.status == "disabled"
    assert imported == []


def test_deepagent_dependency_flag_blocks_import(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    imported: list[str] = []
    original_import = builtins.__import__

    def guarded_import(name: str, *args: object, **kwargs: object):
        if name == "deepagents" or name.startswith("deepagents."):
            imported.append(name)
            raise AssertionError("deepagents must not be imported when dependency flag is false")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    result = build_deepagent_github(
        _agentic_config(enabled=True, deepagent={"enabled": True, "allow_deepagents_dependency": False}),
        cfg=_audit_cfg(tmp_path),
    )

    assert result.status == "dependency_not_allowed"
    assert imported == []


def test_deepagent_builder_injected_create_fn_path(tmp_path: Path) -> None:
    calls: dict[str, object] = {}

    def fake_create_deep_agent(**kwargs: object) -> dict:
        calls.update(kwargs)
        return {"agent": "fake"}

    result = build_deepagent_github(
        _agentic_config(
            enabled=True,
            deepagent={"enabled": True, "allow_deepagents_dependency": True, "model": "local-deep-agent"},
        ),
        create_fn=fake_create_deep_agent,
        cfg=_audit_cfg(tmp_path),
    )

    assert result.created is True
    assert calls["model"] == "local-deep-agent"
    assert "local_shell" not in result.tool_names
    assert "github_write" not in result.tool_names


def test_deepagent_phase5_refuses_write_and_shell_flags(tmp_path: Path) -> None:
    with pytest.raises(AgenticWriteRefused):
        build_deepagent_github(
            _agentic_config(enabled=True, deepagent={"enabled": True, "allow_shell_execution": True}),
            cfg=_audit_cfg(tmp_path),
        )


def test_deepagent_tool_and_subagent_specs_are_minimal() -> None:
    tools = default_tool_specs(DeepAgentPermissionPolicy())
    denied = {tool.name for tool in tools if not tool.allowed}
    subagents = default_subagents()

    assert {"proposal_workspace_write_current", "local_shell", "github_write"} <= denied
    assert {"repo-context-reader", "patch-proposer", "security-reviewer", "pr-writer"} <= {
        subagent.name for subagent in subagents
    }
    assert all("local_shell" in subagent.denied_tools for subagent in subagents)
    assert all("github_write" in subagent.denied_tools for subagent in subagents)
