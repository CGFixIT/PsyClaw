#!/usr/bin/env python

"""
CyClaw FastAPI Gateway — HTTP/MCP entry point.

Invokes the LangGraph controller for every query.
Handles user confirmation flow for Grok fallback at the HTTP layer.
Binds to loopback only (see api.host / api.port in config.yaml).

CHANGES FROM ORIGINAL (soul.md / persistent personality integration):
  - Initialize PersonalityManager from config if personality.enabled
  - Pass personality to build_graph()
  - Add /soul endpoint (GET current soul, POST propose evolution)
  - Add /soul/apply endpoint (POST to apply after user confirmation)
---

Addresses:
  - LangSmith phone-home via langchain-core / langgraph
  - ChromaDB PostHog anonymized telemetry
  - OpenTelemetry OTLP export hooks pulled in by chromadb deps
"""
import asyncio
import hmac
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Resolve all bundled resources relative to this file, not the current working
# directory. When CyClaw is launched by double-clicking gate.py (Windows) the cwd
# is not guaranteed to be the repo root, so cwd-relative opens of config.yaml /
# static/ would crash at import time and the console window would vanish before
# the traceback could be read. Anchoring to __file__ makes startup cwd-independent.
_BASE_DIR = Path(__file__).resolve().parent

# The kill block itself now lives in utils/telemetry_kill.py so the entry points
# that never import gate -- mcp_hybrid_server.py and `python -m retrieval.indexer`
# -- enforce the identical set instead of relying on upstream defaults. This must
# still run BEFORE the heavy imports below (graph/retrieval/langchain/chromadb):
# those libraries latch their telemetry config at import time, so a later apply is
# too late. utils.telemetry_kill is stdlib-only for exactly this reason -- importing
# it cannot drag in a package that reads these vars on the way in.
#
# apply_telemetry_kill() both sets the vars and drops LangChain/LangSmith
# credentials, and returns the mapping it enforced so the startup table below can
# report it. Keep the local _TELEMETRY_KILL name: invariant-guard's G1 check
# asserts (by AST) that an assignment to this name precedes the first heavy import.
from utils.telemetry_kill import apply_telemetry_kill, verify_telemetry_contract

_TELEMETRY_KILL = apply_telemetry_kill()

_verified = {k: os.environ.get(k, "NOT SET") for k in _TELEMETRY_KILL}
print("[TELEMETRY KILL] Verified env state:")
for k, v in _verified.items():
    # Compare against the expected kill value, not a generic "non-empty" check.
    # CHROMA_OTEL_* are intentionally set to "" to disable them; the old
    # `v not in ("", "NOT SET")` check marked them MISSING on every startup.
    status = "OK" if v == _TELEMETRY_KILL[k] else "MISSING"
    print(f"  {status}  {k}={v}")
verify_telemetry_contract()

from importlib.metadata import version as _pkg_version, PackageNotFoundError
try:
    _CYCLAW_VERSION = _pkg_version("cyclaw")
except PackageNotFoundError:
    _CYCLAW_VERSION = "dev"

import logging
import yaml
from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from graph import build_graph, GraphState, _llm_identity
from retrieval.hybrid_search import HybridRetriever
from llm.client import ClaudeClient, LocalLLMClient, GrokClient
from schemas.api import (
    QueryRequest, QueryResponse, SourceInfo, HealthResponse, SoulEvolutionRequest,
)
from utils.logger import audit_log, hash_query, setup_logging
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from utils.sanitizer import check_input
from utils.errors import (
    PromptInjectionError, IndexNotFoundError
)
from utils.guardrail_bridge import build_generate_guard, build_input_guard, build_output_guard
from utils.health import check_all, close_http_client
from utils.numbat_cel import monitor_request
from utils.personality import PersonalityManager
from utils.authn_manager import AuthManager, BOOTSTRAP_USERNAME
from gate_ops import register_ops_routes
from gate_auth import attach_identity_to_query, register_auth_routes
from gate_memory import register_memory_routes
from metrics import summarize_audit

_bearer_scheme = HTTPBearer(auto_error=False)

