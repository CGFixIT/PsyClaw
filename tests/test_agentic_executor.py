"""Tests for agentic.executor -- the sandboxed verification runner.

Real subprocesses are used throughout (short ``python -c`` one-liners), not
mocked ``subprocess.run`` calls: the point of this module is the actual argv/
cwd/env/timeout plumbing, and a mock would only prove the mock was configured
the way the author expected. No network access is attempted or required.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from agentic.executor import Check, VerificationReport, default_checks, run_verification
from agentic.executor.runner import MAX_OUTPUT_CHARS, _scrubbed_env


def _py(code: str, timeout_sec: int = 10) -> Check:
    return Check("probe", (sys.executable, "-c", code), timeout_sec=timeout_sec)


@pytest.fixture(autouse=True)
def _temp_audit(tmp_path, monkeypatch):
    import yaml

    from utils.logger import _get_config, reset_config_cache

    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
           "policy": {"privacy": {}}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reset_config_cache()
    _get_config(str(path))
    yield
    reset_config_cache()


# --- basic execution ---------------------------------------------------------


def test_a_passing_check_reports_ok(tmp_path):
    report = run_verification(tmp_path, [_py("import sys; sys.exit(0)")])
    assert isinstance(report, VerificationReport)
    assert report.ok is True
    assert report.results[0].exit_code == 0
    assert report.results[0].ok is True
    assert report.failed_names() == ()


def test_a_failing_check_reports_not_ok(tmp_path):
    report = run_verification(tmp_path, [_py("import sys; sys.exit(1)")])
    assert report.ok is False
    assert report.results[0].exit_code == 1
    assert report.failed_names() == ("probe",)


def test_one_failure_among_several_fails_the_whole_report(tmp_path):
    checks = [
        Check("a", (sys.executable, "-c", "import sys; sys.exit(0)")),
        Check("b", (sys.executable, "-c", "import sys; sys.exit(1)")),
        Check("c", (sys.executable, "-c", "import sys; sys.exit(0)")),
    ]
    report = run_verification(tmp_path, checks)
    assert report.ok is False
    assert report.failed_names() == ("b",)
    assert len(report.results) == 3


def test_empty_checks_list_is_vacuously_ok(tmp_path):
    report = run_verification(tmp_path, [])
    assert report.ok is True
    assert report.results == ()


def test_stdout_and_stderr_are_captured(tmp_path):
    report = run_verification(
        tmp_path,
        [_py("import sys; sys.stdout.write('out-line'); sys.stderr.write('err-line'); sys.exit(1)")],
    )
    assert "out-line" in report.results[0].stdout
    assert "err-line" in report.results[0].stderr


def test_large_output_is_truncated(tmp_path):
    report = run_verification(tmp_path, [_py(f"import sys; sys.stdout.write('x' * {MAX_OUTPUT_CHARS + 5000})")])
    out = report.results[0].stdout
    assert len(out) < MAX_OUTPUT_CHARS + 100
    assert "truncated" in out


# --- cwd -----------------------------------------------------------------


def test_check_runs_with_cwd_pinned_to_the_worktree(tmp_path):
    marker = tmp_path / "marker.txt"
    marker.write_text("here", encoding="utf-8")
    report = run_verification(
        tmp_path,
        [_py("import pathlib,sys; sys.exit(0 if pathlib.Path('marker.txt').is_file() else 1)")],
    )
    assert report.ok is True


# --- timeout ---------------------------------------------------------------


def test_a_hung_check_times_out_without_crashing_the_run(tmp_path):
    checks = [
        _py("import time; time.sleep(5)", timeout_sec=1),
        _py("import sys; sys.exit(0)"),
    ]
    started = time.monotonic()
    report = run_verification(tmp_path, checks)
    elapsed = time.monotonic() - started
    assert elapsed < 4  # the hang must not block the whole call for 5s
    assert report.ok is False
    assert report.results[0].timed_out is True
    assert report.results[0].ok is False
    # The second, well-behaved check still ran -- one hung check must not
    # abort the rest of the batch.
    assert report.results[1].ok is True


# --- environment scrubbing ---------------------------------------------------


def test_child_does_not_inherit_secret_shaped_env_vars(tmp_path, monkeypatch):
    monkeypatch.setenv("CYCLAW_API_KEY", "should-not-leak")
    monkeypatch.setenv("GROK_API_KEY", "should-not-leak-either")
    monkeypatch.setenv("HTTPS_PROXY", "http://should-not-leak:8080")
    report = run_verification(
        tmp_path,
        [_py("import os,sys; sys.exit(0 if 'CYCLAW_API_KEY' not in os.environ "
             "and 'GROK_API_KEY' not in os.environ and 'HTTPS_PROXY' not in os.environ else 1)")],
    )
    assert report.ok is True


def test_child_still_has_path_to_find_the_interpreter(tmp_path):
    # A totally empty environment would make `python` itself unfindable via
    # argv[0] resolution in some shells -- confirm PATH survives the scrub.
    report = run_verification(tmp_path, [_py("import os,sys; sys.exit(0 if os.environ.get('PATH') else 1)")])
    assert report.ok is True


def test_scrubbed_env_sets_no_proxy_and_pip_no_index(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://example:8080")
    env = _scrubbed_env()
    assert env["NO_PROXY"] == "*"
    assert env["PIP_NO_INDEX"] == "1"
    assert "HTTPS_PROXY" not in env
    assert "HTTP_PROXY" not in env


# --- audit -------------------------------------------------------------------


def test_each_check_emits_an_audit_event(tmp_path):
    from agentic.executor import runner as runner_module

    calls = []
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(runner_module, "audit_log", lambda event, **kw: calls.append(event))
        run_verification(tmp_path, [
            _py("import sys; sys.exit(0)"),
            _py("import sys; sys.exit(1)"),
        ])
    assert len(calls) == 2
    assert calls[0]["event"] == "agentic_executor_check_result"
    assert calls[0]["ok"] is True
    assert calls[1]["ok"] is False


# --- Check validation --------------------------------------------------------


def test_check_rejects_empty_name():
    with pytest.raises(ValueError, match="name"):
        Check("", (sys.executable,))


def test_check_rejects_empty_argv():
    with pytest.raises(ValueError, match="argv"):
        Check("x", ())


# --- default_checks ----------------------------------------------------------


def test_default_checks_shape():
    checks = default_checks(Path("/some/repo"))
    names = [c.name for c in checks]
    assert names == ["pytest", "ruff", "invariant_guard"]
    pytest_check, ruff_check, guard_check = checks
    assert pytest_check.argv[:3] == (sys.executable, "-m", "pytest")
    assert ruff_check.argv[:3] == (sys.executable, "-m", "ruff")
    assert str(Path("/some/repo") / ".claude" / "skills" / "invariant-guard" / "check_invariants.py") \
        in guard_check.argv


def test_default_checks_defaults_to_this_repos_own_root():
    checks = default_checks()
    guard_check = next(c for c in checks if c.name == "invariant_guard")
    assert "invariant-guard" in guard_check.argv[-1]
    assert Path(guard_check.argv[-1]).name == "check_invariants.py"
