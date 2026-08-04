"""Unit tests for telegram.client (httpx mocked; no network)."""

from __future__ import annotations

import hashlib
import traceback
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from telegram.client import chunk_text, get_updates, post_query, reset_http_client_for_tests, send_message
from telegram.config import load_telegram_config
from telegram.ratelimit import get_limiter, reset_limiters_for_tests
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    reset_http_client_for_tests()
    reset_limiters_for_tests()
    yield
    reset_config_cache()
    reset_http_client_for_tests()
    reset_limiters_for_tests()


def _cfg(tmp_path: Path, **overrides: object):
    block = {
        "enabled": True,
        "mode": "chat",
        "allowed_chat_ids": ["42"],
        "query": {"base_url": "http://127.0.0.1:8787"},
    }
    block.update(overrides)
    raw = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "telegram": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_telegram_config(str(path))


def test_send_message_allowlist_refuses(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    with pytest.raises(TelegramRefused) as exc:
        send_message(cfg, chat_id=999, text="hi")
    assert exc.value.details and exc.value.details.get("gate") == "allowlist"


def test_chunk_text_prefers_paragraph() -> None:
    text = "para one\n\n" + ("x" * 50) + "\n\npara three"
    parts = chunk_text(text, max_chars=40)
    assert len(parts) >= 2
    assert all(len(p) <= 40 for p in parts)
    assert "para one" in parts[0]


def test_send_message_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 7}}

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.post.return_value = mock_resp
        data = send_message(cfg, chat_id=42, text="hello")
    assert data["ok"] is True
    assert data["result"]["message_id"] == 7


def test_send_message_chunks_long_body(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path, max_message_chars=30)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 1}}

    with patch("telegram.client.httpx.Client") as client_cls:
        post = client_cls.return_value.post
        post.return_value = mock_resp
        send_message(cfg, chat_id=42, text=("hello world\n\n" * 5).strip())
    assert post.call_count >= 2


def test_send_message_atomically_reserves_chunk_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(
        tmp_path,
        max_message_chars=4,
        rate_limit={"max_ops": 3, "window_seconds": 60},
    )
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": {"message_id": 1}}

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.post.return_value = mock_resp
        send_message(cfg, chat_id=42, text="abcdefghijkl")
        with pytest.raises(TelegramRefused) as exc:
            send_message(cfg, chat_id=42, text="x")
    # The first logical reply is delivered in full before the next reply is
    # refused; a low valid budget can never strand the final chunk forever.
    assert client_cls.return_value.post.call_count == 3
    assert (exc.value.details or {}).get("gate") == "rate_limit"


def test_send_message_chunk_retry_cannot_be_stranded_by_local_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(
        tmp_path,
        max_message_chars=4,
        rate_limit={"max_ops": 3, "window_seconds": 60},
    )
    success = MagicMock()
    success.status_code = 200
    success.json.return_value = {"ok": True, "result": {"message_id": 1}}
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.json.return_value = {
        "ok": False,
        "parameters": {"retry_after": 0},
    }

    with (
        patch("telegram.client.httpx.Client") as client_cls,
        patch("telegram.client.time.sleep") as sleep,
    ):
        client_cls.return_value.post.side_effect = [success, rate_limited, success]
        data = send_message(cfg, chat_id=42, text="abcdefgh")
        with pytest.raises(TelegramRefused) as exc:
            send_message(cfg, chat_id=42, text="x")
    assert data["ok"] is True
    assert client_cls.return_value.post.call_count == 3
    assert (exc.value.details or {}).get("gate") == "rate_limit"
    sleep.assert_called_once_with(0.0)


