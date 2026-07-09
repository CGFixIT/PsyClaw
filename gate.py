#!/usr/bin/env python

"""CyClaw FastAPI Gateway — HTTP/MCP entry point.

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
from contextlib import asynccontextmanager
from pathlib import Path

# Resolve all bundled resources relative to this file, not the current working
# directory. When CyClaw is launched by double-clicking gate.py (Windows) the cwd
# is not guaranteed to be the repo root, so cwd-relative opens of config.yaml /
# static/ would crash at import time and the console window would vanish before
# the traceback could be read. Anchoring to __file__ makes startup cwd-independent.
_BASE_DIR = Path(__file__).resolve().parent

_TELEMETRY_KILL = {
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    "LANGGRAPH_CLI_NO_ANALYTICS": "1",
    "ANONYMIZED_TELEMETRY": "False",
    "CHROMA_OTEL_COLLECTION_ENDPOINT": "",
    "CHROMA_OTEL_SERVICE_NAME": "",
    "OTEL_SDK_DISABLED": "true",        # kills entire OTel SDK
    "OTEL_TRACES_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
}
for k, v in _TELEMETRY_KILL.items():
    os.environ[k] = v

# Hard-remove any accidentally set API keys
for _key in ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_ENDPOINT"):
    os.environ.pop(_key, None)
    
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
from fastapi.responses import FileResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from graph import build_graph, GraphState
from retrieval.hybrid_search import HybridRetriever
from llm.client import ClaudeClient, LocalLLMClient, GrokClient
from schemas.api import (
    QueryRequest, QueryResponse, SourceInfo, HealthResponse, SoulEvolutionRequest,
    OpsSyncRequest, OpsAgenticRequest, OpsFsConnectRequest, OpsSqlConnectRequest,
)
from utils.logger import audit_log, setup_logging
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from utils.sanitizer import check_input
from utils.errors import (
    PromptInjectionError, IndexNotFoundError
)
from utils.health import check_all
from utils.personality import PersonalityManager
# Subprocess shim for the out-of-band sync/ + agentic/ control surface. This is a
# subprocess wrapper ONLY — it never imports sync/ or agentic/, so gate.py's
# out-of-band isolation invariant is preserved (see utils/ops_runner.py).
from utils.ops_runner import run_sync_op, run_agentic_op, run_fsconnect_op, run_sqlconnect_op, OpsError
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
    if not credentials or not hmac.compare_digest(credentials.credentials, api_key):
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
from utils.config_validation import validate_personality_config, validate_retrieval_config
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
    """
    await asyncio.to_thread(audit_log, event)


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
    for env_key in ("GROK_API_KEY", "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "SSC_TOKEN", "CYCLAW_API_KEY"):
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

_llm_timeout = cfg.get("models", {}).get("local_llm", {}).get("timeout_sec", 300)
_graph_timeout = cfg.get("api", {}).get("graph_timeout_sec", 330)
if _llm_timeout >= _graph_timeout:
    logger.warning(
        "local_llm.timeout_sec (%s) >= api.graph_timeout_sec (%s) — the graph "
        "deadline will always fire first, making the per-call LLM timeout unreachable. "
        "Lower timeout_sec or raise graph_timeout_sec.",
        _llm_timeout, _graph_timeout,
    )

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
        ("retriever", retriever),
    ]:
        if _obj is not None:
            try:
                _obj.close()
            except Exception:
                logger.warning("shutdown close failed for %s", _name, exc_info=True)


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
# Host check does. Added after CORS so it is the OUTERMOST middleware (runs first
# on each request). Host matching ignores port; the list is config-driven so an
# operator can add any name/IP they reach CyClaw by (e.g. the home-lab LAN IP).
from starlette.middleware.trustedhost import TrustedHostMiddleware
_allowed_hosts = cfg.get("security", {}).get("allowed_hosts", ["127.0.0.1", "localhost"])  # DevSkim: ignore DS162092,DS137138 - loopback host allow-list by design
app.add_middleware(TrustedHostMiddleware, allowed_hosts=_allowed_hosts)

