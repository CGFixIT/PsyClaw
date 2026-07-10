"""Local data models for optional Deep Agents GitHub planning."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DeepAgentGitHubTask:
    """A local planning task for the optional GitHub coding harness."""

    task_id: str
    repo: str
    instruction: str
    issue_number: int | None = None
    pr_number: int | None = None


@dataclass(frozen=True)
class DeepAgentPlan:
    """Structured no-write output from the skeleton harness."""

    task_id: str
    steps: tuple[str, ...]
    proposed_tests: tuple[str, ...]
    pr_body: str = ""
