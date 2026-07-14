# flake8: noqa: WPS202
# WPS202 (max-module-members=7) is impractical for a single cohesive SQLite
# storage layer without splitting sessions/dedupe/audit into three separate
# files for no readability gain on a relay this small — see the "moderate
# split" decision recorded for this PR. Every individual function here still
# meets WPS's per-function complexity limits.
"""SQLite-backed session, dedupe, and audit storage for the SMS relay."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from sms_relay_config import SMS_DB_PATH, SMS_DEDUPE_TTL_SEC, SMS_SESSION_TTL_SEC
from sms_relay_util import now_ts, phone_hash, sha256_text

_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS sessions (
        phone TEXT PRIMARY KEY,
        state_json TEXT NOT NULL,
        updated_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS inbound_seen (
        msg_sid TEXT PRIMARY KEY,
        created_at INTEGER NOT NULL
    );
    CREATE TABLE IF NOT EXISTS relay_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at INTEGER NOT NULL,
        phone_hash TEXT NOT NULL,
        msg_sid TEXT,
        event_type TEXT NOT NULL,
        query_hash TEXT,
        provider TEXT,
        detail TEXT
    );
"""


@dataclass
class LogFields:
    """Optional relay_log fields, bundled to keep log_event's argument count sane."""

    msg_sid: str | None = None
    query: str | None = None
    provider: str | None = None
    detail: str | None = None


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(SMS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    conn = db()
    conn.executescript(_SCHEMA_SQL)
    conn.commit()
    conn.close()


def log_event(phone: str, event_type: str, fields: LogFields | None = None) -> None:
    fields = fields or LogFields()
    conn = db()
    conn.execute(
        "INSERT INTO relay_log (created_at, phone_hash, msg_sid, event_type, query_hash, provider, detail) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            now_ts(), phone_hash(phone), fields.msg_sid, event_type,
            sha256_text(fields.query) if fields.query else None,
            fields.provider, fields.detail,
        ),
    )
    conn.commit()
    conn.close()


def cleanup_db() -> None:
    # Runs inline on every session read rather than as a periodic background
    # task: this relay is a single-sidecar process with low request volume,
    # so a per-read DELETE is cheap enough that a separate scheduler/task
    # would add operational complexity without a measurable benefit here.
    conn = db()
    conn.execute("DELETE FROM sessions WHERE updated_at < ?", (now_ts() - SMS_SESSION_TTL_SEC,))
    conn.execute("DELETE FROM inbound_seen WHERE created_at < ?", (now_ts() - SMS_DEDUPE_TTL_SEC,))
    conn.commit()
    conn.close()


def get_session(phone: str) -> dict | None:
    cleanup_db()
    conn = db()
    row = conn.execute(
        "SELECT state_json FROM sessions WHERE phone = ?", (phone,)
    ).fetchone()
    conn.close()
    return json.loads(row["state_json"]) if row else None


def set_session(phone: str, state: dict) -> None:
    conn = db()
    conn.execute(
        "INSERT INTO sessions (phone, state_json, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(phone) DO UPDATE SET state_json=excluded.state_json, updated_at=excluded.updated_at",
        (phone, json.dumps(state), now_ts()),
    )
    conn.commit()
    conn.close()


def clear_session(phone: str) -> None:
    conn = db()
    conn.execute("DELETE FROM sessions WHERE phone = ?", (phone,))
    conn.commit()
    conn.close()


def seen_msg(msg_sid: str) -> bool:
    conn = db()
    row = conn.execute("SELECT 1 FROM inbound_seen WHERE msg_sid = ?", (msg_sid,)).fetchone()
    conn.close()
    return bool(row)


def mark_seen(msg_sid: str) -> None:
    conn = db()
    conn.execute(
        "INSERT OR IGNORE INTO inbound_seen (msg_sid, created_at) VALUES (?, ?)",
        (msg_sid, now_ts()),
    )
    conn.commit()
    conn.close()
