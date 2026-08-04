"""CLI smoke tests for telegram.cli (no network)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from telegram.cli import EXIT_ENV, EXIT_FAIL, EXIT_OK, main
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _write(tmp_path: Path, block: dict) -> str:
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
