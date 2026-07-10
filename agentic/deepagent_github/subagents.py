"""Subagent specifications for the optional GitHub coding harness skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from utils.errors import AgenticError, require_non_empty


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

    def __post_init__(self) -> None:
        require_non_empty(self.name, "subagent.name")
        require_non_empty(self.purpose, "subagent.purpose")
        require_non_empty(self.input_contract, "subagent.input_contract")
        require_non_empty(self.output_contract, "subagent.output_contract")
        overlap = set(self.allowed_tools) & set(self.denied_tools)
        if overlap:
            raise AgenticError(
                "subagent tool is both allowed and denied",
                details={"subagent": self.name, "tools": sorted(overlap)},
            )


def _validate_may_call_targets(subagents: tuple[SubagentSpec, ...]) -> None:
    # This can't live in SubagentSpec.__post_init__: a single spec is built
    # before it knows what other subagent names will exist in the same set, so
    # there's no way for one spec alone to tell if its may_call targets are
    # real. We wait until the whole tuple is assembled (see default_subagents
    # below) and check every spec's may_call against everyone else's names at
    # once — that's what catches a typo like "repo-context-redaer" instead of
    # letting it sit there silently until phase 6 wiring tries to use it.
    known = {subagent.name for subagent in subagents}
    for subagent in subagents:
        unknown = [target for target in subagent.may_call if target not in known]
        if unknown:
            raise AgenticError(
                "subagent.may_call references an undeclared subagent name",
                details={"subagent": subagent.name, "unknown_targets": unknown},
            )


def default_subagents() -> tuple[SubagentSpec, ...]:
    denied = ("local_shell", "github_write", "secret_read", "unrestricted_file")
    subagents = (
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
    _validate_may_call_targets(subagents)
    return subagents
