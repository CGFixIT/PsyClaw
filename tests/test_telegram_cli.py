"""CLI smoke tests for telegram.cli (no network)."""

from __future__ import annotations

import getpass
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from telegram.cli import EXIT_ENV, EXIT_FAIL, EXIT_OK, main
from utils.errors import (
    TelegramConfigError,
    TelegramError,
    TelegramRefused,
    TelegramRuntimeError,
)
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write(tmp_path: Path, block: dict) -> str:
    tmp_path.mkdir(parents=True, exist_ok=True)
    raw = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "telegram": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


def test_status_disabled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path, {"enabled": False})
    assert main(["--config", path, "status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "enabled" in out.lower() or "False" in out


def test_test_subcommand(tmp_path: Path) -> None:
    path = _write(tmp_path, {"enabled": False})
    assert main(["--config", path, "test"]) == EXIT_OK


def test_send_disabled_is_noop(tmp_path: Path) -> None:
    path = _write(tmp_path, {"enabled": False})
    assert main(["--config", path, "send", "--chat-id", "1", "--text", "x"]) == EXIT_OK


def test_send_dry_run_allowlisted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["99"]},
    )
    code = main(
        ["--config", path, "send", "--chat-id", "99", "--text", "hello dry", "--dry-run"]
    )
    assert code == EXIT_OK
    assert "dry-run" in capsys.readouterr().out


def test_send_dry_run_not_allowlisted(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["99"]},
    )
    assert (
        main(["--config", path, "send", "--chat-id", "1", "--text", "x", "--dry-run"])
        == EXIT_FAIL
    )


def test_poll_refuses_notify_mode(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )
    assert main(["--config", path, "poll", "--max-iterations", "1"]) == EXIT_ENV


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("test", []),
        ("send", ["--chat-id", "1", "--text", "hello"]),
        ("poll", ["--max-iterations", "1"]),
    ],
)
def test_enabled_commands_classify_missing_token_as_environment_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    extra: list[str],
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]},
    )
    assert main(["--config", path, command, *extra]) == EXIT_ENV


def test_send_can_use_hidden_transient_token(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    token = "fresh-runtime-token"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )

    with (
        patch("telegram.cli.sys.stdin") as stdin,
        patch("telegram.cli.getpass.getpass", return_value=token) as prompt,
        patch("telegram.cli.send_notify", return_value={"result": {"message_id": 7}}) as send,
    ):
        stdin.isatty.return_value = True
        code = main(
            [
                "--config",
                path,
                "send",
                "--chat-id",
                "1",
                "--text",
                "hello",
                "--prompt-token",
            ]
        )

    assert code == EXIT_OK
    prompt.assert_called_once_with("Telegram bot token: ")
    cfg = send.call_args.args[0]
    assert cfg.resolve_bot_token() == token
    assert token not in repr(cfg)
    assert token not in str(cfg.to_public_dict())
    assert "TELEGRAM_BOT_TOKEN" not in os.environ
    captured = capsys.readouterr()
    assert token not in captured.out
    assert token not in captured.err


def test_prompt_token_refuses_noninteractive_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )

    with (
        patch("telegram.cli.sys.stdin") as stdin,
        patch("telegram.cli.getpass.getpass") as prompt,
        patch("telegram.cli.send_notify") as send,
    ):
        stdin.isatty.return_value = False
        code = main(
            [
                "--config",
                path,
                "send",
                "--chat-id",
                "1",
                "--text",
                "hello",
                "--prompt-token",
            ]
        )

    assert code == EXIT_ENV
    prompt.assert_not_called()
    send.assert_not_called()


def test_prompt_token_handles_keyboard_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )

    with (
        patch("telegram.cli.sys.stdin") as stdin,
        patch("telegram.cli.getpass.getpass", side_effect=KeyboardInterrupt),
        patch("telegram.cli.send_notify") as send,
    ):
        stdin.isatty.return_value = True
        code = main(
            [
                "--config",
                path,
                "send",
                "--chat-id",
                "1",
                "--text",
                "hello",
                "--prompt-token",
            ]
        )

    assert code == EXIT_ENV
    send.assert_not_called()
    captured = capsys.readouterr()
    assert "Telegram bot token prompt cancelled" in captured.err
    assert "Traceback" not in captured.err


