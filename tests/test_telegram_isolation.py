"""Invariant guard: the Telegram channel must stay out of the request path.

The security argument for telegram/ is that it is out-of-band — exactly like
sync/, agentic/, and guardrails/. If gate.py, gate_ops.py, graph.py, or
mcp_hybrid_server.py ever imported ``telegram``, the channel would couple into
the request path.
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
def test_request_path_does_not_import_telegram(module_file):
    source = (REPO_ROOT / module_file).read_text(encoding="utf-8")
    assert "telegram" not in _imports(source), (
        f"{module_file} must not import the telegram package "
        "(it would couple the out-of-band channel into the request path)"
    )


def test_reverse_guard_flags_planted_request_path_import(tmp_path):
    forbidden = {"gate", "gate_ops", "graph", "mcp_hybrid_server"}
    planted = tmp_path / "telegram_probe.py"
    planted.write_text("import gate\nfrom graph import build_graph\n", encoding="utf-8")
    leaked = forbidden & _imports(planted.read_text(encoding="utf-8"))
    assert leaked == {"gate", "graph"}


def test_telegram_does_not_import_request_path():
    forbidden = {"gate", "gate_ops", "graph", "mcp_hybrid_server"}
    scanned = 0
    for py in (REPO_ROOT / "telegram").rglob("*.py"):
        scanned += 1
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports request-path module(s): {leaked}"
    assert scanned >= 1


def test_telegram_does_not_import_sibling_out_of_band():
    # Defense in depth: do not couple telegram to agentic/sync/guardrails/harness.
    forbidden = {"agentic", "sync", "guardrails", "harness"}
    for py in (REPO_ROOT / "telegram").rglob("*.py"):
        imported = _imports(py.read_text(encoding="utf-8"))
        leaked = forbidden & imported
        rel = py.relative_to(REPO_ROOT)
        assert not leaked, f"{rel} imports sibling out-of-band module(s): {leaked}"
