"""Optional Deep Agents-backed GitHub coding harness.

The runtime remains lazy and disabled by default. It permits only audited,
scoped proposer-workspace tools; shell, real-repository, and GitHub writes are
not part of this package.
"""

from __future__ import annotations

from agentic.deepagent_github.builder import DeepAgentBuildResult, build_deepagent_github
from agentic.deepagent_github.permissions import DeepAgentPermissionPolicy
from agentic.deepagent_github.runners import draft_plan, invoke_deepagent, resume_deepagent_interrupt
from agentic.deepagent_github.subagents import SubagentSpec, build_subagent_specs, default_subagents
from agentic.deepagent_github.tools import ToolSpec, default_tool_specs, workspace_tool_callables

__all__ = [
    "DeepAgentBuildResult",
    "DeepAgentPermissionPolicy",
    "SubagentSpec",
    "ToolSpec",
    "build_deepagent_github",
    "build_subagent_specs",
    "default_subagents",
    "default_tool_specs",
    "draft_plan",
    "invoke_deepagent",
    "resume_deepagent_interrupt",
    "workspace_tool_callables",
]
