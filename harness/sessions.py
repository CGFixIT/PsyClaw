"""JSON-backed chat session store with per-session token tallies.

One file per session under ``~/.CyClaw/sessions/``. Every LLM exchange records
Ollama's ``prompt_eval_count`` / ``eval_count`` so the console can show a
running tally (the grok-build style "tokens in/out" readout). Files are small
and human-inspectable; writes are atomic (tmp + os.replace), matching the
repo's soul/registry durability pattern.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from utils.errors import AgenticError

_LOCK = threading.Lock()
_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_MAX_MESSAGES = 500  # bound per-session growth; oldest turns drop off first


class SessionStoreError(AgenticError):
    """Session persistence failure (unknown id, unreadable file)."""

    def __init__(self, message: str, code: str = "HARNESS_SESSION_ERROR", details: dict | None = None):
        super().__init__(message, code=code, details=details)


@dataclass
class TokenTally:
    """Cumulative Ollama usage counters for one session."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    exchanges: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str
    ts: float = field(default_factory=time.time)


@dataclass
class Session:
    session_id: str
    title: str
    created_ts: float
    model: str
    messages: list[Message] = field(default_factory=list)
    tally: TokenTally = field(default_factory=TokenTally)

    def summary(self) -> dict:
        last = self.messages[-1].content[:80] if self.messages else ""
        return {
            "session_id": self.session_id,
            "title": self.title,
            "created_ts": self.created_ts,
            "model": self.model,
            "message_count": len(self.messages),
            "last_excerpt": last,
            "tokens": asdict(self.tally) | {"total": self.tally.total},
        }


class SessionStore:
    """Load/save sessions under a directory. Thread-safe within one process."""

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        if not _ID_RE.match(session_id):
            raise SessionStoreError("invalid session id", details={"session_id": session_id})
        # IDs are server-generated hex; the regex above is the traversal gate.
        return self._dir / f"{session_id}.json"

    def create(self, *, model: str, title: str = "") -> Session:
        session = Session(
            session_id=uuid.uuid4().hex[:12],
            title=title.strip() or f"session {time.strftime('%Y-%m-%d %H:%M')}",
            created_ts=time.time(),
            model=model,
        )
        self._write(session)
        return session

    def get(self, session_id: str) -> Session:
        path = self._path(session_id)
        if not path.exists():
            raise SessionStoreError("unknown session", details={"session_id": session_id})
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SessionStoreError(f"unreadable session file: {path.name}") from exc
        tally = data.get("tally", {})
        return Session(
            session_id=data["session_id"],
            title=data.get("title", ""),
            created_ts=float(data.get("created_ts", 0.0)),
            model=data.get("model", ""),
            messages=[Message(**m) for m in data.get("messages", [])],
            tally=TokenTally(
                prompt_tokens=int(tally.get("prompt_tokens", 0)),
                completion_tokens=int(tally.get("completion_tokens", 0)),
                exchanges=int(tally.get("exchanges", 0)),
            ),
        )

    def list(self) -> list[dict]:
        out: list[dict] = []
        for path in sorted(self._dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                out.append(self.get(path.stem).summary())
            except SessionStoreError:
                continue  # skip corrupt files rather than break the listing
        return out

    def record_exchange(
        self,
        session_id: str,
        *,
        user_text: str,
        assistant_text: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Session:
        with _LOCK:
            session = self.get(session_id)
            session.messages.append(Message(role="user", content=user_text))
            session.messages.append(Message(role="assistant", content=assistant_text))
            if len(session.messages) > _MAX_MESSAGES:
                session.messages = session.messages[-_MAX_MESSAGES:]
            session.model = model
            session.tally.prompt_tokens += max(prompt_tokens, 0)
            session.tally.completion_tokens += max(completion_tokens, 0)
            session.tally.exchanges += 1
            self._write(session)
            return session

    def rename(self, session_id: str, title: str) -> Session:
        with _LOCK:
            session = self.get(session_id)
            session.title = title.strip() or session.title
            self._write(session)
            return session

    def _write(self, session: Session) -> None:
        payload = {
            "session_id": session.session_id,
            "title": session.title,
            "created_ts": session.created_ts,
            "model": session.model,
            "messages": [asdict(m) for m in session.messages],
            "tally": asdict(session.tally),
        }
        fd, tmp = tempfile.mkstemp(dir=str(self._dir), prefix=".sess.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(tmp, self._path(session.session_id))
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise SessionStoreError("could not persist session") from exc
