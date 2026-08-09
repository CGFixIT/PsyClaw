"""SQLite + FTS5 memory store (WAL, 0600, parameterized SQL only)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import stat
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from memory.models import Episode, Fact, MemoryProposal
from memory.policy import (
    check_content_size,
    check_tags,
    enforce_content,
    require_reason,
    scan_content,
)
from utils.logger import hash_query, redact_sensitive

logger = logging.getLogger("cyclaw.memory")

_write_lock = threading.Lock()
_episode_counter = 0
_episode_counter_lock = threading.Lock()


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _mem_cfg(cfg: Mapping[str, Any]) -> dict[str, Any]:
    return dict(cfg.get("memory") or {})


def _db_path(cfg: Mapping[str, Any]) -> Path:
    mem = _mem_cfg(cfg)
    raw = mem.get("db_path") or "data/memory/cyclaw_memory.db"
    return Path(str(raw))


def connect(cfg: Mapping[str, Any]) -> sqlite3.Connection:
    """Open (or create) the memory DB with WAL + 0600 permissions."""
    path = _db_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    db_existed = path.exists()
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    os.close(fd)
    if db_existed:
        try:
            if stat.S_IMODE(path.stat().st_mode) != 0o600:
                os.chmod(path, 0o600)
        except OSError:
            logger.warning("Could not harden memory DB permissions to 0600: %s", path)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    _ensure_schema(conn)
    return conn


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS facts (
          id            INTEGER PRIMARY KEY,
          content       TEXT NOT NULL CHECK(length(content) > 0 AND length(content) <= 8192),
          category      TEXT NOT NULL DEFAULT 'general',
          tags_json     TEXT NOT NULL DEFAULT '[]',
          confidence    REAL NOT NULL DEFAULT 1.0
                        CHECK(confidence >= 0.0 AND confidence <= 1.0),
          source        TEXT NOT NULL DEFAULT 'human',
          active        INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
          created_at    TEXT NOT NULL,
          updated_at    TEXT NOT NULL,
          applied_reason TEXT NOT NULL DEFAULT '',
          content_sha256 TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS episodes (
          id            INTEGER PRIMARY KEY,
          query_hash    TEXT NOT NULL,
          answer_summary TEXT NOT NULL DEFAULT '',
          model_used    TEXT NOT NULL DEFAULT '',
          top_score     REAL,
          retrieval_mode TEXT,
          hit_count     INTEGER,
          source_tag    TEXT NOT NULL DEFAULT 'query',
          created_at    TEXT NOT NULL,
          raw_query     TEXT
        );

        CREATE TABLE IF NOT EXISTS memory_proposals (
          id            INTEGER PRIMARY KEY,
          action        TEXT NOT NULL
                        CHECK(action IN ('add_fact','update_fact','deactivate_fact')),
          payload_json  TEXT NOT NULL,
          reason        TEXT NOT NULL,
          status        TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending','applied','rejected')),
          injection_flags_json TEXT NOT NULL DEFAULT '[]',
          created_at    TEXT NOT NULL,
          resolved_at   TEXT,
          resolved_reason TEXT
        );

        CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
          content,
          category,
          tags,
          tokenize = 'porter'
        );

        CREATE TRIGGER IF NOT EXISTS facts_ai AFTER INSERT ON facts WHEN new.active = 1 BEGIN
          INSERT INTO facts_fts(rowid, content, category, tags)
          VALUES (
            new.id,
            new.content,
            new.category,
            replace(replace(replace(new.tags_json, '[', ''), ']', ''), '"', '')
          );
        END;

        CREATE TRIGGER IF NOT EXISTS facts_ad AFTER DELETE ON facts BEGIN
          DELETE FROM facts_fts WHERE rowid = old.id;
        END;

        CREATE TRIGGER IF NOT EXISTS facts_au AFTER UPDATE ON facts BEGIN
          DELETE FROM facts_fts WHERE rowid = old.id;
          INSERT INTO facts_fts(rowid, content, category, tags)
          SELECT new.id, new.content, new.category,
                 replace(replace(replace(new.tags_json, '[', ''), ']', ''), '"', '')
          WHERE new.active = 1;
        END;

        CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(active);
        CREATE INDEX IF NOT EXISTS idx_episodes_query_hash ON episodes(query_hash);
        CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at);
        CREATE INDEX IF NOT EXISTS idx_proposals_status ON memory_proposals(status);
        """
    )
    conn.commit()


