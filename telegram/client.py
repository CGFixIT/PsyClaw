"""HTTP clients for Telegram Bot API and CyClaw POST /query.

stdlib + httpx only. Never imports gate/graph/mcp. Secrets are read from the
environment via TelegramConfig helpers and never written to audit payloads
(only hashes / redacted fields).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any

import httpx

from telegram.config import TelegramConfig
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import audit_log


def _hash_token_fingerprint(token: str) -> str:
    """Short non-reversible fingerprint for audit (never the token itself)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def bot_api_url(cfg: TelegramConfig, method: str, token: str) -> str:
    # Token is path-embedded per Telegram Bot API; callers must not log the URL.
    return f"{cfg.api_base}/bot{token}/{method}"


def send_message(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    text: str,
    token: str | None = None,
    disable_notification: bool = False,
) -> dict[str, Any]:
    """Send a text message via Bot API ``sendMessage``.

    Raises TelegramRefused on allowlist miss; TelegramRuntimeError on HTTP/API failure.
    """
    if not cfg.is_chat_allowed(chat_id):
        raise TelegramRefused(
            "chat_id not in telegram.allowed_chat_ids",
            details={"chat_id": str(chat_id), "gate": "allowlist"},
        )
    body = (text or "").strip()
    if not body:
        raise TelegramRefused("message text is empty", details={"gate": "empty_text"})
    if len(body) > cfg.max_message_chars:
        body = body[: cfg.max_message_chars - 20] + "\n…[truncated]"

    tok = token if token is not None else cfg.resolve_bot_token()
    url = bot_api_url(cfg, "sendMessage", tok)
    payload = {
        "chat_id": int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id,
        "text": body,
        "disable_notification": bool(disable_notification),
    }
    started = time.monotonic()
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload)
    except httpx.HTTPError as exc:
        raise TelegramRuntimeError(
            f"Telegram sendMessage transport error: {exc}",
            details={"method": "sendMessage"},
        ) from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        data = resp.json()
    except ValueError as exc:
        raise TelegramRuntimeError(
            f"Telegram sendMessage returned non-JSON (HTTP {resp.status_code})",
            details={"status": resp.status_code},
        ) from exc

    ok = bool(data.get("ok")) and resp.status_code == 200
    audit_log(
        {
            "event": "telegram_outbound",
            "channel": "telegram",
            "ok": ok,
            "chat_id": str(chat_id),
            "text_len": len(body),
            "latency_ms": latency_ms,
            "http_status": resp.status_code,
            "token_fp": _hash_token_fingerprint(tok),
            "mode": cfg.mode,
        },
        config_path=cfg._config_path,
    )
    if not ok:
        raise TelegramRuntimeError(
            f"Telegram sendMessage failed: {data.get('description', resp.status_code)}",
            details={"status": resp.status_code, "description": data.get("description")},
        )
    return data


def get_updates(
    cfg: TelegramConfig,
    *,
    offset: int | None = None,
    token: str | None = None,
) -> list[dict[str, Any]]:
    """Long-poll ``getUpdates``. Used only in mode=chat (caller enforces)."""
    tok = token if token is not None else cfg.resolve_bot_token()
    url = bot_api_url(cfg, "getUpdates", tok)
    params: dict[str, Any] = {"timeout": cfg.poll_timeout_sec}
    if offset is not None:
        params["offset"] = offset
    # httpx timeout must exceed Telegram long-poll timeout.
    timeout = httpx.Timeout(cfg.poll_timeout_sec + 15.0, connect=10.0)
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise TelegramRuntimeError(
            f"Telegram getUpdates transport error: {exc}",
            details={"method": "getUpdates"},
        ) from exc
    try:
        data = resp.json()
    except ValueError as exc:
        raise TelegramRuntimeError(
            f"Telegram getUpdates returned non-JSON (HTTP {resp.status_code})",
            details={"status": resp.status_code},
        ) from exc
    if not data.get("ok"):
        raise TelegramRuntimeError(
            f"Telegram getUpdates failed: {data.get('description', resp.status_code)}",
            details={"status": resp.status_code},
        )
    result = data.get("result") or []
    if not isinstance(result, list):
        raise TelegramRuntimeError("Telegram getUpdates result is not a list")
    return result


def post_query(
    cfg: TelegramConfig,
    *,
    query: str,
    api_key: str | None = None,
) -> dict[str, Any]:
    """Call existing CyClaw ``POST /query`` over loopback. Never bypasses the graph.

    Does not set user_confirmed_online — hybrid remains triple-gated by the core.
    """
    text = (query or "").strip()
    if not text:
        raise TelegramRefused("query text is empty", details={"gate": "empty_query"})
    if len(text) > cfg.max_message_chars:
        raise TelegramRefused(
            f"query exceeds telegram.max_message_chars ({cfg.max_message_chars})",
            details={"gate": "max_message_chars", "len": len(text)},
        )

    url = f"{cfg.query.base_url}/query"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = api_key if api_key is not None else cfg.resolve_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload = {
        "query": text,
        # Explicit: Telegram must never auto-confirm online / hybrid.
        "user_confirmed_online": False,
    }
    started = time.monotonic()
    try:
        with httpx.Client(timeout=float(cfg.query.timeout_sec)) as client:
            resp = client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise TelegramRuntimeError(
            f"CyClaw /query transport error: {exc}",
            details={"url": url},
        ) from exc

    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        data = resp.json()
    except ValueError:
        data = {"raw": resp.text[:500]}

    audit_log(
        {
            "event": "telegram_query",
            "channel": "telegram",
            "ok": resp.status_code == 200,
            "http_status": resp.status_code,
            "latency_ms": latency_ms,
            "query": text,  # audit_log hashes when include_query_hash is true
            "answer_model": data.get("model_used") or data.get("answer_model"),
        },
        config_path=cfg._config_path,
    )
    if resp.status_code >= 400:
        raise TelegramRuntimeError(
            f"CyClaw /query returned HTTP {resp.status_code}",
            details={"status": resp.status_code, "body_keys": list(data) if isinstance(data, dict) else []},
        )
    if not isinstance(data, dict):
        raise TelegramRuntimeError("CyClaw /query returned non-object JSON")
    return data
