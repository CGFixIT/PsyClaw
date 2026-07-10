"""Lazy builder seam for optional LangChain Deep Agents integration."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentic.config import AgenticConfig
from agentic.deepagent_github.memory import load_local_memory_files
from agentic.deepagent_github.model_adapter import DeepAgentModelSettings
from agentic.deepagent_github.permissions import DeepAgentPermissionPolicy, refuse_unsupported_write_policy
from agentic.deepagent_github.skills import governed_skill_files
from agentic.deepagent_github.subagents import build_subagent_specs, default_subagents
from agentic.deepagent_github.tools import ToolSpec, default_tool_specs, workspace_tool_callables
from agentic.harness_optimizer.mcp.tools import ProposerWorkspaceTools
from agentic.registry import SkillRegistry
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
    input_files: dict[str, object] = field(default_factory=dict)
    interrupt_on: dict[str, object] = field(default_factory=dict)


def _load_create_deep_agent() -> Callable[..., Any]:
    try:
        from deepagents import create_deep_agent
    except ImportError as exc:
        raise AgenticError(
            "optional deepagents dependency is not installed",
            details={"extra": "agentic-deepagents"},
        ) from exc
    return create_deep_agent


def _load_runtime_model(
    settings: DeepAgentModelSettings,
) -> tuple[object, Callable[[], object], Callable[..., object], Callable[[str], object]]:
    """Load optional Deep Agents integrations only after the feature gates pass."""

    try:
        from deepagents import FilesystemPermission
        from deepagents.backends import StateBackend
        from deepagents.backends.utils import create_file_data
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise AgenticError(
            "optional Deep Agents runtime dependencies are not installed",
            details={"extra": "agentic-deepagents"},
        ) from exc

    # The local endpoint usually ignores this placeholder. An operator may supply
    # DEEPAGENT_API_KEY for an authenticated OpenAI-compatible endpoint; it is a
    # credential, not feature configuration, and is never logged.
    model = ChatOpenAI(
        model=settings.model,
        base_url=settings.base_url,
        api_key=os.getenv("DEEPAGENT_API_KEY", "not-needed"),
    )
    return model, StateBackend, FilesystemPermission, create_file_data


def _interrupt_config(tool_specs: tuple[ToolSpec, ...]) -> dict[str, object]:
    """Require approve/reject control before either scoped workspace write."""

    return {
        spec.name: {"allowed_decisions": ["approve", "reject"]}
        for spec in tool_specs
        if spec.allowed and spec.sensitive
    }


def _validate_wired_tools(
    workspace_tools: ProposerWorkspaceTools,
    policy: DeepAgentPermissionPolicy,
) -> tuple[tuple[Callable[..., Any], ...], tuple[str, ...]]:
    """Ensure the advertised tool catalog exactly matches real callable tools."""

    tool_specs = default_tool_specs(policy)
    expected = {spec.name for spec in tool_specs if spec.allowed}
    tool_callables = workspace_tool_callables(workspace_tools, policy)
    actual = {tool.__name__ for tool in tool_callables}
    if not actual:
        raise AgenticError("Deep Agents requires at least one wired tool callable")
    if actual != expected:
        raise AgenticError(
            "Deep Agents callable tools do not match the allowed tool specification",
            details={"expected": sorted(expected), "actual": sorted(actual)},
        )
    return tool_callables, tuple(tool.__name__ for tool in tool_callables)


def build_deepagent_github(
    agentic_config: AgenticConfig,
    *,
    create_fn: Callable[..., Any] | None = None,
    workspace_tools: ProposerWorkspaceTools | None = None,
    skill_registry: SkillRegistry | None = None,
    repo_root: Path | None = None,
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
    subagents = default_subagents()
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
            (),
            subagent_names,
        )
    try:
        refuse_unsupported_write_policy(policy)
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
            (),
            subagent_names,
        )
    settings = DeepAgentModelSettings.from_config(deep_cfg)
    if not settings.model.strip():
        return DeepAgentBuildResult(
            False,
            "model_not_configured",
            "agentic.deepagent_github.model must be configured before building",
            (),
            subagent_names,
        )
    if workspace_tools is None:
        return DeepAgentBuildResult(
            False,
            "workspace_required",
            "a scoped ProposerWorkspaceTools instance is required before building",
            (),
            subagent_names,
        )

    tool_callables, tool_names = _validate_wired_tools(workspace_tools, policy)
    tool_specs = default_tool_specs(policy)
    interrupt_on = _interrupt_config(tool_specs)
    memory_files = load_local_memory_files(repo_root or Path.cwd())
    skill_files = governed_skill_files(skill_registry) if skill_registry else {}
    raw_files = {**memory_files, **skill_files}

    if create_fn is not None:
        # The injected seam keeps unit tests dependency-free. The optional CI
        # test exercises the real StateBackend, permissions, and ChatOpenAI path.
        model: object = settings.model
        input_files: dict[str, object] = dict(raw_files)
        creator = create_fn
        kwargs: dict[str, object] = {}
    else:
        model, state_backend, filesystem_permission, create_file_data = _load_runtime_model(settings)
        input_files = {path: create_file_data(content) for path, content in raw_files.items()}
        creator = _load_create_deep_agent()
        # StateBackend is virtual per-thread storage, not a host filesystem. Deny
        # its built-in file tools anyway so every useful capability is a CyClaw
        # wrapper with the ProposerWorkspaceTools containment and audit checks.
        kwargs = {
            "backend": state_backend(),
            "permissions": [
                filesystem_permission(operations=["read", "write"], paths=["/**"], mode="deny"),
            ],
        }

    subagent_payloads = build_subagent_specs(
        model=model,
        tool_callables=tool_callables,
        interrupt_on=interrupt_on,
    )
    if interrupt_on:
        from langgraph.checkpoint.memory import InMemorySaver

        kwargs["checkpointer"] = InMemorySaver()
        kwargs["interrupt_on"] = interrupt_on
    if memory_files:
        kwargs["memory"] = list(memory_files)
    if skill_files:
        kwargs["skills"] = ["/skills/"]

    agent = creator(
        model=model,
        tools=list(tool_callables),
        system_prompt=(
            "CyClaw GitHub coding harness: use only scoped tools; propose changes, "
            "never mutate a real repository or GitHub."
        ),
        subagents=subagent_payloads,
        **kwargs,
    )
    return DeepAgentBuildResult(
        True,
        "created",
        "Deep Agents harness wired with scoped callables",
        tool_names,
        subagent_names,
        agent=agent,
        input_files=input_files,
        interrupt_on=interrupt_on,
    )
