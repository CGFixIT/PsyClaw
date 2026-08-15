"""Behavior tests for macos/cyclaw-keychain-{env,set}.sh against a fake
`security` CLI.

These exercise the real shell scripts end-to-end (not just the Python-side
argv construction in utils.launchd_plist.wrap_with_keychain_secrets) --
usage errors, the fail-closed missing/empty-item paths, the env-var-name
allowlist, single-secret injection, and the documented two-layer chaining
composition, all without touching a real macOS Keychain.
"""

from __future__ import annotations

import os
import pty
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ENV_SCRIPT = _REPO_ROOT / "macos" / "cyclaw-keychain-env.sh"
_SET_SCRIPT = _REPO_ROOT / "macos" / "cyclaw-keychain-set.sh"
_BASH = shutil.which("bash") or "bash"

_FAKE_SECURITY = """#!/usr/bin/env bash
# Stand-in for macOS's `security` CLI, controlled by env vars:
#   FAKE_SECURITY_ITEMS:     "service=secret|service2=secret2" (present items)
#   FAKE_SECURITY_LOG:       if set, add-generic-password invocations (argv
#                            only, with -w's value never appended -- see
#                            below) are appended here
#   FAKE_SECURITY_STDIN_LOG: if set, whatever add-generic-password reads from
#                            its own stdin after a bare `-w` token is written
#                            here -- simulating the real `security` CLI's
#                            interactive TTY prompt (readpassphrase(3)) for a
#                            `-w` with no attached value, which is exactly how
#                            cyclaw-keychain-set.sh invokes it.
set -euo pipefail
cmd="$1"; shift
case "$cmd" in
  find-generic-password)
    service=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -s) service="$2"; shift 2 ;;
        -w) shift 1 ;;
        *) shift 1 ;;
      esac
    done
    IFS='|' read -ra items <<< "${FAKE_SECURITY_ITEMS:-}"
    for item in "${items[@]}"; do
      key="${item%%=*}"
      val="${item#*=}"
      if [ "$key" = "$service" ]; then
        printf '%s' "$val"
        exit 0
      fi
    done
    exit 44
    ;;
  add-generic-password)
    args_log=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -w)
          # Bare -w, no attached value: read the "password" the same way the
          # real security CLI's interactive prompt would, from this process's
          # own stdin -- never from argv.
          if [ -n "${FAKE_SECURITY_STDIN_LOG:-}" ]; then
            IFS= read -r secret_from_stdin
            printf '%s' "$secret_from_stdin" > "${FAKE_SECURITY_STDIN_LOG}"
          fi
          shift 1
          ;;
        *)
          args_log="$args_log $1"
          shift 1
          ;;
      esac
    done
    if [ -n "${FAKE_SECURITY_LOG:-}" ]; then
      echo "add-generic-password$args_log" >> "$FAKE_SECURITY_LOG"
    fi
    exit 0
    ;;
  *)
    echo "fake security: unsupported command $cmd" >&2
    exit 1
    ;;
esac
"""

pytestmark = pytest.mark.skipif(os.name == "nt", reason="requires a POSIX shell (bash) and chmod semantics")


