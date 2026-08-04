"""Self-contained tests for telegram.config."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from telegram.config import (
    DEFAULT_HYBRID_CONFIRM_TTL_SEC,
    DEFAULT_MEDIA_MAX_DOWNLOAD_BYTES,
    TelegramConfig,
    load_telegram_config,
)
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


def test_t3_t4_defaults_are_default_off_and_bounded(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": False})
    cfg = load_telegram_config(path)
    assert cfg.allow_hybrid_confirm is False
    assert cfg.hybrid_confirm_ttl_sec == DEFAULT_HYBRID_CONFIRM_TTL_SEC
    assert cfg.media.enabled is False
    assert cfg.media.max_download_bytes == DEFAULT_MEDIA_MAX_DOWNLOAD_BYTES


@pytest.mark.parametrize("ttl", [False, 0, -1, 301, "120"])
def test_rejects_unsafe_hybrid_confirmation_ttl(tmp_path: Path, ttl: object) -> None:
    path = _write_config(tmp_path, {"enabled": False, "hybrid_confirm_ttl_sec": ttl})
    with pytest.raises(TelegramConfigError):
        load_telegram_config(path)


def test_media_enable_requires_explicit_fsconnect_root(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": False, "media": {"enabled": True}})
    with pytest.raises(TelegramConfigError) as exc:
        load_telegram_config(path)
    assert "fsconnect_root" in exc.value.message


def test_media_enable_requires_an_absolute_fsconnect_root(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {"enabled": False, "media": {"enabled": True, "fsconnect_root": "relative/staging"}},
    )
    with pytest.raises(TelegramConfigError) as exc:
        load_telegram_config(path)
    assert "absolute" in exc.value.message


@pytest.mark.parametrize("value", [0, -1, 20 * 1024 * 1024 + 1, "1024"])
def test_rejects_invalid_media_download_cap(tmp_path: Path, value: object) -> None:
    path = _write_config(
        tmp_path,
        {
            "enabled": False,
            "media": {"enabled": False, "max_download_bytes": value},
        },
    )
    with pytest.raises(TelegramConfigError):
        load_telegram_config(path)


def test_media_unknown_keys_are_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": False, "media": {"unknown": True}})
    with pytest.raises(TelegramConfigError):
        load_telegram_config(path)


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


def test_accepts_ipv6_loopback_query_url(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {"enabled": False, "query": {"base_url": "http://[::1]:8787"}},
    )
    assert load_telegram_config(path).query.base_url == "http://[::1]:8787"


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


@pytest.mark.parametrize("value", ["42", 42, {"42": True}, None])
def test_rejects_non_list_allowlist(tmp_path: Path, value: object) -> None:
    path = _write_config(tmp_path, {"enabled": False, "allowed_chat_ids": value})
    with pytest.raises(TelegramConfigError):
        load_telegram_config(path)


@pytest.mark.parametrize("field", ["rate_limit", "query"])
@pytest.mark.parametrize("value", [False, None, ""])
def test_rejects_non_mapping_nested_config(
    tmp_path: Path, field: str, value: object
) -> None:
    path = _write_config(tmp_path, {"enabled": False, field: value})
    with pytest.raises(TelegramConfigError):
        load_telegram_config(path)


def test_chat_ids_are_canonical_and_signed_64_bit(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        {"enabled": True, "allowed_chat_ids": ["00042", -(2**63)]},
    )
    cfg = load_telegram_config(path)
    assert cfg.allowed_chat_ids == ["42", str(-(2**63))]

    bad_path = _write_config(
        tmp_path,
        {"enabled": True, "allowed_chat_ids": [2**63]},
    )
    reset_config_cache()
    with pytest.raises(TelegramConfigError):
        load_telegram_config(bad_path)


def test_rejects_message_chunks_above_safe_telegram_limit(tmp_path: Path) -> None:
    boundary_path = _write_config(
        tmp_path,
        {"enabled": False, "max_message_chars": 4096},
    )
    assert load_telegram_config(boundary_path).max_message_chars == 4096

    reset_config_cache()
    path = _write_config(tmp_path, {"enabled": False, "max_message_chars": 4097})
    with pytest.raises(TelegramConfigError):
        load_telegram_config(path)


@pytest.mark.parametrize("raw", ["", "[]\n", "telegram: [\n"])
def test_invalid_config_documents_raise_typed_error(tmp_path: Path, raw: str) -> None:
    path = tmp_path / "config.yaml"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(TelegramConfigError):
        load_telegram_config(str(path))


def test_missing_config_raises_typed_error(tmp_path: Path) -> None:
    with pytest.raises(TelegramConfigError):
        load_telegram_config(str(tmp_path / "missing.yaml"))


def test_invalid_utf8_config_raises_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_bytes(b"\xff")
    with pytest.raises(TelegramConfigError):
        load_telegram_config(str(path))


def test_explicit_null_telegram_block_is_invalid(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("telegram: null\n", encoding="utf-8")
    with pytest.raises(TelegramConfigError):
        load_telegram_config(str(path))


def test_mixed_unknown_key_types_raise_typed_error(tmp_path: Path) -> None:
    path = tmp_path / "config.yaml"
    path.write_text("telegram:\n  1: value\n  unknown: value\n", encoding="utf-8")
    with pytest.raises(TelegramConfigError):
        load_telegram_config(str(path))


def test_invalid_env_name_does_not_echo_possible_secret(tmp_path: Path) -> None:
    possible_token = "123456789:ABC-DEF_possible-live-token"
    path = _write_config(tmp_path, {"enabled": False, "bot_token_env": possible_token})
    with pytest.raises(TelegramConfigError) as exc:
        load_telegram_config(path)
    assert possible_token not in exc.value.message
    assert possible_token not in str(exc.value.details)


def test_to_public_dict_has_no_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "secret-token-value")
    path = _write_config(tmp_path, {"enabled": False})
    cfg = load_telegram_config(path)
    pub = cfg.to_public_dict()
    assert "secret-token-value" not in str(pub)
    assert pub["bot_token_set"] is True
