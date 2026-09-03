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
from telegram.media import (
    MediaAttachment,
    attachment_from_message,
    save_confirmation,
    stage_attachment,
)
from telegram.runner import handle_inbound_media, poll_once
from utils.errors import TelegramRefused, TelegramRuntimeError
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
    telegram_overrides: dict[str, object] | None = None,
    omit_fsconnect: bool = False,
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
    telegram: dict[str, object] = {
        "enabled": True,
        "mode": "chat",
        "allowed_chat_ids": ["42"],
        "query": {"base_url": "http://127.0.0.1:8787"},
        "media": media,
    }
    if telegram_overrides:
        telegram.update(telegram_overrides)
    raw: dict[str, object] = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl")},
        "telegram": telegram,
    }
    if not omit_fsconnect:
        raw["fsconnect"] = fsconnect
    path = tmp_path / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
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


def test_download_refusal_after_stage_requested_emits_terminal_audit(tmp_path: Path) -> None:
    """Issue #793: stream oversize after stage_requested must log telegram_media_refused."""
    cfg = _cfg(tmp_path)
    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 10},
        ),
        patch(
            "telegram.media.tg_client.download_file",
            side_effect=TelegramRefused(
                "Telegram file exceeds the configured download cap",
                details={"gate": "max_download_bytes"},
            ),
        ) as download,
        patch("telegram.media.subprocess.run") as run,
        patch("telegram.media.audit_log") as audit,
        pytest.raises(TelegramRefused) as exc,
    ):
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=77,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    download.assert_called_once()
    run.assert_not_called()
    assert (exc.value.details or {}).get("gate") == "max_download_bytes"
    events = [call.args[0] for call in audit.call_args_list]
    assert any(event.get("event") == "telegram_media_stage_requested" for event in events)
    assert any(
        event.get("event") == "telegram_media_refused"
        and event.get("gate") == "max_download_bytes"
        and event.get("update_id") == 77
        for event in events
    )
    assert not any(event.get("event") == "telegram_media_staged" for event in events)


def test_fsconnect_refuse_after_download_emits_terminal_audit(tmp_path: Path) -> None:
    """Issue #793: post-download fsconnect gate refusal also needs a terminal audit."""
    cfg = _cfg(tmp_path)
    refused = MagicMock(returncode=4, stdout=b"", stderr=b"refused")
    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch("telegram.media.tg_client.download_file", return_value=b"abc"),
        patch("telegram.media.subprocess.run", return_value=refused),
        patch("telegram.media.audit_log") as audit,
        pytest.raises(TelegramRefused) as exc,
    ):
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=78,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc.value.details or {}).get("gate") == "fsconnect_write"
    events = [call.args[0] for call in audit.call_args_list]
    assert any(event.get("event") == "telegram_media_stage_requested" for event in events)
    assert any(
        event.get("event") == "telegram_media_refused"
        and event.get("gate") == "fsconnect_write"
        for event in events
    )


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


@pytest.mark.parametrize(
    ("caption", "expected"),
    [
        ("foo --confirm reason", None),
        ("/save --please reason", None),
        ("/save@CyClawBot --confirm operator reason", "operator reason"),
        ("/SAVE --confirm Keep It", "Keep It"),
    ],
)
def test_save_confirmation_closed_form_edges(caption: str, expected: str | None) -> None:
    assert save_confirmation(caption) == expected


def test_attachment_from_message_skips_invalid_photo_entries() -> None:
    assert attachment_from_message({"photo": "not-a-list"}) is None
    assert attachment_from_message({"photo": ["x", {"file_id": ""}, {"file_id": 12}]}) is None
    assert attachment_from_message({"photo": [{"file_size": True}]}) is None
    photo = attachment_from_message(
        {
            "photo": [
                {"file_id": "keep", "file_size": -1},
                {"file_id": "bigger", "file_size": False},
            ]
        }
    )
    assert photo == MediaAttachment(kind="photo", file_id="bigger", declared_size=None)


def test_media_cap_accepts_dict_writable_roots_and_refuses_bad_shapes(tmp_path: Path) -> None:
    root = tmp_path / "staging"
    cfg = _cfg(
        tmp_path,
        fsconnect_overrides={"writable_roots": [{"path": str(root)}, "  ", {"path": ""}]},
    )
    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch("telegram.media.tg_client.download_file", return_value=b"abc"),
        patch("telegram.media.subprocess.run", return_value=_success_process()),
    ):
        out = stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=11,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert out["bytes"] == 3

    cfg_bad_writable = _cfg(tmp_path / "w", fsconnect_overrides={"writable_roots": "nope"})
    with pytest.raises(TelegramRefused) as exc_w:
        stage_attachment(
            cfg_bad_writable,
            chat_id=42,
            chat_type="private",
            update_id=12,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_w.value.details or {}).get("gate") == "fsconnect_root"

    cfg_bad_allowed = _cfg(tmp_path / "a", fsconnect_overrides={"allowed_roots": "nope"})
    with pytest.raises(TelegramRefused) as exc_a:
        stage_attachment(
            cfg_bad_allowed,
            chat_id=42,
            chat_type="private",
            update_id=13,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_a.value.details or {}).get("gate") == "fsconnect_allowed_roots"

    cfg_bad_entry = _cfg(tmp_path / "e", fsconnect_overrides={"allowed_roots": [""]})
    with pytest.raises(TelegramRefused) as exc_e:
        stage_attachment(
            cfg_bad_entry,
            chat_id=42,
            chat_type="private",
            update_id=14,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_e.value.details or {}).get("gate") == "fsconnect_allowed_roots"


