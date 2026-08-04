"""Unit tests for telegram.runner and telegram.ratelimit."""

from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import MagicMock, patch

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


def test_unauthorized_inbound_never_calls_cyclaw_or_telegram(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 1,
            "message": {"chat": {"id": 999}, "text": "do not forward"},
        }
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch("telegram.client.post_query") as query,
        patch("telegram.client.send_message") as send,
    ):
        next_offset, handled = poll_once(cfg)
    assert next_offset == 2
    assert handled[0]["error"]
    query.assert_not_called()
    send.assert_not_called()


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
    assert out["answer"] == "pong"
    send.assert_called_once()


def test_handle_inbound_forwards_query_response_answer_verbatim(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    terminal_answer = "  exact terminal answer\nwith a deliberate final newline\n"
    with (
        patch("telegram.client.post_query", return_value={"answer": terminal_answer}),
        patch("telegram.client.send_message", return_value={"ok": True}) as send,
    ):
        out = handle_inbound_text(cfg, chat_id=42, text="query", update_id=9)
    assert out["answer"] == terminal_answer
    assert send.call_args.kwargs["text"] == terminal_answer


def test_handle_inbound_audit_receives_hash_not_plaintext(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    text = "private telegram text that must never enter audit plaintext"
    with (
        patch("telegram.runner.audit_log") as audit,
        patch("telegram.client.post_query", return_value={"answer": "pong"}),
        patch(
            "telegram.client.send_message",
            return_value={"ok": True, "result": {"message_id": 1}},
        ),
    ):
        handle_inbound_text(cfg, chat_id=42, text=text, update_id=9)
    event = audit.call_args.args[0]
    assert "query" not in event
    assert event["query_hash"] == hashlib.sha256(text.encode()).hexdigest()
    assert text not in str(event)


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


def test_online_command_stays_local_when_t3_master_switch_is_disabled(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, allow_hybrid_confirm=False)
    with (
        patch("telegram.runner.grant_hybrid_confirm") as grant,
        patch("telegram.client.post_query") as query,
        patch("telegram.client.send_message", return_value={"ok": True}) as send,
    ):
        out = handle_inbound_text(cfg, chat_id=42, text="/online on grok", update_id=10)
    grant.assert_not_called()
    query.assert_not_called()
    send.assert_called_once()
    assert "disabled" in out["answer"].lower()


def test_online_command_grants_only_an_explicit_provider(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, allow_hybrid_confirm=True, hybrid_confirm_ttl_sec=120)
    with (
        patch("telegram.runner.grant_hybrid_confirm", return_value=1120) as grant,
        patch("telegram.runner.audit_log") as audit,
        patch("telegram.client.send_message", return_value={"ok": True}),
    ):
        out = handle_inbound_text(cfg, chat_id=42, text="/online on claude", update_id=10)
    grant.assert_called_once_with(42, provider="claude", ttl_sec=120)
    assert out.get("command") is True
    events = [call.args[0] for call in audit.call_args_list]
    assert any(event.get("event") == "telegram_hybrid_confirm_granted" for event in events)


def test_online_command_does_not_silently_choose_a_provider(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, allow_hybrid_confirm=True)
    with (
        patch("telegram.runner.grant_hybrid_confirm") as grant,
        patch("telegram.client.post_query") as query,
        patch("telegram.client.send_message", return_value={"ok": True}),
    ):
        out = handle_inbound_text(cfg, chat_id=42, text="/online on", update_id=10)
    grant.assert_not_called()
    query.assert_not_called()
    assert "Usage:" in out["answer"]


def test_claimed_hybrid_confirmation_is_forwarded_once_then_consumed(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, allow_hybrid_confirm=True)
    with (
        patch("telegram.runner.claim_hybrid_confirm", side_effect=["grok", None]) as claim,
        patch("telegram.runner.audit_log") as audit,
        patch(
            "telegram.client.post_query",
            side_effect=[{"answer": "external"}, {"answer": "offline"}],
        ) as query,
        patch("telegram.client.send_message", return_value={"ok": True}),
    ):
        first = handle_inbound_text(cfg, chat_id=42, text="first", update_id=11)
        second = handle_inbound_text(cfg, chat_id=42, text="second", update_id=12)
    assert first["answer"] == "external"
    assert second["answer"] == "offline"
    assert claim.call_count == 2
    first_call = query.call_args_list[0].kwargs
    second_call = query.call_args_list[1].kwargs
    assert first_call["user_confirmed_online"] is True
    assert first_call["online_provider"] == "grok"
    assert second_call["user_confirmed_online"] is False
    assert second_call["online_provider"] is None
    events = [call.args[0] for call in audit.call_args_list]
    assert any(event.get("event") == "telegram_hybrid_confirm_consumed" for event in events)


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
    assert handled[0]["retryable"] is True


def test_poll_once_mixed_batch_redelivers_remainder_on_runtime_failure(tmp_path: Path) -> None:
    """A runtime failure mid-batch must not be acked by a later success in the same batch."""
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 10,
            "message": {"chat": {"id": 42}, "text": "first"},
        },
        {
            "update_id": 11,
            "message": {"chat": {"id": 42}, "text": "second"},
        },
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch(
            "telegram.runner.handle_inbound_text",
            side_effect=[
                TelegramRuntimeError("send failed", details={"method": "sendMessage"}),
                {"answer": "ok"},
            ],
        ) as handler,
    ):
        next_offset, handled = poll_once(cfg, offset=None)
    # Failed update_id 10 stays un-acked so Telegram redelivers it (and 11).
    assert next_offset is None
    # The second update was NOT processed this batch; it returns with the redelivery.
    assert handler.call_count == 1
    assert handled[0]["error"]


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