# Security response headers middleware: sets defense-in-depth headers on every
# response (X-Content-Type-Options, X-Frame-Options, Referrer-Policy,
# Permissions-Policy) and adds Cache-Control: no-store on the root / static paths
# to prevent browser caching of the Soul Console. Added after TrustedHost so it
# runs INSIDE the host check (i.e. only on accepted requests).
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request as StarletteRequest
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
            "default-src 'none'; script-src 'self' 'unsafe-inline'; "
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
    retriever = None

# Pass the already-parsed cfg dict into both clients rather than letting them
# re-open a *relative* "config.yaml" (their default when cfg is None). That
# relative read is cwd-dependent and crashes at import when gate.py is launched
# from a non-repo-root cwd — the same fragility _BASE_DIR / the single-read above
# exist to prevent. LocalLLMClient is always built, so this hardens every mode.
local_llm = LocalLLMClient(cfg=cfg)

grok = None
if cfg["app"]["mode"] == "hybrid" and cfg["models"]["grok"].get("enabled", False):
    grok = GrokClient(cfg=cfg)

claude = None
if cfg["app"]["mode"] == "hybrid" and cfg["models"].get("claude", {}).get("enabled", False):
    claude = ClaudeClient(cfg=cfg)

personality = None
if cfg.get("personality", {}).get("enabled", False):
    personality = PersonalityManager(cfg)

