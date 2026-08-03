"""FastAPI control plane + console host for the CyClaw PowerShell harness.

Serves ``static/harness.html`` and a small JSON API on loopback
(``127.0.0.1:8790`` by default — gate.py owns :8787). This is a SEPARATE app
from the RAG gateway: gate.py/graph.py/mcp_hybrid_server.py never import this
package and vice versa (invariant I6). GitHub operations go through the
``utils.ops_runner`` subprocess shim into ``agentic.cli``, exactly like the
``/ops/agentic`` endpoint — the harness never imports agentic's write paths.

Threat-model posture matches CyClaw's: single-operator, loopback-only,
single-tenant. No remote bind, no shell execution, no writes outside the
harness home directory.
"""

from __future__ import annotations

# This process never imports gate.py, so without this it would inherit
# whatever telemetry env the operator's shell/container/observability agent
# happens to carry. Its current imports (fastapi/starlette/llm.client) pull in
# no telemetry-emitting library today -- llm.client itself imports no
# chromadb/retrieval module -- so this is prophylactic, not incident response.
# Every other CyClaw entry point applies the same block unconditionally rather
# than betting on what a future edit here (or to llm.client) ends up importing.
from utils.telemetry_kill import apply_telemetry_kill

apply_telemetry_kill()

# E402 (module-level import not at top of file) is expected and intentional
# below -- these imports must follow apply_telemetry_kill() above. Rather than
# an inline per-import suppression comment on every one of the 19 lines
# (wemake-python-styleguide's WPS402 flags that many as noqa-comment
# overuse), this file carries a blanket E402 grant in pyproject.toml's
# [tool.ruff.lint] per-file-ignores and setup.cfg's flake8 per-file-ignores,
# matching gate.py's identical pattern for the identical reason.
import hmac
import logging
import os
import secrets
import subprocess  # nosec B404 - imported only for TimeoutExpired; no process is spawned here
import sys
from collections.abc import AsyncIterator, Callable, Mapping
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from harness.agent_policy import RUN_ID_RE, CheckProfileError, available_profiles, resolve_check_profiles
from harness.config import _MAX_PORT, _MIN_USER_PORT, HarnessConfig
from harness.ollama import HarnessChatClient, HarnessLLMError
from harness.prompts import compose_system_prompt
from harness.registry_view import full_registry
from harness.schemas import (
    AgentDecisionRequest,
    AgentPublishRequest,
    AgentRunRequest,
    ChatRequest,
    ModelSelectRequest,
    RenameRequest,
    SessionCreateRequest,
    SoulToggleRequest,
)
from harness.sessions import SessionStore, SessionStoreError, TokenTally
from llm.client import ResolvedLocalBackend, resolve_local_backend
from utils.auth import require_api_key
from utils.errors import AgenticError
from utils.logger import _get_config, redact_sensitive
from utils.ops_runner import OpsError, OpsResult, run_agentic_op
from utils.ratelimit import RateLimiter

logger = logging.getLogger("cyclaw.harness.server")

_HARNESS_VERSION = "0.1.0"
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _REPO_ROOT / "config.yaml"
_STATIC = _REPO_ROOT / "static"
_RUNS_DIR = _REPO_ROOT / "data" / "agentic" / "harness_optimizer" / "runs"
_HISTORY_TURNS = 20  # prior turns forwarded to the model per chat call
_MAX_RUNS = 50
_HTTP_CREATED = 201
_HTTP_BAD_REQUEST = 400
_HTTP_FORBIDDEN = 403
_HTTP_NOT_FOUND = 404
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_UNPROCESSABLE = 422
_HTTP_BAD_GATEWAY = 502
_HTTP_GATEWAY_TIMEOUT = 504
# Caps a validation error's echoed field location. An extra="forbid" violation
# reports the caller's own key as the location, so this bounds what an
# oversized key can push into the console after redaction.
_MAX_ERROR_FIELD_LEN = 120
# Stands in for an extra="forbid" violation's location, which is the key the
# caller sent rather than anything this app declared.
_UNEXPECTED_FIELD = "(unexpected field)"
# Caps the malformed run_id echoed back in an INVALID_RUN_ID detail. A valid
# one is 32 chars; this bounds an oversized path segment without hiding a
# near-miss the operator needs to see to spot their own typo.
_MAX_ECHOED_RUN_ID_LEN = 64
_DEFAULT_TIMEOUT_SEC = 300
_DEFAULT_MAX_TOKENS = 3000
_DEFAULT_TEMPERATURE = 0.3
_MODEL_KEY = "model"
# Keys of the error envelope static/harness.html's fetch helper reads. Named
# rather than repeated inline for the same reason _MODEL_KEY above is: every
# endpoint must emit the SAME shape, and the helper reads detail.message -- an
# endpoint that invents its own key renders as a bare "HTTP <status>" with no
# explanation, which is exactly what the 429 did while it emitted "error".
_CODE_KEY = "code"
_MESSAGE_KEY = "message"
_DETAILS_KEY = "details"
# Loopback allow-list, enforced at BOTH the bind (main) and the Host header
# (TrustedHostMiddleware). Deliberately loopback-only — tighter than gate.py's
# LAN-inclusive CORS list — because the harness is a single-operator console
# that refuses any non-loopback bind. The Host check is the DNS-rebinding
# defense gate.py added for the same threat model (a rebinding page can drive
# a loopback server's state-changing POSTs even though CORS blocks reads).
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
# The token placeholder embedded in static/harness.html's <meta> tag. Swapped
# for a real per-process value in console() below, never checked into a
# static asset on disk (the file on disk always carries the placeholder).
_CSRF_PLACEHOLDER = "__CYCLAW_CSRF_TOKEN__"
_CSRF_HEADER = "x-cyclaw-csrf"
# secrets.token_urlsafe's nbytes: 32 random bytes (~43 base64url chars), the
# same size Python's own docs use as the "good enough for a security token"
# example -- comfortably beyond brute-force range for a per-process secret.
_CSRF_TOKEN_BYTES = 32


