"""Telegram channel runners — T1 notify + T2 chat handling.

Phase map (see docs/channels/TELEGRAM_DESIGN.md):
  T1  send_notify          — IMPLEMENTED (this module)
  T2  handle_inbound_text  — IMPLEMENTED (long-poll loop in poll_once / poll_forever)
  T3  hybrid confirm UX    — NOT IMPLEMENTED (allow_hybrid_confirm reserved)
  T4  media / fsconnect    — NOT IMPLEMENTED

No graph imports. All CyClaw answers go through HTTP POST /query.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from telegram import client as tg_client
from telegram.config import TelegramConfig
from telegram.ratelimit import get_limiter
from telegram.state import load_offset, save_offset
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import audit_log, hash_query

# Reserved commands (no /online until T3). BotFather may append @BotName.
_CMD_RE = re.compile(r"^/(help|status|id)(?:@\S+)?(?:\s|$)", re.IGNORECASE)

_HELP_TEXT = (
    "CyClaw Telegram channel\n"
    "/help — this text\n"
    "/status — loopback CyClaw /health\n"
    "/id — your chat id\n"
    "Anything else → local RAG via POST /query (mode=chat).\n"
    "Hybrid /online confirm is not available yet (T3)."
)

_POLL_RETRY_SLEEP_CAP_SEC = 60.0


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
    return tg_client.send_message(cfg, chat_id=chat_id, text=text)


def _extract_answer(query_response: dict[str, Any]) -> str:
    """Answer text from CyClaw /query JSON (schemas.api.QueryResponse.answer first)."""
    # Live gate returns QueryResponse: answer + model_used (+ sources, …).
    val = query_response.get("answer")
    if isinstance(val, str) and val.strip():
        return val.strip()
    # Defensive aliases if a proxy rewrites the body.
    for key in ("response", "text", "output"):
        alt = query_response.get(key)
        if isinstance(alt, str) and alt.strip():
            return alt.strip()
    for nest in ("data", "result"):
        inner = query_response.get(nest)
        if isinstance(inner, dict):
            nested = inner.get("answer")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    err = query_response.get("error")
    if isinstance(err, str) and err.strip():
        return f"(error) {err.strip()}"
    return "(no answer field in /query response)"


def _dispatch_command(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    text: str,
) -> str | None:
    """Return reply text for reserved commands, or None if not a command."""
    m = _CMD_RE.match(text.strip())
    if not m:
        return None
    cmd = m.group(1).lower()
    if cmd == "help":
        return _HELP_TEXT
    if cmd == "id":
        return f"chat_id={chat_id}"
    if cmd == "status":
        return tg_client.fetch_loopback_health(cfg)
    return None


def handle_inbound_text(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    text: str,
    update_id: int | None = None,
) -> dict[str, Any]:
    """T2: allowlisted inbound text → command or POST /query → reply via Bot API."""
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
            # Telegram text never reaches audit_log in plaintext, regardless
            # of the global include_query_hash opt-out.
            "query_hash": hash_query(text),
            "query_len": len(text),
            "mode": cfg.mode,
        },
        config_path=cfg._config_path,
    )

    cmd_reply = _dispatch_command(cfg, chat_id=chat_id, text=text)
    if cmd_reply is not None:
        outbound = tg_client.send_message(cfg, chat_id=chat_id, text=cmd_reply)
        return {"command": True, "outbound": outbound, "answer": cmd_reply}

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
                handle_inbound_text(
                    cfg,
                    chat_id=chat_id,
                    text=text,
                    update_id=uid if isinstance(uid, int) else None,
                )
            )
        except TelegramRefused as exc:
            # Allowlist/mode/input refusals are terminal, so ack them. A spent
            # rate-limit window is temporary: leave that update unacked and stop
            # the batch so poll_forever can back off before Telegram redelivers it.
            details = getattr(exc, "details", None) or {}
            retryable = details.get("gate") == "rate_limit"
            error = {
                "error": getattr(exc, "message", str(exc)),
                "code": getattr(exc, "code", "TELEGRAM_ERROR"),
                "chat_id": str(chat_id),
            }
            if retryable:
                error["retryable"] = True
                if details.get("retry_after") is not None:
                    error["retry_after"] = details["retry_after"]
            handled.append(error)
            if retryable:
                break
        except TelegramRuntimeError as exc:
            details = getattr(exc, "details", None) or {}
            if details.get("fatal") is True:
                # API key/endpoint failures affect the whole bridge, not one
                # message. Stop without acknowledging the current update.
                raise
            # Unknown legacy errors remain retryable; clients explicitly mark
            # permanent 4xx responses false.
            retryable = details.get("retryable") is not False
            error = {
                "error": getattr(exc, "message", str(exc)),
                "code": getattr(exc, "code", "TELEGRAM_ERROR"),
                "chat_id": str(chat_id),
                "retryable": retryable,
            }
            if details.get("retry_after") is not None:
                error["retry_after"] = details["retry_after"]
            handled.append(error)
            if retryable:
                # Leave this update unacknowledged and stop the batch so it and
                # every later update are redelivered after transient recovery.
                break
            # A permanent 4xx is a poison update. Acknowledge it below so one
            # rejected message cannot wedge every later update indefinitely.
        if isinstance(uid, int):
            next_offset = uid + 1
    return next_offset, handled


def poll_forever(
    cfg: TelegramConfig,
    *,
    max_iterations: int | None = None,
    sleep_on_error_sec: float = 2.0,
    offset_path: Path | None = None,
) -> int:
    """Blocking long-poll loop. Returns number of batches processed.

    Loads/saves getUpdates offset from ``offset_path`` (default
    ``data/telegram/offset.json``). Only persists when the offset advances
    (respects no-ack-on-TelegramRuntimeError from poll_once).

    ``max_iterations`` is for tests; production leaves it None.
    """
    if not cfg.enabled:
        raise TelegramRefused("telegram.enabled is false", details={"gate": "enabled"})
    if cfg.mode != "chat":
        raise TelegramRefused(
            "poll requires telegram.mode: chat",
            details={"gate": "mode", "mode": cfg.mode},
        )

    offset: int | None = load_offset(offset_path)
    batches = 0
    while max_iterations is None or batches < max_iterations:
        try:
            new_offset, handled = poll_once(cfg, offset=offset)
            if new_offset is not None and new_offset != offset:
                try:
                    save_offset(new_offset, offset_path)
                except OSError:
                    # ponytail: keep polling even if disk write fails; memory offset still advances.
                    pass
                offset = new_offset
            retryable = [result for result in handled if result.get("retryable") is True]
            if retryable:
                delay = sleep_on_error_sec
                for result in retryable:
                    retry_after = result.get("retry_after")
                    if (
                        isinstance(retry_after, (int, float))
                        and not isinstance(retry_after, bool)
                        and retry_after >= 0
                    ):
                        delay = max(
                            delay,
                            min(float(retry_after), _POLL_RETRY_SLEEP_CAP_SEC),
                        )
                time.sleep(delay)
            batches += 1
        except TelegramRuntimeError as exc:
            details = getattr(exc, "details", None) or {}
            if details.get("retryable") is False:
                raise
            delay = sleep_on_error_sec
            retry_after = details.get("retry_after")
            if (
                isinstance(retry_after, (int, float))
                and not isinstance(retry_after, bool)
                and retry_after >= 0
            ):
                delay = max(
                    delay,
                    min(float(retry_after), _POLL_RETRY_SLEEP_CAP_SEC),
                )
            time.sleep(delay)
            batches += 1
    return batches
