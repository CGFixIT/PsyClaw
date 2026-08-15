"""Behavior tests for macos/setup-cyclaw-keys.sh against a fake `security` CLI.

Mirrors tests/test_cyclaw_keychain_scripts.py: no real Keychain, no Darwin
required. The script is gated on Apple Silicon unless
CYCLAW_SETUP_KEYS_SKIP_PLATFORM=1 (this file always sets that).
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "macos" / "setup-cyclaw-keys.sh"
_BASH = shutil.which("bash") or "bash"

_FAKE_SECURITY = """#!/usr/bin/env bash
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
          if [ -n "${FAKE_SECURITY_STDIN_LOG:-}" ]; then
            IFS= read -r secret_from_stdin || true
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

pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="requires a POSIX shell (bash) and chmod semantics"
)


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
    fake_security_bin: Path,
    home: Path,
    items: str = "",
    extra_env: dict[str, str] | None = None,
    stdin_log: Path | None = None,
    argv_log: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = f"{fake_security_bin}{os.pathsep}{env.get('PATH', '')}"
    env["HOME"] = str(home)
    env["SHELL"] = "/bin/zsh"
    env["CYCLAW_SETUP_KEYS_SKIP_PLATFORM"] = "1"
    env["CYCLAW_SETUP_KEYS_STDIN_STORE"] = "1"
    env["FAKE_SECURITY_ITEMS"] = items
    if stdin_log is not None:
        env["FAKE_SECURITY_STDIN_LOG"] = str(stdin_log)
    if argv_log is not None:
        env["FAKE_SECURITY_LOG"] = str(argv_log)
    if extra_env:
        env.update(extra_env)
    env.pop("CYCLAW_API_KEY", None)
    env.pop("GROK_API_KEY", None)
    env.pop("ANTHROPIC_API_KEY", None)
    env.pop("TELEGRAM_BOT_TOKEN", None)
    env.pop("GH_TOKEN", None)
    env.pop("GITHUB_TOKEN", None)
    return subprocess.run(
        [_BASH, str(_SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        cwd=_REPO_ROOT,
    )


def test_script_is_executable_with_shebang() -> None:
    mode = _SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR
    assert _SCRIPT.read_text(encoding="utf-8").startswith("#!/usr/bin/env bash")


def test_help_exits_zero(fake_security: Path, tmp_path: Path) -> None:
    result = _run("--help", fake_security_bin=fake_security, home=tmp_path)
    assert result.returncode == 0
    assert "CYCLAW_API_KEY" in result.stdout


def test_unknown_option_exits_one(fake_security: Path, tmp_path: Path) -> None:
    result = _run("--not-a-flag", fake_security_bin=fake_security, home=tmp_path)
    assert result.returncode == 1
    assert "unknown option" in result.stderr


def test_skip_prompts_generates_key_into_home_env(
    fake_security: Path, tmp_path: Path
) -> None:
    argv_log = tmp_path / "security-calls.log"
    stdin_log = tmp_path / "security-stdin.log"
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        "--grok-dummy",
        fake_security_bin=fake_security,
        home=tmp_path,
        argv_log=argv_log,
        stdin_log=stdin_log,
    )
    assert result.returncode == 0, result.stderr
    env_file = tmp_path / ".CyClaw" / ".env"
    assert env_file.is_file()
    assert env_file.stat().st_mode & 0o777 == 0o600
    text = env_file.read_text(encoding="utf-8")
    assert "export CYCLAW_API_KEY=" in text
    assert "export GROK_API_KEY='dummy'" in text
    assert "TELEGRAM_BOT_TOKEN" not in text
    assert "ANTHROPIC_API_KEY" not in text
    # 40 hex chars inside quotes
    import re

    match = re.search(r"export CYCLAW_API_KEY='([0-9a-f]{40})'", text)
    assert match, text
    generated = match.group(1)
    assert generated not in result.stderr
    assert generated not in result.stdout
    logged = argv_log.read_text(encoding="utf-8")
    assert "-s com.cgfixit.cyclaw.api-key" in logged
    assert "-T /usr/bin/security" in logged
    assert generated not in logged
    assert stdin_log.read_text(encoding="utf-8") == generated


def test_print_key_emits_once_on_stdout(fake_security: Path, tmp_path: Path) -> None:
    result = _run(
        "--skip-prompts",
        "--print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    env_file = tmp_path / ".CyClaw" / ".env"
    text = env_file.read_text(encoding="utf-8")
    import re

    match = re.search(r"export CYCLAW_API_KEY='([0-9a-f]{40})'", text)
    assert match
    assert result.stdout.strip().endswith(match.group(1))
    assert match.group(1) not in result.stderr


def test_rc_source_block_contains_no_secret(fake_security: Path, tmp_path: Path) -> None:
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    rc = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert "# >>> cyclaw keys >>>" in rc
    assert '. "$HOME/.CyClaw/.env"' in rc
    assert "CYCLAW_API_KEY=" not in rc
    env_text = (tmp_path / ".CyClaw" / ".env").read_text(encoding="utf-8")
    import re

    match = re.search(r"export CYCLAW_API_KEY='([0-9a-f]{40})'", env_text)
    assert match
    assert match.group(1) not in rc


def test_keep_existing_without_rotate(fake_security: Path, tmp_path: Path) -> None:
    home_env = tmp_path / ".CyClaw"
    home_env.mkdir()
    existing = "a" * 40
    (home_env / ".env").write_text(
        f"export CYCLAW_API_KEY='{existing}'\n", encoding="utf-8"
    )
    (home_env / ".env").chmod(0o600)
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    text = (home_env / ".env").read_text(encoding="utf-8")
    assert f"export CYCLAW_API_KEY='{existing}'" in text
    assert "keeping" in result.stdout


def test_rotate_replaces_existing(fake_security: Path, tmp_path: Path) -> None:
    home_env = tmp_path / ".CyClaw"
    home_env.mkdir()
    existing = "b" * 40
    (home_env / ".env").write_text(
        f"export CYCLAW_API_KEY='{existing}'\n", encoding="utf-8"
    )
    result = _run(
        "--skip-prompts",
        "--rotate",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    text = (home_env / ".env").read_text(encoding="utf-8")
    assert existing not in text
    import re

    assert re.search(r"export CYCLAW_API_KEY='([0-9a-f]{40})'", text)


def test_upsert_preserves_unrelated_keys(fake_security: Path, tmp_path: Path) -> None:
    home_env = tmp_path / ".CyClaw"
    home_env.mkdir()
    (home_env / ".env").write_text(
        "export OTHER_THING='keep-me'\nexport CYCLAW_API_KEY='oldoldoldoldoldoldoldoldoldoldoldoldold1'\n",
        encoding="utf-8",
    )
    result = _run(
        "--skip-prompts",
        "--rotate",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    text = (home_env / ".env").read_text(encoding="utf-8")
    assert "export OTHER_THING='keep-me'" in text


def test_refuses_non_tty_without_skip_prompts(
    fake_security: Path, tmp_path: Path
) -> None:
    result = _run(fake_security_bin=fake_security, home=tmp_path)
    assert result.returncode == 1
    assert "TTY" in result.stderr