def require_api_key(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
):
    # Fail-closed (PR #99 #4). Previously, when CYCLAW_API_KEY was unset the
    # server ran in "open mode" and require_api_key was a no-op, leaving every
    # /soul/* mutation endpoint unauthenticated. Now, if no key is configured the
    # endpoint is REFUSED rather than left open — no key is generated, logged, or
    # stored. Set CYCLAW_API_KEY to enable soul mutations.
    #
    # security.api_key_optional (default false) is the one deliberate escape
    # hatch: an operator who explicitly opts in gets this dependency skipped
    # entirely across every route it guards (soul/*, ops/*, memory/*,
    # audit/summary). Checked first, before the env var, so opting in also
    # means "no key needed" rather than "still refused when unset."
    #
    # The bypass additionally requires a LOOPBACK PEER, and that is the load-
    # bearing half. _require_loopback_bind covers `python gate.py`, but nothing
    # calls it when the app is served directly -- the shipped container's CMD is
    # `uvicorn gate:app --host 0.0.0.0`, and any operator can run the same by
    # hand. Keying the bypass off the socket's peer address instead of the bind
    # makes it independent of how the process was launched: a remote caller
    # never receives it, so the flag cannot silently open soul mutation or the
    # /ops/* subprocess shims to a network. TrustedHostMiddleware is not a
    # substitute -- a Host header is attacker-controlled, a peer address is not.
    #
    # The peer alone is not sufficient, and neither is peer+unproxied. See
    # _api_key_bypass_allowed for the full set of conditions and what each one
    # closes -- they are kept there rather than inline because every one of them
    # was added in response to a distinct, verified bypass.
    if _api_key_bypass_allowed(request):
        return
    api_key = os.environ.get("CYCLAW_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=401,
                            detail="Soul mutation disabled: CYCLAW_API_KEY not set")
    # Constant-time comparison: a plain `!=` short-circuits on the first
    # differing byte, leaking key length/prefix via response timing. compare_digest
    # runs in time independent of how many leading characters match.
    # Compare the UTF-8 bytes, not the str: hmac.compare_digest raises TypeError
    # on a str operand containing a non-ASCII character, and Starlette decodes the
    # Authorization header latin-1, so a token with any byte > 0x7F (a pasted
    # curly quote, an accented character) would otherwise escape this handler as
    # an unhandled 500 instead of the fail-closed 401 this endpoint promises. The
    # bytes overload never raises on content and preserves the constant-time property.
    if not credentials or not hmac.compare_digest(
        credentials.credentials.encode("utf-8"), api_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

# Per-IP rate limiter for /query. The limiter itself lives in utils/ratelimit.py
# as a lock-synchronized class so the gateway and its tests share one
# implementation (no duplicated logic) and concurrent requests under FastAPI's
# threadpool cannot interleave and overcount.
#
# Settings come from config.yaml (api.rate_limit), falling back to the historical
# 60 req / 60 s in-memory defaults. RateLimiter already supports sqlite
# write-through persistence, but it was never wired in here, so per-IP counters
# reset to zero on every process/container restart — a restart loop could let a
# client exceed the documented 60 req/min ceiling. Set
# api.rate_limit.persist_path (e.g. "data/rate_limits.db") to make counters
# survive restarts; leaving it null preserves the original in-memory behavior.
from fastapi import Request
from utils.config_validation import (
    validate_auth_config,
    validate_boot_timeout_config,
    validate_tls_config,
    validate_fallback_confirm_placeholder,
    validate_local_llm_reasoning_effort,
    validate_personality_config,
    validate_retrieval_config,
)
from utils.ratelimit import RateLimiter

# Load config.yaml ONCE here, anchored to _BASE_DIR rather than the cwd. The
# previous code opened a *relative* "config.yaml" for the rate-limit settings and
# then re-opened (and re-parsed) the same file via _BASE_DIR for app init below.
# The relative open crashes when gate.py is launched from a non-repo-root cwd
# (e.g. double-clicked on Windows) — the very failure mode _BASE_DIR exists to
# prevent — and the second read was pure startup overhead. One read, reused.
with open(_BASE_DIR / "config.yaml", encoding="utf-8") as _cfg_f:
    cfg = yaml.safe_load(_cfg_f) or {}
# Fail fast on an out-of-range retrieval tunable (e.g. min_score > 1 silently
# forces every query to user_gate; top_k <= 0 breaks retrieval). Without this the
# error would surface as silent mis-routing or a crash deep in a request instead
# of a clear ConfigError at boot.
validate_retrieval_config(cfg)
validate_personality_config(cfg)
validate_auth_config(cfg)
validate_tls_config(cfg)
# An unrecognized reasoning_effort would otherwise reach Ollama and come back as
# an HTTP 400 on the first /query -- surface it here instead, before any socket.
validate_local_llm_reasoning_effort(cfg)
_rl_cfg = ((cfg.get("api", {}) or {}).get("rate_limit", {})) or {}
RATE_LIMIT_REQUESTS = _rl_cfg.get("max_requests", 60)
RATE_LIMIT_WINDOW = _rl_cfg.get("window_seconds", 60)  # seconds
RATE_LIMIT_DB_PATH = _rl_cfg.get("persist_path") or None
# Optional Postgres persistence for rate-limit state (opt-in; defaults to None →
# sqlite persist_path if set, else in-memory). Resolution order: explicit
# api.rate_limit.database_url → CYCLAW_RATELIMIT_DB_URL. Deliberately does NOT
# fall back to the shared CYCLAW_DB_URL (personality DB) — an operator setting
# that for the soul database should not silently opt rate-limiting into
# Postgres too; each subsystem's Postgres backend is opted into independently.
RATE_LIMIT_DB_URL = (
    _rl_cfg.get("database_url")
    or os.environ.get("CYCLAW_RATELIMIT_DB_URL")
    or None
)
_rate_limiter = RateLimiter(
    max_requests=RATE_LIMIT_REQUESTS,
    window_seconds=RATE_LIMIT_WINDOW,
    db_path=RATE_LIMIT_DB_PATH,
    db_url=RATE_LIMIT_DB_URL,
)

def check_rate_limit(client_ip: str) -> bool:
    """Thin gateway-level wrapper over the shared RateLimiter instance."""
    return _rate_limiter.allow(client_ip)


async def _audit(event: dict) -> None:
    """Run audit_log() off the asyncio event loop.

    audit_log() does synchronous disk I/O (mkdir + open + write). Calling it
    directly inside an async handler stalls the single event-loop thread on
    every audited event, so under concurrent load all in-flight requests
    serialize behind each audit write. Hashing/redaction live inside
    audit_log() and are unchanged.

    Passes the module's live cfg explicitly -- without it, audit_log()'s
    cfg=None default re-reads config.yaml from disk (via its own lru_cache),
    ignoring this module's cfg entirely. That's silently harmless in
    production (both loads agree at boot) but wrong the moment a caller's cfg
    diverges from what's on disk, which is exactly the case in tests.
    """
    await asyncio.to_thread(audit_log, event, cfg=cfg)


def _request_username(request: Request) -> str | None:
    """Username stamped by require_session_or_token, or None when auth is off."""
    return getattr(request.state, "auth_username", None)


def _forbid_audit_query(username: str | None) -> None:
    """Audit role may log in but cannot run RAG queries."""
    if not username or auth_manager is None:
        return
    actor = auth_manager.get_user(username)
    if actor is not None and actor.role == "audit":
        raise HTTPException(
            status_code=403,
            detail={"error": "audit role cannot call /query", "code": "AUTH_ROLE_DENIED"},
        )


async def _forbid_audit_query_async(username: str | None) -> None:
    """Offload _forbid_audit_query's sqlite lookup to a worker thread.

    require_session_or_token (gate_auth.py) already keeps its own sqlite
    lookups off the event loop for exactly this reason -- this is the one
    lookup on the same request that was left running directly in the async
    query_endpoint body, blocking the single event-loop thread on every
    authenticated /query while auth is enabled.
    """
    await asyncio.to_thread(_forbid_audit_query, username)


def _reject_cross_site_query(request: Request) -> None:
    """Attached to POST /query UNCONDITIONALLY (see the Stage 3 wiring below)
    -- reuses _looks_cross_site, the same check that already protects the
    /soul api_key_optional bypass. Without this, a same-site (different-port)
    page could ride the operator's session cookie: SameSite=Strict blocks
    cross-SITE requests but not same-site cross-PORT ones, and unlike every
    /auth/* route (which all carry gate_auth.py's own _enforce_same_origin),
    /query had no CSRF/same-origin check to close that gap. A bearer/device
    token is unaffected either way -- it is never browser-attached, so
    _looks_cross_site's absent-header allowance already passes it through.

    This used to be attached only when auth.enabled was true, which is not the
    shipped default -- so the shipped /query had no check of its own. It was
    not reachable even then: QueryRequest is extra='forbid', strict=True, so
    only application/json parses (no cross-site HTML form can post it), and
    application/json forces a preflight the CORS allow-list refuses. But that
    made /query safe by ACCIDENT, as a side effect of its body shape, and the
    accident ends the day allowed_origins is widened for a demo. /index/build
    has carried the same check unconditionally all along; this is the parity.
    """
    if _looks_cross_site(request):
        raise HTTPException(
            status_code=403,
            detail={"error": "Cross-site request rejected", "code": "CROSS_SITE_BLOCKED"},
        )


async def _audit_query(request: Request, event: dict) -> None:
    """Audit a /query-path event, attaching username when a session/token resolved."""
    username = _request_username(request)
    if username:
        event = {**event, "username": username}
    await _audit(event)


async def _check_rate_limit_async(client_ip: str) -> bool:
    """Offload check_rate_limit to a worker thread.

    RateLimiter.allow() takes an internal lock and (when api.rate_limit
    persistence is configured) performs a sqlite/Postgres write. Off-loop is
    cheap and prevents persistence-mode head-of-line blocking; the in-memory
    default path also benefits because the lock-protected critical section no
    longer holds the event-loop thread.
    """
    return await asyncio.to_thread(check_rate_limit, client_ip)


# Throttle state for rate_limit_exceeded audit lines: without it, a sustained
# loopback flood writes one audit record per denied request, drowning every
# real signal in audit.jsonl. One line per IP per rate-limit window keeps the
# signal while bounding the write volume. Mutated only from the event loop
# (_enforce_rate_limit is async), so no lock is needed.
_RATE_LIMIT_AUDIT_CAP = 1024
_rate_limit_audit_last: dict[str, float] = {}


def _should_audit_rate_limit(client_ip: str, now: float) -> bool:
    """True at most once per IP per rate-limit window; hard-caps the map."""
    last = _rate_limit_audit_last.get(client_ip)
    if last is not None and now - last < RATE_LIMIT_WINDOW:
        return False
    _rate_limit_audit_last[client_ip] = now
    # Bound the map under a flood from many distinct IPs: drop stale entries
    # first, then evict oldest until we're at the cap. A many-IP flood inside
    # one window would otherwise grow unbounded because nothing is older than
    # RATE_LIMIT_WINDOW.
    if len(_rate_limit_audit_last) > _RATE_LIMIT_AUDIT_CAP:
        cutoff = now - RATE_LIMIT_WINDOW
        for ip in [ip for ip, ts in _rate_limit_audit_last.items() if ts < cutoff]:
            del _rate_limit_audit_last[ip]
        overflow = len(_rate_limit_audit_last) - _RATE_LIMIT_AUDIT_CAP
        if overflow > 0:
            oldest = sorted(
                _rate_limit_audit_last, key=_rate_limit_audit_last.get
            )[:overflow]
            for ip in oldest:
                del _rate_limit_audit_last[ip]
    return True


async def _enforce_rate_limit(request: Request) -> None:
    """Audit (throttled per IP per window) and raise HTTP 429 when the caller's
    per-IP budget is spent.

    Single enforcement point for every rate-limited endpoint (/query, /soul/*,
    /audit/summary, /ops/*). The 429 detail interpolates the configured
    api.rate_limit values — a hardcoded "(60/min)" here misled operators who
    tuned max_requests/window_seconds away from the defaults.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not await _check_rate_limit_async(client_ip):
        if _should_audit_rate_limit(client_ip, time.monotonic()):
            await _audit({"event": "rate_limit_exceeded", "ip": client_ip})
        raise HTTPException(
            status_code=429,
            detail={
                "error": f"Rate limit exceeded ({RATE_LIMIT_REQUESTS} req / {RATE_LIMIT_WINDOW}s)",
                "code": "RATE_LIMIT",
            },
        )

# Redact sensitive values from exception messages before returning in HTTP responses.
# Strips Bearer tokens, known secret-like patterns, and any live env var values
# that look like credentials (length > 8, not a common word).
_SECRET_PATTERNS = [
    re.compile(r'Bearer\s+[A-Za-z0-9\-_\.]+', re.IGNORECASE),  # Authorization headers
    re.compile(r'[Aa][Pp][Ii][_-]?[Kk][Ee][Yy]["\s:=]+[\w\-\.]+'),  # api_key = ...
    re.compile(r'sk-[A-Za-z0-9]{20,}'),       # OpenAI-style keys
    # Anthropic keys (sk-ant-api03-...) contain hyphens inside the token body,
    # so the OpenAI-style sk- pattern above (no hyphens allowed) never matches
    # them — this is a distinct shape, not a subset of the pattern above.
    re.compile(r'sk-ant-[A-Za-z0-9_\-]{20,}'),  # Anthropic (Claude) API keys
    # xAI keys (xai-...) match none of the shapes above: no sk- prefix, no
    # Bearer/api_key anchor. They were the one integrated provider whose key
    # shape passed through un-redacted (found in the 2026-07-28 sandbox
    # verification, anomaly A3).
    re.compile(r'xai-[A-Za-z0-9]{20,}'),       # xAI (Grok) API keys
    re.compile(r'ghp_[A-Za-z0-9]{36}'),        # GitHub PATs
    re.compile(r'xox[baprs]-[0-9a-zA-Z\-]+'), # Slack tokens
    re.compile(r'AKIA[0-9A-Z]{16}'),           # AWS access keys
]

def _model_provider_for(answer_model: str) -> str:
    """Map an answer_model string to a Numbat-friendly provider label."""
    if answer_model.startswith("grok"):
        return "xai"
    if answer_model.startswith("claude"):
        return "anthropic"
    return "ollama"


def _sanitize_error(exc: Exception) -> str:
    """Strip credential-like content from exception messages before HTTP response."""
    msg = str(exc)
    for pattern in _SECRET_PATTERNS:
        msg = pattern.sub('[REDACTED]', msg)
    # Also redact any live env var that looks like a credential (length > 8).
    # CYCLAW_API_KEY is the server's own auth secret — if it ever surfaced in an
    # auth-library or middleware traceback it must not be echoed in a 500 body.
    # ANTHROPIC_API_KEY mirrors the GROK_API_KEY entry below: ClaudeClient
    # (llm/client.py) reads the same env var, and its failure paths deserve the
    # identical defense-in-depth this loop already gives Grok.
    for env_key in ("GROK_API_KEY", "ANTHROPIC_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "SSC_TOKEN", "CYCLAW_API_KEY"):
        val = os.environ.get(env_key, "")
        if val and len(val) > 8:
            msg = msg.replace(val, '[REDACTED]')
    return msg

# =============================================================================
# App Init
# =============================================================================
# cfg was already loaded once above (anchored to _BASE_DIR) — reuse it instead
# of re-reading and re-parsing config.yaml a second time.

setup_logging(cfg)
logger = logging.getLogger("cyclaw.gate")

if not os.environ.get("CYCLAW_API_KEY", ""):  # pragma: no cover - import-time; tests set the key
    logger.warning(
        "CYCLAW_API_KEY is not set — soul-mutation endpoints (/soul/*) are DISABLED "
        "(fail-closed). Set CYCLAW_API_KEY to enable them."
    )

# Was a boot-time warning only; every OTHER relational config invariant in
# this module (min_score range, soul_max_chars) already fails closed via
# ConfigError, so a misconfigured timeout pair silently degraded in
# production (orphaned graph invocations under load) instead of failing at
# start like its siblings. See utils.config_validation.validate_boot_timeout_config.
validate_boot_timeout_config(cfg)
validate_fallback_confirm_placeholder(cfg)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: nothing extra needed — clients are already initialized at module level.
    yield
    # Shutdown: close persistent connection pools so the OS reclaims file
    # descriptors and TIME_WAIT sockets promptly on server restart.
    # Each close is isolated so one failure does not skip the rest.
    for _name, _obj in [
        ("local_llm", local_llm),
        ("grok", grok),
        ("claude", claude),
        ("rate_limiter", _rate_limiter),
        ("personality", personality),
        ("auth_manager", auth_manager),
        ("retriever", retriever),
    ]:
        if _obj is not None:
            try:
                _obj.close()
            except Exception:
                logger.warning("shutdown close failed for %s", _name, exc_info=True)
    # The /health probe pool is module-level in utils.health (no instance to
    # list above); close it through its own teardown hook for the same reason.
    try:
        close_http_client()
    except Exception:
        logger.warning("shutdown close failed for health http client", exc_info=True)


app = FastAPI(
    title="CyClaw RAG Gateway",
    description="Offline-first, RAG-first, MCP-exposed stack",
    version=_CYCLAW_VERSION,
    lifespan=lifespan,
    # Auto-docs surface disabled: /openapi.json disclosed the full request
    # schemas of /soul/* and /ops/* to any unauthenticated loopback caller, and
    # the Swagger/ReDoc pages load their assets from cdn.jsdelivr.net — both
    # contradict the offline-first, minimal-surface posture. Nothing in the
    # repo (tests, static console, MCP server) consumes these routes.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

@app.exception_handler(RequestValidationError)
async def _on_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Re-emit FastAPI's 422 without ever echoing the value that failed.

    FastAPI's default handler puts each error's raw submitted value under
    ``detail[].input`` -- harmless for /query's query text, but /auth/login's
    ``password`` field turns a merely too-long or wrong-type password into a
    verbatim disclosure in the 422 response body (and a malformed/non-JSON
    body echoes the WHOLE raw request, username+password included, the same
    way). Mirrors harness/server.py's ``_validation_error_response``, which
    solved the identical problem for that app: report only the field
    location, never the value. Applies to every route (there is no way to
    scope a FastAPI exception handler to one path), but only ever REMOVES
    information from the existing default body -- no other route's tests
    assert anything more specific than the 422 status code.
    """
    fields = []
    for err in exc.errors():
        loc = err.get("loc") or ()
        fields.append(".".join(str(part) for part in loc) or "unknown field")
    named = ", ".join(fields) or "unknown field"
    return JSONResponse(
        status_code=422,
        content={"error": f"request body failed validation: {named}", "code": "VALIDATION_ERROR"},
    )


app.mount("/static", StaticFiles(directory=str(_BASE_DIR / "static")), name="static")

@app.get("/", response_class=FileResponse)
def serve_terminal_console():
    """Primary browser entry point — the Soul Console."""
    return FileResponse(str(_BASE_DIR / "static" / "terminal.html"))

_origins = cfg.get("security", {}).get("allowed_origins", [])
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=False,
)

