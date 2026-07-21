"""Tests for utils.ops_runner — the subprocess shim behind /ops/sync and /ops/agentic.

These never spawn the real CLIs: ``_run`` is monkeypatched to capture the argv the
shim builds and to return a synthetic ``CompletedProcess``. The one exception is the
isolation test, which spins a clean interpreter to prove importing the shim never
imports the out-of-band packages.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from utils import ops_runner
from utils.ops_runner import OpsError, run_agentic_op, run_fsconnect_op, run_sqlconnect_op, run_sync_op


def _fake_run(returncode: int = 0, stdout: str = "", stderr: str = ""):
    """Return a (_run replacement, captured-argv list) pair."""
    captured: list[list[str]] = []

    def _runner(argv: list[str], *, timeout_sec: int | None = None) -> subprocess.CompletedProcess[str]:
        # timeout_sec is accepted so run_sync_op's config-aligned budget
        # does not TypeError the stub (production _run takes the same kwarg).
        del timeout_sec
        captured.append(argv)
        return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)

    return _runner, captured


# --------------------------------------------------------------------------- sync


def test_sync_unknown_action_raises() -> None:
    with pytest.raises(OpsError):
        run_sync_op("rm-rf")


def test_sync_status_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout="ok")
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_sync_op("status")
    argv = captured[0]
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "sync.cli"]
    assert "--config" in argv and argv[-1] == "status"
    assert res.ok is True and res.label == "ok" and res.exit_code == 0


def test_sync_dry_run_appends_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run()
    monkeypatch.setattr(ops_runner, "_run", runner)
    run_sync_op("sync", dry_run=True)
    assert captured[0][-1] == "--dry-run"
    assert captured[0][-2] == "sync"


def test_sync_action_uses_config_aligned_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /ops/sync must not hard-kill at 120s when sync.sync_timeout_sec is 3600."""
    captured: list[tuple[list[str], int | None]] = []

    def _runner(argv: list[str], *, timeout_sec: int | None = None) -> subprocess.CompletedProcess[str]:
        captured.append((argv, timeout_sec))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

    monkeypatch.setattr(ops_runner, "_run", _runner)
    monkeypatch.setattr(ops_runner, "_sync_timeout_sec", lambda: 3660)
    run_sync_op("sync")
    assert captured[0][1] == 3660
    # Non-transfer actions keep the short default path (timeout_sec=_TIMEOUT_SEC).
    run_sync_op("status")
    assert captured[1][1] == ops_runner._TIMEOUT_SEC


def test_sync_dry_run_ignored_for_non_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run()
    monkeypatch.setattr(ops_runner, "_run", runner)
    run_sync_op("status", dry_run=True)  # dry_run must NOT leak onto status
    assert "--dry-run" not in captured[0]


@pytest.mark.parametrize(
    "code,ok,label",
    [(0, True, "ok"), (10, True, "ok_reindex_needed"), (1, False, "safety_abort"),
     (2, False, "failed"), (3, False, "env_config"), (99, False, "unknown")],
)
def test_sync_exit_code_labels(monkeypatch: pytest.MonkeyPatch, code: int, ok: bool, label: str) -> None:
    runner, _ = _fake_run(returncode=code, stderr="boom" if code else "")
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_sync_op("sync")
    assert res.ok is ok and res.label == label and res.exit_code == code


# ------------------------------------------------------------------------- agentic


def test_agentic_unknown_action_raises() -> None:
    with pytest.raises(OpsError):
        run_agentic_op("delete-repo")


def test_agentic_context_repo_default(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout='{"repo": "x"}')
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_agentic_op("context")
    assert "--repo" in captured[0]
    assert res.parsed == {"repo": "x"}  # JSON action parsed on success


def test_agentic_context_pr_and_issue(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout="{}")
    monkeypatch.setattr(ops_runner, "_run", runner)
    run_agentic_op("context", pr=42)
    assert captured[0][-2:] == ["--pr", "42"]
    captured.clear()
    run_agentic_op("context", issue=7, no_diff=True)
    assert "--issue" in captured[0] and "7" in captured[0] and "--no-diff" in captured[0]


def test_agentic_propose_requires_name_desc() -> None:
    with pytest.raises(OpsError):
        run_agentic_op("propose-skill", name="x")  # missing desc


