"""T4 media staging tests: all Telegram and subprocess work is mocked."""

from __future__ import annotations

import os
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from telegram.config import load_telegram_config
from telegram.media import MediaAttachment, attachment_from_message, save_confirmation
from telegram.runner import handle_inbound_media, poll_once
from utils.errors import TelegramRefused
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg(
    tmp_path: Path,
    *,
    media_enabled: bool = True,
    fsconnect_overrides: dict[str, object] | None = None,
) -> object:
    root = tmp_path / "staging"
    media = {
        "enabled": media_enabled,
        "fsconnect_root": str(root) if media_enabled else "",
        "max_download_bytes": 1024,
    }
    fsconnect: dict[str, object] = {
        "enabled": True,
        "writes_enabled": True,
        "strict_roots": True,
        "scan_content": True,
        "block_on_injection_flags": True,
        "writable_roots": [str(root)],
        "max_write_bytes": 1024,
        "write_rate_limit": {
            "enabled": True,
            "max_ops": 10,
            "window_seconds": 60,
            "db_path": str(tmp_path / "fsconnect_rate.db"),
        },
    }
    if fsconnect_overrides:
        fsconnect.update(fsconnect_overrides)
    raw = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl")},
        "telegram": {
            "enabled": True,
            "mode": "chat",
            "allowed_chat_ids": ["42"],
            "query": {"base_url": "http://127.0.0.1:8787"},
            "media": media,
        },
        "fsconnect": fsconnect,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return load_telegram_config(str(path))


def _attachment() -> MediaAttachment:
    return MediaAttachment(kind="document", file_id="opaque-file-id", declared_size=3)


def _success_process() -> subprocess.CompletedProcess[bytes]:
    return subprocess.CompletedProcess(
        args=["fsconnect"],
        returncode=0,
        stdout=json.dumps({"status": "applied", "executed": True}).encode("utf-8"),
        stderr=b"",
    )


def test_attachment_parser_uses_no_original_filename_and_selects_largest_photo() -> None:
    document = attachment_from_message(
        {"document": {"file_id": "doc-id", "file_size": 7, "file_name": "../../evil.py"}}
    )
    photo = attachment_from_message(
        {
            "photo": [
                {"file_id": "small", "file_size": 1},
                {"file_id": "large", "file_size": 4},
            ]
        }
    )
    assert document == MediaAttachment(kind="document", file_id="doc-id", declared_size=7)
    assert photo == MediaAttachment(kind="photo", file_id="large", declared_size=4)


@pytest.mark.parametrize("caption", [None, "/save", "/save --confirm", "/saveevil --confirm x"])
def test_save_confirmation_requires_the_closed_explicit_form(caption: object) -> None:
    assert save_confirmation(caption) is None



# take a moment and rethink the non indent more of oversight from before I luckily noticed - but tldr it should err on paranoid with fs access until
# I feel like i understand telegram a lot more on api and attack vector level. this is a test function but it raises a good q for later
#
# UPDATE: unless windows ci check fails here (which i would want it to irl rn), this has been addressed for bow but leaving the comments as a reminder to lock cyclaw tele down or its like the most hilariously scary potential prompt injection vector. if its anything like slack this shoukd a thing
#

@pytest.mark.skipif(
    os.name == "nt",
    reason="fsconnect writes hard-refused on Windows (name-based TOCTOU; see writer._writes_refused_platform / codex #593 P1)",
)
def test_confirmed_private_media_executes_the_existing_fsconnect_cli(
    tmp_path: Path,
) -> None:
    """Exercise the bridge's real local write boundary without Telegram network I/O."""
    cfg = _cfg(tmp_path)
    caption = "/save --confirm controlled local staging"
    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch("telegram.media.tg_client.download_file", return_value=b"abc"),
        patch("telegram.client.send_message", return_value={"ok": True, "result": {"message_id": 1}}),
    ):
        out = handle_inbound_media(
            cfg,
            chat_id=42,
            chat_type="private",
            attachment=_attachment(),
            caption=caption,
            update_id=991,
        )
    target = Path(cfg.media.fsconnect_root) / str(out["media"]["target"])
    assert target.read_bytes() == b"abc"
    audit_text = (tmp_path / "audit.jsonl").read_text(encoding="utf-8")
    assert caption not in audit_text



def test_confirmed_private_media_stages_with_bounded_stdin_and_no_raw_caption(
    tmp_path: Path,
) -> None:
    cfg = _cfg(tmp_path)
    caption = "/save --confirm file for local review only"
    runner_audit = MagicMock()
    media_audit = MagicMock()
    with (
        patch("telegram.runner.audit_log", runner_audit),
        patch("telegram.media.audit_log", media_audit),
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ) as get_file,
        patch("telegram.media.tg_client.download_file", return_value=b"abc") as download,
        patch("telegram.media.subprocess.run", return_value=_success_process()) as run,
        patch("telegram.client.send_message", return_value={"ok": True, "result": {"message_id": 1}}),
    ):
        out = handle_inbound_media(
            cfg,
            chat_id=42,
            chat_type="private",
            attachment=_attachment(),
            caption=caption,
            update_id=99,
        )
    assert out["answer"] == "Attachment staged in the configured local fsconnect root."
    get_file.assert_called_once_with(cfg, file_id="opaque-file-id")
    download.assert_called_once_with(cfg, file_path="documents/opaque.bin", max_bytes=1024)
    command = run.call_args.args[0]
    kwargs = run.call_args.kwargs
    assert command[1:3] == ["-m", "agentic.fsconnect.cli"]
    assert "--confirm" in command
    assert "../../evil.py" not in command
    assert caption not in command
    assert kwargs["input"] == b"abc"
    assert kwargs["timeout"] == 120
    events = [call.args[0] for call in [*runner_audit.call_args_list, *media_audit.call_args_list]]
    assert caption not in str(events)
    assert any(event.get("event") == "telegram_media_staged" for event in events)


