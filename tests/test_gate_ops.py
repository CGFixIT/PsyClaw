"""Route-level tests for gate_ops.py — the four /ops/* endpoints.

Until now the /ops/* surface was covered only by static route introspection
(tests/test_terminal_contract.py: the routes exist and accept POST) and by
utils/ops_runner.py unit tests (argv construction). The handlers themselves —
auth dependency wiring, rate-limit-before-subprocess ordering, OpsError -> 400
mapping, sanitized 500s, audit events, and the per-subsystem ``config`` block
the console renders — had no HTTP-level coverage (gate_ops.py sat at 25%).

register_ops_routes takes gate.py's security callables by injection, so these
tests build a minimal FastAPI app and inject fakes — no gate.py import (which
would trigger full app init) and no subprocess ever launches: the runner
functions are patched at the gate_ops namespace boundary.
"""

from __future__ import annotations

import hmac
import os
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient

from gate_ops import register_ops_routes
from utils.ops_runner import OpsError, OpsResult

_TEST_CFG: dict[str, Any] = {
    "sync": {"enabled": True, "direction": "pull", "max_delete": 25,
             "max_transfer": "500M", "schedule_hour": 2, "schedule_min": 30},
    "agentic": {"enabled": True, "mode": "read", "writes_enabled": False,
                "repo": "CGFixIT/CyClaw"},
    "fsconnect": {"enabled": False, "allowed_roots": ["/srv/share"],
                  "writes_enabled": False, "max_file_bytes": 1024},
    "sqlconnect": {"enabled": False, "driver": "mssql", "read_only": True,
                   "max_rows": 50},
}

_OPS_ROUTES = ("/ops/sync", "/ops/agentic", "/ops/fsconnect", "/ops/sqlconnect")
_ROUTE_BODIES = {
    "/ops/sync": {"action": "status"},
    "/ops/agentic": {"action": "status"},
    "/ops/fsconnect": {"action": "status"},
    "/ops/sqlconnect": {"action": "status"},
}


def _build_app(*, rate_limited: bool = False):
    """A minimal FastAPI app with the ops routes registered and fakes injected."""
    audit_events: list[dict[str, Any]] = []

    async def audit(event: dict[str, Any]) -> None:
        audit_events.append(event)

    async def enforce_rate_limit(request: Request) -> None:
        if rate_limited:
            raise HTTPException(
                status_code=429, detail={"error": "rate limit", "code": "RATE_LIMIT"}
            )

    def sanitize_error(exc: Exception) -> str:
        return "sanitized"

    async def require_api_key(request: Request) -> None:
        # Mirrors gate.py's fail-closed contract: unset key rejects everything.
        key = os.environ.get("CYCLAW_API_KEY", "")
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if not key or not hmac.compare_digest(token, key):
            raise HTTPException(
                status_code=401, detail={"error": "unauthorized", "code": "UNAUTHORIZED"}
            )

    app = FastAPI()
    register_ops_routes(
        app, _TEST_CFG, audit, enforce_rate_limit, sanitize_error, require_api_key
    )
    return TestClient(app, raise_server_exceptions=False), audit_events


def _result(subsystem: str, action: str) -> OpsResult:
    return OpsResult(subsystem, action, 0, True, "ok", "some stdout", "")


class TestOpsAuth:
    """Every /ops/* route is API-key gated; an unset server key fails closed."""

    @pytest.mark.parametrize("path", _OPS_ROUTES)
    def test_unset_server_key_fails_closed(self, monkeypatch, path):
        monkeypatch.delenv("CYCLAW_API_KEY", raising=False)
        client, _ = _build_app()
        resp = client.post(
            path, json=_ROUTE_BODIES[path],
            headers={"Authorization": "Bearer anything"},
        )
        assert resp.status_code == 401

    @pytest.mark.parametrize("path", _OPS_ROUTES)
    def test_wrong_key_rejected(self, monkeypatch, path):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, _ = _build_app()
        resp = client.post(
            path, json=_ROUTE_BODIES[path],
            headers={"Authorization": "Bearer wrong-key"},
        )
        assert resp.status_code == 401