def test_media_cap_refuses_unresolvable_root_and_missing_fsconnect(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with (
        patch(
            "telegram.media.os.path.expanduser",
            side_effect=RuntimeError("bad home"),
        ),
        pytest.raises(TelegramRefused) as exc_resolve,
    ):
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=15,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_resolve.value.details or {}).get("gate") == "fsconnect_root"

    with (
        patch("telegram.media._get_config", side_effect=OSError("gone")),
        pytest.raises(TelegramRuntimeError) as exc_cfg,
    ):
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=16,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_cfg.value.details or {}).get("gate") == "fsconnect_config"

    cfg_missing = _cfg(tmp_path / "nofsc", omit_fsconnect=True)
    with pytest.raises(TelegramRefused) as exc_missing:
        stage_attachment(
            cfg_missing,
            chat_id=42,
            chat_type="private",
            update_id=17,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_missing.value.details or {}).get("gate") == "fsconnect_config"


def test_media_cap_refuses_root_not_writable_repo_overlap_and_bad_max_bytes(
    tmp_path: Path,
) -> None:
    other = tmp_path / "other"
    other.mkdir()
    cfg_not_listed = _cfg(
        tmp_path,
        fsconnect_overrides={"writable_roots": [str(other)]},
    )
    with pytest.raises(TelegramRefused) as exc_listed:
        stage_attachment(
            cfg_not_listed,
            chat_id=42,
            chat_type="private",
            update_id=18,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_listed.value.details or {}).get("gate") == "fsconnect_root"

    import telegram.media as media_mod

    repo_root = media_mod._REPO_ROOT.resolve()
    cfg_overlap = _cfg(
        tmp_path / "overlap",
        fsconnect_overrides={"writable_roots": [str(repo_root)]},
        telegram_overrides={
            "media": {
                "enabled": True,
                "fsconnect_root": str(repo_root),
                "max_download_bytes": 1024,
            }
        },
    )
    with pytest.raises(TelegramRefused) as exc_overlap:
        stage_attachment(
            cfg_overlap,
            chat_id=42,
            chat_type="private",
            update_id=19,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_overlap.value.details or {}).get("gate") == "fsconnect_root_scope"

    cfg_bytes = _cfg(tmp_path / "bytes", fsconnect_overrides={"max_write_bytes": True})
    with pytest.raises(TelegramRefused) as exc_bytes:
        stage_attachment(
            cfg_bytes,
            chat_id=42,
            chat_type="private",
            update_id=20,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_bytes.value.details or {}).get("gate") == "fsconnect_max_write_bytes"


def test_fsconnect_write_timeout_oserror_and_bad_payloads(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    common = {
        "chat_id": 42,
        "chat_type": "private",
        "attachment": _attachment(),
        "confirmation": "operator confirmed",
    }
    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch("telegram.media.tg_client.download_file", return_value=b"abc"),
        patch(
            "telegram.media.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="fsconnect", timeout=1),
        ),
        pytest.raises(TelegramRuntimeError) as exc_timeout,
    ):
        stage_attachment(cfg, update_id=21, **common)
    assert "timed out" in exc_timeout.value.message

    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch("telegram.media.tg_client.download_file", return_value=b"abc"),
        patch("telegram.media.subprocess.run", side_effect=OSError("noexec")),
        pytest.raises(TelegramRuntimeError) as exc_os,
    ):
        stage_attachment(cfg, update_id=22, **common)
    assert "could not start" in exc_os.value.message

    failed = MagicMock(returncode=1, stdout=b"", stderr=b"fail")
    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch("telegram.media.tg_client.download_file", return_value=b"abc"),
        patch("telegram.media.subprocess.run", return_value=failed),
        pytest.raises(TelegramRuntimeError) as exc_rc,
    ):
        stage_attachment(cfg, update_id=23, **common)
    assert "failed" in exc_rc.value.message

    bad_json = MagicMock(returncode=0, stdout=b"not-json", stderr=b"")
    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch("telegram.media.tg_client.download_file", return_value=b"abc"),
        patch("telegram.media.subprocess.run", return_value=bad_json),
        pytest.raises(TelegramRuntimeError) as exc_json,
    ):
        stage_attachment(cfg, update_id=24, **common)
    assert "did not confirm" in exc_json.value.message

    not_applied = MagicMock(
        returncode=0,
        stdout=json.dumps({"status": "ok", "executed": False}).encode("utf-8"),
        stderr=b"",
    )
    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch("telegram.media.tg_client.download_file", return_value=b"abc"),
        patch("telegram.media.subprocess.run", return_value=not_applied),
        pytest.raises(TelegramRuntimeError) as exc_applied,
    ):
        stage_attachment(cfg, update_id=25, **common)
    assert "did not confirm" in exc_applied.value.message


