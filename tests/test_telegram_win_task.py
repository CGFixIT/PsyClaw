"""Tests for `python -m telegram.cli poll-task` / `health-task`."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import yaml

from telegram.cli import EXIT_ENV, EXIT_FAIL, EXIT_OK, main


def _write(tmp_path: Path, block: dict) -> str:
    raw = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "telegram": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


def _run(config_path: str, tmp_home: Path, *args: str) -> int:
    with (
        patch("telegram.cli.platform.system", return_value="Windows"),
        patch("utils.win_schtasks.Path.home", return_value=tmp_home),
    ):
        return main(["--config", config_path, *args])


def test_poll_task_non_windows_refuses(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]})
    with patch("telegram.cli.platform.system", return_value="Linux"):
        assert main(["--config", cp, "poll-task"]) == EXIT_ENV


def test_poll_task_requires_chat_mode(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]})
    assert _run(cp, tmp_path / "home", "poll-task") == EXIT_ENV


def test_poll_task_disabled_is_noop(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": False})
    assert _run(cp, tmp_path / "home", "poll-task") == EXIT_OK
    assert not (tmp_path / "home" / ".CyClaw" / "tasks").exists()


def test_poll_task_wraps_credman_and_has_no_token(tmp_path: Path, capsys) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "chat", "allowed_chat_ids": ["12345"]})
    home = tmp_path / "home"
    assert _run(cp, home, "poll-task") == EXIT_OK
    cmd = (home / ".CyClaw" / "tasks" / "CyClaw-telegram-poll.cmd").read_text(encoding="utf-8")
    assert "CyClaw-CredMan-Env.ps1" in cmd
    assert "TELEGRAM_BOT_TOKEN" in cmd
    assert "123456:" not in cmd
    xml = (home / ".CyClaw" / "tasks" / "CyClaw-telegram-poll.xml").read_bytes().decode("utf-16")
    assert "PT10S" in xml
    assert "LogonTrigger" in xml
    assert "schtasks /Create" not in xml
    out = capsys.readouterr().out
    assert "schtasks /Create" in out


def test_health_task_rejects_bad_chat_and_interval(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["111"]})
    assert _run(cp, tmp_path / "home", "health-task", "--chat-id", "999") == EXIT_FAIL
    assert _run(cp, tmp_path / "home", "health-task", "--interval-sec", "0") == EXIT_FAIL


def test_health_task_empty_allowlist_is_env_not_exception(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": []})
    assert _run(cp, tmp_path / "home", "health-task") == EXIT_ENV


def test_health_task_embeds_chat_not_token(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["111"]})
    home = tmp_path / "home"
    assert _run(cp, home, "health-task") == EXIT_OK
    cmd = (home / ".CyClaw" / "tasks" / "CyClaw-telegram-health.cmd").read_text(encoding="utf-8")
    assert "111" in cmd
    assert "curl.exe" in cmd
    assert "CyClaw-CredMan-Env.ps1" in cmd
    assert "REPLACE_" not in cmd
