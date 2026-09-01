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
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
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

    @contextmanager
    def _sqlite_txn(self) -> Iterator[sqlite3.Connection]:
        """Run statements on the cached connection, rolling back on error.

        sqlite3's default ``isolation_level=""`` opens a transaction implicitly
        on the first DML statement, so a failed ``commit()`` (SQLITE_BUSY while
        another process holds a read transaction, a full disk) leaves this
        long-lived connection *inside* that transaction. It would keep a write
        lock other limiter instances cannot get past, and the next successful
        commit would flush the earlier failed request's hit alongside its own.

        The per-write connection this replaced was closed in a ``finally``,
        which rolled back implicitly; caching the connection removed that for
        free, so it is restored explicitly here.
        """
        conn = self._sqlite_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            with suppress(sqlite3.Error):
                conn.rollback()
            raise

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
                    last_sweep DOUBLE PRECISION NOT NULL,
                    window_seconds DOUBLE PRECISION NOT NULL DEFAULT 0
                )
            """
        return """
            CREATE TABLE IF NOT EXISTS rate_hits (
                ip TEXT PRIMARY KEY,
                timestamps TEXT NOT NULL,
                last_sweep REAL NOT NULL,
                window_seconds REAL NOT NULL DEFAULT 0
            )
        """

    def _ensure_window_seconds_column(self) -> None:
        """Add ``window_seconds`` to a table created before this schema version.

        A table freshly created by ``_ddl()`` above already has the column; this
        only matters for an existing ``data/rate_limits.db`` (or Postgres
        ``rate_hits``) from before it. Neither backend at the pins this repo
        carries has a portable ``ADD COLUMN IF NOT EXISTS``, so the idiom is:
        attempt the ALTER, and treat "the column is already there" as success
        rather than probing ``PRAGMA table_info``/``information_schema.columns``
        first -- that also makes this race-safe if two processes migrate the
        same file concurrently on first boot after an upgrade.

        ``DEFAULT 0`` for pre-existing rows is deliberate, not a guess at their
        original window: 0 makes a legacy row immediately eligible for the next
        sweep's conditional DELETE (``last_sweep + window_seconds <= now``), so
        the table converges to the per-row-policy invariant this column exists
        to provide as fast as the next sweep, rather than carrying an
        unknowable borrowed value indefinitely. The cost is one-time and
        bounded: a client already mid-window when the migration runs may see
        its budget reset early once -- never a security regression (only ever
        more permissive, and only until that IP's next real hit re-persists a
        live ``window_seconds``).
        """
        column_type = "DOUBLE PRECISION" if self._backend == "postgres" else "REAL"
        sql = f"ALTER TABLE rate_hits ADD COLUMN window_seconds {column_type} NOT NULL DEFAULT 0"
        if self._backend == "postgres":
            import psycopg  # noqa: PLC0415 -- lazy, matches _pg_connection

            try:
                self._pg_connection().execute(sql)
            except psycopg.errors.DuplicateColumn:
                pass
            return
        try:
            with self._sqlite_txn() as conn:
                conn.execute(sql)
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise

    def _upsert_sql(self) -> str:
        # noqa S608: self._ph is a fixed placeholder char ("?"/"%s"), never user
        # data — values are always bound via parameters. (Mirrors the S608 ignore
        # already applied to utils/personality.py for the same placeholder pattern.)
        #
        # window_seconds is written on every upsert, not just at row creation:
        # the row must always reflect the policy of whichever instance most
        # recently touched it, so a later config change (a different
        # window_seconds on restart) takes effect on that IP's very next hit
        # rather than being stuck with whatever wrote the row first.
        if self._backend == "postgres":
            return (
                "INSERT INTO rate_hits (ip, timestamps, last_sweep, window_seconds) "  # noqa: S608
                f"VALUES ({self._ph}, {self._ph}, {self._ph}, {self._ph}) "
                "ON CONFLICT (ip) DO UPDATE SET "
                "timestamps = EXCLUDED.timestamps, last_sweep = EXCLUDED.last_sweep, "
                "window_seconds = EXCLUDED.window_seconds"
            )
        return (
            "INSERT OR REPLACE INTO rate_hits (ip, timestamps, last_sweep, window_seconds) "  # noqa: S608
            f"VALUES ({self._ph}, {self._ph}, {self._ph}, {self._ph})"
        )

    def _init_db(self) -> None:
        if self._backend == "postgres":
            self._pg_connection().execute(self._ddl())
            self._ensure_window_seconds_column()
            return
        # The connection is opened once here (parent dir created by
        # _sqlite_connection) and reused for the object's lifetime; close()
        # releases it. It is deliberately NOT closed per statement -- see
        # _sqlite_connection's docstring for why that cost mattered.
        with self._sqlite_txn() as conn:
            conn.execute(self._ddl())
        self._ensure_window_seconds_column()

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
        params = (client_ip, json.dumps(self._hits[client_ip]), now, self.window_seconds)
        if self._backend == "postgres":
            self._pg_connection().execute(self._upsert_sql(), params)
            return
        with self._sqlite_txn() as conn:
            conn.execute(self._upsert_sql(), params)

    def _delete_rows(self, ips: list[str], now: float) -> set[str]:
        """Delete expired rows; return the candidates whose rows SURVIVED.

        A survivor is a candidate another writer refreshed after this instance
        took its snapshot: the conditional DELETE correctly matches zero rows
        for it. The caller must keep those in ``_hits``, because ``_hits`` is
        the only thing that can nominate a row for deletion -- evicting one
        whose row is still on disk means that if the refreshing instance exits,
        nothing here can ever nominate it again and it persists until restart.

        A candidate with no row at all is NOT a survivor: it is already gone,
        so the caller should drop it from memory as usual.

        Caller must hold ``self._lock``.

        ``ips`` comes from this instance's in-memory map, which is a snapshot
        taken when the instance was constructed. Several limiters can share one
        backend -- ``agentic/fsconnect/writer.py`` builds a per-root and a
        global limiter against the same file, separate processes can point at
        it too, and a rolling config change can put two DIFFERENT
        ``window_seconds`` values on the same backend simultaneously -- so
        "stale according to my snapshot" is not the same as "stale on disk",
        and "stale according to MY window" is not the same as "stale according
        to the policy that actually wrote this row".

        The delete is conditional on ``last_sweep + window_seconds <= now``,
        using the ROW's OWN persisted ``window_seconds`` (written by
        ``_persist`` under whichever instance most recently touched it) rather
        than this sweeping instance's. A row a long-window process just wrote
        therefore survives a short-window process's sweep even though it is
        already older than the short window -- it is evaluated against the
        policy that actually governs it, not the policy of whoever happens to
        run the next sweep. ``_ensure_window_seconds_column`` covers what a
        table predating this column does for rows it never wrote.

        The boundary is INCLUSIVE to match ``_sweep``, which calls a timestamp
        stale at ``now - t >= window_seconds``. With an exclusive ``<`` the two
        disagreed at exactly ``now - window_seconds``: ``_sweep`` dropped the IP
        from ``_hits`` while the DELETE spared the row, so nothing could ever
        nominate it again and it sat on disk forever -- reintroducing the
        unbounded growth this eviction exists to remove.

        Both statements are written out in full rather than built from
        ``self._ph``: an f-string here would be flagged B608/S608 (as
        ``_upsert_sql`` already is) even though the interpolated text is only a
        placeholder run. Values are always bound as parameters.
        """
        if not self._backend:
            return set()
        rows = [(ip, now) for ip in ips]
        candidates = set(ips)
        if self._backend == "postgres":
            # psycopg 3 puts executemany on the CURSOR, not the connection --
            # Connection has execute() but no executemany(), so calling it on
            # the connection raises AttributeError on the first sweep.
            with self._pg_connection().cursor() as cur:
                cur.executemany(
                    "DELETE FROM rate_hits WHERE ip = %s AND last_sweep + window_seconds <= %s", rows
                )
                cur.execute("SELECT ip FROM rate_hits")
                remaining = {row[0] for row in cur.fetchall()}
            return candidates & remaining
        with self._sqlite_txn() as conn:
            conn.executemany(
                "DELETE FROM rate_hits WHERE ip = ? AND last_sweep + window_seconds <= ?", rows
            )
        # One bounded scan rather than a per-IP probe: this table is kept small
        # by the very eviction running here, and it is the same read
        # _load_from_db already does at construction. Avoids an IN (...) clause,
        # which would need interpolated placeholders (B608/S608).
        remaining = {
            row[0] for row in self._sqlite_connection().execute("SELECT ip FROM rate_hits")
        }
        return candidates & remaining

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
        # Evict from the backend as well. Without this the in-memory map is
        # bounded but the table is not: `rate_hits` kept one row per distinct
        # client IP forever, and _load_from_db reads every row back (and
        # JSON-parses each) on construction -- so boot cost and memory grew
        # monotonically with the number of IPs ever seen. The rows deleted here
        # are precisely those whose timestamps are all outside the window, so
        # they carry no live rate-limit state for any process to lose.
        #
        # The backend delete runs BEFORE the in-memory eviction, and the memory
        # eviction only on success. `_hits` is the only thing that can nominate
        # a row for deletion, so dropping the candidates first meant a raising
        # backend (sqlite contention, a transient Postgres failure, a full
        # disk) left those rows on disk with nothing able to retry them --
        # orphaned until a restart, which is exactly the unbounded growth this
        # eviction exists to remove. Keeping them in `_hits` when the delete
        # fails means the next sweep nominates them again.
        survivors: set[str] = set()
        if stale:
            survivors = self._delete_rows(stale, now)
        for ip in stale:
            if ip in survivors:
                # Another writer refreshed this row after our snapshot, so the
                # conditional DELETE spared it. Keep tracking it: dropping it
                # here would leave the row on disk with nothing able to
                # nominate it once that writer exits.
                continue
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
