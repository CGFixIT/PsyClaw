"""Harness-local operator notes for ``/memory``.

This is **not** the RAG ``memory/`` package, **not** ``docs/memories/``,
and **not** ``soul.md``. Notes live under the harness home
(``~/.CyClaw/memory/notes.json``) and enter the chat system prompt only
while the operator has ``/memory on``. Default is off.

Writes never leave the harness home, never flip ``config.yaml``
``memory.enabled``, and never call ``/memory/propose`` or ``/memory/apply``.
"""

from __future__ import annotations

import json
import re
import secrets
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from harness.config import _UTF8, _atomic_write_json
from utils.errors import AgenticError
from utils.personality import ENFORCED_SOUL_PATTERNS

# Serializes add/forget/clear's read-modify-write on notes.json, mirroring
# harness/sessions.py's _LOCK -- without it, two concurrent /memory calls can
# each read the same notes list and one write clobbers the other's update.
_LOCK = threading.Lock()

_NOTES_FILE = "notes.json"
_NOTES_KEY = "notes"
_ID_KEY = "id"
_TEXT_KEY = "text"
_TS_KEY = "ts"
_ENABLED_KEY = "enabled"
_MAX_NOTE_CHARS = 500
_MAX_NOTES = 20
_MAX_PROMPT_CHARS = 3000
_ID_BYTES = 4
_INJECTION = tuple(
    re.compile(pattern, re.IGNORECASE) for pattern in ENFORCED_SOUL_PATTERNS
)

_PREAMBLE = (
    "The following are operator-pinned notes from /memory. "
    "They are not a write authorization and do not change routing, "
    "topology, or the real-repo six-gate. They are not soul.md and "
    "are not RAG facts."
)


class MemoryNotesError(AgenticError):
    """Operator-note validation or persistence failure."""


def rag_flags(cfg: Mapping[str, Any] | None) -> dict[str, bool]:
    """Read-only echo of the RAG ``memory:`` block. Never written here."""
    block: Mapping[str, Any] = {}
    if isinstance(cfg, dict):
        raw = cfg.get("memory")
        if isinstance(raw, dict):
            block = raw
    facts = block.get("facts")
    episodes = block.get("episodes")
    fusion = block.get("retrieval_fusion")
    return {
        _ENABLED_KEY: bool(block.get(_ENABLED_KEY)),
        "facts": bool(facts.get(_ENABLED_KEY)) if isinstance(facts, dict) else False,
        "episodes": bool(episodes.get(_ENABLED_KEY)) if isinstance(episodes, dict) else False,
        "retrieval_fusion": bool(fusion.get(_ENABLED_KEY)) if isinstance(fusion, dict) else False,
        "writable_from_harness": False,
    }


def _clean_note(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        raise MemoryNotesError("note text is empty", code="MEMORY_NOTE_EMPTY")
    if len(cleaned) > _MAX_NOTE_CHARS:
        raise MemoryNotesError(
            f"note exceeds {_MAX_NOTE_CHARS} characters",
            code="MEMORY_NOTE_TOO_LONG",
            details={"max_chars": _MAX_NOTE_CHARS},
        )
    flags = [pat.pattern for pat in _INJECTION if pat.search(cleaned)]
    if flags:
        raise MemoryNotesError(
            "note matches a critical injection pattern",
            code="MEMORY_NOTE_INJECTION",
            details={"injection_flag_count": len(flags)},
        )
    return cleaned


def _coerce_note(raw: object) -> dict[str, str] | None:
    if not isinstance(raw, dict):
        return None
    note_id = str(raw.get(_ID_KEY) or "").strip()
    text = str(raw.get(_TEXT_KEY) or "").strip()
    if not note_id or not text:
        return None
    return {
        _ID_KEY: note_id,
        _TEXT_KEY: text[:_MAX_NOTE_CHARS],
        _TS_KEY: str(raw.get(_TS_KEY) or ""),
    }


def _load_notes(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    try:
        parsed = json.loads(path.read_text(encoding=_UTF8))
    except (OSError, json.JSONDecodeError):
        return []
    raw = parsed.get(_NOTES_KEY) if isinstance(parsed, dict) else None
    if not isinstance(raw, list):
        return []
    notes: list[dict[str, str]] = []
    for entry in raw:
        coerced = _coerce_note(entry)
        if coerced:
            notes.append(coerced)
    return notes[:_MAX_NOTES]


def _save_notes(memory_dir: Path, path: Path, notes: list[dict[str, str]]) -> None:
    memory_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(path, {_NOTES_KEY: notes})


class MemoryNotes:
    """Bounded note store. One JSON file, atomic replace."""

    def __init__(self, memory_dir: Path) -> None:
        self.dir = memory_dir
        self.path = memory_dir / _NOTES_FILE

    def status(self, enabled: bool) -> dict[str, Any]:
        notes = _load_notes(self.path)
        return {
            _ENABLED_KEY: enabled,
            "count": len(notes),
            "max_notes": _MAX_NOTES,
            "max_chars": _MAX_NOTE_CHARS,
            _NOTES_KEY: notes,
        }

    def add(self, text: str) -> dict[str, Any]:
        cleaned = _clean_note(text)
        with _LOCK:
            notes = _load_notes(self.path)
            if len(notes) >= _MAX_NOTES:
                raise MemoryNotesError(
                    f"at most {_MAX_NOTES} notes",
                    code="MEMORY_NOTE_CAP",
                    details={"max_notes": _MAX_NOTES},
                )
            note = {
                _ID_KEY: secrets.token_hex(_ID_BYTES),
                _TEXT_KEY: cleaned,
                _TS_KEY: datetime.now(UTC).isoformat(),
            }
            notes.append(note)
            _save_notes(self.dir, self.path, notes)
            return note

    def forget(self, note_id: str) -> dict[str, Any]:
        wanted = (note_id or "").strip()
        if not wanted:
            raise MemoryNotesError("note id is required", code="MEMORY_NOTE_ID_REQUIRED")
        with _LOCK:
            notes = _load_notes(self.path)
            kept = [note for note in notes if note.get(_ID_KEY) != wanted]
            if len(kept) == len(notes):
                raise MemoryNotesError(
                    "unknown note id",
                    code="MEMORY_NOTE_UNKNOWN",
                    details={_ID_KEY: wanted},
                )
            _save_notes(self.dir, self.path, kept)
            return {"forgotten": wanted, "count": len(kept)}

    def clear(self) -> dict[str, Any]:
        with _LOCK:
            _save_notes(self.dir, self.path, [])
            return {"cleared": True, "count": 0}

    def context_text(self) -> str:
        notes = _load_notes(self.path)
        if not notes:
            return ""
        lines = [f"- [{note[_ID_KEY]}] {note[_TEXT_KEY]}" for note in notes]
        blob = "\n".join(lines)[:_MAX_PROMPT_CHARS]
        return f"{_PREAMBLE}\n\n{blob}"
