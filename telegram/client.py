"""HTTP clients for Telegram Bot API and CyClaw POST /query.

stdlib + httpx only. Never imports gate/graph/mcp. Secrets are read from the
environment via TelegramConfig helpers and never written to audit payloads
(only hashes / redacted fields).
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from typing import Any, cast

import httpx

from telegram.config import TelegramConfig
from telegram.ratelimit import RateLimitReservation, get_limiter
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import audit_log, hash_query

# Pooled module-level client (same pattern as llm/client.py's persistent
# clients): a forever-poll loop otherwise pays a fresh TCP+TLS handshake per
# Bot API call. Lazily created; every call still passes its own per-request
# timeout, so per-call timeout semantics are unchanged.
_http_client: httpx.Client | None = None
_loopback_http_client: httpx.Client | None = None

# A Telegram-controlled retry_after must not stall the local bridge forever.
# One retry is enough to honor normal Bot API flood control without turning a
# persistent 429 into an unbounded loop; the wait itself is capped at one minute.
_TELEGRAM_429_MAX_RETRIES: int = 1
_TELEGRAM_RETRY_AFTER_CAP_SEC: float = 60.0
_TELEGRAM_RETRY_AFTER_MAX_SEC: float = 86_400.0

# Telegram may ask for a delay longer than this process should synchronously
# sleep. Keep the full server deadline in memory and refuse locally until it
# expires; this bounds each sleep without issuing a knowingly premature retry.
_RETRY_NOT_BEFORE: dict[str, float] = {}
_RETRY_LOCK = threading.Lock()


def _get_http_client() -> httpx.Client:
    global _http_client
    if _http_client is None:
        _http_client = httpx.Client()
    return _http_client


def _get_loopback_http_client() -> httpx.Client:
    """Client for CyClaw loopback calls that never honors ambient proxies."""
    global _loopback_http_client
    if _loopback_http_client is None:
        _loopback_http_client = httpx.Client(trust_env=False)
    return _loopback_http_client


def reset_http_client_for_tests() -> None:
    """Drop pooled transport/cooldown state (unit tests only)."""
    global _http_client, _loopback_http_client
    if _http_client is not None:
        _http_client.close()
        _http_client = None
    if _loopback_http_client is not None:
        _loopback_http_client.close()
        _loopback_http_client = None
    with _RETRY_LOCK:
        _RETRY_NOT_BEFORE.clear()


def _hash_token_fingerprint(token: str) -> str:
    """Short non-reversible fingerprint for audit (never the token itself)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]


def _handle_retry_after(
    data: dict[str, Any],
    token: str,
    attempt: int,
    method: str,
    max_retries: int,
) -> tuple[bool, float | None]:
    """Record Telegram's deadline and perform at most one bounded retry."""
    parameters = data.get("parameters")
    value = parameters.get("retry_after") if isinstance(parameters, dict) else None
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return False, None
    try:
        delay = float(value)
    except (OverflowError, ValueError):
        return False, None
    if not math.isfinite(delay):
        return False, None
    if delay > _TELEGRAM_RETRY_AFTER_MAX_SEC:
        # Do not turn attacker- or proxy-shaped metadata into an effectively
        # permanent process-local denial of service, and do not retry early.
        raise TelegramRuntimeError(
            f"Telegram {method} returned an unsafe retry_after",
            details={
                "method": method,
                "status": 429,
                "retryable": False,
                "fatal": True,
            },
        )
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    deadline = time.monotonic() + delay
    with _RETRY_LOCK:
        _RETRY_NOT_BEFORE[key] = max(_RETRY_NOT_BEFORE.get(key, 0.0), deadline)
    if attempt >= max_retries or delay > _TELEGRAM_RETRY_AFTER_CAP_SEC:
        return False, delay

    time.sleep(delay)
    with _RETRY_LOCK:
        # Do not clear a later deadline installed concurrently by another 429.
        if _RETRY_NOT_BEFORE.get(key, 0.0) <= deadline:
            _RETRY_NOT_BEFORE.pop(key, None)
    return True, delay


