"""Telegram channel runners — T1 notify + T2 chat handling.

Phase map (see docs/channels/TELEGRAM_DESIGN.md):
  T1  send_notify          — IMPLEMENTED (this module)
  T2  handle_inbound_text  — IMPLEMENTED (long-poll loop in poll_once / poll_forever)
  T3  hybrid confirm UX    — NOT IMPLEMENTED (allow_hybrid_confirm reserved)
  T4  media / fsconnect    — NOT IMPLEMENTED

No graph imports. All CyClaw answers go through HTTP POST /query.
"""

from __future__ import annotations

import time
from typing import Any

from telegram import client as tg_client
from telegram.config import TelegramConfig
from telegram.ratelimit import get_limiter
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import audit_log


def send_notify(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    text: str,
) -> dict[str, Any]:
    """T1: outbound notify. Works in mode=notify and mode=chat."""
    if not cfg.enabled:
        raise TelegramRefused(
            "telegram.enabled is false",
            details={"gate": "enabled"},
        )
    get_limiter(cfg.rate_limit.max_ops, cfg.rate_limit.window_seconds).check("tg:outbound")
    return tg_client.send_message(cfg, chat_id=chat_id, text=text)


def _extract_answer(query_response: dict[str, Any]) -> str:
    """Best-effort answer text from CyClaw /query response shapes."""
    for key in ("answer", "response", "text", "output"):
        val = query_response.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    # Some responses nest under data/result.
    for nest in ("data", "result"):
        inner = query_response.get(nest)
        if isinstance(inner, dict):
            for key in ("answer", "response", "text"):
                val = inner.get(key)
                if isinstance(val, str) and val.strip():
                    return val.strip()
    return "(no answer field in /query response)"


def handle_inbound_text(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    text: str,
    update_id: int | None = None,
) -> dict[str, Any]:
    """T2: allowlisted inbound text → POST /query → reply via Bot API."""
    if not cfg.enabled:
        raise TelegramRefused("telegram.enabled is false", details={"gate": "enabled"})
    if cfg.mode != "chat":
        raise TelegramRefused(
            "inbound chat requires telegram.mode: chat",
            details={"gate": "mode", "mode": cfg.mode},
        )
    if not cfg.is_chat_allowed(chat_id):
        audit_log(
            {
                "event": "telegram_inbound_refused",
                "channel": "telegram",
                "reason": "allowlist",
                "chat_id": str(chat_id),
                "update_id": update_id,
            },
            config_path=cfg._config_path,
        )
        raise TelegramRefused(
            "chat_id not in telegram.allowed_chat_ids",
            details={"chat_id": str(chat_id), "gate": "allowlist"},
        )

    get_limiter(cfg.rate_limit.max_ops, cfg.rate_limit.window_seconds).check(
        f"tg:inbound:{chat_id}"
    )

    audit_log(
        {
            "event": "telegram_inbound",
            "channel": "telegram",
            "chat_id": str(chat_id),
            "update_id": update_id,
            "query": text,
            "mode": cfg.mode,
        },
        config_path=cfg._config_path,
    )

    result = tg_client.post_query(cfg, query=text)
    answer = _extract_answer(result)
    # Prefix model tag when present so the phone UI shows offline vs local.
    model = result.get("model_used") or result.get("answer_model")
    if model:
        answer = f"{answer}\n\n— {model}"

    outbound = tg_client.send_message(cfg, chat_id=chat_id, text=answer)
    return {"query_response": result, "outbound": outbound, "answer": answer}


def _message_from_update(update: dict[str, Any]) -> tuple[int | str, str] | None:
    """Return (chat_id, text) for a text message update, else None."""
    msg = update.get("message") or update.get("edited_message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = msg.get("text")
    if chat_id is None or not isinstance(text, str) or not text.strip():
        return None
    return chat_id, text.strip()


def poll_once(
    cfg: TelegramConfig,
    *,
    offset: int | None = None,
) -> tuple[int | None, list[dict[str, Any]]]:
    """Fetch one getUpdates batch and handle text messages. Returns (next_offset, results)."""
    if not cfg.enabled:
        raise TelegramRefused("telegram.enabled is false", details={"gate": "enabled"})
    if cfg.mode != "chat":
        raise TelegramRefused(
            "poll requires telegram.mode: chat",
            details={"gate": "mode", "mode": cfg.mode},
        )

    updates = tg_client.get_updates(cfg, offset=offset)
    next_offset = offset
    handled: list[dict[str, Any]] = []
    for upd in updates:
        if not isinstance(upd, dict):
            continue
        uid = upd.get("update_id")
        parsed = _message_from_update(upd)
        if parsed is None:
            # Non-text updates (edits, stickers, …): ack so the stream advances.
            if isinstance(uid, int):
                next_offset = uid + 1
            continue
        chat_id, text = parsed
        try:
            handled.append(
                handle_inbound_text(cfg, chat_id=chat_id, text=text, update_id=uid if isinstance(uid, int) else None)
            )
        except TelegramRefused as exc:
            # Terminal policy refusal (allowlist/mode/rate-limit): ack so we do
            # not re-process forever. Transport/query failures must NOT ack.
            handled.append(
                {
                    "error": getattr(exc, "message", str(exc)),
                    "code": getattr(exc, "code", "TELEGRAM_ERROR"),
                    "chat_id": str(chat_id),
                }
            )
        except TelegramRuntimeError as exc:
            handled.append(
                {
                    "error": getattr(exc, "message", str(exc)),
                    "code": getattr(exc, "code", "TELEGRAM_ERROR"),
                    "chat_id": str(chat_id),
                }
            )
            # Leave next_offset unchanged for this update_id so Telegram redelivers
            # after a transient /query or sendMessage failure.
            continue
        if isinstance(uid, int):
            next_offset = uid + 1
    return next_offset, handled


def poll_forever(
    cfg: TelegramConfig,
    *,
    max_iterations: int | None = None,
    sleep_on_error_sec: float = 2.0,
) -> int:
    """Blocking long-poll loop. Returns number of batches processed.

    ``max_iterations`` is for tests; production leaves it None.
    """
    if not cfg.enabled:
        raise TelegramRefused("telegram.enabled is false", details={"gate": "enabled"})
    if cfg.mode != "chat":
        raise TelegramRefused(
            "poll requires telegram.mode: chat",
            details={"gate": "mode", "mode": cfg.mode},
        )

    offset: int | None = None
    batches = 0
    while max_iterations is None or batches < max_iterations:
        try:
            offset, _ = poll_once(cfg, offset=offset)
            batches += 1
        except TelegramRuntimeError:
            time.sleep(sleep_on_error_sec)
            batches += 1
    return batches