def _row_to_fact(row: sqlite3.Row) -> Fact:
    tags = json.loads(row["tags_json"] or "[]")
    if not isinstance(tags, list):
        tags = []
    return Fact(
        id=int(row["id"]),
        content=row["content"],
        category=row["category"] or "general",
        tags=[str(t) for t in tags],
        confidence=float(row["confidence"]),
        source=row["source"] or "human",
        active=bool(row["active"]),
        created_at=row["created_at"] or "",
        updated_at=row["updated_at"] or "",
        applied_reason=row["applied_reason"] or "",
        content_sha256=row["content_sha256"] or "",
    )


def _row_to_episode(row: sqlite3.Row) -> Episode:
    return Episode(
        id=int(row["id"]),
        query_hash=row["query_hash"],
        answer_summary=row["answer_summary"] or "",
        model_used=row["model_used"] or "",
        top_score=row["top_score"],
        retrieval_mode=row["retrieval_mode"],
        hit_count=row["hit_count"],
        source_tag=row["source_tag"] or "query",
        created_at=row["created_at"] or "",
        raw_query=row["raw_query"] if "raw_query" in row.keys() else None,
    )


def _row_to_proposal(row: sqlite3.Row) -> MemoryProposal:
    flags = json.loads(row["injection_flags_json"] or "[]")
    payload = json.loads(row["payload_json"] or "{}")
    return MemoryProposal(
        id=int(row["id"]),
        action=row["action"],  # type: ignore[arg-type]
        payload=payload if isinstance(payload, dict) else {},
        reason=row["reason"] or "",
        status=row["status"],  # type: ignore[arg-type]
        injection_flags=[str(f) for f in flags] if isinstance(flags, list) else [],
        created_at=row["created_at"] or "",
        resolved_at=row["resolved_at"],
        resolved_reason=row["resolved_reason"],
    )


