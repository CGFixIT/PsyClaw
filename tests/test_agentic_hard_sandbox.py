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
    ArgvListSandbox,
    DarwinSeatbeltSandbox,
    HardSandboxUnavailable,
    LinuxNetnsSandbox,
    WindowsJobObjectSandbox,
    _assign_pid,
    _create_job,
    _kill_sandbox_tree,
    production_sandbox,
    seatbelt_profile,
    stream_to_str,
    truncate_output,
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


def test_stream_to_str_none_str_and_bytes() -> None:
    assert stream_to_str(None) == ""
    assert stream_to_str("already text") == "already text"
    assert stream_to_str(b"bytes\xfftext") == "bytes\ufffdtext"


def test_truncate_output_passthrough_and_cap() -> None:
    from agentic.executor import hard_sandbox as hs

    assert truncate_output("short") == "short"
    huge = "x" * (hs.MAX_OUTPUT_CHARS + 10)
    out = truncate_output(huge)
    assert out.startswith("x" * hs.MAX_OUTPUT_CHARS)
    assert "truncated" in out


def test_production_sandbox_selects_darwin_and_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr("agentic.executor.hard_sandbox.shutil.which", lambda cmd: "/usr/bin/sandbox-exec")
    assert isinstance(production_sandbox(), DarwinSeatbeltSandbox)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("agentic.executor.hard_sandbox.shutil.which", lambda cmd: "/usr/bin/unshare")

    class _Probe:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(
        "agentic.executor.hard_sandbox.subprocess.run",
        lambda *a, **k: _Probe(),
    )
    assert isinstance(production_sandbox(), LinuxNetnsSandbox)

    monkeypatch.setattr(sys, "platform", "aix")
    with pytest.raises(HardSandboxUnavailable, match="no hard-sandbox backend"):
        production_sandbox()


def test_darwin_seatbelt_run_wraps_argv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentic.executor.hard_sandbox.shutil.which", lambda cmd: "/usr/bin/sandbox-exec")
    seen: dict[str, object] = {}

    def _fake_run(self, argv, *, cwd, env, timeout_sec):  # noqa: ANN001
        seen["argv"] = list(argv)
        seen["env"] = dict(env)
        from agentic.executor.hard_sandbox import SandboxOutcome

        return SandboxOutcome(exit_code=0, stdout="ok")

    monkeypatch.setattr(ArgvListSandbox, "run", _fake_run)
    backend = DarwinSeatbeltSandbox()
    outcome = backend.run([sys.executable, "-c", "pass"], cwd=tmp_path, env={"PATH": "/bin"}, timeout_sec=5)
    assert outcome.exit_code == 0
    assert seen["argv"][0] == "/usr/bin/sandbox-exec"
    assert seen["argv"][1] == "-p"
    assert "--" in seen["argv"]
    assert "TMPDIR" in seen["env"]  # type: ignore[operator]


def test_linux_netns_probe_oserror_and_nonzero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentic.executor.hard_sandbox.shutil.which", lambda cmd: "/usr/bin/unshare")

    def _oserror(*_a, **_k):
        raise OSError("probe blocked")

    monkeypatch.setattr("agentic.executor.hard_sandbox.subprocess.run", _oserror)
    with pytest.raises(HardSandboxUnavailable, match="could not run"):
        LinuxNetnsSandbox()

    class _Bad:
        returncode = 1
        stderr = b"EPERM"

    monkeypatch.setattr(
        "agentic.executor.hard_sandbox.subprocess.run",
        lambda *a, **k: _Bad(),
    )
    with pytest.raises(HardSandboxUnavailable, match="probe failed"):
        LinuxNetnsSandbox()


def test_linux_netns_run_wraps_unshare(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agentic.executor.hard_sandbox.shutil.which", lambda cmd: "/usr/bin/unshare")

    class _Probe:
        returncode = 0
        stderr = b""

    monkeypatch.setattr(
        "agentic.executor.hard_sandbox.subprocess.run",
        lambda *a, **k: _Probe(),
    )
    seen: dict[str, object] = {}

    def _fake_run(self, argv, *, cwd, env, timeout_sec):  # noqa: ANN001
        seen["argv"] = list(argv)
        from agentic.executor.hard_sandbox import SandboxOutcome

        return SandboxOutcome(exit_code=0)

    monkeypatch.setattr(ArgvListSandbox, "run", _fake_run)
    backend = LinuxNetnsSandbox()
    outcome = backend.run(["echo", "hi"], cwd=tmp_path, env={}, timeout_sec=2)
    assert outcome.exit_code == 0
    assert seen["argv"][:3] == ["/usr/bin/unshare", "--net", "--"]


def test_argv_list_sets_start_new_session_off_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    monkeypatch.setattr(sys, "platform", "linux")
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0

        def communicate(self, timeout=None):
            return ("", "")

    def _popen(argv, **kwargs):
        captured.update(kwargs)
        return _Proc()

    monkeypatch.setattr(subprocess, "Popen", _popen)
    ArgvListSandbox().run(["true"], cwd=tmp_path, env={}, timeout_sec=1)
    assert captured.get("start_new_session") is True


def test_kill_sandbox_tree_posix_uses_killpg(monkeypatch: pytest.MonkeyPatch) -> None:
    import signal

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)
    calls: list[tuple[int, int]] = []

    def _killpg(pid: int, sig: int) -> None:
        calls.append((pid, sig))

    monkeypatch.setattr(os, "killpg", _killpg, raising=False)

    class _Proc:
        pid = 4242

        def kill(self) -> None:
            raise AssertionError("killpg succeeded; proc.kill must not run")

    _kill_sandbox_tree(_Proc())  # type: ignore[arg-type]
    assert calls == [(4242, 9)]


