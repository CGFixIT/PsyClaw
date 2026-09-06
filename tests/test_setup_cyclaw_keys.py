"""Behavior tests for macos/setup-cyclaw-keys.sh against a fake `security` CLI.

Mirrors tests/test_cyclaw_keychain_scripts.py: no real Keychain, no Darwin
required. The script is gated on Apple Silicon unless
CYCLAW_SETUP_KEYS_SKIP_PLATFORM=1 (this file always sets that).
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import socket
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

if os.name != "nt":
    # pty is POSIX-only. The module-level skipif below is a runtime skip and
    # does not stop pytest from importing this file during collection on
    # Windows (same trap as tests/test_cyclaw_keychain_scripts.py).
    import pty
    import select

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "macos" / "setup-cyclaw-keys.sh"
_BASH = shutil.which("bash") or "bash"

_API_KEY_RE = re.compile(r"export CYCLAW_API_KEY='([0-9a-f]{40})'")


def _unused_listen_port() -> int:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


# Tools --skip-prompts --restart-servers may need. lsof is deliberately omitted
# so command -v lsof fails while openssl/id/kill stay resolvable.
_PATH_WITHOUT_LSOF_TOOLS = (
    "bash",
    "sh",
    "openssl",
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
    "touch",
    "true",
    "false",
    "env",
    "head",
    "cut",
    "grep",
    "awk",
    "date",
    "launchctl",
)


def _path_without_lsof(extra_bin: Path, scratch: Path) -> str:
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
    return f"{extra_bin}{os.pathsep}{shadow}"

_FAKE_SECURITY = """#!/usr/bin/env bash
set -euo pipefail
cmd="$1"; shift
case "$cmd" in
  find-generic-password)
    service=""
    read_value=0
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -s) service="$2"; shift 2 ;;
        -w) read_value=1; shift 1 ;;
        *) shift 1 ;;
      esac
    done
    if [ -n "${FAKE_SECURITY_FIND_LOG:-}" ]; then
      if [ "$read_value" -eq 1 ]; then
        printf 'read\t%s\n' "$service" >> "${FAKE_SECURITY_FIND_LOG}"
      else
        printf 'probe\t%s\n' "$service" >> "${FAKE_SECURITY_FIND_LOG}"
      fi
    fi
    if [ "$read_value" -eq 0 ] && [ -n "${FAKE_SECURITY_PROBE_RC:-}" ]; then
      exit "${FAKE_SECURITY_PROBE_RC}"
    fi
    items_raw="${FAKE_SECURITY_ITEMS:-}"
    [ -n "$items_raw" ] || exit 44
    IFS='|' read -ra items <<< "$items_raw"
    for item in "${items[@]}"; do
      key="${item%%=*}"
      val="${item#*=}"
      if [ "$key" = "$service" ]; then
        if [ "$read_value" -eq 1 ] && [ -n "${FAKE_SECURITY_READ_RC:-}" ]; then
          exit "${FAKE_SECURITY_READ_RC}"
        fi
        if [ "$read_value" -eq 1 ]; then
          printf '%s' "$val"
        fi
        exit 0
      fi
    done
    exit 44
    ;;
  add-generic-password)
    args_log=""
    service=""
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -s)
          service="$2"
          args_log="$args_log $1 $2"
          shift 2
          ;;
        -w)
          if [ -n "${FAKE_SECURITY_STDIN_LOG:-}" ]; then
            IFS= read -r secret_from_stdin || true
            # One line per write, keyed by service, so --grok-dummy cannot
            # clobber the generated API key assertion.
            printf '%s\\t%s\\n' "$service" "$secret_from_stdin" >> "${FAKE_SECURITY_STDIN_LOG}"
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
    if [ -n "${FAKE_SECURITY_WRITE_RC:-}" ]; then
      exit "${FAKE_SECURITY_WRITE_RC}"
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


def _base_env(
    fake_security_bin: Path,
    home: Path,
    extra_env: dict[str, str] | None = None,
    stdin_log: Path | None = None,
    argv_log: Path | None = None,
    items: str = "",
) -> dict[str, str]:
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
    for key in (
        "CYCLAW_API_KEY",
        "GROK_API_KEY",
        "ANTHROPIC_API_KEY",
        "TELEGRAM_BOT_TOKEN",
        "GH_TOKEN",
        "GITHUB_TOKEN",
        "CYCLAW_HOME",
        "CYCLAW_REPO",
    ):
        env.pop(key, None)
    if extra_env:
        env.update(extra_env)
    return env


