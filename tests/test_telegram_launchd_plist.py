"""Tests for `python -m telegram.cli poll-plist` / `health-plist` -- the
generated (never auto-loaded) launchd plists that inject TELEGRAM_BOT_TOKEN
via the macOS Keychain wrapper instead of writing it into the plist.

No real ~/Library/LaunchAgents or Keychain is ever touched: Path.home() (via
utils.launchd_plist) is monkeypatched to a tmp_path in every test that writes
a plist. Nothing here shells out to `security` -- these tests only verify the
generated plist's structure, not the wrapper script's own Keychain lookup
(covered separately by manual verification of macos/cyclaw-keychain-env.sh).
"""

from __future__ import annotations

import os
import plistlib
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from telegram.cli import EXIT_ENV, EXIT_FAIL, EXIT_OK, main

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX fixtures")


def _write(tmp_path: Path, block: dict) -> str:
    raw = {"logging": {"audit_file": str(tmp_path / "audit.jsonl")}, "telegram": block}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return str(path)


def _run(config_path: str, tmp_home: Path, *args: str) -> int:
    with (
        patch("telegram.cli.platform.system", return_value="Darwin"),
        patch("utils.launchd_plist.Path.home", return_value=tmp_home),
    ):
        return main(["--config", config_path, *args])


# ---------------------------------------------------------------------------
# poll-plist
# ---------------------------------------------------------------------------


def test_poll_plist_non_darwin_refuses(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]})
    with patch("telegram.cli.platform.system", return_value="Linux"):
        assert main(["--config", cp, "poll-plist"]) == EXIT_ENV


def test_poll_plist_disabled_is_noop(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": False})
    assert _run(cp, tmp_path / "home", "poll-plist") == EXIT_OK
    assert not (tmp_path / "home" / "Library" / "LaunchAgents").exists()


def test_poll_plist_requires_chat_mode(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "notify", "allowed_chat_ids": ["1"]})
    assert _run(cp, tmp_path / "home", "poll-plist") == EXIT_ENV


def test_poll_plist_generates_valid_plist(tmp_path: Path, capsys) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "chat", "allowed_chat_ids": ["12345"]})
    home = tmp_path / "home"

    assert _run(cp, home, "poll-plist") == EXIT_OK

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.telegram-poll.plist"
    assert plist_path.exists()
    document = plistlib.loads(plist_path.read_bytes())

    assert document["Label"] == "com.cgfixit.cyclaw.telegram-poll"
    assert document["KeepAlive"] is True
    assert document["ThrottleInterval"] == 10
    assert document["RunAtLoad"] is False
    assert "EnvironmentVariables" not in document

    args = document["ProgramArguments"]
    assert args[0].endswith("cyclaw-keychain-env.sh")
    assert args[1] == "com.cgfixit.cyclaw.telegram-bot-token"
    assert args[2] == "TELEGRAM_BOT_TOKEN"
    assert args[3] == "--"
    assert "-m" in args and "telegram.cli" in args
    assert args[-1] == "poll"

    raw = plist_path.read_bytes()
    assert b"REPLACE_" not in raw
    assert b"EnvironmentVariables" not in raw  # the only place a literal secret value could hide

    out = capsys.readouterr().out
    assert "launchctl bootstrap gui/" in out
    assert "cyclaw-keychain-set.sh" in out
    # KeepAlive + ThrottleInterval=10 means loading before the token is
    # stored crash-loops every 10s -- the operator has to be told, not just
    # shown the (easy to miss) storage command.
    assert "crash loop" in out
    assert "Store the token FIRST" in out


