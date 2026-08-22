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

Every action is: loopback-only (inherited loopback-address bind + TrustedHost
allow-list; api.host in config.yaml owns the literal value), rate-limited
(shared gateway limiter), API-key-gated (require_api_key — uniform with /soul/*
mutations; subprocess execution is more sensitive than a /soul GET), and
audited. A CLI that exits non-zero is reported
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


def _log_safe(value: str) -> str:
    """Strip CR/LF so a request-derived value cannot forge extra log lines.

    Every req.action below is a closed Pydantic Literal in strict mode, so a
    non-enum value never reaches these handlers — this can never actually fire.
    It exists because CodeQL's py/log-injection taint tracking cannot see the
    Literal narrowing (PR #465 alerts 679-682); stripping newlines is the
    sanitizer it recognizes, and it keeps the defense explicit if a future
    schema ever loosens action to a free string.
    """
    return value.replace("\r", "").replace("\n", "")


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

    async def _run_ops_route(
        *,
        route: str,
        action: str,
        op_fn: Callable[..., Any],
        op_kwargs: dict[str, Any],
        config_fn: Callable[[], dict[str, Any]],
        extra_executed_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Shared try/except/audit/payload shape for all four /ops/* routes.

        ``op_fn``/``config_fn`` are passed in by each route's own body (not
        captured at registration time) so a test patching e.g.
        ``gate_ops.run_sync_op`` still intercepts the call made here.
        """
        try:
            result = await asyncio.to_thread(op_fn, action, **op_kwargs)
        except OpsError as e:
            await audit({"event": f"ops_{route}_rejected", "action": action, "error": str(e)})
            raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
        except Exception as e:
            safe_msg = sanitize_error(e)
            await audit({"event": f"ops_{route}_error", "action": action, "error": safe_msg})
            logger.exception("Unexpected error in /ops/%s action=%r", route, _log_safe(action))
            raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
        await audit({
            "event": f"ops_{route}_executed", "action": action,
            **(extra_executed_fields or {}),
            "exit_code": result.exit_code, "label": result.label,
        })
        payload = result.to_dict()
        payload["config"] = config_fn()
        return payload

    @app.post("/ops/sync", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def ops_sync(request: Request, req: OpsSyncRequest) -> dict[str, Any]:
        return await _run_ops_route(
            route="sync", action=req.action, op_fn=run_sync_op,
            op_kwargs={"dry_run": req.dry_run}, config_fn=_ops_sync_config,
            extra_executed_fields={"dry_run": req.dry_run},
        )

    @app.post("/ops/agentic", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def ops_agentic(request: Request, req: OpsAgenticRequest) -> dict[str, Any]:
        return await _run_ops_route(
            route="agentic", action=req.action, op_fn=run_agentic_op,
            op_kwargs={
                "pr": req.pr, "issue": req.issue, "no_diff": req.no_diff,
                "name": req.name, "desc": req.desc, "body": req.body,
                "reason": req.reason, "confirm": req.confirm,
            },
            config_fn=_ops_agentic_config,
        )

    @app.post("/ops/fsconnect", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def ops_fsconnect(request: Request, req: OpsFsConnectRequest) -> dict[str, Any]:
        return await _run_ops_route(
            route="fsconnect", action=req.action, op_fn=run_fsconnect_op,
            op_kwargs={
                "root": req.root, "path": req.path, "pattern": req.pattern,
                "regex": req.regex, "recursive": req.recursive,
            },
            config_fn=_ops_fsconnect_config,
        )

    @app.post("/ops/sqlconnect", dependencies=[Depends(enforce_rate_limit), Depends(require_api_key)])
    async def ops_sqlconnect(request: Request, req: OpsSqlConnectRequest) -> dict[str, Any]:
        return await _run_ops_route(
            route="sqlconnect", action=req.action, op_fn=run_sqlconnect_op,
            op_kwargs={
                "sql": req.sql, "table": req.table, "explain": req.explain,
                "count": req.count, "fmt": req.fmt,
            },
            config_fn=_ops_sqlconnect_config,
        )