def test_send_message_uses_partial_retry_headroom(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(
        tmp_path,
        max_message_chars=4,
        rate_limit={"max_ops": 5, "window_seconds": 60},
    )
    get_limiter(5, 60).check("tg:outbound", cost=2)
    success = MagicMock()
    success.status_code = 200
    success.json.return_value = {"ok": True, "result": {"message_id": 1}}
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.json.return_value = {
        "ok": False,
        "parameters": {"retry_after": 0},
    }

    with (
        patch("telegram.client.httpx.Client") as client_cls,
        patch("telegram.client.time.sleep") as sleep,
    ):
        client_cls.return_value.post.side_effect = [success, rate_limited, success]
        data = send_message(cfg, chat_id=42, text="abcdefgh")

    assert data["ok"] is True
    assert client_cls.return_value.post.call_count == 3
    sleep.assert_called_once_with(0.0)


def test_send_message_refuses_oversized_chunk_batch_before_first_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(
        tmp_path,
        max_message_chars=4,
        rate_limit={"max_ops": 2, "window_seconds": 60},
    )

    with patch("telegram.client.httpx.Client") as client_cls:
        with pytest.raises(TelegramRefused) as exc:
            send_message(cfg, chat_id=42, text="abcdefghijkl")
    assert client_cls.return_value.post.call_count == 0
    assert (exc.value.details or {}).get("gate") == "rate_limit_capacity"


def test_send_message_api_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {"ok": False, "description": "bad request"}

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.post.return_value = mock_resp
        with pytest.raises(TelegramRuntimeError):
            send_message(cfg, chat_id=42, text="hello")


def test_send_message_redacts_token_from_api_description(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token = "123456:ABC-live-secret"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {
        "ok": False,
        "description": f"bad request for {token}",
    }

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.post.return_value = mock_resp
        with pytest.raises(TelegramRuntimeError) as exc:
            send_message(cfg, chat_id=42, text="hello")
    assert token not in exc.value.message
    assert token not in str(exc.value.details)
    assert (exc.value.details or {}).get("retryable") is False


def test_send_message_retries_429_with_bounded_retry_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.json.return_value = {
        "ok": False,
        "error_code": 429,
        "parameters": {"retry_after": 2},
    }
    success = MagicMock()
    success.status_code = 200
    success.json.return_value = {"ok": True, "result": {"message_id": 8}}

    with (
        patch("telegram.client.httpx.Client") as client_cls,
        patch("telegram.client.time.sleep") as sleep,
    ):
        client_cls.return_value.post.side_effect = [rate_limited, success]
        data = send_message(cfg, chat_id=42, text="hello")
    assert data["result"]["message_id"] == 8
    assert client_cls.return_value.post.call_count == 2
    sleep.assert_called_once_with(2.0)


def test_send_message_defers_over_cap_429_without_premature_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.json.return_value = {
        "ok": False,
        "description": "retry later",
        "parameters": {"retry_after": 999},
    }

    with (
        patch("telegram.client.httpx.Client") as client_cls,
        patch("telegram.client.time.sleep") as sleep,
    ):
        client_cls.return_value.post.return_value = rate_limited
        with pytest.raises(TelegramRuntimeError) as exc:
            send_message(cfg, chat_id=42, text="hello")
        # The same bot is locally deferred, so even getUpdates cannot issue a
        # request before Telegram's server-provided deadline.
        with pytest.raises(TelegramRuntimeError) as cooldown:
            get_updates(cfg)
    assert client_cls.return_value.post.call_count == 1
    assert client_cls.return_value.get.call_count == 0
    sleep.assert_not_called()
    assert (exc.value.details or {}).get("retry_after") == 999.0
    assert (cooldown.value.details or {}).get("retryable") is True


def test_cooldown_refusal_does_not_consume_outbound_capacity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(
        tmp_path,
        rate_limit={"max_ops": 1, "window_seconds": 60},
    )
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.json.return_value = {
        "ok": False,
        "parameters": {"retry_after": 999},
    }
    success = MagicMock()
    success.status_code = 200
    success.json.return_value = {"ok": True, "result": {"message_id": 1}}

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.get.return_value = rate_limited
        with pytest.raises(TelegramRuntimeError):
            get_updates(cfg)
        with pytest.raises(TelegramRuntimeError):
            send_message(cfg, chat_id=42, text="deferred")

        # Clearing only the Telegram cooldown exposes whether the refused send
        # incorrectly consumed the separate local outbound budget.
        reset_http_client_for_tests()
        client_cls.return_value.post.return_value = success
        data = send_message(cfg, chat_id=42, text="ready")

    assert data["ok"] is True
    assert client_cls.return_value.post.call_count == 1


@pytest.mark.parametrize("retry_after", [True, -1, float("nan"), 10**1000])
def test_send_message_rejects_malformed_retry_after_without_crashing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, retry_after: object
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.json.return_value = {
        "ok": False,
        "description": "retry later",
        "parameters": {"retry_after": retry_after},
    }

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.post.return_value = rate_limited
        with pytest.raises(TelegramRuntimeError) as exc:
            send_message(cfg, chat_id=42, text="hello")
    assert (exc.value.details or {}).get("status") == 429
    assert (exc.value.details or {}).get("retry_after") is None


def test_send_message_rejects_effectively_unbounded_retry_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.json.return_value = {
        "ok": False,
        "parameters": {"retry_after": 1e308},
    }

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.post.return_value = rate_limited
        with pytest.raises(TelegramRuntimeError) as exc:
            send_message(cfg, chat_id=42, text="hello")
    assert (exc.value.details or {}).get("fatal") is True
    assert (exc.value.details or {}).get("retryable") is False


def test_send_message_transport_error_redacts_bot_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """httpx errors often embed the request URL; the bot token is in that path."""
    import httpx

    token = "123456:ABC-DEF_secret-token"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    cfg = _cfg(tmp_path)

    class _HttpLeak(httpx.HTTPError):
        def __str__(self) -> str:
            return (
                f"ConnectError: GET https://api.telegram.org/bot{token}/sendMessage "
                "body=hello failed"
            )

    with (
        patch("telegram.client.httpx.Client") as client_cls,
        patch("telegram.client.audit_log") as audit,
    ):
        client_cls.return_value.post.side_effect = _HttpLeak("boom")
        with pytest.raises(TelegramRuntimeError) as exc:
            send_message(cfg, chat_id=42, text="hello")
    msg = getattr(exc.value, "message", str(exc.value))
    assert token not in msg
    assert "sendMessage" in msg
    rendered = "".join(traceback.format_exception(exc.value))
    assert token not in rendered
    assert "body=hello" not in rendered
    event = audit.call_args.args[0]
    assert event["ok"] is False
    assert event["error_type"] == "_HttpLeak"
    assert "hello" not in str(event)
    assert token not in str(event)


def test_get_updates_transport_error_redacts_bot_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    token = "999:live-bot-token-xyz"
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", token)
    cfg = _cfg(tmp_path)

    class _HttpLeak(httpx.HTTPError):
        def __str__(self) -> str:
            return f"ReadTimeout for url https://api.telegram.org/bot{token}/getUpdates"

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.get.side_effect = _HttpLeak(
            "timeout"
        )
        with pytest.raises(TelegramRuntimeError) as exc:
            get_updates(cfg, offset=1)
    msg = getattr(exc.value, "message", str(exc.value))
    assert token not in msg
    assert token not in "".join(traceback.format_exception(exc.value))

def test_post_query_empty_refuses(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with pytest.raises(TelegramRefused):
        post_query(cfg, query="   ")


def test_post_query_success(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"answer": "from local", "model_used": "qwen"}

    with patch("telegram.client.httpx.Client") as client_cls:
        client = client_cls.return_value
        client.post.return_value = mock_resp
        data = post_query(cfg, query="what is CyClaw?")
        # Must never auto-confirm hybrid.
        kwargs = client.post.call_args
        payload = kwargs.kwargs.get("json") or kwargs[1].get("json")
        assert payload["user_confirmed_online"] is False
    assert data["answer"] == "from local"


def test_post_query_uses_proxy_disabled_loopback_client(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"answer": "local"}

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.post.return_value = mock_resp
        post_query(cfg, query="stay local")
    client_cls.assert_called_once_with(trust_env=False)


def test_post_query_transport_error_redacts_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    key = "cyclaw-local-api-key-secret"
    monkeypatch.setenv("CYCLAW_API_KEY", key)
    cfg = _cfg(tmp_path)

    class _HeaderLeak(httpx.HTTPError):
        def __str__(self) -> str:
            return f"request failed Authorization: Bearer {key}; body=local only"

    with (
        patch("telegram.client.httpx.Client") as client_cls,
        patch("telegram.client.audit_log") as audit,
    ):
        client_cls.return_value.post.side_effect = _HeaderLeak("boom")
        with pytest.raises(TelegramRuntimeError) as exc:
            post_query(cfg, query="local only")
    message = getattr(exc.value, "message", str(exc.value))
    assert key not in message
    rendered = "".join(traceback.format_exception(exc.value))
    assert key not in rendered
    assert "body=local only" not in rendered
    event = audit.call_args.args[0]
    assert event["ok"] is False
    assert event["http_status"] is None
    assert event["error_type"] == "_HeaderLeak"
    assert "local only" not in str(event)


def test_post_query_audit_receives_hash_not_plaintext(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    query = "unique telegram plaintext that must not be logged"
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"answer": "local"}

    with (
        patch("telegram.client.httpx.Client") as client_cls,
        patch("telegram.client.audit_log") as audit,
    ):
        client_cls.return_value.post.return_value = mock_resp
        post_query(cfg, query=query)
    event = audit.call_args.args[0]
    assert "query" not in event
    assert event["query_hash"] == hashlib.sha256(query.encode()).hexdigest()
    assert query not in str(event)


@pytest.mark.parametrize(
    ("status", "retryable", "fatal"),
    [
        (201, False, True),
        (302, False, True),
        (400, False, False),
        (401, False, True),
        (404, False, True),
        (503, True, False),
    ],
)
def test_post_query_classifies_http_failures(
    tmp_path: Path, status: int, retryable: bool, fatal: bool
) -> None:
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = status
    mock_resp.json.return_value = {"error": "request failed"}

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.post.return_value = mock_resp
        with pytest.raises(TelegramRuntimeError) as exc:
            post_query(cfg, query="classify me")
    assert (exc.value.details or {}).get("retryable") is retryable
    assert (exc.value.details or {}).get("fatal") is fatal


@pytest.mark.parametrize("payload", [None, ["not", "an", "object"]])
def test_post_query_rejects_invalid_200_payload_and_audits_failure(
    tmp_path: Path, payload: object
) -> None:
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    if payload is None:
        mock_resp.json.side_effect = ValueError("not json")
    else:
        mock_resp.json.return_value = payload

    with (
        patch("telegram.client.httpx.Client") as client_cls,
        patch("telegram.client.audit_log") as audit,
    ):
        client_cls.return_value.post.return_value = mock_resp
        with pytest.raises(TelegramRuntimeError):
            post_query(cfg, query="invalid response")
    event = audit.call_args.args[0]
    assert event["ok"] is False
    assert event["error_type"] in {"non_json", "non_object_json"}


def test_get_updates_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "ok": True,
        "result": [{"update_id": 1, "message": {"chat": {"id": 42}, "text": "hi"}}],
    }
    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.get.return_value = mock_resp
        updates = get_updates(cfg, offset=None)
    assert len(updates) == 1
    assert updates[0]["update_id"] == 1


@pytest.mark.parametrize("result", [None, {}, "not-a-list"])
def test_get_updates_rejects_missing_or_non_list_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, result: object
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"ok": True, "result": result}

    with patch("telegram.client.httpx.Client") as client_cls:
        client_cls.return_value.get.return_value = mock_resp
        with pytest.raises(TelegramRuntimeError) as exc:
            get_updates(cfg)
    assert (exc.value.details or {}).get("retryable") is True


def test_get_updates_retries_429_after_server_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.json.return_value = {
        "ok": False,
        "error_code": 429,
        "parameters": {"retry_after": 2},
    }
    success = MagicMock()
    success.status_code = 200
    success.json.return_value = {"ok": True, "result": []}

    with (
        patch("telegram.client.httpx.Client") as client_cls,
        patch("telegram.client.time.sleep") as sleep,
    ):
        client_cls.return_value.get.side_effect = [rate_limited, success]
        updates = get_updates(cfg, offset=4)
    assert updates == []
    assert client_cls.return_value.get.call_count == 2
    sleep.assert_called_once_with(2.0)
