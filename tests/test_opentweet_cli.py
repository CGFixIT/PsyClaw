"""CLI: disabled post, dry-run, generate-don't-load plist/task."""

from __future__ import annotations

import os
import plistlib
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from opentweet.cli import EXIT_ENV, EXIT_OK, main
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset() -> None:
    reset_config_cache()
    yield
    reset_config_cache()


def _write(tmp_path: Path, block: dict) -> str:
    raw = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "opentweet": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


def test_status_rejects_url_userinfo_without_echo(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    cp = _write(tmp_path, {"api_base": "https://user:supersecret@opentweet.io"})
    assert main(["--config", cp, "status"]) == EXIT_ENV
    err = capsys.readouterr().err
    assert "supersecret" not in err
    assert "credentials" in err


def test_status_env_presence_without_api_key_identifiers(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENTWEET_API_KEY", "ot_supersecret")
    cp = _write(tmp_path, {"enabled": False})
    assert main(["--config", cp, "status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "ot_supersecret" not in out
    assert "api_key_env" not in out
    assert "api_key_set" not in out
    assert "vendor env configured" in out
    assert "query env configured" in out
    assert "yes" in out


def test_post_disabled_is_env(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": False})
    assert main(["--config", cp, "post", "--topic", "hi"]) == EXIT_ENV


def test_schedule_flag_requires_config(tmp_path: Path) -> None:
    topic = tmp_path / "t.txt"
    topic.write_text("x", encoding="utf-8")
    cp = _write(tmp_path, {"enabled": True, "topic_file": str(topic), "schedule_enabled": False})
    assert main(["--config", cp, "post", "--schedule", "--dry-run"]) == EXIT_ENV


def test_post_without_schedule_flag_stays_draft(tmp_path: Path) -> None:
    topic = tmp_path / "t.txt"
    topic.write_text("soul", encoding="utf-8")
    cp = _write(tmp_path, {"enabled": True, "topic_file": str(topic), "schedule_enabled": True})
    fake = {
        "ok": True,
        "mode": "draft",
        "text_hash": "a" * 64,
        "text_len": 10,
        "dry_run": True,
        "opentweet_id": None,
    }
    with patch("opentweet.cli.post_once", return_value=fake) as once:
        assert main(["--config", cp, "post", "--dry-run"]) == EXIT_OK
    assert once.call_args.kwargs.get("schedule") is False


def test_dry_run_skips_opentweet_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENTWEET_API_KEY", "ot_test")
    topic = tmp_path / "t.txt"
    topic.write_text("soul", encoding="utf-8")
    cp = _write(tmp_path, {"enabled": True, "topic_file": str(topic)})
    answer = {
        "answer": "Keep I6: posting stays out of band.",
        "hit_count": 2,
        "model_used": "local",
        "needs_confirm": False,
        "error": None,
    }
    with (
        patch("opentweet.client.post_query", return_value=answer),
        patch("opentweet.client.create_post") as create,
        patch("opentweet.client.get_me") as me,
    ):
        assert main(["--config", cp, "post", "--dry-run"]) == EXIT_OK
    create.assert_not_called()
    me.assert_not_called()


def test_schedule_plist_non_darwin_refuses(tmp_path: Path) -> None:
    topic = tmp_path / "t.txt"
    topic.write_text("x", encoding="utf-8")
    cp = _write(tmp_path, {"enabled": True, "topic_file": str(topic)})
    with patch("opentweet.cli.platform.system", return_value="Linux"):
        assert main(["--config", cp, "schedule-plist"]) == EXIT_ENV


def test_schedule_plist_disabled_noop(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": False})
    with (
        patch("opentweet.cli.platform.system", return_value="Darwin"),
        patch("utils.launchd_plist.Path.home", return_value=tmp_path / "home"),
    ):
        assert main(["--config", cp, "schedule-plist"]) == EXIT_OK
    assert not (tmp_path / "home" / "Library" / "LaunchAgents").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX plist home fixtures")
def test_schedule_plist_wraps_keychain_no_secret(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    topic = tmp_path / "t.txt"
    topic.write_text("x", encoding="utf-8")
    cp = _write(tmp_path, {"enabled": True, "topic_file": str(topic), "weekday": 1, "fire_hour": 6, "fire_minute": 0})
    home = tmp_path / "home"
    with (
        patch("opentweet.cli.platform.system", return_value="Darwin"),
        patch("utils.launchd_plist.Path.home", return_value=home),
    ):
        assert main(["--config", cp, "schedule-plist"]) == EXIT_OK
    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.opentweet.plist"
    document = plistlib.loads(plist_path.read_bytes())
    assert document["Label"] == "com.cgfixit.cyclaw.opentweet"
    assert document["StartCalendarInterval"]["Weekday"] == 1
    assert document["StartCalendarInterval"]["Hour"] == 6
    assert "EnvironmentVariables" not in document
    args = document["ProgramArguments"]
    assert args[0].endswith("cyclaw-keychain-env.sh")
    assert "OPENTWEET_API_KEY" in args
    assert "ot_" not in " ".join(args)
    assert "post" in args
    assert "--schedule" not in args
    raw = plist_path.read_bytes()
    assert b"ot_" not in raw
    out = capsys.readouterr().out
    assert "NOT loaded" in out
    assert "launchctl bootstrap" in out


def test_schedule_task_non_windows_refuses(tmp_path: Path) -> None:
    topic = tmp_path / "t.txt"
    topic.write_text("x", encoding="utf-8")
    cp = _write(tmp_path, {"enabled": True, "topic_file": str(topic)})
    with patch("opentweet.cli.platform.system", return_value="Linux"):
        assert main(["--config", cp, "schedule-task"]) == EXIT_ENV


def test_schedule_task_passes_schedule_flag_when_enabled(tmp_path: Path) -> None:
    topic = tmp_path / "t.txt"
    topic.write_text("x", encoding="utf-8")
    cp = _write(tmp_path, {"enabled": True, "topic_file": str(topic), "schedule_enabled": True})
    home = tmp_path / "home"
    with (
        patch("opentweet.cli.platform.system", return_value="Windows"),
        patch("utils.win_schtasks.Path.home", return_value=home),
    ):
        assert main(["--config", cp, "schedule-task"]) == EXIT_OK
    cmd = (home / ".CyClaw" / "tasks" / "CyClaw-opentweet.cmd").read_text(encoding="utf-8")
    assert "--schedule" in cmd


def test_schedule_task_wraps_credman(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    topic = tmp_path / "t.txt"
    topic.write_text("x", encoding="utf-8")
    cp = _write(tmp_path, {"enabled": True, "topic_file": str(topic)})
    home = tmp_path / "home"
    with (
        patch("opentweet.cli.platform.system", return_value="Windows"),
        patch("utils.win_schtasks.Path.home", return_value=home),
    ):
        assert main(["--config", cp, "schedule-task"]) == EXIT_OK
    cmd = (home / ".CyClaw" / "tasks" / "CyClaw-opentweet.cmd").read_text(encoding="utf-8")
    assert "CyClaw-CredMan-Env.ps1" in cmd
    assert "OPENTWEET_API_KEY" in cmd
    assert "ot_" not in cmd
    xml = (home / ".CyClaw" / "tasks" / "CyClaw-opentweet.xml").read_bytes().decode("utf-16")
    assert "ScheduleByWeek" in xml
    assert "Monday" in xml
    out = capsys.readouterr().out
    assert "schtasks /Create" in out
    assert "NOT registered" in out
