"""Optional-dependency integration checks for the real Deep Agents builder."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("deepagents")
pytest.importorskip("langchain_openai")

from deepagents.backends import StateBackend
from deepagents.backends.protocol import SandboxBackendProtocol

from agentic.config import AgenticConfig
from agentic.deepagent_github.builder import build_deepagent_github
from agentic.harness_optimizer import Experiment, ProposerWorkspaceTools, Surface, SurfaceType, build_proposer_workspace
from utils.logger import close_audit_handles


@pytest.fixture(autouse=True)
def _close_audit_handles():
    yield
    close_audit_handles()


def test_real_deepagents_builder_uses_state_backend_and_hitl(tmp_path: Path) -> None:
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}}
    experiment = Experiment(
        experiment_id="optional_deepagents",
        target_workspace="data/agentic/workspaces/optional_deepagents",
        surfaces=(Surface("planner", SurfaceType.GITHUB_CODING_PROMPT, "planner.md"),),
    )
    workspace = build_proposer_workspace(tmp_path / "runs", experiment, "candidate", cfg=cfg)
    agentic_config = AgenticConfig(
        deepagent_github={
            "enabled": True,
            "allow_deepagents_dependency": True,
            "allow_filesystem_write_tools": True,
            "model": "fixture-model",
        }
    )
    agentic_config.enabled = True  # type: ignore[attr-defined]

    result = build_deepagent_github(
        agentic_config,
        workspace_tools=ProposerWorkspaceTools(workspace, cfg=cfg),
        cfg=cfg,
    )

    assert result.created is True
    assert result.agent is not None
    assert set(result.interrupt_on) == {"proposal_workspace_write_current", "finish_proposal"}
    assert "local_shell" not in result.tool_names
    assert "github_write" not in result.tool_names
    # Deep Agents exposes an execute tool only for sandbox-capable backends. The
    # selected StateBackend is virtual state, so it cannot execute host commands.
    assert not isinstance(StateBackend(), SandboxBackendProtocol)
    assert not hasattr(StateBackend(), "execute")
