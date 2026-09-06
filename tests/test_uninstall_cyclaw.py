"""Behavior tests for macos/uninstall-cyclaw.sh Keychain purge and port free.

No real Keychain and no Darwin required. CYCLAW_UNINSTALL_TEST_MODE=1 lets
the Darwin-only purge path run against a fake `security` on Linux CI.
"""

from __future__ import annotations

import os
import shutil
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "macos" / "uninstall-cyclaw.sh"
_BASH = shutil.which("bash") or "bash"

_FAKE_SECURITY = """#!/usr/bin/env bash
set -euo pipefail
cmd="$1"; shift
case "$cmd" in
  delete-generic-password)
    account=""
    service=""
    args_log=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -a)
          account="$2"
          args_log="$args_log $1 $2"
          shift 2
          ;;
        -s)
          service="$2"
          args_log="$args_log $1 $2"
          shift 2
          ;;
        -w)
          echo "fake security: refuse -w on delete (secret must not be argv)" >&2
          exit 2
          ;;
        *)
          args_log="$args_log $1"
          shift 1
          ;;
      esac
    done
    if [ -n "${FAKE_SECURITY_LOG:-}" ]; then
      echo "delete-generic-password$args_log" >> "$FAKE_SECURITY_LOG"
    fi
    missing="${FAKE_SECURITY_MISSING:-}"
    case "|$missing|" in
      *"|$service|"*) exit 44 ;;
    esac
    if [ -n "${FAKE_SECURITY_DELETE_RC:-}" ]; then
      exit "${FAKE_SECURITY_DELETE_RC}"
    fi
    exit 0
    ;;
  *)
    echo "fake security: unsupported command $cmd" >&2
    exit 1
    ;;
esac
"""

_SERVICES = (
    "com.cgfixit.cyclaw.api-key",
    "com.cgfixit.cyclaw.telegram-bot-token",
    "com.cgfixit.cyclaw.grok-api-key",
    "com.cgfixit.cyclaw.anthropic-api-key",
    "com.cgfixit.cyclaw.gh-token",
)

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="requires a POSIX shell (bash) and chmod semantics"
)


def _unused_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


# Default uninstall's port helper may call these. lsof is omitted on purpose.
_PATH_WITHOUT_LSOF_TOOLS = (
    "bash",
    "sh",
    "id",
    "uname",
    "mkdir",
    "chmod",
    "mktemp",
    "mv",
    "rm",
    "cp",
    "kill",
    "sleep",
    "cat",
    "sed",
    "tr",
    "dirname",
    "basename",
    "true",
    "false",
    "env",
    "launchctl",
    "python3",
    "python",
    "rmdir",
    "ls",
)


def _path_without_lsof(extra_bin: Path | None, scratch: Path) -> str:
    shadow = scratch / "no_lsof_bin"
    shadow.mkdir(parents=True)
    for name in _PATH_WITHOUT_LSOF_TOOLS:
        found = shutil.which(name)
        if found is None:
            continue
        dest = shadow / name
        if dest.exists():
            continue
        dest.symlink_to(found)
    if extra_bin is None:
        return str(shadow)
    return f"{extra_bin}{os.pathsep}{shadow}"


