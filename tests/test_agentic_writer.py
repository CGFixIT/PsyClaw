"""Tests for agentic.writer -- the disabled/stubbed write gate.

Proves the triple-gate refuses every under-satisfied request, that a fully gated
request still only DRY-RUNS (never executes), and that the executor is unwired.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agentic.config import AgenticConfig
from agentic.writer import EXECUTION_ENABLED, execute_write, plan_write
from utils.errors import AgenticError, AgenticWriteRefused
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _temp_audit(tmp_path: Path):
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
           "policy": {"privacy": {}}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reset_config_cache()
    from utils.logger import _get_config
    _get_config(str(path))
    yield
    reset_config_cache()


def _read_cfg() -> AgenticConfig:
    return AgenticConfig(mode="read", writes_enabled=False)


def _write_cfg() -> AgenticConfig:
    return AgenticConfig(mode="write", writes_enabled=True)


def _audit_events(tmp_path: Path) -> list[dict]:
    audit_file = tmp_path / "audit.jsonl"
    return [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _config_path(tmp_path: Path) -> str:
    return str(tmp_path / "config.yaml")


def test_execution_hard_disabled():
    assert EXECUTION_ENABLED is False


def test_refuses_when_not_write_mode(tmp_path: Path):
    with pytest.raises(AgenticWriteRefused) as exc:
        plan_write(
            _read_cfg(),
            "pr_comment",
            "valid reason",
            confirm=True,
            config_path=_config_path(tmp_path),
            number=1,
            body="hi",
        )
    assert exc.value.details["failed_gate"] == "mode"
    assert any(
        event.get("event") == "agentic_write_refused" and event.get("gate") == "mode"
        for event in _audit_events(tmp_path)
    )


def test_refuses_when_writes_disabled():
    cfg = AgenticConfig(mode="write", writes_enabled=False)
    with pytest.raises(AgenticWriteRefused) as exc:
        plan_write(cfg, "pr_comment", "valid reason", confirm=True, number=1, body="hi")
    assert exc.value.details["failed_gate"] == "writes_enabled"


def test_refuses_when_reason_empty():
    with pytest.raises(AgenticWriteRefused) as exc:
        plan_write(_write_cfg(), "pr_comment", "   ", confirm=True, number=1, body="hi")
    assert exc.value.details["failed_gate"] == "reason"


def test_refuses_when_not_confirmed():
    with pytest.raises(AgenticWriteRefused) as exc:
        plan_write(_write_cfg(), "pr_comment", "valid reason", confirm=False, number=1, body="hi")
    assert exc.value.details["failed_gate"] == "confirm"


def test_unknown_op_raises():
    with pytest.raises(AgenticError):
        plan_write(_write_cfg(), "force_push", "valid reason", confirm=True)


def test_non_integer_number_raises_typed_error():
    # A caller-supplied non-integer 'number' must surface as AgenticError (the
    # contract plan_write documents; CLI maps it to EXIT_FAIL), never as a bare
    # ValueError/TypeError traceback -- mirrors gh_client.build_read_argv's guard.
    for bad in ("abc", None, [12]):
        with pytest.raises(AgenticError, match="must be an integer"):
            plan_write(_write_cfg(), "pr_comment", "valid reason", confirm=True, number=bad, body="hi")


def test_full_gate_returns_dryrun_only(tmp_path: Path):
    plan = plan_write(
        _write_cfg(),
        "pr_comment",
        "explain the fix",
        confirm=True,
        config_path=_config_path(tmp_path),
        number=12,
        body="LGTM",
    )
    assert plan["status"] == "dry_run_plan"
    assert plan["executed"] is False
    assert isinstance(plan["would_run"], list)
    assert "comment" in plan["would_run"]
    assert any(
        event.get("event") == "agentic_write_dryrun" and event.get("op") == "pr_comment"
        for event in _audit_events(tmp_path)
    )


def test_full_gate_pr_create_returns_dryrun_only():
    plan = plan_write(_write_cfg(), "pr_create", "open focused fix PR", confirm=True,
                      title="Fix thing", body="details")
    assert plan["status"] == "dry_run_plan"
    assert plan["executed"] is False
    assert plan["would_run"] == [
        "gh",
        "pr",
        "create",
        "--repo",
        "CGFixIT/CyClaw",
        "--title",
        "Fix thing",
        "--body",
        "details",
        "--draft",
    ]


def test_executor_refused_by_kill_switch(tmp_path: Path):
    # EXECUTION_ENABLED is False (shipped state), so even a fully gate-satisfied
    # plan is refused at the execution boundary -- the kill switch is enforced,
    # not merely documented.
    plan = plan_write(
        _write_cfg(),
        "issue_comment",
        "explain",
        confirm=True,
        config_path=_config_path(tmp_path),
        number=1,
        body="note",
    )
    with pytest.raises(AgenticWriteRefused) as exc:
        execute_write(plan, config_path=_config_path(tmp_path))
    assert exc.value.details.get("failed_gate") == "execution_enabled"
    assert any(
        event.get("event") == "agentic_write_execution_blocked"
        and event.get("gate") == "execution_enabled"
        for event in _audit_events(tmp_path)
    )


def test_executor_unimplemented_even_with_flag_flipped(monkeypatch):
    # Flipping the flag is NOT sufficient to enable writes: the executor itself
    # is still unwired, so it raises NotImplementedError rather than running a gh
    # command. Enabling real writes remains a deliberate, separate change.
    # setattr via dotted string avoids importing agentic.writer a second way
    # (the module is already pulled in via `from agentic.writer import ...`).
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    plan = plan_write(_write_cfg(), "issue_comment", "explain", confirm=True,
                      number=1, body="note")
    with pytest.raises(NotImplementedError):
        execute_write(plan)