def _llm_settings() -> dict:
    """Read-only view of the repo config's ``models.local_llm`` block."""
    parsed = _get_config(str(_CONFIG_PATH))
    if not isinstance(parsed, dict):
        return {}
    models = parsed.get("models", {})
    return models.get("local_llm", {}) if isinstance(models, dict) else {}


def _rate_limit_settings() -> dict:
    """Read-only view of the repo config's ``api.rate_limit`` block.

    Reuses gate.py's existing tunables (same defaults: 60 req / 60s) rather
    than inventing a harness-specific config surface. In-memory only -- no
    persist_path/database_url reuse -- the harness is a single-operator,
    single-process console with no restart-persistence requirement gate.py's
    multi-worker deployments have.
    """
    parsed = _get_config(str(_CONFIG_PATH))
    if not isinstance(parsed, dict):
        return {}
    api = parsed.get("api", {})
    return api.get("rate_limit", {}) if isinstance(api, dict) else {}


def _resolve_backend() -> ResolvedLocalBackend:
    """Route the console through the same primary/fallback resolution gate.py
    uses (llm.client.resolve_local_backend), so that when fallback.enabled is
    true and the primary (Ollama) is down, chat targets the live backend (LM
    Studio) exactly like /query and /health already do. With fallback disabled
    (the shipped default) this returns the primary with no network probe. An
    empty/unreadable config degrades to the Ollama default rather than failing
    app build, preserving the previous hardcoded-default behavior."""
    llm = _llm_settings()
    if not str(llm.get("base_url") or "").strip():
        return ResolvedLocalBackend(
            provider="ollama", # Or LM studio - Default fallback label
            base_url="http://127.0.0.1:11434/v1",
            model="qwen3.6:27b",
            source="primary",
        )
    return resolve_local_backend(llm)


def _default_chat_client(backend: ResolvedLocalBackend) -> HarnessChatClient:
    """Chat client for the resolved backend (tests inject their own instead)."""
    llm = _llm_settings()
    return HarnessChatClient(
        base_url=backend.base_url,
        model=backend.model,
        timeout_sec=float(llm.get("timeout_sec", _DEFAULT_TIMEOUT_SEC)),
        api_key=backend.api_key,
    )


def _err(status: int, exc: AgenticError) -> HTTPException:
    detail = {_CODE_KEY: exc.code, _MESSAGE_KEY: exc.message, _DETAILS_KEY: exc.details}
    return HTTPException(status_code=status, detail=detail)


def _error_location(err: Mapping[str, object]) -> str:
    """One validation error's reportable field location.

    ``extra_forbidden`` is the only error type whose ``loc`` is caller-supplied
    (it IS the key that was sent), so that one is replaced outright rather than
    filtered -- see ``_validation_error_response`` for why redaction alone is
    not sufficient there. Every other type names a field declared on the model.
    """
    if err.get("type") == "extra_forbidden":
        return _UNEXPECTED_FIELD
    raw = err.get("loc")
    # Pydantic always supplies a tuple here, but the error dicts are typed
    # loosely enough that asserting it would be a silenced cast rather than a
    # check -- an unexpected shape degrades to "unknown" instead of raising
    # inside an error handler, where a second exception is the worst outcome.
    parts = raw if isinstance(raw, (list, tuple)) else ()
    location = ".".join(str(part) for part in parts)
    return redact_sensitive(location)[:_MAX_ERROR_FIELD_LEN]