def test_agentic_apply_requires_reason() -> None:
    with pytest.raises(OpsError):
        run_agentic_op("apply-skill", name="x", desc="y", reason="   ")


def test_agentic_apply_confirm_and_body_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    runner, captured = _fake_run(returncode=0, stdout='{"status": "applied"}')
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_agentic_op(
        "apply-skill", name="demo", desc="a demo skill", body="# body\ncontent",
        reason="adding demo", confirm=True,
    )
    argv = captured[0]
    # name/desc/reason are passed in --opt=value form so a leading-dash value
    # cannot be reparsed by the child argparse as a separate flag.
    assert "--name=demo" in argv and "--desc=a demo skill" in argv
    assert "--reason=adding demo" in argv
    assert "--confirm" in argv
    # body routed through a temp --body-file, never inlined as an argv token
    assert "--body-file" in argv
    body_idx = argv.index("--body-file") + 1
    assert "# body" not in argv  # the literal body is not on the command line
    # the temp file is cleaned up after the run
    from pathlib import Path
    assert not Path(argv[body_idx]).exists()
    assert res.parsed == {"status": "applied"}


def test_agentic_leading_dash_value_bound_to_option(monkeypatch: pytest.MonkeyPatch) -> None:
    # A desc/reason that begins with '-' must travel as a single --opt=value argv
    # element, never as a bare token the child argparse could mistake for a flag.
    runner, captured = _fake_run(returncode=0, stdout='{"status": "applied"}')
    monkeypatch.setattr(ops_runner, "_run", runner)
    run_agentic_op(
        "apply-skill", name="demo", desc="-- dashed desc",
        reason="-x suspicious reason", confirm=True,
    )
    argv = captured[0]
    assert "--desc=-- dashed desc" in argv
    assert "--reason=-x suspicious reason" in argv
    # No bare element equals the raw leading-dash value.
    assert "-- dashed desc" not in argv
    assert "-x suspicious reason" not in argv


def test_agentic_apply_body_file_cleaned_up_when_run_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # run_agentic_op's docstring promises the temp --body-file is unlinked on
    # EVERY exit path, "return or raise". Only the return path was covered; this
    # locks in the finally-block cleanup when _run raises (e.g. the CLI hangs and
    # subprocess.TimeoutExpired fires) so a crashing op cannot leak skill-body
    # temp files into the OS temp dir on repeated failures.
    from pathlib import Path

    captured: list[list[str]] = []

    def _boom(argv: list[str]) -> subprocess.CompletedProcess[str]:
        captured.append(argv)
        raise subprocess.TimeoutExpired(cmd=argv, timeout=1)

    monkeypatch.setattr(ops_runner, "_run", _boom)

    with pytest.raises(subprocess.TimeoutExpired):
        run_agentic_op(
            "apply-skill", name="demo", desc="a demo skill", body="# body\ncontent",
            reason="adding demo", confirm=True,
        )

    # The body-file was created and handed to _run, then unlinked by the finally
    # block despite _run raising.
    argv = captured[0]
    body_file = argv[argv.index("--body-file") + 1]
    assert not Path(body_file).exists()


def test_agentic_apply_no_confirm_omits_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=4, stderr="apply-skill requires --confirm")
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_agentic_op("apply-skill", name="x", desc="y", reason="r", confirm=False)
    assert "--confirm" not in captured[0]
    assert res.exit_code == 4 and res.label == "write_refused" and res.ok is False


@pytest.mark.parametrize(
    "code,ok,label",
    [(0, True, "ok"), (2, False, "failed"), (3, False, "env_config"),
     (4, False, "write_refused"), (77, False, "unknown")],
)
def test_agentic_exit_code_labels(monkeypatch: pytest.MonkeyPatch, code: int, ok: bool, label: str) -> None:
    runner, _ = _fake_run(returncode=code, stdout="{}" if code == 0 else "")
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_agentic_op("status")
    assert res.ok is ok and res.label == label


def test_agentic_text_action_not_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    # status emits human text, not JSON — parsed must stay None even on success.
    runner, _ = _fake_run(returncode=0, stdout="  enabled... False")
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_agentic_op("status")
    assert res.parsed is None


def test_agentic_json_action_with_non_json_stdout_yields_none_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
    # A JSON-emitting action (context) that returns exit 0 but garbled stdout
    # silently degrades to parsed=None rather than raising. Documents the
    # _maybe_json contract: non-JSON output from JSON actions is not an error.
    runner, _ = _fake_run(returncode=0, stdout="not-valid-json-at-all")
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_agentic_op("context")
    assert res.ok is True
    assert res.parsed is None


