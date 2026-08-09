"""Isolation guard: memory package is lazy-only on the request path.

Memory is an optional core feature (not OOB like telegram). Isolation means:
no top-level ``import memory`` in gate/graph/mcp/hybrid_search/gate_memory,
and memory must not import OOB packages or request-path modules.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Modules that must not top-level-import the memory package.
NO_TOPLEVEL_MEMORY = [
    "gate.py",
    "graph.py",
    "mcp_hybrid_server.py",
    "gate_memory.py",
    "retrieval/hybrid_search.py",
]

MEMORY_FORBIDDEN_IMPORTS = {
    "gate",
    "gate_ops",
    "gate_memory",
    "graph",
    "mcp_hybrid_server",
    "telegram",
    "agentic",
    "sync",
    "guardrails",
    "harness",
}


def _toplevel_imports(source: str) -> set[str]:
    """Return package roots imported at module top level only (not inside functions)."""
    names: set[str] = set()
    tree = ast.parse(source)
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module.split(".")[0])
    return names


def _all_imports(source: str) -> set[str]:
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


@pytest.mark.parametrize("module_file", NO_TOPLEVEL_MEMORY)
def test_no_toplevel_import_of_memory(module_file: str):
    path = REPO_ROOT / module_file
    assert path.is_file(), f"missing {module_file}"
    source = path.read_text(encoding="utf-8")
    assert "memory" not in _toplevel_imports(source), (
        f"{module_file} must not top-level-import the memory package "
        "(lazy import inside handlers/hooks only)"
    )


def test_memory_package_does_not_import_forbidden():
    scanned = 0
    for py in (REPO_ROOT / "memory").rglob("*.py"):
        scanned += 1
        imported = _all_imports(py.read_text(encoding="utf-8"))
        leaked = MEMORY_FORBIDDEN_IMPORTS & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports forbidden module(s): {leaked}"
    assert scanned >= 1


def test_gate_may_import_gate_memory_only():
    """gate.py may import gate_memory (like gate_ops); that is not package memory."""
    source = (REPO_ROOT / "gate.py").read_text(encoding="utf-8")
    top = _toplevel_imports(source)
    assert "memory" not in top
    # After wiring, gate_memory should be present; allow either during partial checkout.
    # Soft check: if register_memory_routes appears, gate_memory must be imported.
    if "register_memory_routes" in source:
        assert "gate_memory" in top or "gate_memory" in _all_imports(source)