def _run(
    *args: str,
    fake_security_bin: Path,
    home: Path,
    items: str = "",
    extra_env: dict[str, str] | None = None,
    stdin_log: Path | None = None,
    argv_log: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = list(args)
    # Isolate from the real checkout: _SCRIPT_DIR/.. always looks like a
    # CyClaw repo, so without this the script would write a sibling .env.
    if "--repo-path" not in argv and "--no-repo-env" not in argv:
        argv.append("--no-repo-env")
    env = _base_env(
        fake_security_bin,
        home,
        extra_env=extra_env,
        stdin_log=stdin_log,
        argv_log=argv_log,
        items=items,
    )
    return subprocess.run(
        [_BASH, str(_SCRIPT), *argv],
        check=False,
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
        cwd=_REPO_ROOT,
    )


def _stdin_writes(stdin_log: Path) -> dict[str, str]:
    writes: dict[str, str] = {}
    for line in stdin_log.read_text(encoding="utf-8").splitlines():
        service, _, secret = line.partition("\t")
        writes[service] = secret
    return writes


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


def test_skip_prompts_generates_key_into_home_env(fake_security: Path, tmp_path: Path) -> None:
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
    match = _API_KEY_RE.search(text)
    assert match, text
    generated = match.group(1)
    assert generated not in result.stderr
    assert generated not in result.stdout
    logged = argv_log.read_text(encoding="utf-8")
    assert "-s com.cgfixit.cyclaw.api-key" in logged
    assert "-s com.cgfixit.cyclaw.grok-api-key" in logged
    assert "-T /usr/bin/security" in logged
    assert generated not in logged
    writes = _stdin_writes(stdin_log)
    assert writes["com.cgfixit.cyclaw.api-key"] == generated
    assert writes["com.cgfixit.cyclaw.grok-api-key"] == "dummy"


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
    match = _API_KEY_RE.search(text)
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
    assert "# <<< cyclaw keys <<<" in rc
    assert '. "$HOME/.CyClaw/.env"' in rc
    assert "CYCLAW_API_KEY=" not in rc
    env_text = (tmp_path / ".CyClaw" / ".env").read_text(encoding="utf-8")
    match = _API_KEY_RE.search(env_text)
    assert match
    assert match.group(1) not in rc


def test_keep_existing_without_rotate(fake_security: Path, tmp_path: Path) -> None:
    home_env = tmp_path / ".CyClaw"
    home_env.mkdir()
    existing = "a" * 40
    (home_env / ".env").write_text(f"export CYCLAW_API_KEY='{existing}'\n", encoding="utf-8")
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


def test_reads_existing_keychain_item_once_after_presence_probe(fake_security: Path, tmp_path: Path) -> None:
    existing = "c" * 40
    find_log = tmp_path / "security-find.log"
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
        items=f"com.cgfixit.cyclaw.api-key={existing}",
        extra_env={"FAKE_SECURITY_FIND_LOG": str(find_log)},
    )
    assert result.returncode == 0, result.stderr
    assert "already present (Keychain)" in result.stdout
    assert f"export CYCLAW_API_KEY='{existing}'" in (tmp_path / ".CyClaw" / ".env").read_text(encoding="utf-8")
    assert find_log.read_text(encoding="utf-8").splitlines() == [
        "probe\tcom.cgfixit.cyclaw.api-key",
        "read\tcom.cgfixit.cyclaw.api-key",
    ]


@pytest.mark.parametrize(
    ("extra_env", "message"),
    [
        ({"FAKE_SECURITY_READ_RC": "36"}, "exists but could not be read (security exit 36)"),
        ({"FAKE_SECURITY_PROBE_RC": "1"}, "could not query the Keychain for CYCLAW_API_KEY (security exit 1)"),
    ],
)
def test_keychain_lookup_failure_never_generates_or_writes_dotenv(
    fake_security: Path,
    tmp_path: Path,
    extra_env: dict[str, str],
    message: str,
) -> None:
    existing = "d" * 40
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
        items=f"com.cgfixit.cyclaw.api-key={existing}",
        extra_env=extra_env,
    )
    assert result.returncode == 1
    assert message in result.stderr
    assert "generated CYCLAW_API_KEY" not in result.stdout
    assert not (tmp_path / ".CyClaw" / ".env").exists()


def test_empty_keychain_item_never_falls_through_to_generation(fake_security: Path, tmp_path: Path) -> None:
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
        items="com.cgfixit.cyclaw.api-key=",
    )
    assert result.returncode == 1
    assert "exists but its value is empty" in result.stderr
    assert "generated CYCLAW_API_KEY" not in result.stdout
    assert not (tmp_path / ".CyClaw" / ".env").exists()


