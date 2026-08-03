"""Unit tests for telegram.runner and telegram.ratelimit."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from telegram.config import load_telegram_config
from telegram.ratelimit import SlidingWindowLimiter, get_limiter, reset_limiters_for_tests
from telegram.runner import _extract_answer, handle_inbound_text, poll_forever, poll_once, send_notify
from telegram.state import load_offset, save_offset
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    reset_limiters_for_tests()
    yield
    reset_config_cache()
    reset_limiters_for_tests()


def _cfg(tmp_path: Path, **overrides: object):
    block = {
        "enabled": True,
        "mode": "chat",
        "allowed_chat_ids": ["42"],
        "rate_limit": {"max_ops": 3, "window_seconds": 60},
        "query": {"base_url": "http://127.0.0.1:8787"},
    }
    block.update(overrides)
    raw = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "telegram": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_telegram_config(str(path))


def test_send_notify_disabled(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, enabled=False, allowed_chat_ids=[])
    with pytest.raises(TelegramRefused) as exc:
        send_notify(cfg, chat_id=42, text="x")
    assert "enabled" in (exc.value.details or {}).get("gate", "")


def test_handle_inbound_requires_chat_mode(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, mode="notify")
    with pytest.raises(TelegramRefused) as exc:
        handle_inbound_text(cfg, chat_id=42, text="hi")
    assert (exc.value.details or {}).get("gate") == "mode"


def test_extract_answer_query_response_fixture() -> None:
    """Align with schemas.api.QueryResponse field names (gate live shape)."""
    body = {
        "answer": "From the vault: CyClaw is offline-first.",
        "sources": [
            {
                "source": "docs/x.md",
                "score": 0.05,
                "rrf_score": 0.05,
                "content_preview": "…",
            }
        ],
        "retrieval_mode": "hybrid",
        "hit_count": 1,
        "model_used": "qwen3.6:27b",
        "needs_confirm": False,
        "confirm_message": None,
        "available_providers": [],
        "error": None,
    }
    assert _extract_answer(body) == "From the vault: CyClaw is offline-first."


def test_handle_inbound_happy_path(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with (
        patch(
            "telegram.client.post_query",
            return_value={"answer": "pong", "model_used": "qwen3.6:27b"},
        ),
        patch(
            "telegram.client.send_message",
            return_value={"ok": True, "result": {"message_id": 1}},
        ) as send,
    ):
        out = handle_inbound_text(cfg, chat_id=42, text="ping", update_id=9)
    assert "pong" in out["answer"]
    assert "qwen3.6:27b" in out["answer"]
    send.assert_called_once()


def test_handle_inbound_help_command(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with (
        patch("telegram.client.post_query") as pq,
        patch(
            "telegram.client.send_message",
            return_value={"ok": True, "result": {"message_id": 2}},
        ) as send,
    ):
        out = handle_inbound_text(cfg, chat_id=42, text="/help")
    pq.assert_not_called()
    send.assert_called_once()
    assert out.get("command") is True
    assert "/status" in out["answer"]


def test_handle_inbound_id_command(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with patch(
        "telegram.client.send_message",
        return_value={"ok": True, "result": {"message_id": 3}},
    ):
        out = handle_inbound_text(cfg, chat_id=42, text="/id@MyBot")
    assert out["answer"] == "chat_id=42"


def test_handle_inbound_status_command(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with (
        patch(
            "telegram.client.fetch_loopback_health",
            return_value="status=ok mode=offline index_ready=True graph_ready=True http=200",
        ),
        patch(
            "telegram.client.send_message",
            return_value={"ok": True, "result": {"message_id": 4}},
        ),
    ):
        out = handle_inbound_text(cfg, chat_id=42, text="/status")
    assert "status=ok" in out["answer"]


def test_poll_once_handles_text_update(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 5,
            "message": {"chat": {"id": 42}, "text": "hello"},
        }
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch(
            "telegram.runner.handle_inbound_text",
            return_value={"answer": "ok"},
        ) as handler,
    ):
        next_offset, handled = poll_once(cfg, offset=None)
    assert next_offset == 6
    assert handled == [{"answer": "ok"}]
    handler.assert_called_once()


def test_poll_once_does_not_advance_offset_on_runtime_failure(tmp_path: Path) -> None:
    """A failed send/query must not ack the update (Telegram will not redeliver)."""
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 10,
            "message": {"chat": {"id": 42}, "text": "hello"},
        }
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch(
            "telegram.runner.handle_inbound_text",
            side_effect=TelegramRuntimeError("send failed", details={"method": "sendMessage"}),
        ),
    ):
        next_offset, handled = poll_once(cfg, offset=None)
    assert next_offset is None
    assert handled[0]["code"]
    assert "error" in handled[0]


def test_poll_once_advances_offset_on_terminal_refusal(tmp_path: Path) -> None:
    """Allowlist/mode refusals are terminal — ack so the stream does not stall."""
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 11,
            "message": {"chat": {"id": 42}, "text": "hello"},
        }
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch(
            "telegram.runner.handle_inbound_text",
            side_effect=TelegramRefused("not allowed", details={"gate": "allowlist"}),
        ),
    ):
        next_offset, handled = poll_once(cfg, offset=None)
    assert next_offset == 12
    assert handled[0]["error"]


def test_rate_limiter_trips() -> None:
    lim = SlidingWindowLimiter(max_ops=2, window_seconds=60)
    lim.check("k")
    lim.check("k")
    with pytest.raises(TelegramRefused) as exc:
        lim.check("k")
    assert (exc.value.details or {}).get("gate") == "rate_limit"


def test_get_limiter_shared() -> None:
    a = get_limiter(5, 60)
    b = get_limiter(5, 60)
    assert a is b


def test_offset_roundtrip(tmp_path: Path) -> None:
    path = tmp_path / "offset.json"
    assert load_offset(path) is None
    save_offset(99, path)
    assert load_offset(path) == 99
    # atomic: no leftover tmp files
    assert list(tmp_path.glob(".offset.json.*.tmp")) == []


def test_poll_forever_persists_offset(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    offset_path = tmp_path / "offset.json"
    updates = [
        {
            "update_id": 5,
            "message": {"chat": {"id": 42}, "text": "hello"},
        }
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch(
            "telegram.runner.handle_inbound_text",
            return_value={"answer": "ok"},
        ),
    ):
        n = poll_forever(cfg, max_iterations=1, offset_path=offset_path)
    assert n == 1
    assert load_offset(offset_path) == 6
