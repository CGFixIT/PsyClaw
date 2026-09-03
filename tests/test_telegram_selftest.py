"""Direct tests for telegram.selftest.run_self_test (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from telegram.selftest import run_self_test
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write(tmp_path: Path, block: dict) -> str:
    raw = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "telegram": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


def test_run_self_test_invalid_config_skips_remaining(tmp_path: Path) -> None:
    path = str(tmp_path / "missing.yaml")
    passed, total, lines = run_self_test(config_path=path)
    assert total == 8
    # skip() counts as passed; only check 01 fails.
    assert passed == 7
    assert any("01. Config loads" in ln and "[FAIL]" in ln for ln in lines)
    assert sum(1 for ln in lines if "[SKIP]" in ln) == 7


def test_run_self_test_enabled_with_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unit-test-token")
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]},
    )
    passed, total, lines = run_self_test(config_path=path)
    assert total == 8
    assert passed == total
    joined = "\n".join(lines)
    assert "04. enabled with 1 allowlisted chat(s)" in joined
    assert "05. bot token is set via TELEGRAM_BOT_TOKEN" in joined


def test_run_self_test_enabled_missing_token_fails_check_05(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )
    passed, total, lines = run_self_test(config_path=path)
    assert total == 8
    assert passed < total
    assert any("05. bot token is set" in ln and "[FAIL]" in ln for ln in lines)


def test_run_self_test_hybrid_and_media_skips_when_armed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    root = tmp_path / "fsroot"
    root.mkdir()
    path = _write(
        tmp_path,
        {
            "enabled": False,
            "allow_hybrid_confirm": True,
            "media": {"enabled": True, "fsconnect_root": str(root)},
        },
    )
    _passed, _total, lines = run_self_test(config_path=path)
    joined = "\n".join(lines)
    assert "[SKIP] 07. allow_hybrid_confirm is false" in joined
    assert "[SKIP] 08. media staging is disabled by default" in joined


def test_run_self_test_detects_forbidden_import_leak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, {"enabled": False})
    fake_pkg = tmp_path / "fake_telegram_pkg"
    fake_pkg.mkdir()
    (fake_pkg / "leaky.py").write_text("import gate\n", encoding="utf-8")
    monkeypatch.setattr("telegram.selftest.PKG_ROOT", fake_pkg)
    monkeypatch.setattr("telegram.selftest.REPO_ROOT", tmp_path)
    _passed, _total, lines = run_self_test(config_path=path)
    assert any(
        "06. telegram/ does not import request-path modules" in ln and "[FAIL]" in ln
        for ln in lines
    )


def test_run_self_test_fail_branches_for_url_mode_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from telegram.config import load_telegram_config

    path = _write(tmp_path, {"enabled": False})

    def _bad_url(config_path: str = "config.yaml"):
        cfg = load_telegram_config(config_path)
        cfg.query.base_url = "https://evil.example"
        return cfg

    monkeypatch.setattr("telegram.selftest.load_telegram_config", _bad_url)
    _p, _t, lines = run_self_test(config_path=path)
    assert any("02. query.base_url is loopback" in ln and "[FAIL]" in ln for ln in lines)

    def _bad_mode(config_path: str = "config.yaml"):
        cfg = load_telegram_config(config_path)
        cfg.mode = "not-a-mode"
        return cfg

    monkeypatch.setattr("telegram.selftest.load_telegram_config", _bad_mode)
    _p, _t, lines = run_self_test(config_path=path)
    assert any("03. mode is valid" in ln and "[FAIL]" in ln for ln in lines)

    def _enabled_empty(config_path: str = "config.yaml"):
        cfg = load_telegram_config(config_path)
        cfg.enabled = True
        cfg.allowed_chat_ids = []
        return cfg

    monkeypatch.setattr("telegram.selftest.load_telegram_config", _enabled_empty)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    _p, _t, lines = run_self_test(config_path=path)
    assert any("04. enabled requires allowlist" in ln and "[FAIL]" in ln for ln in lines)
