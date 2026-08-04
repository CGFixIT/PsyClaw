"""HTTP clients for Telegram Bot API and CyClaw POST /query.

stdlib + httpx only. Never imports gate/graph/mcp. Secrets are read from the
environment via TelegramConfig helpers and never written to audit payloads
(only hashes / redacted fields).
"""

from __future__ import annotations

import hashlib
import time
from typing import Any, cast

import httpx

from telegram.config import TelegramConfig
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import audit_log

# Pooled module-level client (same pattern as llm/client.py's persistent
# clients): a forever-poll loop otherwise pays a fresh TCP+TLS handshake per
# Bot API call. Lazily created; every call still passes its own per-request
# timeout, so per-call timeout semantics are unchanged.
_http_client: httpx.Client | None = None

# A Telegram-controlled retry_after must not stall the local bridge forever.
# One retry is enough to honor normal Bot API flood control without turning a
# persistent 429 into an unbounded loop; the wait itself is capped at one minute.
_TELEGRAM_429_MAX_RETRIES: int = 1
_TELEGRAM_RETRY_AFTER_CAP_SEC: float = 60.0


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client()
    return _http_client


def reset_http_client_for_tests() -> None:
    """Drop the pooled client (unit tests only — it can hold a patched mock)."""
    global _http_client
    if _http_client is not None:
        _http_client.close()
        _http_client = None


