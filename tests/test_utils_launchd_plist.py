"""Tests for utils.launchd_plist -- shared, stdlib-only macOS plist helpers.

No real launchctl or ~/Library/LaunchAgents is ever touched: Path.home() and
subprocess.run are monkeypatched in every test that could reach either.
"""

from __future__ import annotations

import os
import plistlib
from pathlib import Path
from unittest.mock import MagicMock, patch

from utils import launchd_plist


def _completed(returncode: int = 0) -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    return m


def test_python_executable_prefers_sys_executable() -> None:
    assert launchd_plist.python_executable()  # non-empty, resolvable in this env


def test_current_uid_matches_os_getuid_when_present() -> None:
    if hasattr(os, "getuid"):
        assert launchd_plist.current_uid() == os.getuid()


def test_current_uid_falls_back_to_zero_when_getuid_absent() -> None:
    if not hasattr(os, "getuid"):
        # Already absent on this platform (e.g. Windows) -- exercises the
        # fallback directly; nothing to save/delete/restore.
        assert launchd_plist.current_uid() == 0
        return
    real_getuid = os.getuid
    del os.getuid
    try:
        assert launchd_plist.current_uid() == 0
    finally:
        os.getuid = real_getuid


def test_agents_dir_and_logs_dir_and_plist_path(tmp_path: Path) -> None:
    with patch("utils.launchd_plist.Path.home", return_value=tmp_path):
        assert launchd_plist.agents_dir() == tmp_path / "Library" / "LaunchAgents"
        assert launchd_plist.logs_dir() == tmp_path / "Library" / "Logs" / "CyClaw"
        assert launchd_plist.plist_path("com.example.job") == (
            tmp_path / "Library" / "LaunchAgents" / "com.example.job.plist"
        )


def test_logs_dir_creates_the_directory(tmp_path: Path) -> None:
    # launchd does not create a missing StandardOutPath/StandardErrorPath
    # directory itself -- every caller builds a log_path from this return
    # value, so the directory must exist by the time write_plist() runs.
    with patch("utils.launchd_plist.Path.home", return_value=tmp_path):
        expected = tmp_path / "Library" / "Logs" / "CyClaw"
        assert not expected.exists()
        result = launchd_plist.logs_dir()
        assert result == expected
        assert result.is_dir()


def test_logs_dir_is_idempotent_when_already_present(tmp_path: Path) -> None:
    with patch("utils.launchd_plist.Path.home", return_value=tmp_path):
        launchd_plist.logs_dir()
        launchd_plist.logs_dir()  # must not raise on the second call
        assert (tmp_path / "Library" / "Logs" / "CyClaw").is_dir()


def test_write_plist_is_atomic_and_leaves_no_tmp_file(tmp_path: Path) -> None:
    path = tmp_path / "nested" / "com.example.job.plist"
    document = {"Label": "com.example.job", "RunAtLoad": False}

    launchd_plist.write_plist(document, path)

    assert path.exists()
    assert plistlib.loads(path.read_bytes()) == document
    assert list(path.parent.glob("*.tmp")) == []


def test_write_plist_overwrites_idempotently(tmp_path: Path) -> None:
    path = tmp_path / "com.example.job.plist"
    launchd_plist.write_plist({"Label": "a", "N": 1}, path)
    launchd_plist.write_plist({"Label": "a", "N": 2}, path)

    assert plistlib.loads(path.read_bytes())["N"] == 2
    assert list(path.parent.glob("com.example.job.plist*")) == [path]


def test_bootstrap_hint_never_calls_subprocess(tmp_path: Path) -> None:
    path = tmp_path / "com.example.job.plist"
    with patch("utils.launchd_plist.subprocess.run") as mock_run:
        hint = launchd_plist.bootstrap_hint(path)
    mock_run.assert_not_called()
    assert hint == f"launchctl bootstrap gui/{launchd_plist.current_uid()} {path}"


def test_bootout_no_op_when_launchctl_missing(tmp_path: Path) -> None:
    path = tmp_path / "com.example.job.plist"
    with (
        patch("utils.launchd_plist.shutil.which", return_value=None),
        patch("utils.launchd_plist.subprocess.run") as mock_run,
    ):
        launchd_plist.bootout(path)
    mock_run.assert_not_called()


