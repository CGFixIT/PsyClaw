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
  - enhance ci or create explicit logging to ensure soul is injected to appropriate chat queries-first just test by adding a unique non llm training fact and it should say vault miss but replies correct-thats the lazy version to start but ci long term if metrics not already  
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
from contextlib import asynccontextmanager
from pathlib import Path

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
from utils.telemetry_kill import apply_telemetry_kill

_TELEMETRY_KILL = apply_telemetry_kill()

_verified = {k: os.environ.get(k, "NOT SET") for k in _TELEMETRY_KILL}
print("[TELEMETRY KILL] Verified env state:")
for k, v in _verified.items():
    # Compare against the expected kill value, not a generic "non-empty" check.
    # CHROMA_OTEL_* are intentionally set to "" to disable them; the old
    # `v not in ("", "NOT SET")` check marked them MISSING on every startup.
    status = "OK" if v == _TELEMETRY_KILL[k] else "MISSING"
    print(f"  {status}  {k}={v}")

from importlib.metadata import version as _pkg_version, PackageNotFoundError
try:
    _CYCLAW_VERSION = _pkg_version("cyclaw")
except PackageNotFoundError:
    _CYCLAW_VERSION = "dev"

import logging
import yaml
from fastapi import FastAPI, HTTPException, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from graph import build_graph, GraphState
from retrieval.hybrid_search import HybridRetriever
from llm.client import ClaudeClient, LocalLLMClient, GrokClient
from schemas.api import (
    QueryRequest, QueryResponse, SourceInfo, HealthResponse, SoulEvolutionRequest,
)
from utils.logger import audit_log, setup_logging
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from utils.sanitizer import check_input
from utils.errors import (
    PromptInjectionError, IndexNotFoundError
)
from utils.guardrail_bridge import build_input_guard, build_output_guard
from utils.health import check_all, close_http_client
from utils.personality import PersonalityManager
from utils.authn_manager import AuthManager, BOOTSTRAP_USERNAME
from gate_ops import register_ops_routes
from gate_auth import register_auth_routes
from metrics import summarize_audit

_bearer_scheme = HTTPBearer(auto_error=False)

def require_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
):
    # Fail-closed (PR #99 #4). Previously, when CYCLAW_API_KEY was unset the
    # server ran in "open mode" and require_api_key was a no-op, leaving every
    # /soul/* mutation endpoint unauthenticated. Now, if no key is configured the
    # endpoint is REFUSED rather than left open — no key is generated, logged, or
    # stored. Set CYCLAW_API_KEY to enable soul mutations.
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
    validate_fallback_confirm_placeholder,
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


async def _check_rate_limit_async(client_ip: str) -> bool:
    """Offload check_rate_limit to a worker thread.

    RateLimiter.allow() takes an internal lock and (when api.rate_limit
    persistence is configured) performs a sqlite/Postgres write. Off-loop is
    cheap and prevents persistence-mode head-of-line blocking; the in-memory
    default path also benefits because the lock-protected critical section no
    longer holds the event-loop thread.
    """
    return await asyncio.to_thread(check_rate_limit, client_ip)


async def _enforce_rate_limit(request: Request) -> None:
    """Audit and raise HTTP 429 when the caller's per-IP budget is spent.

    Single enforcement point for every rate-limited endpoint (/query, /soul/*,
    /audit/summary, /ops/*). The 429 detail interpolates the configured
    api.rate_limit values — a hardcoded "(60/min)" here misled operators who
    tuned max_requests/window_seconds away from the defaults.
    """
    client_ip = request.client.host if request.client else "unknown"
    if not await _check_rate_limit_async(client_ip):
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

if not os.environ.get("CYCLAW_API_KEY", ""):
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

try:
    retriever = HybridRetriever()
