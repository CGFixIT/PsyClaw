"""Operator docs must name the live graph node count, not a stale 10-node shape.

#986 added ``pre_action_hook_grok`` / ``pre_action_hook_claude``. Live
``graph.py`` is 12 nodes. Historical snapshots under docs/work/ and docs/NeMo
are out of scope — they already declare themselves stale.
"""

from __future__ import annotations

import ast
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]

_OPERATOR_DOCS = (
    "README.md",
    "CLAUDE.md",
    "docs/channels/TELEGRAM_DESIGN.md",
)


def _graph_add_node_count() -> int:
    tree = ast.parse((_REPO_ROOT / "graph.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        if node.func.attr != "add_node" or not node.args:
            continue
        arg0 = node.args[0]
        if isinstance(arg0, ast.Constant) and isinstance(arg0.value, str):
            names.add(arg0.value)
    return len(names)


def test_operator_docs_match_live_graph_node_count() -> None:
    count = _graph_add_node_count()
    assert count >= 12, f"graph.py add_node count is {count}, expected ≥12 after #986"
    needle = f"{count}-node"
    for rel in _OPERATOR_DOCS:
        text = (_REPO_ROOT / rel).read_text(encoding="utf-8")
        assert "10-node" not in text, f"{rel} still claims a 10-node graph"
        assert needle in text, f"{rel} does not mention the live {needle} graph"
