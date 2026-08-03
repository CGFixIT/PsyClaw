"""Self-contained tests for telegram.config."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from telegram.config import TelegramConfig, load_telegram_config
from utils.errors import TelegramConfigError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_config(tmp_path: Path, telegram_block: dict | None) -> str:
    cfg: dict = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}}
    if telegram_block is not None:
        cfg["telegram"] = telegram_block
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def test_absent_block_is_disabled(tmp_path: Path) -> None:
    path = _write_config(tmp_path, None)
    cfg = load_telegram_config(path)
    assert isinstance(cfg, TelegramConfig)
    assert cfg.enabled is False
    assert cfg.mode == "notify"
    assert cfg.allowed_chat_ids == []


def test_disabled_may_have_empty_allowlist(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": False, "allowed_chat_ids": []})
    cfg = load_telegram_config(path)
    assert cfg.enabled is False


def test_enabled_requires_allowlist(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": True, "allowed_chat_ids": []})
    with pytest.raises(TelegramConfigError) as exc:
        load_telegram_config(path)
    assert exc.value.code == "TELEGRAM_CONFIG_INVALID"


def test_valid_enabled_load(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "enabled": True,
            "mode": "chat",
            "allowed_chat_ids": [12345, "67890"],
            "query": {"base_url": "http://127.0.0.1:8787"},
        },
    )
    cfg = load_telegram_config(path)
    assert cfg.enabled is True
    assert cfg.mode == "chat"
    assert cfg.allowed_chat_ids == ["12345", "67890"]
    assert cfg.is_chat_allowed(12345)
    assert cfg.is_chat_allowed("67890")
    assert not cfg.is_chat_allowed(999)


def test_rejects_non_loopback_query_url(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {
            "enabled": False,
            "query": {"base_url": "http://example.com:8787"},
        },
    )
    with pytest.raises(TelegramConfigError):
        load_telegram_config(path)


def test_rejects_unknown_keys(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": False, "not_a_real_key": True})
    with pytest.raises(TelegramConfigError):
        load_telegram_config(path)


def test_rejects_invalid_mode(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": False, "mode": "webhook"})
    with pytest.raises(TelegramConfigError):
        load_telegram_config(path)


def test_dedupes_chat_ids(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {"enabled": True, "allowed_chat_ids": ["1", 1, "2"]},
    )
    cfg = load_telegram_config(path)
    assert cfg.allowed_chat_ids == ["1", "2"]


def test_to_public_dict_has_no_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token-value")
    path = _write_config(tmp_path, {"enabled": False})
    cfg = load_telegram_config(path)
    pub = cfg.to_public_dict()
    assert "secret-token-value" not in str(pub)
    assert pub["bot_token_set"] is True