def _raise_during_retry_cooldown(token: str, method: str) -> None:
    key = hashlib.sha256(token.encode("utf-8")).hexdigest()
    now = time.monotonic()
    with _RETRY_LOCK:
        deadline = _RETRY_NOT_BEFORE.get(key, 0.0)
        if deadline <= now:
            _RETRY_NOT_BEFORE.pop(key, None)
            return
    raise TelegramRuntimeError(
        f"Telegram {method} deferred by Bot API flood control",
        details={
            "method": method,
            "status": 429,
            "retry_after": max(1, math.ceil(deadline - now)),
            "retryable": True,
        },
    )


def _http_status_is_retryable(status: int) -> bool:
    """Standard retry split: transient statuses retry; other 4xx are terminal."""
    return status in {408, 409, 425, 429} or status >= 500


def _bot_api_response_is_retryable(status: int) -> bool:
    """Also retry a malformed Bot API success response as a protocol glitch."""
    return 200 <= status < 300 or _http_status_is_retryable(status)


def _bot_api_response_is_fatal(status: int) -> bool:
    """Stop the bridge for credential/endpoint failures shared by all updates."""
    return status in {401, 404} or 300 <= status < 400


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


def _transport_error_message(label: str, exc: BaseException) -> str:
    """Build a transport error string containing only a safe exception type.

    httpx embeds the full request URL in many HTTPError messages. Telegram puts
    the bot token in the URL path, and unusual exceptions may also echo headers
    or request bodies. Do not copy arbitrary exception text across this boundary.
    """
    return f"{label} transport error: {type(exc).__name__}"


def _audit_outbound_attempt(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    body: str,
    token: str,
    started: float,
    status: int | None,
    ok: bool,
    error_type: str | None = None,
) -> None:
    """Record one wire attempt without message text or secret material."""
    event: dict[str, Any] = {
        "event": "telegram_outbound",
        "channel": "telegram",
        "method": "sendMessage",
        "ok": ok,
        "chat_id": str(chat_id),
        "text_len": len(body),
        "latency_ms": int((time.monotonic() - started) * 1000),
        "http_status": status,
        "token_fp": _hash_token_fingerprint(token),
        "mode": cfg.mode,
    }
    if error_type:
        event["error_type"] = error_type
    audit_log(event, config_path=cfg._config_path)


def _send_one(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    body: str,
    tok: str,
    disable_notification: bool,
    reservation: RateLimitReservation,
    max_retries: int,
) -> tuple[dict[str, Any], int]:
    """POST one sized body; return Bot API JSON and consumed attempt count."""
    url = bot_api_url(cfg, "sendMessage", tok)
    payload = {
        "chat_id": int(chat_id) if str(chat_id).lstrip("-").isdigit() else chat_id,
        "text": body,
        "disable_notification": bool(disable_notification),
    }
    for attempt in range(max_retries + 1):
        _raise_during_retry_cooldown(tok, "sendMessage")
        # The batch holds all possible attempt slots before its first POST.
        # Charge this one now so the event timestamp matches the wire attempt.
        reservation.consume()
        started = time.monotonic()
        try:
            resp = _get_http_client().post(url, json=payload, timeout=30.0)
        except httpx.HTTPError as exc:
            _audit_outbound_attempt(
                cfg,
                chat_id=chat_id,
                body=body,
                token=tok,
                started=started,
                status=None,
                ok=False,
                error_type=type(exc).__name__,
            )
            raise TelegramRuntimeError(
                _transport_error_message("Telegram sendMessage", exc),
                details={"method": "sendMessage", "retryable": True},
            ) from None

        try:
            raw_data = resp.json()
        except ValueError:
            _audit_outbound_attempt(
                cfg,
                chat_id=chat_id,
                body=body,
                token=tok,
                started=started,
                status=resp.status_code,
                ok=False,
                error_type="non_json",
            )
            raise TelegramRuntimeError(
                f"Telegram sendMessage returned non-JSON (HTTP {resp.status_code})",
                details={
                    "method": "sendMessage",
                    "status": resp.status_code,
                    "retryable": _bot_api_response_is_retryable(resp.status_code),
                    "fatal": _bot_api_response_is_fatal(resp.status_code),
                },
            ) from None
        if not isinstance(raw_data, dict):
            _audit_outbound_attempt(
                cfg,
                chat_id=chat_id,
                body=body,
                token=tok,
                started=started,
                status=resp.status_code,
                ok=False,
                error_type="non_object_json",
            )
            raise TelegramRuntimeError(
                f"Telegram sendMessage returned non-object JSON (HTTP {resp.status_code})",
                details={
                    "method": "sendMessage",
                    "status": resp.status_code,
                    "retryable": _bot_api_response_is_retryable(resp.status_code),
                    "fatal": _bot_api_response_is_fatal(resp.status_code),
                },
            )
        data = cast(dict[str, Any], raw_data)
        result = data.get("result")
        message_id = result.get("message_id") if isinstance(result, dict) else None
        ok = (
            data.get("ok") is True
            and resp.status_code == 200
            and isinstance(message_id, int)
            and not isinstance(message_id, bool)
        )
        _audit_outbound_attempt(
            cfg,
            chat_id=chat_id,
            body=body,
            token=tok,
            started=started,
            status=resp.status_code,
            ok=ok,
        )
        if ok:
            return data, attempt + 1

        retry_after = None
        if resp.status_code == 429:
            should_retry, retry_after = _handle_retry_after(
                data, tok, attempt, "sendMessage", max_retries
            )
            if should_retry:
                continue
        raise TelegramRuntimeError(
            f"Telegram sendMessage failed (HTTP {resp.status_code})",
            details={
                "method": "sendMessage",
                "status": resp.status_code,
                "retry_after": retry_after,
                "retryable": _bot_api_response_is_retryable(resp.status_code),
                "fatal": _bot_api_response_is_fatal(resp.status_code),
            },
        )

    raise TelegramRuntimeError(
        "Telegram sendMessage retry loop exhausted",
        details={"method": "sendMessage", "retryable": True},
    )


