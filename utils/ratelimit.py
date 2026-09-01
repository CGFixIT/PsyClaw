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
        self._sqlite_conn: sqlite3.Connection | None = None  # held sqlite connection (sqlite backend only)

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

    def _sqlite_connection(self) -> sqlite3.Connection:
        """Lazily open + cache the single sqlite connection.

        Mirrors ``_pg_connection`` above. Previously every persisted call
        opened its own connection and closed it again, so each allowed request
        paid a file open + journal setup + ``commit()`` fsync + close -- all of
        it inside ``self._lock``, which serializes request admission behind
        that disk I/O. The window this cost lands in is exactly the one the
        limiter exists to keep cheap.

        ``check_same_thread=False`` is safe here and required: the gateway
        serves requests from a thread pool, so ``allow()`` reaches this handle
        from many threads. Every statement issued against it runs while the
        caller holds ``self._lock`` (``_init_db``/``_load_from_db`` run
        single-threaded during construction), so access stays serialized.
        """
        if self._sqlite_conn is None:
            Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
            self._sqlite_conn = sqlite3.connect(self._db_path, check_same_thread=False)
        return self._sqlite_conn

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
        # The connection is opened once here (parent dir created by
        # _sqlite_connection) and reused for the object's lifetime; close()
        # releases it. It is deliberately NOT closed per statement -- see
        # _sqlite_connection's docstring for why that cost mattered.
        conn = self._sqlite_connection()
        conn.execute(self._ddl())
        conn.commit()

    def _load_from_db(self) -> None:
        if not self._backend:
            return
        if self._backend == "postgres":
            cur = self._pg_connection().execute("SELECT ip, timestamps, last_sweep FROM rate_hits")
            rows = cur.fetchall()
        else:
            rows = self._sqlite_connection().execute(
                "SELECT ip, timestamps, last_sweep FROM rate_hits"
            ).fetchall()
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
        conn = self._sqlite_connection()
        conn.execute(self._upsert_sql(), params)
        conn.commit()

    def _delete_rows(self, ips: list[str]) -> None:
        """Delete the given IPs' persisted rows. Caller must hold ``self._lock``.

        Both statements are written out in full rather than built from
        ``self._ph``: an f-string here would be flagged B608/S608 (as
        ``_upsert_sql`` already is) even though the interpolated text is only a
        placeholder run, and executemany over a one-parameter DELETE needs no
        interpolation at all. The IPs are always bound as parameters.
        """
        if not self._backend:
            return
        rows = [(ip,) for ip in ips]
        if self._backend == "postgres":
            self._pg_connection().executemany("DELETE FROM rate_hits WHERE ip = %s", rows)
            return
        conn = self._sqlite_connection()
        conn.executemany("DELETE FROM rate_hits WHERE ip = ?", rows)
        conn.commit()

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
        # Evict from the backend as well. Without this the in-memory map is
        # bounded but the table is not: `rate_hits` kept one row per distinct
        # client IP forever, and _load_from_db reads every row back (and
        # JSON-parses each) on construction -- so boot cost and memory grew
        # monotonically with the number of IPs ever seen. The rows deleted here
        # are precisely those whose timestamps are all outside the window, so
        # they carry no live rate-limit state for any process to lose.
        if stale:
            self._delete_rows(stale)

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
        """Close the held backend connection, if any (no-op for in-memory)."""
        if self._pg_conn is not None:
            try:
                self._pg_conn.close()
            finally:
                self._pg_conn = None
        if self._sqlite_conn is not None:
            try:
                self._sqlite_conn.close()
            finally:
                self._sqlite_conn = None
