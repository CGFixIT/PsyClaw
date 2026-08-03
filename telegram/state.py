"""Persistent getUpdates offset for the Telegram poller.

Survives process restarts so re-handled messages are not re-processed.
Atomic write (tmp + os.replace). Path defaults to data/telegram/offset.json
under the repo root — never shares the gateway rate-limit DB.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OFFSET_PATH = _REPO_ROOT / "data" / "telegram" / "offset.json"


def default_offset_path() -> Path:
    return DEFAULT_OFFSET_PATH


def load_offset(path: Path | None = None) -> int | None:
    """Return stored offset, or None if missing/invalid."""
    p = path if path is not None else default_offset_path()
    try:
        raw = p.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    off = data.get("offset")
    if isinstance(off, bool) or not isinstance(off, int) or off < 0:
        return None
    return off


def save_offset(offset: int, path: Path | None = None) -> None:
    """Atomically persist offset. Raises OSError on write failure."""
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ValueError(f"offset must be a non-negative int, got {offset!r}")
    p = path if path is not None else default_offset_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"offset": offset}, separators=(",", ":")) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{p.name}.",
        suffix=".tmp",
        dir=p.parent,
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(payload)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, p)
    finally:
        if fd >= 0:
            os.close(fd)
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
