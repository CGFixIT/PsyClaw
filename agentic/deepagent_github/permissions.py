"""Permission policy for optional Deep Agents GitHub harness."""

from __future__ import annotations

from dataclasses import dataclass

from agentic.config import DeepAgentGitHubConfig
from utils.errors import AgenticWriteRefused


@dataclass(frozen=True)
class DeepAgentPermissionPolicy:
    """Default-deny policy for the optional Deep Agents integration."""

    allow_deepagents_dependency: bool = False
    allow_filesystem_write_tools: bool = False
    allow_shell_execution: bool = False
    allow_github_writes: bool = False

    @classmethod
    def from_config(cls, cfg: DeepAgentGitHubConfig) -> DeepAgentPermissionPolicy:
        return cls(
            allow_deepagents_dependency=cfg.allow_deepagents_dependency,
            allow_filesystem_write_tools=cfg.allow_filesystem_write_tools,
            allow_shell_execution=cfg.allow_shell_execution,
            allow_github_writes=cfg.allow_github_writes,
        )


def refuse_unsupported_write_policy(policy: DeepAgentPermissionPolicy) -> None:
    """Reject capabilities that remain outside phases 6-9.

    Scoped proposer-workspace writes are allowed in phase 6, but only through
    ``ProposerWorkspaceTools`` and only with Deep Agents HITL interrupts. Shell
    and GitHub writes remain hard-refused regardless of configuration.
    """

    if policy.allow_shell_execution:
        raise AgenticWriteRefused("Deep Agents shell execution is not implemented")
    if policy.allow_github_writes:
        raise AgenticWriteRefused("Deep Agents GitHub writes are not implemented")


def refuse_phase5_write_policy(policy: DeepAgentPermissionPolicy) -> None:
    """Backward-compatible name for callers that still use the phase-5 helper."""

    refuse_unsupported_write_policy(policy)
