"""Tests for `python -m agentic.fsconnect.cli trash-empty-task`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from agentic.fsconnect import cli
from utils.logger import reset_config_cache


def setup_function() -> None:
    reset_config_cache()


def teardown_function() -> None:
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
        patch("agentic.fsconnect.cli.platform.system", return_value="Windows"),
        patch("utils.win_schtasks.Path.home", return_value=tmp_home),
    ):
        return cli.main(["--config", config_path, "trash-empty-task", *args])


def test_non_windows_refuses(tmp_path: Path) -> None:
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(tmp_path / "share")]})
    with patch("agentic.fsconnect.cli.platform.system", return_value="Linux"):
        assert cli.main(["--config", cp, "trash-empty-task"]) == 3


def test_disabled_is_noop(tmp_path: Path) -> None:
    cp = _cfg(tmp_path, {"enabled": False})
    assert _run(cp, tmp_path / "home") == 0
    assert not (tmp_path / "home" / ".CyClaw" / "tasks").exists()


def test_generates_xml_without_registering(tmp_path: Path, capsys) -> None:
    share = tmp_path / "share"
    share.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(share)]})
    home = tmp_path / "home"

    assert _run(cp, home) == 0

    xml_path = home / ".CyClaw" / "tasks" / "CyClaw-fsconnect-trash.xml"
    assert xml_path.exists()
    text = xml_path.read_bytes().decode("utf-16")
    assert "trash-empty" in text or (home / ".CyClaw" / "tasks" / "CyClaw-fsconnect-trash.cmd").exists()
    cmd = (home / ".CyClaw" / "tasks" / "CyClaw-fsconnect-trash.cmd").read_text(encoding="utf-8")
    assert "trash-empty" in cmd
    assert "--confirm" in cmd
    assert str(share) in cmd
    assert "REPLACE_" not in cmd
    assert "schtasks /Create" not in text
    out = capsys.readouterr().out
    assert "schtasks /Create" in out
    assert "/XML" in out


def test_out_of_range_schedule_args_fail(tmp_path: Path) -> None:
    share = tmp_path / "share"
    share.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(share)]})
    assert _run(cp, tmp_path / "home", "--weekday", "8") == 2
    assert _run(cp, tmp_path / "home", "--hour", "24") == 2