def list_facts(
    cfg: Mapping[str, Any],
    *,
    active_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[Fact]:
    conn = connect(cfg)
    try:
        if active_only:
            rows = conn.execute(
                "SELECT * FROM facts WHERE active = 1 ORDER BY id DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM facts ORDER BY id DESC LIMIT ? OFFSET ?",
                (int(limit), int(offset)),
            ).fetchall()
        return [_row_to_fact(r) for r in rows]
    finally:
        conn.close()


def get_fact(cfg: Mapping[str, Any], fact_id: int) -> Fact | None:
    conn = connect(cfg)
    try:
        row = conn.execute("SELECT * FROM facts WHERE id = ?", (int(fact_id),)).fetchone()
        return _row_to_fact(row) if row else None
    finally:
        conn.close()


def insert_fact(
    cfg: Mapping[str, Any],
    content: str,
    *,
    category: str = "general",
    tags: list[str] | None = None,
    confidence: float = 1.0,
    source: str = "human",
    reason: str = "",
) -> Fact:
    check_content_size(content, dict(cfg))
    cleaned_tags = check_tags(tags)
    conf = max(0.0, min(1.0, float(confidence)))
    now = _now()
    digest = _sha256(content)
    with _write_lock:
        conn = connect(cfg)
        try:
            cur = conn.execute(
                """
                INSERT INTO facts (
                  content, category, tags_json, confidence, source, active,
                  created_at, updated_at, applied_reason, content_sha256
                ) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    content,
                    (category or "general")[:64],
                    json.dumps(cleaned_tags),
                    conf,
                    source or "human",
                    now,
                    now,
                    reason or "",
                    digest,
                ),
            )
            conn.commit()
            fact_id = int(cur.lastrowid)
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (fact_id,)).fetchone()
            if row is None:
                raise RuntimeError("fact row missing after write")
            return _row_to_fact(row)
        finally:
            conn.close()


def update_fact(
    cfg: Mapping[str, Any],
    fact_id: int,
    *,
    content: str | None = None,
    category: str | None = None,
    tags: list[str] | None = None,
    confidence: float | None = None,
    reason: str = "",
) -> Fact:
    existing = get_fact(cfg, fact_id)
    if existing is None:
        raise ValueError(f"fact {fact_id} not found")
    new_content = content if content is not None else existing.content
    check_content_size(new_content, dict(cfg))
    new_tags = check_tags(tags) if tags is not None else existing.tags
    new_cat = (category if category is not None else existing.category)[:64]
    new_conf = (
        max(0.0, min(1.0, float(confidence)))
        if confidence is not None
        else existing.confidence
    )
    now = _now()
    digest = _sha256(new_content)
    with _write_lock:
        conn = connect(cfg)
        try:
            conn.execute(
                """
                UPDATE facts SET content=?, category=?, tags_json=?, confidence=?,
                  updated_at=?, applied_reason=?, content_sha256=?, active=1
                WHERE id=?
                """,
                (
                    new_content,
                    new_cat,
                    json.dumps(new_tags),
                    new_conf,
                    now,
                    reason or existing.applied_reason,
                    digest,
                    int(fact_id),
                ),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (int(fact_id),)).fetchone()
            if row is None:
                raise RuntimeError("fact row missing after write")
            return _row_to_fact(row)
        finally:
            conn.close()


def deactivate_fact(cfg: Mapping[str, Any], fact_id: int, *, reason: str = "") -> Fact:
    existing = get_fact(cfg, fact_id)
    if existing is None:
        raise ValueError(f"fact {fact_id} not found")
    now = _now()
    with _write_lock:
        conn = connect(cfg)
        try:
            conn.execute(
                """
                UPDATE facts SET active=0, updated_at=?, applied_reason=?
                WHERE id=?
                """,
                (now, reason or existing.applied_reason, int(fact_id)),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (int(fact_id),)).fetchone()
            if row is None:
                raise RuntimeError("fact row missing after write")
            return _row_to_fact(row)
        finally:
            conn.close()


def create_proposal(
    cfg: Mapping[str, Any],
    action: str,
    payload: dict[str, Any],
    reason: str,
) -> MemoryProposal:
    require_reason(reason)
    if action not in ("add_fact", "update_fact", "deactivate_fact"):
        raise ValueError(f"invalid action: {action}")
    content = str(payload.get("content") or "")
    flags = scan_content(content, dict(cfg), enforced=False) if content else []
    now = _now()
    with _write_lock:
        conn = connect(cfg)
        try:
            cur = conn.execute(
                """
                INSERT INTO memory_proposals (
                  action, payload_json, reason, status, injection_flags_json, created_at
                ) VALUES (?, ?, ?, 'pending', ?, ?)
                """,
                (action, json.dumps(payload), reason.strip(), json.dumps(flags), now),
            )
            conn.commit()
            pid = int(cur.lastrowid)
            row = conn.execute(
                "SELECT * FROM memory_proposals WHERE id = ?", (pid,)
            ).fetchone()
            if row is None:
                raise RuntimeError("proposal row missing after write")
            return _row_to_proposal(row)
        finally:
            conn.close()


def get_proposal(cfg: Mapping[str, Any], proposal_id: int) -> MemoryProposal | None:
    conn = connect(cfg)
    try:
        row = conn.execute(
            "SELECT * FROM memory_proposals WHERE id = ?", (int(proposal_id),)
        ).fetchone()
        return _row_to_proposal(row) if row else None
    finally:
        conn.close()


def list_proposals(
    cfg: Mapping[str, Any],
    *,
    status: str | None = "pending",
    limit: int = 50,
) -> list[MemoryProposal]:
    conn = connect(cfg)
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM memory_proposals WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, int(limit)),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM memory_proposals ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
        return [_row_to_proposal(r) for r in rows]
    finally:
        conn.close()


def set_proposal_status(
    cfg: Mapping[str, Any],
    proposal_id: int,
    status: str,
    *,
    resolved_reason: str = "",
) -> MemoryProposal:
    if status not in ("pending", "applied", "rejected"):
        raise ValueError(f"invalid status: {status}")
    now = _now()
    with _write_lock:
        conn = connect(cfg)
        try:
            conn.execute(
                """
                UPDATE memory_proposals
                SET status=?, resolved_at=?, resolved_reason=?
                WHERE id=?
                """,
                (status, now, resolved_reason, int(proposal_id)),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM memory_proposals WHERE id = ?", (int(proposal_id),)
            ).fetchone()
            if row is None:
                raise ValueError(f"proposal {proposal_id} not found")
            return _row_to_proposal(row)
        finally:
            conn.close()


def apply_proposal(cfg: Mapping[str, Any], proposal_id: int, reason: str) -> dict[str, Any]:
    """Apply a pending proposal: enforce injection, mutate facts, mark applied."""
    require_reason(reason)
    prop = get_proposal(cfg, proposal_id)
    if prop is None:
        raise ValueError(f"proposal {proposal_id} not found")
    if prop.status != "pending":
        raise ValueError(f"proposal {proposal_id} is not pending (status={prop.status})")

    action = prop.action
    payload = prop.payload
    cfg_dict = dict(cfg)

    if action == "add_fact":
        content = str(payload.get("content") or "")
        check_content_size(content, cfg_dict)
        enforce_content(content, cfg_dict)
        fact = insert_fact(
            cfg,
            content,
            category=str(payload.get("category") or "general"),
            tags=list(payload.get("tags") or []),
            confidence=float(payload.get("confidence", 1.0)),
            source=str(payload.get("source") or "human"),
            reason=reason.strip(),
        )
        set_proposal_status(cfg, proposal_id, "applied", resolved_reason=reason.strip())
        return {"status": "applied", "action": action, "fact_id": fact.id, "proposal_id": proposal_id}

    if action == "update_fact":
        fact_id = int(payload.get("fact_id") or 0)
        if fact_id < 1:
            raise ValueError("update_fact requires fact_id")
        content = payload.get("content")
        if content is not None:
            check_content_size(str(content), cfg_dict)
            enforce_content(str(content), cfg_dict)
        fact = update_fact(
            cfg,
            fact_id,
            content=str(content) if content is not None else None,
            category=str(payload["category"]) if "category" in payload else None,
            tags=list(payload["tags"]) if "tags" in payload else None,
            confidence=float(payload["confidence"]) if "confidence" in payload else None,
            reason=reason.strip(),
        )
        set_proposal_status(cfg, proposal_id, "applied", resolved_reason=reason.strip())
        return {"status": "applied", "action": action, "fact_id": fact.id, "proposal_id": proposal_id}

    if action == "deactivate_fact":
        fact_id = int(payload.get("fact_id") or 0)
        if fact_id < 1:
            raise ValueError("deactivate_fact requires fact_id")
        fact = deactivate_fact(cfg, fact_id, reason=reason.strip())
        set_proposal_status(cfg, proposal_id, "applied", resolved_reason=reason.strip())
        return {"status": "applied", "action": action, "fact_id": fact.id, "proposal_id": proposal_id}

    raise ValueError(f"unsupported action: {action}")


def reject_proposal(cfg: Mapping[str, Any], proposal_id: int, reason: str) -> MemoryProposal:
    require_reason(reason)
    prop = get_proposal(cfg, proposal_id)
    if prop is None:
        raise ValueError(f"proposal {proposal_id} not found")
    if prop.status != "pending":
        raise ValueError(f"proposal {proposal_id} is not pending (status={prop.status})")
    return set_proposal_status(cfg, proposal_id, "rejected", resolved_reason=reason.strip())


def search_facts_fts(
    cfg: Mapping[str, Any],
    query: str,
    *,
    limit: int = 5,
) -> list[tuple[int, str, float]]:
    """Return (id, content, rank) for active facts matching FTS query.

    rank is bm25-style (lower is better from fts5 bm25); converted by fusion.
    """
    q = (query or "").strip()
    if not q:
        return []
    conn = connect(cfg)
    try:
        # Restrict to active facts via JOIN
        rows = conn.execute(
            """
            SELECT f.id, f.content, bm25(facts_fts) AS rank
            FROM facts_fts
            JOIN facts f ON f.id = facts_fts.rowid
            WHERE facts_fts MATCH ? AND f.active = 1
            ORDER BY rank
            LIMIT ?
            """,
            (q, int(limit)),
        ).fetchall()
        return [(int(r["id"]), r["content"], float(r["rank"])) for r in rows]
    except sqlite3.OperationalError:
        # MATCH syntax errors on odd queries — treat as no hits
        return []
    finally:
        conn.close()


def stage_episode(cfg: Mapping[str, Any], state: Mapping[str, Any]) -> None:
    """Non-fatal-friendly episode write from GraphState-like mapping.

    Caller must wrap in try/except. Only stages when memory+episodes enabled.
    """
    mem = _mem_cfg(cfg)
    if mem.get("enabled") is not True:
        return
    ep_cfg = mem.get("episodes") or {}
    if ep_cfg.get("enabled") is not True:
        return

    query = str(state.get("query") or "")
    qh = hash_query(query)
    max_sum = int(ep_cfg.get("max_answer_summary_chars", 2000))
    answer = str(state.get("answer") or state.get("final_answer") or "")
    # Prefer a short truncated answer; redact if privacy patterns apply
    summary = redact_sensitive(answer, dict(cfg))[:max_sum] if answer else ""

    raw_query: str | None = None
    if ep_cfg.get("store_raw_query") is True:
        raw_query = redact_sensitive(query, dict(cfg))

    now = _now()
    with _write_lock:
        conn = connect(cfg)
        try:
            conn.execute(
                """
                INSERT INTO episodes (
                  query_hash, answer_summary, model_used, top_score,
                  retrieval_mode, hit_count, source_tag, created_at, raw_query
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    qh,
                    summary,
                    str(state.get("answer_model") or ""),
                    state.get("top_score"),
                    state.get("retrieval_mode"),
                    len(state.get("retrieved_docs") or []),
                    str(state.get("source_tag") or "query"),
                    now,
                    raw_query,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # Amortized TTL prune
    global _episode_counter
    prune_every = int(ep_cfg.get("prune_every", 100) or 100)
    with _episode_counter_lock:
        _episode_counter += 1
        should_prune = prune_every > 0 and (_episode_counter % prune_every == 0)
    if should_prune:
        prune_episodes(cfg)


def prune_episodes(cfg: Mapping[str, Any]) -> int:
    mem = _mem_cfg(cfg)
    ep_cfg = mem.get("episodes") or {}
    ttl_days = int(ep_cfg.get("ttl_days", 365) or 365)
    if ttl_days <= 0:
        return 0
    cutoff = (datetime.now(UTC) - timedelta(days=ttl_days)).isoformat()
    with _write_lock:
        conn = connect(cfg)
        try:
            cur = conn.execute("DELETE FROM episodes WHERE created_at < ?", (cutoff,))
            conn.commit()
            return int(cur.rowcount or 0)
        finally:
            conn.close()


def list_episodes(
    cfg: Mapping[str, Any],
    *,
    limit: int = 100,
    offset: int = 0,
) -> list[Episode]:
    conn = connect(cfg)
    try:
        rows = conn.execute(
            "SELECT * FROM episodes ORDER BY id DESC LIMIT ? OFFSET ?",
            (int(limit), int(offset)),
        ).fetchall()
        return [_row_to_episode(r) for r in rows]
    finally:
        conn.close()


def count_active_facts(cfg: Mapping[str, Any]) -> int:
    conn = connect(cfg)
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM facts WHERE active = 1").fetchone()
        return int(row["c"]) if row else 0
    finally:
        conn.close()


def count_episodes(cfg: Mapping[str, Any]) -> int:
    conn = connect(cfg)
    try:
        row = conn.execute("SELECT COUNT(*) AS c FROM episodes").fetchone()
        return int(row["c"]) if row else 0
    finally:
        conn.close()