@pytest.fixture
def fake_security(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "security"
    stub.write_text(_FAKE_SECURITY, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run(
    *args: str,
    home: Path,
    fake_security_bin: Path | None = None,
    extra_env: dict[str, str] | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["SHELL"] = "/bin/bash"
    # Never touch the host's 8787/8790 in unit tests.
    env.setdefault("CYCLAW_GATE_PORT", str(_unused_port()))
    env.setdefault("CYCLAW_HARNESS_PORT", str(_unused_port()))
    if fake_security_bin is not None:
        env["PATH"] = f"{fake_security_bin}{os.pathsep}{env.get('PATH', '')}"
        env["CYCLAW_UNINSTALL_TEST_MODE"] = "1"
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [_BASH, str(_SCRIPT), *args],
        check=False,
        capture_output=True,
        input=input_text,
        text=True,
        env=env,
        timeout=20,
        cwd=_REPO_ROOT,
    )


def test_unknown_option_exits_one(tmp_path: Path) -> None:
    result = _run("--not-a-flag", home=tmp_path)
    assert result.returncode == 1
    assert "unknown option" in result.stderr


def test_default_uninstall_does_not_call_security(fake_security: Path, tmp_path: Path) -> None:
    argv_log = tmp_path / "security-calls.log"
    result = _run(
        home=tmp_path,
        fake_security_bin=fake_security,
        extra_env={"FAKE_SECURITY_LOG": str(argv_log)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("uninstall complete") == 1
    assert not argv_log.exists()
    assert "delete-generic-password" not in result.stdout


def test_remove_keychain_without_yes_on_nontty_keeps_items(
    fake_security: Path, tmp_path: Path
) -> None:
    argv_log = tmp_path / "security-calls.log"
    result = _run(
        "--remove-keychain",
        home=tmp_path,
        fake_security_bin=fake_security,
        extra_env={"FAKE_SECURITY_LOG": str(argv_log)},
        input_text="",
    )
    assert result.returncode == 0, result.stderr
    assert "kept Keychain items" in result.stdout
    assert not argv_log.exists()


def test_remove_keychain_yes_deletes_five_documented_services(
    fake_security: Path, tmp_path: Path
) -> None:
    argv_log = tmp_path / "security-calls.log"
    account = subprocess.check_output(["/usr/bin/id", "-un"], text=True).strip()
    result = _run(
        "--remove-keychain",
        "--yes",
        home=tmp_path,
        fake_security_bin=fake_security,
        extra_env={"FAKE_SECURITY_LOG": str(argv_log)},
    )
    assert result.returncode == 0, result.stderr
    logged = argv_log.read_text(encoding="utf-8")
    assert "-w" not in logged
    for service in _SERVICES:
        assert f"-s {service}" in logged
        assert f"-a {account}" in logged
        assert f"deleted {service}" in result.stdout
    assert logged.count("delete-generic-password") == 5


def test_remove_keychain_yes_treats_missing_item_as_success(
    fake_security: Path, tmp_path: Path
) -> None:
    result = _run(
        "--remove-keychain",
        "--yes",
        home=tmp_path,
        fake_security_bin=fake_security,
        extra_env={"FAKE_SECURITY_MISSING": "com.cgfixit.cyclaw.api-key"},
    )
    assert result.returncode == 0, result.stderr
    assert "already absent" in result.stdout
    assert "uninstall complete" in result.stdout


def test_remove_keychain_yes_survives_security_failure(
    fake_security: Path, tmp_path: Path
) -> None:
    result = _run(
        "--remove-keychain",
        "--yes",
        home=tmp_path,
        fake_security_bin=fake_security,
        extra_env={"FAKE_SECURITY_DELETE_RC": "1"},
    )
    assert result.returncode == 0, result.stderr
    assert "WARNING: could not delete Keychain" in result.stderr
    assert "uninstall complete" in result.stdout


def test_remove_home_without_keychain_prints_leftover_note(tmp_path: Path) -> None:
    home_dir = tmp_path / ".CyClaw"
    home_dir.mkdir()
    (home_dir / "keep.txt").write_text("x", encoding="utf-8")
    result = _run("--remove-home", "--yes", home=tmp_path)
    assert result.returncode == 0, result.stderr
    assert not home_dir.exists()
    assert "Keychain items were not removed" in result.stdout
    assert "--remove-keychain" in result.stdout


def test_yes_alone_does_not_purge_keychain(fake_security: Path, tmp_path: Path) -> None:
    argv_log = tmp_path / "security-calls.log"
    result = _run(
        "--yes",
        home=tmp_path,
        fake_security_bin=fake_security,
        extra_env={"FAKE_SECURITY_LOG": str(argv_log)},
    )
    assert result.returncode == 0, result.stderr
    assert not argv_log.exists()


def test_remove_keychain_without_test_mode_skips_off_darwin(tmp_path: Path) -> None:
    if sys.platform == "darwin":
        pytest.skip("this pin is the Linux skip path")
    result = _run(
        "--remove-keychain",
        "--yes",
        home=tmp_path,
        extra_env={"CYCLAW_UNINSTALL_TEST_MODE": ""},
    )
    assert result.returncode == 0, result.stderr
    assert "Darwin-only" in result.stdout


def test_missing_lsof_marks_port_unverified() -> None:
    """No lsof means the port was not inspected — treat it as still held."""
    source = _SCRIPT.read_text(encoding="utf-8")
    idx = source.index("lsof not found; cannot free listeners")
    window = source[idx : idx + 280]
    assert "_LOOPBACK_PORT_HELD=1" in window


def test_uninstall_without_lsof_warns_ports_may_still_be_held(tmp_path: Path) -> None:
    gate = _unused_port()
    harness = _unused_port()
    result = _run(
        home=tmp_path,
        extra_env={
            "CYCLAW_GATE_PORT": str(gate),
            "CYCLAW_HARNESS_PORT": str(harness),
            "PATH": _path_without_lsof(None, tmp_path / "nopath"),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "lsof not found" in result.stderr
    assert "may still be held" in result.stderr
    assert "uninstall complete" in result.stdout


def test_garbage_gate_port_does_not_abort_uninstall(tmp_path: Path) -> None:
    result = _run(
        home=tmp_path,
        extra_env={"CYCLAW_GATE_PORT": "not-a-port", "CYCLAW_HARNESS_PORT": str(_unused_port())},
    )
    assert result.returncode == 0, result.stderr
    assert "non-numeric port" in result.stderr
    assert "uninstall complete" in result.stdout


@pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof required to free listeners")
def test_uninstall_stops_loopback_listener_and_survives_if_already_gone(
    tmp_path: Path,
) -> None:
    port = _unused_port()
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import socket, time\n"
            "s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n"
            f"s.bind(('127.0.0.1', {port}))\n"
            "s.listen(1)\n"
            "time.sleep(60)\n",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            check = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                if check.connect_ex(("127.0.0.1", port)) == 0:
                    break
            finally:
                check.close()
            time.sleep(0.05)
        else:
            listener.kill()
            raise AssertionError("listener never bound")

        result = _run(
            home=tmp_path,
            extra_env={
                "CYCLAW_GATE_PORT": str(port),
                "CYCLAW_HARNESS_PORT": str(_unused_port()),
            },
        )
        assert result.returncode == 0, result.stderr
        assert f"stopping listener pid {listener.pid} on :{port}" in result.stdout
        listener.wait(timeout=5)
        assert listener.returncode is not None
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait(timeout=2)
