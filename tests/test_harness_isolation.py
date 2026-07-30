"""Invariant guard: the PowerShell coding harness must stay out of the request path.

CLAUDE.md's module table claims harness/ carries "the same I6 isolation as
every other out-of-band layer" -- but unlike agentic/, sync/, and guardrails/,
that claim had zero automated enforcement in either direction: harness was
never added to invariant-guard's OUT_OF_BAND_PKGS, and no dedicated pytest
isolation test existed for it (see test_agentic_isolation.py,
test_sync_isolation.py, test_guardrails_isolation.py for the sibling guards
this file matches). The property held today by convention only. This file
gives harness/ the same standalone regression guard its siblings already have.
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
def test_request_path_does_not_import_harness(module_file):
    source = (REPO_ROOT / module_file).read_text(encoding="utf-8")
    assert "harness" not in _imports(source), (
        f"{module_file} must not import the harness package "
        "(it would couple the out-of-band layer into the request path)"
    )


def test_reverse_guard_flags_planted_request_path_import(tmp_path):
    # Symmetric negative self-test: a planted harness-side module importing
    # gate/graph must trip the forbidden set, proving the guard below isn't
    # vacuously passing because _imports() is broken.
    forbidden = {"gate", "gate_ops", "graph", "mcp_hybrid_server"}
    planted = tmp_path / "harness_probe.py"
    planted.write_text("import gate\nfrom graph import build_graph\n", encoding="utf-8")
    leaked = forbidden & _imports(planted.read_text(encoding="utf-8"))
    assert leaked == {"gate", "graph"}, (
        f"guard is blind: planted request-path imports must be flagged, got {leaked}"
    )


def test_harness_does_not_import_request_path():
    # Symmetric guard: harness must not pull in gate/gate_ops/graph/mcp either.
    forbidden = {"gate", "gate_ops", "graph", "mcp_hybrid_server"}
    scanned = 0
    for py in (REPO_ROOT / "harness").rglob("*.py"):
        scanned += 1
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports request-path module(s): {leaked}"
    # Guard against a silently-empty glob masking a regression.
    assert scanned >= 1


def test_harness_does_not_import_sibling_out_of_band():
    # Defense in depth: harness must not import agentic/sync/guardrails
    # directly either -- GitHub operations go through utils.ops_runner's
    # subprocess shim into agentic.cli, exactly like /ops/agentic, per
    # harness/server.py's own module docstring.
    forbidden = {"agentic", "sync", "guardrails"}
    for py in (REPO_ROOT / "harness").rglob("*.py"):
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports sibling out-of-band module(s): {leaked}"
