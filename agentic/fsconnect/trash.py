"""Trash-first governed-deletion helpers for the filesystem connector (Phase 2).

This module is pure policy/formatting glue on top of the ``pathsafe`` mechanism: it
computes collision-proof, time-sortable trash entry names, serializes/parses the
``.meta.json`` sidecars, and lists/expires entries. It performs NO privileged syscall
itself -- every filesystem touch goes through :class:`agentic.fsconnect.pathsafe.ScopedRoots`
so trash operations inherit the exact same root containment as ordinary writes.

Trash lives *inside* each writable root (``<root>/.cyclaw-trash/``) so it is a same-fs
atomic ``os.replace`` away (no cross-device copy, no partial state), counts against the
root's quota, and never crosses a filesystem boundary. Reserved-name enforcement (a
caller may not ``fs_write`` into ``.cyclaw-trash`` and forge deletion history) lives in
``writer.py`` (policy), not here and not in ``pathsafe`` (mechanism).

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from agentic.fsconnect.pathsafe import ScopedRoots, split_components
from utils.errors import FsConnectRuntimeError

logger = logging.getLogger(__name__)

TRASH_DIR = ".cyclaw-trash"
META_SUFFIX = ".meta.json"
_MAX_META_BYTES = 64 * 1024  # a sidecar is tiny; cap the read defensively


def _stamp(now: datetime) -> str:
    """Sortable UTC stamp for a trash entry name, e.g. ``20260709T145501Z``."""
    return now.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def iso(now: datetime) -> str:
    """ISO-8601 UTC string with a trailing ``Z`` (audit/meta timestamp format)."""
    return now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def entry_name(original: str, now: datetime) -> str:
    """Collision-proof, time-sortable trash entry name.

    ``<UTCstamp>__<orig path, '/'->'__'>__<8-hex of sha256(orig_path + stamp)><8-hex random>``.
    The digest binds the name to the exact original path + stamp; the random suffix
    disambiguates two deletes of the same path in the same second (the digest alone
    cannot -- both its inputs are identical in that case).
    """
    stamp = _stamp(now)
    comps = split_components(original)
    slug = "__".join(comps) if comps else "root"
    digest = hashlib.sha256(f"{original}{stamp}".encode()).hexdigest()[:8]
    return f"{stamp}__{slug}__{digest}{secrets.token_hex(4)}"


@dataclass(frozen=True)
class TrashEntry:
    """A single trashed item + its sidecar metadata."""

    name: str
    original_path: str
    deleted_at: str
    reason: str
    sha256: str | None
    size: int
    kind: str  # "file" | "dir"
    retention_expires_at: str
    sha256_skipped: str | None = None

    def meta_bytes(self) -> bytes:
        return json.dumps(asdict(self), indent=2, sort_keys=True).encode("utf-8")


def make_entry(
    original: str,
    now: datetime,
    *,
    reason: str,
    sha256: str | None,
    size: int,
    kind: str,
    retention_days: int,
    sha256_skipped: str | None = None,
) -> TrashEntry:
    expires = now.astimezone(UTC) + timedelta(days=retention_days)
    return TrashEntry(
        name=entry_name(original, now),
        original_path=original,
        deleted_at=iso(now),
        reason=reason,
        sha256=sha256,
        size=size,
        kind=kind,
        retention_expires_at=iso(expires),
        sha256_skipped=sha256_skipped,
    )


def _parse_meta(name: str, data: bytes) -> TrashEntry | None:
    try:
        raw = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    try:
        return TrashEntry(
            name=str(raw.get("name") or name.removesuffix(META_SUFFIX)),
            original_path=str(raw["original_path"]),
            deleted_at=str(raw.get("deleted_at", "")),
            reason=str(raw.get("reason", "")),
            sha256=raw.get("sha256"),
            size=int(raw.get("size", 0)),
            kind=str(raw.get("kind", "file")),
            retention_expires_at=str(raw.get("retention_expires_at", "")),
            sha256_skipped=raw.get("sha256_skipped"),
        )
    except (KeyError, TypeError, ValueError):
        return None


def list_entries(roots: ScopedRoots, root: str | None) -> list[TrashEntry]:
    """Read every ``.meta.json`` sidecar in ``<root>/.cyclaw-trash`` into a TrashEntry.

    Returns ``[]`` when the trash dir does not exist yet. Corrupt/garbled sidecars are
    skipped (never fatal) so a single bad file cannot break ``trash-empty``.
    """
    try:
        listing = roots.list_dir(TRASH_DIR, root=root)
    except Exception:  # noqa: BLE001 -- absence/permission => empty trash, not an error
        return []
    out: list[TrashEntry] = []
    for item in listing:
        name = item.get("name", "")
        if not name.endswith(META_SUFFIX):
            continue
        try:
            data = roots.read_bytes(f"{TRASH_DIR}/{name}", root=root, max_bytes=_MAX_META_BYTES)
        except Exception:  # noqa: BLE001 -- unreadable sidecar => skip, not fatal
            # An unreadable/corrupt sidecar (truncated crash write, permission
            # blip) must not abort trash-empty. Silently dropping it hid the
            # metadata loss; log the skipped entry so it is observable, then
            # continue past this one entry.
            logger.warning("Skipping unreadable trash sidecar %r in root %r", name, root)
            continue
        entry = _parse_meta(name, data)
        if entry is not None:
            out.append(entry)
    out.sort(key=lambda e: e.name)
    return out


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


def expired(entries: list[TrashEntry], now: datetime) -> list[TrashEntry]:
    """Entries whose ``retention_expires_at`` is at or before ``now``.

    An entry with an unparseable expiry is treated as expired (fail toward disposal:
    a corrupt sidecar should not pin storage forever), matching the trash contract.
    """
    ref = now.astimezone(UTC)
    out: list[TrashEntry] = []
    for e in entries:
        exp = _parse_iso(e.retention_expires_at)
        if exp is None or exp <= ref:
            out.append(e)
    return out


def find_entry(entries: list[TrashEntry], name: str) -> TrashEntry:
    for e in entries:
        if e.name == name:
            return e
    raise FsConnectRuntimeError(
        f"trash entry not found: {name!r}",
        details={"entry": name},
    )


__all__ = [
    "TRASH_DIR",
    "META_SUFFIX",
    "TrashEntry",
    "entry_name",
    "iso",
    "make_entry",
    "list_entries",
    "expired",
    "find_entry",
]