def test_stage_attachment_pre_download_gates(tmp_path: Path) -> None:
    disabled = _cfg(tmp_path / "off", telegram_overrides={"enabled": False})
    with pytest.raises(TelegramRefused) as exc_en:
        stage_attachment(
            disabled,
            chat_id=42,
            chat_type="private",
            update_id=30,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_en.value.details or {}).get("gate") == "enabled"

    notify = _cfg(tmp_path / "notify", telegram_overrides={"mode": "notify"})
    with pytest.raises(TelegramRefused) as exc_mode:
        stage_attachment(
            notify,
            chat_id=42,
            chat_type="private",
            update_id=31,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_mode.value.details or {}).get("gate") == "mode"

    cfg = _cfg(tmp_path)
    with (
        patch("telegram.media.audit_log") as audit,
        pytest.raises(TelegramRefused) as exc_allow,
    ):
        stage_attachment(
            cfg,
            chat_id=999,
            chat_type="private",
            update_id=32,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_allow.value.details or {}).get("gate") == "allowlist"
    assert any(
        e.args[0].get("event") == "telegram_media_refused" and e.args[0].get("gate") == "allowlist"
        for e in audit.call_args_list
    )

    with pytest.raises(TelegramRefused) as exc_uid:
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=-1,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_uid.value.details or {}).get("gate") == "update_id"

    with (
        patch("telegram.media.audit_log") as audit_confirm,
        pytest.raises(TelegramRefused) as exc_confirm,
    ):
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=33,
            attachment=_attachment(),
            confirmation="   ",
        )
    assert (exc_confirm.value.details or {}).get("gate") == "media_confirm"
    assert any(
        e.args[0].get("event") == "telegram_media_refused"
        and e.args[0].get("gate") == "media_confirm"
        for e in audit_confirm.call_args_list
    )


def test_stage_attachment_post_resolve_and_download_refusals(tmp_path: Path) -> None:
    cfg = _cfg(tmp_path)
    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 2048},
        ),
        patch("telegram.media.tg_client.download_file") as download,
        patch("telegram.media.subprocess.run") as run,
        patch("telegram.media.audit_log") as audit,
        pytest.raises(TelegramRefused) as exc_size,
    ):
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=40,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_size.value.details or {}).get("gate") == "max_download_bytes"
    download.assert_not_called()
    run.assert_not_called()
    assert any(
        e.args[0].get("event") == "telegram_media_refused"
        and e.args[0].get("gate") == "max_download_bytes"
        for e in audit.call_args_list
    )

    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_size": 3},
        ),
        pytest.raises(TelegramRuntimeError) as exc_path,
    ):
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=41,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_path.value.details or {}).get("method") == "getFile"

    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch("telegram.media.tg_client.download_file", return_value=b"x" * 2048),
        patch("telegram.media.subprocess.run") as run2,
        patch("telegram.media.audit_log") as audit2,
        pytest.raises(TelegramRefused) as exc_len,
    ):
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=42,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert (exc_len.value.details or {}).get("gate") == "max_download_bytes"
    run2.assert_not_called()
    assert any(
        e.args[0].get("event") == "telegram_media_refused"
        and e.args[0].get("gate") == "max_download_bytes"
        for e in audit2.call_args_list
    )

    with (
        patch(
            "telegram.media.tg_client.get_file",
            return_value={"file_path": "documents/opaque.bin", "file_size": 3},
        ),
        patch(
            "telegram.media.tg_client.download_file",
            side_effect=TelegramRefused("denied", details={"gate": "   "}),
        ),
        patch("telegram.media.audit_log") as audit3,
        pytest.raises(TelegramRefused),
    ):
        stage_attachment(
            cfg,
            chat_id=42,
            chat_type="private",
            update_id=43,
            attachment=_attachment(),
            confirmation="operator confirmed",
        )
    assert any(
        e.args[0].get("event") == "telegram_media_refused"
        and e.args[0].get("gate") == "media_download"
        for e in audit3.call_args_list
    )