def _validation_error_response(exc: RequestValidationError) -> JSONResponse:
    """Re-emit FastAPI's 422 in the envelope static/harness.html can read.

    FastAPI's default body puts a LIST of error objects under ``detail``, but
    the console's fetch helper reads ``data.detail.message`` -- on a list that
    is undefined, so every malformed request rendered as a bare "HTTP 422"
    with no hint about which field was wrong. That was already true of the
    five original POSTs; the agent-run body (six fields, a branch pattern, and
    a decision Literal) makes a validation failure the single most likely way
    this app says no, so it is fixed here rather than worked around per-route.

    gate.py solves the same class of problem by hand-raising an HTTPException
    per case (see its /soul/apply INVALID_REASON path); one handler is the
    equivalent for a whole app whose every model already forbids extra keys.
    The offending VALUE is never included, and neither is a caller-invented
    field NAME. Those are not the same guarantee: for every other error type
    ``loc`` names a field declared on the model, but for an ``extra="forbid"``
    violation ``loc`` IS the key the caller sent, so ``{"sk-ant-<key>": 1}``
    would echo that key into a response the console renders as text.
    Redaction alone does not close it -- ``redact_sensitive`` matches known
    secret SHAPES, so anything outside them still round-trips. The whole
    location is replaced instead, which is a guarantee rather than a filter;
    redaction and the length cap still run over the declared-field names as
    defense in depth.
    """
    fields = [_error_location(err) for err in exc.errors()]
    named = ", ".join(fields) or "unknown field"
    detail = {
        _CODE_KEY: "VALIDATION_ERROR",
        _MESSAGE_KEY: f"request body failed validation: {named}",
        _DETAILS_KEY: {"fields": fields},
    }
    return JSONResponse(status_code=_HTTP_UNPROCESSABLE, content={"detail": detail})


def _timeout_err(action: str, exc: subprocess.TimeoutExpired) -> HTTPException:
    """Map a shim timeout to the same envelope every other failure uses.

    ``subprocess.TimeoutExpired`` is a ``SubprocessError``, NOT an ``OpsError``
    (which subclasses ``ValueError``), so the ``except OpsError`` that guards
    every other shim call does not catch it and it escaped as an unhandled
    500 with a text/plain body -- which ``api()`` cannot parse at all, making
    the longest-running route produce the least informative failure.

    ``exc.cmd`` is deliberately not echoed: the argv carries
    ``--instruction=``, ``--commit-message=`` and ``--reason=`` values.
    """
    return _err(
        _HTTP_GATEWAY_TIMEOUT,
        AgenticError(
            f"agentic {action} exceeded its {int(exc.timeout)}s budget",
            code="AGENTIC_TIMEOUT",
            details={"action": action, "timeout_sec": int(exc.timeout)},
        ),
    )


