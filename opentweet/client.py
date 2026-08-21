"""HTTP clients for CyClaw POST /query and the OpenTweet REST API.

stdlib + httpx only. Never imports gate/graph/mcp. ``user_confirmed_online``
is hardcoded false. Secrets are never written to audit payloads.
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from opentweet.config import OpenTweetConfig
from utils.errors import OpenTweetRuntimeError
from utils.logger import audit_log, hash_query

_loopback_http_client: httpx.Client | None = None
_opentweet_http_client: httpx.Client | None = None
_OT_TIMEOUT_SEC = 30.0


def _get_loopback_http_client() -> httpx.Client:
    global _loopback_http_client
    if _loopback_http_client is None:
        _loopback_http_client = httpx.Client(trust_env=False)
    return _loopback_http_client


def _get_opentweet_http_client() -> httpx.Client:
    global _opentweet_http_client
    if _opentweet_http_client is None:
        _opentweet_http_client = httpx.Client(trust_env=False)
    return _opentweet_http_client


def reset_http_client_for_tests() -> None:
    """Drop pooled transport (unit tests only)."""
    global _loopback_http_client, _opentweet_http_client
    if _loopback_http_client is not None:
        _loopback_http_client.close()
        _loopback_http_client = None
    if _opentweet_http_client is not None:
        _opentweet_http_client.close()
        _opentweet_http_client = None


def _transport_error_message(label: str, exc: httpx.HTTPError) -> str:
    return f"{label} transport error: {type(exc).__name__}"


def post_query(cfg: OpenTweetConfig, query: str) -> dict[str, Any]:
    """Call existing CyClaw ``POST /query`` over loopback. Never confirms online."""
    url = f"{cfg.query.base_url}/query"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = cfg.resolve_query_api_key()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    payload = {"query": query, "user_confirmed_online": False}
    started = time.monotonic()
    try:
        resp = _get_loopback_http_client().post(
            url, json=payload, headers=headers, timeout=float(cfg.query.timeout_sec)
        )
    except httpx.HTTPError as exc:
        audit_log(
            {
                "event": "opentweet_query",
                "channel": "opentweet",
                "ok": False,
                "http_status": None,
                "latency_ms": int((time.monotonic() - started) * 1000),
                "query_hash": hash_query(query),
                "query_len": len(query),
                "error_type": type(exc).__name__,
            },
            config_path=cfg._config_path,
        )
        raise OpenTweetRuntimeError(
            _transport_error_message("CyClaw /query", exc),
            details={"method": "POST /query", "retryable": True},
        ) from None

    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        data = resp.json()
    except ValueError:
        data = None
    event = {
        "event": "opentweet_query",
        "channel": "opentweet",
        "ok": resp.status_code == 200 and isinstance(data, dict),
        "http_status": resp.status_code,
        "latency_ms": latency_ms,
        "query_hash": hash_query(query),
        "query_len": len(query),
        "answer_model": data.get("model_used") if isinstance(data, dict) else None,
        "user_confirmed_online": False,
    }
    audit_log(event, config_path=cfg._config_path)
    if resp.status_code != 200:
        raise OpenTweetRuntimeError(
            f"CyClaw /query returned HTTP {resp.status_code}",
            details={"method": "POST /query", "status": resp.status_code, "retryable": True},
        )
    if not isinstance(data, dict):
        raise OpenTweetRuntimeError(
            "CyClaw /query returned non-object JSON",
            details={"method": "POST /query", "retryable": True},
        )
    return data


def get_me(cfg: OpenTweetConfig) -> dict[str, Any]:
    """``GET /api/v1/me`` — auth + subscription/limits. Never logs the key."""
    url = f"{cfg.api_base}/api/v1/me"
    headers = {"Authorization": f"Bearer {cfg.resolve_api_key()}"}
    try:
        resp = _get_opentweet_http_client().get(url, headers=headers, timeout=_OT_TIMEOUT_SEC)
    except httpx.HTTPError as exc:
        raise OpenTweetRuntimeError(
            _transport_error_message("OpenTweet /me", exc),
            details={"method": "GET /api/v1/me", "retryable": True},
        ) from None
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code != 200 or not isinstance(data, dict):
        raise OpenTweetRuntimeError(
            f"OpenTweet /me returned HTTP {resp.status_code}",
            details={"method": "GET /api/v1/me", "status": resp.status_code},
        )
    return data


def create_post(
    cfg: OpenTweetConfig,
    text: str,
    *,
    scheduled_date: str | None = None,
) -> dict[str, Any]:
    """``POST /api/v1/posts``. Omit ``scheduled_date`` to save a draft.

    Never sends ``publish_now``.
    """
    url = f"{cfg.api_base}/api/v1/posts"
    headers = {
        "Authorization": f"Bearer {cfg.resolve_api_key()}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {"text": text}
    if scheduled_date is not None:
        payload["scheduled_date"] = scheduled_date
    try:
        resp = _get_opentweet_http_client().post(
            url, json=payload, headers=headers, timeout=_OT_TIMEOUT_SEC
        )
    except httpx.HTTPError as exc:
        raise OpenTweetRuntimeError(
            _transport_error_message("OpenTweet /posts", exc),
            details={"method": "POST /api/v1/posts", "retryable": True},
        ) from None
    try:
        data = resp.json()
    except ValueError:
        data = None
    if resp.status_code not in (200, 201) or not isinstance(data, dict):
        raise OpenTweetRuntimeError(
            f"OpenTweet /posts returned HTTP {resp.status_code}",
            details={"method": "POST /api/v1/posts", "status": resp.status_code},
        )
    return data
