"""Governance checks for Deep Agents GitHub harness outputs."""

from __future__ import annotations

from agentic.deepagent_github.permissions import DeepAgentPermissionPolicy, refuse_unsupported_write_policy


def validate_write_policy(policy: DeepAgentPermissionPolicy) -> bool:
    """Return true when the policy has no unsupported (shell/GitHub-write) capability enabled."""

    refuse_unsupported_write_policy(policy)
    return True
