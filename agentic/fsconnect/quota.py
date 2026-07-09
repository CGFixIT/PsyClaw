"""Per-root capacity accounting for the filesystem connector (Phase 2).

The write gates answer "may you mutate at all"; quotas answer "is there capacity".
Exact ``du``-walks per op are O(files) and unacceptable on large roots, while a pure
incremental ledger drifts (out-of-band writes, crashes). The chosen design is a
**ledger with verified recompute**:

  * ``<root>/.cyclaw-quota.json`` holds ``{used_bytes, file_count, computed_at,
    generation}`` and is updated after every applied op.
  * A full lstat-walk recompute (``follow_symlinks=False``) runs when the ledger is
    missing/corrupt, older than ``quota_recompute_hours``, or on explicit
    ``--recompute``. Symlinks are never followed and their targets never counted.
  * **Fail closed:** if the ledger is stale/corrupt and recompute fails, the caller
    (``FsWriter._check_quota``) refuses the write -- it never assumes zero usage.

This module owns accounting only; enforcement/refusal taxonomy lives in ``writer.py``.
All filesystem access for load/save goes through ``pathsafe`` so the ledger file
inherits root containment. The recompute walk uses ``os.scandir`` anchored at the
already-resolved, held-fd root path (quota is not a security boundary -- containment
is enforced by ``pathsafe``; this walk only counts bytes).

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

import json
import os
import stat as statmod
from dataclasses import asdict, dataclass
from datetime import UTC, datetime

from agentic.fsconnect.pathsafe import SafeRoot, ScopedRoots
from agentic.fsconnect.trash import iso

QUOTA_FILE = ".cyclaw-quota.json"
_MAX_LEDGER_BYTES = 64 * 1024


@dataclass
class QuotaLedger:
    used_bytes: int
    file_count: int
    computed_at: str
    generation: int

    def to_bytes(self) -> bytes:
        return json.dumps(asdict(self), indent=2, sort_keys=True).encode("utf-8")


def _now_iso(now: datetime) -> str:
    return iso(now)


def load(roots: ScopedRoots, root: str | None) -> QuotaLedger | None:
    """Read ``.cyclaw-quota.json`` for ``root``. Returns ``None`` if missing/corrupt."""
    try:
        data = roots.read_bytes(QUOTA_FILE, root=root, max_bytes=_MAX_LEDGER_BYTES)
    except Exception:  # noqa: BLE001 -- missing/unreadable ledger => recompute path
        return None
    try:
        raw = json.loads(data.decode("utf-8"))
        return QuotaLedger(
            used_bytes=int(raw["used_bytes"]),
            file_count=int(raw["file_count"]),
            computed_at=str(raw.get("computed_at", "")),
            generation=int(raw.get("generation", 0)),
        )
    except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError):
        return None


def save(roots: ScopedRoots, root: str | None, ledger: QuotaLedger) -> None:
    """Persist the ledger via pathsafe's atomic write (overwrite-in-place)."""
    roots.write_bytes(QUOTA_FILE, ledger.to_bytes(), root=root, overwrite=True)


def _walk_usage(base: str) -> tuple[int, int]:
    """Sum sizes + count of regular files under ``base`` (symlinks never followed).

    Excludes the ledger file itself and any orphaned ``*.cyclaw-tmp`` files so the
    recompute is stable across save cycles and does not count crash leftovers.
    """
    used = 0
    files = 0
    stack = [base]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for entry in it:
                    try:
                        st = entry.stat(follow_symlinks=False)
                    except OSError:
                        continue
                    if statmod.S_ISDIR(st.st_mode):
                        stack.append(entry.path)
                    elif statmod.S_ISREG(st.st_mode):
                        if entry.name == QUOTA_FILE or entry.name.endswith(".cyclaw-tmp"):
                            continue
                        used += int(st.st_size)
                        files += 1
        except OSError:
            continue
    return used, files


def recompute(sr: SafeRoot, now: datetime, generation: int) -> QuotaLedger:
    """Full lstat-walk of the resolved root; raises OSError only on a total failure."""
    used, files = _walk_usage(str(sr.path))
    return QuotaLedger(
        used_bytes=used,
        file_count=files,
        computed_at=_now_iso(now),
        generation=generation,
    )


def is_stale(ledger: QuotaLedger | None, now: datetime, recompute_hours: int) -> bool:
    """True if the ledger is missing, undated, or older than ``recompute_hours``."""
    if ledger is None:
        return True
    dt = _parse_iso(ledger.computed_at)
    if dt is None:
        return True
    age_hours = (now.astimezone(UTC) - dt).total_seconds() / 3600.0
    return age_hours >= recompute_hours


def _parse_iso(value: str) -> datetime | None:
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except (ValueError, TypeError):
        return None


__all__ = [
    "QUOTA_FILE",
    "QuotaLedger",
    "load",
    "save",
    "recompute",
    "is_stale",
]
