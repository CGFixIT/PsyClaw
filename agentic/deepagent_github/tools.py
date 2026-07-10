"""Tool specifications for the optional Deep Agents GitHub harness skeleton."""

from __future__ import annotations

from dataclasses import dataclass

from agentic.deepagent_github.permissions import DeepAgentPermissionPolicy


@dataclass(frozen=True)
class ToolSpec:
    """Declarative tool policy, not a callable host tool."""

    name: str
    purpose: str
    allowed: bool
    sensitive: bool = False


def default_tool_specs(policy: DeepAgentPermissionPolicy | None = None) -> tuple[ToolSpec, ...]:
    """Return the phase-5 tool catalog.

    Write/shell/GitHub-write tools are represented only as denied specs so tests
    can assert they are unavailable by default.
    """

    policy = policy or DeepAgentPermissionPolicy()
    return (
        ToolSpec("repo_context_read", "Read repository, issue, or PR context through CyClaw wrappers.", True),
        ToolSpec("local_repo_read", "Read explicitly scoped local fixture/workspace files.", True),
        ToolSpec("rag_search_readonly", "Read-only CyClaw RAG lookup.", True),
        ToolSpec(
            "proposal_workspace_write_current",
            "Write only inside an optimizer proposer current/ workspace.",
            policy.allow_filesystem_write_tools,
            sensitive=True,
        ),
        ToolSpec("local_shell", "Host shell execution.", policy.allow_shell_execution, sensitive=True),
        ToolSpec("github_write", "External GitHub mutation.", policy.allow_github_writes, sensitive=True),
    )
