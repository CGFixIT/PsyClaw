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

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from harness import __version__
from harness.config import HarnessConfig
from harness.ollama import HarnessChatClient, HarnessLLMError
from harness.prompts import compose_system_prompt
from harness.registry_view import full_registry
from harness.sessions import SessionStore, SessionStoreError
from utils.errors import AgenticError
from utils.logger import _get_config
from utils.ops_runner import OpsError, run_agentic_op

logger = logging.getLogger("cyclaw.harness.server")

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_PATH = _REPO_ROOT / "config.yaml"
_STATIC = _REPO_ROOT / "static"
_RUNS_DIR = _REPO_ROOT / "data" / "agentic" / "harness_optimizer" / "runs"
_HISTORY_TURNS = 20  # prior turns forwarded to the model per chat call


class ChatRequest(BaseModel, extra="forbid"):
    message: str = Field(min_length=1, max_length=32768)
    session_id: str | None = None
    model: str | None = None


class SessionCreateRequest(BaseModel, extra="forbid"):
    title: str = Field(default="", max_length=200)


class RenameRequest(BaseModel, extra="forbid"):
    title: str = Field(min_length=1, max_length=200)


class SoulToggleRequest(BaseModel, extra="forbid"):
    enabled: bool


class ModelSelectRequest(BaseModel, extra="forbid"):
    model: str = Field(min_length=1, max_length=200)


def _llm_settings() -> dict:
    """Read-only view of the repo config's ``models.local_llm`` block."""
    cfg = _get_config(str(_CONFIG_PATH))
    if not isinstance(cfg, dict):
        return {}
    models = cfg.get("models", {})
    return models.get("local_llm", {}) if isinstance(models, dict) else {}


def _err(status: int, exc: AgenticError) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": exc.code, "message": exc.message, "details": exc.details})


def create_app(config: HarnessConfig | None = None, chat_client: HarnessChatClient | None = None) -> FastAPI:
    """App factory — tests inject a tmp-home config and a MockTransport client."""
    cfg = config or HarnessConfig.load()
    store = SessionStore(cfg.sessions_dir)

    llm = _llm_settings()
    client = chat_client or HarnessChatClient(
        base_url=str(llm.get("base_url", "http://127.0.0.1:11434/v1")),
        model=str(llm.get("model", "")),
        timeout_sec=float(llm.get("timeout_sec", 300)),
        api_key=str(llm.get("api_key", "") or ""),
    )

    app = FastAPI(title="CyClaw Harness", version=__version__)

    def _current_model() -> str:
        return cfg.selected_model or str(llm.get("model", ""))

    # -- console -------------------------------------------------------
    @app.get("/", response_class=FileResponse)
    def console() -> FileResponse:
        return FileResponse(str(_STATIC / "harness.html"))

    # -- status --------------------------------------------------------
    @app.get("/api/status")
    def status() -> dict:
        sessions = store.list()
        total_tokens = sum(s["tokens"]["total"] for s in sessions)
        return {
            "version": __version__,
            "model": _current_model(),
            "provider": llm.get("provider", "ollama"),
            "base_url": llm.get("base_url", ""),
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

    @app.post("/api/sessions", status_code=201)
    def create_session(req: SessionCreateRequest) -> dict:
        session = store.create(model=_current_model(), title=req.title)
        return session.summary()

    @app.get("/api/sessions/{session_id}")
    def get_session(session_id: str) -> dict:
        try:
            s = store.get(session_id)
        except SessionStoreError as exc:
            raise _err(404, exc) from exc
        return s.summary() | {"messages": [{"role": m.role, "content": m.content, "ts": m.ts} for m in s.messages]}

    @app.post("/api/sessions/{session_id}/rename")
    def rename_session(session_id: str, req: RenameRequest) -> dict:
        try:
            return store.rename(session_id, req.title).summary()
        except SessionStoreError as exc:
            raise _err(404, exc) from exc

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
        return {"model": _current_model()}

    # -- chat ------------------------------------------------------------
    @app.post("/api/chat")
    def chat(req: ChatRequest) -> dict:
        try:
            if req.session_id:
                session = store.get(req.session_id)
            else:
                session = store.create(model=_current_model())
        except SessionStoreError as exc:
            raise _err(404, exc) from exc

        system_prompt = compose_system_prompt(soul_enabled=cfg.soul_enabled)
        history = [
            {"role": m.role, "content": m.content}
            for m in session.messages[-_HISTORY_TURNS:]
            if m.role in {"user", "assistant"}
        ]
        history.append({"role": "user", "content": req.message})
        try:
            result = client.chat(
                system_prompt=system_prompt,
                messages=history,
                model=req.model or None,
                max_tokens=int(llm.get("max_tokens", 3000)),
                temperature=float(llm.get("temperature", 0.3)),
            )
        except HarnessLLMError as exc:
            raise _err(502, exc) from exc

        updated = store.record_exchange(
            session.session_id,
            user_text=req.message,
            assistant_text=result.content,
            model=result.model,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
        )
        return {
            "session_id": session.session_id,
            "reply": result.content,
            "model": result.model,
            "usage": {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
            },
            "tally": updated.summary()["tokens"],
        }

    # -- GitHub (agentic) ------------------------------------------------
    @app.get("/api/github/status")
    def github_status() -> dict:
        try:
            result = run_agentic_op("status")
        except OpsError as exc:
            raise _err(400, AgenticError(str(exc))) from exc
        return result.to_dict()

    # -- harness optimizer runs ------------------------------------------
    @app.get("/api/harness/runs")
    def harness_runs() -> dict:
        runs: list[dict] = []
        if _RUNS_DIR.is_dir():
            for path in sorted(_RUNS_DIR.iterdir(), reverse=True)[:50]:
                if path.is_dir():
                    runs.append({"run_id": path.name, "path": str(path)})
        return {"runs": runs, "count": len(runs)}

    return app


def main() -> None:
    """``python -m harness.server`` / ``cyclaw-harness`` entry point."""
    import uvicorn

    cfg = HarnessConfig.load()
    host = os.environ.get("CYCLAW_HARNESS_HOST", cfg.host)
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise SystemExit("harness binds loopback only (threat model: single-operator)")
    port_env = os.environ.get("CYCLAW_HARNESS_PORT", "").strip()
    port = int(port_env) if port_env.isdigit() else cfg.port
    app = create_app(cfg)
    uvicorn.run(app, host=host, port=port)  # DevSkim: ignore DS162092 - loopback-only binding by design


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