def test_to_dict_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, _ = _fake_run(returncode=0, stdout="ok")
    monkeypatch.setattr(ops_runner, "_run", runner)
    d = run_sync_op("status").to_dict()
    assert set(d) == {"subsystem", "action", "exit_code", "ok", "label", "stdout", "stderr", "parsed"}


def test_to_dict_redacts_subprocess_output(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = {
        "policy": {
            "privacy": {
                "redact_emails": False,
                "redact_ips": False,
                "redact_secrets_like": [r"sk-[A-Za-z0-9]{8,}", r"Bearer\s+[A-Za-z0-9\-_.]+"],
            }
        }
    }
    monkeypatch.setattr(ops_runner, "_get_config", lambda *_args, **_kwargs: cfg)
    res = ops_runner.OpsResult(
        "sync", "status", 0, True, "ok",
        "stdout sk-testsecret123",
        "stderr Bearer abc.def-ghi",
        {"nested": ["sk-nestedsecret123"]},
    )

    d = res.to_dict()

    assert "sk-testsecret123" not in d["stdout"]
    assert "abc.def-ghi" not in d["stderr"]
    assert "sk-nestedsecret123" not in d["parsed"]["nested"][0]
    assert "[REDACTED_SECRET]" in d["stdout"]
    assert "[REDACTED_SECRET]" in d["stderr"]
    assert "[REDACTED_SECRET]" in d["parsed"]["nested"][0]


# ----------------------------------------------------------------------- isolation


# --------------------------------------------------------------------- fsconnect


def test_fsconnect_unknown_action_raises() -> None:
    with pytest.raises(OpsError):
        run_fsconnect_op("rm-rf")


def test_fsconnect_status_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout="ok")
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_fsconnect_op("status")
    argv = captured[0]
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "agentic.fsconnect.cli"]
    assert "--config" in argv and argv[-1] == "status"
    assert res.ok is True and res.label == "ok"


def test_fsconnect_list_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout='[{"name": "file.txt"}]')
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_fsconnect_op("list", root="/data", path="subdir")
    argv = captured[0]
    # --opt=value single-token form (so a leading-dash value binds to its option).
    assert "--root=/data" in argv
    assert "--path=subdir" in argv
    assert res.parsed == [{"name": "file.txt"}]


def test_fsconnect_grep_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout='{"matches": []}')
    monkeypatch.setattr(ops_runner, "_run", runner)
    run_fsconnect_op("grep", root="/data", path="file.txt", pattern="hello")
    argv = captured[0]
    assert "--pattern=hello" in argv
    assert "--regex" not in argv


def test_fsconnect_leading_dash_pattern_bound_to_option(monkeypatch: pytest.MonkeyPatch) -> None:
    # An operator grepping their notes for a literal dash-leading string
    # ("--dry-run", "-v" — the kind of flag text an ops corpus contains) must
    # travel as one --pattern=value token, else the child argparse rejects the
    # value as a stray flag and the op fails instead of returning matches.
    runner, captured = _fake_run(returncode=0, stdout='{"matches": []}')
    monkeypatch.setattr(ops_runner, "_run", runner)
    run_fsconnect_op("grep", path="notes.md", pattern="--dry-run")
    argv = captured[0]
    assert "--pattern=--dry-run" in argv
    assert "--pattern" not in argv    # no two-token form remains
    assert "--dry-run" not in argv    # the bare value is never a standalone token


def test_fsconnect_regex_rejected() -> None:
    with pytest.raises(OpsError, match="regex grep is CLI-only"):
        run_fsconnect_op("grep", path="file.txt", pattern="(a+)+$", regex=True)

def test_ops_fsconnect_request_rejects_regex_true() -> None:
    from pydantic import ValidationError
    from schemas.api import OpsFsConnectRequest

    with pytest.raises(ValidationError):
        OpsFsConnectRequest(action="grep", path="file.txt", pattern="(a+)+$", regex=True)


def test_fsconnect_glob_no_recursive(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout="[]")
    monkeypatch.setattr(ops_runner, "_run", runner)
    run_fsconnect_op("glob", root="/data", pattern="*.md", recursive=False)
    assert "--no-recursive" in captured[0]