except IndexNotFoundError as e:
    print(f"FATAL: {e.message}", file=sys.stderr)
    print("Run: python -m retrieval.indexer", file=sys.stderr)
    logger.critical("Retrieval index not found at startup: %s", e.message)
    retriever = None

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
if cfg.get("app", {}).get("mode") == "hybrid" and cfg["models"].get("grok", {}).get("enabled", False):
    grok = GrokClient(cfg=cfg)

claude = None
# Same double-gate as grok above, mirrored for the Claude fallback (PR #441).
if cfg.get("app", {}).get("mode") == "hybrid" and cfg["models"].get("claude", {}).get("enabled", False):
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

def _confirm_choices(providers: list[str]) -> str:
    """The 'here are your options' half of a confirm prompt, provider-accurate."""
    if not providers:
        return (
            "No external provider is available (offline mode, provider disabled, "
            "or its API key is unset), so the only option is Offline Best Effort."
        )
    labels = [_PROVIDER_LABELS[p] for p in providers]
    return "Choose Offline Best Effort or " + " or ".join(labels) + "."

personality = None
if cfg.get("personality", {}).get("enabled", False):
    personality = PersonalityManager(cfg)

# Stage 2 of docs/AUTHENTICATION_DESIGN.md. auth_manager stays None (the
# request path below never constructs it) unless auth.enabled is true --
# matching every other CyClaw subsystem's disabled-by-default convention.
# gate_auth.py's routes always exist regardless (see its own docstring for
# why), but every handler checks for None first and returns 503.
auth_manager = None
if cfg.get("auth", {}).get("enabled", False):
    auth_manager = AuthManager(cfg)
    # bootstrap_if_empty() is a no-op on every boot after the first: it only
    # ever acts when the users table is genuinely empty. The password is
    # returned exactly once here and never persisted in plaintext or logged
    # -- only hash_password()'s output reaches the database. This is why
    # auth.enabled defaults false: turning it on is the one moment an
    # operator MUST be watching this console output.
    _bootstrap_password = auth_manager.bootstrap_if_empty()
    if _bootstrap_password:
        print(
            f"\n{'=' * 70}\n"
            f"CyClaw authentication is now enabled. A first account was created:\n"
            f"\n"
            f"    username: {BOOTSTRAP_USERNAME}\n"
            f"    password: {_bootstrap_password}\n"
            f"\n"
            f"This password is shown ONCE and is not stored anywhere in plaintext.\n"
            f"Save it now, then log in and consider `cyclaw-user passwd "
            f"{BOOTSTRAP_USERNAME}` to set your own.\n"
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

compiled_graph = None
if retriever is not None:
    compiled_graph = build_graph(
        retriever=retriever, llm=local_llm, grok=grok, claude=claude, cfg=cfg,
        personality=personality, input_guard=input_guard, output_guard=output_guard,
    )

@app.post("/query", response_model=QueryResponse)
async def query_endpoint(request: Request, req: QueryRequest):
    await _enforce_rate_limit(request)

    if compiled_graph is None:
        raise HTTPException(
            status_code=503,
            detail={"error": "Index not built. Run: python -m retrieval.indexer",
                    "code": "INDEX_NOT_FOUND"}
        )

    try:
        check_input(req.query)
    except PromptInjectionError as e:
        # Pass the full query: audit_log() SHA-256-hashes the "query" field, so
        # truncating here yields a hash of only the first 50 chars that diverges
        # from the canonical full-query hash written by the graph audit node and
        # the MCP path. No raw text is persisted either way.
        await _audit({"event": "prompt_injection_blocked", "query": req.query})
        raise HTTPException(
            status_code=400,
            detail={"error": e.message, "code": e.code, "details": e.details}
        ) from e

    initial_state: GraphState = {
        "query": req.query,
        "user_confirmed_online": req.user_confirmed_online,
        "online_provider": req.online_provider,
    }

    # Overall server-side deadline: a stalled Ollama / retrieval must not hold
    # the request (and a worker thread) open indefinitely. The per-call LLM
    # timeouts are an inner bound; this is the outer one covering the whole graph.
    graph_timeout = cfg.get("api", {}).get("graph_timeout_sec", 660)
    try:
        result = await asyncio.wait_for(
            asyncio.to_thread(compiled_graph.invoke, initial_state),
            timeout=graph_timeout,
        )
    except TimeoutError as e:
        await _audit({"event": "graph_timeout", "query": req.query, "timeout_sec": graph_timeout})
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
        await _audit({"event": "graph_error", "query": req.query, "error": safe_msg})
        raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "GRAPH_ERROR"}) from e

    needs_confirm = result.get("needs_user_confirm", False)
    answer_model = result.get("answer_model", "")

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
        choices = _confirm_choices(providers)
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
        await _audit({"event": "skipped_sources", "query": req.query,
                       "skipped_count": skipped_sources,
                       "total_sources": len(docs)})

    return QueryResponse(
        answer=result.get("answer", "[No answer generated]"),
        sources=sources,
        retrieval_mode=result.get("retrieval_mode", "none"),
        hit_count=len(result.get("retrieved_docs", [])),
        model_used=result.get("answer_model", "unknown"),
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
        await _audit({"event": "soul_apply_injection_blocked", "reason": req.reason})
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

@app.get("/health", response_model=HealthResponse)
async def health():
    statuses = await asyncio.to_thread(check_all)
    return HealthResponse(
        status="ok" if all(s.healthy for s in statuses) else "degraded",
        services={s.name: {"healthy": s.healthy, "latency_ms": s.latency_ms, "error": s.error} for s in statuses},
        index_ready=retriever is not None,
        graph_ready=compiled_graph is not None,
        mode=cfg["app"]["mode"],
        graph_timeout_sec=cfg.get("api", {}).get("graph_timeout_sec", 660),
        version=_CYCLAW_VERSION,
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

# Stage 2 of docs/AUTHENTICATION_DESIGN.md: /auth/login, /auth/logout,
# /auth/whoami. Same registration-function shape as register_ops_routes
# above, for the same reason -- gate_auth never imports anything gate.py
# doesn't already inject into it. auth_manager is None when auth.enabled is
# false; every handler in gate_auth.py checks for that and returns 503.
register_auth_routes(
    app,
    cfg=cfg,
    audit=_audit,
    enforce_rate_limit=_enforce_rate_limit,
    auth_manager=auth_manager,
)


_ALLOW_NON_LOOPBACK_ENV = "CYCLAW_ALLOW_NON_LOOPBACK_BIND"


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


# gate_auth.register_auth_routes exports this dependency for Stage 3 to attach
# to /query. Matched by NAME rather than by identity because it is a closure
# built inside that function and gate.py never holds a reference to it -- the
# same name-matching approach .claude/skills/invariant-guard uses to assert
# structural properties it cannot import.
_AUTH_DEPENDENCY_NAME = "require_session_or_token"


def _request_path_enforcement_active() -> bool:
    """True when ``/query`` actually carries an authentication dependency.

    A capability probe, not a config read, and the reason the auth+TLS bind
    path is safe to have landed before Stage 3 exists. ``auth.enabled: true``
    states an intention; this answers whether the intention was delivered. As
    of this commit Stage 3 has not shipped -- ``/query`` is registered with no
    ``dependencies=[...]`` -- so this returns False and the auth+TLS route
    past loopback cannot open, no matter what the two booleans say. It starts
    returning True on its own the moment Stage 3 attaches the dependency; no
    edit here is required, and none should be made to force it.
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

    Returns True when it is safe to proceed.
    """
    if _is_loopback_host(host):
        return True
    if _auth_and_tls_enabled() and _request_path_enforcement_active():
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
        "own: this guard also checks that /query actually enforces a credential,\n"
        "which docs/AUTHENTICATION_DESIGN.md's Stage 3 has not yet delivered. Once\n"
        "it has, those two flags allow this bind with no override needed.\n"
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
    uvicorn.run(app, host=host, port=port, proxy_headers=False)  # DevSkim: ignore DS162092 - loopback-only binding by design


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