#lol fix your spelling


@pytest.mark.parametrize(
    ("chat_id", "chat_type", "media_enabled", "caption"),
    [
        (999, "private", True, "/save --confirm x"),
        (42, "group", True, "/save --confirm x"),
        (42, "private", False, "/save --confirm x"),
        (42, "private", True, None),
    ],
)
def test_refused_media_never_resolves_download_or_starts_fsconnect(
    tmp_path: Path,
    chat_id: int,
    chat_type: str,
    media_enabled: bool,
    caption: object,
) -> None:
    cfg = _cfg(tmp_path, media_enabled=media_enabled)
    with (
        patch("telegram.media.tg_client.get_file") as get_file,
        patch("telegram.media.tg_client.download_file") as download,
        patch("telegram.media.subprocess.run") as run,
        pytest.raises(TelegramRefused),
    ):
        handle_inbound_media(
            cfg,
            chat_id=chat_id,
            chat_type=chat_type,
            attachment=_attachment(),
            caption=caption,
            update_id=5,
        )
    get_file.assert_not_called()
    download.assert_not_called()
    run.assert_not_called()


def test_media_refuses_before_download_when_fsconnect_injection_gate_is_off(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, fsconnect_overrides={"block_on_injection_flags": False})
    with (
        patch("telegram.media.tg_client.get_file") as get_file,
        patch("telegram.media.subprocess.run") as run,
        pytest.raises(TelegramRefused) as exc,
    ):
        handle_inbound_media(
            cfg,
            chat_id=42,
            chat_type="private",
            attachment=_attachment(),
            caption="/save --confirm guarded content",
            update_id=6,
        )
    assert (exc.value.details or {}).get("gate") == "fsconnect_block_on_injection_flags"
    get_file.assert_not_called()
    run.assert_not_called()


def test_media_requires_fsconnect_persistent_write_rate_limit_before_download(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path, fsconnect_overrides={"write_rate_limit": {"enabled": False}})
    with (
        patch("telegram.media.tg_client.get_file") as get_file,
        patch("telegram.media.subprocess.run") as run,
        pytest.raises(TelegramRefused) as exc,
    ):
        handle_inbound_media(
            cfg,
            chat_id=42,
            chat_type="private",
            attachment=_attachment(),
            caption="/save --confirm safe input",
            update_id=62,
        )
    assert (exc.value.details or {}).get("gate") == "fsconnect_write_rate_limit"
    get_file.assert_not_called()
    run.assert_not_called()


def test_media_refuses_writable_root_that_overlaps_a_read_root_before_download(
    tmp_path: Path,
) -> None:
    root = tmp_path / "staging"
    cfg = _cfg(tmp_path, fsconnect_overrides={"allowed_roots": [str(root)]})
    with (
        patch("telegram.media.tg_client.get_file") as get_file,
        patch("telegram.media.subprocess.run") as run,
        pytest.raises(TelegramRefused) as exc,
    ):
        handle_inbound_media(
            cfg,
            chat_id=42,
            chat_type="private",
            attachment=_attachment(),
            caption="/save --confirm safe input",
            update_id=63,
        )
    assert (exc.value.details or {}).get("gate") == "fsconnect_root_overlap"
    get_file.assert_not_called()
    run.assert_not_called()


def test_oversize_media_is_refused_before_get_file(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    oversized = MediaAttachment(kind="document", file_id="opaque", declared_size=1025)
    with (
        patch("telegram.media.tg_client.get_file") as get_file,
        patch("telegram.media.subprocess.run") as run,
        pytest.raises(TelegramRefused) as exc,
    ):
        handle_inbound_media(
            cfg,
            chat_id=42,
            chat_type="private",
            attachment=oversized,
            caption="/save --confirm too big",
            update_id=7,
        )
    assert (exc.value.details or {}).get("gate") == "max_download_bytes"
    get_file.assert_not_called()
    run.assert_not_called()


def test_poll_once_routes_a_confirmed_document_to_the_media_handler(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    updates = [
        {
            "update_id": 8,
            "message": {
                "chat": {"id": 42, "type": "private"},
                "caption": "/save --confirm controlled staging",
                "document": {"file_id": "opaque", "file_size": 3, "file_name": "ignored.md"},
            },
        }
    ]
    with (
        patch("telegram.client.get_updates", return_value=updates),
        patch("telegram.runner.handle_inbound_media", return_value={"answer": "staged"}) as handler,
    ):
        next_offset, handled = poll_once(cfg)
    assert next_offset == 9
    assert handled == [{"answer": "staged"}]
    assert handler.call_args.kwargs["attachment"].file_id == "opaque"
