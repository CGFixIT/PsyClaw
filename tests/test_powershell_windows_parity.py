"""Contract tests for powershell/ Windows parity glue.

No real schtasks, Credential Manager, or ACL mutation. Most tests read the
scripts as text; the installer regression uses a disposable home and fake git.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PS = _REPO_ROOT / "powershell"


def test_installer_update_checks_git_exit() -> None:
    text = (_PS / "Install-CyClaw.ps1").read_text(encoding="utf-8")
    update = text.split('Write-Step "repo already present at $Repo (pulling latest main)"', 1)[1]
    update = update.split("Assert-SafeRepoPath $Repo", 1)[0]
    assert re.search(
        r"& git -C \$Repo pull --ff-only --no-autostash\r?\n"
        r'\s*if \(\$LASTEXITCODE -ne 0\) \{ throw "git pull failed',
        update,
    )


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell 5.1")
def test_installer_git_failure_stops_before_launcher(tmp_path: Path) -> None:
    home = tmp_path / "operator"
    server = home / ".CyClaw" / "repo" / "harness" / "server.py"
    server.parent.mkdir(parents=True)
    server.write_text("", encoding="utf-8")

    shim_dir = tmp_path / "bin"
    shim_dir.mkdir()
    (shim_dir / "git.cmd").write_text("@echo off\r\nexit /b 37\r\n", encoding="ascii")

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        pytest.skip("Windows PowerShell 5.1 is unavailable")

    env = os.environ.copy()
    env["USERPROFILE"] = str(home)
    env["PATH"] = str(shim_dir) + os.pathsep + env.get("PATH", "")
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_PS / "Install-CyClaw.ps1"),
            "-SkipPythonDeps",
            "-NoProfileEdit",
            "-NoPathEdit",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "git pull failed (exit 37)" in output
    assert not (home / ".CyClaw" / "bin" / "Invoke-CyClaw.ps1").exists()


def test_uninstall_deletes_only_known_task_names() -> None:
    text = (_PS / "Uninstall-CyClaw.ps1").read_text(encoding="utf-8")
    assert "Unschedule-KnownTasks" in text
    assert "Unschedule-SyncJob" in text
    assert "schtasks.exe" in text
    assert "/Delete" in text
    assert "/TN" in text
    for name in (
        "CyClaw Dropbox Sync",
        "CyClaw fsconnect-trash",
        "CyClaw telegram-poll",
        "CyClaw telegram-health",
        "CyClaw gate",
        "CyClaw harness",
        "CyClaw opentweet",
    ):
        assert name in text
    assert "*" not in text.split("KnownTaskNames")[1].split(")")[0]
    assert "/Delete /TN *" not in text
    assert "wildcard" in text.lower()


def test_uninstall_missing_schtasks_delete_is_noop_under_ps51() -> None:
    """Bare `/Delete` under `$ErrorActionPreference=Stop` aborts PS 5.1.

    schtasks writes 'ERROR: The system cannot find the file specified.' to
    stderr when the task is absent; Windows PowerShell 5.1 turns that into a
    terminating NativeCommandError. The body must query first and relax Stop
    around the native calls (macOS twin: `launchctl bootout … || true`).
    """
    text = (_PS / "Uninstall-CyClaw.ps1").read_text(encoding="utf-8")
    body = text.split("function Unschedule-KnownTasks", 1)[1]
    body = body.split("Unschedule-SyncJob", 1)[0]
    assert "/Query" in body
    assert "cmd.exe" in body
    assert "ErrorActionPreference =" not in body
    assert "cannot find" in body
    assert "2>$null | Out-Null" not in body
    assert "exit 0" in text


def test_invoke_loads_persisted_api_key_from_dotenv() -> None:
    """Daily cyclaw / Invoke-CyClaw.ps1 must source ~/.CyClaw/.env when empty."""
    text = (_PS / "Invoke-CyClaw.ps1").read_text(encoding="utf-8")
    assert 'Join-Path $Home_ ".env"' in text
    assert 'Join-Path $Repo ".env"' in text
    assert "Test-CyclawDotenvOwnerOnly" in text
    assert "BUILTIN\\Users" in text
    assert "FileSystemRights]::ReadData" not in text
    assert "(R,W)" in text
    assert "refusing to source" in text
    assert "ACL is not owner-only" in text
    load_idx = text.index('Join-Path $Home_ ".env"')
    start_idx = text.index("-m harness.server")
    assert load_idx < start_idx
    warn = "Typing the key in the browser cannot configure the server"
    assert warn in text
    assert load_idx < text.index(warn)


def test_invoke_validates_port_before_console_url() -> None:
    """Port range must match harness _MIN_USER_PORT/_MAX_PORT before URL print."""
    text = (_PS / "Invoke-CyClaw.ps1").read_text(encoding="utf-8")
    assert re.search(
        r"\$Port\s+-lt\s+1024\s+-or\s+\$Port\s+-gt\s+65535",
        text,
    )
    range_idx = text.index("$Port -lt 1024")
    url_idx = text.index("http://127.0.0.1:$Port")
    assert range_idx < url_idx


def test_installer_requires_explicit_flag_to_replace_existing_repo() -> None:
    """A stale %USERPROFILE%\\.CyClaw\\repo must not be silently Remove-Item'd."""
    text = (_PS / "Install-CyClaw.ps1").read_text(encoding="utf-8")
    assert "[switch]$ReplaceRepo" in text
    assert "re-run with -ReplaceRepo to overwrite it" in text
    assert "if (Test-Path $Repo) { Remove-Item -Recurse -Force $Repo }" not in text


