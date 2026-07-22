"""Local OpenAI-compatible LLM (Ollama / LM Studio), Grok, and Claude clients.

Local backends and Grok use the OpenAI-compatible /chat/completions endpoint.
Claude uses Anthropic's Messages API.

Transient failures (timeouts, transport/network errors, and retryable HTTP
statuses — 5xx and 429) are retried with bounded exponential backoff. Client
errors (other 4xx) and unexpected exceptions fail fast — retrying a 400/401
only wastes time and, for Grok, external credits. Retry is config-driven via a
``retry`` block under each model; when absent, ``max_retries`` defaults to 0,
preserving the original single-attempt behavior.

Optional local failover (``models.local_llm.fallback``): when enabled, a short
HTTP probe prefers the primary (Ollama) and, if unreachable, selects the
secondary (LM Studio). Selection is cached for the process; default is
fallback disabled so single-backend installs stay fail-closed.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import yaml

from utils.errors import ClaudeServiceError, GrokServiceError, LLMServiceError, RAGError

log = logging.getLogger(__name__)

_RETRYABLE_STATUS_FLOOR = 500
_RETRYABLE_EXTRA_STATUS = frozenset({429})
_DEFAULT_PROBE_TIMEOUT_SEC = 1.5
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_retryable_status(status: int) -> bool:
    """5xx server errors and 429 (rate limit) are transient; other 4xx are not."""
    return status >= _RETRYABLE_STATUS_FLOOR or status in _RETRYABLE_EXTRA_STATUS


def _extract_content(resp: httpx.Response) -> str:
    """Pull ``choices[0].message.content`` from a 200 response, failing clearly.

    A 2xx body that is not valid JSON or lacks the expected OpenAI-compatible
    shape (e.g. an upstream proxy returning an HTML error page, or an
    ``{"error": ...}`` payload) would otherwise surface as a bare ``KeyError``/
    ``JSONDecodeError`` with a cryptic message. Raising a descriptive
    ``ValueError`` here lets the caller's ``on_other`` map it to the project's
    typed error with an auditable message. The shape is deterministic, so this
    is not retried — a retry would re-fetch the same malformed body."""
    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as e:
        # Type-only message: exception text can echo body fragments / proxy HTML
        # that later lands in graph answers and HTTP 200 QueryResponse.error.
        raise ValueError(f"malformed LLM response ({type(e).__name__})") from e
    # A structurally-valid envelope can still carry no usable text: some
    # OpenAI-compatible backends return content=null (e.g. alongside a refusal or
    # tool-call) or an empty/whitespace string. Returning that verbatim would
    # surface a blank answer downstream; treat it as a malformed (non-retryable)
    # response so the caller maps it to a clear typed LLM/Grok error instead.
    if not isinstance(content, str) or not content.strip():
        raise ValueError("empty LLM response: content missing or blank")
    # A reply cut off at max_tokens is otherwise indistinguishable from a
    # complete one — the truncated text is still returned (partial answer beats
    # none), but the operator gets an audit-visible WARNING instead of silence.
    if data["choices"][0].get("finish_reason") == "length":
        log.warning("LLM response truncated at max_tokens (finish_reason=length)")
    return content


def _extract_claude_content(resp: httpx.Response) -> str:
    """Pull text blocks from an Anthropic Messages API response."""
    try:
        data = resp.json()
        parts = [
            block.get("text", "")
            for block in data["content"]
            if isinstance(block, dict) and block.get("type") == "text"
        ]
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        raise ValueError(f"malformed Claude response ({type(e).__name__})") from e
    content = "\n".join(p for p in parts if isinstance(p, str) and p.strip()).strip()
    if not content:
        raise ValueError("empty Claude response: no text content")
    # Same truncation visibility as _extract_content, in Anthropic's dialect.
    if data.get("stop_reason") == "max_tokens":
        log.warning("Claude response truncated at max_tokens (stop_reason=max_tokens)")
    return content


# Fallback ceiling on a single backoff sleep when ``backoff_max_sec`` is absent
# from config. Without a cap, ``backoff_base * 2**attempt`` grows unbounded, so a
# mis-tuned ``max_retries``/``backoff_base_sec`` could block the calling worker
# for minutes-to-hours on the final attempt.
_DEFAULT_BACKOFF_MAX_SEC = 30.0


def _read_retry(model_cfg: dict) -> tuple[int, float, float]:
    """Parse the optional ``retry`` block; default to no retries (backward compatible).

    Returns ``(max_retries, backoff_base_sec, backoff_max_sec)``. ``backoff_max_sec``
    caps each individual sleep so an aggressive base/retry combo cannot stall the
    worker; it defaults to ``_DEFAULT_BACKOFF_MAX_SEC`` when the key is absent.

    Negative values are clamped to their safe floor (0 for counts, 0.0 for durations).
    A negative ``max_retries`` would make ``range(max_retries + 1)`` empty, causing
    every call to fall through to the unreachable ``AssertionError``. A negative
    ``backoff_base`` would pass a negative value to ``time.sleep()``, raising
    ``ValueError`` on the first retry attempt.
    """
    retry_cfg = model_cfg.get("retry") or {}
    max_retries = max(0, int(retry_cfg.get("max_retries", 0)))
    backoff_base = max(0.0, float(retry_cfg.get("backoff_base_sec", 1.0)))
    backoff_max = max(0.0, float(retry_cfg.get("backoff_max_sec", _DEFAULT_BACKOFF_MAX_SEC)))
    return max_retries, backoff_base, backoff_max


def _post_with_retry(
    *,
    service: str,
    do_post: Callable[[], httpx.Response],
    max_retries: int,
    backoff_base: float,
    on_http: Callable[[httpx.HTTPStatusError], RAGError],
    on_timeout: Callable[[httpx.TimeoutException], RAGError],
    on_other: Callable[[Exception], RAGError],
    backoff_max: float = _DEFAULT_BACKOFF_MAX_SEC,
    retry_on_timeout: bool = True,
    extract_content: Callable[[httpx.Response], str] = _extract_content,
) -> str:
    """POST with bounded exponential-backoff retry on transient failures.

    Retries timeouts, transport/network errors, and retryable HTTP statuses
    (5xx, 429) up to ``max_retries`` extra attempts, sleeping
    ``min(backoff_base * 2**attempt, backoff_max)`` seconds between tries — the
    cap keeps a mis-tuned base/retries combo from blocking the worker for an
    unbounded time. Other 4xx statuses and any non-transport exception fail fast.
    ``max_retries == 0`` reproduces the original single-attempt behavior. The
    ``on_*`` callables map the terminal failure to the caller's typed error so
    messages/codes stay unchanged.

    ``retry_on_timeout=False`` makes a read timeout fail fast (no retry), while
    transport/5xx/429 still retry. This is the right policy for a local Ollama
    backend: a read timeout there means inference is *stalled* (the classic
    "0% processing"), so a retry just issues a second request that waits the full
    per-call ``timeout_sec`` again — and, because the gateway wraps the whole graph
    in a shorter ``api.graph_timeout_sec`` deadline, the extra attempt is already
    unreachable (the client returns 504 first) and only orphans a worker thread on
    a second stalled call. Cloud Grok keeps the default (transient network blips
    behind its short timeout are worth a retry).

    Each transient retry logs a WARNING and each terminal give-up / fail-fast a
    ERROR, tagged with ``service`` (e.g. ``ollama`` / ``grok``), the attempt
    count, and the failure category. Only network/HTTP metadata is logged —
    never the prompt or response body — so the breadcrumb is safe for the audit
    trail. This turns previously-silent retries (a 12-minute Ollama stall would
    leave no trace) into an observable signal.
    """
    def _delay(attempt: int) -> float:
        return min(backoff_base * (2 ** attempt), backoff_max)

    total = max_retries + 1
    for attempt in range(max_retries + 1):
        try:
            resp = do_post()
            resp.raise_for_status()
            return extract_content(resp)
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            if attempt < max_retries and _is_retryable_status(status):
                delay = _delay(attempt)
                log.warning(
                    "%s call HTTP %s (attempt %d/%d); retrying in %.1fs",
                    service, status, attempt + 1, total, delay,
                )
                time.sleep(delay)
                continue
            log.error("%s call failed: HTTP %s after %d attempt(s)", service, status, attempt + 1)
            raise on_http(e) from e
        except httpx.TimeoutException as e:
            if retry_on_timeout and attempt < max_retries:
                delay = _delay(attempt)
                log.warning(
                    "%s call timed out (attempt %d/%d); retrying in %.1fs",
                    service, attempt + 1, total, delay,
                )
                time.sleep(delay)
                continue
            log.error("%s call failed: timeout after %d attempt(s)", service, attempt + 1)
            raise on_timeout(e) from e
        except httpx.TransportError as e:
            # Connection/read/write/protocol errors below the HTTP layer.
            if attempt < max_retries:
                delay = _delay(attempt)
                log.warning(
                    "%s transport error %s (attempt %d/%d); retrying in %.1fs",
                    service, type(e).__name__, attempt + 1, total, delay,
                )
                time.sleep(delay)
                continue
            log.error(
                "%s call failed: transport error %s after %d attempt(s)",
                service, type(e).__name__, attempt + 1,
            )
            raise on_other(e) from e
        except Exception as e:
            # Non-transient (e.g. malformed response body); fail fast, no retry.
            # Log only the exception *type* — its message can echo response content.
            log.error("%s call failed: non-retryable %s", service, type(e).__name__)
            raise on_other(e) from e
    # The loop guarantees every iteration either returns or raises, so this line
    # is structurally unreachable. If it somehow fires, surface it as a typed
    # service error so it lands in structured logs rather than a bare exception.
    log.error("%s retry loop exited without return or raise — this is a bug", service)  # pragma: no cover
    raise LLMServiceError("retry loop exited without return/raise")  # pragma: no cover


# =============================================================================
# Local backend resolution (Ollama primary → optional LM Studio fallback)
# =============================================================================


@dataclass(frozen=True)
class ResolvedLocalBackend:
    """Active local OpenAI-compatible backend after optional failover probe."""

    provider: str
    base_url: str
    model: str
    source: str  # "primary" | "fallback"
    api_key: str = ""


# Process-level resolution cache so LocalLLMClient and /health share one probe.
_resolved_local_backends: dict[str, ResolvedLocalBackend] = {}


def reset_local_backend_cache() -> None:
    """Drop cached local-backend resolution (tests / config reload)."""
    _resolved_local_backends.clear()


def _provider_label(provider: str) -> str:
    labels = {
        "ollama": "Ollama",
        "lmstudio": "LM Studio",
        "openai_compatible": "local LLM",
    }
    return labels.get(provider, provider or "local LLM")


def _is_loopback_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in _LOOPBACK_HOSTS


def _probe_openai_models(
    base_url: str,
    *,
    timeout_sec: float,
    api_key: str = "",
) -> bool:
    """True if GET {base}/models returns 2xx within timeout (discovery only)."""
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.get(url, headers=headers)
            resp.raise_for_status()
            return True
    # Only network/HTTP failures mean "backend down". A programming or config
    # error (e.g. a malformed base_url raising InvalidURL) must propagate
    # instead of silently masquerading as an unreachable backend and steering
    # discovery to the fallback.
    except (httpx.HTTPError, OSError):
        return False


def _cache_key_for_local_llm(llm_cfg: dict) -> str:
    fb = llm_cfg.get("fallback") or {}
    return "|".join(
        [
            str(llm_cfg.get("base_url", "")),
            str(llm_cfg.get("model", "")),
            str(llm_cfg.get("provider", "ollama")),
            str(bool((fb or {}).get("enabled", False))),
            str((fb or {}).get("base_url", "")),
            str((fb or {}).get("model", "")),
            str((fb or {}).get("provider", "")),
            str((fb or {}).get("probe_timeout_sec", _DEFAULT_PROBE_TIMEOUT_SEC)),
        ]
    )


def resolve_local_backend(llm_cfg: dict, *, force: bool = False) -> ResolvedLocalBackend:
    """Pick primary or fallback local backend.

    When ``fallback.enabled`` is false (default), returns primary with **no**
    network probe — identical to pre-failover behaviour.

    When enabled: short-probe primary; on hard failure short-probe secondary.
    If both probes fail, still returns primary (gate boot must not die) and
    logs a warning — generate will surface the connection error as today.
    """
    key = _cache_key_for_local_llm(llm_cfg)
    if not force and key in _resolved_local_backends:
        return _resolved_local_backends[key]

    primary_url = str(llm_cfg.get("base_url") or "").strip()
    primary_model = str(llm_cfg.get("model") or "").strip()
    primary_provider = str(llm_cfg.get("provider") or "ollama").strip() or "ollama"
    primary_key = str(llm_cfg.get("api_key") or "").strip()
    # model may be empty on minimal test fixtures / availability-only probes;
    # generate still needs a real pin in production config.yaml.
    if not primary_url:
        raise LLMServiceError(
            "models.local_llm.base_url is required",
            details={"base_url": primary_url},
        )

    primary = ResolvedLocalBackend(
        provider=primary_provider,
        base_url=primary_url.rstrip("/"),
        model=primary_model,
        source="primary",
        api_key=primary_key,
    )

    fb = llm_cfg.get("fallback") or {}
    if not isinstance(fb, dict) or not fb.get("enabled", False):
        _resolved_local_backends[key] = primary
        return primary

    fb_url = str(fb.get("base_url") or "").strip()
    fb_model = str(fb.get("model") or "").strip()
    fb_provider = str(fb.get("provider") or "lmstudio").strip() or "lmstudio"
    probe_timeout = float(fb.get("probe_timeout_sec", _DEFAULT_PROBE_TIMEOUT_SEC))
    if probe_timeout <= 0:
        probe_timeout = _DEFAULT_PROBE_TIMEOUT_SEC

    if not fb_url or not fb_model:
        raise LLMServiceError(
            "models.local_llm.fallback requires base_url and model when enabled",
            details={"base_url": bool(fb_url), "model": bool(fb_model)},
        )
    if not _is_loopback_url(fb_url):
        raise LLMServiceError(
            "models.local_llm.fallback.base_url must be loopback (127.0.0.1 / localhost / ::1)",
            details={"hint": "non-loopback local servers must be set as primary base_url explicitly"},
        )
    if not _is_loopback_url(primary_url):
        # Primary already non-loopback is an operator choice; still allow fallback
        # only if secondary is loopback (validated above).
        pass

    if _probe_openai_models(primary_url, timeout_sec=probe_timeout, api_key=primary_key):
        log.info("local LLM backend: primary (%s) probe ok", primary_provider)
        _resolved_local_backends[key] = primary
        return primary

    secondary = ResolvedLocalBackend(
        provider=fb_provider,
        base_url=fb_url.rstrip("/"),
        model=fb_model,
        source="fallback",
        api_key=str(fb.get("api_key") or "").strip(),
    )
    if _probe_openai_models(secondary.base_url, timeout_sec=probe_timeout, api_key=secondary.api_key):
        log.warning(
            "local LLM backend: primary unreachable; using fallback (%s)",
            fb_provider,
        )
        try:
            from utils.logger import audit_log

            audit_log(
                {
                    "event": "local_llm_backend_selected",
                    "source": "fallback",
                    "provider": fb_provider,
                    "primary_provider": primary_provider,
                }
            )
        except Exception:
            log.debug("audit_log for local_llm_backend_selected failed", exc_info=True)
        _resolved_local_backends[key] = secondary
        return secondary

    log.warning(
        "local LLM backend: primary and fallback probes failed; using primary (%s)",
        primary_provider,
    )
    _resolved_local_backends[key] = primary
    return primary


class LocalLLMClient:
    def __init__(self, config_path: str = "config.yaml", cfg: dict | None = None):
        if cfg is None:
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
        llm_cfg = cfg["models"]["local_llm"]
        resolved = resolve_local_backend(llm_cfg)
        self.provider = resolved.provider
        self.base_url = resolved.base_url
        self.model = resolved.model
        self.backend_source = resolved.source  # "primary" | "fallback"
        self.max_tokens = llm_cfg["max_tokens"]
        self.temperature = llm_cfg["temperature"]
        self.timeout = llm_cfg["timeout_sec"]
        self.retry_max, self.retry_backoff, self.retry_backoff_max = _read_retry(llm_cfg)
        # Coerce to a stripped str so a bare YAML number (parsed as int) or an
        # accidental whitespace value can't produce a malformed header. Empty /
        # whitespace-only -> "" -> no Authorization header is sent (see generate).
        self.api_key = resolved.api_key or str(llm_cfg.get("api_key") or "").strip()
        self._client = httpx.Client(timeout=self.timeout)
        self._label = _provider_label(self.provider)

    def close(self) -> None:
        self._client.close()

    def generate(self, prompt: str) -> str:
        label = self._label

        def do_post() -> httpx.Response:
            headers = {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}
            return self._client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature
                },
            )

        return _post_with_retry(
            service=self.provider or "ollama",
            do_post=do_post,
            max_retries=self.retry_max,
            backoff_base=self.retry_backoff,
            backoff_max=self.retry_backoff_max,
            # A local read timeout = a stalled model; retrying just burns another
            # full timeout_sec on an orphaned thread (the graph deadline already
            # returned 504). Transport/5xx/429 still retry — those fail fast.
            retry_on_timeout=False,
            on_http=lambda e: LLMServiceError(
                f"{label} HTTP error: {e.response.status_code}",
                details={"status": e.response.status_code, "provider": self.provider},
            ),
            on_timeout=lambda e: LLMServiceError(
                f"{label} timeout",
                details={"timeout_sec": self.timeout, "provider": self.provider},
            ),
            # Type-only: str(e) can carry URLs, body fragments, or secrets that
            # graph embeds into HTTP 200 answers via _generate_or_error.
            on_other=lambda e: LLMServiceError(
                f"{label} error: {type(e).__name__}",
                details={"exc_type": type(e).__name__, "provider": self.provider},
            ),
        )

class GrokClient:
    def __init__(self, config_path: str = "config.yaml", cfg: dict | None = None):
        if cfg is None:
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
        grok_cfg = cfg["models"]["grok"]
        self.base_url = grok_cfg["base_url"]
        self.model = grok_cfg["model"]
        self.max_tokens = grok_cfg["max_tokens"]
        self.temperature = grok_cfg["temperature"]
        self.timeout = grok_cfg["timeout_sec"]
        self.retry_max, self.retry_backoff, self.retry_backoff_max = _read_retry(grok_cfg)
        # Strip so whitespace-only counts as missing and padded values don't leak into the auth header.
        self.api_key = (os.environ.get("GROK_API_KEY") or "").strip()
        self._client = httpx.Client(timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str) -> str:
        if not self.api_key:
            raise GrokServiceError("GROK_API_KEY not set",
                                    details={"required_env": "GROK_API_KEY"})

        def do_post() -> httpx.Response:
            return self._client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": self.max_tokens,
                    "temperature": self.temperature
                },
            )

        return _post_with_retry(
            service="grok",
            do_post=do_post,
            max_retries=self.retry_max,
            backoff_base=self.retry_backoff,
            backoff_max=self.retry_backoff_max,
            on_http=lambda e: GrokServiceError(
                f"Grok HTTP {e.response.status_code}",
                details={"status": e.response.status_code},
            ),
            on_timeout=lambda e: GrokServiceError(
                "Grok timeout", details={"timeout_sec": self.timeout}
            ),
            on_other=lambda e: GrokServiceError(
                f"Grok error: {type(e).__name__}",
                details={"exc_type": type(e).__name__},
            ),
        )


class ClaudeClient:
    def __init__(self, config_path: str = "config.yaml", cfg: dict | None = None):
        if cfg is None:
            with open(config_path, encoding="utf-8") as f:
                cfg = yaml.safe_load(f)
        claude_cfg = cfg["models"]["claude"]
        self.base_url = claude_cfg["base_url"].rstrip("/")
        self.model = claude_cfg["model"]
        self.max_tokens = claude_cfg["max_tokens"]
        self.timeout = claude_cfg["timeout_sec"]
        self.anthropic_version = claude_cfg.get("anthropic_version", "2023-06-01")
        self.retry_max, self.retry_backoff, self.retry_backoff_max = _read_retry(claude_cfg)
        self.api_key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        self._client = httpx.Client(timeout=self.timeout)

    def close(self) -> None:
        self._client.close()

    def is_available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str) -> str:
        if not self.api_key:
            raise ClaudeServiceError("ANTHROPIC_API_KEY not set",
                                     details={"required_env": "ANTHROPIC_API_KEY"})

        def do_post() -> httpx.Response:
            return self._client.post(
                f"{self.base_url}/messages",
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": self.anthropic_version,
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": self.max_tokens,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )

        return _post_with_retry(
            service="claude",
            do_post=do_post,
            max_retries=self.retry_max,
            backoff_base=self.retry_backoff,
            backoff_max=self.retry_backoff_max,
            extract_content=_extract_claude_content,
            on_http=lambda e: ClaudeServiceError(
                f"Claude HTTP {e.response.status_code}",
                details={"status": e.response.status_code},
            ),
            on_timeout=lambda e: ClaudeServiceError(
                "Claude timeout", details={"timeout_sec": self.timeout}
            ),
            on_other=lambda e: ClaudeServiceError(
                f"Claude error: {type(e).__name__}",
                details={"exc_type": type(e).__name__},
            ),
        )
