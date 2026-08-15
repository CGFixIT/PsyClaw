"""Thread-safe rate limiter with optional sqlite or Postgres persistence.

Extracted from ``gate.py`` so the limiter is a single importable unit shared
by the FastAPI gateway and its tests.

Persistence backends (all opt-in; in-memory stays the zero-config default):
  * ``db_url=None, db_path=None`` (default): pure in-memory (fast, resets on
    restart) — original behavior, untouched.
  * ``db_path="data/rate_limits.db"``: sqlite write-through; state survives
    restarts. Connection opened per write (cheap for a local file).
  * ``db_url="postgresql://…"``: Postgres write-through for multi-process /
    durable deployments. A SINGLE hardened connection is held for the limiter's
    lifetime (a TLS reconnect per request would dominate the hot path) and every
    access is serialized by the same ``threading.Lock`` as the in-memory map.

The gateway calls ``allow()`` from FastAPI's threadpool, so we keep the
threading.Lock for the hot path. When persistence is enabled we also write
through to the backend under the same lock (simple but correct).

Behavior preserved:
  * 60 requests / 60-second sliding window per client IP (configurable).
  * Idle-IP eviction.
  * Injectable clock for deterministic tests.
  * O(1) per-request persist (only the touched IP is written).

Note on scale-out: a Postgres round-trip per persisted request is heavier than a
local sqlite write. Persistence is opt-in for durability; for true multi-instance
rate limiting Redis (atomic counters + TTL) is the recommended target — not built
here. The Postgres backend exists for operators who already run Postgres for the
personality DB (see utils/personality_db.py) and want rate-limit state to survive
restarts too, without standing up Redis as a second dependency for that alone —
not for high-throughput multi-instance scale-out. See tests/test_ratelimit_postgres.py
(gated on CYCLAW_DB_URL, run by the postgres-backend CI job) for live coverage.
"""

import json
import logging
import sqlite3
import threading
import time
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

logger = logging.getLogger(__name__)


def _is_pg_dsn(value: str | None) -> bool:
    return bool(value) and (value.startswith("postgresql") or value.startswith("postgres"))


