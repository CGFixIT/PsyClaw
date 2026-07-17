"""Invariant guard: the NeMo guardrails layer must stay out of the request path.

The whole security argument for the guardrails layer is that it is out-of-band --
exactly like sync/ and agentic/. If gate.py, gate_ops.py, graph.py, or
mcp_hybrid_server.py ever imported ``guardrails``, the content-safety layer
would be coupled into the request path and could influence retrieval/routing
as hidden middleware, breaking the topology=policy invariant. This test fails
loudly if that coupling appears.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUEST_PATH_MODULES = ["gate.py", "gate_ops.py", "graph.py", "mcp_hybrid_server.py"]


def _imports(source: str) -> set[str]:
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
def test_request_path_does_not_import_guardrails(module_file):
    source = (REPO_ROOT / module_file).read_text(encoding="utf-8")
    assert "guardrails" not in _imports(source), (
        f"{module_file} must not import the guardrails package "
        "(it would couple the out-of-band layer into the request path)"
    )


def test_guardrails_does_not_import_request_path():
    forbidden = {"gate", "graph", "mcp_hybrid_server"}
    scanned = 0
    for py in (REPO_ROOT / "guardrails").rglob("*.py"):
        scanned += 1
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports request-path module(s): {leaked}"
    assert scanned >= 1


def test_guardrails_does_not_import_sibling_out_of_band():
    # Defense in depth: guardrails must not import agentic/ or sync/ either.
    forbidden = {"agentic", "sync"}
    for py in (REPO_ROOT / "guardrails").rglob("*.py"):
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports sibling out-of-band module(s): {leaked}"


def test_nemo_config_files_present():
    cfg_dir = REPO_ROOT / "guardrails" / "config"
    assert (cfg_dir / "config.yml").is_file()
    assert (cfg_dir / "rails.co").is_file()
