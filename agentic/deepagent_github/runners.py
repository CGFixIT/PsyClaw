"""No-write runner helpers for the optional Deep Agents GitHub skeleton."""

from __future__ import annotations

from agentic.deepagent_github.core import DeepAgentGitHubTask, DeepAgentPlan


def draft_plan(task: DeepAgentGitHubTask) -> DeepAgentPlan:
    """Return a local planning-only skeleton result."""

    return DeepAgentPlan(
        task_id=task.task_id,
        steps=("read context", "draft implementation plan", "propose diff", "select tests", "draft PR body"),
        proposed_tests=("agentic unit tests", "ruff agentic tests docs"),
    )