def send_message(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    text: str,
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

    tok = cfg.resolve_bot_token()
    # Refuse before consuming local capacity when Telegram has already told
    # this bot to wait. Keep the check inside _send_one as a concurrency guard.
    _raise_during_retry_cooldown(tok, "sendMessage")
    limiter = get_limiter(cfg.rate_limit.max_ops, cfg.rate_limit.window_seconds)
    # Hold initial attempts plus as many bounded 429 retries as the configured
    # ceiling can support. If current traffic leaves room only for the initial
    # batch, send without automatic retries rather than stranding a prefix.
    retry_slots = min(
        len(pieces) * _TELEGRAM_429_MAX_RETRIES,
        max(0, cfg.rate_limit.max_ops - len(pieces)),
    )
    reservation, granted = limiter.reserve_up_to(
        "tg:outbound",
        minimum=len(pieces),
        maximum=len(pieces) + retry_slots,
    )
    retry_slots = granted - len(pieces)

    last: dict[str, Any] = {}
    try:
        for piece in pieces:
            last, attempts = _send_one(
                cfg,
                chat_id=chat_id,
                body=piece,
                tok=tok,
                disable_notification=disable_notification,
                reservation=reservation,
                max_retries=_TELEGRAM_429_MAX_RETRIES if retry_slots > 0 else 0,
            )
            retry_slots -= attempts - 1
        return last
    finally:
        reservation.close()


def fetch_loopback_health(cfg: TelegramConfig, *, timeout_sec: float = 5.0) -> str:
    """GET CyClaw ``/health`` over loopback only. No graph imports."""
    url = f"{cfg.query.base_url}/health"
    try:
        resp = _get_loopback_http_client().get(url, timeout=timeout_sec)
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
) -> list[dict[str, Any]]:
    """Long-poll ``getUpdates``. Used only in mode=chat (caller enforces)."""
    tok = cfg.resolve_bot_token()
    url = bot_api_url(cfg, "getUpdates", tok)
    params: dict[str, Any] = {"timeout": cfg.poll_timeout_sec}
    if offset is not None:
        params["offset"] = offset
    # httpx timeout must exceed Telegram long-poll timeout.
    timeout = httpx.Timeout(cfg.poll_timeout_sec + 15.0, connect=10.0)
    for attempt in range(_TELEGRAM_429_MAX_RETRIES + 1):
        _raise_during_retry_cooldown(tok, "getUpdates")
        try:
            resp = _get_http_client().get(url, params=params, timeout=timeout)
        except httpx.HTTPError as exc:
            raise TelegramRuntimeError(
                _transport_error_message("Telegram getUpdates", exc),
                details={"method": "getUpdates", "retryable": True},
            ) from None
        try:
            raw_data = resp.json()
        except ValueError:
            raise TelegramRuntimeError(
                f"Telegram getUpdates returned non-JSON (HTTP {resp.status_code})",
                details={
                    "method": "getUpdates",
                    "status": resp.status_code,
                    "retryable": _bot_api_response_is_retryable(resp.status_code),
                },
            ) from None
        if not isinstance(raw_data, dict):
            raise TelegramRuntimeError(
                f"Telegram getUpdates returned non-object JSON (HTTP {resp.status_code})",
                details={
                    "method": "getUpdates",
                    "status": resp.status_code,
                    "retryable": _bot_api_response_is_retryable(resp.status_code),
                },
            )
        data = cast(dict[str, Any], raw_data)

        if data.get("ok") is True and resp.status_code == 200:
            result = data.get("result")
            if not isinstance(result, list):
                raise TelegramRuntimeError(
                    "Telegram getUpdates result is not a list",
                    details={"method": "getUpdates", "retryable": True},
                )
            return result

        retry_after = None
        if resp.status_code == 429:
            should_retry, retry_after = _handle_retry_after(
                data,
                tok,
                attempt,
                "getUpdates",
                _TELEGRAM_429_MAX_RETRIES,
            )
            if should_retry:
                continue
        raise TelegramRuntimeError(
            f"Telegram getUpdates failed (HTTP {resp.status_code})",
            details={
                "method": "getUpdates",
                "status": resp.status_code,
                "retry_after": retry_after,
                "retryable": _bot_api_response_is_retryable(resp.status_code),
            },
        )

    raise TelegramRuntimeError(
        "Telegram getUpdates retry loop exhausted",
        details={"method": "getUpdates", "retryable": True},
    )


