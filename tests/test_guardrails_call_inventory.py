"""Fail-closed generation-call inventory (no nemoguardrails required)."""

from __future__ import annotations

from pathlib import Path

from guardrails.call_inventory import extra_call_sites, main, scan_tree


def test_inventory_finds_safe_generate_in_guardrails() -> None:
    sites = scan_tree()
    names = {(s.path.replace("\\", "/"), s.name) for s in sites}
    assert any(
        path.endswith("guardrails/integration.py") and name == "generate_async"
        for path, name in names
    )


def test_core_request_path_has_no_llmrails_generate_async() -> None:
    extras = extra_call_sites()
    core = [
        s
        for s in extras
        if s.path.replace("\\", "/") in {"gate.py", "graph.py", "mcp_hybrid_server.py"}
    ]
    assert core == []


def test_registered_adapters_are_the_only_production_callers() -> None:
    extras = extra_call_sites()
    assert extras == [], [f"{s.path}:{s.line} {s.name}" for s in extras]


def test_unregistered_chatxai_is_extra(tmp_path: Path) -> None:
    (tmp_path / "sneaky.py").write_text("ChatXAI(model='x')\n", encoding="utf-8")
    extras = extra_call_sites(tmp_path)
    assert any(s.name == "ChatXAI" and s.path.replace("\\", "/") == "sneaky.py" for s in extras)


def test_cli_exits_nonzero_on_extras(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "sneaky.py").write_text("ChatAnthropic(model='x')\n", encoding="utf-8")
    monkeypatch.setattr("guardrails.call_inventory.REPO_ROOT", tmp_path)
    assert main() == 1


def test_cli_exits_zero_when_no_extras() -> None:
    assert main() == 0


def test_scan_tree_skips_vcs_and_unreadable(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hook.py").write_text("generate()\n", encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()
    (tmp_path / "__pycache__" / "cached.py").write_text("generate()\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def (\n", encoding="utf-8")
    (tmp_path / "dir.py").mkdir()
    (tmp_path / "ok.py").write_text("print(1)\n", encoding="utf-8")
    sites = scan_tree(tmp_path)
    paths = {s.path.replace("\\", "/") for s in sites}
    assert "ok.py" not in paths
    assert not any(p.startswith(".git/") or p.startswith("__pycache__/") for p in paths)
    assert "broken.py" not in paths
    assert "dir.py" not in paths