def test_keychain_write_failure_aborts_before_dotenv_write(fake_security: Path, tmp_path: Path) -> None:
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
        extra_env={"FAKE_SECURITY_WRITE_RC": "1"},
    )
    assert result.returncode == 1
    assert "Keychain store failed for CYCLAW_API_KEY" in result.stderr
    assert not (tmp_path / ".CyClaw" / ".env").exists()


def test_generated_api_key_warns_that_gate_restart_is_required(fake_security: Path, tmp_path: Path) -> None:
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert "restart gate.py" in result.stdout
    assert "nothing in CyClaw reads .env at runtime" in result.stdout
    assert "--restart-servers" in result.stdout
    assert "NOT applied live" in result.stdout


def test_restart_servers_flag_is_port_scoped_not_pkill() -> None:
    source = _SCRIPT.read_text(encoding="utf-8")
    assert "--restart-servers" in source
    assert "free_loopback_port" in source
    assert "pkill" not in source
    assert "still has a TCP LISTEN after signaling" in source
    assert "sleep 0.2" in source


def test_missing_lsof_marks_port_unverified() -> None:
    """No lsof means the port was not inspected — do not claim it is free."""
    source = _SCRIPT.read_text(encoding="utf-8")
    idx = source.index("lsof not found; cannot free listeners")
    window = source[idx : idx + 280]
    assert "_LOOPBACK_PORT_HELD=1" in window


def test_restart_servers_without_lsof_does_not_claim_ports_freed(
    fake_security: Path, tmp_path: Path
) -> None:
    gate = _unused_listen_port()
    harness = _unused_listen_port()
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        "--restart-servers",
        "--gate-port",
        str(gate),
        "--harness-port",
        str(harness),
        fake_security_bin=fake_security,
        home=tmp_path,
        extra_env={"PATH": _path_without_lsof(fake_security, tmp_path / "nopath")},
    )
    assert result.returncode == 0, result.stderr
    assert "lsof not found" in result.stderr
    assert "ports freed" not in result.stdout
    assert "may still be held" in result.stderr


def test_restart_servers_runs_after_generate(fake_security: Path, tmp_path: Path) -> None:
    gate = _unused_listen_port()
    harness = _unused_listen_port()
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        "--restart-servers",
        "--gate-port",
        str(gate),
        "--harness-port",
        str(harness),
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert f"freeing loopback listeners on :{gate} / :{harness}" in result.stdout
    assert "ports freed. Start cyclaw in a new shell" in result.stdout
    assert "NOT applied live" in result.stdout


@pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof required to free listeners")
def test_restart_servers_stops_a_loopback_listener(fake_security: Path, tmp_path: Path) -> None:
    port = _unused_listen_port()
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
            "--skip-prompts",
            "--no-print-key",
            "--restart-servers",
            "--gate-port",
            str(port),
            "--harness-port",
            str(_unused_listen_port()),
            fake_security_bin=fake_security,
            home=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert f"stopping listener pid {listener.pid} on :{port}" in result.stdout
        assert "ports freed. Start cyclaw in a new shell" in result.stdout
        listener.wait(timeout=5)
        assert listener.returncode is not None
    finally:
        if listener.poll() is None:
            listener.kill()
            listener.wait(timeout=2)


@pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof required to free listeners")
def test_restart_servers_warns_if_listener_ignores_term(
    fake_security: Path, tmp_path: Path
) -> None:
    """kill(1) success is not a closed listen socket — report that honestly."""
    port = _unused_listen_port()
    listener = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import signal, socket, time\n"
            "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
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
            "--skip-prompts",
            "--no-print-key",
            "--restart-servers",
            "--gate-port",
            str(port),
            "--harness-port",
            str(_unused_listen_port()),
            fake_security_bin=fake_security,
            home=tmp_path,
        )
        assert result.returncode == 0, result.stderr
        assert f"stopping listener pid {listener.pid} on :{port}" in result.stdout
        assert "ports freed." not in result.stdout
        assert "still has a TCP LISTEN after signaling" in result.stderr
        assert "may still be held" in result.stderr
    finally:
        if listener.poll() is None:
            listener.send_signal(signal.SIGKILL)
            listener.wait(timeout=2)


def test_rotate_replaces_existing(fake_security: Path, tmp_path: Path) -> None:
    home_env = tmp_path / ".CyClaw"
    home_env.mkdir()
    existing = "b" * 40
    (home_env / ".env").write_text(f"export CYCLAW_API_KEY='{existing}'\n", encoding="utf-8")
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
    assert _API_KEY_RE.search(text)


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