def test_missing_config_is_typed_environment_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = str(tmp_path / "missing.yaml")
    assert main(["--config", path, "status"]) == EXIT_ENV
    captured = capsys.readouterr()
    assert "Unable to load Telegram configuration" in captured.err
    assert "Traceback" not in captured.err


def test_invalid_env_name_does_not_echo_possible_token(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    possible_token = "123456789:ABC-DEF_possible-live-token"
    path = _write(tmp_path, {"enabled": False, "bot_token_env": possible_token})
    assert main(["--config", path, "status"]) == EXIT_ENV
    captured = capsys.readouterr()
    assert possible_token not in captured.out
    assert possible_token not in captured.err


def test_prompt_token_handles_eof_as_environment_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )

    with (
        patch("telegram.cli.sys.stdin") as stdin,
        patch("telegram.cli.getpass.getpass", side_effect=EOFError),
        patch("telegram.cli.send_notify") as send,
    ):
        stdin.isatty.return_value = True
        code = main(
            [
                "--config",
                path,
                "send",
                "--chat-id",
                "1",
                "--text",
                "hello",
                "--prompt-token",
            ]
        )

    assert code == EXIT_ENV
    send.assert_not_called()
    assert "Unable to read the bot token without echo" in capsys.readouterr().err


def test_prompt_token_handles_getpass_warning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )

    with (
        patch("telegram.cli.sys.stdin") as stdin,
        patch(
            "telegram.cli.getpass.getpass",
            side_effect=getpass.GetPassWarning("no tty"),
        ),
        patch("telegram.cli.send_notify") as send,
    ):
        stdin.isatty.return_value = True
        code = main(
            [
                "--config",
                path,
                "send",
                "--chat-id",
                "1",
                "--text",
                "hello",
                "--prompt-token",
            ]
        )

    assert code == EXIT_ENV
    send.assert_not_called()


def test_send_missing_config_is_environment_error(tmp_path: Path) -> None:
    path = str(tmp_path / "missing.yaml")
    assert main(["--config", path, "send", "--chat-id", "1", "--text", "x"]) == EXIT_ENV


def test_send_body_file_reads_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unit-test-token")
    body = tmp_path / "body.txt"
    body.write_text("from-file-body", encoding="utf-8")
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )
    with patch(
        "telegram.cli.send_notify", return_value={"result": {"message_id": 3}}
    ) as send:
        code = main(
            ["--config", path, "send", "--chat-id", "1", "--body-file", str(body)]
        )
    assert code == EXIT_OK
    assert send.call_args.kwargs["text"] == "from-file-body"


def test_send_body_file_oserror_is_environment_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )
    missing = tmp_path / "no-such-body.txt"
    code = main(
        ["--config", path, "send", "--chat-id", "1", "--body-file", str(missing)]
    )
    assert code == EXIT_ENV
    assert "Could not read --body-file" in capsys.readouterr().err


def test_send_empty_text_is_environment_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )
    code = main(["--config", path, "send", "--chat-id", "1", "--text", "   "])
    assert code == EXIT_ENV
    assert "Provide --text or --body-file" in capsys.readouterr().err


def test_send_dry_run_truncates_long_preview(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _write(
        tmp_path,
        {
            "enabled": True,
            "mode": "notify",
            "allowed_chat_ids": ["99"],
            "max_message_chars": 40,
        },
    )
    long_text = "x" * 80
    code = main(
        [
            "--config",
            path,
            "send",
            "--chat-id",
            "99",
            "--text",
            long_text,
            "--dry-run",
        ]
    )
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "dry-run" in out
    assert "…[truncated]" in out


@pytest.mark.parametrize(
    ("exc", "exit_code"),
    [
        (TelegramRefused("refused", details={"chat_id": "1"}), EXIT_FAIL),
        (TelegramRuntimeError("runtime", details={"status": 500}), EXIT_FAIL),
        (TelegramError("generic"), EXIT_FAIL),
    ],
)
def test_send_maps_typed_telegram_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    exc: TelegramError,
    exit_code: int,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unit-test-token")
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )
    with patch("telegram.cli.send_notify", side_effect=exc):
        code = main(["--config", path, "send", "--chat-id", "1", "--text", "hi"])
    assert code == exit_code
    err = capsys.readouterr().err
    assert exc.message in err
    for key, value in exc.details.items():
        assert f"{key}: {value}" in err