compiled_graph = None
if retriever is not None:
    compiled_graph = build_graph(
        retriever=retriever, llm=local_llm, grok=grok, claude=claude, cfg=cfg, personality=personality
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

    # Overall server-side deadline: a stalled LM Studio / retrieval must not hold
    # the request (and a worker thread) open indefinitely. The per-call LLM
    # timeouts are an inner bound; this is the outer one covering the whole graph.
    graph_timeout = cfg.get("api", {}).get("graph_timeout_sec", 330)
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
                    f"retrieval likely stalled — check that LM Studio is running and that its "
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

    if needs_confirm and not answer_model:
        top_score = result.get("top_score", 0.0)
        threshold = cfg.get("retrieval", {}).get("min_score", 0.4)
        # A retrieval failure (retrieve_node caught a RAGError and set
        # state["error"] with top_score=0.0) also lands here, but it is NOT a
        # vault miss — presenting it as one hides a broken index behind a
        # routine "send to Grok?" prompt. Name the failure in the confirm
        # message (the console renders only confirm_message on this path) and
        # pass error through for API consumers, matching the answered path.
        retrieval_error = result.get("error")
        if retrieval_error:
            confirm_message = (
                f"Retrieval failed ({retrieval_error}) — no vault results available. "
                f"Choose Offline Best Effort, Send to Grok, or Send to Claude."
            )
        else:
            confirm_message = (
                f"Vault miss (best score: {top_score:.3f} < {threshold}). "
                f"Choose Offline Best Effort, Send to Grok, or Send to Claude."
            )
        return QueryResponse(
            answer="",
            sources=[],
            retrieval_mode=result.get("retrieval_mode", "none"),
            hit_count=len(result.get("retrieved_docs", [])),
            model_used="",
            needs_confirm=True,
            confirm_message=confirm_message,
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

@app.get("/soul", dependencies=[Depends(require_api_key)])
async def get_soul(request: Request):
    await _enforce_rate_limit(request)
    if personality is None:
        raise HTTPException(status_code=404, detail="Personality system not enabled")
    await _audit({"event": "soul_read", "version": personality.get_version()})
    return {
        "soul": personality.get_system_prompt_additive(),
        "version": personality.get_version(),
        "source": str(personality.soul_path)
    }

@app.post("/soul/propose", dependencies=[Depends(require_api_key)])
async def propose_soul_evolution(request: Request, req: SoulEvolutionRequest):
    await _enforce_rate_limit(request)
    if personality is None:
        raise HTTPException(status_code=404, detail="Personality system not enabled")
    proposal = await asyncio.to_thread(personality.propose_evolution, req.new_soul, req.reason)
    await _audit({"event": "soul_evolution_proposed", "reason": req.reason})
    return proposal

@app.post("/soul/apply", dependencies=[Depends(require_api_key)])
async def apply_soul_evolution(request: Request, req: SoulEvolutionRequest):
    await _enforce_rate_limit(request)
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
    return result

@app.post("/soul/reload", dependencies=[Depends(require_api_key)])
async def reload_soul(request: Request):
    await _enforce_rate_limit(request)
    if personality is None:
        raise HTTPException(status_code=404, detail="Personality system not enabled")
    await asyncio.to_thread(personality.reload)
    return {"status": "reloaded", "version": personality.get_version()}

@app.post("/soul/restore", dependencies=[Depends(require_api_key)])
async def restore_soul(request: Request):
    await _enforce_rate_limit(request)
    if personality is None:
        raise HTTPException(status_code=404, detail="Personality system not enabled")
    try:
        result = await asyncio.to_thread(personality.restore_from_backup)
        return result
    except FileNotFoundError as e:
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
        graph_timeout_sec=cfg.get("api", {}).get("graph_timeout_sec", 330),
        version=_CYCLAW_VERSION,
    )


@app.get("/audit/summary", dependencies=[Depends(require_api_key)])
async def audit_summary(request: Request):
    """API-key-gated compliance summary over the audit log.

    Returns aggregates only — query volume, score distribution, retrieval-mode
    and model-usage breakdowns, and external-LLM escalation count. The audit log
    persists only SHA-256 query hashes (never plaintext), so no raw query text is
    exposed here either. This is operational evidence, not a formal compliance
    artifact or certification.
    """
    await _enforce_rate_limit(request)
    audit_file = cfg.get("logging", {}).get("audit_file", "audit.jsonl")
    # Single off-loop pass: summarize_audit streams the JSONL through
    # compute_metrics without materializing the (unbounded) file in memory.
    return await asyncio.to_thread(summarize_audit, audit_file)


# =============================================================================
# Ops endpoints — out-of-band sync/ + agentic/ control surface (terminal panels)
# =============================================================================
# These back the Soul Console's Sync + Agentic panels. A browser cannot spawn a
# subprocess, so the gateway does — via utils/ops_runner, which is a pure
# subprocess shim. gate.py NEVER imports sync/ or agentic/, so out-of-band
# isolation (and the five security invariants that rest on it) is preserved.
#
# Every action is: loopback-only (inherited 127.0.0.1 bind + TrustedHost
# allow-list), rate-limited (shared _rate_limiter), API-key-gated
# (require_api_key — uniform with /soul/* mutations; subprocess execution is more
# sensitive than a /soul GET), and audited. A CLI that exits non-zero is reported
# inside the JSON envelope (HTTP 200) so the UI can render exit codes / stderr;
# only gateway-level problems (bad action -> 400, rate limit -> 429, launch
# failure -> 500) raise HTTP errors.
#
# The "config" block is read from the already-parsed cfg dict (NOT an import of
# sync/ or agentic/) so the UI can surface enabled/mode/writes_enabled — the two
# config-driven gates of the agentic apply checklist — authoritatively.

def _ops_sync_config() -> dict:
    s = cfg.get("sync", {}) or {}
    return {
        "enabled": bool(s.get("enabled", False)),
        "direction": s.get("direction", "pull"),
        "max_delete": s.get("max_delete"),
        "max_transfer": s.get("max_transfer"),
        "schedule": f"{int(s.get('schedule_hour', 2)):02d}:{int(s.get('schedule_min', 0)):02d}",
    }


def _ops_agentic_config() -> dict:
    a = cfg.get("agentic", {}) or {}
    return {
        "enabled": bool(a.get("enabled", False)),
        "mode": a.get("mode", "read"),
        "writes_enabled": bool(a.get("writes_enabled", False)),
        "repo": a.get("repo", ""),
    }


@app.post("/ops/sync", dependencies=[Depends(require_api_key)])
async def ops_sync(request: Request, req: OpsSyncRequest):
    await _enforce_rate_limit(request)
    try:
        result = await asyncio.to_thread(run_sync_op, req.action, dry_run=req.dry_run)
    except OpsError as e:
        await _audit({"event": "ops_sync_rejected", "action": req.action, "error": str(e)})
        raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
    except Exception as e:
        safe_msg = _sanitize_error(e)
        await _audit({"event": "ops_sync_error", "action": req.action, "error": safe_msg})
        logger.exception("Unexpected error in /ops/sync action=%r", req.action)
        raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
    await _audit({
        "event": "ops_sync_executed", "action": req.action, "dry_run": req.dry_run,
        "exit_code": result.exit_code, "label": result.label,
    })
    payload = result.to_dict()
    payload["config"] = _ops_sync_config()
    return payload


@app.post("/ops/agentic", dependencies=[Depends(require_api_key)])
async def ops_agentic(request: Request, req: OpsAgenticRequest):
    await _enforce_rate_limit(request)
    try:
        result = await asyncio.to_thread(
            run_agentic_op, req.action,
            pr=req.pr, issue=req.issue, no_diff=req.no_diff,
            name=req.name, desc=req.desc, body=req.body, reason=req.reason, confirm=req.confirm,
        )
    except OpsError as e:
        await _audit({"event": "ops_agentic_rejected", "action": req.action, "error": str(e)})
        raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
    except Exception as e:
        safe_msg = _sanitize_error(e)
        await _audit({"event": "ops_agentic_error", "action": req.action, "error": safe_msg})
        logger.exception("Unexpected error in /ops/agentic action=%r", req.action)
        raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
    await _audit({
        "event": "ops_agentic_executed", "action": req.action,
        "exit_code": result.exit_code, "label": result.label,
    })
    payload = result.to_dict()
    payload["config"] = _ops_agentic_config()
    return payload


def _ops_fsconnect_config() -> dict:
    f = cfg.get("fsconnect", {}) or {}
    return {
        "enabled": bool(f.get("enabled", False)),
        "allowed_roots": f.get("allowed_roots", []) or [],
        "writes_enabled": bool(f.get("writes_enabled", False)),
        "max_file_bytes": f.get("max_file_bytes", 5242880),
    }


def _ops_sqlconnect_config() -> dict:
    s = cfg.get("sqlconnect", {}) or {}
    return {
        "enabled": bool(s.get("enabled", False)),
        "driver": s.get("driver", "postgres"),
        "read_only": bool(s.get("read_only", True)),
        "max_rows": s.get("max_rows", 1000),
    }


@app.post("/ops/fsconnect", dependencies=[Depends(require_api_key)])
async def ops_fsconnect(request: Request, req: OpsFsConnectRequest):
    await _enforce_rate_limit(request)
    try:
        result = await asyncio.to_thread(
            run_fsconnect_op, req.action,
            root=req.root, path=req.path, pattern=req.pattern,
            regex=req.regex, recursive=req.recursive,
        )
    except OpsError as e:
        await _audit({"event": "ops_fsconnect_rejected", "action": req.action, "error": str(e)})
        raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
    except Exception as e:
        safe_msg = _sanitize_error(e)
        await _audit({"event": "ops_fsconnect_error", "action": req.action, "error": safe_msg})
        logger.exception("Unexpected error in /ops/fsconnect action=%r", req.action)
        raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
    await _audit({
        "event": "ops_fsconnect_executed", "action": req.action,
        "exit_code": result.exit_code, "label": result.label,
    })
    payload = result.to_dict()
    payload["config"] = _ops_fsconnect_config()
    return payload


@app.post("/ops/sqlconnect", dependencies=[Depends(require_api_key)])
async def ops_sqlconnect(request: Request, req: OpsSqlConnectRequest):
    await _enforce_rate_limit(request)
    try:
        result = await asyncio.to_thread(
            run_sqlconnect_op, req.action,
            sql=req.sql, table=req.table, explain=req.explain,
            count=req.count, fmt=req.fmt,
        )
    except OpsError as e:
        await _audit({"event": "ops_sqlconnect_rejected", "action": req.action, "error": str(e)})
        raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
    except Exception as e:
        safe_msg = _sanitize_error(e)
        await _audit({"event": "ops_sqlconnect_error", "action": req.action, "error": safe_msg})
        logger.exception("Unexpected error in /ops/sqlconnect action=%r", req.action)
        raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
    await _audit({
        "event": "ops_sqlconnect_executed", "action": req.action,
        "exit_code": result.exit_code, "label": result.label,
    })
    payload = result.to_dict()
    payload["config"] = _ops_sqlconnect_config()
    return payload


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

    uvicorn.run(app, host=host, port=port)  # DevSkim: ignore DS162092 - loopback-only binding by design


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
