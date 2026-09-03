"""Tests for agentic.selftest -- pre-flight smoke (tolerates missing gh)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentic.selftest import run_self_test
from utils.errors import GhNotInstalledError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _config(tmp_path: Path) -> str:
    cfg = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]},
                   "privacy": {}},
        "agentic": {
            "enabled": True,
            "repo": "CGFixIT/CyClaw",
            "mode": "read",
            "writes_enabled": False,
            "gh_min_version": "2.40.0",
            "registry_path": "data/agentic/skills_registry.json",
        },
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def test_selftest_all_pass_without_gh(tmp_path, monkeypatch):
    # Even with gh absent (SKIP counts as pass), the suite should fully pass.
    def missing_gh(**_kwargs):
        raise GhNotInstalledError("gh not on PATH")

    monkeypatch.setattr("agentic.selftest.check_gh_version", missing_gh)
    passed, total, lines = run_self_test(_config(tmp_path))
    assert passed == total
    assert total >= 5
    joined = "\n".join(lines)
    assert "Write gate refuses" in joined
    assert "injection payload" in joined


def test_selftest_bad_config_skips_rest(tmp_path):
    cfg = {"logging": {"audit_file": str(tmp_path / "a.jsonl")}}  # no agentic block
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    passed, total, lines = run_self_test(str(path))
    # A missing agentic block is a real failure to surface: check 01 fails, the
    # rest are skipped (skips count as pass), so exactly one check fails.
    assert total == 5
    assert passed == total - 1
    assert "no config" in "\n".join(lines).lower()


def test_selftest_gh_ok_and_version_error(tmp_path, monkeypatch):
    from utils.errors import GhVersionError

    monkeypatch.setattr("agentic.selftest.check_gh_version", lambda **_k: (2, 50, 0))
    passed, total, lines = run_self_test(_config(tmp_path))
    assert passed == total
    assert any("gh 2.50.0" in ln for ln in lines)

    monkeypatch.setattr(
        "agentic.selftest.check_gh_version",
        lambda **_k: (_ for _ in ()).throw(GhVersionError("too old")),
    )
    passed, total, lines = run_self_test(_config(tmp_path))
    assert passed == total - 1
    assert any("gh >=" in ln.lower() or "too old" in ln.lower() for ln in lines)


def test_selftest_read_argv_and_write_gate_failure_branches(tmp_path, monkeypatch):
    monkeypatch.setattr("agentic.selftest.check_gh_version", lambda **_k: (2, 50, 0))
    monkeypatch.setattr("agentic.selftest.build_read_argv", lambda *_a, **_k: ["gh", "wrong"])
    passed, total, lines = run_self_test(_config(tmp_path))
    assert passed == total - 1
    assert any("Read argv" in ln for ln in lines)

    monkeypatch.setattr(
        "agentic.selftest.build_read_argv",
        lambda *_a, **_k: ["gh", "pr", "view", "1", "--repo", "CGFixIT/CyClaw"],
    )

    def _no_refuse(*_a, **_k):
        return {"status": "dry_run_plan"}

    monkeypatch.setattr("agentic.selftest.plan_write", _no_refuse)
    passed, total, lines = run_self_test(_config(tmp_path))
    assert passed == total - 1
    assert any("did NOT refuse" in ln for ln in lines)


def test_selftest_registry_scanner_failure_branches(tmp_path, monkeypatch):
    monkeypatch.setattr("agentic.selftest.check_gh_version", lambda **_k: (2, 50, 0))

    class _BadReg:
        def propose_skill(self, *_a, **_k):
            return {"safe_to_apply": True, "injection_flag_count": 0}

    monkeypatch.setattr("agentic.selftest.SkillRegistry", lambda *_a, **_k: _BadReg())
    passed, total, lines = run_self_test(_config(tmp_path))
    assert passed == total - 1
    assert any("not flagged" in ln for ln in lines)

    class _BoomReg:
        def propose_skill(self, *_a, **_k):
            raise RuntimeError("scanner exploded")

    monkeypatch.setattr("agentic.selftest.SkillRegistry", lambda *_a, **_k: _BoomReg())
    passed, total, lines = run_self_test(_config(tmp_path))
    assert passed == total - 1
    assert any("scanner exploded" in ln for ln in lines)
