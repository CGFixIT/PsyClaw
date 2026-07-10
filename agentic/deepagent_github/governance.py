"""Governance checks for Deep Agents GitHub skeleton outputs."""

from __future__ import annotations

from agentic.deepagent_github.permissions import DeepAgentPermissionPolicy, refuse_phase5_write_policy


def validate_phase5_policy(policy: DeepAgentPermissionPolicy) -> bool:
    """Return true when the phase-5 no-write policy is satisfied."""

    refuse_phase5_write_policy(policy)
    return True
