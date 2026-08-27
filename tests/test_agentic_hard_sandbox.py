"""Production hard-sandbox selection and Windows Job Object.

These tests do NOT inject ArgvListSandbox. Linux CI asserts fail-closed.
Windows CI / this host assert Job Object actually runs python -c.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from agentic.executor.hard_sandbox import (
    HardSandboxUnavailable,
    WindowsJobObjectSandbox,
    production_sandbox,
)
from agentic.executor.runner import Check, run_verification
from utils.errors import AgenticError


def _py(code: str, timeout_sec: int = 10) -> Check:
    return Check("probe", (sys.executable, "-c", code), timeout_sec=timeout_sec)


def test_production_sandbox_is_fail_closed_off_windows() -> None:
    if sys.platform == "win32":
        backend = production_sandbox()
        assert isinstance(backend, WindowsJobObjectSandbox)
        return
    with pytest.raises(HardSandboxUnavailable, match="fails closed"):
        production_sandbox()


def test_run_verification_without_injected_sandbox_fails_closed_off_windows(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Windows has a production backend")
    with pytest.raises(AgenticError, match="HARD_SANDBOX_UNAVAILABLE|fails closed|no hard-sandbox"):
        run_verification(tmp_path, [_py("import sys; sys.exit(0)")])


def test_empty_checks_do_not_require_a_backend(tmp_path: Path) -> None:
    report = run_verification(tmp_path, [])
    assert report.ok is True
    assert report.results == ()


def test_no_env_flag_selects_argv_list_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYCLAW_SOFT_SANDBOX", "1")
    monkeypatch.setenv("CYCLAW_ALLOW_SOFT_SANDBOX", "1")
    if sys.platform == "win32":
        report = run_verification(tmp_path, [_py("import sys; sys.exit(0)")])
        assert report.ok is True
        return
    with pytest.raises(HardSandboxUnavailable):
        run_verification(tmp_path, [_py("import sys; sys.exit(0)")])


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object is the Windows backend")
def test_job_object_runs_a_passing_check(tmp_path: Path) -> None:
    report = run_verification(tmp_path, [_py("import sys; sys.exit(0)")])
    assert report.ok is True
    assert report.results[0].exit_code == 0


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object is the Windows backend")
def test_job_object_times_out_a_hung_check(tmp_path: Path) -> None:
    report = run_verification(tmp_path, [_py("import time; time.sleep(30)", timeout_sec=1)])
    assert report.ok is False
    assert report.results[0].timed_out is True


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object is the Windows backend")
def test_job_object_does_not_inherit_api_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROK_API_KEY", "must-not-leak")
    monkeypatch.setenv("CYCLAW_API_KEY", "must-not-leak")
    report = run_verification(
        tmp_path,
        [_py(
            "import os,sys; sys.exit(0 if 'GROK_API_KEY' not in os.environ "
            "and 'CYCLAW_API_KEY' not in os.environ else 1)"
        )],
    )
    assert report.ok is True


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object is the Windows backend")
def test_job_object_uses_disposable_home_not_operator_home(tmp_path: Path) -> None:
    operator_home = os.environ.get("USERPROFILE") or os.environ.get("HOME") or ""
    report = run_verification(
        tmp_path,
        [_py(
            "import os,sys; home=os.environ.get('USERPROFILE') or os.environ.get('HOME',''); "
            f"sys.exit(0 if 'cyclaw-exec-home-' in home and home != {operator_home!r} else 1)"
        )],
    )
    assert report.ok is True
