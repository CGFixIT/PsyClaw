"""Hard sandbox backends for agentic verification.

Production ``run_verification`` must not call unconstrained ``subprocess.run``.
Windows uses a Job Object (kill-on-close + active-process cap). Other platforms
raise ``HardSandboxUnavailable`` -- fail closed, no software fallback.

``ArgvListSandbox`` is the old argv-list ``subprocess.run`` path, imported by
tests only. It is not selected by ``production_sandbox``.
"""

from __future__ import annotations

import subprocess  # noqa: S404 -- argv-list only; no shell
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from utils.errors import AgenticError

MAX_OUTPUT_CHARS = 20_000

# Same cap as the previous runner: a runaway pytest dump must not blow memory.
_JOB_ACTIVE_PROCESS_LIMIT = 32

# Windows Job Object flags (winnt.h).
_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_KILL_ON_CLOSE = 0x00002000
_JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
_CREATE_BREAKAWAY_FROM_JOB = 0x01000000
_PROCESS_SET_QUOTA = 0x0100
_PROCESS_TERMINATE = 0x0001


class HardSandboxUnavailable(AgenticError):
    """No supported hard-sandbox backend on this platform / host."""

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message, code="HARD_SANDBOX_UNAVAILABLE", details=details)


@dataclass(frozen=True)
class SandboxOutcome:
    """Backend result before the runner attaches the Check name."""

    exit_code: int
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False


class HardSandbox(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_sec: int,
    ) -> SandboxOutcome:
        """Run argv inside the sandbox. Never uses shell=True."""


def truncate_output(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n... [output truncated at {MAX_OUTPUT_CHARS} chars]"


def stream_to_str(stream: str | bytes | None) -> str:
    """TimeoutExpired.stdout is str on Windows text mode, bytes on POSIX."""
    if stream is None:
        return ""
    if isinstance(stream, str):
        return stream
    return stream.decode("utf-8", errors="replace")


class ArgvListSandbox:
    """Test double: the pre-Phase-4 argv-list subprocess.run path.

    Production ``production_sandbox()`` never returns this. Tests inject it so
    Linux CI can still exercise timeout/truncate/cwd plumbing without claiming
    a kernel sandbox.
    """

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_sec: int,
    ) -> SandboxOutcome:
        try:
            completed = subprocess.run(  # noqa: S603 -- argv list, no shell
                list(argv),
                cwd=str(cwd),
                env=dict(env),
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return SandboxOutcome(
                exit_code=-1,
                stdout=truncate_output(stream_to_str(exc.stdout)),
                stderr=f"timed out after {timeout_sec}s",
                timed_out=True,
            )
        except OSError as exc:
            return SandboxOutcome(
                exit_code=-2,
                stderr=f"could not execute {argv[0]!r}: {exc}",
            )
        return SandboxOutcome(
            exit_code=completed.returncode,
            stdout=truncate_output(completed.stdout or ""),
            stderr=truncate_output(completed.stderr or ""),
        )


def production_sandbox() -> HardSandbox:
    """Return the host backend, or raise. Never falls back to ArgvListSandbox."""
    if sys.platform == "win32":
        return WindowsJobObjectSandbox()
    raise HardSandboxUnavailable(
        f"no hard-sandbox backend for platform {sys.platform!r}; "
        "agentic verification fails closed (issue #1134 Phase 4). "
        "Windows Job Object is the shipped backend; Darwin Seatbelt / Linux "
        "netns are not implemented in this PR.",
        details={"platform": sys.platform},
    )


class WindowsJobObjectSandbox:
    """Assign the child to a Job Object with KILL_ON_JOB_CLOSE.

    This is a process-tree kill boundary, not a network namespace. Sockets
    still work until a later Phase 4 slice. Assign failure fails the check
    rather than running unconstrained.
    """

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        env: Mapping[str, str],
        timeout_sec: int,
    ) -> SandboxOutcome:
        job = _create_job()
        try:
            try:
                proc = subprocess.Popen(  # noqa: S603 -- argv list, no shell
                    list(argv),
                    cwd=str(cwd),
                    env=dict(env),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    creationflags=_CREATE_BREAKAWAY_FROM_JOB,
                )
            except OSError:
                try:
                    proc = subprocess.Popen(  # noqa: S603 -- still assigned to the job below
                        list(argv),
                        cwd=str(cwd),
                        env=dict(env),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                    )
                except OSError as exc:
                    return SandboxOutcome(
                        exit_code=-2,
                        stderr=f"could not execute {argv[0]!r}: {exc}",
                    )
            if not _assign_pid(job, proc.pid):
                proc.kill()
                proc.communicate()
                return SandboxOutcome(
                    exit_code=-2,
                    stderr="AssignProcessToJobObject failed; refusing unconstrained run",
                )
            try:
                stdout, stderr = proc.communicate(timeout=timeout_sec)
            except subprocess.TimeoutExpired:
                _terminate_job(job)
                try:
                    stdout, stderr = proc.communicate(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    stdout, stderr = proc.communicate()
                return SandboxOutcome(
                    exit_code=-1,
                    stdout=truncate_output(stream_to_str(stdout)),
                    stderr=f"timed out after {timeout_sec}s",
                    timed_out=True,
                )
            return SandboxOutcome(
                exit_code=proc.returncode if proc.returncode is not None else -1,
                stdout=truncate_output(stdout or ""),
                stderr=truncate_output(stderr or ""),
            )
        finally:
            _close_handle(job)


def _win_kernel32():  # type: ignore[no-untyped-def]
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    k32.CreateJobObjectW.restype = wintypes.HANDLE
    k32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
    ]
    k32.SetInformationJobObject.restype = wintypes.BOOL
    k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    k32.AssignProcessToJobObject.restype = wintypes.BOOL
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateJobObject.restype = wintypes.BOOL
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    return ctypes, wintypes, k32


def _create_job() -> int:
    ctypes, wintypes, k32 = _win_kernel32()

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [("x", ctypes.c_uint64 * 6)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    handle = k32.CreateJobObjectW(None, None)
    if not handle:
        raise HardSandboxUnavailable(
            "CreateJobObjectW failed",
            details={"winerror": ctypes.get_last_error()},
        )
    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = (
        _JOB_OBJECT_LIMIT_KILL_ON_CLOSE | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS
    )
    info.BasicLimitInformation.ActiveProcessLimit = _JOB_ACTIVE_PROCESS_LIMIT
    ok = k32.SetInformationJobObject(
        handle,
        _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        k32.CloseHandle(handle)
        raise HardSandboxUnavailable(
            "SetInformationJobObject failed",
            details={"winerror": ctypes.get_last_error()},
        )
    return int(handle)


def _assign_pid(job: int, pid: int) -> bool:
    ctypes, _wintypes, k32 = _win_kernel32()
    access = _PROCESS_SET_QUOTA | _PROCESS_TERMINATE
    proc_handle = k32.OpenProcess(access, False, pid)
    if not proc_handle:
        return False
    try:
        return bool(k32.AssignProcessToJobObject(job, proc_handle))
    finally:
        k32.CloseHandle(proc_handle)


def _terminate_job(job: int) -> None:
    _ctypes, _wintypes, k32 = _win_kernel32()
    k32.TerminateJobObject(job, 1)


def _close_handle(job: int) -> None:
    _ctypes, _wintypes, k32 = _win_kernel32()
    k32.CloseHandle(job)
