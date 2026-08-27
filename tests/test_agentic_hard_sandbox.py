"""Production hard-sandbox selection and Windows Job Object.

These tests do NOT inject ArgvListSandbox. Missing Darwin/Linux binaries
fail closed. Windows CI / this host assert Job Object actually runs python -c.
Seatbelt/netns profile and missing-binary paths are unit-tested; this file
does not claim a live ``sandbox-exec`` run on Windows.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from agentic.executor.hard_sandbox import (
    DarwinSeatbeltSandbox,
    HardSandboxUnavailable,
    LinuxNetnsSandbox,
    WindowsJobObjectSandbox,
    production_sandbox,
    seatbelt_profile,
)
from agentic.executor.runner import Check, run_verification
from utils.errors import AgenticError


def _py(code: str, timeout_sec: int = 10) -> Check:
    return Check("probe", (sys.executable, "-c", code), timeout_sec=timeout_sec)


def test_production_sandbox_win32_is_job_object() -> None:
    if sys.platform != "win32":
        pytest.skip("Job Object is the Windows backend")
    backend = production_sandbox()
    assert isinstance(backend, WindowsJobObjectSandbox)


def test_production_sandbox_is_fail_closed_off_windows() -> None:
    if sys.platform == "win32":
        backend = production_sandbox()
        assert isinstance(backend, WindowsJobObjectSandbox)
        return
    try:
        backend = production_sandbox()
    except HardSandboxUnavailable as exc:
        assert "fails closed" in str(exc).lower() or "fail" in str(exc).lower()
        return
    if sys.platform == "darwin":
        assert isinstance(backend, DarwinSeatbeltSandbox)
    elif sys.platform.startswith("linux"):
        assert isinstance(backend, LinuxNetnsSandbox)
    else:
        pytest.fail(f"unexpected backend on {sys.platform!r}")


def test_run_verification_without_injected_sandbox_fails_closed_off_windows(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("Windows has a production backend")
    try:
        production_sandbox()
    except HardSandboxUnavailable:
        with pytest.raises(AgenticError, match="HARD_SANDBOX_UNAVAILABLE|fails closed|no hard-sandbox"):
            run_verification(tmp_path, [_py("import sys; sys.exit(0)")])
        return
    report = run_verification(tmp_path, [_py("import sys; sys.exit(0)")])
    assert report.ok is True


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
    try:
        production_sandbox()
    except HardSandboxUnavailable:
        with pytest.raises(HardSandboxUnavailable):
            run_verification(tmp_path, [_py("import sys; sys.exit(0)")])
        return
    report = run_verification(tmp_path, [_py("import sys; sys.exit(0)")])
    assert report.ok is True


def test_seatbelt_profile_contains_deny_network(tmp_path: Path) -> None:
    profile = seatbelt_profile(tmp_path)
    assert "deny network" in profile
    assert "file-write" in profile
    assert str(tmp_path.resolve()).replace("\\", "\\\\") in profile or str(tmp_path.resolve()) in profile


def test_seatbelt_profile_allows_a_writable_tmpdir(tmp_path: Path) -> None:
    cwd = tmp_path / "work"
    tmp = tmp_path / "tmp"
    cwd.mkdir()
    tmp.mkdir()
    profile = seatbelt_profile(cwd, tmp)
    assert "require-any" in profile
    assert str(tmp.resolve()).replace("\\", "\\\\") in profile or str(tmp.resolve()) in profile


def test_darwin_seatbelt_missing_binary_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    real_which = shutil.which

    def _which(cmd: str) -> str | None:
        if cmd == "sandbox-exec":
            return None
        return real_which(cmd)

    monkeypatch.setattr("agentic.executor.hard_sandbox.shutil.which", _which)
    with pytest.raises(HardSandboxUnavailable, match="sandbox-exec"):
        DarwinSeatbeltSandbox()


def test_linux_netns_missing_binary_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    real_which = shutil.which

    def _which(cmd: str) -> str | None:
        if cmd == "unshare":
            return None
        return real_which(cmd)

    monkeypatch.setattr("agentic.executor.hard_sandbox.shutil.which", _which)
    with pytest.raises(HardSandboxUnavailable, match="unshare"):
        LinuxNetnsSandbox()


def test_production_sandbox_never_returns_argv_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even with soft-sandbox env flags, production_sandbox stays hard."""
    monkeypatch.setenv("CYCLAW_SOFT_SANDBOX", "1")
    if sys.platform == "win32":
        assert isinstance(production_sandbox(), WindowsJobObjectSandbox)
        return
    monkeypatch.setattr(
        "agentic.executor.hard_sandbox.shutil.which",
        lambda _cmd: None,
    )
    with pytest.raises(HardSandboxUnavailable):
        production_sandbox()


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