class TestOpsSync:
    def test_unknown_action_rejected_at_schema_boundary(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, _ = _build_app()
        with patch("gate_ops.run_sync_op") as runner:
            resp = client.post(
                "/ops/sync", json={"action": "nuke"},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 422
        runner.assert_not_called()

    def test_status_success_payload_config_and_audit(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, audit_events = _build_app()
        with patch("gate_ops.run_sync_op", return_value=_result("sync", "status")) as runner:
            resp = client.post(
                "/ops/sync", json={"action": "status"},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["label"] == "ok"
        assert data["exit_code"] == 0
        # The console renders the shipped sync gates from this block.
        assert data["config"] == {
            "enabled": True, "direction": "pull", "max_delete": 25,
            "max_transfer": "500M", "schedule": "02:30",
        }
        runner.assert_called_once_with("status", dry_run=False)
        assert [e["event"] for e in audit_events] == ["ops_sync_executed"]
        assert audit_events[0]["action"] == "status"

    def test_dry_run_forwarded_only_for_sync_action(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, _ = _build_app()
        with patch("gate_ops.run_sync_op", return_value=_result("sync", "sync")) as runner:
            resp = client.post(
                "/ops/sync", json={"action": "sync", "dry_run": True},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 200
        runner.assert_called_once_with("sync", dry_run=True)

    def test_ops_error_maps_to_400(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, audit_events = _build_app()
        with patch("gate_ops.run_sync_op", side_effect=OpsError("bad action")):
            resp = client.post(
                "/ops/sync", json={"action": "status"},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "OPS_BAD_ACTION"
        assert [e["event"] for e in audit_events] == ["ops_sync_rejected"]

    def test_unexpected_error_is_sanitized_500(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, audit_events = _build_app()
        with patch("gate_ops.run_sync_op", side_effect=RuntimeError("/home/operator/secret-path")):
            resp = client.post(
                "/ops/sync", json={"action": "status"},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["code"] == "OPS_ERROR"
        assert detail["error"] == "sanitized"
        assert "secret-path" not in resp.text
        assert [e["event"] for e in audit_events] == ["ops_sync_error"]


class TestOpsAgentic:
    def test_context_forwards_selectors(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, audit_events = _build_app()
        with patch("gate_ops.run_agentic_op", return_value=_result("agentic", "context")) as runner:
            resp = client.post(
                "/ops/agentic", json={"action": "context", "pr": 123, "no_diff": True},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 200
        assert resp.json()["config"] == {
            "enabled": True, "mode": "read", "writes_enabled": False,
            "repo": "CGFixIT/CyClaw",
        }
        runner.assert_called_once_with(
            "context", pr=123, issue=None, no_diff=True,
            name=None, desc=None, body=None, reason=None, confirm=False,
        )
        assert [e["event"] for e in audit_events] == ["ops_agentic_executed"]

    def test_apply_skill_forwards_reason_and_confirm(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, _ = _build_app()
        with patch("gate_ops.run_agentic_op", return_value=_result("agentic", "apply-skill")) as runner:
            resp = client.post(
                "/ops/agentic",
                json={"action": "apply-skill", "name": "deploy", "desc": "runbook",
                      "reason": "add deploy runbook", "confirm": True},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 200
        runner.assert_called_once_with(
            "apply-skill", pr=None, issue=None, no_diff=False,
            name="deploy", desc="runbook", body=None,
            reason="add deploy runbook", confirm=True,
        )


class TestOpsFsConnect:
    def test_read_forwards_scoped_args(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, audit_events = _build_app()
        with patch("gate_ops.run_fsconnect_op", return_value=_result("fsconnect", "read")) as runner:
            resp = client.post(
                "/ops/fsconnect",
                json={"action": "read", "root": "/srv/share", "path": "notes/a.md"},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 200
        assert resp.json()["config"] == {
            "enabled": False, "allowed_roots": ["/srv/share"],
            "writes_enabled": False, "max_file_bytes": 1024,
        }
        runner.assert_called_once_with(
            "read", root="/srv/share", path="notes/a.md",
            pattern=None, regex=False, recursive=True,
        )
        assert [e["event"] for e in audit_events] == ["ops_fsconnect_executed"]

    def test_regex_grep_rejected_at_schema_boundary(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, _ = _build_app()
        with patch("gate_ops.run_fsconnect_op") as runner:
            resp = client.post(
                "/ops/fsconnect",
                json={"action": "grep", "pattern": "(a+)+$", "regex": True},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 422
        runner.assert_not_called()


class TestOpsSqlConnect:
    def test_table_query_forwards_args(self, monkeypatch):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, audit_events = _build_app()
        with patch("gate_ops.run_sqlconnect_op", return_value=_result("sqlconnect", "query")) as runner:
            resp = client.post(
                "/ops/sqlconnect",
                json={"action": "query", "table": "public.users", "count": True},
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 200
        assert resp.json()["config"] == {
            "enabled": False, "driver": "mssql", "read_only": True, "max_rows": 50,
        }
        runner.assert_called_once_with(
            "query", sql=None, table="public.users",
            explain=False, count=True, fmt="json",
        )
        assert [e["event"] for e in audit_events] == ["ops_sqlconnect_executed"]


class TestOpsErrorMapping:
    """The OpsError -> 400 / unexpected -> sanitized 500 contract is uniform
    across the agentic, fsconnect, and sqlconnect routes (the sync route's
    mapping is covered explicitly in TestOpsSync above)."""

    _CASES = (
        ("/ops/agentic", {"action": "status"}, "gate_ops.run_agentic_op",
         "ops_agentic_rejected", "ops_agentic_error"),
        ("/ops/fsconnect", {"action": "status"}, "gate_ops.run_fsconnect_op",
         "ops_fsconnect_rejected", "ops_fsconnect_error"),
        ("/ops/sqlconnect", {"action": "status"}, "gate_ops.run_sqlconnect_op",
         "ops_sqlconnect_rejected", "ops_sqlconnect_error"),
    )

    @pytest.mark.parametrize(
        "path,body,patch_target,rejected_event,_error_event", _CASES
    )
    def test_ops_error_maps_to_400(
        self, monkeypatch, path, body, patch_target, rejected_event, _error_event
    ):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, audit_events = _build_app()
        with patch(patch_target, side_effect=OpsError("bad action")):
            resp = client.post(
                path, json=body, headers={"Authorization": "Bearer test-key-123"}
            )
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "OPS_BAD_ACTION"
        assert [e["event"] for e in audit_events] == [rejected_event]

    @pytest.mark.parametrize(
        "path,body,patch_target,_rejected_event,error_event", _CASES
    )
    def test_unexpected_error_is_sanitized_500(
        self, monkeypatch, path, body, patch_target, _rejected_event, error_event
    ):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, audit_events = _build_app()
        with patch(patch_target, side_effect=RuntimeError("/home/operator/secret-path")):
            resp = client.post(
                path, json=body, headers={"Authorization": "Bearer test-key-123"}
            )
        assert resp.status_code == 500
        detail = resp.json()["detail"]
        assert detail["code"] == "OPS_ERROR"
        assert detail["error"] == "sanitized"
        assert "secret-path" not in resp.text
        assert [e["event"] for e in audit_events] == [error_event]


class TestOpsRateLimit:
    """The shared rate limiter runs before any subprocess launch."""

    @pytest.mark.parametrize("path", _OPS_ROUTES)
    def test_rate_limited_429_before_subprocess(self, monkeypatch, path):
        monkeypatch.setenv("CYCLAW_API_KEY", "test-key-123")
        client, _ = _build_app(rate_limited=True)
        with (
            patch("gate_ops.run_sync_op") as sync_runner,
            patch("gate_ops.run_agentic_op") as agentic_runner,
            patch("gate_ops.run_fsconnect_op") as fs_runner,
            patch("gate_ops.run_sqlconnect_op") as sql_runner,
        ):
            resp = client.post(
                path, json=_ROUTE_BODIES[path],
                headers={"Authorization": "Bearer test-key-123"},
            )
        assert resp.status_code == 429
        assert resp.json()["detail"]["code"] == "RATE_LIMIT"
        for runner in (sync_runner, agentic_runner, fs_runner, sql_runner):
            runner.assert_not_called()