class RateLimiter:
    """Fixed-window-ish sliding rate limiter, safe under concurrent threads.

    When ``db_path`` (sqlite) or ``db_url`` (Postgres) is provided, hits are
    persisted so the limiter survives restarts. ``db_url`` takes precedence over
    ``db_path`` if both are set.
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: float = 60,
        clock: Callable[[], float] = time.time,
        db_path: str | None = None,   # set to "data/rate_limits.db" for sqlite persistence
        db_url: str | None = None,    # set to "postgresql://…" for Postgres persistence
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._last_sweep = 0.0
        self._lock = threading.Lock()
        self._pg_conn = None  # held Postgres connection (Postgres backend only)

        # Resolve the persistence backend. Postgres wins over sqlite if both given.
        if _is_pg_dsn(db_url):
            self._backend = "postgres"
            self._ph = "%s"
            self._db_url = db_url
            self._db_path = None
        elif db_path:
            self._backend = "sqlite"
            self._ph = "?"
            self._db_url = None
            self._db_path = db_path
        else:
            self._backend = None
            self._ph = "?"
            self._db_url = None
            self._db_path = None

        if self._backend:
            self._init_db()
            self._load_from_db()

    # ------------------------------------------------------------------ backends
    def _pg_connection(self):
        """Lazily open + cache the single hardened Postgres connection."""
        if self._pg_conn is None:
            import psycopg  # noqa: PLC0415 -- lazy: in-memory/sqlite installs need no driver

            from utils.personality_db import _harden_pg_conninfo

            # autocommit: each write-through persist is a standalone statement; no
            # multi-statement transaction is needed for a rate-limit cache.
            self._pg_conn = psycopg.connect(_harden_pg_conninfo(self._db_url), autocommit=True)
        return self._pg_conn

    def _ddl(self) -> str:
        if self._backend == "postgres":
            return """
                CREATE TABLE IF NOT EXISTS rate_hits (
                    ip TEXT PRIMARY KEY,
                    timestamps TEXT NOT NULL,
                    last_sweep DOUBLE PRECISION NOT NULL
                )
            """
        return """
            CREATE TABLE IF NOT EXISTS rate_hits (
                ip TEXT PRIMARY KEY,
                timestamps TEXT NOT NULL,
                last_sweep REAL NOT NULL
            )
        """

    def _upsert_sql(self) -> str:
        # noqa S608: self._ph is a fixed placeholder char ("?"/"%s"), never user
        # data — values are always bound via parameters. (Mirrors the S608 ignore
        # already applied to utils/personality.py for the same placeholder pattern.)
        if self._backend == "postgres":
            return (
                "INSERT INTO rate_hits (ip, timestamps, last_sweep) "  # noqa: S608
                f"VALUES ({self._ph}, {self._ph}, {self._ph}) "
                "ON CONFLICT (ip) DO UPDATE SET "
                "timestamps = EXCLUDED.timestamps, last_sweep = EXCLUDED.last_sweep"
            )
        return (
            "INSERT OR REPLACE INTO rate_hits (ip, timestamps, last_sweep) "  # noqa: S608
            f"VALUES ({self._ph}, {self._ph}, {self._ph})"
        )

    def _init_db(self) -> None:
        if self._backend == "postgres":
            self._pg_connection().execute(self._ddl())
            return
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        # `with sqlite3.connect(...) as conn:` only manages the transaction
        # (commit on success / rollback on exception) -- it does NOT close the
        # connection, so the object would otherwise sit until CPython's
        # refcounting GC reaps it. Cheap in practice on CPython, but explicit
        # is safer than relying on interpreter-specific GC timing.
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(self._ddl())
            conn.commit()
        finally:
            conn.close()

    def _load_from_db(self) -> None:
        if not self._backend:
            return
        if self._backend == "postgres":
            cur = self._pg_connection().execute("SELECT ip, timestamps, last_sweep FROM rate_hits")
            rows = cur.fetchall()
        else:
            conn = sqlite3.connect(self._db_path)
            try:
                rows = conn.execute("SELECT ip, timestamps, last_sweep FROM rate_hits").fetchall()
            finally:
                conn.close()
        for ip, ts_json, last_sweep in rows:
            try:
                self._hits[ip] = json.loads(ts_json)
            except (json.JSONDecodeError, TypeError, ValueError):
                # Corrupt/garbled persisted window (e.g. truncated write from a
                # prior crash). Dropping it silently erased an IP's live rate
                # limit on every restart with no trace; log it so the state
                # loss is auditable rather than invisible. Recover gracefully
                # by resetting just this IP's window to empty.
                logger.warning(
                    "Rate-limit state for IP %s is corrupt; resetting its window to empty", ip
                )
                self._hits[ip] = []
            self._last_sweep = max(self._last_sweep, last_sweep or 0.0)

    def _persist(self, client_ip: str, now: float) -> None:
        """Persist a single IP's window to the configured backend.

        Caller must hold ``self._lock``. Only the IP touched by the current
        ``allow()`` call is written — previously this rewrote the ENTIRE IP map
        on every request, turning each request into O(N) row writes (N = tracked
        IPs). Under load that was severe write amplification; one upsert for the
        touched IP is O(1).
        """
        if not self._backend:
            return
        params = (client_ip, json.dumps(self._hits[client_ip]), now)
        if self._backend == "postgres":
            self._pg_connection().execute(self._upsert_sql(), params)
            return
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(self._upsert_sql(), params)
            conn.commit()
        finally:
            conn.close()

    # --------------------------------------------------------------------- logic
    def _sweep(self, now: float) -> None:
        """Evict clients whose timestamps are all outside the window.

        Caller must hold ``self._lock``. Runs at most once per window so the
        hot path stays cheap.
        """
        if now - self._last_sweep < self.window_seconds:
            return
        self._last_sweep = now
        stale = [
            ip for ip, hits in self._hits.items()
            if all(now - t >= self.window_seconds for t in hits)
        ]
        for ip in stale:
            del self._hits[ip]

    def allow(self, client_ip: str) -> bool:
        """Return True if the request is within the limit, else False.

        The entire read-modify-write is performed under the lock.
        When persistence is enabled we also flush to the backend under the same lock.
        """
        now = self._clock()
        with self._lock:
            self._sweep(now)
            prior_len = len(self._hits[client_ip])
            recent = [t for t in self._hits[client_ip] if now - t < self.window_seconds]
            if len(recent) >= self.max_requests:
                self._hits[client_ip] = recent
                # Persist only when expiry pruning actually changed the stored
                # window. A rejected request appends no timestamp, so when
                # nothing was pruned the backend already holds exactly this
                # state — and skipping the redundant upsert removes a per-request
                # DB write under precisely the hammering/abuse condition the
                # limiter exists to make cheap.
                if len(recent) != prior_len:
                    self._persist(client_ip, now)
                return False
            recent.append(now)
            self._hits[client_ip] = recent
            self._persist(client_ip, now)
            return True

    def retry_after_sec(self, client_ip: str) -> float:
        """Seconds until the oldest in-window hit expires. 0 if under the limit.

        Read-only peek: does not record a hit and does not persist. Used by
        the harness (and callers that want a Retry-After) after allow()
        returned False.
        """
        now = self._clock()
        with self._lock:
            recent = [t for t in self._hits[client_ip] if now - t < self.window_seconds]
            if len(recent) < self.max_requests:
                return 0.0
            return max(0.0, self.window_seconds - (now - min(recent)))

    def tracked_ips(self) -> int:
        """Number of IPs currently held in the map (for eviction tests/metrics)."""
        with self._lock:
            return len(self._hits)

    def close(self) -> None:
        """Close the held Postgres connection, if any (no-op for sqlite/in-memory)."""
        if self._pg_conn is not None:
            try:
                self._pg_conn.close()
            finally:
                self._pg_conn = None
