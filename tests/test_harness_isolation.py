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
REQUEST_PATH_MODULES = ["gate.py", "gate_ops.py", "gate_auth.py", "gate_memory.py", "graph.py", "mcp_hybrid_server.py"]


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


def _import_modules(source: str) -> set[str]:
    """Full dotted module names (``from guardrails.tool_broker import X``)."""
    names: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.level == 0:
                names.add(node.module)
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
    forbidden = {"gate", "gate_ops", "gate_auth", "gate_memory", "graph", "mcp_hybrid_server"}
    planted = tmp_path / "harness_probe.py"
    planted.write_text("import gate\nfrom graph import build_graph\n", encoding="utf-8")
    leaked = forbidden & _imports(planted.read_text(encoding="utf-8"))
    assert leaked == {"gate", "graph"}, (
        f"guard is blind: planted request-path imports must be flagged, got {leaked}"
    )


def test_harness_does_not_import_request_path():
    # Symmetric guard: harness must not pull in the I6 request-path set.
    forbidden = {"gate", "gate_ops", "gate_auth", "gate_memory", "graph", "mcp_hybrid_server"}
    scanned = 0
    for py in (REPO_ROOT / "harness").rglob("*.py"):
        scanned += 1
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports request-path module(s): {leaked}"
    # Guard against a silently-empty glob masking a regression.
    assert scanned >= 1


# I6 still forbids gate/graph/mcp → guardrails. Harness may import ONLY the
# ToolBroker name-gate (oob-to-oob). broker.py / integration.py stay off-limits.
_SIBLING_FORBIDDEN_TOP = frozenset({"agentic", "sync", "memory"})
_GUARDRAILS_ALLOWED = frozenset({"guardrails.tool_broker"})


def test_harness_does_not_import_sibling_out_of_band():
    # Defense in depth: GitHub ops still go through utils.ops_runner, not
    # agentic imports. ToolBroker is the one allowed guardrails submodule.
    for py in (REPO_ROOT / "harness").rglob("*.py"):
        imported = _import_modules(py.read_text(encoding="utf-8"))
        rel = py.relative_to(REPO_ROOT)
        for name in imported:
            top = name.split(".")[0]
            if top in _SIBLING_FORBIDDEN_TOP:
                raise AssertionError(f"{rel} imports sibling out-of-band module: {name}")
            if top == "guardrails" and name not in _GUARDRAILS_ALLOWED:
                raise AssertionError(
                    f"{rel} imports {name}; only {_GUARDRAILS_ALLOWED} is allowed"
                )


def test_harness_sibling_guard_flags_nemo_broker_import():
    planted = "from guardrails.broker import GuardrailBroker\n"
    imported = _import_modules(planted)
    assert "guardrails.broker" in imported
    assert "guardrails.broker" not in _GUARDRAILS_ALLOWED
