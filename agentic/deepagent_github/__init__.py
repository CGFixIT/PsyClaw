"""Optional Deep Agents-backed GitHub coding harness skeleton.

Phase 5 only: lazy builder seam, no writes, no shell, no GitHub writes, and no
``deepagents`` import unless explicitly enabled and requested.
"""

from __future__ import annotations

from agentic.deepagent_github.builder import DeepAgentBuildResult, build_deepagent_github
from agentic.deepagent_github.permissions import DeepAgentPermissionPolicy
from agentic.deepagent_github.subagents import SubagentSpec, default_subagents
from agentic.deepagent_github.tools import ToolSpec, default_tool_specs

__all__ = [
    "DeepAgentBuildResult",
    "DeepAgentPermissionPolicy",
    "SubagentSpec",
    "ToolSpec",
    "build_deepagent_github",
    "default_subagents",
    "default_tool_specs",
]
