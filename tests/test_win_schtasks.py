"""Tests for utils/win_schtasks.py — generate-only Task Scheduler XML."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from utils import win_schtasks


def test_register_hint_never_executes_and_uses_xml() -> None:
    hint = win_schtasks.register_hint("CyClaw fsconnect-trash", Path("C:/tmp/t.xml"))
    assert hint.startswith("schtasks /Create")
    assert "/XML" in hint
    assert "CyClaw fsconnect-trash" in hint
    assert "/Run" not in hint


def test_wrap_with_credman_secrets_nests_and_leaves_argv_secret_free() -> None:
    inner = ["python", "-m", "telegram.cli", "poll"]
    wrapped = win_schtasks.wrap_with_credman_secrets(
        inner,
        [("svc-token", "TELEGRAM_BOT_TOKEN"), ("svc-key", "CYCLAW_API_KEY")],
        wrapper_path="C:/repo/powershell/CyClaw-CredMan-Env.ps1",
        powershell="C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe",
    )
    joined = " ".join(wrapped)
    assert "TELEGRAM_BOT_TOKEN" in joined
    assert "svc-token" in joined
    assert "--" in wrapped
    assert "sk-" not in joined
    assert "123456:ABC" not in joined
    assert wrapped[-3:] == ["-m", "telegram.cli", "poll"]


def test_wrap_empty_secrets_is_identity() -> None:
    argv = ["python", "-m", "x"]
    assert win_schtasks.wrap_with_credman_secrets(argv, [], "wrapper.ps1") == argv


def test_write_generated_weekly_task_is_utf16_and_has_no_create(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    with patch("utils.win_schtasks.Path.home", return_value=home):
        xml_path, cmd_path = win_schtasks.write_generated_task(
            task_name="CyClaw fsconnect-trash",
            argv=["python", "-m", "agentic.fsconnect.cli", "trash-empty", "--confirm"],
            working_directory=str(tmp_path),
            triggers=win_schtasks.weekly_calendar_trigger(1, 3, 0),
        )
    raw = xml_path.read_bytes()
    assert raw.startswith(b"\xff\xfe") or raw[0:2] == b"\xff\xfe"
    text = raw.decode("utf-16")
    assert "schtasks /Create" not in text
    assert "<Monday />" in text
    assert "03:00:00" in text
    assert "IgnoreNew" in text
    assert cmd_path.exists()
    cmd = cmd_path.read_text(encoding="utf-8")
    assert "trash-empty" in cmd
    assert "--confirm" in cmd


def test_weekly_weekday_out_of_range() -> None:
    try:
        win_schtasks.weekly_calendar_trigger(8, 0, 0)
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_interval_trigger_pt_seconds() -> None:
    xml = win_schtasks.interval_trigger(300)
    assert "PT300S" in xml


def test_bat_quote_doubles_percent() -> None:
    assert win_schtasks.bat_quote(r"C:\Users\%TEMP%\x") == r'"C:\Users\%%TEMP%%\x"'
