"""T4 media staging via the existing fsconnect CLI, never a direct import.

Attachments are accepted only from an allowlisted private chat with an explicit
``/save --confirm <reason>`` caption. The bridge keeps no original filename or
caption in state/audits, streams a bounded Bot API download, and invokes the
existing fsconnect CLI with stdin plus its normal write gates. Automatic corpus
indexing is intentionally absent.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram import client as tg_client
from telegram.config import MAX_TELEGRAM_CLOUD_DOWNLOAD_BYTES, TelegramConfig
from utils.errors import TelegramRefused, TelegramRuntimeError
from utils.logger import _get_config, audit_log, hash_query

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FSCONNECT_TIMEOUT_SEC = 120
_SAVE_CONFIRM_PREFIX = "/save"


@dataclass(frozen=True)
class MediaAttachment:
    """Minimal attachment metadata needed for a bounded download."""

    kind: str
    file_id: str
    declared_size: int | None


def save_confirmation(caption: object) -> str | None:
    """Return a non-empty explicit save reason, without retaining it elsewhere."""
    if not isinstance(caption, str):
        return None
    parts = caption.strip().split(maxsplit=2)
    if len(parts) != 3:
        return None
    command, confirm_flag, reason = parts
    if not command.lower().startswith(_SAVE_CONFIRM_PREFIX):
        return None
    if command.lower() != _SAVE_CONFIRM_PREFIX and not command.lower().startswith(
        f"{_SAVE_CONFIRM_PREFIX}@"
    ):
        return None
    if confirm_flag != "--confirm" or not reason.strip():
        return None
    return reason.strip()


def attachment_from_message(message: dict[str, Any]) -> MediaAttachment | None:
    """Select one document or highest-size photo without trusting filenames."""
    document = message.get("document")
    if isinstance(document, dict):
        file_id = document.get("file_id")
        if isinstance(file_id, str) and file_id.strip():
            return MediaAttachment(
                kind="document",
                file_id=file_id,
                declared_size=_optional_file_size(document.get("file_size")),
            )

    photos = message.get("photo")
    if not isinstance(photos, list):
        return None
    candidates: list[tuple[int, int, str, int | None]] = []
    for index, photo in enumerate(photos):
        if not isinstance(photo, dict):
            continue
        file_id = photo.get("file_id")
        if not isinstance(file_id, str) or not file_id.strip():
            continue
        file_size = _optional_file_size(photo.get("file_size"))
        candidates.append((file_size if file_size is not None else -1, index, file_id, file_size))
    if not candidates:
        return None
    _, _, file_id, file_size = max(candidates)
    return MediaAttachment(kind="photo", file_id=file_id, declared_size=file_size)


def _optional_file_size(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _configured_writable_roots(fsconnect: dict[str, Any]) -> set[str]:
    roots = fsconnect.get("writable_roots")
    if not isinstance(roots, list):
        return set()
    configured: set[str] = set()
    for entry in roots:
        if isinstance(entry, str) and entry.strip():
            configured.add(entry.strip())
        elif isinstance(entry, dict):
            path = entry.get("path")
            if isinstance(path, str) and path.strip():
                configured.add(path.strip())
    return configured


def _configured_read_roots(fsconnect: dict[str, Any]) -> set[str]:
    # An omitted read scope is the fsconnect default: no read roots. Treat it
    # as empty rather than confusing an otherwise valid write-only deployment
    # with an invalid configuration shape.
    roots = fsconnect.get("allowed_roots", [])
    if not isinstance(roots, list):
        raise TelegramRefused(
            "fsconnect.allowed_roots must be a list for Telegram media staging",
            details={"gate": "fsconnect_allowed_roots"},
        )
    configured: set[str] = set()
    for entry in roots:
        if not isinstance(entry, str) or not entry.strip():
            raise TelegramRefused(
                "fsconnect.allowed_roots contains an invalid entry",
                details={"gate": "fsconnect_allowed_roots"},
            )
        configured.add(entry.strip())
    return configured


def _resolved_root(root: str) -> Path:
    try:
        return Path(os.path.expanduser(os.path.expandvars(root))).resolve(strict=False)
    except (OSError, RuntimeError):
        raise TelegramRefused(
            "Telegram media staging root cannot be resolved safely",
            details={"gate": "fsconnect_root"},
        ) from None


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left in right.parents or right in left.parents


def _media_cap(cfg: TelegramConfig) -> int:
    """Validate final fsconnect gates before touching Telegram's file service."""
    try:
        root_cfg = _get_config(cfg._config_path)
    except (OSError, UnicodeError):
        raise TelegramRuntimeError(
            "fsconnect configuration is unavailable for Telegram media staging",
            details={"gate": "fsconnect_config", "retryable": False},
        ) from None
    fsconnect = root_cfg.get("fsconnect") if isinstance(root_cfg, dict) else None
    if not isinstance(fsconnect, dict):
        raise TelegramRefused(
            "Telegram media staging requires an enabled fsconnect configuration",
            details={"gate": "fsconnect_config"},
        )
    for key in (
        "enabled",
        "writes_enabled",
        "strict_roots",
        "scan_content",
        "block_on_injection_flags",
    ):
        if fsconnect.get(key) is not True:
            raise TelegramRefused(
                "Telegram media staging requires all fsconnect safety gates",
                details={"gate": f"fsconnect_{key}"},
            )
    if cfg.media.fsconnect_root not in _configured_writable_roots(fsconnect):
        raise TelegramRefused(
            "telegram.media.fsconnect_root is not an explicit fsconnect writable root",
            details={"gate": "fsconnect_root"},
        )
    media_root = _resolved_root(cfg.media.fsconnect_root)
    repo_root = _REPO_ROOT.resolve()
    corpus_root = (repo_root / "data" / "corpus").resolve()
    if _paths_overlap(media_root, repo_root) or _paths_overlap(media_root, corpus_root):
        raise TelegramRefused(
            "Telegram media staging root must be outside the CyClaw repository and corpus",
            details={"gate": "fsconnect_root_scope"},
        )
    for read_root in _configured_read_roots(fsconnect):
        if _paths_overlap(media_root, _resolved_root(read_root)):
            raise TelegramRefused(
                "Telegram media staging root must not overlap an fsconnect read root",
                details={"gate": "fsconnect_root_overlap"},
            )
    write_rate_limit = fsconnect.get("write_rate_limit")
    if not isinstance(write_rate_limit, dict) or write_rate_limit.get("enabled") is not True:
        raise TelegramRefused(
            "Telegram media staging requires fsconnect.write_rate_limit.enabled",
            details={"gate": "fsconnect_write_rate_limit"},
        )
    max_write_bytes = fsconnect.get("max_write_bytes")
    if isinstance(max_write_bytes, bool) or not isinstance(max_write_bytes, int) or max_write_bytes <= 0:
        raise TelegramRefused(
            "fsconnect.max_write_bytes must be a positive integer for Telegram media staging",
            details={"gate": "fsconnect_max_write_bytes"},
        )
    return min(cfg.media.max_download_bytes, max_write_bytes, MAX_TELEGRAM_CLOUD_DOWNLOAD_BYTES)


