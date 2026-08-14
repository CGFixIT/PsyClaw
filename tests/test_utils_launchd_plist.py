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