def test_refuses_non_tty_without_skip_prompts(fake_security: Path, tmp_path: Path) -> None:
    result = _run(fake_security_bin=fake_security, home=tmp_path)
    assert result.returncode == 1
    assert "TTY" in result.stderr


def test_schedule_rotate_plist_has_no_secret(fake_security: Path, tmp_path: Path) -> None:
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        "--no-copy-key",
        "--schedule-rotate",
        "monthly",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    plist = tmp_path / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.keys-rotate.plist"
    assert plist.is_file()
    text = plist.read_text(encoding="utf-8")
    assert "--rotate" in text
    assert "--skip-prompts" in text
    assert "--no-print-key" in text
    assert "CYCLAW_API_KEY=" not in text
    assert "dummy" not in text
    assert not re.search(r"[0-9a-f]{40}", text)
    helper = tmp_path / ".CyClaw" / "bin" / "setup-cyclaw-keys.sh"
    assert helper.is_file()
    assert helper.stat().st_mode & stat.S_IXUSR
    assert "re-run --schedule-rotate" in result.stdout


def test_schedule_rotate_rejects_unknown_interval(fake_security: Path, tmp_path: Path) -> None:
    result = _run(
        "--schedule-rotate",
        "daily",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 1
    assert "monthly" in result.stderr


def test_unschedule_rotate_removes_plist(fake_security: Path, tmp_path: Path) -> None:
    first = _run(
        "--skip-prompts",
        "--no-print-key",
        "--no-copy-key",
        "--schedule-rotate",
        "weekly",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert first.returncode == 0, first.stderr
    plist = tmp_path / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.keys-rotate.plist"
    assert plist.is_file()
    second = _run(
        "--skip-prompts",
        "--no-print-key",
        "--no-copy-key",
        "--unschedule-rotate",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert second.returncode == 0, second.stderr
    assert not plist.exists()


def test_help_lists_browser_and_schedule_flags(fake_security: Path, tmp_path: Path) -> None:
    result = _run("--help", fake_security_bin=fake_security, home=tmp_path)
    assert result.returncode == 0
    assert "--fill-browser" in result.stdout
    assert "--schedule-rotate" in result.stdout
    assert "--copy-key" in result.stdout
    assert "--restart-servers" in result.stdout


def test_custom_cyclaw_home_rc_sources_that_env(fake_security: Path, tmp_path: Path) -> None:
    custom = tmp_path / "alt home"
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
        extra_env={"CYCLAW_HOME": str(custom)},
    )
    assert result.returncode == 0, result.stderr
    env_file = custom / ".env"
    assert env_file.is_file()
    assert not (tmp_path / ".CyClaw" / ".env").exists()
    rc = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert str(env_file) in rc
    assert '. "$HOME/.CyClaw/.env"' not in rc
    match = _API_KEY_RE.search(env_file.read_text(encoding="utf-8"))
    assert match
    assert match.group(1) not in rc


def test_keep_round_trips_apostrophe_in_existing_key(fake_security: Path, tmp_path: Path) -> None:
    home_env = tmp_path / ".CyClaw"
    home_env.mkdir()
    # Managed encoding of abc'def-not-a-real-key-xx (apostrophe in the value).
    encoded = "export CYCLAW_API_KEY='abc'\\''def-not-a-real-key-xx'\n"
    (home_env / ".env").write_text(encoded, encoding="utf-8")
    (home_env / ".env").chmod(0o600)
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    text = (home_env / ".env").read_text(encoding="utf-8")
    assert "export CYCLAW_API_KEY='abc'\\''def-not-a-real-key-xx'" in text
    # A broken decoder would re-quote the escape sequence and double it.
    assert "\\\\''" not in text
    assert "keeping" in result.stdout


def test_malformed_keys_block_is_left_unchanged(fake_security: Path, tmp_path: Path) -> None:
    rc = tmp_path / ".zshrc"
    rc.write_text("# >>> cyclaw keys >>>\n# half-written, no end marker\n", encoding="utf-8")
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    assert "malformed" in result.stderr
    assert rc.read_text(encoding="utf-8") == ("# >>> cyclaw keys >>>\n# half-written, no end marker\n")


def test_repo_path_writes_checkout_env(fake_security: Path, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "gate.py").write_text("# stub\n", encoding="utf-8")
    result = _run(
        "--skip-prompts",
        "--no-print-key",
        "--repo-path",
        str(checkout),
        fake_security_bin=fake_security,
        home=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    repo_env = checkout / ".env"
    assert repo_env.is_file()
    assert repo_env.stat().st_mode & 0o777 == 0o600
    assert "export CYCLAW_API_KEY=" in repo_env.read_text(encoding="utf-8")


def _run_prompt_and_signal(
    fake_security_bin: Path,
    home: Path,
    sig: signal.Signals,
) -> int:
    env = _base_env(fake_security_bin, home)
    master_fd, slave_fd = pty.openpty()
    try:
        proc = subprocess.Popen(
            [_BASH, str(_SCRIPT), "--no-print-key", "--no-repo-env"],
            stdin=slave_fd,
            stdout=slave_fd,
            stderr=slave_fd,
            env=env,
            cwd=_REPO_ROOT,
            close_fds=True,
        )
        os.close(slave_fd)
        slave_fd = -1
        buf = b""
        deadline = time.monotonic() + 10
        saw_prompt = False
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master_fd], [], [], 0.2)
            if ready:
                try:
                    chunk = os.read(master_fd, 1024)
                except OSError:
                    break
                if not chunk:
                    break
                buf += chunk
                if b"paste the secret" in buf or b"already set" in buf:
                    saw_prompt = True
                    break
            if proc.poll() is not None:
                break
        if not saw_prompt:
            proc.kill()
            raise AssertionError(f"never reached a secret prompt: {buf!r}")
        proc.send_signal(sig)
        try:
            return proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
            raise AssertionError(f"process did not exit after {sig!r}: {buf!r}") from None
    finally:
        if slave_fd >= 0:
            os.close(slave_fd)
        os.close(master_fd)


def _exited_by(rc: int, sig: signal.Signals) -> None:
    """Bash trap uses 128+N; Popen.wait() uses -N if the kernel won the race."""
    n = int(sig)
    assert rc in (128 + n, -n), rc


def test_sigint_during_prompt_exits_130(fake_security: Path, tmp_path: Path) -> None:
    rc = _run_prompt_and_signal(fake_security, tmp_path, signal.SIGINT)
    _exited_by(rc, signal.SIGINT)
    # CYCLAW_API_KEY is written before the first prompt; later tokens must not be.
    env_text = (tmp_path / ".CyClaw" / ".env").read_text(encoding="utf-8")
    assert "export CYCLAW_API_KEY=" in env_text
    assert "TELEGRAM_BOT_TOKEN" not in env_text
    assert "ANTHROPIC_API_KEY" not in env_text
    assert "GROK_API_KEY" not in env_text
    assert "GH_TOKEN" not in env_text


def test_sigterm_during_prompt_exits_143(fake_security: Path, tmp_path: Path) -> None:
    rc = _run_prompt_and_signal(fake_security, tmp_path, signal.SIGTERM)
    _exited_by(rc, signal.SIGTERM)
    env_text = (tmp_path / ".CyClaw" / ".env").read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN" not in env_text
    assert "GROK_API_KEY" not in env_text



def _function_body(source: str, name: str) -> str:
    """Text of a top-level `name() { ... }` shell function."""
    start = source.index(f"\n{name}() {{\n")
    end = source.index("\n}\n", start)
    return source[start:end]


def test_secret_staging_functions_clean_up_on_interrupt() -> None:
    """Every function that stages a cleartext secret registers an EXIT trap.

    _fill_browser already carried this idiom; _copy_key and
    _keychain_store_value staged secrets the same way without it.
    """
    source = _SCRIPT.read_text(encoding="utf-8")
    for name in ("_copy_key", "_keychain_store_value", "_fill_browser"):
        body = _function_body(source, name)
        assert "mktemp" in body, f"{name} no longer stages a temp file; revisit this contract"
        assert "trap " in body and "EXIT" in body, (
            f"{name} stages a cleartext secret in a temp file but registers no EXIT trap, "
            "so an interrupted run leaves it on disk"
        )


def test_copy_key_restores_the_umask_it_sets() -> None:
    """_copy_key saves and restores umask like its two sibling functions.

    It previously set `umask 077` (after mktemp, where it could not affect the
    file mktemp had already created) and never restored it, so the value
    leaked into every file the script created afterwards.
    """
    body = _function_body(_SCRIPT.read_text(encoding="utf-8"), "_copy_key")
    assert 'old_umask="$(umask)"' in body, "_copy_key must save the prior umask"
    assert 'umask "$old_umask"' in body, "_copy_key must restore the prior umask"
    # Compare executable lines only -- the surrounding comments mention both
    # "umask" and "mktemp", so a raw substring index would match prose.
    code = "\n".join(
        line for line in body.splitlines() if not line.lstrip().startswith("#")
    )
    # The umask must be set before mktemp, or it governs nothing: mktemp has
    # already created the file by the time a later `umask 077` runs.
    assert code.index("umask 077") < code.index("mktemp"), \
        "umask must be set before mktemp creates the file"