@pytest.fixture
def fake_security(tmp_path: Path) -> Path:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    stub = bin_dir / "security"
    stub.write_text(_FAKE_SECURITY, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return bin_dir


def _run(
    script: Path,
    *args: str,
    fake_security_bin: Path,
    items: str = "",
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_security_bin}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_SECURITY_ITEMS"] = items
    return subprocess.run(
        [_BASH, str(script), *args],
        check=False,
        capture_output=True,
        input=input_text,
        text=True,
        env=env,
        timeout=30,
        cwd=_REPO_ROOT,
    )


def test_env_script_is_executable_with_shebang() -> None:
    mode = _ENV_SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR
    assert _ENV_SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


def test_env_script_usage_error_on_missing_separator(fake_security: Path) -> None:
    result = _run(_ENV_SCRIPT, "svc", "VAR", "not-dash-dash", "echo", fake_security_bin=fake_security)
    assert result.returncode == 1
    assert "usage:" in result.stderr


def test_env_script_usage_error_on_too_few_args(fake_security: Path) -> None:
    result = _run(_ENV_SCRIPT, "svc", "VAR", fake_security_bin=fake_security)
    assert result.returncode == 1
    assert "usage:" in result.stderr


def test_env_script_fails_closed_on_missing_item(fake_security: Path) -> None:
    result = _run(_ENV_SCRIPT, "missing-svc", "MY_VAR", "--", "echo", "should-not-run", fake_security_bin=fake_security, items="")
    assert result.returncode == 1
    assert "no Keychain item" in result.stderr
    assert "should-not-run" not in result.stdout


def test_env_script_fails_closed_on_empty_item(fake_security: Path) -> None:
    result = _run(
        _ENV_SCRIPT, "svc", "MY_VAR", "--", "echo", "should-not-run",
        fake_security_bin=fake_security, items="svc=",
    )
    assert result.returncode == 1
    assert "is empty" in result.stderr
    assert "should-not-run" not in result.stdout


def test_env_script_rejects_invalid_var_name_before_touching_keychain(fake_security: Path) -> None:
    # No Keychain item configured at all -- if the script queried `security`
    # before validating VAR_NAME, this would fail with "no Keychain item"
    # instead of the var-name-specific message, so this also pins the order.
    result = _run(_ENV_SCRIPT, "svc", "1-not-a-valid-name", "--", "echo", "x", fake_security_bin=fake_security, items="")
    assert result.returncode == 1
    assert "invalid environment variable name" in result.stderr


def test_env_script_injects_single_secret_without_leaking_it_to_stderr(fake_security: Path) -> None:
    result = _run(
        _ENV_SCRIPT, "svc", "MY_VAR", "--", sys.executable, "-c", "import os; print(os.environ['MY_VAR'])",
        fake_security_bin=fake_security, items="svc=hunter2",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "hunter2"
    assert "hunter2" not in result.stderr


def test_env_script_chains_two_secrets_via_nested_exec(fake_security: Path) -> None:
    print_both = "import os; print(os.environ['VAR_A'] + ':' + os.environ['VAR_B'])"
    result = _run(
        _ENV_SCRIPT,
        "svc-a", "VAR_A", "--",
        str(_ENV_SCRIPT), "svc-b", "VAR_B", "--",
        sys.executable, "-c", print_both,
        fake_security_bin=fake_security,
        items="svc-a=vala|svc-b=valb",
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "vala:valb"


def test_env_script_second_layer_still_fails_closed_on_missing_item(fake_security: Path) -> None:
    # First layer's secret exists; second layer's does not -- the whole
    # chain must still fail closed rather than running with VAR_B unset.
    result = _run(
        _ENV_SCRIPT,
        "svc-a", "VAR_A", "--",
        str(_ENV_SCRIPT), "svc-b", "VAR_B", "--",
        "echo", "should-not-run",
        fake_security_bin=fake_security,
        items="svc-a=vala",
    )
    assert result.returncode == 1
    assert "no Keychain item" in result.stderr
    assert "should-not-run" not in result.stdout


def test_set_script_usage_error_without_service_arg(fake_security: Path) -> None:
    # Wrong arg count is checked before the TTY check, so this still exercises
    # cleanly over a plain (non-TTY) pipe.
    result = _run(_SET_SCRIPT, fake_security_bin=fake_security, input_text="secret\n")
    assert result.returncode == 1
    assert "usage:" in result.stderr


def test_set_script_requires_tty_and_refuses_non_interactive_stdin(fake_security: Path) -> None:
    # cyclaw-keychain-set.sh no longer reads the secret into a shell variable
    # at all (see its header) -- it hands a bare `security ... -w` to the real
    # `security` CLI, which prompts via the controlling TTY. That means it can
    # no longer validate "was the secret empty" itself (it never sees the
    # value), so it instead refuses outright when stdin isn't a TTY, rather
    # than silently proceeding into a `security` call that would hang or fail
    # in a way the operator can't diagnose. This subprocess's stdin is a
    # regular pipe (matching how a CI/non-interactive caller would invoke it),
    # never a pty, so the guard must fire.
    result = _run(_SET_SCRIPT, "svc", fake_security_bin=fake_security, input_text="hunter2\n")
    assert result.returncode == 1
    assert "requires an interactive terminal" in result.stderr


def _run_set_script_via_pty(
    *, fake_security_bin: Path, service: str, typed_secret: str, log_path: Path, stdin_log_path: Path
) -> subprocess.CompletedProcess[str]:
    """Run cyclaw-keychain-set.sh with a real pty on stdin, matching how an
    operator actually invokes it at a terminal -- `[ -t 0 ]` must see a TTY,
    and the fake `security` stub reads the "typed" secret from that same TTY
    the way the real CLI's bare `-w` prompt would, never from our script's
    own variables or argv.
    """
    env = os.environ.copy()
    env["PATH"] = f"{fake_security_bin}{os.pathsep}{env.get('PATH', '')}"
    env["FAKE_SECURITY_LOG"] = str(log_path)
    env["FAKE_SECURITY_STDIN_LOG"] = str(stdin_log_path)

    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(  # noqa: S603 - fixed argv, test-only fake security stub
            [_BASH, str(_SET_SCRIPT), service],
            stdin=slave_fd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            cwd=_REPO_ROOT,
            text=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        os.write(master_fd, f"{typed_secret}\n".encode())
        stdout, stderr = proc.communicate(timeout=30)
        return subprocess.CompletedProcess(proc.args, proc.returncode, stdout, stderr)
    finally:
        os.close(master_fd)
        if slave_fd != -1:
            os.close(slave_fd)


def test_set_script_stores_via_fake_security_and_never_echoes_secret_to_argv(fake_security: Path, tmp_path: Path) -> None:
    log_path = tmp_path / "security-calls.log"
    stdin_log_path = tmp_path / "security-stdin.log"

    result = _run_set_script_via_pty(
        fake_security_bin=fake_security,
        service="svc",
        typed_secret="hunter2",  # noqa: S106 - fake fixture value, not a real credential
        log_path=log_path,
        stdin_log_path=stdin_log_path,
    )

    assert result.returncode == 0
    assert "stored Keychain item: service=svc" in result.stdout

    logged = log_path.read_text(encoding="utf-8")
    assert "-s svc" in logged
    assert "-T /usr/bin/security" in logged
    assert "-U" in logged
    assert "hunter2" not in logged  # the whole point: never in the security process's argv

    # The fake stub *did* receive the real secret -- just via the TTY read
    # path (its own stdin), proving the mechanism actually delivers the
    # value end-to-end rather than silently losing it.
    assert stdin_log_path.read_text(encoding="utf-8") == "hunter2"
