"""No-write planning and HITL helpers for the optional Deep Agents harness."""

from __future__ import annotations

from typing import Any, Literal

from httpx import HTTPError

from agentic.deepagent_github.builder import DeepAgentBuildResult
from agentic.deepagent_github.core import DeepAgentGitHubTask, DeepAgentPlan
from utils.errors import AgenticError
from utils.logger import audit_log


def draft_plan(task: DeepAgentGitHubTask) -> DeepAgentPlan:
    """Create a deterministic no-write harness plan from the requested task."""

    if not task.task_id.strip() or not task.repo.strip() or not task.instruction.strip():
        raise AgenticError("Deep Agent task_id, repo, and instruction must be non-empty")
    source = f"PR #{task.pr_number}" if task.pr_number is not None else (
        f"issue #{task.issue_number}" if task.issue_number is not None else "repository context"
    )
    steps = (
        f"Collect read-only {source} for {task.repo}.",
        "Build a scoped proposer workspace and expose only CyClaw tool wrappers.",
        "Produce an unapplied diff and proposal.md in that workspace.",
        "Run fixture-only validation and draft a PR body for human review.",
    )
    proposed_tests = (
        "pytest tests/test_agentic_harness_phase679.py -q",
        "pytest tests/test_agentic_deepagent_optional.py -q",
    )
    return DeepAgentPlan(
        task_id=task.task_id,
        steps=steps,
        proposed_tests=proposed_tests,
        pr_body=(
            "## Summary\n"
            f"- Planned a scoped, no-write Deep Agents task for `{task.repo}`.\n\n"
            "## Validation\n"
            "- Fixture-only harness validation is required before any proposal is accepted.\n\n"
            "## Security\n"
            "- No real repository, shell, or GitHub mutation is part of this plan.\n"
        ),
    )


def invoke_deepagent(
    build: DeepAgentBuildResult,
    task: DeepAgentGitHubTask,
    *,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> object:
    """Invoke a built agent with only virtual memory/skill files in its state."""

    if not build.created or build.agent is None:
        raise AgenticError("Deep Agents harness is not built", details={"status": build.status})
    payload = {
        "messages": [{"role": "user", "content": task.instruction}],
        "files": dict(build.input_files),
    }
    run_config = {"configurable": {"thread_id": task.task_id}}
    try:
        result = build.agent.invoke(payload, config=run_config, version="v2")  # type: ignore[attr-defined]
    except (HTTPError, OSError, RuntimeError, TypeError, ValueError) as exc:
        audit_log(
            {"event": "agentic_deepagent_invocation_failed", "error_type": type(exc).__name__},
            config_path=config_path,
            cfg=cfg,
        )
        raise AgenticError("Deep Agents invocation failed") from exc
    audit_log(
        {"event": "agentic_deepagent_invocation_finished", "task_id": task.task_id},
        config_path=config_path,
        cfg=cfg,
    )
    return result


def resume_deepagent_interrupt(
    agent: Any,
    *,
    task_id: str,
    decision: Literal["approve", "reject", "timeout"],
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> object:
    """Resume a Deep Agents HITL interrupt; timeout is an explicit rejection."""

    if decision not in {"approve", "reject", "timeout"}:
        raise AgenticError("Deep Agents interrupt decision must be approve, reject, or timeout")
    from langgraph.types import Command

    resolved = "reject" if decision == "timeout" else decision
    message = "approval timed out" if decision == "timeout" else f"human {resolved}d the action"
    audit_log(
        {
            "event": "agentic_deepagent_interrupt_resumed",
            "task_id": task_id,
            "decision": decision,
            "resolved_decision": resolved,
        },
        config_path=config_path,
        cfg=cfg,
    )
    return agent.invoke(
        Command(resume={"decisions": [{"type": resolved, "message": message}]}),
        config={"configurable": {"thread_id": task_id}},
        version="v2",
    )
