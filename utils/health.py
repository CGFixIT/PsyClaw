"""Health checks for external dependencies.

Checks LM Studio, and optionally Grok and/or Claude when their respective
mode==hybrid + <provider>.enabled gates are on. No Ollama — embeddings are
local sentence-transformers.
"""

import os
import re
import threading
import time
from pathlib import Path

import httpx
import yaml

from .errors import HealthStatus

_cfg_cache: dict[str, tuple[dict, float]] = {}
_cfg_ttl_sec = 60
_status_cache: dict[str, tuple[tuple[HealthStatus, ...], float]] = {}
_status_ttl_sec = 2

# Shared pooled HTTP client for all probes. Constructing a fresh httpx.Client
# per request (what module-level httpx.get() does under the hood) costs ~48 ms
# on this hot path — it rebuilds the SSL context and re-reads the CA bundle
# from disk even for plain-http loopback URLs — versus ~0.5 ms when the client
# (and its connection pool) is reused. /health awaits check_all() on every
# call, so that per-probe overhead was the endpoint's entire latency budget.
_http_client: httpx.Client | None = None
_http_client_lock = threading.Lock()


def _http_get(url: str, *, timeout: float, headers: dict | None = None) -> httpx.Response:
    """GET through the shared client (lazily created, thread-safe)."""
    global _http_client
    if _http_client is None:
        with _http_client_lock:
            if _http_client is None:
                _http_client = httpx.Client(timeout=timeout)
    return _http_client.get(url, timeout=timeout, headers=headers)
# Anchor relative config_path lookups to the repo root, mirroring gate.py's
# _BASE_DIR pattern — see utils/logger.py's _REPO_ROOT for the matching fix.
# gate.py calls check_all() with no config_path (line 539), so the bare
# "config.yaml" default must not depend on the process CWD.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _health_cfg(config_path: str) -> dict:
    """Parse config with 60-second TTL to balance performance and hot-reload responsiveness.

    If config.yaml is edited post-startup, the cache expires within 60 seconds,
    allowing operators to toggle settings (e.g. Grok) without restarting.
    """
    now = time.monotonic()
    if config_path in _cfg_cache:
        cached_cfg, cached_at = _cfg_cache[config_path]
        if now - cached_at < _cfg_ttl_sec:
            return cached_cfg
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    with open(path.resolve(), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    _cfg_cache[config_path] = (cfg, now)
    return cfg


def _safe_error(exc: Exception) -> str:
    return re.sub(r"https?://\S+", "[URL REDACTED]", str(exc))


def check_all(config_path: str = "config.yaml") -> list[HealthStatus]:
    now = time.monotonic()
    if config_path in _status_cache:
        cached_statuses, cached_at = _status_cache[config_path]
        if now - cached_at < _status_ttl_sec:
            return list(cached_statuses)

    try:
        cfg = _health_cfg(config_path)
        llm_base = cfg["models"]["local_llm"]["base_url"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        return [HealthStatus(name="config", healthy=False, error=f"config load failed: {_safe_error(exc)}")]

    results = []
    results.append(_ping(f"{llm_base}/models", "lm_studio"))
    if (cfg["app"]["mode"] == "hybrid" and
            cfg["models"]["grok"].get("enabled", False)):
        grok_base = cfg["models"]["grok"]["base_url"]
        # xAI's /v1/models is an authenticated endpoint: without a Bearer token it
        # returns 401, which made grok_api report *unhealthy* on every probe even
        # when the API was fully up — masking real outages. Send the same key the
        # GrokClient uses. With no key configured (the default offline posture),
        # report a clear "key not set" state instead of a misleading 401/network
        # error, and skip the doomed request entirely.
        api_key = os.environ.get("GROK_API_KEY", "")
        if api_key:
            results.append(_ping(
                f"{grok_base}/models", "grok_api",
                headers={"Authorization": f"Bearer {api_key}"},
                expect_model=cfg["models"]["grok"].get("model", ""),
            ))
        else:
            results.append(HealthStatus(
                name="grok_api", healthy=False,
                error="GROK_API_KEY not set (hybrid mode enabled but no API key)",
            ))
    if (cfg["app"]["mode"] == "hybrid" and
            cfg["models"].get("claude", {}).get("enabled", False)):
        claude_cfg = cfg["models"]["claude"]
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        if api_key:
            results.append(_ping(
                f"{claude_cfg['base_url'].rstrip('/')}/models", "claude_api",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": claude_cfg.get("anthropic_version", "2023-06-01"),
                },
                expect_model=claude_cfg.get("model", ""),
            ))
        else:
            results.append(HealthStatus(
                name="claude_api", healthy=False,
                error="ANTHROPIC_API_KEY not set (hybrid mode enabled but no API key)",
            ))
    results.append(HealthStatus(name="embeddings_local", healthy=True, latency_ms=0.0))
    _status_cache[config_path] = (tuple(results), time.monotonic())
    return results

def _ping(url: str, name: str, headers: dict | None = None,
          expect_model: str | None = None) -> HealthStatus:
    try:
        start = time.monotonic()
        resp = _http_get(url, timeout=5.0, headers=headers or {})
        latency = (time.monotonic() - start) * 1000
        resp.raise_for_status()
    except Exception as e:
        # Redact the URL (which may contain credentials or internal hostnames)
        # from the exception message before surfacing it in the public /health response.
        return HealthStatus(name=name, healthy=False, error=_safe_error(e))
    # Model-pin drift guard for OpenAI-style /models endpoints: a retired or
    # renamed pin (xAI retired grok-beta and grok-4) otherwise surfaces only as
    # a runtime HTTP 4xx on the first live fallback — after the user already
    # confirmed the escalation. Checked only when the endpoint is up and the
    # body parses to the documented shape; an unparseable/odd body never fails
    # an otherwise-healthy availability probe.
    if expect_model:
        try:
            listed = {m.get("id") for m in resp.json().get("data", []) if isinstance(m, dict)}
        except (ValueError, AttributeError):
            listed = set()
        if listed and expect_model not in listed:
            return HealthStatus(
                name=name, healthy=False,
                error=f"configured model '{expect_model}' not in provider /models list",
            )
    return HealthStatus(name=name, healthy=True, latency_ms=round(latency, 1))
