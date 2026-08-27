"""Advisory generation-call inventory (no nemoguardrails required)."""

from __future__ import annotations

from guardrails.call_inventory import extra_call_sites, scan_tree


def test_inventory_finds_safe_generate_in_guardrails() -> None:
    sites = scan_tree()
    names = {(s.path.replace("\\", "/"), s.name) for s in sites}
    assert any(path.endswith("guardrails/integration.py") and name == "generate_async" for path, name in names)


def test_core_request_path_has_no_llmrails_generate_async() -> None:
    extras = extra_call_sites()
    core = [s for s in extras if s.path.replace("\\", "/") in {"gate.py", "graph.py", "mcp_hybrid_server.py"}]
    assert core == []