def test_poll_once_advances_offset_on_terminal_runtime_failure(tmp_path: Path) -> None:
    """Permanent 4xx failures must not poison the queue for every later update."""
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 11,
            "message": {"chat": {"id": 42}, "text": "bad request"},
        }
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch(
            "telegram.runner.handle_inbound_text",
            side_effect=TelegramRuntimeError(
                "CyClaw /query returned HTTP 400",
                details={"status": 400, "retryable": False},
            ),
        ),
    ):
        next_offset, handled = poll_once(cfg, offset=None)
    assert next_offset == 12
    assert handled[0]["retryable"] is False


@pytest.mark.parametrize("payload", [None, {"ok": True, "result": {}}])
def test_poll_once_keeps_offset_on_invalid_send_success_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, payload: object
) -> None:
    """A protocol-glitched reply must be redelivered rather than silently lost."""
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 11,
            "message": {"chat": {"id": 42}, "text": "hello"},
        }
    ]
    malformed = MagicMock()
    malformed.status_code = 200
    if payload is None:
        malformed.json.side_effect = ValueError("truncated response")
    else:
        malformed.json.return_value = payload
    http_client = MagicMock()
    http_client.post.return_value = malformed

    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch("telegram.client.post_query", return_value={"answer": "local"}),
        patch("telegram.client._get_http_client", return_value=http_client),
    ):
        next_offset, handled = poll_once(cfg, offset=None)

    assert next_offset is None
    assert handled[0]["retryable"] is True
    assert http_client.post.call_count == 1


def test_poll_once_surfaces_send_redirect_without_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok-abc")
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 11,
            "message": {"chat": {"id": 42}, "text": "hello"},
        }
    ]
    redirect = MagicMock()
    redirect.status_code = 302
    redirect.json.return_value = {"ok": False}
    http_client = MagicMock()
    http_client.post.return_value = redirect

    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch("telegram.client.post_query", return_value={"answer": "local"}),
        patch("telegram.client._get_http_client", return_value=http_client),
        pytest.raises(TelegramRuntimeError),
    ):
        poll_once(cfg, offset=None)

    assert http_client.post.call_count == 1


@pytest.mark.parametrize("status", [401, 403, 404])
def test_poll_once_surfaces_bridge_fatal_http_failure_without_ack(
    tmp_path: Path, status: int
) -> None:
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
            side_effect=TelegramRuntimeError(
                f"CyClaw /query returned HTTP {status}",
                details={"status": status, "retryable": False, "fatal": True},
            ),
        ),
    ):
        with pytest.raises(TelegramRuntimeError):
            poll_once(cfg, offset=None)


def test_poll_once_does_not_advance_offset_on_rate_limit_refusal(tmp_path: Path) -> None:
    """A spent inbound budget is temporary, so Telegram must redeliver the update."""
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 12,
            "message": {"chat": {"id": 42}, "text": "hello"},
        }
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch(
            "telegram.runner.handle_inbound_text",
            side_effect=TelegramRefused(
                "rate limited",
                details={"gate": "rate_limit", "retry_after": 12.5},
            ),
        ),
    ):
        next_offset, handled = poll_once(cfg, offset=None)
    assert next_offset is None
    assert handled[0]["retryable"] is True
    assert handled[0]["retry_after"] == 12.5


