"""Invariant guard: the agentic layer must stay out of the request path.

The whole security argument for the agentic layer is that it is out-of-band --
exactly like sync/. If gate.py, gate_ops.py, graph.py, or mcp_hybrid_server.py
ever imported ``agentic``, the layer would be coupled into the request path
and could (in principle) influence retrieval, routing, or the MCP surface.
This test fails loudly if that coupling is ever introduced.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUEST_PATH_MODULES = ["gate.py", "gate_ops.py", "graph.py", "mcp_hybrid_server.py"]


def _imports(source: str) -> set[str]:
    """Top-level module names imported by ``source`` (import X / from X import ...)."""
    names: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


@pytest.mark.parametrize("module_file", REQUEST_PATH_MODULES)
def test_request_path_does_not_import_agentic(module_file):
    source = (REPO_ROOT / module_file).read_text(encoding="utf-8")
    assert "agentic" not in _imports(source), (
        f"{module_file} must not import the agentic package "
        "(it would couple the out-of-band layer into the request path)"
    )


def test_agentic_does_not_import_request_path():
    # Symmetric guard: agentic must not pull in gate/graph/mcp either. rglob so the
    # out-of-band sub-packages (agentic/fsconnect, agentic/sqlconnect) are covered too.
    forbidden = {"gate", "graph", "mcp_hybrid_server"}
    scanned = 0
    for py in (REPO_ROOT / "agentic").rglob("*.py"):
        scanned += 1
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports request-path module(s): {leaked}"
    # Guard against a silently-empty glob masking a regression.
    assert scanned >= 1


def test_subpackages_are_covered_by_isolation_scan():
    # Explicitly assert the new connectors exist and would be scanned above.
    for sub in ("fsconnect", "sqlconnect"):
        pkg = REPO_ROOT / "agentic" / sub
        assert pkg.is_dir(), f"expected out-of-band sub-package agentic/{sub}"
        assert list(pkg.rglob("*.py")), f"agentic/{sub} has no modules"
