"""Static pins for the Darwin twin of windows-smoke.ps1.

macos-smoke.sh is the operator-facing + macos-latest CI equivalent of
.claude/skills/CyClaw-Sandbox/windows-smoke.ps1. It is not executed here
(needs live gate.py + harness.server); this module pins the contract so a
later edit cannot silently drop an endpoint, require jq/Homebrew, echo a
secret, or leave Darwin bash 3.2.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MACOS = _REPO_ROOT / ".claude" / "skills" / "CyClaw-Sandbox" / "macos-smoke.sh"
_WINDOWS = _REPO_ROOT / ".claude" / "skills" / "CyClaw-Sandbox" / "windows-smoke.ps1"

# Endpoint path fragments both scripts must exercise. Keep in lock-step with
# windows-smoke.ps1's numbered checks; a missing string here means Darwin
# lost parity, not that the Windows script grew a new check unnoticed.
_SHARED_PATHS = (
    "/health",
    "/query",
    "/soul",
    "/static/terminal.html",
    "/api/status",
    "/api/registry",
    "/api/sessions",
    "/rename",
    "/api/soul",
    "/api/model",
    "/api/chat",
    "/api/github/status",
    "/api/harness/runs",
    "/api/agent/checks",
    "/api/agent/run",
    "/decision",
    "/ops/fsconnect",
)


def _macos_text() -> str:
    return _MACOS.read_text(encoding="utf-8")


def _windows_text() -> str:
    return _WINDOWS.read_text(encoding="utf-8")


def test_macos_smoke_exists_and_is_bash() -> None:
    text = _macos_text()
    assert _MACOS.is_file()
    assert text.startswith("#!/usr/bin/env bash")
    assert "windows-smoke.ps1" in text


def test_macos_smoke_bash_syntax() -> None:
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    bash = str(git_bash) if git_bash.is_file() else shutil.which("bash")
    assert bash, "bash is required to syntax-check macos-smoke.sh"
    result = subprocess.run(
        [bash, "-n", str(_MACOS)],
        capture_output=True,
        text=True,
        check=False,
    )
    combined = f"{result.stdout}{result.stderr}".lower()
    if result.returncode and (
        "access is denied" in combined
        or "wslstore" in combined
        or "windows subsystem for linux" in combined
    ):
        pytest.skip("discovered Bash executable cannot run on this host")
    assert result.returncode == 0, result.stderr


def _code_lines(text: str) -> str:
    return "\n".join(
        line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
    )


def test_macos_smoke_stays_on_bash32_and_bsd_userland() -> None:
    text = _macos_text()
    for unsupported in ("declare -A", "mapfile", "readarray", "local -n"):
        assert unsupported not in text
    code = _code_lines(text)
    assert "grep -P" not in code
    assert re.search(r"\bjq\b", code) is None
    assert "brew " not in code


def test_macos_smoke_does_not_start_servers() -> None:
    """Twin of windows-smoke.ps1: against already-running servers, not a launcher."""
    text = _macos_text()
    code = _code_lines(text)
    assert "uvicorn" not in code
    assert "harness.server" not in code
    assert "already-running" in text


def test_macos_smoke_loopback_only_and_does_not_print_secrets() -> None:
    text = _macos_text()
    assert "127.0.0.1" in text
    assert "0.0.0.0" not in text  # noqa: S104 — pin: script must NOT bind all interfaces
    assert 'echo "$API_KEY"' not in text
    assert 'echo "$CSRF"' not in text
    assert "echo $API_KEY" not in text
    assert "Bearer ${API_KEY}" in text
    assert "CYCLAW_API_KEY" in text


def test_macos_smoke_covers_windows_smoke_endpoints() -> None:
    mac = _macos_text()
    win = _windows_text()
    for path in _SHARED_PATHS:
        assert path in win, f"windows-smoke.ps1 lost {path} — update _SHARED_PATHS"
        assert path in mac, f"macos-smoke.sh missing Windows twin path {path}"
    assert "X-CyClaw-CSRF" in mac
    assert "csrf-token" in mac
    assert "/ops/sync" in mac
    assert "/ops/agentic" in mac
    assert "/ops/sqlconnect" in mac
    assert "auth-gate-only" in mac
    assert "3600" in mac


def test_ci_invokes_macos_smoke_full_bomb() -> None:
    """macos-latest live-smoke must run the 22-check twin, not the old 5-check inline body."""
    ci = (_REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "macos-smoke.sh" in ci
    assert "SMALLER check set" not in ci
    assert 'Bash(bash .claude/skills/CyClaw-Sandbox/macos-smoke.sh)' in (
        _REPO_ROOT / ".claude" / "settings.json"
    ).read_text(encoding="utf-8")