def _hash_token_fingerprint(token: str) -> str:
    """Short non-reversible fingerprint for audit (never the token itself)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def bot_api_url(cfg: TelegramConfig, method: str, token: str) -> str:
    # Token is path-embedded per Telegram Bot API; callers must not log the URL.
    return f"{cfg.api_base}/bot{token}/{method}"


def chunk_text(text: str, max_chars: int) -> list[str]:
    """Split text into pieces that fit Telegram/config max length.

    Prefer paragraph (\\n\\n) then line (\\n) boundaries; hard-split only as last resort.
    """
    body = (text or "").strip()
    if not body:
        return []
    if max_chars <= 0:
        raise ValueError("max_chars must be > 0")
    if len(body) <= max_chars:
        return [body]

    chunks: list[str] = []
    remaining = body
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        window = remaining[:max_chars]
        split_at = window.rfind("\n\n")
        if split_at < max_chars // 4:
            split_at = window.rfind("\n")
        if split_at < max_chars // 4:
            split_at = max_chars
        piece = remaining[:split_at].rstrip()
        if not piece:
            piece = remaining[:max_chars]
            split_at = max_chars
        chunks.append(piece)
        remaining = remaining[split_at:].lstrip()
    return chunks


def _transport_error_message(method: str, exc: BaseException, *secrets: str) -> str:
    """Build a transport error string that never echoes Bot API secrets.

    httpx embeds the full request URL in many HTTPError messages. Telegram puts
    the bot token in the URL path (``/bot<token>/method``), so ``str(exc)`` can
    leak the live token into CLI output, poll error dicts, and logs. Scrub every
    known secret substring; fall back to exception type only if scrubbing fails.
    """
    raw = f"{type(exc).__name__}: {exc}"
    for secret in secrets:
        if secret and secret in raw:
            raw = raw.replace(secret, "<redacted>")
    return f"Telegram {method} transport error: {raw}"


def _send_one(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    body: str,
    tok: str,
    disable_notification: bool,
) -> dict[str, Any]:
    """POST one sendMessage body (already sized). Returns Bot API JSON."""
    url = bot_api_url(cfg, "sendMessage", tok)
    payload = {
        "chat_id": int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id,
        "text": body,
        "disable_notification": bool(disable_notification),
    }
    started = time.monotonic()
    for attempt in range(_TELEGRAM_429_MAX_RETRIES + 1):
        try:
            resp = _get_http_client().post(url, json=payload, timeout=30.0)
        except httpx.HTTPError as exc:
            raise TelegramRuntimeError(
                _transport_error_message("sendMessage", exc, tok),
                details={"method": "sendMessage"},
            ) from exc

        try:
            raw_data = resp.json()
        except ValueError as exc:
            raise TelegramRuntimeError(
                f"Telegram sendMessage returned non-JSON (HTTP {resp.status_code})",
                details={"status": resp.status_code},
            ) from exc
        if not isinstance(raw_data, dict):
            raise TelegramRuntimeError(
                f"Telegram sendMessage returned non-object JSON (HTTP {resp.status_code})",
                details={"status": resp.status_code},
            )
        data = cast(dict[str, Any], raw_data)

        parameters = data.get("parameters")
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if (
            resp.status_code == 429
            and attempt < _TELEGRAM_429_MAX_RETRIES
            and isinstance(retry_after, (int, float))
            and not isinstance(retry_after, bool)
            and retry_after >= 0
        ):
            time.sleep(min(float(retry_after), _TELEGRAM_RETRY_AFTER_CAP_SEC))
            continue
        break

    latency_ms = int((time.monotonic() - started) * 1000)
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


def send_message(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    text: str,
    token: str | None = None,
    disable_notification: bool = False,
) -> dict[str, Any]:
    """Send a text message via Bot API ``sendMessage``.

    Long text is split on paragraph/line boundaries (see ``chunk_text``) and
    sent as sequential messages. Returns the last Bot API response.

    Raises TelegramRefused on allowlist miss; TelegramRuntimeError on HTTP/API failure.
    """
    if not cfg.is_chat_allowed(chat_id):
        raise TelegramRefused(
            "chat_id not in telegram.allowed_chat_ids",
            details={"chat_id": str(chat_id), "gate": "allowlist"},
        )
    pieces = chunk_text(text, cfg.max_message_chars)
    if not pieces:
        raise TelegramRefused("message text is empty", details={"gate": "empty_text"})

    tok = token if token is not None else cfg.resolve_bot_token()
    last: dict[str, Any] = {}
    for piece in pieces:
        last = _send_one(
            cfg,
            chat_id=chat_id,
            body=piece,
            tok=tok,
            disable_notification=disable_notification,
        )
    return last


def fetch_loopback_health(cfg: TelegramConfig, *, timeout_sec: float = 5.0) -> str:
    """GET CyClaw ``/health`` over loopback only. No graph imports."""
    url = f"{cfg.query.base_url}/health"
    try:
        resp = _get_http_client().get(url, timeout=timeout_sec)
    except httpx.HTTPError as exc:
        return f"health unreachable: {type(exc).__name__}"
    try:
        data = resp.json()
    except ValueError:
        return f"health HTTP {resp.status_code} (non-JSON)"
    if not isinstance(data, dict):
        return f"health HTTP {resp.status_code}"
    status = data.get("status", "?")
    mode = data.get("mode", "?")
    index_ready = data.get("index_ready")
    graph_ready = data.get("graph_ready")
    return (
        f"status={status} mode={mode} "
        f"index_ready={index_ready} graph_ready={graph_ready} "
        f"http={resp.status_code}"
    )


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
    for attempt in range(_TELEGRAM_429_MAX_RETRIES + 1):
        try:
            resp = _get_http_client().get(url, params=params, timeout=timeout)
        except httpx.HTTPError as exc:
            raise TelegramRuntimeError(
                _transport_error_message("getUpdates", exc, tok),
                details={"method": "getUpdates"},
            ) from exc
        try:
            raw_data = resp.json()
        except ValueError as exc:
            raise TelegramRuntimeError(
                f"Telegram getUpdates returned non-JSON (HTTP {resp.status_code})",
                details={"status": resp.status_code},
            ) from exc
        if not isinstance(raw_data, dict):
            raise TelegramRuntimeError(
                f"Telegram getUpdates returned non-object JSON (HTTP {resp.status_code})",
                details={"status": resp.status_code},
            )
        data = cast(dict[str, Any], raw_data)

        parameters = data.get("parameters")
        retry_after = parameters.get("retry_after") if isinstance(parameters, dict) else None
        if (
            resp.status_code == 429
            and attempt < _TELEGRAM_429_MAX_RETRIES
            and isinstance(retry_after, (int, float))
            and not isinstance(retry_after, bool)
            and retry_after >= 0
        ):
            time.sleep(min(float(retry_after), _TELEGRAM_RETRY_AFTER_CAP_SEC))
            continue
        break

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
        resp = _get_http_client().post(
            url, json=payload, headers=headers, timeout=float(cfg.query.timeout_sec)
        )
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