def test_poll_once_mixed_batch_stops_exactly_before_transient_failure(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    updates = [
        {"update_id": 20, "message": {"chat": {"id": 42}, "text": "first"}},
        {"update_id": 21, "message": {"chat": {"id": 42}, "text": "second"}},
        {"update_id": 22, "message": {"chat": {"id": 42}, "text": "third"}},
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch(
            "telegram.runner.handle_inbound_text",
            side_effect=[
                {"answer": "ok"},
                TelegramRuntimeError("temporary", details={"retryable": True}),
                {"answer": "must not run"},
            ],
        ) as handler,
    ):
        next_offset, handled = poll_once(cfg, offset=None)
    assert next_offset == 21
    assert handler.call_count == 2
    assert handled[-1]["retryable"] is True


def test_rate_limiter_trips() -> None:
    lim = SlidingWindowLimiter(max_ops=2, window_seconds=60)
    lim.check("k")
    lim.check("k")
    with pytest.raises(TelegramRefused) as exc:
        lim.check("k")
    assert (exc.value.details or {}).get("gate") == "rate_limit"
    retry_after = (exc.value.details or {}).get("retry_after")
    assert isinstance(retry_after, float)
    assert 0 < retry_after <= 60


def test_rate_limiter_releases_event_at_window_boundary() -> None:
    limiter = SlidingWindowLimiter(max_ops=1, window_seconds=60)
    with patch("telegram.ratelimit.time.monotonic", side_effect=[0.0, 60.0]):
        limiter.check("k")
        limiter.check("k")


def test_rate_limiter_reserves_batch_atomically() -> None:
    limiter = SlidingWindowLimiter(max_ops=3, window_seconds=60)
    limiter.check("k", cost=2)
    with pytest.raises(TelegramRefused) as exc:
        limiter.check("k", cost=2)
    assert (exc.value.details or {}).get("gate") == "rate_limit"
    # Failed cost=2 admission recorded nothing, leaving the final slot usable.
    limiter.check("k")

    with pytest.raises(TelegramRefused) as capacity:
        limiter.check("other", cost=4)
    assert (capacity.value.details or {}).get("gate") == "rate_limit_capacity"


def test_rate_limiter_holds_batch_capacity_and_releases_unused_slots() -> None:
    limiter = SlidingWindowLimiter(max_ops=2, window_seconds=60)
    reservation = limiter.reserve("k", cost=2)
    with pytest.raises(TelegramRefused):
        limiter.check("k")

    reservation.consume()
    reservation.close()
    limiter.check("k")
    with pytest.raises(TelegramRefused):
        limiter.check("k")


def test_rate_limiter_reserves_partial_optional_capacity() -> None:
    limiter = SlidingWindowLimiter(max_ops=5, window_seconds=60)
    limiter.check("k", cost=2)
    reservation, granted = limiter.reserve_up_to("k", minimum=2, maximum=4)
    assert granted == 3

    reservation.consume()
    reservation.consume()
    reservation.consume()
    reservation.close()
    with pytest.raises(TelegramRefused):
        limiter.check("k")


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


def test_poll_forever_backs_off_after_message_runtime_failure(tmp_path: Path) -> None:
    """A failed query/send must back off without acknowledging the Telegram update."""
    cfg = _cfg(tmp_path)
    offset_path = tmp_path / "offset.json"
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
            side_effect=TelegramRuntimeError(
                "send failed",
                details={"method": "sendMessage"},
            ),
        ),
        patch("telegram.runner.time.sleep") as sleep,
    ):
        n = poll_forever(
            cfg,
            max_iterations=1,
            sleep_on_error_sec=2.5,
            offset_path=offset_path,
        )
    assert n == 1
    assert load_offset(offset_path) is None
    sleep.assert_called_once_with(2.5)


def test_poll_forever_caps_sleep_while_honoring_local_429_cooldown(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with (
        patch(
            "telegram.runner.poll_once",
            side_effect=TelegramRuntimeError(
                "flood control",
                details={"status": 429, "retry_after": 999, "retryable": True},
            ),
        ),
        patch("telegram.runner.time.sleep") as sleep,
    ):
        assert poll_forever(cfg, max_iterations=1, sleep_on_error_sec=2.0) == 1
    sleep.assert_called_once_with(60.0)


def test_poll_forever_surfaces_terminal_get_updates_failure(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with patch(
        "telegram.runner.poll_once",
        side_effect=TelegramRuntimeError(
            "unauthorized",
            details={"status": 401, "retryable": False},
        ),
    ):
        with pytest.raises(TelegramRuntimeError):
            poll_forever(cfg, max_iterations=1)