def test_kill_sandbox_tree_posix_falls_back_on_oserror(monkeypatch: pytest.MonkeyPatch) -> None:
    import signal

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(signal, "SIGKILL", 9, raising=False)

    def _boom(pid: int, sig: int) -> None:
        raise OSError("no process group")

    monkeypatch.setattr(os, "killpg", _boom, raising=False)
    killed = {"n": 0}

    class _Proc:
        pid = 7

        def kill(self) -> None:
            killed["n"] += 1

    _kill_sandbox_tree(_Proc())  # type: ignore[arg-type]
    assert killed["n"] == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object helpers are Windows-only")
def test_job_object_popen_fallback_and_assign_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    class _BoomProc:
        def __init__(self, *a, **k):
            raise OSError("create failed")

    monkeypatch.setattr(subprocess, "Popen", _BoomProc)
    outcome = WindowsJobObjectSandbox().run(
        ["missing-bin"], cwd=tmp_path, env={}, timeout_sec=1
    )
    assert outcome.exit_code == -2
    assert "could not execute" in outcome.stderr

    class _Alive:
        pid = 999001
        returncode = None

        def __init__(self, *a, **k):
            pass

        def kill(self) -> None:
            self.returncode = -1

        def communicate(self, timeout=None):
            return ("", "")

    monkeypatch.setattr(subprocess, "Popen", _Alive)
    monkeypatch.setattr("agentic.executor.hard_sandbox._assign_pid", lambda job, pid: False)
    outcome = WindowsJobObjectSandbox().run(
        [sys.executable, "-c", "pass"], cwd=tmp_path, env={}, timeout_sec=1
    )
    assert outcome.exit_code == -2
    assert "AssignProcessToJobObject failed" in outcome.stderr


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object helpers are Windows-only")
def test_job_object_double_timeout_drain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    class _Hung:
        pid = 999002
        returncode = None
        _n = 0

        def __init__(self, *a, **k):
            pass

        def communicate(self, timeout=None):
            self._n += 1
            if self._n == 1:
                raise subprocess.TimeoutExpired(cmd=["x"], timeout=timeout or 1)
            if self._n == 2:
                raise subprocess.TimeoutExpired(cmd=["x"], timeout=timeout or 5)
            return ("drained", "")

        def kill(self) -> None:
            self.returncode = -1

    monkeypatch.setattr(subprocess, "Popen", _Hung)
    monkeypatch.setattr("agentic.executor.hard_sandbox._assign_pid", lambda job, pid: True)
    monkeypatch.setattr("agentic.executor.hard_sandbox._terminate_job", lambda job: None)
    outcome = WindowsJobObjectSandbox().run(
        [sys.executable, "-c", "pass"], cwd=tmp_path, env={}, timeout_sec=1
    )
    assert outcome.timed_out is True
    assert outcome.exit_code == -1


@pytest.mark.skipif(sys.platform != "win32", reason="Job Object helpers are Windows-only")
def test_create_job_and_assign_pid_failure_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    import agentic.executor.hard_sandbox as hs

    class _FakeK32:
        def CreateJobObjectW(self, *_a):
            return 0

        def SetInformationJobObject(self, *_a):
            return 0

        def CloseHandle(self, *_a):
            return 1

        def OpenProcess(self, *_a):
            return 0

        def AssignProcessToJobObject(self, *_a):
            return 0

    def _fake_kernel():
        import ctypes

        return ctypes, ctypes.wintypes, _FakeK32()

    monkeypatch.setattr(hs, "_win_kernel32", _fake_kernel)
    with pytest.raises(HardSandboxUnavailable, match="CreateJobObjectW"):
        _create_job()

    class _FakeK32SetFail(_FakeK32):
        def CreateJobObjectW(self, *_a):
            return 1234

    def _fake_kernel_set():
        import ctypes

        return ctypes, ctypes.wintypes, _FakeK32SetFail()

    monkeypatch.setattr(hs, "_win_kernel32", _fake_kernel_set)
    with pytest.raises(HardSandboxUnavailable, match="SetInformationJobObject"):
        _create_job()

    assert _assign_pid(1, 2) is False