# TrustedHostMiddleware (PR #99 #3): reject requests whose Host header is not in
# the allow-list. CORS governs response *readability*; it does not stop a
# DNS-rebinding page from executing state-changing POST /soul/* server-side. The
# Host check does. Starlette's add_middleware inserts each new middleware at the
# OUTSIDE of the stack, so at this point (added after CORS, before the security-
# headers middleware below) TrustedHost is the outer wrapper around CORS + the
# routes — the security-headers middleware added last ends up outside it. Host
# matching ignores port; the list is config-driven so an operator can add any
# name/IP they reach CyClaw by (e.g. the home-lab LAN IP).
from starlette.middleware.trustedhost import TrustedHostMiddleware
_allowed_hosts = cfg.get("security", {}).get("allowed_hosts", ["127.0.0.1", "localhost"])  # DevSkim: ignore DS162092,DS137138 - loopback host allow-list by design
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

# Max-body-size middleware (added before _SecurityHeadersMiddleware below, so
# that middleware — the outermost layer — still wraps this one and stamps its
# headers on a 413 rejection too, same as it already does for TrustedHost's
# 400). schemas/api.py's per-field max_length caps only bound what survives
# Pydantic parsing; Starlette buffers the entire raw body into memory first,
# so an oversized POST costs memory regardless of what the parsed fields turn
# out to be. See config.yaml security.max_request_body_bytes for the accepted
# scope of this check (Content-Length only).
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse as _JSONResponse


class _MaxBodySizeMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, max_bytes: int):
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: StarletteRequest, call_next):  # type: ignore[override]
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                declared_bytes = int(declared)
            except ValueError:
                declared_bytes = None
            if declared_bytes is not None and declared_bytes > self._max_bytes:
                return _JSONResponse(
                    status_code=413,
                    content={
                        "error": f"request body exceeds {self._max_bytes} bytes",
                        "code": "PAYLOAD_TOO_LARGE",
                    },
                )
        return await call_next(request)


_max_body_bytes = cfg.get("security", {}).get("max_request_body_bytes", 1048576)
app.add_middleware(_MaxBodySizeMiddleware, max_bytes=_max_body_bytes)

# Security response headers middleware: sets defense-in-depth headers on every
# response (X-Content-Type-Options, X-Frame-Options, Referrer-Policy,
# Permissions-Policy) and adds Cache-Control: no-store on the root / static paths
# to prevent browser caching of the Soul Console. Added LAST, so (per Starlette's
# outside-in add_middleware ordering) it is the OUTERMOST middleware and wraps the
# TrustedHost check — it therefore stamps these headers on every response,
# including the 400 a rejected Host produces. That is intentional: defense-in-depth
# headers belong on error responses too, and they carry no request data.
from starlette.responses import Response as StarletteResponse


class _SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: StarletteRequest, call_next):  # type: ignore[override]
        response: StarletteResponse = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
            "connect-src 'self'; font-src 'self'; base-uri 'none'; "
            "form-action 'none'; frame-ancestors 'none'"
        )
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers.setdefault(
                "Cache-Control", "no-store, no-cache, must-revalidate, max-age=0"
            )
        return response


app.add_middleware(_SecurityHeadersMiddleware)

# Declared here, constructed by _init_retrieval() below once its dependencies
# (the LLM clients, personality, and the guardrail rails) exist. Both are
# module globals on purpose: /query resolves them at CALL time, not at import,
# so reassigning them makes a freshly built index live without restarting the
# process -- which is what /index/build relies on.
retriever = None
compiled_graph = None

# Pass the already-parsed cfg dict into both clients rather than letting them
# re-open a *relative* "config.yaml" (their default when cfg is None). That
# relative read is cwd-dependent and crashes at import when gate.py is launched
# from a non-repo-root cwd — the same fragility _BASE_DIR / the single-read above
# exist to prevent. LocalLLMClient is always built, so this hardens every mode.
local_llm = LocalLLMClient(cfg=cfg)

grok = None
# This `if` IS two of the triple-gate's three conditions (mode=="hybrid" AND
# grok.enabled) — the graph itself only checks the third (user confirmed) plus
# whether this client ended up non-None. Building a GrokClient anywhere else,
# or loosening this check "to simplify," would let a confirmed low-score query
# reach a paid external API even in offline mode, since the graph has no
# backstop for mode/enabled (see INVARIANTS.md Rule 2).
# ``is True`` is deliberate: quoted YAML ``"false"`` must fail closed instead
# of becoming a truthy string that constructs an external client.
if (
    cfg.get("app", {}).get("mode") == "hybrid"
    and cfg["models"].get("grok", {}).get("enabled") is True
):
    grok = GrokClient(cfg=cfg)

claude = None
# Same strict double-gate as grok above, mirrored for the Claude fallback (PR #441).
if (
    cfg.get("app", {}).get("mode") == "hybrid"
    and cfg["models"].get("claude", {}).get("enabled") is True
):
    claude = ClaudeClient(cfg=cfg)

def _usable_online_providers() -> list[str]:
    # The SAME predicate graph.py's user_gate_router applies before routing to a
    # fallback node: the client must exist (mode=="hybrid" AND that provider
    # enabled, both decided above) AND is_available() must be true (its API key
    # env var is set). Kept as one helper so the confirm prompt cannot offer a
    # provider the router would silently decline to use -- before this, the
    # prompt named Grok and Claude unconditionally, so an operator running fully
    # offline could pick "Send to Grok" and get a local offline answer labelled
    # as if it had gone to Grok. is_available() is pure key presence with no
    # network probe (llm/client.py), so calling it per request costs nothing.
    usable: list[str] = []
    if grok is not None and grok.is_available():
        usable.append("grok")
    if claude is not None and claude.is_available():
        usable.append("claude")
    return usable

_PROVIDER_LABELS = {"grok": "Send to Grok", "claude": "Send to Claude"}

def _confirm_choices(providers: list[str], local_tag: str) -> str:
    """The 'here are your options' half of a confirm prompt, provider-accurate.

    local_tag names the model that WOULD answer if the user stays offline --
    resolved by the caller via _llm_identity("offline-best-effort", cfg), so
    this never re-derives config.yaml's models.local_llm.model itself. Naming
    it here (instead of a generic "Offline Best Effort") is the fix for a
    console that showed opaque role labels with no way to tell which model any
    given answer actually came from.
    """
    if not providers:
        return (
            f"No external provider is available (offline mode, provider disabled, "
            f"or its API key is unset), so {local_tag} will answer from its own knowledge."
        )
    labels = [_PROVIDER_LABELS[p] for p in providers]
    return f"Stay offline with {local_tag}, or " + " or ".join(labels) + "."

def _boot_personality_enabled(personality_cfg: object) -> bool:
    """True only when ``personality.enabled`` is the literal boolean ``True``.

    Same quoted-YAML fail-closed rule as ``_boot_auth_enabled``: a string
    ``"false"`` is truthy in Python and would otherwise construct
    PersonalityManager and prepend soul to every local prompt.
    """
    if not isinstance(personality_cfg, dict):
        return False
    return personality_cfg.get("enabled") is True


personality = None
if _boot_personality_enabled(cfg.get("personality")):
    personality = PersonalityManager(cfg)

