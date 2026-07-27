"""Invariant guard: the Dropbox corpus sync layer must stay out of the request path.

The whole security argument for sync/ is that it is out-of-band -- exactly like
agentic/ and guardrails/ (see test_agentic_isolation.py, test_guardrails_isolation.py).
If gate.py, gate_ops.py, graph.py, or mcp_hybrid_server.py ever imported ``sync``,
the layer would be coupled into the request path. invariant-guard's own I6 check
(check_invariants.py) has always covered sync/ in both directions, but unlike
agentic/ and guardrails/, sync/ had no dedicated pytest-level isolation test of
its own -- this file gives it the same standalone regression guard its siblings
already have.
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
def test_request_path_does_not_import_sync(module_file):
    source = (REPO_ROOT / module_file).read_text(encoding="utf-8")
    assert "sync" not in _imports(source), (
        f"{module_file} must not import the sync package "
        "(it would couple the out-of-band layer into the request path)"
    )


def test_reverse_guard_flags_planted_request_path_import(tmp_path):
    # Symmetric negative self-test for test_sync_does_not_import_request_path:
    # a planted sync-side module importing gate/graph must trip the forbidden set.
    forbidden = {"gate", "gate_ops", "graph", "mcp_hybrid_server"}
    planted = tmp_path / "sync_probe.py"
    planted.write_text("import gate\nfrom graph import build_graph\n", encoding="utf-8")
    leaked = forbidden & _imports(planted.read_text(encoding="utf-8"))
    assert leaked == {"gate", "graph"}, (
        f"guard is blind: planted request-path imports must be flagged, got {leaked}"
    )


def test_sync_does_not_import_request_path():
    # Symmetric guard: sync must not pull in gate/gate_ops/graph/mcp either.
    forbidden = {"gate", "gate_ops", "graph", "mcp_hybrid_server"}
    scanned = 0
    for py in (REPO_ROOT / "sync").rglob("*.py"):
        scanned += 1
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports request-path module(s): {leaked}"
    # Guard against a silently-empty glob masking a regression.
    assert scanned >= 1


def test_sync_does_not_import_sibling_out_of_band():
    # Defense in depth: sync must not import agentic/ or guardrails/ either.
    forbidden = {"agentic", "guardrails"}
    for py in (REPO_ROOT / "sync").rglob("*.py"):
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports sibling out-of-band module(s): {leaked}"