def test_bootout_calls_launchctl_bootout(tmp_path: Path) -> None:
    path = tmp_path / "com.example.job.plist"
    with (
        patch("utils.launchd_plist.shutil.which", return_value="/bin/launchctl"),
        patch("utils.launchd_plist.subprocess.run", return_value=_completed()) as mock_run,
    ):
        launchd_plist.bootout(path)
    argv = mock_run.call_args.args[0]
    assert argv[0] == "/bin/launchctl"
    assert argv[1] == "bootout"
    assert str(path) in argv


def test_is_loaded_returns_none_when_launchctl_missing() -> None:
    with (
        patch("utils.launchd_plist.shutil.which", return_value=None),
        patch("utils.launchd_plist.subprocess.run") as mock_run,
    ):
        assert launchd_plist.is_loaded("com.example.job") is None
    mock_run.assert_not_called()


def test_is_loaded_true_and_false() -> None:
    with patch("utils.launchd_plist.shutil.which", return_value="/bin/launchctl"):
        with patch("utils.launchd_plist.subprocess.run", return_value=_completed(returncode=0)):
            assert launchd_plist.is_loaded("com.example.job") is True
        with patch("utils.launchd_plist.subprocess.run", return_value=_completed(returncode=1)):
            assert launchd_plist.is_loaded("com.example.job") is False


def test_keychain_wrapper_path(tmp_path: Path) -> None:
    assert launchd_plist.keychain_wrapper_path(tmp_path) == str(
        tmp_path / "macos" / "cyclaw-keychain-env.sh"
    )


def test_wrap_with_keychain_secrets_empty_list_is_noop() -> None:
    argv = ["python", "-m", "telegram.cli", "poll"]
    assert launchd_plist.wrap_with_keychain_secrets(argv, [], "/wrapper.sh") == argv


def test_wrap_with_keychain_secrets_single_secret() -> None:
    argv = ["python", "-m", "telegram.cli", "poll"]
    wrapped = launchd_plist.wrap_with_keychain_secrets(
        argv, [("svc-a", "VAR_A")], "/wrapper.sh"
    )
    assert wrapped == ["/wrapper.sh", "svc-a", "VAR_A", "--", "python", "-m", "telegram.cli", "poll"]


def test_wrap_with_keychain_secrets_chains_in_order() -> None:
    argv = ["python", "poll"]
    wrapped = launchd_plist.wrap_with_keychain_secrets(
        argv, [("svc-a", "VAR_A"), ("svc-b", "VAR_B")], "/wrapper.sh"
    )
    # Outermost layer resolves the FIRST secret in the list.
    assert wrapped == [
        "/wrapper.sh", "svc-a", "VAR_A", "--",
        "/wrapper.sh", "svc-b", "VAR_B", "--",
        "python", "poll",
    ]

def test_probe_python_raises_when_imports_missing() -> None:
    with patch(
        "utils.launchd_plist.subprocess.run",
        return_value=_completed(returncode=1),
    ):
        try:
            launchd_plist._probe_python("/fake/python")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "fastapi/uvicorn" in str(exc)


def test_python_executable_uses_cyclaw_home_venv(tmp_path: Path, monkeypatch) -> None:
    venv_py = tmp_path / "venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("", encoding="utf-8")
    monkeypatch.delenv("CYCLAW_PYTHON", raising=False)
    monkeypatch.setenv("CYCLAW_HOME", str(tmp_path))
    with patch("utils.launchd_plist._probe_python") as probe:
        assert launchd_plist.python_executable() == str(venv_py)
        probe.assert_called_once_with(str(venv_py))


def test_python_executable_falls_back_to_which(monkeypatch) -> None:
    monkeypatch.delenv("CYCLAW_PYTHON", raising=False)
    monkeypatch.delenv("CYCLAW_HOME", raising=False)
    monkeypatch.setattr(launchd_plist.sys, "executable", "")
    monkeypatch.setattr(launchd_plist.os.path, "isfile", lambda _p: False)
    monkeypatch.setattr(launchd_plist.shutil, "which", lambda name: "/usr/bin/python3" if name == "python3" else None)
    with patch("utils.launchd_plist._probe_python") as probe:
        assert launchd_plist.python_executable() == "/usr/bin/python3"
        probe.assert_called_once_with("/usr/bin/python3")
    monkeypatch.setattr(launchd_plist.shutil, "which", lambda _name: None)
    assert launchd_plist.python_executable() == "python"