def _audit_refusal(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    update_id: int,
    gate: str,
    kind: str,
) -> None:
    audit_log(
        {
            "event": "telegram_media_refused",
            "channel": "telegram",
            "chat_id": str(chat_id),
            "update_id": update_id,
            "kind": kind,
            "gate": gate,
        },
        config_path=cfg._config_path,
    )


def _safe_target(update_id: int, attachment: MediaAttachment) -> str:
    digest = hashlib.sha256(attachment.file_id.encode("utf-8")).hexdigest()[:20]
    extension = ".jpg" if attachment.kind == "photo" else ".bin"
    return f"telegram_{update_id}_{digest}{extension}"


def _run_fsconnect_write(
    cfg: TelegramConfig,
    *,
    target: str,
    data: bytes,
    confirmation_hash: str,
) -> None:
    command = [
        sys.executable,
        "-m",
        "agentic.fsconnect.cli",
        "--config",
        str(Path(cfg._config_path).resolve()),
        "write",
        "--root",
        cfg.media.fsconnect_root,
        "--path",
        target,
        "--reason",
        f"telegram_media_confirm:{confirmation_hash[:16]}",
        "--confirm",
    ]
    try:
        completed = subprocess.run(  # noqa: S603 -- fixed executable and argv list; no shell
            command,
            cwd=str(_REPO_ROOT),
            input=data,
            capture_output=True,
            timeout=_FSCONNECT_TIMEOUT_SEC,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise TelegramRuntimeError(
            "fsconnect media staging timed out",
            details={"method": "fsconnect write", "retryable": False},
        ) from None
    except OSError:
        raise TelegramRuntimeError(
            "fsconnect media staging could not start",
            details={"method": "fsconnect write", "retryable": False},
        ) from None
    if completed.returncode == 4:
        raise TelegramRefused(
            "fsconnect refused Telegram media staging",
            details={"gate": "fsconnect_write"},
        )
    if completed.returncode != 0:
        raise TelegramRuntimeError(
            "fsconnect media staging failed",
            details={"method": "fsconnect write", "retryable": False},
        )
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict) or payload.get("status") != "applied" or payload.get("executed") is not True:
        raise TelegramRuntimeError(
            "fsconnect media staging did not confirm an applied write",
            details={"method": "fsconnect write", "retryable": False},
        )


