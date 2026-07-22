"""JSON-backed chat session store with per-session token tallies.

One file per session under ``~/.CyClaw/sessions/``. Every LLM exchange records
the ``usage.prompt_tokens`` / ``usage.completion_tokens`` counts from the
OpenAI-compatible ``/v1`` endpoint (see ``harness/ollama.py`` for why that
surface, not the native Ollama API) so the console can show a running tally
(the grok-build style "tokens in/out" readout). Files are small
and human-inspectable; writes are atomic (staged file + os.replace), matching
the repo's soul/registry durability pattern.
"""

from __future__ import annotations

import json
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from os.path import getmtime
from pathlib import Path

from harness.config import _UTF8, _atomic_write_json
from utils.errors import AgenticError

_LOCK = threading.Lock()
_ID_RE = re.compile(r"^[0-9a-f]{12}$")
_SESSION_ID_CHARS = 12
_MAX_MESSAGES = 500  # bound per-session growth; oldest turns drop off first
_TITLE_TS_FORMAT = "%Y-%m-%d %H:%M"
_EXCERPT_CHARS = 80
_SID_KEY = "session_id"


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
    text: str
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
        last = ""
        if self.messages:
            last = self.messages[-1].text[:_EXCERPT_CHARS]
        return {
            _SID_KEY: self.session_id,
            "title": self.title,
            "created_ts": self.created_ts,
            "model": self.model,
            "message_count": len(self.messages),
            "last_excerpt": last,
            "tokens": asdict(self.tally) | {"total": self.tally.total},
        }


def _session_path(sessions_dir: Path, session_id: str) -> Path:
    if not _ID_RE.match(session_id):
        raise SessionStoreError("invalid session id", details={_SID_KEY: session_id})
    # IDs are server-generated hex; the regex above is the traversal gate.
    return sessions_dir / f"{session_id}.json"


def _session_from_dict(parsed: dict) -> Session:
    tally = parsed.get("tally", {})
    messages = []
    for msg in parsed.get("messages", []):
        messages.append(Message(**msg))
    return Session(
        session_id=parsed[_SID_KEY],
        title=parsed.get("title", ""),
        created_ts=float(parsed.get("created_ts", 0)),
        model=parsed.get("model", ""),
        messages=messages,
        tally=TokenTally(
            prompt_tokens=int(tally.get("prompt_tokens", 0)),
            completion_tokens=int(tally.get("completion_tokens", 0)),
            exchanges=int(tally.get("exchanges", 0)),
        ),
    )


class SessionStore:
    """Load/save sessions under a directory. Thread-safe within one process."""

    def __init__(self, sessions_dir: Path) -> None:
        self._dir = sessions_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def create(self, *, model: str, title: str = "") -> Session:
        session = Session(
            session_id=uuid.uuid4().hex[:_SESSION_ID_CHARS],
            title=title.strip() or f"session {time.strftime(_TITLE_TS_FORMAT)}",
            created_ts=time.time(),
            model=model,
        )
        self._write(session)
        return session

    def get(self, session_id: str) -> Session:
        path = _session_path(self._dir, session_id)
        if not path.exists():
            raise SessionStoreError("unknown session", details={_SID_KEY: session_id})
        try:
            parsed = json.loads(path.read_text(encoding=_UTF8))
            return _session_from_dict(parsed)
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError, AttributeError) as exc:
            # _session_from_dict raises KeyError/TypeError/ValueError/AttributeError
            # on JSON that parses but isn't session-shaped (non-dict payload, missing
            # session_id, unexpected message keys). Mapping them all to
            # SessionStoreError is what lets list() actually skip corrupt files
            # instead of 500-ing the console listing.
            raise SessionStoreError(f"unreadable session file: {path.name}") from exc

    def list(self) -> list[dict]:
        summaries: list[dict] = []
        paths = list(self._dir.glob("*.json"))
        paths.sort(key=getmtime, reverse=True)
        for path in paths:
            try:
                summaries.append(self.get(path.stem).summary())
            except SessionStoreError:
                ...  # skip corrupt files rather than break the listing
        return summaries

    def record_exchange(
        self,
        session_id: str,
        *,
        user_text: str,
        assistant_text: str,
        model: str,
        usage: TokenTally,
    ) -> Session:
        with _LOCK:
            session = self.get(session_id)
            session.messages.append(Message(role="user", text=user_text))
            session.messages.append(Message(role="assistant", text=assistant_text))
            if len(session.messages) > _MAX_MESSAGES:
                session.messages = session.messages[-_MAX_MESSAGES:]
            session.model = model
            session.tally.prompt_tokens += max(usage.prompt_tokens, 0)
            session.tally.completion_tokens += max(usage.completion_tokens, 0)
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
            _SID_KEY: session.session_id,
            "title": session.title,
            "created_ts": session.created_ts,
            "model": session.model,
            "messages": [asdict(msg) for msg in session.messages],
            "tally": asdict(session.tally),
        }
        try:
            _atomic_write_json(_session_path(self._dir, session.session_id), payload)
        except OSError as exc:
            raise SessionStoreError("could not persist session") from exc
