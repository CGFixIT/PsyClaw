"""Behavioral regressions for the native installers' Python 3.12 contract."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MACOS_INSTALLER = _REPO_ROOT / "macos" / "install-cyclaw.sh"
_POWERSHELL_INSTALLER = _REPO_ROOT / "powershell" / "Install-CyClaw.ps1"


def _find_working_bash() -> str | None:
    candidates = [shutil.which("bash")]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Git\bin\bash.exe",
                r"C:\Program Files\Git\usr\bin\bash.exe",
            ]
        )
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            completed = subprocess.run(  # noqa: S603 -- fixed local shell candidates
                [candidate, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except OSError:
            continue
        if completed.returncode == 0:
            return candidate
    return None


_BASH = _find_working_bash()
_POWERSHELL = shutil.which("powershell") if os.name == "nt" else None


def _write_fake_posix_python(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env bash
set -eu
printf '%s %s\\n' "$0" "$*" >> "$FAKE_PYTHON_LOG"
case "$0" in
  */.CyClaw/venv/bin/python) version="$FAKE_VENV_VERSION" ;;
  */python3.12) version="$FAKE_PYTHON312_VERSION" ;;
  */python3) version="$FAKE_PYTHON3_VERSION" ;;
  */python) version="$FAKE_PYTHON_VERSION" ;;
  *) exit 90 ;;
esac
if [ "${1:-}" = "-c" ]; then
  printf '%s\\n' "$version"
  exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "venv" ]; then
  mkdir -p "$3/bin"
  cp "$0" "$3/bin/python"
  chmod +x "$3/bin/python"
  exit 0
fi
if [ "${1:-}" = "-m" ] && [ "${2:-}" = "pip" ]; then
  exit 0
fi
exit 91
""",
        encoding="utf-8",
        newline="\n",
    )
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _fake_posix_python_bin(tmp_path: Path) -> Path:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    for name in ("python3.12", "python3", "python"):
        _write_fake_posix_python(fake_bin / name)
    return fake_bin