def stage_attachment(
    cfg: TelegramConfig,
    *,
    chat_id: int | str,
    chat_type: str,
    update_id: int,
    attachment: MediaAttachment,
    confirmation: str,
) -> dict[str, object]:
    """Stage one explicitly confirmed attachment through fsconnect's CLI gate."""
    if not cfg.enabled:
        raise TelegramRefused("telegram.enabled is false", details={"gate": "enabled"})
    if cfg.mode != "chat":
        raise TelegramRefused("inbound chat requires telegram.mode: chat", details={"gate": "mode"})
    if not cfg.is_chat_allowed(chat_id):
        _audit_refusal(
            cfg,
            chat_id=chat_id,
            update_id=update_id,
            gate="allowlist",
            kind=attachment.kind,
        )
        raise TelegramRefused(
            "chat_id not in telegram.allowed_chat_ids",
            details={"chat_id": str(chat_id), "gate": "allowlist"},
        )
    if chat_type != "private":
        _audit_refusal(
            cfg,
            chat_id=chat_id,
            update_id=update_id,
            gate="private_chat",
            kind=attachment.kind,
        )
        raise TelegramRefused(
            "Telegram media staging is limited to private chats",
            details={"gate": "private_chat"},
        )
    if not cfg.media.enabled:
        _audit_refusal(
            cfg,
            chat_id=chat_id,
            update_id=update_id,
            gate="media_enabled",
            kind=attachment.kind,
        )
        raise TelegramRefused(
            "telegram.media.enabled is false",
            details={"gate": "media_enabled"},
        )
    if isinstance(update_id, bool) or not isinstance(update_id, int) or update_id < 0:
        raise TelegramRefused("Telegram update id is invalid", details={"gate": "update_id"})
    if not isinstance(confirmation, str) or not confirmation.strip():
        _audit_refusal(
            cfg,
            chat_id=chat_id,
            update_id=update_id,
            gate="media_confirm",
            kind=attachment.kind,
        )
        raise TelegramRefused(
            "media staging requires an explicit confirmation caption",
            details={"gate": "media_confirm"},
        )

    confirmation_hash = hash_query(confirmation)
    try:
        cap = _media_cap(cfg)
    except TelegramRefused as exc:
        _audit_refusal(
            cfg,
            chat_id=chat_id,
            update_id=update_id,
            gate=(exc.details or {}).get("gate", "fsconnect_config"),
            kind=attachment.kind,
        )
        raise
    if attachment.declared_size is not None and attachment.declared_size > cap:
        _audit_refusal(
            cfg,
            chat_id=chat_id,
            update_id=update_id,
            gate="max_download_bytes",
            kind=attachment.kind,
        )
        raise TelegramRefused(
            "Telegram attachment exceeds the configured download cap",
            details={"gate": "max_download_bytes"},
        )

    file_id_hash = hash_query(attachment.file_id)
    audit_log(
        {
            "event": "telegram_media_stage_requested",
            "channel": "telegram",
            "chat_id": str(chat_id),
            "update_id": update_id,
            "kind": attachment.kind,
            "declared_size": attachment.declared_size,
            "max_bytes": cap,
            "file_id_hash": file_id_hash,
            "confirmation_hash": confirmation_hash,
            "confirmation_len": len(confirmation),
        },
        config_path=cfg._config_path,
    )
    descriptor = tg_client.get_file(cfg, file_id=attachment.file_id)
    resolved_size = _optional_file_size(descriptor.get("file_size"))
    if resolved_size is not None and resolved_size > cap:
        _audit_refusal(
            cfg,
            chat_id=chat_id,
            update_id=update_id,
            gate="max_download_bytes",
            kind=attachment.kind,
        )
        raise TelegramRefused(
            "Telegram attachment exceeds the configured download cap",
            details={"gate": "max_download_bytes"},
        )
    file_path = descriptor.get("file_path")
    if not isinstance(file_path, str):
        raise TelegramRuntimeError(
            "Telegram file resolution returned no download path",
            details={"method": "getFile", "retryable": True},
        )
    data = tg_client.download_file(cfg, file_path=file_path, max_bytes=cap)
    if len(data) > cap:
        raise TelegramRefused(
            "Telegram attachment exceeds the configured download cap",
            details={"gate": "max_download_bytes"},
        )
    target = _safe_target(update_id, attachment)
    _run_fsconnect_write(
        cfg,
        target=target,
        data=data,
        confirmation_hash=confirmation_hash,
    )
    data_hash = hashlib.sha256(data).hexdigest()
    audit_log(
        {
            "event": "telegram_media_staged",
            "channel": "telegram",
            "chat_id": str(chat_id),
            "update_id": update_id,
            "kind": attachment.kind,
            "target": target,
            "bytes": len(data),
            "sha256": data_hash,
            "file_id_hash": file_id_hash,
            "confirmation_hash": confirmation_hash,
        },
        config_path=cfg._config_path,
    )
    return {"target": target, "bytes": len(data), "sha256": data_hash, "kind": attachment.kind}


__all__ = ["MediaAttachment", "attachment_from_message", "save_confirmation", "stage_attachment"]
