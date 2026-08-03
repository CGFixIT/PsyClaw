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