def _run_macos_installer(
    tmp_path: Path,
    *,
    python312: str,
    python3: str,
    python: str,
    venv_python: str,
    skip_python_deps: bool = False,
) -> subprocess.CompletedProcess[str]:
    assert _BASH is not None
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    fake_bin = _fake_posix_python_bin(tmp_path)
    log = tmp_path / "python.log"
    env = os.environ.copy()
    env.update(
        {
            "FAKE_PYTHON312_VERSION": python312,
            "FAKE_PYTHON3_VERSION": python3,
            "FAKE_PYTHON_VERSION": python,
            "FAKE_VENV_VERSION": venv_python,
            "FAKE_PYTHON_LOG": str(log),
            "SHELL": "/bin/bash",
        }
    )
    flags = ["--repo-path", str(_REPO_ROOT), "--no-fsconnect", "--no-profile-edit", "--no-path-edit"]
    if skip_python_deps:
        flags.append("--skip-python-deps")

    if os.name == "nt":
        wrapper = """
fake_bin="$(cygpath -u "$1")"
installer="$(cygpath -u "$2")"
repo="$(cygpath -u "$3")"
home="$(cygpath -u "$4")"
export FAKE_PYTHON_LOG="$(cygpath -u "$5")"
export HOME="$home"
export PATH="$fake_bin:/usr/bin:/bin"
exec bash "$installer" --repo-path "$repo" --no-fsconnect --no-profile-edit --no-path-edit ${6:+"$6"}
"""
        command = [
            _BASH,
            "-c",
            wrapper,
            "bash",
            str(fake_bin),
            str(_MACOS_INSTALLER),
            str(_REPO_ROOT),
            str(home),
            str(log),
            "--skip-python-deps" if skip_python_deps else "",
        ]
    else:
        env["HOME"] = str(home)
        env["PATH"] = f"{fake_bin}{os.pathsep}{env.get('PATH', '')}"
        command = [_BASH, str(_MACOS_INSTALLER), *flags]

    return subprocess.run(  # noqa: S603 -- repository-owned installer and controlled fake PATH
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


@pytest.mark.skipif(_BASH is None, reason="requires a working POSIX bash")
def test_macos_installer_rejects_python_313_candidates(tmp_path: Path) -> None:
    completed = _run_macos_installer(
        tmp_path,
        python312="3.13",
        python3="3.13",
        python="3.13",
        venv_python="3.13",
    )

    assert completed.returncode != 0
    assert "Python 3.12.x not found" in completed.stderr
    assert not (tmp_path / "home" / ".CyClaw" / "venv" / "bin" / "python").exists()


@pytest.mark.skipif(_BASH is None, reason="requires a working POSIX bash")
def test_macos_installer_selects_only_candidate_reporting_312(tmp_path: Path) -> None:
    completed = _run_macos_installer(
        tmp_path,
        python312="3.13",
        python3="3.12",
        python="3.13",
        venv_python="3.12",
    )

    assert completed.returncode == 0, completed.stderr
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    assert "python3.12 -c" in log
    assert "python3 -c" in log
    assert "python3 -m venv" in log
    assert "python3.12 -m venv" not in log


@pytest.mark.skipif(_BASH is None, reason="requires a working POSIX bash")
def test_macos_installer_refuses_existing_non_312_venv(tmp_path: Path) -> None:
    stale_python = tmp_path / "home" / ".CyClaw" / "venv" / "bin" / "python"
    stale_python.parent.mkdir(parents=True)
    _write_fake_posix_python(stale_python)

    completed = _run_macos_installer(
        tmp_path,
        python312="3.12",
        python3="3.13",
        python="3.13",
        venv_python="3.13",
    )

    assert completed.returncode != 0
    assert "Existing virtual environment" in completed.stderr
    assert "not Python 3.12.x" in completed.stderr
    assert "will not replace it automatically" in completed.stderr
    assert stale_python.is_file()
    assert " -m pip" not in (tmp_path / "python.log").read_text(encoding="utf-8")


@pytest.mark.skipif(_BASH is None, reason="requires a working POSIX bash")
def test_macos_skip_python_deps_does_not_reject_existing_venv(tmp_path: Path) -> None:
    stale_python = tmp_path / "home" / ".CyClaw" / "venv" / "bin" / "python"
    stale_python.parent.mkdir(parents=True)
    _write_fake_posix_python(stale_python)

    completed = _run_macos_installer(
        tmp_path,
        python312="3.13",
        python3="3.13",
        python="3.13",
        venv_python="3.13",
        skip_python_deps=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert stale_python.is_file()
    assert " -m pip" not in (tmp_path / "python.log").read_text(encoding="utf-8")


def _write_fake_windows_launchers(fake_bin: Path) -> None:
    fake_bin.mkdir()
    (fake_bin / "py.cmd").write_text(
        """@echo off
>>"%FAKE_PYTHON_LOG%" echo py %*
if "%1"=="-0p" (
  echo -V:3.12 C:\\advertised-but-missing\\python.exe
  exit /b 0
)
if not "%1"=="-3.12" exit /b 91
if "%FAKE_PY312_VERSION%"=="missing" exit /b 92
echo %FAKE_PY312_VERSION%
""",
        encoding="ascii",
    )
    (fake_bin / "python.cmd").write_text(
        """@echo off
>>"%FAKE_PYTHON_LOG%" echo python %*
echo %FAKE_PATH_PYTHON_VERSION%
""",
        encoding="ascii",
    )


def _run_powershell_installer(
    tmp_path: Path,
    *,
    py312: str,
    path_python: str,
    skip_python_deps: bool,
) -> subprocess.CompletedProcess[str]:
    assert _POWERSHELL is not None
    fake_bin = tmp_path / "fake-bin"
    _write_fake_windows_launchers(fake_bin)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    log = tmp_path / "python.log"
    env = os.environ.copy()
    for key in tuple(env):
        if key.upper() == "PATH":
            del env[key]
    env.update(
        {
            "PATH": str(fake_bin),
            "USERPROFILE": str(home),
            "FAKE_PYTHON_LOG": str(log),
            "FAKE_PY312_VERSION": py312,
            "FAKE_PATH_PYTHON_VERSION": path_python,
        }
    )
    command = [
        _POWERSHELL,
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(_POWERSHELL_INSTALLER),
        "-RepoPath",
        str(_REPO_ROOT),
        "-NoProfileEdit",
        "-NoPathEdit",
    ]
    if skip_python_deps:
        command.append("-SkipPythonDeps")
    return subprocess.run(  # noqa: S603 -- repository-owned installer and controlled fake PATH
        command,
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )


@pytest.mark.skipif(_POWERSHELL is None, reason="requires Windows PowerShell")
def test_powershell_probes_exact_py312_launcher_and_rejects_313(tmp_path: Path) -> None:
    completed = _run_powershell_installer(
        tmp_path,
        py312="3.13",
        path_python="3.13",
        skip_python_deps=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Python 3.12.x not found" in completed.stdout
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    assert "py -3.12 -c" in log
    assert "py -0p" not in log
    assert "python -c" in log


@pytest.mark.skipif(_POWERSHELL is None, reason="requires Windows PowerShell")
def test_powershell_falls_back_when_exact_py312_launcher_is_missing(tmp_path: Path) -> None:
    completed = _run_powershell_installer(
        tmp_path,
        py312="missing",
        path_python="3.12",
        skip_python_deps=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "Python 3.12.x not found" not in completed.stdout
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    assert "py -3.12 -c" in log
    assert "py -0p" not in log
    assert "python -c" in log


@pytest.mark.skipif(_POWERSHELL is None, reason="requires Windows PowerShell")
def test_powershell_path_fallback_invokes_full_python_command(tmp_path: Path) -> None:
    completed = _run_powershell_installer(
        tmp_path,
        py312="missing",
        path_python="3.12",
        skip_python_deps=False,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "venv creation failed" in combined
    assert "The term 'p'" not in combined
    log = (tmp_path / "python.log").read_text(encoding="utf-8")
    assert "python -m venv" in log


@pytest.mark.skipif(_POWERSHELL is None, reason="requires Windows PowerShell")
def test_powershell_installer_refuses_existing_invalid_venv(tmp_path: Path) -> None:
    stale_python = tmp_path / "home" / ".CyClaw" / "venv" / "Scripts" / "python.exe"
    stale_python.parent.mkdir(parents=True)
    system_root = Path(os.environ["SystemRoot"])
    shutil.copy2(system_root / "System32" / "where.exe", stale_python)

    completed = _run_powershell_installer(
        tmp_path,
        py312="3.12",
        path_python="3.13",
        skip_python_deps=False,
    )

    combined = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "Existing virtual environment" in combined
    assert "not Python 3.12.x" in combined
    assert "will not replace it automatically" in combined
    assert stale_python.is_file()