def post_query(
    cfg: TelegramConfig,
    *,
    query: str,
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
    key = cfg.resolve_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"

    payload = {
        "query": text,
        # Explicit: Telegram must never auto-confirm online / hybrid.
        "user_confirmed_online": False,
    }
    started = time.monotonic()
    try:
        resp = _get_loopback_http_client().post(
            url, json=payload, headers=headers, timeout=float(cfg.query.timeout_sec)
        )
    except httpx.HTTPError as exc:
        audit_log(
            {
                "event": "telegram_query",
                "channel": "telegram",
                "ok": False,
                "http_status": None,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "query_hash": hash_query(text),
                "query_len": len(text),
                "error_type": type(exc).__name__,
            },
            config_path=cfg._config_path,
        )
        raise TelegramRuntimeError(
            _transport_error_message("CyClaw /query", exc),
            details={"method": "POST /query", "retryable": True},
        ) from None

    latency_ms = int((time.monotonic() - started) * 1000)
    parse_error: str | None = None
    try:
        data = resp.json()
    except ValueError:
        data = None
        parse_error = "non_json"

    answer_model = None
    if isinstance(data, dict):
        answer_model = data.get("model_used") or data.get("answer_model")
    event = {
        "event": "telegram_query",
        "channel": "telegram",
        "ok": resp.status_code == 200 and isinstance(data, dict),
        "http_status": resp.status_code,
        "latency_ms": latency_ms,
        # Telegram text is never handed to the audit layer in plaintext,
        # even if the global audit hash toggle is intentionally disabled.
        "query_hash": hash_query(text),
        "query_len": len(text),
        "answer_model": answer_model,
    }
    if parse_error:
        event["error_type"] = parse_error
    elif not isinstance(data, dict):
        event["error_type"] = "non_object_json"
    audit_log(event, config_path=cfg._config_path)
    if resp.status_code != 200:
        raise TelegramRuntimeError(
            f"CyClaw /query returned HTTP {resp.status_code}",
            details={
                "method": "POST /query",
                "status": resp.status_code,
                "body_keys": list(data) if isinstance(data, dict) else [],
                "retryable": _http_status_is_retryable(resp.status_code),
                "fatal": (
                    resp.status_code in {401, 403, 404}
                    or 200 <= resp.status_code < 400
                ),
            },
        )
    if parse_error:
        raise TelegramRuntimeError(
            "CyClaw /query returned non-JSON",
            details={"method": "POST /query", "retryable": True},
        )
    if not isinstance(data, dict):
        raise TelegramRuntimeError(
            "CyClaw /query returned non-object JSON",
            details={"method": "POST /query", "retryable": True},
        )
    return data
