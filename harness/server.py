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
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.middleware.trustedhost import TrustedHostMiddleware

from harness.config import _MAX_PORT, _MIN_USER_PORT, HarnessConfig
from harness.ollama import HarnessChatClient, HarnessLLMError
from harness.prompts import compose_system_prompt
from harness.registry_view import full_registry
from harness.schemas import (
    ChatRequest,
    ModelSelectRequest,
    RenameRequest,
    SessionCreateRequest,
    SoulToggleRequest,
)
from harness.sessions import SessionStore, SessionStoreError, TokenTally
from llm.client import ResolvedLocalBackend, resolve_local_backend
from utils.errors import AgenticError
from utils.logger import _get_config
from utils.ops_runner import OpsError, run_agentic_op
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
_HTTP_NOT_FOUND = 404
_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_BAD_GATEWAY = 502
_DEFAULT_TIMEOUT_SEC = 300
_DEFAULT_MAX_TOKENS = 3000
_DEFAULT_TEMPERATURE = 0.3
_MODEL_KEY = "model"
# Loopback allow-list, enforced at BOTH the bind (main) and the Host header
# (TrustedHostMiddleware). Deliberately loopback-only — tighter than gate.py's
# LAN-inclusive CORS list — because the harness is a single-operator console
# that refuses any non-loopback bind. The Host check is the DNS-rebinding
# defense gate.py added for the same threat model (a rebinding page can drive
# a loopback server's state-changing POSTs even though CORS blocks reads).
_LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")


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
            model="qwen2.5:7b",
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
    detail = {"code": exc.code, "message": exc.message, "details": exc.details}
    return HTTPException(status_code=status, detail=detail)


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

        Applied to the two routes that cost real resources per call:
        /api/chat (a model round-trip) and /api/github/status (a Python
        subprocess via utils.ops_runner, up to _TIMEOUT_SEC=120s each). The
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
                    "error": f"Rate limit exceeded ({rate_limiter.max_requests} req / "
                    f"{rate_limiter.window_seconds}s)",
                    "code": "RATE_LIMIT",
                },
            )

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

    def _current_model() -> str:
        return cfg.selected_model or backend.model

    # -- console -------------------------------------------------------
    @app.get("/", response_class=FileResponse)
    def console() -> FileResponse:
        return FileResponse(
            str(_STATIC / "harness.html"),
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

    @app.post("/api/sessions", status_code=_HTTP_CREATED)
    def create_session(req: SessionCreateRequest) -> dict:
        session = store.create(model=_current_model(), title=req.title)
        return session.summary()

    @app.get("/api/sessions/{session_id}")
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

    @app.post("/api/sessions/{session_id}/rename")
    def rename_session(session_id: str, req: RenameRequest) -> dict:
        try:
            return store.rename(session_id, req.title).summary()
        except SessionStoreError as exc:
            raise _err(_HTTP_NOT_FOUND, exc) from exc

    # -- soul / model toggles (harness-local; soul.md itself untouched) --
    @app.get("/api/soul")
    def soul_state() -> dict:
        return {"enabled": cfg.soul_enabled}

    @app.post("/api/soul")
    def soul_toggle(req: SoulToggleRequest) -> dict:
        cfg.soul_enabled = req.enabled
        cfg.save()
        return {"enabled": cfg.soul_enabled}

    @app.post("/api/model")
    def model_select(req: ModelSelectRequest) -> dict:
        cfg.selected_model = req.model.strip()
        cfg.save()
        return {_MODEL_KEY: _current_model()}

    # -- chat ------------------------------------------------------------
    @app.post("/api/chat", dependencies=[Depends(_enforce_rate_limit)])
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
    @app.get("/api/github/status", dependencies=[Depends(_enforce_rate_limit)])
    def github_status() -> dict:
        try:
            gh_result = run_agentic_op("status")
        except OpsError as exc:
            raise _err(_HTTP_BAD_REQUEST, AgenticError(str(exc))) from exc
        return gh_result.to_dict()

    # -- harness optimizer runs ------------------------------------------
    @app.get("/api/harness/runs")
    def harness_runs() -> dict:
        runs: list[dict] = []
        if _RUNS_DIR.is_dir():
            # dirs-only BEFORE the slice: a stray file (index, .lock) among the
            # newest entries must not push a real run out of the _MAX_RUNS window
            entries = (entry for entry in _RUNS_DIR.iterdir() if entry.is_dir())
            for path in sorted(entries, reverse=True)[:_MAX_RUNS]:
                runs.append({"run_id": path.name, "path": str(path)})
        return {"runs": runs, "count": len(runs)}

    return app


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
