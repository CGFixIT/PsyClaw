"""Dependency-free proposer workspace tool wrappers.

Named ``mcp`` because these are the future MCP tool boundaries. This package
does not import or start an MCP server in phases 3-5.
"""

from __future__ import annotations

from agentic.harness_optimizer.mcp.tools import ProposerWorkspaceTools

__all__ = ["ProposerWorkspaceTools"]
