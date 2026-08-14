"""Tests for `python -m agentic.fsconnect.cli trash-empty-plist` -- the
generated (never auto-loaded) launchd plist for the weekly trash-empty job.

No real ~/Library/LaunchAgents is touched: Path.home() (via
utils.launchd_plist) is monkeypatched to a tmp_path in every test that
writes a plist.
"""

from __future__ import annotations

import os
import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from agentic.fsconnect import cli
from utils.logger import reset_config_cache

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX fixtures")


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg(tmp_path: Path, fsblock: dict) -> str:
    doc = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]}, "privacy": {}},
        "fsconnect": fsblock,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def _run(config_path: str, tmp_home: Path, *args: str) -> int:
    with (
        patch("agentic.fsconnect.cli.platform.system", return_value="Darwin"),
        patch("utils.launchd_plist.Path.home", return_value=tmp_home),
    ):
        return cli.main(["--config", config_path, "trash-empty-plist", *args])


def test_non_darwin_refuses(tmp_path: Path) -> None:
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(tmp_path / "share")]})
    with patch("agentic.fsconnect.cli.platform.system", return_value="Linux"):
        assert cli.main(["--config", cp, "trash-empty-plist"]) == 3


def test_disabled_is_noop(tmp_path: Path) -> None:
    cp = _cfg(tmp_path, {"enabled": False})
    assert _run(cp, tmp_path / "home") == 0
    assert not (tmp_path / "home" / "Library" / "LaunchAgents").exists()


def test_no_writable_root_configured_fails(tmp_path: Path) -> None:
    # writable_roots defaults to [None] (an OS-default path), NOT empty --
    # must be explicitly emptied to exercise the "nothing configured" path.
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": []})
    assert _run(cp, tmp_path / "home") == 2


def test_generates_valid_plist_from_default_root(tmp_path: Path, capsys) -> None:
    share = tmp_path / "share"
    share.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(share)]})
    home = tmp_path / "home"

    assert _run(cp, home) == 0

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.fsconnect-trash.plist"
    assert plist_path.exists()
    document = plistlib.loads(plist_path.read_bytes())

    assert document["Label"] == "com.cgfixit.cyclaw.fsconnect-trash"
    assert document["RunAtLoad"] is False
    assert document["StartCalendarInterval"] == {"Weekday": 1, "Hour": 3, "Minute": 0}
    args = document["ProgramArguments"]
    assert args[1:3] == ["-m", "agentic.fsconnect.cli"]
    assert "--config" in args
    assert "trash-empty" in args
    assert args[args.index("--root") + 1] == str(share)
    assert args[args.index("--reason") + 1] == "weekly launchd retention purge"
    assert "--confirm" in args
    assert "EnvironmentVariables" not in document

    out = capsys.readouterr().out
    assert "launchctl bootstrap gui/" in out


def test_generated_plist_never_contains_replace_or_secret_markers(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(share)]})
    home = tmp_path / "home"
    _run(cp, home)

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.fsconnect-trash.plist"
    raw = plist_path.read_bytes()
    assert b"REPLACE_" not in raw
    assert b"TOKEN" not in raw
    assert b"SECRET" not in raw


def test_root_weekday_hour_minute_reason_overrides(tmp_path: Path) -> None:
    share = tmp_path / "share"
    other = tmp_path / "other-share"
    share.mkdir()
    other.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(share)]})
    home = tmp_path / "home"

    assert (
        _run(
            cp,
            home,
            "--root",
            str(other),
            "--weekday",
            "5",
            "--hour",
            "10",
            "--minute",
            "30",
            "--reason",
            "custom purge reason",
        )
        == 0
    )

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.fsconnect-trash.plist"
    document = plistlib.loads(plist_path.read_bytes())
    assert document["StartCalendarInterval"] == {"Weekday": 5, "Hour": 10, "Minute": 30}
    args = document["ProgramArguments"]
    assert args[args.index("--root") + 1] == str(other)
    assert args[args.index("--reason") + 1] == "custom purge reason"


@pytest.mark.parametrize(
    "flag_args",
    [
        ["--weekday", "8"],
        ["--weekday", "-1"],
        ["--hour", "24"],
        ["--minute", "60"],
    ],
)
def test_out_of_range_schedule_args_fail(tmp_path: Path, flag_args: list[str]) -> None:
    share = tmp_path / "share"
    share.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(share)]})
    assert _run(cp, tmp_path / "home", *flag_args) == 2


def test_writes_enabled_false_prints_note(tmp_path: Path, capsys) -> None:
    share = tmp_path / "share"
    share.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(share)], "writes_enabled": False})
    assert _run(cp, tmp_path / "home") == 0
    out = capsys.readouterr().out
    assert "writes_enabled is currently false" in out


def test_idempotent_overwrite(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(share)]})
    home = tmp_path / "home"

    _run(cp, home, "--hour", "1")
    _run(cp, home, "--hour", "9")

    agents_dir = home / "Library" / "LaunchAgents"
    matches = list(agents_dir.glob("com.cgfixit.cyclaw.fsconnect-trash*"))
    assert len(matches) == 1
    document = plistlib.loads(matches[0].read_bytes())
    assert document["StartCalendarInterval"]["Hour"] == 9