def test_send_handles_non_mapping_api_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unit-test-token")
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )
    with patch("telegram.cli.send_notify", return_value=["not-a-dict"]):
        code = main(["--config", path, "send", "--chat-id", "1", "--text", "hi"])
    assert code == EXIT_OK
    out = capsys.readouterr().out
    assert "Sent to chat_id=1" in out
    assert "message_id=" not in out


def test_poll_missing_config_is_environment_error(tmp_path: Path) -> None:
    path = str(tmp_path / "missing.yaml")
    assert main(["--config", path, "poll", "--max-iterations", "1"]) == EXIT_ENV


def test_poll_disabled_is_noop(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = _write(tmp_path, {"enabled": False})
    assert main(["--config", path, "poll", "--max-iterations", "1"]) == EXIT_OK
    assert "telegram.enabled is false" in capsys.readouterr().out


def test_poll_prompt_token_then_poll_forever(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]},
    )
    with (
        patch("telegram.cli.sys.stdin") as stdin,
        patch("telegram.cli.getpass.getpass", return_value="prompted-token"),
        patch("telegram.cli.poll_forever") as poll,
    ):
        stdin.isatty.return_value = True
        code = main(
            [
                "--config",
                path,
                "poll",
                "--prompt-token",
                "--max-iterations",
                "2",
            ]
        )
    assert code == EXIT_OK
    poll.assert_called_once()
    assert poll.call_args.kwargs["max_iterations"] == 2


def test_poll_keyboard_interrupt_is_clean_exit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unit-test-token")
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]},
    )
    with patch("telegram.cli.poll_forever", side_effect=KeyboardInterrupt):
        code = main(["--config", path, "poll", "--max-iterations", "1"])
    assert code == EXIT_OK
    assert "Interrupted." in capsys.readouterr().out


@pytest.mark.parametrize(
    ("exc", "exit_code"),
    [
        (TelegramRefused("refused"), EXIT_FAIL),
        (TelegramRuntimeError("runtime"), EXIT_FAIL),
    ],
)
def test_poll_maps_refused_and_runtime_errors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exc: Exception,
    exit_code: int,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unit-test-token")
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]},
    )
    with patch("telegram.cli.poll_forever", side_effect=exc):
        assert main(["--config", path, "poll", "--max-iterations", "1"]) == exit_code


def test_poll_maps_config_error_from_poll_forever(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "unit-test-token")
    path = _write(
        tmp_path,
        {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]},
    )
    with patch(
        "telegram.cli.poll_forever",
        side_effect=TelegramConfigError("late config"),
    ):
        assert main(["--config", path, "poll", "--max-iterations", "1"]) == EXIT_ENV


def _run_darwin(config_path: str, tmp_home: Path, *args: str) -> int:
    with (
        patch("telegram.cli.platform.system", return_value="Darwin"),
        patch("utils.launchd_plist.Path.home", return_value=tmp_home),
        patch("utils.launchd_plist._probe_python"),
    ):
        return main(["--config", config_path, *args])


def test_poll_plist_refuses_off_darwin(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]})
    with patch("telegram.cli.platform.system", return_value="Linux"):
        assert main(["--config", cp, "poll-plist"]) == EXIT_ENV


