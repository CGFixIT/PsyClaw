"""Subagent specifications for the optional GitHub coding harness skeleton."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SubagentSpec:
    """Declarative subagent contract; not a running agent."""

    name: str
    purpose: str
    allowed_tools: tuple[str, ...]
    denied_tools: tuple[str, ...]
    input_contract: str
    output_contract: str
    may_call: tuple[str, ...] = ()


def default_subagents() -> tuple[SubagentSpec, ...]:
    denied = ("local_shell", "github_write", "secret_read", "unrestricted_file")
    return (
        SubagentSpec(
            "repo-context-reader",
            "Read repository, issue, PR, and local fixture context.",
            ("repo_context_read", "local_repo_read", "rag_search_readonly"),
            denied,
            "repo id plus optional issue/pr/task instruction",
            "context bundle with source labels",
        ),
        SubagentSpec(
            "issue-planner",
            "Turn context into an implementation plan.",
            ("repo_context_read", "local_repo_read"),
            denied,
            "context bundle",
            "ordered implementation plan",
            ("repo-context-reader",),
        ),
        SubagentSpec(
            "patch-proposer",
            "Propose diffs without applying them to the real repo.",
            ("local_repo_read",),
            denied,
            "implementation plan and scoped workspace",
            "unapplied unified diff",
        ),
        SubagentSpec(
            "test-selector",
            "Select relevant validation commands.",
            ("repo_context_read", "local_repo_read"),
            denied,
            "plan and proposed diff",
            "test command list with rationale",
        ),
        SubagentSpec(
            "diff-reviewer",
            "Review proposed diff for regressions.",
            ("local_repo_read",),
            denied,
            "proposed diff and context",
            "review findings",
        ),
        SubagentSpec(
            "security-reviewer",
            "Review injection, secrets, path traversal, and supply-chain risks.",
            ("local_repo_read", "rag_search_readonly"),
            denied,
            "proposed diff and policy context",
            "security findings",
        ),
        SubagentSpec(
            "pr-writer",
            "Draft PR title, body, and checklist only.",
            ("local_repo_read",),
            denied,
            "plan, diff, and validation result summary",
            "draft PR text",
        ),
        SubagentSpec(
            "harness-proposer",
            "Propose harness prompt, skill, or policy improvements for optimizer review.",
            ("local_repo_read", "rag_search_readonly"),
            denied,
            "optimizer run report and visible artifacts",
            "candidate proposal only",
        ),
    )
