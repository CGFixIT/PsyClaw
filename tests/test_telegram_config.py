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


def test_non_bool_enabled_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"enabled": "yes"})
    with pytest.raises(TelegramConfigError, match="YAML boolean"):
        load_telegram_config(path)


def test_rate_limit_non_int_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"rate_limit": {"max_ops": "fast"}})
    with pytest.raises(TelegramConfigError, match="must be an integer"):
        load_telegram_config(path)


def test_rate_limit_negative_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"rate_limit": {"max_ops": -1}})
    with pytest.raises(TelegramConfigError, match="must be > 0"):
        load_telegram_config(path)


def test_query_url_blank_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "   "}})
    with pytest.raises(TelegramConfigError, match="is required"):
        load_telegram_config(path)


def test_query_url_shell_metachar_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "http://127.0.0.1:8787;rm"}})
    with pytest.raises(TelegramConfigError, match="disallowed characters"):
        load_telegram_config(path)


def test_query_url_bad_scheme_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "ftp://127.0.0.1:8787"}})
    with pytest.raises(TelegramConfigError, match="must be http or https"):
        load_telegram_config(path)


def test_query_url_credentials_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "http://u:p@127.0.0.1:8787"}})
    with pytest.raises(TelegramConfigError, match="must not contain URL credentials"):
        load_telegram_config(path)


def test_query_url_fragment_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "http://127.0.0.1:8787/#x"}})
    with pytest.raises(TelegramConfigError, match="query or fragment"):
        load_telegram_config(path)


def test_shipped_query_timeout_clears_graph_deadline() -> None:
    # 790 = api.graph_timeout_sec (780) + 10s: the channel client must lose the
    # race so the server's diagnosable 504 GRAPH_TIMEOUT arrives instead of a
    # client abort (same pattern as static/terminal.js's queryDeadlineMs).
    shipped_path = Path(__file__).resolve().parent.parent / "config.yaml"
    shipped = yaml.safe_load(shipped_path.read_text(encoding="utf-8"))
    assert shipped["telegram"]["query"]["timeout_sec"] == 790
    assert shipped["telegram"]["query"]["timeout_sec"] > shipped["api"]["graph_timeout_sec"]

def test_validate_positive_int_allow_zero_rejects_negative() -> None:
    from telegram.config import _validate_positive_int

    with pytest.raises(TelegramConfigError, match="must be >= 0"):
        _validate_positive_int(-1, "field", allow_zero=True)
    assert _validate_positive_int(0, "field", allow_zero=True) == 0


def test_query_url_invalid_port_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"query": {"base_url": "http://127.0.0.1:notaport"}})
    with pytest.raises(TelegramConfigError, match="not a valid URL"):
        load_telegram_config(path)


def test_media_fsconnect_root_type_and_nul_rejected(tmp_path: Path) -> None:
    from telegram.config import TelegramMediaConfig

    path = _write_config(tmp_path, {"media": {"fsconnect_root": 12}})
    with pytest.raises(TelegramConfigError, match="fsconnect_root must be a string"):
        load_telegram_config(path)
    with pytest.raises(TelegramConfigError, match="NUL"):
        TelegramMediaConfig(fsconnect_root="C:/x\x00y")


def test_api_base_validation_branches() -> None:
    from telegram.config import TelegramConfig

    cases = [
        (123, "must be an https URL"),
        ("https://api.telegram.org;evil", "disallowed characters"),
        ("https://api.telegram.org:bad", "not a valid URL"),
        ("http://api.telegram.org", "must be an https URL"),
        ("https://user:pass@api.telegram.org", "must not contain URL credentials"),
        ("https://api.telegram.org/#frag", "query or fragment"),
    ]
    for value, match in cases:
        with pytest.raises(TelegramConfigError, match=match):
            TelegramConfig(api_base=value)  # type: ignore[arg-type]


def test_allowed_chat_ids_entry_type_and_shape_rejected() -> None:
    from telegram.config import TelegramConfig

    with pytest.raises(TelegramConfigError, match="must be int or digit-string"):
        TelegramConfig(allowed_chat_ids=[True])  # type: ignore[list-item]
    with pytest.raises(TelegramConfigError, match="entry invalid"):
        TelegramConfig(allowed_chat_ids=["abc"])


def test_nested_config_must_be_dataclass_instances(tmp_path: Path) -> None:
    # Construct TelegramConfig directly with bad nested types to hit __post_init__.
    from telegram.config import TelegramConfig, TelegramMediaConfig, TelegramQueryConfig, TelegramRateLimitConfig

    with pytest.raises(TelegramConfigError, match="rate_limit must be a mapping"):
        TelegramConfig(rate_limit="nope")  # type: ignore[arg-type]
    with pytest.raises(TelegramConfigError, match="query must be a mapping"):
        TelegramConfig(query="nope")  # type: ignore[arg-type]
    with pytest.raises(TelegramConfigError, match="media must be a mapping"):
        TelegramConfig(media="nope")  # type: ignore[arg-type]
    # Sanity: valid nested defaults still construct.
    assert isinstance(TelegramConfig().rate_limit, TelegramRateLimitConfig)
    assert isinstance(TelegramConfig().query, TelegramQueryConfig)
    assert isinstance(TelegramConfig().media, TelegramMediaConfig)


def test_runtime_bot_token_and_resolve_helpers(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = _write_config(tmp_path, {"bot_token_env": "TG_BOT_TOKEN", "query": {"api_key_env": "TG_API_KEY"}})
    cfg = load_telegram_config(path)
    with pytest.raises(TelegramConfigError, match="Prompted bot token is empty"):
        cfg.set_runtime_bot_token("   ")
    cfg.set_runtime_bot_token("123:ABC")
    assert cfg.resolve_bot_token() == "123:ABC"
    monkeypatch.delenv("TG_BOT_TOKEN", raising=False)
    cfg2 = load_telegram_config(path)
    with pytest.raises(TelegramConfigError, match="unset or empty"):
        cfg2.resolve_bot_token()
    monkeypatch.setenv("TG_BOT_TOKEN", "env-token")
    assert cfg2.resolve_bot_token() == "env-token"
    monkeypatch.delenv("TG_API_KEY", raising=False)
    assert cfg2.resolve_api_key() is None
    monkeypatch.setenv("TG_API_KEY", "k")
    assert cfg2.resolve_api_key() == "k"


def test_unknown_nested_keys_rejected(tmp_path: Path) -> None:
    path = _write_config(tmp_path, {"rate_limit": {"max_ops": 1, "window_seconds": 1, "extra": 1}})
    with pytest.raises(TelegramConfigError, match="rate_limit unknown"):
        load_telegram_config(path)
    reset_config_cache()
    path = _write_config(tmp_path, {"query": {"base_url": "http://127.0.0.1:8787", "surprise": True}})
    with pytest.raises(TelegramConfigError, match="query unknown"):
        load_telegram_config(path)
    reset_config_cache()
    path = _write_config(tmp_path, {"media": "not-a-map"})
    with pytest.raises(TelegramConfigError, match="media must be a mapping"):
        load_telegram_config(path)