def _boot_auth_enabled(auth_cfg: object) -> bool:
    """True only when ``auth_cfg["enabled"]`` is the literal boolean ``True``.

    A standalone, testable twin of ``_flag_is_true`` below (used by the bind
    guard): that helper can't be called from here because it is defined
    later in this file for the bind guard, and this runs at MODULE IMPORT
    TIME, before it exists. Deliberately NOT truthy `.get("enabled", False)`:
    every non-empty string is truthy, so a config carrying `enabled: "false"`
    (quoted, which YAML parses as the STRING "false") would otherwise
    construct the AuthManager, create the auth database, and bootstrap +
    print a first account's password -- while `_flag_is_true` reads the SAME
    key strictly and reports it OFF, leaving the server in a state where
    auth is simultaneously "on" (accounts exist, routes work) and "off" (no
    non-loopback bind was ever intended).
    """
    return isinstance(auth_cfg, dict) and auth_cfg.get("enabled") is True


# Stage 2 of docs/AUTHENTICATION_DESIGN.md. auth_manager stays None (the
# request path below never constructs it) unless auth.enabled is true --
# matching every other CyClaw subsystem's disabled-by-default convention.
# gate_auth.py's routes always exist regardless (see its own docstring for
# why), but every handler checks for None first and returns 503.
auth_manager = None
if _boot_auth_enabled(cfg.get("auth")):  # pragma: no cover - import-time; shipped/tests leave auth off
    auth_manager = AuthManager(cfg)
    # bootstrap_if_empty() is a no-op on every boot after the first: it only
    # ever acts when the users table is genuinely empty. No credential
    # appears in this banner on purpose: an earlier version printed a
    # generated one-time password here, and CodeQL rightly flagged it
    # (alert #1057) -- a service's stdout is not ephemeral (systemd journal,
    # Docker log driver, any log shipper all persist it). The account is
    # created with an unusable placeholder hash instead (see
    # bootstrap_if_empty's docstring), so there is no secret to show: the
    # operator sets the first real password locally, off any output channel,
    # via getpass.
    if auth_manager.bootstrap_if_empty():
        print(
            f"\n{'=' * 70}\n"
            f"CyClaw authentication is now enabled. A first account was created:\n"
            f"\n"
            f"    username: {BOOTSTRAP_USERNAME}\n"
            f"\n"
            f"It cannot be logged into yet: no password is set (and none was\n"
            f"generated anywhere visible). Set one now, on this machine:\n"
            f"\n"
            f"    cyclaw-user passwd {BOOTSTRAP_USERNAME}\n"
            f"{'=' * 70}\n"
        )

# Phase 2 NeMo Guardrails offline input rail (docs/NeMo/phase2_implementation_plan.md).
# build_input_guard returns None when guardrails.enabled is false (the shipped
# default) without importing guardrails at all -- gate.py never names that
# package, preserving module isolation (invariant I6).
input_guard = build_input_guard(cfg)

# Phase 4 NeMo Guardrails offline output (grounding) rail
# (docs/NeMo/phase4_implementation_plan.md). Same enabled gate and the same
# module-isolation guarantee as build_input_guard above.
output_guard = build_output_guard(cfg)
generate_guard = build_generate_guard(cfg)

def _init_retrieval(*, boot: bool = False) -> bool:
    """(Re)build ``retriever`` and ``compiled_graph`` from the index on disk.

    Called once at import and again after a successful /index/build, which is
    the whole reason it is a function: previously this ran only at module
    scope, so a first-run operator who built the index had to restart the
    server before /query would answer.

    Fail-soft by design. A missing index leaves both globals None, /query
    answers 503 INDEX_NOT_FOUND, and /health reports index_ready false -- the
    documented first-run state, not a crash. The stderr banner is boot-only:
    after a build the same condition means "the build produced no index",
    which the caller reports through /index/status instead.

    Builds the new retriever/graph into locals and swaps the globals only at
    the end, so an in-flight /query during a hot rebuild continues to use the
    previous graph instead of hitting a transient 503 INDEX_NOT_FOUND. The
    previous retriever is closed after the swap (no-op for ChromaDB, releases
    the psycopg connection for pgvector). Returns True when a usable graph now
    exists.
    """
    global retriever, compiled_graph
    new_retriever = None
    try:
        new_retriever = HybridRetriever()
    except IndexNotFoundError as e:
        if boot:
            print(f"FATAL: {e.message}", file=sys.stderr)
            print("Run: python -m retrieval.indexer", file=sys.stderr)
        logger.critical("Retrieval index not found: %s", e.message)

    new_graph = None
    if new_retriever is not None:
        new_graph = build_graph(
            retriever=new_retriever, llm=local_llm, grok=grok, claude=claude, cfg=cfg,
            personality=personality, input_guard=input_guard, output_guard=output_guard,
            generate_guard=generate_guard,
        )

    old_retriever = retriever
    retriever = new_retriever
    compiled_graph = new_graph
    if old_retriever is not None and old_retriever is not new_retriever:
        try:
            old_retriever.close()
        except Exception:
            logger.warning("hot-init close failed for previous retriever", exc_info=True)

    return compiled_graph is not None


_init_retrieval(boot=True)


# ── FIRST-RUN INDEX BUILD ──────────────────────────────────────────────────
# A missing index was already fail-soft (503 INDEX_NOT_FOUND), but the only
# way out was a CLI command plus a process restart -- unusable for anyone who
# did not set the server up themselves, and the first thing a new operator
# hits. These two routes build the index in place and hot-init retrieval when
# it finishes, so the console can recover from first-run without a terminal.
_INDEX_BUILD_LOCK = threading.Lock()
_index_build: dict[str, Any] = {
    "state": "idle",       # idle | running | done | error
    "started_at": None,
    "finished_at": None,
    "error": None,
    "chunks_done": 0,
    "chunks_total": 0,
}


