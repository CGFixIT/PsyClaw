"""Telegram channel runners — T1 notify, T2 chat, T3 consent, and T4 staging.

Phase map (see docs/channels/TELEGRAM_DESIGN.md):
  T1  send_notify          — IMPLEMENTED (this module)
  T2  handle_inbound_text  — IMPLEMENTED (long-poll loop in poll_once / poll_forever)
  T3  hybrid confirm UX    — explicit, single-use, TTL-bound command
  T4  media / fsconnect    — explicit private-chat staging only; no auto-index

No graph imports. All CyClaw answers go through HTTP POST /query.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any

from telegram import client as tg_client
from telegram.config import TelegramConfig
from telegram.media import MediaAttachment, attachment_from_message, save_confirmation, stage_attachment
from telegram.ratelimit import get_limiter
from telegram.state import claim_hybrid_confirm, grant_hybrid_confirm, load_offset, save_offset
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import audit_log, hash_query

# Reserved commands. BotFather may append @BotName.
_CMD_RE = re.compile(r"^/(help|status|id|online|save)(?:@\S+)?(?:\s|$)", re.IGNORECASE)
_ONLINE_CMD_RE = re.compile(r"^/online(?:@\S+)?(?:\s+(?P<args>.*))?$", re.IGNORECASE)

_HELP_TEXT = (
    "CyClaw Telegram channel\n"
    "/help — this text\n"
    "/status — loopback CyClaw /health\n"
    "/id — your chat id\n"
    "/online on <grok|claude> — one explicit external fallback for your next message\n"
    "Anything else → local RAG via POST /query (mode=chat).\n"
    "Media staging requires a private-chat photo/document captioned /save --confirm <reason>."
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
        return val
    # Defensive aliases if a proxy rewrites the body.
    for key in ("response", "text", "output"):
        alt = query_response.get(key)
        if isinstance(alt, str) and alt.strip():
            return alt
    for nest in ("data", "result"):
        inner = query_response.get(nest)
        if isinstance(inner, dict):
            nested = inner.get("answer")
            if isinstance(nested, str) and nested.strip():
                return nested
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
    if cmd == "online":
        return _online_command(cfg, chat_id=chat_id, text=text)
    if cmd == "save":
        return "Attach a photo or document with caption: /save --confirm <reason>"
    return None


def _online_command(cfg: TelegramConfig, *, chat_id: int | str, text: str) -> str:
    """Grant one short-lived, provider-specific T3 consent without a core import."""
    if not cfg.allow_hybrid_confirm:
        return "Hybrid confirmation is disabled by telegram.allow_hybrid_confirm."
    match = _ONLINE_CMD_RE.fullmatch(text.strip())
    args = match.group("args").split() if match and match.group("args") else []
    if len(args) != 2 or args[0].lower() != "on" or args[1].lower() not in {"grok", "claude"}:
        return "Usage: /online on <grok|claude> (one next message only)."
    provider = args[1].lower()
    confirm_until = grant_hybrid_confirm(
        chat_id,
        provider=provider,
        ttl_sec=cfg.hybrid_confirm_ttl_sec,
    )
    audit_log(
        {
            "event": "telegram_hybrid_confirm_granted",
            "channel": "telegram",
            "chat_id": str(chat_id),
            "provider": provider,
            "ttl_sec": cfg.hybrid_confirm_ttl_sec,
            "confirm_until": confirm_until,
        },
        config_path=cfg._config_path,
    )
    return (
        f"One {provider} confirmation is armed for your next non-command message "
        f"for up to {cfg.hybrid_confirm_ttl_sec} seconds."
    )


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

    online_provider = None
    if cfg.allow_hybrid_confirm:
        online_provider = claim_hybrid_confirm(chat_id)
    if online_provider is not None:
        audit_log(
            {
                "event": "telegram_hybrid_confirm_consumed",
                "channel": "telegram",
                "chat_id": str(chat_id),
                "update_id": update_id,
                "provider": online_provider,
                "query_hash": hash_query(text),
                "query_len": len(text),
            },
            config_path=cfg._config_path,
        )
    result = tg_client.post_query(
        cfg,
        query=text,
        user_confirmed_online=online_provider is not None,
        online_provider=online_provider,
    )
    answer = _extract_answer(result)
    outbound = tg_client.send_message(cfg, chat_id=chat_id, text=answer)
    return {"query_response": result, "outbound": outbound, "answer": answer}


def handle_inbound_media(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    chat_type: str,
    attachment: MediaAttachment,
    caption: object,
    update_id: int,
) -> dict[str, Any]:
    """T4: stage one explicitly confirmed private-chat attachment through fsconnect."""
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
    confirmation = save_confirmation(caption)
    caption_text = caption if isinstance(caption, str) else ""
    audit_log(
        {
            "event": "telegram_media_inbound",
            "channel": "telegram",
            "chat_id": str(chat_id),
            "update_id": update_id,
            "kind": attachment.kind,
            "declared_size": attachment.declared_size,
            "caption_hash": hash_query(caption_text) if caption_text else None,
            "caption_len": len(caption_text),
            "explicit_confirm": confirmation is not None,
        },
        config_path=cfg._config_path,
    )
    if confirmation is None:
        audit_log(
            {
                "event": "telegram_media_refused",
                "channel": "telegram",
                "chat_id": str(chat_id),
                "update_id": update_id,
                "kind": attachment.kind,
                "gate": "media_confirm",
            },
            config_path=cfg._config_path,
        )
        raise TelegramRefused(
            "media staging requires caption: /save --confirm <reason>",
            details={"gate": "media_confirm"},
        )
    staged = stage_attachment(
        cfg,
        chat_id=chat_id,
        chat_type=chat_type,
        update_id=update_id,
        attachment=attachment,
        confirmation=confirmation,
    )
    answer = "Attachment staged in the configured local fsconnect root."
    outbound = tg_client.send_message(cfg, chat_id=chat_id, text=answer)
    return {"media": staged, "outbound": outbound, "answer": answer}


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


def _media_from_update(
    update: dict[str, Any],
) -> tuple[int | str, str, MediaAttachment, object] | None:
    """Return an untrusted attachment descriptor for a new private/group message only."""
    msg = update.get("message")
    if not isinstance(msg, dict):
        return None
    chat = msg.get("chat")
    if not isinstance(chat, dict):
        return None
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    if chat_id is None or not isinstance(chat_type, str):
        return None
    attachment = attachment_from_message(msg)
    if attachment is None:
        return None
    return chat_id, chat_type, attachment, msg.get("caption")


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
        parsed_text = _message_from_update(upd)
        parsed_media = _media_from_update(upd) if parsed_text is None else None
        if parsed_text is None and parsed_media is None:
            # Non-text updates (edits, stickers, …): ack so the stream advances.
            if isinstance(uid, int):
                next_offset = uid + 1
            continue
        try:
            if parsed_text is not None:
                chat_id, text = parsed_text
                handled.append(
                    handle_inbound_text(
                        cfg,
                        chat_id=chat_id,
                        text=text,
                        update_id=uid if isinstance(uid, int) else None,
                    )
                )
            else:
                chat_id, chat_type, attachment, caption = parsed_media
                if isinstance(uid, bool) or not isinstance(uid, int):
                    raise TelegramRefused("Telegram update id is invalid", details={"gate": "update_id"})
                handled.append(
                    handle_inbound_media(
                        cfg,
                        chat_id=chat_id,
                        chat_type=chat_type,
                        attachment=attachment,
                        caption=caption,
                        update_id=uid,
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