@pytest.mark.parametrize(
    "code,ok,label",
    [(0, True, "ok"), (2, False, "failed"), (3, False, "env_config"),
     (4, False, "write_refused"), (99, False, "unknown")],
)
def test_fsconnect_exit_code_labels(monkeypatch: pytest.MonkeyPatch, code: int, ok: bool, label: str) -> None:
    runner, _ = _fake_run(returncode=code)
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_fsconnect_op("status")
    assert res.ok is ok and res.label == label


# ------------------------------------------------------------------- sqlconnect


def test_sqlconnect_unknown_action_raises() -> None:
    with pytest.raises(OpsError):
        run_sqlconnect_op("drop-table")


def test_sqlconnect_status_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout="ok")
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_sqlconnect_op("status")
    argv = captured[0]
    assert argv[0] == sys.executable
    assert argv[1:3] == ["-m", "agentic.sqlconnect.cli"]
    assert "--config" in argv and argv[-1] == "status"
    assert res.ok is True and res.label == "ok"


def test_sqlconnect_query_sql_argv(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout='{"rows": []}')
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_sqlconnect_op("query", sql="SELECT 1", fmt="csv")
    argv = captured[0]
    assert "--sql=SELECT 1" in argv                 # free text -> --opt=value form
    assert "--format" in argv and "csv" in argv     # constrained enum -> two-token is safe
    assert res.parsed == {"rows": []}


def test_sqlconnect_query_table_count(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout='{"count": 42}')
    monkeypatch.setattr(ops_runner, "_run", runner)
    run_sqlconnect_op("query", table="public.users", count=True)
    argv = captured[0]
    assert "--table=public.users" in argv
    assert "--count" in argv


def test_sqlconnect_query_explain(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, captured = _fake_run(returncode=0, stdout='{"plan": "Seq Scan"}')
    monkeypatch.setattr(ops_runner, "_run", runner)
    run_sqlconnect_op("query", sql="SELECT 1", explain=True)
    assert "--explain" in captured[0]


def test_sqlconnect_query_requires_sql_or_table() -> None:
    with pytest.raises(OpsError):
        run_sqlconnect_op("query")


@pytest.mark.parametrize(
    "code,ok,label",
    [(0, True, "ok"), (2, False, "failed"), (3, False, "env_config"), (77, False, "unknown")],
)
def test_sqlconnect_exit_code_labels(monkeypatch: pytest.MonkeyPatch, code: int, ok: bool, label: str) -> None:
    runner, _ = _fake_run(returncode=code)
    monkeypatch.setattr(ops_runner, "_run", runner)
    res = run_sqlconnect_op("status")
    assert res.ok is ok and res.label == label


# ----------------------------------------------------------------------- isolation


def test_importing_shim_does_not_import_out_of_band_packages() -> None:
    """Hard invariant: importing the shim must not import sync/ or agentic/.

    Run in a clean interpreter so prior test imports cannot mask a regression.
    """
    code = (
        "import sys; import utils.ops_runner; "
        "assert 'sync' not in sys.modules, 'ops_runner imported sync'; "
        "assert 'agentic' not in sys.modules, 'ops_runner imported agentic'; "
        "print('ISOLATED_OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ops_runner._REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert "ISOLATED_OK" in proc.stdout


# --------------------------------------------------------------------------- sync budget

def test_sync_timeout_sec_single_budget_without_post_sync_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        ops_runner, "_get_config",
        lambda _path: {"sync": {"sync_timeout_sec": 3600}},
    )
    assert ops_runner._sync_timeout_sec() == 3600 + 60


def test_sync_timeout_sec_doubles_when_post_sync_check_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    # The runner can hold the lock for one full sync_timeout_sec of rclone sync
    # PLUS a second full timeout for rclone check; the shim must not kill a
    # healthy run mid-check (mirrors sync.runner._lock_stale_after_sec).
    monkeypatch.setattr(
        ops_runner, "_get_config",
        lambda _path: {"sync": {"sync_timeout_sec": 3600, "post_sync_check": True}},
    )
    assert ops_runner._sync_timeout_sec() == 2 * 3600 + 60


def test_sync_timeout_sec_fallback_on_unreadable_config(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(_path):
        raise OSError("config missing")

    monkeypatch.setattr(ops_runner, "_get_config", _boom)
    assert ops_runner._sync_timeout_sec() == 3600 + 60
