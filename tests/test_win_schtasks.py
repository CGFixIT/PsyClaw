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


def test_bat_quote_rejects_newline() -> None:
    try:
        win_schtasks.bat_quote("foo\nbar")
    except ValueError:
        return
    raise AssertionError("expected ValueError")


def test_wrap_with_credman_secrets_rejects_bad_target() -> None:
    try:
        win_schtasks.wrap_with_credman_secrets(
            ["python", "-m", "x"],
            [("bad target", "TELEGRAM_BOT_TOKEN")],
            wrapper_path="wrapper.ps1",
        )
    except ValueError:
        return
    raise AssertionError("expected ValueError")

def test_python_executable_falls_back(monkeypatch) -> None:
    monkeypatch.setattr(win_schtasks.sys, "executable", "")
    monkeypatch.setattr(win_schtasks.os.path, "isfile", lambda _p: False)
    monkeypatch.setattr(win_schtasks.shutil, "which", lambda name: "C:/py.exe" if name == "python" else None)
    assert win_schtasks.python_executable() == "C:/py.exe"
    monkeypatch.setattr(win_schtasks.shutil, "which", lambda _name: None)
    assert win_schtasks.python_executable() == "python"


def test_tasks_and_logs_dir_create(tmp_path: Path) -> None:
    with patch("utils.win_schtasks.Path.home", return_value=tmp_path):
        assert win_schtasks.tasks_dir() == tmp_path / ".CyClaw" / "tasks"
        assert win_schtasks.logs_dir() == tmp_path / ".CyClaw" / "logs"
        assert (tmp_path / ".CyClaw" / "tasks").is_dir()
        assert (tmp_path / ".CyClaw" / "logs").is_dir()


def test_write_cmd_launcher_rejects_empty_and_bad_env(tmp_path: Path) -> None:
    path = tmp_path / "x.cmd"
    try:
        win_schtasks.write_cmd_launcher(path, [])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        win_schtasks.write_cmd_launcher(path, ["python"], env={"bad-name": "1"})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        win_schtasks.write_cmd_launcher(path, ["python"], env={"OK": 'has"quote'})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_credman_wrapper_path_and_settings_restart(tmp_path: Path) -> None:
    assert "CyClaw-CredMan" in win_schtasks.credman_wrapper_path(tmp_path)
    xml = win_schtasks.build_task_xml(
        task_name="t",
        command="cmd.exe",
        arguments="/c x.cmd",
        working_directory=str(tmp_path),
        triggers=win_schtasks.logon_trigger(),
        restart_interval="PT1M",
        restart_count=2,
    )
    assert "RestartOnFailure" in xml
    assert "PT1M" in xml
    assert "<LogonTrigger>" in xml


def test_weekly_hour_minute_and_interval_validation() -> None:
    try:
        win_schtasks.weekly_calendar_trigger(1, 24, 0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        win_schtasks.interval_trigger(0)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass

def test_python_executable_returns_sys_executable_when_present() -> None:
    exe = win_schtasks.python_executable()
    assert exe
    assert Path(exe).exists() or exe == "python"


def test_write_cmd_launcher_rejects_newline_env_value(tmp_path: Path) -> None:
    try:
        win_schtasks.write_cmd_launcher(tmp_path / "x.cmd", ["python"], env={"OK": "a\nb"})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
    try:
        win_schtasks.write_cmd_launcher(tmp_path / "y.cmd", ["python"], env={"OK": "a\rb"})
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


def test_write_cmd_launcher_writes_env_with_percent(tmp_path: Path) -> None:
    path = tmp_path / "z.cmd"
    win_schtasks.write_cmd_launcher(path, ["python", "-m", "x"], env={"FOO": "a%b"})
    text = path.read_text(encoding="utf-8")
    assert 'set "FOO=a%%b"' in text


def test_wrap_with_credman_rejects_bad_var_name() -> None:
    try:
        win_schtasks.wrap_with_credman_secrets(
            ["python", "-m", "x"],
            [("svc-token", "BAD VAR")],
            wrapper_path="wrapper.ps1",
        )
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
