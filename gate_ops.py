"""Ops endpoints — out-of-band sync/ + agentic/ control surface (terminal panels).

Extracted from gate.py so the gateway module carries only core query/soul/health
concerns; the four /ops/* routes and their config readers live here as one
bounded surface. Wiring is a registration function (register_ops_routes) rather
than module-level imports from gate, so there is no gate <-> gate_ops import
cycle and the security-relevant callables (auth dependency, rate limiter, audit,
error sanitizer) stay defined in gate.py and are injected unchanged. Handlers
are decorated directly onto the FastAPI app (not an APIRouter): FastAPI 0.138's
include_router wraps sub-routers lazily (_IncludedRouter), which hides the
routes from app.routes introspection — the terminal-contract test and any
operator tooling that enumerates APIRoute objects would go blind to /ops/*.

These back the Soul Console's Sync + Agentic panels. A browser cannot spawn a
subprocess, so the gateway does — via utils/ops_runner, which is a pure
subprocess shim. Neither gate.py nor this module ever imports sync/ or
agentic/, so out-of-band isolation (and the six security invariants that rest
on it) is preserved.

Every action is: loopback-only (inherited 127.0.0.1 bind + TrustedHost
allow-list), rate-limited (shared gateway limiter), API-key-gated
(require_api_key — uniform with /soul/* mutations; subprocess execution is more
sensitive than a /soul GET), and audited. A CLI that exits non-zero is reported
inside the JSON envelope (HTTP 200) so the UI can render exit codes / stderr;
only gateway-level problems (bad action -> 400, rate limit -> 429, launch
failure -> 500) raise HTTP errors.

The "config" block is read from the already-parsed cfg dict (NOT an import of
sync/ or agentic/) so the UI can surface enabled/mode/writes_enabled — the two
config-driven gates of the agentic apply checklist — authoritatively.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from schemas.api import (
    OpsAgenticRequest,
    OpsFsConnectRequest,
    OpsSqlConnectRequest,
    OpsSyncRequest,
)

# Subprocess shim for the out-of-band sync/ + agentic/ control surface. This is a
# subprocess wrapper ONLY — it never imports sync/ or agentic/, so the gateway's
# out-of-band isolation invariant is preserved (see utils/ops_runner.py).
from utils.ops_runner import OpsError, run_agentic_op, run_fsconnect_op, run_sqlconnect_op, run_sync_op

logger = logging.getLogger("cyclaw.gate_ops")


def register_ops_routes(
    app: FastAPI,
    cfg: dict[str, Any],
    audit: Callable[[dict[str, Any]], Awaitable[None]],
    enforce_rate_limit: Callable[[Request], Awaitable[None]],
    sanitize_error: Callable[[Exception], str],
    require_api_key: Callable[..., Any],
) -> None:
    """Register the /ops/* endpoints on ``app`` with gate.py's security callables injected."""

    def _ops_sync_config() -> dict[str, Any]:
        s = cfg.get("sync", {}) or {}
        return {
            "enabled": bool(s.get("enabled", False)),
            "direction": s.get("direction", "pull"),
            "max_delete": s.get("max_delete"),
            "max_transfer": s.get("max_transfer"),
            "schedule": f"{int(s.get('schedule_hour', 2)):02d}:{int(s.get('schedule_min', 0)):02d}",
        }

    def _ops_agentic_config() -> dict[str, Any]:
        a = cfg.get("agentic", {}) or {}
        return {
            "enabled": bool(a.get("enabled", False)),
            "mode": a.get("mode", "read"),
            "writes_enabled": bool(a.get("writes_enabled", False)),
            "repo": a.get("repo", ""),
        }

    def _ops_fsconnect_config() -> dict[str, Any]:
        f = cfg.get("fsconnect", {}) or {}
        return {
            "enabled": bool(f.get("enabled", False)),
            "allowed_roots": f.get("allowed_roots", []) or [],
            "writes_enabled": bool(f.get("writes_enabled", False)),
            "max_file_bytes": f.get("max_file_bytes", 5242880),
        }

    def _ops_sqlconnect_config() -> dict[str, Any]:
        s = cfg.get("sqlconnect", {}) or {}
        return {
            "enabled": bool(s.get("enabled", False)),
            "driver": s.get("driver", "postgres"),
            "read_only": bool(s.get("read_only", True)),
            "max_rows": s.get("max_rows", 1000),
        }

    @app.post("/ops/sync", dependencies=[Depends(require_api_key)])
    async def ops_sync(request: Request, req: OpsSyncRequest) -> dict[str, Any]:
        await enforce_rate_limit(request)
        try:
            result = await asyncio.to_thread(run_sync_op, req.action, dry_run=req.dry_run)
        except OpsError as e:
            await audit({"event": "ops_sync_rejected", "action": req.action, "error": str(e)})
            raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
        except Exception as e:
            safe_msg = sanitize_error(e)
            await audit({"event": "ops_sync_error", "action": req.action, "error": safe_msg})
            logger.exception("Unexpected error in /ops/sync action=%r", req.action)
            raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
        await audit({
            "event": "ops_sync_executed", "action": req.action, "dry_run": req.dry_run,
            "exit_code": result.exit_code, "label": result.label,
        })
        payload = result.to_dict()
        payload["config"] = _ops_sync_config()
        return payload

    @app.post("/ops/agentic", dependencies=[Depends(require_api_key)])
    async def ops_agentic(request: Request, req: OpsAgenticRequest) -> dict[str, Any]:
        await enforce_rate_limit(request)
        try:
            result = await asyncio.to_thread(
                run_agentic_op, req.action,
                pr=req.pr, issue=req.issue, no_diff=req.no_diff,
                name=req.name, desc=req.desc, body=req.body, reason=req.reason, confirm=req.confirm,
            )
        except OpsError as e:
            await audit({"event": "ops_agentic_rejected", "action": req.action, "error": str(e)})
            raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
        except Exception as e:
            safe_msg = sanitize_error(e)
            await audit({"event": "ops_agentic_error", "action": req.action, "error": safe_msg})
            logger.exception("Unexpected error in /ops/agentic action=%r", req.action)
            raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
        await audit({
            "event": "ops_agentic_executed", "action": req.action,
            "exit_code": result.exit_code, "label": result.label,
        })
        payload = result.to_dict()
        payload["config"] = _ops_agentic_config()
        return payload

    @app.post("/ops/fsconnect", dependencies=[Depends(require_api_key)])
    async def ops_fsconnect(request: Request, req: OpsFsConnectRequest) -> dict[str, Any]:
        await enforce_rate_limit(request)
        try:
            result = await asyncio.to_thread(
                run_fsconnect_op, req.action,
                root=req.root, path=req.path, pattern=req.pattern,
                regex=req.regex, recursive=req.recursive,
            )
        except OpsError as e:
            await audit({"event": "ops_fsconnect_rejected", "action": req.action, "error": str(e)})
            raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
        except Exception as e:
            safe_msg = sanitize_error(e)
            await audit({"event": "ops_fsconnect_error", "action": req.action, "error": safe_msg})
            logger.exception("Unexpected error in /ops/fsconnect action=%r", req.action)
            raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
        await audit({
            "event": "ops_fsconnect_executed", "action": req.action,
            "exit_code": result.exit_code, "label": result.label,
        })
        payload = result.to_dict()
        payload["config"] = _ops_fsconnect_config()
        return payload

    @app.post("/ops/sqlconnect", dependencies=[Depends(require_api_key)])
    async def ops_sqlconnect(request: Request, req: OpsSqlConnectRequest) -> dict[str, Any]:
        await enforce_rate_limit(request)
        try:
            result = await asyncio.to_thread(
                run_sqlconnect_op, req.action,
                sql=req.sql, table=req.table, explain=req.explain,
                count=req.count, fmt=req.fmt,
            )
        except OpsError as e:
            await audit({"event": "ops_sqlconnect_rejected", "action": req.action, "error": str(e)})
            raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
        except Exception as e:
            safe_msg = sanitize_error(e)
            await audit({"event": "ops_sqlconnect_error", "action": req.action, "error": safe_msg})
            logger.exception("Unexpected error in /ops/sqlconnect action=%r", req.action)
            raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
        await audit({
            "event": "ops_sqlconnect_executed", "action": req.action,
            "exit_code": result.exit_code, "label": result.label,
        })
        payload = result.to_dict()
        payload["config"] = _ops_sqlconnect_config()
        return payload