def test_poll_plist_optional_api_key_service_chains_second_wrapper(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]})
    home = tmp_path / "home"

    assert (
        _run(cp, home, "poll-plist", "--api-key-service", "com.cgfixit.cyclaw.api-key")
        == EXIT_OK
    )

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.telegram-poll.plist"
    document = plistlib.loads(plist_path.read_bytes())
    args = document["ProgramArguments"]

    # Two chained wrapper layers: [wrapper, svc1, VAR1, --, wrapper, svc2, VAR2, --, python, ...]
    assert args[0].endswith("cyclaw-keychain-env.sh")
    assert args[1] == "com.cgfixit.cyclaw.telegram-bot-token"
    assert args[2] == "TELEGRAM_BOT_TOKEN"
    assert args[3] == "--"
    assert args[4].endswith("cyclaw-keychain-env.sh")
    assert args[5] == "com.cgfixit.cyclaw.api-key"
    assert args[6] == "CYCLAW_API_KEY"
    assert args[7] == "--"
    assert "poll" in args


def test_poll_plist_idempotent_overwrite(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "mode": "chat", "allowed_chat_ids": ["1"]})
    home = tmp_path / "home"

    _run(cp, home, "poll-plist", "--token-service", "svc-a")
    _run(cp, home, "poll-plist", "--token-service", "svc-b")

    agents_dir = home / "Library" / "LaunchAgents"
    matches = list(agents_dir.glob("com.cgfixit.cyclaw.telegram-poll*"))
    assert len(matches) == 1
    document = plistlib.loads(matches[0].read_bytes())
    assert document["ProgramArguments"][1] == "svc-b"


# ---------------------------------------------------------------------------
# health-plist
# ---------------------------------------------------------------------------


def test_health_plist_non_darwin_refuses(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["1"]})
    with patch("telegram.cli.platform.system", return_value="Linux"):
        assert main(["--config", cp, "health-plist"]) == EXIT_ENV


def test_health_plist_disabled_is_noop(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": False})
    assert _run(cp, tmp_path / "home", "health-plist") == EXIT_OK


def test_health_plist_defaults_to_first_allowed_chat_id(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["111", "222"]})
    home = tmp_path / "home"

    assert _run(cp, home, "health-plist") == EXIT_OK

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.telegram-health.plist"
    document = plistlib.loads(plist_path.read_bytes())
    args = document["ProgramArguments"]
    # args: [wrapper, svc, VAR, --, /bin/bash, -lc, <script>]
    script = args[-1]
    assert "--chat-id 111" in script
    assert document["StartInterval"] == 300
    assert "KeepAlive" not in document
    assert document["RunAtLoad"] is False


def test_health_plist_rejects_chat_id_not_allowlisted(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["111"]})
    assert _run(cp, tmp_path / "home", "health-plist", "--chat-id", "999") == EXIT_FAIL


def test_health_plist_rejects_nonpositive_interval(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["111"]})
    assert _run(cp, tmp_path / "home", "health-plist", "--interval-sec", "0") == EXIT_FAIL


def test_health_plist_script_is_shell_safe_for_paths_with_spaces(tmp_path: Path) -> None:
    weird_dir = tmp_path / "has space"
    weird_dir.mkdir()
    cp = _write(weird_dir, {"enabled": True, "allowed_chat_ids": ["111"]})
    home = tmp_path / "home"

    assert _run(cp, home, "health-plist") == EXIT_OK

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.telegram-health.plist"
    document = plistlib.loads(plist_path.read_bytes())
    script = document["ProgramArguments"][-1]
    # The config path is quoted as a single token despite containing a space.
    assert "has space" in script
    assert "--config " + shlex.quote(cp) in script


def test_health_plist_generated_plist_never_contains_secret_markers(tmp_path: Path) -> None:
    cp = _write(tmp_path, {"enabled": True, "allowed_chat_ids": ["111"]})
    home = tmp_path / "home"
    _run(cp, home, "health-plist")

    plist_path = home / "Library" / "LaunchAgents" / "com.cgfixit.cyclaw.telegram-health.plist"
    raw = plist_path.read_bytes()
    assert b"REPLACE_" not in raw
    assert b"EnvironmentVariables" not in raw
