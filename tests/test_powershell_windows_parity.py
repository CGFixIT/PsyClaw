"""Static contract tests for powershell/ Windows parity glue.

No real schtasks, Credential Manager, or ACL mutation: these tests read the
scripts as text the same way tests/test_macos_scripts.py pins macos/*.sh.
"""

from __future__ import annotations

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PS = _REPO_ROOT / "powershell"


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
    assert "Dispose()" in set_text


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