def test_poll_plist_generates_under_darwin_mock(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "chat", "allowed_chat_ids": ["7"]})
    home = tmp_path / "home"
    assert _run_darwin(cp, home, "poll-plist", "--api-key-service", "svc-api") == EXIT_OK
    plist = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.telegram-poll.plist"
    assert plist.exists()
    out = capsys.readouterr().out
    assert "api-key Keychain service" in out
    assert "crash loop" in out


def test_poll_plist_disabled_and_notify_mode_under_darwin(tmp_path: Path) -> None:
    disabled = _write(tmp_path / "disabled", {"enabled": False})
    assert _run_darwin(disabled, tmp_path / "home-a", "poll-plist") == EXIT_OK
    notify = _write(
        tmp_path / "notify",
        {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]},
    )
    assert _run_darwin(notify, tmp_path / "home-b", "poll-plist") == EXIT_ENV


def test_poll_plist_missing_config_is_environment_error(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.yaml")
    with (
        patch("telegram.cli.platform.system", return_value="Darwin"),
        patch("utils.launchd_plist.Path.home", return_value=tmp_path / "home"),
    ):
        assert main(["--config", missing, "poll-plist"]) == EXIT_ENV


def test_health_plist_refuses_off_darwin(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["1"]})
    with patch("telegram.cli.platform.system", return_value="Linux"):
        assert main(["--config", cp, "health-plist"]) == EXIT_ENV


def test_health_plist_generates_under_darwin_mock(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["111", "222"]})
    home = tmp_path / "home"
    assert _run_darwin(cp, home, "health-plist", "--interval-sec", "60") == EXIT_OK
    plist = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.telegram-health.plist"
    assert plist.exists()


def test_health_plist_validation_branches_under_darwin(tmp_path: Path) -> None:
    disabled = _write(tmp_path / "disabled", {"enabled": False})
    assert _run_darwin(disabled, tmp_path / "h1", "health-plist") == EXIT_OK
    cp = _write(tmp_path / "ok", {"enabled": True, "allowed_chat_ids": ["111"]})
    assert (
        _run_darwin(cp, tmp_path / "h2", "health-plist", "--chat-id", "999") == EXIT_FAIL
    )
    assert (
        _run_darwin(cp, tmp_path / "h3", "health-plist", "--interval-sec", "0")
        == EXIT_FAIL
    )


def test_health_plist_missing_config_is_environment_error(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.yaml")
    with (
        patch("telegram.cli.platform.system", return_value="Darwin"),
        patch("utils.launchd_plist.Path.home", return_value=tmp_path / "home"),
    ):
        assert main(["--config", missing, "health-plist"]) == EXIT_ENV


def _run_windows(config_path: str, tmp_home: Path, *args: str) -> int:
    with (
        patch("telegram.cli.platform.system", return_value="Windows"),
        patch("utils.win_schtasks.Path.home", return_value=tmp_home),
    ):
        return main(["--config", config_path, *args])


def test_poll_task_missing_config_is_environment_error(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.yaml")
    assert _run_windows(missing, tmp_path / "home", "poll-task") == EXIT_ENV


def test_poll_task_optional_api_key_service(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]})
    home = tmp_path / "home"
    assert (
        _run_windows(cp, home, "poll-task", "--api-key-service", "cyclaw-api-key")
        == EXIT_OK
    )
    out = capsys.readouterr().out
    assert "api-key CredMan target" in out
    cmd = (home / ".CyClaw" / "tasks" / "CyClaw-telegram-poll.cmd").read_text(
        encoding="utf-8"
    )
    assert "CYCLAW_API_KEY" in cmd


def test_health_task_missing_config_is_environment_error(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.yaml")
    assert _run_windows(missing, tmp_path / "home", "health-task") == EXIT_ENV


def test_health_task_disabled_is_noop(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": False})
    assert _run_windows(cp, tmp_path / "home", "health-task") == EXIT_OK
    assert not (tmp_path / "home" / ".CyClaw" / "tasks").exists()


def test_health_task_refuses_off_windows(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["1"]})
    with patch("telegram.cli.platform.system", return_value="Linux"):
        assert main(["--config", cp, "health-task"]) == EXIT_ENV