def create_app(config: HarnessConfig | None = None, chat_client: HarnessChatClient | None = None) -> FastAPI:
    """App factory — tests inject a tmp-home config and a MockTransport client."""
    cfg = config or HarnessConfig.load()
    store = SessionStore(cfg.sessions_dir)

    backend = _resolve_backend()
    client = chat_client or _default_chat_client(backend)

    # Per-instance, not module-level: create_app() is the harness's test
    # boundary (mirrors store/client/backend above) -- a module-level
    # singleton would leak rate-limit state across separately-configured
    # create_app() calls in tests.
    rl_cfg = _rate_limit_settings()
    rate_limiter = RateLimiter(
        max_requests=rl_cfg.get("max_requests", 60),
        window_seconds=rl_cfg.get("window_seconds", 60),
    )

    def _enforce_rate_limit(request: Request) -> None:
        """Per-IP throttle for the expensive routes, mirroring gate.py's _enforce_rate_limit.

        Closes a local-DoS / runaway-token-burn vector: harness routes are
        loopback-only and unauthenticated (single-operator console), so a
        misbehaving local process could otherwise hammer them with no
        limit at all -- every other CyClaw entry point rate-limits its
        request-generating endpoint (/query), this one didn't.

        Applied to every route that costs real resources per call: /api/chat
        (a model round-trip), /api/github/status, and the three /api/agent/*
        routes (each a Python subprocess via utils.ops_runner -- 120s for a
        status or a decision; a run's budget is derived per request from its
        own iteration/check counts, capped at 3600s). The
        cheap read-only routes (/api/status, /api/sessions, ...) stay exempt
        so a console refresh loop never eats the budget the expensive routes
        need. The budget is shared across the limited routes on purpose: it
        is a per-IP ceiling on the app, not a per-route quota.
        """
        client_ip = request.client.host if request.client else "unknown"
        if not rate_limiter.allow(client_ip):
            raise HTTPException(
                status_code=_HTTP_TOO_MANY_REQUESTS,
                detail={
                    _CODE_KEY: "RATE_LIMIT",
                    _MESSAGE_KEY: f"Rate limit exceeded ({rate_limiter.max_requests} req / "
                    f"{rate_limiter.window_seconds}s)",
                    _DETAILS_KEY: {},
                },
            )

    def _enforce_same_origin(request: Request) -> None:
        """Reject browser-initiated cross-site requests to a state-changing route.

        TrustedHostMiddleware already blocks classic DNS rebinding (the rebound
        page's Host header is the attacker's name, not a loopback one). The gap it
        leaves is a page on any origin doing

            fetch('http://127.0.0.1:8790/api/soul', {method: 'POST',
                  headers: {'Content-Type': 'text/plain'}, body: ...})

        -- a CORS "simple request", so no preflight is sent, and the Host header is
        a legitimate 127.0.0.1. The browser does stamp Origin and Sec-Fetch-Site on
        it, which is what this checks.

        Absent headers are ALLOWED on purpose: curl, PowerShell, and the sandbox
        verifier send neither, and a non-browser client is not a CSRF vector. Every
        browser that can mount this attack sends at least Origin on a cross-origin
        POST.
        """
        site = request.headers.get("sec-fetch-site")
        if site is not None and site not in ("same-origin", "none"):
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "CROSS_SITE_BLOCKED",
                    _MESSAGE_KEY: "Cross-site request rejected",
                    _DETAILS_KEY: {"sec_fetch_site": site},
                },
            )
        origin = request.headers.get("origin")
        if origin is not None and urlparse(origin).hostname not in _LOOPBACK_HOSTS:
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "CROSS_ORIGIN_BLOCKED",
                    _MESSAGE_KEY: "Cross-origin request rejected",
                    _DETAILS_KEY: {"origin_host": urlparse(origin).hostname},
                },
            )

    # Per-instance, like rate_limiter above: a module-level singleton would leak
    # across separately-configured create_app() calls in tests, and a fixed
    # token would defeat the point. Minted fresh per process, embedded ONLY in
    # the page GET / serves (see console() below), and checked unconditionally
    # -- unlike _enforce_same_origin, there is no "no header -> allow" carve-out.
    csrf_token = secrets.token_urlsafe(_CSRF_TOKEN_BYTES)

    def _enforce_csrf_token(request: Request) -> None:
        """Reject any guarded request that doesn't carry this instance's CSRF token.

        Closes the gap _enforce_same_origin leaves on purpose: that check allows
        a request carrying NEITHER Origin nor Sec-Fetch-Site, on the theory that
        a non-browser client (curl, PowerShell, the sandbox verifier) is not a
        CSRF vector -- true, but it means a local process holding
        CYCLAW_API_KEY and sending no browser headers reaches every guarded
        route unimpeded. This token is handed out nowhere except embedded in
        the console page itself, so a caller that never fetched it doesn't have
        it, regardless of whether it also holds a valid API key.
        """
        supplied = request.headers.get(_CSRF_HEADER, "")
        if not hmac.compare_digest(supplied.encode("utf-8"), csrf_token.encode("utf-8")):
            raise HTTPException(
                status_code=_HTTP_FORBIDDEN,
                detail={
                    _CODE_KEY: "CSRF_TOKEN_INVALID",
                    _MESSAGE_KEY: "Missing or invalid CSRF token",
                    _DETAILS_KEY: {},
                },
            )

    # Dependency order is load-bearing and mirrors gate.py:624 -- throttle FIRST,
    # then origin, then auth, then CSRF. A wrong key against a spent budget must
    # return 429, not 401, or the limiter stops bounding key-guessing. CSRF runs
    # LAST so a missing/wrong API key still reports 401 (not 403) exactly as
    # before -- it is an additional, independent condition, not a replacement
    # for any earlier one.
    guarded = [
        Depends(_enforce_rate_limit),
        Depends(_enforce_same_origin),
        Depends(require_api_key),
        Depends(_enforce_csrf_token),
    ]

    # Same shutdown contract gate.py's lifespan already implements: close the
    # persistent httpx pool so the OS reclaims file descriptors and TIME_WAIT
    # sockets promptly on restart. HarnessChatClient.close() existed but nothing
    # ever called it, so every create_app leaked a pool for the process's life.
    # Isolated in try/except for the same reason gate.py isolates each close --
    # a failing teardown must not turn shutdown into an exception.
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        close = getattr(client, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                logger.warning("shutdown close failed for harness chat client", exc_info=True)

    app = FastAPI(
        title="CyClaw Harness",
        version=_HARNESS_VERSION,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(_LOOPBACK_HOSTS))

    @app.exception_handler(RequestValidationError)
    async def _on_validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return _validation_error_response(exc)

    def _current_model() -> str:
        return cfg.selected_model or backend.model

    # -- console -------------------------------------------------------
    # Read once per app instance (the file is static; only the substituted
    # token differs per instance) rather than per request. app.state also
    # carries the raw token for tests that need to attach it without a page
    # fetch -- see tests/test_harness_auth.py.
    console_html = (_STATIC / "harness.html").read_text(encoding="utf-8").replace(
        _CSRF_PLACEHOLDER, csrf_token
    )
    app.state.csrf_token = csrf_token

    @app.get("/", response_class=HTMLResponse)
    def console() -> HTMLResponse:
        return HTMLResponse(
            console_html,
            headers={
                "Content-Security-Policy": "frame-ancestors 'none'",
                "X-Frame-Options": "DENY",
            },
        )

    # -- status --------------------------------------------------------
    @app.get("/api/status")
    def status() -> dict:
        sessions = store.list()
        total_tokens = sum(entry["tokens"]["total"] for entry in sessions)
        return {
            "version": _HARNESS_VERSION,
            _MODEL_KEY: _current_model(),
            # resolved (active) backend, not the raw config primary — when
            # fallback is live these are what chat actually talks to
            "provider": backend.provider,
            "base_url": backend.base_url,
            "soul_enabled": cfg.soul_enabled,
            "home": str(cfg.home),
            "repo_root": str(cfg.repo_root),
            "sessions": len(sessions),
            "total_tokens": total_tokens,
            "layout": {
                "sessions": str(cfg.sessions_dir),
                "skills": str(cfg.skills_dir),
                "tools": str(cfg.tools_dir),
                "memory": str(cfg.memory_dir),
            },
        }

    # -- registry ------------------------------------------------------
    @app.get("/api/registry")
    def registry() -> dict:
        return full_registry()

    # -- sessions ------------------------------------------------------
    @app.get("/api/sessions")
    def list_sessions() -> dict:
        return {"sessions": store.list()}

    @app.post("/api/sessions", status_code=_HTTP_CREATED, dependencies=guarded)
    def create_session(req: SessionCreateRequest) -> dict:
        session = store.create(model=_current_model(), title=req.title)
        return session.summary()

    @app.get("/api/sessions/{session_id}", dependencies=guarded)
    def get_session(session_id: str) -> dict:
        try:
            session = store.get(session_id)
        except SessionStoreError as exc:
            raise _err(_HTTP_NOT_FOUND, exc) from exc
        messages = [
            {"role": msg.role, "content": msg.text, "ts": msg.ts}
            for msg in session.messages
        ]
        return session.summary() | {"messages": messages}

    @app.post("/api/sessions/{session_id}/rename", dependencies=guarded)
    def rename_session(session_id: str, req: RenameRequest) -> dict:
        try:
            return store.rename(session_id, req.title).summary()
        except SessionStoreError as exc:
            raise _err(_HTTP_NOT_FOUND, exc) from exc

    # -- soul / model toggles (harness-local; soul.md itself untouched) --
    @app.get("/api/soul")
    def soul_state() -> dict:
        return {"enabled": cfg.soul_enabled}

    @app.post("/api/soul", dependencies=guarded)
    def soul_toggle(req: SoulToggleRequest) -> dict:
        cfg.soul_enabled = req.enabled
        cfg.save()
        return {"enabled": cfg.soul_enabled}

    @app.post("/api/model", dependencies=guarded)
    def model_select(req: ModelSelectRequest) -> dict:
        cfg.selected_model = req.model.strip()
        cfg.save()
        return {_MODEL_KEY: _current_model()}

    # -- chat ------------------------------------------------------------
    @app.post("/api/chat", dependencies=guarded)
    def chat(req: ChatRequest) -> dict:
        try:
            if req.session_id:
                session = store.get(req.session_id)
            else:
                session = store.create(model=_current_model())
        except SessionStoreError as exc:
            raise _err(_HTTP_NOT_FOUND, exc) from exc

        system_prompt = compose_system_prompt(soul_enabled=cfg.soul_enabled)
        history = [
            {"role": msg.role, "content": msg.text}
            for msg in session.messages[-_HISTORY_TURNS:]
            if msg.role in {"user", "assistant"}
        ]
        history.append({"role": "user", "content": req.message})
        try:
            reply = client.chat(
                system_prompt=system_prompt,
                messages=history,
                # req.model (a genuine per-request override) wins; otherwise
                # fall back to the operator's persisted /model selection, not
                # straight to the backend default -- omitting cfg.selected_model
                # here meant /api/status and the session record could report a
                # model the chat call never actually used.
                model=req.model or _current_model(),
                # _llm_settings() is lru-cached upstream, so the per-request
                # inline reads cost a dict lookup, not a config re-parse
                max_tokens=int(_llm_settings().get("max_tokens", _DEFAULT_MAX_TOKENS)),
                temperature=float(_llm_settings().get("temperature", _DEFAULT_TEMPERATURE)),
            )
        except HarnessLLMError as exc:
            raise _err(_HTTP_BAD_GATEWAY, exc) from exc

        updated = store.record_exchange(
            session.session_id,
            user_text=req.message,
            assistant_text=reply.body_text,
            model=reply.model,
            usage=TokenTally(
                prompt_tokens=reply.prompt_tokens,
                completion_tokens=reply.completion_tokens,
            ),
        )
        return {
            "session_id": session.session_id,
            "reply": reply.body_text,
            _MODEL_KEY: reply.model,
            "usage": {
                "prompt_tokens": reply.prompt_tokens,
                "completion_tokens": reply.completion_tokens,
            },
            "tally": updated.summary()["tokens"],
        }

    # -- GitHub (agentic) ------------------------------------------------
    # Rate-limited: unlike every other GET here, this one spawns a Python
    # subprocess (utils.ops_runner -> `python -m agentic.cli status`) that can
    # hold a slot for up to ops_runner._TIMEOUT_SEC (120s). A plain cross-origin
    # GET needs no CORS preflight and TrustedHostMiddleware only checks the Host
    # name, so a page the operator happens to visit can drive this route in a
    # loop -- unreadable to the attacker, but the process-table/CPU cost lands on
    # the operator's box regardless. gate.py's equivalent surface (POST
    # /ops/agentic, gate_ops.py) carries both a rate limit and an API key; the
    # harness is single-operator so it keeps no key, but the throttle applies.
    @app.get("/api/github/status", dependencies=guarded)
    def github_status() -> dict:
        try:
            gh_result = run_agentic_op("status")
        except OpsError as exc:
            # Mirror gh_result.to_dict()'s redaction of stdout/stderr/parsed on
            # the success path -- "status" is a hardcoded literal in
            # _AGENTIC_ACTIONS so this OpsError is unreachable today, but the
            # redaction should not depend on that staying true if this route
            # is ever parameterized.
            raise _err(_HTTP_BAD_REQUEST, AgenticError(redact_sensitive(str(exc)))) from exc
        return gh_result.to_dict()

    # -- agentic coding runs ---------------------------------------------
    # The operator surface for agentic/real_repo_loop.py's two-step gate: run
    # (clone, plan, patch, verify -- but never commit), then a separate human
    # approve/reject that is what actually commits. Each route is one shim
    # call; harness/ holds no run state of its own, because it cannot: every
    # call is a fresh `python -m agentic.cli` subprocess (I6), so the only
    # thing that survives between "start a run" and "decide on it" is the run
    # record agentic/real_repo_run_store.py writes to disk.
    #
    # All three carry the full `guarded` chain, decision included. The plan
    # this implements argued that throttling an approval is hostile; the
    # limiter runs FIRST in that chain specifically so it bounds API-key
    # guessing, and dropping it from the highest-privilege route would remove
    # that bound exactly where it matters most. At 60 req/60s a decision costs
    # one of sixty.
    def _validated_run_id(run_id: str) -> str:
        """Reject a malformed run_id before it becomes a `--run-id=` argv element."""
        # fullmatch, not match: Python's `$` also matches just before a
        # trailing newline, so `match()` would accept "<32 hex>\n" -- the
        # pattern text stays byte-identical to agentic's so the drift test
        # still compares the two.
        if not RUN_ID_RE.fullmatch(run_id):
            raise _err(
                _HTTP_BAD_REQUEST,
                AgenticError(
                    "run_id must be 32 lowercase hex characters",
                    code="INVALID_RUN_ID",
                    details={"run_id": run_id[:_MAX_ECHOED_RUN_ID_LEN]},
                ),
            )
        return run_id

    def _agentic_call(action: str, call: Callable[[], OpsResult]) -> dict:
        """Run one shim call, mapping every failure into the console's envelope.

        Takes a thunk rather than ``**kwargs`` on purpose: forwarding kwargs
        through this helper would have to type them as ``object`` and silence
        the resulting mismatch, which would stop mypy checking the ONE thing
        worth checking here -- that each route passes ``run_agentic_op`` the
        kwargs that action actually takes. With a thunk the call is written at
        its route, fully typed, and this only owns the exception mapping.

        Note what is NOT translated: a non-zero CLI exit is a successful shim
        call, so it returns HTTP 200 carrying ok=false plus the CLI's own
        stderr. That is the same contract GET /api/github/status already has,
        and it is what lets the console distinguish "the run was refused"
        (exit 4) from "the request was malformed" (400) without parsing prose.
        """
        try:
            return call().to_dict()
        except OpsError as exc:
            raise _err(_HTTP_BAD_REQUEST, AgenticError(redact_sensitive(str(exc)))) from exc
        except subprocess.TimeoutExpired as exc:
            raise _timeout_err(action, exc) from exc

    @app.get("/api/agent/checks")
    def agent_checks() -> dict:
        """The selectable verification profiles. Open: a static allow-list listing.

        Read-only, spawns nothing, and reveals only the names of commands this
        module already hardcodes -- so it stays outside `guarded` for the same
        reason /api/registry does: the console must be able to populate its
        help before the operator has entered a key.
        """
        return {"profiles": [{"name": name, "description": desc} for name, desc in available_profiles()]}

    @app.post("/api/agent/run", dependencies=guarded)
    def agent_run(req: AgentRunRequest) -> dict:
        """Start one real-repo run. BLOCKS until the run finishes.

        The wall-clock budget is derived per request by
        utils.ops_runner._real_repo_run_timeout_sec from this request's own
        --max-iterations and check count, capped at 3600s -- it was a flat 900s
        until that flat ceiling started killing legitimate runs once the
        planner timeout became configurable (default 600s x 3 iterations).

        Deliberately synchronous, and deliberately not a poll-a-background-job
        design, for two reasons found in the backend rather than chosen here:
        agentic/cli.py writes the run record only when the run ENDS, so a
        status poll returns "not found" for the whole run and there is no
        progress to report; and the run_id is minted inside that subprocess and
        first appears in its stdout, so a route that returned early would hand
        the operator no handle to approve with. GET /api/github/status already
        blocks on a 120s subprocess in this same app -- this is that shape with
        a longer budget, not a new one.
        """
        try:
            checks = resolve_check_profiles(req.checks)
        except CheckProfileError as exc:
            raise _err(
                _HTTP_BAD_REQUEST,
                AgenticError(str(exc), code="UNKNOWN_CHECK_PROFILE", details={"requested": req.checks}),
            ) from exc
        return _agentic_call(
            "real-repo-run",
            lambda: run_agentic_op(
                "real-repo-run",
                instruction=req.instruction,
                checks=checks,
                branch=req.branch,
                commit_message=req.commit_message,
                reason=req.reason,
                confirm=req.confirm,
                max_iterations=req.max_iterations,
                pr=req.pr,
                issue=req.issue,
            ),
        )

    @app.get("/api/agent/runs/{run_id}", dependencies=guarded)
    def agent_run_status(run_id: str) -> dict:
        """Read one run's persisted record.

        Guarded despite being a read: the record names a branch, the changed
        file paths, and the clone's absolute location, and serving it spawns a
        subprocess like /api/github/status does.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-status", lambda: run_agentic_op("real-repo-run-status", run_id=checked)
        )

    @app.post("/api/agent/runs/{run_id}/decision", dependencies=guarded)
    def agent_run_decision(run_id: str, req: AgentDecisionRequest) -> dict:
        """Approve (commit) or reject (discard) a pending run.

        This is the request that can actually put a commit in the clone --
        the only one in this app that reaches a git write. It performs no
        gating of its own on purpose: the four conditions
        (allow_git_write_tools, a pending record, a non-terminal status, git's
        own refusal of an empty second commit) all live in agentic/, where
        they are tested, and re-implementing any of them here would create a
        second place for them to drift.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-decide",
            lambda: run_agentic_op("real-repo-run-decide", run_id=checked, decision=req.decision),
        )

    @app.post("/api/agent/runs/{run_id}/push", dependencies=guarded)
    def agent_run_push(run_id: str) -> dict:
        """Push an approved run's branch to origin. The first request here that leaves the box.

        Its own route rather than a field on the decision body because it is
        its own decision, taken AFTER approve: the backend's
        ``real-repo-run-decide`` refuses a second call on an already-decided
        run, so an approve-then-push sequence through one endpoint could never
        reach the push. It carries no body -- invoking it IS the confirmation,
        the same shape ``decision: "approve"`` already has.

        Gating stays in ``agentic/`` (``allow_git_write_tools``, ships
        ``false``, enforced inside ``push_branch``; plus the run-state guard
        ``require_approved_for_push``). Re-implementing either here would give
        them a second place to drift.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-push", lambda: run_agentic_op("real-repo-run-push", run_id=checked)
        )

    @app.post("/api/agent/runs/{run_id}/publish", dependencies=guarded)
    def agent_run_publish(run_id: str, req: AgentPublishRequest) -> dict:
        """Open a draft PR for an already-pushed run. The most gated route in this app.

        Reaches ``agentic/writer.py``'s six-gate chain, whose first gate
        (``EXECUTION_ENABLED``) is a hardcoded ``False`` in source that no
        config file and no request body can flip -- so on a shipped checkout
        this route always refuses, by design. Arming it is the filed-checklist
        operator procedure in ``docs/agentic/GITHUB_WRITE_ENABLEMENT.md``, not
        a runtime toggle.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-publish",
            lambda: run_agentic_op(
                "real-repo-run-publish", run_id=checked, reason=req.reason, confirm=req.confirm,
            ),
        )

    @app.post("/api/agent/runs/{run_id}/discard", dependencies=guarded)
    def agent_run_discard(run_id: str) -> dict:
        """Reclaim a decided run's clone from disk. The only route that frees disk.

        Not redundant with ``reject``: reject applies only to a run still
        awaiting a decision, and an APPROVED run's clone is deliberately
        retained past its decision because push/publish still need it. Without
        this route a console-driven operator accumulates one full repository
        clone per approved run under ``workspace_root`` with no way to free
        any of them -- the CLI had the reclamation step, the console had no
        path to it.

        The refusal for a still-pending run lives in ``agentic/`` (discarding
        then would destroy a live candidate with no decision ever recorded),
        not here.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-discard", lambda: run_agentic_op("real-repo-run-discard", run_id=checked)
        )

    # -- harness optimizer runs ------------------------------------------
    @app.get("/api/harness/runs")
    def harness_runs() -> dict:
        """List persisted accepted optimizer artifacts, newest first.

        The only writer (agentic/harness_optimizer/patching.py) persists runs
        as ``runs/accepted/<variant_id>.json`` FILES. Listing directories under
        ``runs/`` (the old behavior) could therefore never surface a real run
        -- the console showed the phantom ``accepted`` directory itself and
        nothing else. Sort by mtime (not name): variant ids are not
        chronologically sortable.
        """
        runs: list[dict] = []
        accepted = _RUNS_DIR / "accepted"
        if accepted.is_dir():
            # Split list/sort/slice so WPS Jones complexity stays under the limit
            # (inline sorted(..., key=lambda p: ...) failed WPS221 + WPS111).
            # os.path.getmtime accepts Path and avoids a short-name lambda key.
            json_files = [
                entry for entry in accepted.glob("*.json") if entry.is_file()
            ]
            json_files.sort(key=os.path.getmtime, reverse=True)
            for path in json_files[:_MAX_RUNS]:
                runs.append({"run_id": path.stem, "path": str(path)})
        return {"runs": runs, "count": len(runs)}

    return app


@lru_cache(maxsize=1)
def _default_app() -> FastAPI:
    """The app `uvicorn harness.server:app` resolves, built once on first use.

    Deliberately behind a cache rather than bound at module scope.
    HarnessConfig.load() reads (and creates) the operator's ~/.CyClaw home and
    create_app() reads harness.html off disk, so an eager module-level
    `app = create_app(...)` would do all of that on ANY `import harness.server`
    -- including every test module that imports this file only for a helper.
    """
    return create_app(HarnessConfig.load())


def __getattr__(name: str) -> object:  # noqa: WPS413 - see the comment below
    # PEP 562 module-level __getattr__, so `uvicorn harness.server:app` works
    # the way `uvicorn gate:app` does -- uvicorn's import_from_string does
    # importlib.import_module() then getattr(module, "app"), and that getattr
    # is what lands here. WPS413 objects to magic module functions as a class;
    # this is the one construct that buys the symmetry without the import-time
    # side effects described on _default_app above, so it is suppressed inline
    # (a setup.cfg per-file grant would also silently permit any FUTURE magic
    # module function added to this file, which is broader than intended).
    #
    # Security note: this bypasses main()'s bind-address guard, so
    # `uvicorn harness.server:app --host 0.0.0.0` WILL open a public socket
    # where `cyclaw-harness` would have refused. The containment that actually
    # matters is unaffected -- create_app() installs TrustedHostMiddleware with
    # loopback-only allowed_hosts, so a request carrying any other Host header
    # is rejected regardless of what the socket is bound to. Prefer
    # `cyclaw-harness`, which keeps both layers.
    if name == "app":
        return _default_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    """``python -m harness.server`` / ``cyclaw-harness`` entry point."""
    import uvicorn

    cfg = HarnessConfig.load()
    host = os.environ.get("CYCLAW_HARNESS_HOST", cfg.host)
    if host not in _LOOPBACK_HOSTS:
        sys.exit("harness binds loopback only (threat model: single-operator)")
    port_env = os.environ.get("CYCLAW_HARNESS_PORT", "").strip()
    port = int(port_env) if port_env.isdigit() else cfg.port
    if not _MIN_USER_PORT <= port <= _MAX_PORT:
        # same bounds config.py applies to the stored port; without this the env
        # override accepts 0 (ephemeral bind — console link breaks) or >65535
        sys.exit(f"CYCLAW_HARNESS_PORT out of range ({_MIN_USER_PORT}-{_MAX_PORT}): {port_env}")
    app = create_app(cfg)
    uvicorn.run(app, host=host, port=port)  # DevSkim: ignore DS162092 - loopback-only binding by design


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
