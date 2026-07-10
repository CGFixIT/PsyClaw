"""Tool specifications for the optional Deep Agents GitHub harness."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentic.deepagent_github.permissions import DeepAgentPermissionPolicy
from agentic.harness_optimizer.mcp.tools import ProposerWorkspaceTools
from utils.errors import AgenticError, require_non_empty
from utils.logger import audit_log


@dataclass(frozen=True)
class ToolSpec:
    """Declarative tool policy, not a callable host tool."""

    name: str
    purpose: str
    allowed: bool
    sensitive: bool = False

    def __post_init__(self) -> None:
        require_non_empty(self.name, "tool.name")
        require_non_empty(self.purpose, "tool.purpose")


def default_tool_specs(policy: DeepAgentPermissionPolicy | None = None) -> tuple[ToolSpec, ...]:
    """Return the constrained Deep Agents tool catalog.

    Only scoped proposer-workspace writes can become available. Host shell and
    GitHub write capabilities remain denied regardless of caller configuration.
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
        ToolSpec(
            "finish_proposal",
            "Write proposal.md through the explicit proposer workspace boundary.",
            policy.allow_filesystem_write_tools,
            sensitive=True,
        ),
        ToolSpec("local_shell", "Host shell execution.", policy.allow_shell_execution, sensitive=True),
        ToolSpec("github_write", "External GitHub mutation.", policy.allow_github_writes, sensitive=True),
    )


def workspace_tool_callables(
    workspace_tools: ProposerWorkspaceTools,
    policy: DeepAgentPermissionPolicy,
) -> tuple[Callable[..., Any], ...]:
    """Bind only audited, scoped workspace methods for a Deep Agent run."""

    allowed = {spec.name for spec in default_tool_specs(policy) if spec.allowed}

    def _call(name: str, func: Callable[..., Any], *args: object) -> Any:
        try:
            result = func(*args)
        except AgenticError as exc:
            audit_log(
                {"event": "agentic_deepagent_tool_denied", "tool": name, "reason": str(exc)},
                config_path=workspace_tools.config_path,
                cfg=workspace_tools.cfg,
            )
            raise
        audit_log(
            {"event": "agentic_deepagent_tool_allowed", "tool": name},
            config_path=workspace_tools.config_path,
            cfg=workspace_tools.cfg,
        )
        return result

    def repo_context_read() -> dict:
        """Read the runner-provided surface manifest for this scoped workspace."""

        return _call("repo_context_read", workspace_tools.read_surface_manifest)

    def local_repo_read(target: str) -> str:
        """Read one visible file from the scoped workspace."""

        return _call("local_repo_read", workspace_tools.read_file, target)

    def rag_search_readonly(query: str) -> dict:
        """Run the injected read-only RAG lookup for the scoped task."""

        return _call("rag_search_readonly", workspace_tools.rag_search_readonly, query)

    def proposal_workspace_write_current(target: str, content: str) -> dict:
        """Write a candidate file below current/ after human approval."""

        return _call("proposal_workspace_write_current", workspace_tools.write_current_file, target, content)

    def finish_proposal(content: str) -> dict:
        """Write proposal.md after human approval."""

        return _call("finish_proposal", workspace_tools.finish_proposal, content)

    callables = (
        repo_context_read,
        local_repo_read,
        rag_search_readonly,
        proposal_workspace_write_current,
        finish_proposal,
    )
    return tuple(tool for tool in callables if tool.__name__ in allowed)
