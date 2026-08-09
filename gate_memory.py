"""Memory admin HTTP surface — registration injection (mirrors gate_ops shape).

Lazy-imports ``memory.*`` inside handlers only. Top-level must not import the
``memory`` package (enforced by tests/test_memory_isolation.py).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse

from schemas.api import MemoryApplyRequest, MemoryProposeRequest, MemoryRejectRequest
from utils.errors import PromptInjectionError

logger = logging.getLogger("cyclaw.gate_memory")


def register_memory_routes(
    app: FastAPI,
    cfg: dict[str, Any],
    audit: Callable[[dict[str, Any]], Awaitable[None]],
    enforce_rate_limit: Callable[..., Any],
    require_api_key: Callable[..., Any],
) -> None:
    """Register /memory/* and /query/export/html on ``app``."""

    def _mem() -> dict[str, Any]:
        return dict(cfg.get("memory") or {})

    def _master_on() -> bool:
        return _mem().get("enabled") is True

    def _propose_on() -> bool:
        m = _mem()
        return m.get("enabled") is True and (m.get("propose_apply") or {}).get("enabled") is True

    def _export_on() -> bool:
        m = _mem()
        return m.get("enabled") is True and (m.get("export_html") or {}).get("enabled") is True

    @app.get("/memory/status", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def memory_status(request: Request) -> dict[str, Any]:
        # Prefer 200 + flags so consoles can probe without treating disabled as error.
        from memory.mirror import status_dict  # lazy

        status = status_dict(cfg)
        await audit({"event": "memory_status", "enabled": status.get("enabled")})
        return status

    @app.get("/memory/facts", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def memory_facts(request: Request, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        if not _master_on():
            raise HTTPException(status_code=404, detail="Memory system not enabled")
        from memory.store import list_facts  # lazy

        facts = list_facts(cfg, active_only=True, limit=min(limit, 500), offset=max(offset, 0))
        return {"facts": [asdict(f) for f in facts]}

    @app.get("/memory/episodes", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def memory_episodes(request: Request, limit: int = 100, offset: int = 0) -> dict[str, Any]:
        if not _master_on():
            raise HTTPException(status_code=404, detail="Memory system not enabled")
        from memory.store import list_episodes  # lazy

        eps = list_episodes(cfg, limit=min(limit, 500), offset=max(offset, 0))
        return {"episodes": [asdict(e) for e in eps]}

    @app.get("/memory/proposals", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def memory_proposals(request: Request, status: str = "pending") -> dict[str, Any]:
        if not _propose_on():
            raise HTTPException(status_code=404, detail="Memory propose/apply not enabled")
        from memory.store import list_proposals  # lazy

        st = status if status in ("pending", "applied", "rejected", "all") else "pending"
        props = list_proposals(cfg, status=None if st == "all" else st, limit=100)
        return {"proposals": [asdict(p) for p in props]}

    @app.post("/memory/propose", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def memory_propose(request: Request, req: MemoryProposeRequest) -> dict[str, Any]:
        if not _propose_on():
            raise HTTPException(status_code=404, detail="Memory propose/apply not enabled")
        from memory.policy import require_reason  # lazy
        from memory.store import create_proposal  # lazy

        try:
            require_reason(req.reason)
            payload: dict[str, Any] = {
                "content": req.content,
                "fact_id": req.fact_id,
                "category": req.category,
                "tags": list(req.tags),
                "confidence": req.confidence,
            }
            prop = create_proposal(cfg, req.action, payload, req.reason)
        except ValueError as e:
            await audit({"event": "memory_propose_rejected", "error": str(e)})
            raise HTTPException(
                status_code=400,
                detail={"error": str(e), "code": "INVALID_REASON" if "reason" in str(e).lower() else "MEMORY_BAD_REQUEST"},
            ) from e
        await audit({
            "event": "memory_propose",
            "proposal_id": prop.id,
            "action": prop.action,
            "injection_flag_count": len(prop.injection_flags),
        })
        return asdict(prop)

    @app.post("/memory/apply", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def memory_apply(request: Request, req: MemoryApplyRequest) -> dict[str, Any]:
        if not _propose_on():
            raise HTTPException(status_code=404, detail="Memory propose/apply not enabled")
        from memory.store import apply_proposal  # lazy

        try:
            result = apply_proposal(cfg, req.proposal_id, req.reason)
        except PromptInjectionError as e:
            await audit({
                "event": "memory_apply_injection_blocked",
                "proposal_id": req.proposal_id,
            })
            raise HTTPException(
                status_code=400,
                detail={"error": e.message, "code": e.code, "details": e.details},
            ) from e
        except ValueError as e:
            code = "INVALID_REASON" if "reason" in str(e).lower() else "MEMORY_BAD_REQUEST"
            await audit({"event": "memory_apply_rejected", "proposal_id": req.proposal_id, "error": str(e)})
            raise HTTPException(status_code=400, detail={"error": str(e), "code": code}) from e
        await audit({
            "event": "memory_apply",
            "proposal_id": req.proposal_id,
            "fact_id": result.get("fact_id"),
            "action": result.get("action"),
        })
        return result

    @app.post("/memory/reject", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def memory_reject(request: Request, req: MemoryRejectRequest) -> dict[str, Any]:
        if not _propose_on():
            raise HTTPException(status_code=404, detail="Memory propose/apply not enabled")
        from memory.store import reject_proposal  # lazy

        try:
            prop = reject_proposal(cfg, req.proposal_id, req.reason)
        except ValueError as e:
            code = "INVALID_REASON" if "reason" in str(e).lower() else "MEMORY_BAD_REQUEST"
            await audit({"event": "memory_reject_rejected", "proposal_id": req.proposal_id, "error": str(e)})
            raise HTTPException(status_code=400, detail={"error": str(e), "code": code}) from e
        await audit({"event": "memory_reject", "proposal_id": req.proposal_id})
        return asdict(prop)

    @app.get("/query/export/html", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)], response_class=HTMLResponse)
    async def query_export_html(request: Request) -> HTMLResponse:
        if not _export_on():
            raise HTTPException(status_code=404, detail="Memory HTML export not enabled")
        from memory.mirror import export_html  # lazy

        body = export_html(cfg)
        await audit({"event": "memory_export_html"})
        return HTMLResponse(content=body, media_type="text/html; charset=utf-8")
