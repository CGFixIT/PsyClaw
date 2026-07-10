"""Lazy builder seam for optional LangChain Deep Agents integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentic.config import AgenticConfig
from agentic.deepagent_github.model_adapter import DeepAgentModelSettings
from agentic.deepagent_github.permissions import DeepAgentPermissionPolicy, refuse_phase5_write_policy
from agentic.deepagent_github.subagents import default_subagents
from agentic.deepagent_github.tools import default_tool_specs
from utils.errors import AgenticError, AgenticWriteRefused
from utils.logger import audit_log


@dataclass(frozen=True)
class DeepAgentBuildResult:
    """Result of attempting to build the optional Deep Agents harness."""

    created: bool
    status: str
    reason: str
    tool_names: tuple[str, ...]
    subagent_names: tuple[str, ...]
    agent: object | None = None


def _load_create_deep_agent() -> Callable[..., Any]:
    try:
        from deepagents import create_deep_agent
    except ImportError as exc:
        raise AgenticError(
            "optional deepagents dependency is not installed",
            details={"extra": "agentic-deepagents"},
        ) from exc
    return create_deep_agent


def build_deepagent_github(
    agentic_config: AgenticConfig,
    *,
    create_fn: Callable[..., Any] | None = None,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> DeepAgentBuildResult:
    """Build or describe the optional Deep Agents GitHub harness.

    Disabled configs and dependency-disallowed configs return a no-op result
    without importing ``deepagents``. The import happens only when both the
    top-level agentic layer and nested deepagent feature are enabled and
    ``allow_deepagents_dependency`` is true.
    """

    deep_cfg = agentic_config.deepagent_github
    policy = DeepAgentPermissionPolicy.from_config(deep_cfg)
    tools = default_tool_specs(policy)
    subagents = default_subagents()
    tool_names = tuple(tool.name for tool in tools if tool.allowed)
    subagent_names = tuple(subagent.name for subagent in subagents)
    enabled = bool(getattr(agentic_config, "enabled", False) and deep_cfg.enabled)

    audit_log(
        {
            "event": "agentic_deepagent_invoked",
            "enabled": enabled,
            "dependency_allowed": policy.allow_deepagents_dependency,
        },
        config_path=config_path,
        cfg=cfg,
    )

    if not enabled:
        return DeepAgentBuildResult(
            False,
            "disabled",
            "agentic or deepagent_github is disabled",
            tool_names,
            subagent_names,
        )
    # refuse_phase5_write_policy has no config/audit context of its own (it's a
    # pure policy check), so we catch its refusal here where config_path/cfg
    # are already in scope and log it before re-raising. Every other deny path
    # in this codebase (e.g. ProposerWorkspaceTools._deny) audits before
    # raising; without this, a request to enable shell/GitHub-write/filesystem
    # tools would leave no trace beyond the generic "invoked" event above.
    try:
        refuse_phase5_write_policy(policy)
    except AgenticWriteRefused as exc:
        audit_log(
            {"event": "agentic_deepagent_write_policy_refused", "reason": str(exc)},
            config_path=config_path,
            cfg=cfg,
        )
        raise
    if not policy.allow_deepagents_dependency:
        return DeepAgentBuildResult(
            False,
            "dependency_not_allowed",
            "agentic.deepagent_github.allow_deepagents_dependency is false",
            tool_names,
            subagent_names,
        )

    creator = create_fn or _load_create_deep_agent()
    settings = DeepAgentModelSettings.from_config(deep_cfg)
    agent = creator(
        model=settings.model,
        tools=[],
        system_prompt="CyClaw GitHub coding harness: propose plans and diffs only; do not write.",
        subagents=[subagent.name for subagent in subagents],
    )
    return DeepAgentBuildResult(True, "created", "deepagents builder invoked", tool_names, subagent_names, agent=agent)