class _IndexProgressHandler(logging.Handler):
    """Turn retrieval.indexer's own progress logging into build progress.

    build_index() takes no callback and returns None, so there is nothing to
    subscribe to. It does emit one ``logger.info("Indexed %d/%d chunks", ...)``
    per batch, so this matches on ``record.msg`` -- the FORMAT STRING, not the
    rendered text -- and reads ``record.args``. That needs no string parsing
    and survives any wording change that keeps the same format literal.

    Fail-soft: if that log line ever disappears the build still completes and
    progress simply stays at zero, which the console renders as an
    indeterminate spinner plus elapsed time rather than a wrong percentage.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            if record.msg == "Indexed %d/%d chunks" and record.args:
                with _INDEX_BUILD_LOCK:
                    _index_build["chunks_done"] = int(record.args[0])
                    _index_build["chunks_total"] = int(record.args[1])
        except Exception:  # noqa: BLE001, S110  # nosec B110 -- progress is best-effort only
            pass


def _index_status_payload() -> dict[str, Any]:
    with _INDEX_BUILD_LOCK:
        snapshot = dict(_index_build)
    started, finished = snapshot["started_at"], snapshot["finished_at"]
    elapsed = None
    if started is not None:
        elapsed = round((finished if finished is not None else time.monotonic()) - started, 1)
    return {
        "state": snapshot["state"],
        "elapsed_sec": elapsed,
        "chunks_done": snapshot["chunks_done"],
        "chunks_total": snapshot["chunks_total"],
        "error": snapshot["error"],
        "index_ready": compiled_graph is not None,
    }


def _run_index_build() -> None:
    """Worker body for /index/build. Runs on a plain daemon thread."""
    # Lazy import: the indexer pulls the embedding stack, and every process
    # that imports gate.py would otherwise pay for it even though only this
    # one route uses it. I6 is unaffected -- retrieval/ is core, not one of
    # the out-of-band layers gate.py is forbidden to import.
    from retrieval.indexer import build_index

    handler = _IndexProgressHandler()
    idx_logger = logging.getLogger("retrieval.indexer")
    idx_logger.addHandler(handler)
    try:
        build_index(str(_BASE_DIR / "config.yaml"))
        index_ready = _init_retrieval()
        with _INDEX_BUILD_LOCK:
            if index_ready:
                _index_build["state"] = "done"
            else:
                _index_build["state"] = "error"
                _index_build["error"] = "Build finished but no index was produced."
    except Exception as e:  # noqa: BLE001 -- surfaced via /index/status, never raised into a request
        logger.exception("Index build failed")
        with _INDEX_BUILD_LOCK:
            _index_build["state"] = "error"
            _index_build["error"] = _sanitize_error(e)
    finally:
        idx_logger.removeHandler(handler)
        with _INDEX_BUILD_LOCK:
            _index_build["finished_at"] = time.monotonic()


@app.post("/index/build", dependencies=[Depends(_enforce_rate_limit)])
async def index_build(request: Request) -> dict[str, Any]:
    """Start a background index build from the configured corpus.

    Auth is the loopback socket peer plus same-origin, NOT the API key --
    deliberately, and for the same reason /auth/bootstrap-password uses that
    pair: on a genuine first run CYCLAW_API_KEY may not be set yet, and an
    unset key fails CLOSED (401), so key-gating this route would brick exactly
    the flow it exists to unblock. The peer check is on the socket, which a
    Host or Origin header cannot forge.
    """
    client_host = request.client.host if request.client else ""
    if not _is_loopback_host(client_host):
        await _audit({"event": "index_build_rejected", "reason": "non_loopback", "ip": client_host})
        raise HTTPException(
            status_code=403,
            detail={"error": "Index builds must be started from this machine",
                    "code": "INDEX_BUILD_LOOPBACK_ONLY"},
        )
    if _looks_cross_site(request):
        await _audit({"event": "index_build_rejected", "reason": "cross_site", "ip": client_host})
        raise HTTPException(
            status_code=403,
            detail={"error": "Cross-site request rejected", "code": "CROSS_SITE_BLOCKED"},
        )

    # One build at a time. Two concurrent runs would write the same ChromaDB
    # collection and the same bm25.json, so the loser corrupts the winner.
    with _INDEX_BUILD_LOCK:
        if _index_build["state"] == "running":
            raise HTTPException(
                status_code=409,
                detail={"error": "An index build is already running",
                        "code": "INDEX_BUILD_IN_PROGRESS"},
            )
        _index_build.update({
            "state": "running", "started_at": time.monotonic(), "finished_at": None,
            "error": None, "chunks_done": 0, "chunks_total": 0,
        })

    await _audit({"event": "index_build_started", "ip": client_host})
    threading.Thread(target=_run_index_build, name="cyclaw-index-build", daemon=True).start()
    return _index_status_payload()


# Deliberately NOT rate-limited, and this is the only way to say so: the
# limiter is a per-route dependency, never global middleware, so "exempt" means
# declaring no dependency here. The console polls this every INDEX_POLL_MS
# (1.5s, static/terminal.js) for the whole length of a build, which is 40 of
# the 60 req/min a single IP gets -- leaving an operator's own queries to
# finish a build inside the remaining 20. Throttling a progress bar to protect
# a server from the progress bar is the wrong trade: the handler is a
# lock-guarded dict copy and one subtraction (_index_status_payload), it
# returns build state and never corpus content, and GET /health is the
# standing precedent -- already unauthenticated AND unlimited while doing an
# entire check_all service fan-out per call.
@app.get("/index/status")
async def index_status() -> dict[str, Any]:
    """Progress of the current or most recent index build. Always 200."""
    return _index_status_payload()


@app.post("/query", response_model=QueryResponse)
async def query_endpoint(request: Request, req: QueryRequest):
    await _enforce_rate_limit(request)

    if compiled_graph is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Index not built. Run: python -m retrieval.indexer",
                    "code": "INDEX_NOT_FOUND"}
        )

    username = _request_username(request)
    await _forbid_audit_query_async(username)

    try:
        check_input(req.query)
    except PromptInjectionError as e:
        # Pass the full query: audit_log() SHA-256-hashes the "query" field, so
        # truncating here yields a hash of only the first 50 chars that diverges
        # from the canonical full-query hash written by the graph audit node and
        # the MCP path. No raw text is persisted either way.
        await _audit_query(request, {"event": "prompt_injection_blocked", "query": req.query})
        raise HTTPException(
            status_code=400,
            detail={"error": e.message, "code": e.code, "details": e.details}
        ) from e

    initial_state: GraphState = {
        "query": req.query,
        "user_confirmed_online": req.user_confirmed_online,
        "online_provider": req.online_provider,
    }
    if username:
        initial_state["username"] = username

    # Overall server-side deadline: a stalled Ollama / retrieval must not hold
    # the request (and a worker thread) open indefinitely. The per-call LLM
    # timeouts are an inner bound; this is the outer one covering the whole graph.
    graph_timeout = cfg.get("api", {}).get("graph_timeout_sec", 780)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(compiled_graph.invoke, initial_state),
            timeout=graph_timeout,
        )
    except TimeoutError as e:
        await _audit_query(request, {"event": "graph_timeout", "query": req.query, "timeout_sec": graph_timeout})
        logger.warning("graph invoke exceeded %ss deadline", graph_timeout)
        raise HTTPException(
            status_code=504,
            detail={
                "error": (
                    f"Request exceeded the {graph_timeout}s server deadline. The local LLM or "
                    f"retrieval likely stalled — check that Ollama is running (ollama serve) and that its "
                    f"loaded context length >= retrieval.max_context_tokens + "
                    f"models.local_llm.max_tokens + ~1500 headroom (see config.yaml), or it can "
                    f"stall at '0% processing'."
                ),
                "code": "GRAPH_TIMEOUT",
            },
        ) from e
    except Exception as e:
        safe_msg = _sanitize_error(e)
        await _audit_query(request, {"event": "graph_error", "query": req.query, "error": safe_msg})
        raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "GRAPH_ERROR"}) from e

    # Optional CEL monitor-only rules over structured, safe fields. Runs after
    # graph invoke so top_score/answer_model/guardrail_* are known. Fail-open:
    # any error here is logged and must not affect the response or audit trail.
    # Skipped entirely when numbat.cel.enabled is not true (matching
    # utils.numbat_cel's literal-True check) so no hashing work happens on the
    # request path; when enabled it runs in a worker thread like _audit and
    # _check_rate_limit_async, since CEL compile/eval and the Numbat file write
    # are synchronous. The graph state key is answer_sources (graph.py's
    # GraphState) — result.get("sources") was never populated, which left
    # source_hashes permanently empty.
    #
    # ALL config access lives inside this try. A truthy non-dict `numbat:` OR
    # nested `cel:` (malformed YAML: cel: true / cel: "yes") must not
    # AttributeError into a 500. Nested cel is isinstance(..., dict) before
    # .get("enabled"), matching utils.numbat_cel._cel_cfg.
    try:
        numbat = cfg.get("numbat")
        cel_block = numbat.get("cel") if isinstance(numbat, dict) else None
        if isinstance(cel_block, dict) and cel_block.get("enabled") is True:
            sources = result.get("answer_sources", []) or []
            await asyncio.to_thread(
                monitor_request,
                query_hash=hash_query(req.query),
                top_score=result.get("top_score"),
                answer_model=result.get("answer_model"),
                guardrail_blocked=result.get("guardrail_blocked"),
                guardrail_rails=result.get("guardrail_rails"),
                model_provider=_model_provider_for(result.get("answer_model", "")),
                source_hashes=[
                    hash_query(f"{s.get('source', '')}:{s.get('chunk_id', -1)}")
                    for s in sources
                ],
                cfg=cfg,
            )
    except Exception as exc:
        logger.warning("CEL monitor request failed: %s", exc)

    needs_confirm = result.get("needs_user_confirm", False)
    answer_model = result.get("answer_model", "")
    # Same mapping graph.py's audit_logger_node uses for audit.jsonl, reused
    # here so the console's model badge and the audit trail can never disagree
    # about which model answered. answer_model is empty on the pause path, and
    # _llm_identity("") resolves to llm_model=None there -- correct, since no
    # model has run yet.
    llm_model = _llm_identity(answer_model, cfg).get("llm_model")

    # needs_user_confirm stays True in the final graph state even after a
    # confirmed query gets answered by a fallback node (it's set once by
    # route_by_score/user_gate and never cleared downstream) — so checking it
    # alone can't tell "still waiting on the user" from "already answered".
    # answer_model is only empty on the genuine pause path, so both conditions
    # together are what actually distinguishes the two.
    if needs_confirm and not answer_model:
        top_score = result.get("top_score", 0.0)
        # validate_retrieval_config() ran at boot (above), so this key is
        # guaranteed present and in [0, 1] — the old 0.4 fallback was
        # unreachable dead code that contradicted the shipped 0.028 default.
        threshold = cfg["retrieval"]["min_score"]
        # A retrieval failure (retrieve_node caught a RAGError and set
        # state["error"] with top_score=0.0) also lands here, but it is NOT a
        # vault miss — presenting it as one hides a broken index behind a
        # routine "send to Grok?" prompt. Name the failure in the confirm
        # message (the console renders only confirm_message on this path) and
        # pass error through for API consumers, matching the answered path.
        retrieval_error = result.get("error")
        providers = _usable_online_providers()
        # The model that WOULD answer if the user stays offline -- resolved
        # regardless of the pause's own (empty) answer_model, since nothing has
        # run yet at this point.
        offline_tag = _llm_identity("offline-best-effort", cfg).get("llm_model") or "the local model"
        choices = _confirm_choices(providers, offline_tag)
        if retrieval_error:
            confirm_message = (
                f"Retrieval failed ({retrieval_error}) — no vault results available. {choices}"
            )
        else:
            confirm_message = (
                f"Vault miss (best score: {top_score:.3f} < {threshold}). {choices}"
            )
        return QueryResponse(
            answer="",
            sources=[],
            retrieval_mode=result.get("retrieval_mode", "none"),
            hit_count=len(result.get("retrieved_docs", [])),
            model_used="",
            llm_model=llm_model,
            needs_confirm=True,
            confirm_message=confirm_message,
            available_providers=providers,
            error=retrieval_error,
        )

    docs = result.get("answer_sources", [])
    sources = []
    skipped_sources = 0
    for d in docs:
        if isinstance(d, dict):
            sources.append(SourceInfo(
                source=d.get("source", ""),
                score=d.get("score", 0.0),
                chunk_id=d.get("chunk_id", -1),
                source_sha256=d.get("source_sha256", ""),
                stem_tags=d.get("stem_tags", []),
                semantic_score=d.get("semantic_score"),
                semantic_rank=d.get("semantic_rank"),
                keyword_score=d.get("keyword_score"),
                keyword_rank=d.get("keyword_rank"),
                rrf_score=d.get("rrf_score"),
                rrf_semantic_contrib=d.get("rrf_semantic_contrib"),
                rrf_keyword_contrib=d.get("rrf_keyword_contrib")
            ))
        else:
            skipped_sources += 1
    if skipped_sources:
        logger.warning("Dropped %d non-dict source(s) from /query response", skipped_sources)
        await _audit_query(request, {"event": "skipped_sources", "query": req.query,
                       "skipped_count": skipped_sources,
                       "total_sources": len(docs)})

    return QueryResponse(
        answer=result.get("answer", "[No answer generated]"),
        sources=sources,
        retrieval_mode=result.get("retrieval_mode", "none"),
        hit_count=len(result.get("retrieved_docs", [])),
        model_used=result.get("answer_model", "unknown"),
        llm_model=llm_model,
        needs_confirm=False,
        error=result.get("error")
    )

@app.get("/soul", dependencies=[Depends(_enforce_rate_limit), Depends(require_api_key)])
async def get_soul(request: Request):
    if personality is None:
        raise HTTPException(status_code=404, detail="Personality system not enabled")
    await _audit({"event": "soul_read", "version": personality.get_version()})
    return {
        "soul": personality.get_system_prompt_additive(),
        "version": personality.get_version(),
        "source": str(personality.soul_path)
    }

@app.post("/soul/propose", dependencies=[Depends(_enforce_rate_limit), Depends(require_api_key)])
async def propose_soul_evolution(request: Request, req: SoulEvolutionRequest):
    if personality is None:
        raise HTTPException(status_code=404, detail="Personality system not enabled")
    proposal = await asyncio.to_thread(personality.propose_evolution, req.new_soul, req.reason)
    await _audit({"event": "soul_evolution_proposed", "reason": req.reason})
    return proposal

@app.post("/soul/apply", dependencies=[Depends(_enforce_rate_limit), Depends(require_api_key)])
async def apply_soul_evolution(request: Request, req: SoulEvolutionRequest):
    if personality is None:
        raise HTTPException(status_code=404, detail="Personality system not enabled")
    try:
        result = await asyncio.to_thread(personality.apply_evolution, req.new_soul, req.reason)
    except PromptInjectionError as e:
        # PersonalityManager.apply_evolution already audited
        # soul_apply_injection_blocked (with flag_count) before raising.
        # Do not emit a second event here -- it doubled metrics and looked
        # like two distinct blocks.
        raise HTTPException(
            status_code=400,
            detail={"error": e.message, "code": e.code, "details": e.details},
        ) from e
    except ValueError as e:
        # apply_evolution enforces the I5 human-reason gate itself and signals a
        # bad reason with ValueError. SoulEvolutionRequest only caps reason at
        # min_length=1, so an all-whitespace reason passes validation, reaches
        # that raise, and — with no exception_handler registered anywhere in
        # gate.py/gate_ops.py — escaped as an unhandled 500. It's a malformed
        # request, so report it as one.
        await _audit({"event": "soul_apply_rejected", "reason": req.reason})
        raise HTTPException(
            status_code=400,
            detail={"error": str(e), "code": "INVALID_REASON"},
        ) from e
    return result

@app.post("/soul/reload", dependencies=[Depends(_enforce_rate_limit), Depends(require_api_key)])
async def reload_soul(request: Request):
    if personality is None:
        raise HTTPException(status_code=404, detail="Personality system not enabled")
    await asyncio.to_thread(personality.reload)
    return {"status": "reloaded", "version": personality.get_version()}

@app.post("/soul/restore", dependencies=[Depends(_enforce_rate_limit), Depends(require_api_key)])
async def restore_soul(request: Request):
    if personality is None:
        raise HTTPException(status_code=404, detail="Personality system not enabled")
    try:
        result = await asyncio.to_thread(personality.restore_from_backup)
        return result
    except FileNotFoundError as e:
        await _audit({"event": "soul_restore_failed", "error": str(e)})
        raise HTTPException(status_code=404, detail=str(e)) from e

def _ops_sync_timeout_sec() -> int:
    """The server's own /ops/sync budget, for the console to bound its fetch above.

    sync.sync_timeout_sec has no upper bound, so no console-side constant can
    cover every valid configuration -- the client has to be told. No guard here
    on purpose: utils.ops_runner.sync_timeout_sec is contractually fail-soft
    (it falls back to the shipped budget on an unreadable or malformed config),
    so wrapping it again would only hide a future regression behind a value
    /health would then report as fact.
    """
    from utils.ops_runner import sync_timeout_sec

    return int(sync_timeout_sec())


@app.get("/health", response_model=HealthResponse)
async def health():
    statuses = await asyncio.to_thread(check_all)
    return HealthResponse(
        status="ok" if all(s.healthy for s in statuses) else "degraded",
        services={s.name: {"healthy": s.healthy, "latency_ms": s.latency_ms, "error": s.error} for s in statuses},
        index_ready=retriever is not None,
        graph_ready=compiled_graph is not None,
        mode=cfg["app"]["mode"],
        graph_timeout_sec=cfg.get("api", {}).get("graph_timeout_sec", 780),
        # Imported lazily: utils.ops_runner is only needed to answer this one
        # field, and a module-level import here would sit among the heavy
        # imports the _TELEMETRY_KILL block deliberately precedes.
        ops_sync_timeout_sec=_ops_sync_timeout_sec(),
        version=_CYCLAW_VERSION,
        # Display-only, so the first-run panel can name the folder to put
        # documents in. The configured value verbatim (relative as written in
        # config.yaml), NOT the absolute resolved path -- no reason to publish
        # the server's directory layout to answer "where do my files go?".
        corpus_path=str(cfg.get("corpus", {}).get("path", "") or ""),
    )


@app.get("/audit/summary", dependencies=[Depends(_enforce_rate_limit), Depends(require_api_key)])
async def audit_summary(request: Request):
    """API-key-gated compliance summary over the audit log.

    Returns aggregates only — query volume, score distribution, retrieval-mode
    and model-usage breakdowns, external-LLM escalation count, and counts of
    injection findings over GitHub-sourced context text (by code, field, repo,
    and matched pattern rule). The audit log persists only SHA-256 query hashes
    (never plaintext), and an injection finding names the rule that fired rather
    than the text that fired it, so no raw query or PR content is exposed here
    either. This is operational evidence, not a formal compliance artifact or
    certification.
    """
    # _BASE_DIR / value resolves correctly whether the configured path is
    # relative or already absolute (Path.__truediv__ discards the left side
    # for an absolute right-hand operand) -- same cwd-independence _BASE_DIR
    # already guarantees for config.yaml/static/ above.
    audit_file = str(_BASE_DIR / cfg.get("logging", {}).get("audit_file", "audit.jsonl"))
    # Single off-loop pass: summarize_audit streams the JSONL through
    # compute_metrics without materializing the (unbounded) file in memory.
    return await asyncio.to_thread(summarize_audit, audit_file)


# The four /ops/* endpoints (out-of-band sync/ + agentic/ control surface) live
# in gate_ops.py as one bounded module; the security callables defined above are
# injected so auth, rate limiting, auditing, and error sanitization stay
# byte-identical to the pre-extraction handlers. gate_ops imports nothing from
# gate, so there is no import cycle, and it never imports sync/ or agentic/
# (module isolation, invariant I6). Registration decorates handlers directly on
# app — see gate_ops.py for why an APIRouter is deliberately not used here.
register_ops_routes(
    app,
    cfg=cfg,
    audit=_audit,
    enforce_rate_limit=_enforce_rate_limit,
    sanitize_error=_sanitize_error,
    require_api_key=require_api_key,
)

# Stage 2+3 of docs/AUTHENTICATION_DESIGN.md: /auth/login, /auth/logout,
# /auth/whoami. Same registration-function shape as register_ops_routes
# above, for the same reason -- gate_auth never imports anything gate.py
# doesn't already inject into it. auth_manager is None when auth.enabled is
# false; every handler in gate_auth.py checks for that and returns 503.
# Stage 3 attaches require_session_or_token to /query ONLY when a manager
# exists -- attaching the named closure while auth is off would 503 every
# query and, worse, flip the bind-guard probe on a no-op.
require_identity = register_auth_routes(
    app,
    cfg=cfg,
    audit=_audit,
    enforce_rate_limit=_enforce_rate_limit,
    auth_manager=auth_manager,
)
if auth_manager is not None:  # pragma: no cover - import-time; shipped/tests leave auth_manager None
    attach_identity_to_query(app, require_identity)
# attach_identity_to_query is dependency-shape-agnostic despite its name -- it
# just appends a parameterless dependency to POST /query. Calling it again with
# the cross-site check closes the CSRF/same-origin gap /query had relative to
# every /auth/* route (see _reject_cross_site_query's docstring).
#
# OUTSIDE the auth branch above, unlike require_session_or_token: a cross-site
# check is not a credential, so it has nothing to 503 about when auth is off,
# and the shipped default (auth.enabled false) is exactly the configuration
# that was carrying no check at all. It is also safe for the bind guard, where
# the named closure was not: _request_path_enforcement_active matches by NAME
# (_AUTH_DEPENDENCY_NAME) and this callable is not that name, so attaching it
# on the disabled default cannot false-open a LAN bind.
#
# Ordering: each call inserts at index 0 of the dependant tree, so keeping this
# call LAST is what makes the cross-site check run BEFORE require_identity --
# the same ordering gate_auth.py's own routes use (_enforce_same_origin
# declared ahead of the identity dependency). Note /query's rate limiter is an
# inline await in the handler body, not a route dependency, so this check runs
# ahead of it; that was already true whenever auth was on, and failed-auth is
# still limited because require_session_or_token charges the limiter itself on
# its rejection path.
attach_identity_to_query(app, _reject_cross_site_query)

# Optional memory admin surface (default-off). gate_memory lazy-imports memory.*
# inside handlers only — same registration-injection shape as ops/auth.
register_memory_routes(
    app,
    cfg=cfg,
    audit=_audit,
    enforce_rate_limit=_enforce_rate_limit,
    require_api_key=require_api_key,
)


_ALLOW_NON_LOOPBACK_ENV = "CYCLAW_ALLOW_NON_LOOPBACK_BIND"


# Headers a reverse proxy adds when it forwards a request. Their PRESENCE is the
# signal, not their value: a proxy on this host makes every remote caller look
# like a loopback peer, so the peer check alone would hand the api_key_optional
# bypass to the whole internet. The values are attacker-controlled and are
# deliberately never parsed or trusted here -- only "did something forward this".
_FORWARDING_HEADERS = (
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-real-ip",
    "forwarded",
)


def _looks_cross_site(request: Request) -> bool:
    """True when a browser says this request came from another site.

    CORS does NOT protect these routes. A bodyless cross-origin POST is a
    "simple request": no preflight is sent, so it REACHES the handler and its
    side effect happens; CORSMiddleware only withholds the response from the
    attacker's script afterwards. Verified against the live app -- an
    ``Origin: https://evil.example`` POST to /soul/reload returned 200 and
    invoked personality.reload().

    Until now the Bearer key WAS the CSRF defense for /soul/* and /ops/*: a page
    cannot attach an Authorization header to a simple request, and adding one
    forces a preflight the browser then blocks. api_key_optional removes that
    key, so this check has to replace what it was implicitly providing.

    Absent headers are ALLOWED, matching harness/server.py's
    _enforce_same_origin: curl and PowerShell send neither, and a non-browser
    client is not a CSRF vector. Every browser capable of mounting this attack
    sends at least Origin on a cross-origin POST.

    The Origin arm is gate_auth.py's _enforce_same_origin predicate, expressed
    as a bool instead of a raise. It used to ask only "is the Origin's host
    loopback?", which was wrong in BOTH directions. Too lax: it ignored the
    port outright, so ``Origin: http://127.0.0.1:9999`` passed as same-site --
    the very same-host-different-port ride _reject_cross_site_query exists to
    stop, open to any browser that sends Origin without Sec-Fetch-Site. Too
    strict: it called a LAN-served console cross-site, even though
    docs/THREAT_MODEL.md documents an auth+TLS path to a non-loopback bind and
    security.allowed_hosts ships LAN entries for exactly that deployment.
    Comparing against THIS request's own host/port/scheme answers both, and is
    the question a same-origin check was always asking.
    """
    site = request.headers.get("sec-fetch-site")
    if site is not None and site not in {"same-origin", "none"}:
        return True
    origin = request.headers.get("origin")
    if origin is None:
        return False
    try:
        parsed = urlparse(origin)
    except ValueError:
        # A structurally malformed Origin (e.g. an unbalanced IPv6
        # bracket: "http://[evil") makes urlparse() itself raise, not
        # merely a lazy .hostname/.port access -- attacker-controlled on
        # an unauthenticated route, so this must fail closed as
        # cross-site rather than let the exception escape as a 500.
        return True
    try:
        # .port is a lazy property, so a well-formed urlparse() result can
        # still raise here on a non-numeric or out-of-range port string
        # ("http://localhost:notaport", "http://localhost:99999"). Same
        # attacker-controlled input, same reason not to let it become a 500.
        origin_port = parsed.port
    except ValueError:
        # Reject outright rather than falling back to None. None is not a
        # neutral value here -- it is what request.url.port ITSELF reads as
        # whenever the server was reached on a scheme-default port, so a None
        # fallback would make "http://host:notaport" compare EQUAL to the
        # target and pass as same-origin. Verified: it did.
        return True
    # Allow-list membership is a second, INDEPENDENT condition, not a
    # substitute for the host comparison: allowed_hosts ships two distinct LAN
    # machines, so membership alone would call a page served by one of them
    # same-origin with a CyClaw running on the other -- the "another device on
    # the LAN" adversary. It still earns its place, because
    # TrustedHostMiddleware honours a "*" entry by skipping Host validation
    # entirely, and an Origin of "null" parses to hostname None.
    #
    # _allowed_hosts, the module global TrustedHostMiddleware itself was
    # constructed from, NOT a fresh cfg read: one source of truth for "which
    # hosts may reach this server" keeps the Origin arm and the Host filter
    # from ever disagreeing, and a cfg read would answer from a config the
    # middleware is not enforcing.
    same_origin = (
        parsed.hostname is not None
        and parsed.hostname == request.url.hostname
        and _host_matches_allow_list(parsed.hostname)
        and origin_port == request.url.port
        and parsed.scheme == request.url.scheme
    )
    return not same_origin


def _host_matches_allow_list(hostname: str) -> bool:
    """Does ``hostname`` satisfy security.allowed_hosts the way the middleware does?

    Mirrors starlette's TrustedHostMiddleware rule -- an exact match, or a
    leading ``*.`` domain wildcard matched by suffix. A plain ``in`` test is
    stricter than the middleware that already admitted the request, so an
    operator who allow-lists ``*.example.com`` and is served at
    ``node.example.com`` would have their own console called cross-site.

    One deliberate divergence: a bare ``"*"`` never matches here, even though
    it makes the middleware skip Host validation altogether. That is exactly
    why -- with the Host unvalidated there is nothing for an Origin to be
    compared against, so this check has to refuse rather than accept a pair
    that is equally unvalidated on both sides.
    """
    for pattern in _allowed_hosts:
        if pattern == "*":
            continue
        if hostname == pattern or (pattern.startswith("*.") and hostname.endswith(pattern[1:])):
            return True
    return False


def _api_key_bypass_allowed(request: Request) -> bool:
    """Whether security.api_key_optional may skip the key for THIS request.

    Every condition is necessary; each closes a hole the previous ones left:
      * the flag is set at all;
      * the socket peer is loopback -- a bind check cannot cover a directly
        served app (the container's own CMD is `uvicorn gate:app --host 0.0.0.0`);
      * no reverse-proxy forwarding header -- a proxy on this host makes every
        remote caller present a loopback peer;
      * not cross-site -- a page the operator visits is a loopback peer too, and
        a CORS-simple POST executes before CORS withholds the response.
    """
    if not _api_key_gate_bypassed():
        return False
    if not _is_loopback_peer(request):
        return False
    if _looks_proxied(request):
        return False
    return not _looks_cross_site(request)


def _looks_proxied(request: Request) -> bool:
    """True when any reverse-proxy forwarding header is present."""
    return any(header in request.headers for header in _FORWARDING_HEADERS)


def _is_loopback_peer(request: Request) -> bool:
    """True when this request arrived from this machine.

    Keyed on the socket peer, NOT on the Host header and NOT on the bind
    address. Both alternatives fail here: a Host header is attacker-supplied
    (TrustedHostMiddleware is a DNS-rebinding control, not authentication), and
    the bind is unknown to a request handler -- ``_require_loopback_bind`` only
    runs under ``main()``, which the container's ``uvicorn gate:app --host
    0.0.0.0`` never calls.

    On X-Forwarded-For: ``_serve`` passes ``proxy_headers=False``, so under the
    documented entry point this is the real peer. When the app is served
    directly, uvicorn's default ``forwarded_allow_ips="127.0.0.1"`` rewrites
    ``scope["client"]`` from XFF only when the ACTUAL peer is already loopback
    -- so a remote caller cannot spoof its way into looking local, and a
    loopback caller spoofing a remote value only makes this stricter. An
    operator who runs with ``--forwarded-allow-ips='*'`` has delegated peer
    identity to their proxy by choice; that is outside what this can assert.

    A missing ``client`` (an ASGI scope without one) reads as NOT loopback:
    this backs a security bypass, so an unknown peer fails closed.
    """
    peer = request.client
    if peer is None:
        return False
    return _is_loopback_host(peer.host or "")


def _is_loopback_host(host: str) -> bool:
    """True if binding ``host`` reaches only this machine.

    ``import ipaddress`` is local for the same reason ``_is_port_in_use``'s
    ``import socket`` is: this runs once at startup and the module-level import
    block above is deliberately ordered around the telemetry-kill guard.

    Accepts the whole 127.0.0.0/8 range and ``::1`` rather than only the literal
    "127.0.0.1", so ``127.0.0.2`` is not refused for no reason. That makes it a
    superset of config-guard's C4 literal set -- anything C4 accepts, this
    accepts. An empty host is NOT loopback: uvicorn reads "" as all interfaces.
    """
    import ipaddress

    if not host:
        return False
    if host == "localhost":  # DevSkim: ignore DS162092,DS137138 - loopback name by design
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # A hostname we cannot resolve to a literal address. Refuse rather than
        # guess -- resolving it here would make the bind decision depend on DNS.
        return False


def _flag_is_true(section: object, key: str) -> bool:
    """True only when ``section[key]`` is the literal boolean ``True``.

    Deliberately NOT ``bool(...)``. This backs a security gate, and every
    non-empty string is truthy in Python -- so an operator who writes
    ``enabled: "false"`` in config.yaml (quoted, which YAML parses as the
    STRING "false", not the boolean) would otherwise read as enabled and open
    the gate they were trying to keep shut. Unquoted ``true``/``yes``/``on``
    all parse to the literal ``True`` PyYAML gives us here, so the ordinary
    ways of writing it still work; anything else fails closed.
    """
    if not isinstance(section, dict):
        return False
    value = section.get(key)
    if value is not True and value is not False and value is not None:
        logger.warning(
            "config: %s is %r (%s), not a boolean -- treating it as OFF. "
            "Write it unquoted (%s: true) if you meant to enable it.",
            key, value, type(value).__name__, key,
        )
    return value is True


def _auth_and_tls_enabled() -> bool:
    """True when config.yaml's ``auth.enabled`` and ``api.tls.enabled`` are both set.

    A config READ of operator INTENT, not a safety guarantee -- which is
    exactly why ``_require_loopback_bind`` does not act on it alone. It backs
    docs/AUTHENTICATION_DESIGN.md §7's rule ("a non-loopback bind is allowed
    when authentication is enabled and TLS is enabled"), but a boolean in a
    file does not put a credential in front of ``/query`` (Stage 3) or TLS on
    the socket (Stage 4). ``_request_path_enforcement_active`` is the half
    that checks whether the intent was actually delivered.

    Reads fail closed on any malformed shape (a non-dict ``auth``/``api.tls``)
    rather than raising, matching the rest of this module's config access.
    """
    api_cfg = cfg.get("api") or {}
    tls_cfg = api_cfg.get("tls") if isinstance(api_cfg, dict) else None
    return _flag_is_true(cfg.get("auth") or {}, "enabled") and _flag_is_true(tls_cfg, "enabled")


def _api_key_gate_bypassed() -> bool:
    """True when ``security.api_key_optional`` disables the CYCLAW_API_KEY gate.

    Same literal-``True`` discipline as ``_flag_is_true`` above, and for the
    same reason: this backs a bind-time security decision, so a quoted
    ``"false"`` must not read as enabled.
    """
    return _flag_is_true(cfg.get("security") or {}, "api_key_optional")


# gate_auth.register_auth_routes exports this dependency for Stage 3 to attach
# to /query. Matched by NAME rather than by identity because it is a closure
# built inside that function and gate.py never holds a reference to it -- the
# same name-matching approach .claude/skills/invariant-guard uses to assert
# structural properties it cannot import.
_AUTH_DEPENDENCY_NAME = "require_session_or_token"


def _request_path_enforcement_active() -> bool:
    """True when ``/query`` actually carries an authentication dependency.

    A capability probe, not a config read. ``auth.enabled: true`` states an
    intention; this answers whether the intention was delivered. Stage 3
    attaches ``require_session_or_token`` to ``/query`` only when
    ``auth_manager`` is constructed (literal ``auth.enabled: true``). The
    shipped default leaves that manager None, so this stays False and the
    auth+TLS route past loopback cannot open on an unmodified config. Do not
    hard-code this True.
    """
    for route in app.routes:
        if getattr(route, "path", None) != "/query":
            continue
        dependant = getattr(route, "dependant", None)
        if dependant is None:
            return False
        return _AUTH_DEPENDENCY_NAME in _dependant_call_names(dependant)
    return False


def _dependant_call_names(root: object) -> set[str]:
    """Every callable name in a FastAPI dependant tree, including nested ones.

    Iterative rather than recursive: a dependency graph is operator-shaped
    data, and a stack cannot blow the interpreter's recursion limit on a
    deeply nested one.
    """
    names: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        name = getattr(getattr(node, "call", None), "__name__", None)
        if name:
            names.add(name)
        stack.extend(getattr(node, "dependencies", []))
    return names


def _require_loopback_bind(host: str) -> bool:
    """Refuse to serve on a non-loopback address unless explicitly opted in.

    docs/THREAT_MODEL.md scopes CyClaw as single-operator and loopback-bound,
    and `.claude/skills/config-guard/check_config.py`'s C4 already fails a
    non-loopback ``api.host``. But C4 is a CI check: it only ever sees config
    that was committed and pushed. An operator who edits ``api.host`` in their
    working copy and runs ``python gate.py`` reaches no check at all, and the
    consequence is not subtle -- ``/query``, ``/health``, ``/`` and ``/static/*``
    carry no authentication, so a non-loopback bind publishes the whole corpus
    (via answers) and the local model to anything that can route to the port.
    ``security.allowed_hosts`` is not a backstop here either: it ships with real
    LAN addresses alongside the loopback names, so TrustedHostMiddleware would
    admit those Hosts rather than reject them.

    This is the runtime half of that same rule. It gates ``main()`` only -- the
    container's ``CMD`` runs ``uvicorn gate:app --host 0.0.0.0`` directly and
    never calls this, which is correct: there the bind is in-container and
    docker-compose owns exposure by publishing ``127.0.0.1:8787:8787``. Running
    uvicorn by hand outside a container likewise bypasses this; the guard covers
    the documented entry points (``python gate.py`` / ``cyclaw-server``), not
    every conceivable way to import the app.

    Three ways past loopback, checked in this order: (1) auth+TLS configured
    AND the request path demonstrably enforcing a credential -- the durable
    path docs/AUTHENTICATION_DESIGN.md §7 designs toward, so a LAN operator is
    not stuck carrying an env var forever once those stages ship; (2) the env
    var below -- a deliberate escape hatch for someone fronting CyClaw with
    their own reverse proxy/auth today; (3) neither -- refuse.

    (1) deliberately requires BOTH the config flags and
    ``_request_path_enforcement_active``. The flags alone are a statement of
    intent, and a warning is not a control: an operator who sets
    ``auth.enabled: true`` reasonably believes they turned authentication on,
    which is precisely the belief that must not be allowed to open a LAN bind
    while Stage 3 leaves ``/query`` unauthenticated. Checking the delivered
    capability instead means this route past loopback simply cannot open
    today, and opens by itself when the stage that makes it true ships.

    (1) additionally requires that ``security.api_key_optional`` is NOT set.
    That flag governs a different credential from the one this route checks:
    ``_request_path_enforcement_active`` proves ``/query`` carries a session,
    but ``api_key_optional: true`` simultaneously removes the CYCLAW_API_KEY
    gate from ``/soul/*``, ``/ops/*``, ``/memory/*`` and ``/audit/summary``.
    Without this clause the two flags compose into the exact hole the rest of
    this docstring exists to prevent: a LAN bind admitted on the strength of
    ``/query``'s session while soul mutation and the ``/ops/*`` subprocess
    shims sit open to anything that can route to the port. config-guard's C13
    warns on the same combination, but by this module's own standard above a
    warning is not a control -- this is the control.

    Returns True when it is safe to proceed.
    """
    if _is_loopback_host(host):
        return True
    if _auth_and_tls_enabled() and _request_path_enforcement_active() and not _api_key_gate_bypassed():
        if auth_manager is not None and auth_manager.needs_password_setup():
            print(
                "\nRefusing to bind beyond loopback: auth is enabled but the "
                "admin password is not set yet. Set it from this machine "
                f"(terminal, harness, or `cyclaw-user passwd {BOOTSTRAP_USERNAME}`) "
                "before a LAN bind.\n"
            )
            return False
        logger.warning(
            "Binding %s — beyond loopback, allowed because auth.enabled and "
            "api.tls.enabled are both set and /query enforces a credential. "
            "Confirm docs/AUTHENTICATION_DESIGN.md's Stage 4 (TLS wiring into "
            "uvicorn) has also shipped before treating traffic to %s as "
            "encrypted; this guard can prove the credential, not the socket.",
            host, host,
        )
        return True
    if os.environ.get(_ALLOW_NON_LOOPBACK_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        logger.warning(
            "Binding %s — beyond loopback, allowed only because %s is set. "
            "CyClaw has no authentication on /query, /health, / or /static/*.",
            host, _ALLOW_NON_LOOPBACK_ENV,
        )
        return True
    print(
        f"\nRefusing to start: api.host is {host!r}, which is not a loopback address.\n"
        "\n"
        "CyClaw serves /query, /health, / and /static/* with no authentication, so\n"
        "binding beyond loopback exposes your corpus and your local model to every\n"
        "host that can reach this port. docs/THREAT_MODEL.md scopes CyClaw as\n"
        "single-operator and loopback-bound.\n"
        "\n"
        "Set api.host back to 127.0.0.1 in config.yaml.\n"  # DevSkim: ignore DS162092 - loopback host by design
        "\n"
        "Setting auth.enabled and api.tls.enabled to true is not enough on its\n"
        "own: this guard also checks that /query actually enforces a credential\n"
        "(Stage 3 attaches require_session_or_token only when auth.enabled is the\n"
        "literal boolean true). Both flags plus that attachment allow this bind\n"
        "with no override needed.\n"
        "\n"
        "That auth+TLS route is ALSO refused while security.api_key_optional is\n"
        "true: that flag removes the CYCLAW_API_KEY gate from /soul/*, /ops/*,\n"
        "/memory/* and /audit/summary, so a session on /query would not stop a\n"
        "LAN caller reaching soul mutation or the /ops/* subprocess shims. Set\n"
        "security.api_key_optional back to false to use that route.\n"
        f"If the exposure is deliberate today, set {_ALLOW_NON_LOOPBACK_ENV}=1 and\n"
        "put your own authentication in front of it first.\n",
        file=sys.stderr,
    )
    return False


def _is_port_in_use(host: str, port: int) -> bool:
    """Return True if a TCP listener already holds ``host:port``.

    Used to detect a stale/duplicate CyClaw before binding, so a double-clicked
    launch can print a clear message instead of dying on OSError [WinError 10048].
    """
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def _tls_ssl_kwargs() -> tuple[dict[str, str] | None, str | None]:
    """Return (uvicorn ssl kwargs, error). None kwargs means TLS is off.

    Fail-closed: enabled + missing/unreadable files returns an error string
    and no kwargs, so ``_serve`` never starts a plaintext socket while
    cookies would be marked Secure.
    """
    api_cfg = cfg.get("api") or {}
    tls_cfg = api_cfg.get("tls") if isinstance(api_cfg, dict) else None
    if not _flag_is_true(tls_cfg, "enabled"):
        return None, None
    if not isinstance(tls_cfg, dict):  # pragma: no cover - _flag_is_true already rejects non-mappings
        return None, "api.tls.enabled is true but api.tls is not a mapping"
    cert_raw = tls_cfg.get("certfile")
    key_raw = tls_cfg.get("keyfile")
    if not isinstance(cert_raw, str) or not cert_raw.strip() or not isinstance(key_raw, str) or not key_raw.strip():
        return None, "api.tls.enabled is true but certfile/keyfile are missing"
    cert = Path(cert_raw).expanduser()
    key = Path(key_raw).expanduser()
    if not cert.is_absolute():
        cert = _BASE_DIR / cert
    if not key.is_absolute():
        key = _BASE_DIR / key
    for label, path in (("certfile", cert), ("keyfile", key)):
        if not path.is_file():
            return None, f"api.tls.{label} does not exist or is not a file: {path}"
        try:
            with path.open("rb") as handle:
                handle.read(1)
        except OSError as exc:
            return None, f"api.tls.{label} is not readable: {path} ({exc})"
    return {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}, None


def _serve(host: str, port: int) -> None:
    """Thin wrapper over ``uvicorn.run`` — kept separate so tests can patch the
    serve step without standing up a real server."""
    import uvicorn

    # proxy_headers=False: uvicorn defaults it to True with forwarded_allow_ips
    # "127.0.0.1", so on a loopback deployment EVERY peer is trusted and
    # ProxyHeadersMiddleware rewrites scope["client"] from an attacker-supplied
    # X-Forwarded-For. That is the value _enforce_rate_limit keys its per-IP
    # bucket on, so any local process could mint a fresh 60/min budget per
    # request just by varying the header. CyClaw sits behind no reverse proxy —
    # the real peer is always the right answer here.
    run_kwargs: dict = {
        "app": app,
        "host": host,
        "port": port,
        "proxy_headers": False,
    }
    ssl_kwargs, tls_error = _tls_ssl_kwargs()
    if tls_error:
        print(f"\nRefusing to start: {tls_error}\n", file=sys.stderr)
        return
    if ssl_kwargs:
        run_kwargs.update(ssl_kwargs)
    uvicorn.run(**run_kwargs)  # DevSkim: ignore DS162092 - loopback-only binding by design


def _hold_console() -> None:
    """Keep a double-clicked console window open long enough to read a message.

    No-op when stdin is not a TTY (CI, piped, service launch) so it never blocks
    automated runs.
    """
    try:
        if sys.stdin and sys.stdin.isatty():
            input("Press Enter to close...")
    except (EOFError, KeyboardInterrupt):
        # Nothing to do: the prompt only exists to hold the window open. A closed
        # stdin (EOFError) or an impatient Ctrl-C (KeyboardInterrupt) both mean
        # "stop waiting and exit" — swallow them so shutdown stays clean.
        pass


def main() -> None:
    """Console entry point for ``cyclaw-server`` (see pyproject [project.scripts]).

    Serves the FastAPI app on the loopback host/port from config.yaml. Wraps the
    serve call so that a double-clicked launch (Windows) never vanishes on an
    unhandled traceback: a port already in use prints an actionable message and
    holds the window, and KeyboardInterrupt exits cleanly.
    """
    api_cfg = cfg.get("api", {})
    host = api_cfg.get("host", "127.0.0.1")  # DevSkim: ignore DS162092 - loopback-only binding by design
    port = api_cfg.get("port", 8787)

    # Before the port probe, not after: a non-loopback host should be refused on
    # its own terms, not reported as "something is already listening".
    if not _require_loopback_bind(host):
        _hold_console()
        return

    if _is_port_in_use(host, port):
        print(
            f"\nCyClaw may already be running on {host}:{port}.\n"
            "Close the other window, or wait ~30 s for the port to release, then try again."
        )
        _hold_console()
        return

    try:
        _serve(host, port)
    except KeyboardInterrupt:
        print("\nCyClaw stopped.")
    except OSError as e:
        print(f"\nFailed to start CyClaw: {_sanitize_error(e)}")
        _hold_console()


if __name__ == "__main__":
    main()
