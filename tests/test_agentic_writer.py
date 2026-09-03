"""Tests for agentic.writer -- the armed write gate.

Proves the gate chain refuses every under-satisfied request, that plan_write
only dry-runs (never executes), that EXECUTION_ENABLED ships armed, and that
the kill switch still refuses when flipped back to False.
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
    cfg = AgenticConfig(mode="read", writes_enabled=False)
    cfg.enabled = True  # isolates the gate under test; see test_master_switch_* for gate 0 itself
    return cfg


def _write_cfg() -> AgenticConfig:
    cfg = AgenticConfig(mode="write", writes_enabled=True)
    cfg.enabled = True  # isolates the gate under test; see test_master_switch_* for gate 0 itself
    return cfg


def _audit_events(tmp_path: Path) -> list[dict]:
    audit_file = tmp_path / "audit.jsonl"
    return [
        json.loads(line)
        for line in audit_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _config_path(tmp_path: Path) -> str:
    return str(tmp_path / "config.yaml")


@pytest.mark.uses_shipped_execution_flag
def test_execution_ships_armed():
    """Operator-signed enablement: EXECUTION_ENABLED ships True.

    Arming the flag is not sufficient to mutate GitHub on its own — agentic.enabled,
    mode, writes_enabled, reason, and confirm still fail closed. See
    docs/agentic/GITHUB_WRITE_ENABLEMENT.md. Kill-switch rollback is covered by
    test_executor_refused_when_kill_switch_off.
    """
    assert EXECUTION_ENABLED is True


def test_master_switch_refuses_plan_write_when_agentic_disabled(tmp_path: Path):
    """A codex review finding: _require_gates never checked cfg.enabled.

    agentic.enabled is documented repo-wide as the layer's master switch and
    is enforced by the CLI (_disabled_noop), but plan_write/execute_write are
    a direct programmatic boundary a caller can reach without going through
    the CLI at all -- the gate must hold on its own.
    """
    cfg = AgenticConfig(mode="write", writes_enabled=True)  # .enabled left unset -> False
    with pytest.raises(AgenticWriteRefused) as exc:
        plan_write(cfg, "pr_comment", "valid reason", confirm=True,
                   config_path=_config_path(tmp_path), number=1, body="hi")
    assert exc.value.details["failed_gate"] == "enabled"


def test_master_switch_refuses_execute_write_when_agentic_disabled(monkeypatch, tmp_path: Path):
    """Same gate, at the execution boundary: mode=write, writes_enabled=True,
    and a fresh confirm=True are not enough on their own if agentic.enabled
    is false -- exactly the scenario the finding described (a direct caller
    mutating GitHub while the layer is 'nominally disabled')."""
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    plan = plan_write(_write_cfg(), "pr_create", "ship it", confirm=True,
                      config_path=_config_path(tmp_path), head="agent/topic", title="t", body="b")
    disabled = AgenticConfig(mode="write", writes_enabled=True)  # .enabled left unset -> False
    with pytest.raises(AgenticWriteRefused) as exc:
        execute_write(plan, cfg=disabled, confirm=True, config_path=_config_path(tmp_path))
    assert exc.value.details.get("failed_gate") == "enabled"


def test_execute_write_does_not_manufacture_confirm(monkeypatch, tmp_path: Path):
    """A codex review finding: execute_write used to hardcode confirm=True when
    re-running the gate, making gate 4 unconditionally satisfied -- a caller
    could execute a fully gate-satisfied plan without ever confirming the
    mutation itself. confirm is now a required, keyword-only argument that the
    caller of execute_write must supply fresh; passing False must refuse,
    exactly like plan_write's own confirm gate does.
    """
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    plan = plan_write(_write_cfg(), "pr_create", "ship it", confirm=True,
                      config_path=_config_path(tmp_path), head="agent/topic", title="t", body="b")
    with pytest.raises(AgenticWriteRefused) as exc:
        execute_write(plan, cfg=_write_cfg(), confirm=False, config_path=_config_path(tmp_path))
    assert exc.value.details.get("failed_gate") == "confirm"


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
    cfg.enabled = True  # isolates gate 2 (writes_enabled) from gate 0 (the master switch)
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
    # P10 added --head (required) and --base (explicit) to this argv. The
    # assertion stays an exact list on purpose: it is the only thing in the
    # suite pinning that the PR is a DRAFT, and now also the only thing pinning
    # that the head branch is never left for gh to infer from the cwd.
    plan = plan_write(_write_cfg(), "pr_create", "open focused fix PR", confirm=True,
                      head="agent/fix-thing", title="Fix thing", body="details")
    assert plan["status"] == "dry_run_plan"
    assert plan["executed"] is False
    assert plan["would_run"] == [
        "gh",
        "pr",
        "create",
        "--repo",
        "cgfixit/CyClaw",
        "--head",
        "agent/fix-thing",
        "--base",
        "main",
        "--title",
        "Fix thing",
        "--body",
        "details",
        "--draft",
    ]


def test_executor_refused_when_kill_switch_off(monkeypatch, tmp_path: Path):
    """Rollback path: flipping EXECUTION_ENABLED back to False must refuse.

    Shipped state is armed (True). This test monkeypatches False to prove the
    kill switch still works when an operator rolls back.
    """
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", False)
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
        execute_write(plan, cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path))
    assert exc.value.details.get("failed_gate") == "execution_enabled"
    assert any(
        event.get("event") == "agentic_write_execution_blocked"
        and event.get("gate") == "execution_enabled"
        for event in _audit_events(tmp_path)
    )


def test_env_kill_switch_refuses_pr_create(monkeypatch, tmp_path: Path):
    """CYCLAW_AGENTIC_WRITE_DISABLE closes the gate with EXECUTION_ENABLED True.

    Deliberately uses pr_create, not issue_comment: pr_create is the only member
    of _EXECUTABLE_WRITE_OPS, so it is the only op that would otherwise reach
    subprocess.run. A kill-switch test on a non-executable op proves the switch
    reports first, not that it stops a real mutation.
    """
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    plan = plan_write(_write_cfg(), "pr_create", "ship it", confirm=True,
                      config_path=_config_path(tmp_path), head="agent/topic", title="t", body="b")
    monkeypatch.setattr("agentic.writer._DISABLED_BY_ENV", True)
    with pytest.raises(AgenticWriteRefused) as exc:
        execute_write(plan, cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path))
    assert exc.value.details.get("failed_gate") == "execution_enabled"
    # The message must name the env var, or an operator who exported it is left
    # reading "EXECUTION_ENABLED is False" against a source file that says True.
    assert "CYCLAW_AGENTIC_WRITE_DISABLE" in str(exc.value)
    assert any(
        event.get("event") == "agentic_write_execution_blocked"
        and event.get("gate") == "execution_enabled"
        for event in _audit_events(tmp_path)
    )


def test_env_kill_switch_is_disable_only(monkeypatch):
    """The switch is AND-ed, never OR-ed: it can close the gate but never open it.

    That asymmetry is the whole reason it is safe to read an arming decision
    from the environment at all -- a hostile or accidental env var must not be
    able to arm a build whose source ships disarmed.
    """
    from agentic import writer

    monkeypatch.setattr(writer, "EXECUTION_ENABLED", False)
    monkeypatch.setattr(writer, "_DISABLED_BY_ENV", False)
    assert writer._execution_enabled() is False  # env "off" cannot arm a False flag

    monkeypatch.setattr(writer, "EXECUTION_ENABLED", True)
    monkeypatch.setattr(writer, "_DISABLED_BY_ENV", True)
    assert writer._execution_enabled() is False  # env kill beats an armed flag

    monkeypatch.setattr(writer, "EXECUTION_ENABLED", True)
    monkeypatch.setattr(writer, "_DISABLED_BY_ENV", False)
    assert writer._execution_enabled() is True  # only both-open is open


@pytest.mark.parametrize(
    ("value", "expected"),
    [("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
     (" 1 ", True), ("0", False), ("false", False), ("", False), ("maybe", False)],
)
def test_env_kill_switch_parses_at_import(monkeypatch, value, expected):
    """The env var is read once at import, so prove the parse with a real reload.

    Monkeypatching _DISABLED_BY_ENV (as the tests above do) exercises the gate
    but never the parsing. Only a reload with the variable actually set proves
    an operator exporting it gets the behavior the docstring promises.
    """
    import importlib

    from agentic import writer

    monkeypatch.setenv("CYCLAW_AGENTIC_WRITE_DISABLE", value)
    try:
        reloaded = importlib.reload(writer)
        assert reloaded._DISABLED_BY_ENV is expected
    finally:
        # Restore the module to the ambient environment so later tests in this
        # session see the real import-time state, not this parametrization's.
        monkeypatch.delenv("CYCLAW_AGENTIC_WRITE_DISABLE", raising=False)
        importlib.reload(writer)


def test_arming_the_flag_is_not_sufficient_without_the_config_gates(monkeypatch, tmp_path: Path):
    """The replacement for test_executor_unimplemented_even_with_flag_flipped.

    That test asserted the executor was unwired. P10 wires it, so the honest
    successor asserts the property that ACTUALLY carries the safety now: arming
    the flag still does not permit a write, because execute_write re-runs the
    four config gates itself. A plan dict is data -- hand-buildable, JSON
    round-trippable -- so holding one must not be authority to write.

    Here the flag is armed, a fresh confirm=True is supplied, and a fully
    gate-satisfied plan is supplied -- but the config handed to the executor
    is a config with write gates closed (mode=read, writes_enabled=False) with the
    master switch on, so this isolates gate 1 specifically. It refuses there.
    """
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    plan = plan_write(_write_cfg(), "issue_comment", "explain", confirm=True,
                      config_path=_config_path(tmp_path), number=1, body="note")
    shipped_default = AgenticConfig()
    shipped_default.enabled = True
    with pytest.raises(AgenticWriteRefused) as exc:
        execute_write(plan, cfg=shipped_default, confirm=True, config_path=_config_path(tmp_path))
    assert exc.value.details.get("failed_gate") == "mode"


def test_a_planned_but_unexecutable_op_is_refused_at_execution(monkeypatch, tmp_path: Path):
    """pr_comment/issue_comment can be planned; only pr_create can be run.

    Describing an op and being allowed to perform it are separate authorities,
    and P10 only reviewed the pr_create path.
    """
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    plan = plan_write(_write_cfg(), "issue_comment", "explain", confirm=True,
                      config_path=_config_path(tmp_path), number=1, body="note")
    with pytest.raises(AgenticWriteRefused) as exc:
        execute_write(plan, cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path))
    assert exc.value.details.get("failed_gate") == "executable_op"


def test_a_tampered_would_run_is_never_executed(monkeypatch, tmp_path: Path):
    """The argv is rebuilt from the plan's own params; would_run is not trusted.

    This is the concrete reason execute_write does not simply run the list it
    was handed: a plan can cross a boundary, and a swapped would_run would
    otherwise be arbitrary command execution behind a gate that already passed.
    """
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    plan = plan_write(_write_cfg(), "pr_create", "ship it", confirm=True,
                      config_path=_config_path(tmp_path),
                      head="agent/topic", title="t", body="b")
    plan["would_run"] = ["gh", "repo", "delete", "--yes"]
    with pytest.raises(AgenticWriteRefused) as exc:
        execute_write(plan, cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path))
    assert exc.value.details.get("failed_gate") == "plan_integrity"


def test_a_plan_naming_another_repo_is_refused(monkeypatch, tmp_path: Path):
    """The config's repo is authoritative; the plan's is advisory."""
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    plan = plan_write(_write_cfg(), "pr_create", "ship it", confirm=True,
                      config_path=_config_path(tmp_path),
                      head="agent/topic", title="t", body="b")
    plan["repo"] = "attacker/elsewhere"
    with pytest.raises(AgenticWriteRefused) as exc:
        execute_write(plan, cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path))
    assert exc.value.details.get("failed_gate") == "repo_match"


@pytest.mark.parametrize("bad_head", [None, "", "main", "feature/x", "-rf", "agent/" + "z" * 100])
def test_pr_create_requires_a_claude_head_branch(bad_head, tmp_path: Path):
    """Without --head, gh infers the head branch from the process's cwd.

    On the ops_runner path that cwd is the operator's own checkout, so an
    omitted head would open a PR from whatever branch they had checked out.
    It is required, and constrained to the agent/ namespace.
    """
    params = {"title": "t", "body": "b"}
    if bad_head is not None:
        params["head"] = bad_head
    with pytest.raises(AgenticError):
        plan_write(_write_cfg(), "pr_create", "ship it", confirm=True,
                   config_path=_config_path(tmp_path), **params)


def test_the_head_branch_pattern_matches_repo_workspaces():
    """Duplicated to keep writer.py importable without the deepagents extras.

    This test may import that module; writer.py may not afford to.
    """
    from agentic.deepagent_github.repo_workspace import BRANCH_NAME_RE

    from agentic.writer import _HEAD_BRANCH_RE

    assert _HEAD_BRANCH_RE.pattern == BRANCH_NAME_RE.pattern


def test_missing_number_raises_typed_error():
    from agentic.writer import _build_write_argv

    with pytest.raises(AgenticError, match="requires 'number'"):
        _build_write_argv("pr_comment", "CGFixIT/CyClaw", {"body": "hi"})


def test_unknown_write_op_in_builder_raises():
    from agentic.writer import _build_write_argv

    with pytest.raises(AgenticError, match="Unknown write op"):
        _build_write_argv("force_push", "CGFixIT/CyClaw", {})


def test_execute_write_rejects_non_dict_or_non_string_op(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    with pytest.raises(AgenticError, match="plan dict"):
        execute_write(["not-a-plan"], cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path))
    with pytest.raises(AgenticError, match="plan dict"):
        execute_write({"op": 12, "repo": "CGFixIT/CyClaw"}, cfg=_write_cfg(), confirm=True,
                      config_path=_config_path(tmp_path))


def test_execute_write_runs_subprocess_and_handles_outcomes(monkeypatch, tmp_path: Path):
    """Drive the real execute_write path with gh/network mocked at the boundary."""
    import subprocess

    monkeypatch.setattr("agentic.writer.EXECUTION_ENABLED", True)
    plan = plan_write(
        _write_cfg(), "pr_create", "ship it", confirm=True,
        config_path=_config_path(tmp_path), head="agent/topic", title="t", body="b",
    )

    monkeypatch.setattr("agentic.writer.shutil.which", lambda _bin: None)
    with pytest.raises(AgenticError, match="gh binary not found"):
        execute_write(plan, cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path))

    monkeypatch.setattr("agentic.writer.shutil.which", lambda _bin: r"C:\fake\gh.exe")

    def _timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd=["gh"], timeout=1)

    monkeypatch.setattr("agentic.writer.subprocess.run", _timeout)
    with pytest.raises(AgenticError, match="INDETERMINATE") as timed:
        execute_write(plan, cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path), timeout_sec=1)
    assert timed.value.details.get("indeterminate") is True

    failed = subprocess.CompletedProcess(args=["gh"], returncode=1, stdout="", stderr="boom")
    monkeypatch.setattr("agentic.writer.subprocess.run", lambda *_a, **_k: failed)
    with pytest.raises(AgenticError, match="exit code 1"):
        execute_write(plan, cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path))

    ok = subprocess.CompletedProcess(args=["gh"], returncode=0, stdout="https://example/pr/1", stderr="")
    monkeypatch.setattr("agentic.writer.subprocess.run", lambda *_a, **_k: ok)
    result = execute_write(plan, cfg=_write_cfg(), confirm=True, config_path=_config_path(tmp_path))
    assert result["status"] == "executed"
    assert result["op"] == "pr_create"
    assert result["stdout"] == "https://example/pr/1"