@pytest.mark.skipif(os.name != "nt", reason="requires Windows PowerShell 5.1")
def test_installer_preserves_an_unusable_default_repo_without_replace_flag(tmp_path: Path) -> None:
    """Default clone path must fail closed before deleting existing data."""
    home = tmp_path / "operator"
    repo = home / ".CyClaw" / "repo"
    repo.mkdir(parents=True)
    sentinel = repo / "operator-data.txt"
    sentinel.write_text("keep me", encoding="utf-8")

    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    powershell = system_root / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    if not powershell.is_file():
        pytest.skip("Windows PowerShell 5.1 is unavailable")

    env = os.environ.copy()
    env["USERPROFILE"] = str(home)
    result = subprocess.run(
        [
            str(powershell),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(_PS / "Install-CyClaw.ps1"),
            "-SkipPythonDeps",
            "-NoProfileEdit",
            "-NoPathEdit",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert sentinel.read_text(encoding="utf-8") == "keep me"
    assert "-ReplaceRepo" in output


def test_credman_marshal_sites_carry_devskim_suppression() -> None:
    """GHAS DevSkim DS104456 flags Marshal/PtrToStructure as restricted.

    CredRead/CredWrite cannot be done fail-closed via cmdkey (argv leak).
    Each PowerShell Marshal / call-operator site must carry an inline ignore.
    """
    env_text = (_PS / "CyClaw-CredMan-Env.ps1").read_text(encoding="utf-8")
    set_text = (_PS / "CyClaw-CredMan-Set.ps1").read_text(encoding="utf-8")
    for line in env_text.splitlines():
        if "InteropServices.Marshal" in line or "PtrToStructure" in line:
            assert "DevSkim: ignore DS104456" in line, line
    for line in set_text.splitlines():
        if "InteropServices.Marshal" in line:
            assert "DevSkim: ignore DS104456" in line, line
        if "Marshal.WriteByte" in line:
            assert "DevSkim: ignore DS104456" in line, line
    assert "Dispose()" in set_text


def test_credman_set_ps7_ctrl_c_registers_cancel_keypress() -> None:
    """PS7 Ctrl+C must run the same wipe path as finally, then exit 130.

    Windows PowerShell 5.1 often skips finally on Ctrl+C. The handler is
    gated on PSVersion.Major -ge 7 so 5.1 cannot hang on e.Cancel=$true.
    """
    text = (_PS / "CyClaw-CredMan-Set.ps1").read_text(encoding="utf-8")
    assert "function Invoke-CyclawCredCleanup" in text
    assert "CancelKeyPress" in text
    assert "RegisterCancelHandler" in text
    assert "private static void HandleCancel" in text
    assert "$PSVersionTable.PSVersion.Major -ge 7" in text
    assert "eventArgs.Cancel = true" in text
    assert "Environment.Exit(130)" in text
    assert "Marshal.WriteByte" in text
    assert "Interlocked.Exchange" in text
    assert "UnregisterCancelHandler" in text
    assert "[ConsoleCancelEventHandler]{" not in text
    assert "Do not install this on Windows PowerShell" in text


def test_uninstall_and_setup_never_enable_writes_or_indexing() -> None:
    combined = "\n".join(
        (_PS / name).read_text(encoding="utf-8")
        for name in ("Setup-FsConnect.ps1", "Uninstall-CyClaw.ps1", "Install-CyClaw.ps1")
    )
    assert "writes_enabled: true" not in combined
    assert "index_enabled: true" not in combined
    assert "writes_enabled = $true" not in combined


def test_setup_fsconnect_is_prepare_only_safe() -> None:
    setup = (_PS / "Setup-FsConnect.ps1").read_text(encoding="utf-8")
    assert "PrepareOnly" in setup
    assert "_enable_fsconnect_readlist.py" in setup
    assert "CyClaw-FS" in setup
    assert "SetAccessRuleProtection" in setup


def test_credman_set_never_uses_cmdkey_pass() -> None:
    set_text = (_PS / "CyClaw-CredMan-Set.ps1").read_text(encoding="utf-8")
    env_text = (_PS / "CyClaw-CredMan-Env.ps1").read_text(encoding="utf-8")
    assert not re.search(r"(?m)^\s*cmdkey\b", set_text, re.IGNORECASE)
    assert not re.search(r"(?m)^\s*cmdkey\b", env_text, re.IGNORECASE)
    assert "CredWrite" in set_text
    assert "Read-Host" in set_text
    assert "AsSecureString" in set_text
    assert "CredRead" in env_text
    assert re.search(r"\s--\s", env_text) or '"--"' in env_text
    assert "IsInputRedirected" in set_text


def test_credman_env_validates_env_var_name() -> None:
    text = (_PS / "CyClaw-CredMan-Env.ps1").read_text(encoding="utf-8")
    assert r"^[A-Za-z_][A-Za-z0-9_]*$" in text


def test_powershell_readme_documents_credman_and_known_task_names() -> None:
    readme = (_PS / "README.md").read_text(encoding="utf-8")
    assert "CyClaw-CredMan-Set.ps1" in readme
    assert "CyClaw-CredMan-Env.ps1" in readme
    assert "Setup-FsConnect.ps1" in readme
    assert "CyClaw Dropbox Sync" in readme
    assert "wildcard" in readme.lower()
