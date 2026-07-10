"""No-write runner helpers for the optional Deep Agents GitHub skeleton."""

from __future__ import annotations

from agentic.deepagent_github.core import DeepAgentGitHubTask, DeepAgentPlan


def draft_plan(task: DeepAgentGitHubTask) -> DeepAgentPlan:
    """Raise until real planning is wired; will return a DeepAgentPlan derived from task."""

    raise NotImplementedError(
        "draft_plan is a phase-5 placeholder; real planning is wired in phase 6/7 — "
        "see docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md 'Unwired scaffold inventory'"
    )
