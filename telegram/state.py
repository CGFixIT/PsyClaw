"""Persistent state for the Telegram poller.

Survives process restarts so re-handled messages are not re-processed and a
T3 hybrid confirmation cannot become an always-online setting. State is written
atomically under ``data/telegram`` and never stores bot tokens or message text.
"""

from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path

from utils.errors import TelegramRuntimeError

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_STATE_DIR = _REPO_ROOT / "data" / "telegram"
DEFAULT_OFFSET_PATH = DEFAULT_STATE_DIR / "offset.json"
_CHAT_ID_RE = re.compile(r"^-?\d{1,20}$")
_MIN_CHAT_ID = -(2**63)
_MAX_CHAT_ID = 2**63 - 1
_ONLINE_PROVIDERS = frozenset({"grok", "claude"})


def default_offset_path() -> Path:
    return DEFAULT_OFFSET_PATH


def default_hybrid_session_path(chat_id: int | str) -> Path:
    """Return the fixed per-chat T3 state path; reject path-shaped ids."""
    return DEFAULT_STATE_DIR / f"session_{_canonical_chat_id(chat_id)}.json"


def _canonical_chat_id(chat_id: int | str) -> str:
    if isinstance(chat_id, bool) or not isinstance(chat_id, (int, str)):
        raise ValueError("chat_id must be a signed integer")
    value = str(chat_id).strip()
    if not _CHAT_ID_RE.match(value):
        raise ValueError("chat_id must be a signed integer")
    numeric = int(value)
    if not _MIN_CHAT_ID <= numeric <= _MAX_CHAT_ID:
        raise ValueError("chat_id is outside signed 64-bit range")
    return str(numeric)


def _safe_now(now: float | None) -> float:
    value = time.time() if now is None else now
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("now must be a finite number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("now must be a finite number")
    return result


@contextmanager
def _session_lock(path: Path) -> Iterator[None]:
    """Serialize per-session claim/write operations across poller processes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f".{path.name}.lock")
    with open(lock_path, "a+b") as lock:
        lock.seek(0, os.SEEK_END)
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _save_json(payload: dict[str, object], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fd = -1
            fh.write(json.dumps(payload, separators=(",", ":")) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if fd >= 0:
            os.close(fd)
        with suppress(OSError):
            tmp_path.unlink(missing_ok=True)


def _load_hybrid_session(path: Path) -> tuple[int, str] | None:
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    confirm_until = data.get("confirm_until")
    provider = data.get("provider")
    if (
        isinstance(confirm_until, bool)
        or not isinstance(confirm_until, int)
        or confirm_until < 0
        or not isinstance(provider, str)
        or provider not in _ONLINE_PROVIDERS
    ):
        return None
    return confirm_until, provider


def grant_hybrid_confirm(
    chat_id: int | str,
    *,
    provider: str,
    ttl_sec: int,
    now: float | None = None,
    path: Path | None = None,
) -> int:
    """Persist one explicit T3 confirmation and return its UNIX expiry time."""
    if provider not in _ONLINE_PROVIDERS:
        raise ValueError("provider must be grok or claude")
    if isinstance(ttl_sec, bool) or not isinstance(ttl_sec, int) or ttl_sec <= 0:
        raise ValueError("ttl_sec must be a positive integer")
    canonical_chat_id = _canonical_chat_id(chat_id)
    session_path = path if path is not None else default_hybrid_session_path(canonical_chat_id)
    confirm_until = int(_safe_now(now) + ttl_sec)
    try:
        with _session_lock(session_path):
            _save_json(
                {"confirm_until": confirm_until, "provider": provider},
                session_path,
            )
    except OSError:
        raise TelegramRuntimeError(
            "Telegram hybrid confirmation state is unavailable",
            details={"gate": "hybrid_confirm_state", "retryable": True},
        ) from None
    return confirm_until


def claim_hybrid_confirm(
    chat_id: int | str,
    *,
    now: float | None = None,
    path: Path | None = None,
) -> str | None:
    """Atomically claim one unexpired T3 confirmation, returning its provider."""
    canonical_chat_id = _canonical_chat_id(chat_id)
    session_path = path if path is not None else default_hybrid_session_path(canonical_chat_id)
    current = _safe_now(now)
    try:
        with _session_lock(session_path):
            session = _load_hybrid_session(session_path)
            if session is None:
                return None
            confirm_until, provider = session
            with suppress(OSError):
                session_path.unlink(missing_ok=True)
            if current >= confirm_until:
                return None
            return provider
    except OSError:
        raise TelegramRuntimeError(
            "Telegram hybrid confirmation state is unavailable",
            details={"gate": "hybrid_confirm_state", "retryable": True},
        ) from None


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
    _save_json({"offset": offset}, p)
