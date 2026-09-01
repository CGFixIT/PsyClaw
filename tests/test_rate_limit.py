"""Unit tests for the production rate limiter (utils/ratelimit.RateLimiter).

These tests import the REAL limiter used by gate.py — not a re-implemented
copy — so a regression in the production limiter makes them fail. Timing is
driven by an injected fake clock; there is no time.sleep and no wall-clock
dependence.
"""

import sqlite3
import threading
import time
from collections import defaultdict

import pytest

from utils.ratelimit import RateLimiter

# NOTE: `gate` is imported lazily inside the two tests that need it
# (test_gate_uses_production_limiter / test_429_detail_reflects_configured_limits).
# A module-level `import gate` boots the whole FastAPI app — config load,
# HybridRetriever construction attempt, LLM clients — just to run limiter unit
# tests, which made `pytest tests/test_rate_limit.py` alone pay the full app
# startup cost.


class _SlowHits(defaultdict):
    """A timestamp map whose reads yield the GIL.

    Replacing ``RateLimiter._hits`` with this forces a context switch in the
    middle of the read-modify-write, so concurrent threads deterministically
    interleave there *unless* a real lock serializes the region. It turns the
    "missing lock" race from probabilistic into reliably observable.
    """

    def __init__(self, target_ip):
        super().__init__(list)
        self._target_ip = target_ip

    def __getitem__(self, key):
        value = super().__getitem__(key)
        if key == self._target_ip:
            # Yield AFTER snapshotting the value: concurrent threads then hold a
            # stale view across the switch, so an unguarded region overcounts.
            time.sleep(0.001)
        return value


def _persisted_ips(db_path) -> set[str]:
    """IPs currently present in the persisted rate_hits table."""
    conn = sqlite3.connect(str(db_path))
    try:
        return {row[0] for row in conn.execute("SELECT ip FROM rate_hits")}
    finally:
        conn.close()


def _row_count(db_path) -> int:
    """Rows currently in the persisted rate_hits table."""
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM rate_hits").fetchone()[0]
    finally:
        conn.close()


class FakeClock:
    """Deterministic, advanceable clock for window/eviction tests."""

    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def test_allows_under_limit():
    clock = FakeClock()
    rl = RateLimiter(max_requests=5, window_seconds=2, clock=clock)
    for i in range(5):
        assert rl.allow("10.0.0.1") is True, f"request {i + 1} should be allowed"


def test_blocks_over_limit():
    clock = FakeClock()
    rl = RateLimiter(max_requests=5, window_seconds=2, clock=clock)
    for _ in range(5):
        rl.allow("10.0.0.1")
    assert rl.allow("10.0.0.1") is False, "6th request should be blocked"


def test_retry_after_sec_is_zero_under_limit_and_positive_when_blocked():
    clock = FakeClock()
    rl = RateLimiter(max_requests=2, window_seconds=10, clock=clock)
    assert rl.retry_after_sec("10.0.0.1") == 0.0
    rl.allow("10.0.0.1")
    rl.allow("10.0.0.1")
    assert rl.allow("10.0.0.1") is False
    assert rl.retry_after_sec("10.0.0.1") == 10.0
    clock.advance(4.0)
    assert rl.retry_after_sec("10.0.0.1") == 6.0
    clock.advance(6.1)
    assert rl.retry_after_sec("10.0.0.1") == 0.0


def test_window_expiry_via_clock():
    """After the window elapses, the IP is allowed again (no time.sleep)."""
    clock = FakeClock()
    rl = RateLimiter(max_requests=5, window_seconds=2, clock=clock)
    for _ in range(5):
        rl.allow("10.0.0.1")
    assert rl.allow("10.0.0.1") is False
    clock.advance(2.1)  # past the window
    assert rl.allow("10.0.0.1") is True


def test_idle_ip_eviction():
    """Idle IPs are swept so the map cannot grow without bound."""
    clock = FakeClock()
    rl = RateLimiter(max_requests=5, window_seconds=2, clock=clock)

    # Seen 50 distinct IPs in the first window.
    for n in range(50):
        rl.allow(f"10.0.0.{n}")
    assert rl.tracked_ips() == 50

    # Advance well past the window and touch one new IP; the sweep (runs at most
    # once per window) must evict all 50 now-idle IPs, leaving only the new one.
    clock.advance(3.0)
    rl.allow("192.168.1.1")
    assert rl.tracked_ips() == 1


def test_per_ip_isolation():
    clock = FakeClock()
    rl = RateLimiter(max_requests=5, window_seconds=2, clock=clock)
    for _ in range(5):
        rl.allow("10.0.0.1")
    assert rl.allow("10.0.0.1") is False
    # A different IP is unaffected by the first IP's exhausted budget.
    assert rl.allow("10.0.0.2") is True


def test_concurrent_requests_never_overcount():
    """N threads hammer one IP with a frozen clock; exactly max_requests pass.

    With a real lock the read-modify-write cannot interleave, so the number of
    allowed requests is exactly the limit — never more. Without the lock this
    test would intermittently allow > max_requests.
    """
    clock = FakeClock()  # frozen — window never advances during the test
    limit = 50
    target_ip = "10.0.0.1"
    rl = RateLimiter(max_requests=limit, window_seconds=60, clock=clock)
    # Force a yield inside the read-modify-write so a missing lock overcounts.
    rl._hits = _SlowHits(target_ip)

    threads_count = 16
    per_thread = 25  # 16 * 25 = 400 attempts, far above the limit of 50
    allowed = []
    allowed_lock = threading.Lock()
    barrier = threading.Barrier(threads_count)

    def worker():
        barrier.wait()  # maximize contention by starting together
        local = 0
        for _ in range(per_thread):
            if rl.allow("10.0.0.1"):
                local += 1
        with allowed_lock:
            allowed.append(local)

    threads = [threading.Thread(target=worker) for _ in range(threads_count)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    total_allowed = sum(allowed)
    assert total_allowed == limit, (
        f"expected exactly {limit} allowed, got {total_allowed} "
        "(overcount indicates the lock is missing/broken)"
    )


def test_gate_uses_production_limiter():
    """gate.check_rate_limit delegates to the shared RateLimiter instance."""
    import gate  # lazy: boots the app only when this test runs
    assert isinstance(gate._rate_limiter, RateLimiter)
    # The wrapper must call through to the instance (not a private copy).
    assert gate.check_rate_limit("203.0.113.7") is True


def test_429_detail_reflects_configured_limits(monkeypatch):
    """The 429 body must quote the CONFIGURED api.rate_limit values.

    Regression guard: gate.py used to hardcode "Rate limit exceeded (60/min)"
    in six handlers, so an operator who tuned max_requests/window_seconds still
    saw the stale default in every 429.
    """
    import asyncio
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import HTTPException

    import gate  # lazy: boots the app only when this test runs

    monkeypatch.setattr(gate, "_check_rate_limit_async", AsyncMock(return_value=False))
    monkeypatch.setattr(gate, "_audit", AsyncMock())
    request = MagicMock()
    request.client.host = "203.0.113.9"

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(gate._enforce_rate_limit(request))

    assert exc_info.value.status_code == 429
    detail = exc_info.value.detail
    assert detail["code"] == "RATE_LIMIT"
    assert str(gate.RATE_LIMIT_REQUESTS) in detail["error"]
    assert str(gate.RATE_LIMIT_WINDOW) in detail["error"]
    assert "60/min" not in detail["error"]


class TestPersistence:
    """Optional sqlite persistence (api.rate_limit.persist_path in config.yaml).

    gate.py now wires ``db_path`` from config, so per-IP counters can survive a
    process/container restart instead of resetting to zero. These tests exercise
    the underlying RateLimiter persistence that wiring depends on. The fake clock
    keeps both windows pinned so the reloaded hits stay in-window.
    """

    def test_counters_survive_restart(self, tmp_path):
        db = tmp_path / "rl.db"
        now = 1000.0
        rl = RateLimiter(max_requests=3, window_seconds=60, clock=lambda: now, db_path=str(db))
        assert rl.allow("198.51.100.5") is True   # 1
        assert rl.allow("198.51.100.5") is True   # 2

        # A fresh limiter pointed at the same db reloads the prior hits, so the
        # window continues across the simulated restart rather than resetting.
        rl2 = RateLimiter(max_requests=3, window_seconds=60, clock=lambda: now, db_path=str(db))
        assert rl2.allow("198.51.100.5") is True   # 3rd hit fills the window
        assert rl2.allow("198.51.100.5") is False  # 4th exceeds max_requests=3

    def test_db_file_and_parent_created(self, tmp_path):
        db = tmp_path / "nested" / "rl.db"
        rl = RateLimiter(max_requests=5, window_seconds=60, db_path=str(db))
        rl.allow("203.0.113.9")
        assert db.exists(), "persistence db (and its parent dir) should be created on first use"

    def test_in_memory_default_has_no_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        rl = RateLimiter(max_requests=5, window_seconds=60)  # db_path=None -> in-memory
        assert rl.allow("203.0.113.10") is True
        assert rl.tracked_ips() == 1
        assert not list(tmp_path.glob("*.db")), "in-memory mode must not write a sqlite file"

    def test_multiple_ips_each_persist_independently(self, tmp_path):
        """Per-IP persistence regression guard. ``_persist`` writes only the IP
        touched by the current ``allow()`` (O(1)) instead of rewriting the whole
        map (O(N)). Each tracked IP must still have its own correctly-counted row
        that survives a restart."""
        import json
        import sqlite3

        db = tmp_path / "rl.db"
        now = 1000.0
        rl = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: now, db_path=str(db))
        assert rl.allow("1.1.1.1") is True
        assert rl.allow("2.2.2.2") is True
        assert rl.allow("1.1.1.1") is True   # 1.1.1.1 now at the limit (2 hits)

        rows = dict(
            sqlite3.connect(str(db)).execute("SELECT ip, timestamps FROM rate_hits").fetchall()
        )
        assert set(rows) == {"1.1.1.1", "2.2.2.2"}, "every touched IP gets its own row"
        assert len(json.loads(rows["1.1.1.1"])) == 2
        assert len(json.loads(rows["2.2.2.2"])) == 1

        # State per IP survives a restart independently.
        rl2 = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: now, db_path=str(db))
        assert rl2.allow("1.1.1.1") is False  # already at limit
        assert rl2.allow("2.2.2.2") is True   # had room for one more

    def test_corrupt_persisted_state_logs_and_recovers(self, tmp_path, caplog):
        """A corrupt timestamps blob must not crash startup or vanish silently.

        Previously the load swallowed any error with a bare ``except`` and reset
        the IP's window with no trace. Now the corruption is logged (auditable)
        and the limiter still recovers by resetting just that IP to an empty
        window."""
        import logging
        import sqlite3

        db = tmp_path / "rl.db"
        now = 1000.0
        rl = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: now, db_path=str(db))
        assert rl.allow("9.9.9.9") is True  # writes a valid row

        # Corrupt the persisted timestamps JSON directly in sqlite.
        with sqlite3.connect(str(db)) as conn:
            conn.execute("UPDATE rate_hits SET timestamps = ? WHERE ip = ?", ("{not json", "9.9.9.9"))
            conn.commit()

        # Reload across a simulated restart: the corrupt row is detected.
        with caplog.at_level(logging.WARNING, logger="utils.ratelimit"):
            rl2 = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: now, db_path=str(db))

        assert any("corrupt" in r.message.lower() and "9.9.9.9" in r.getMessage() for r in caplog.records), \
            "corruption must be logged with the affected IP"
        # Recovered: the window reset to empty, so the IP can make requests again.
        assert rl2.allow("9.9.9.9") is True
        assert rl2.allow("9.9.9.9") is True
        assert rl2.allow("9.9.9.9") is False  # max_requests=2 enforced on the fresh window


    def test_sweep_evicts_persisted_rows_not_just_memory(self, tmp_path):
        """Swept IPs are deleted from the backend, so the table stays bounded.

        Eviction used to be memory-only: `_sweep` dropped the entry from
        `self._hits` while the row stayed in `rate_hits` forever, so the table
        accumulated one permanent row per distinct client IP and every restart
        re-read (and JSON-parsed) all of them.
        """
        db = tmp_path / "rl.db"
        clock = FakeClock()
        rl = RateLimiter(max_requests=5, window_seconds=2, clock=clock, db_path=str(db))

        for n in range(20):
            rl.allow(f"10.0.0.{n}")
        assert _row_count(db) == 20, "each IP persists a row while its window is live"

        # Advance past the window and touch one new IP; the sweep evicts the 20
        # now-idle IPs from memory AND from the table.
        clock.advance(3.0)
        rl.allow("192.168.1.1")
        assert rl.tracked_ips() == 1
        assert _row_count(db) == 1, "swept IPs must not leave rows behind"

        # A restart therefore loads only the live IP, not every IP ever seen.
        rl2 = RateLimiter(max_requests=5, window_seconds=2, clock=clock, db_path=str(db))
        assert rl2.tracked_ips() == 1

    def test_sqlite_connection_is_reused_across_requests(self, tmp_path):
        """The sqlite handle is opened once, not per persisted request.

        Every allowed request used to pay a connect + commit-fsync + close
        inside the admission lock; the Postgres branch already cached its
        connection. Asserting the reuse directly (rather than timing it) keeps
        this deterministic.
        """
        db = tmp_path / "rl.db"
        rl = RateLimiter(max_requests=100, window_seconds=60, db_path=str(db))

        first = rl._sqlite_connection()
        for n in range(10):
            assert rl.allow(f"10.0.0.{n}") is True
        assert rl._sqlite_connection() is first, "persisting must not reopen the connection"

        # Writes still landed despite the connection never being reopened.
        assert _row_count(db) == 10

    def test_close_releases_the_sqlite_handle(self, tmp_path):
        """close() drops the cached sqlite connection (it was Postgres-only)."""
        db = tmp_path / "rl.db"
        rl = RateLimiter(max_requests=5, window_seconds=60, db_path=str(db))
        rl.allow("10.0.0.1")
        assert rl._sqlite_conn is not None

        rl.close()
        assert rl._sqlite_conn is None

        # Idempotent, and the limiter still works (it reopens on demand).
        rl.close()
        assert rl.allow("10.0.0.2") is True


    def test_stale_sweep_never_deletes_a_window_another_instance_refreshed(self, tmp_path):
        """One limiter's stale snapshot must not destroy another's live window.

        Several limiters can share one backend (agentic/fsconnect/writer.py
        builds a per-root and a global limiter against the same file, and
        separate processes can point at it too). `_hits` is a snapshot taken at
        construction, so "stale according to me" is not "stale on disk".
        Deleting by IP alone handed the budget back to a client the persisted
        limiter was actively throttling.
        """
        db = tmp_path / "rl.db"
        clock = FakeClock()
        first = RateLimiter(max_requests=5, window_seconds=10, clock=clock, db_path=str(db))
        first.allow("10.0.0.9")

        # Quiet past the window, then a second limiter loads the table -- its
        # snapshot shows 10.0.0.9 as stale.
        clock.advance(30.0)
        second = RateLimiter(max_requests=5, window_seconds=10, clock=clock, db_path=str(db))

        # The first limiter records a fresh hit: 10.0.0.9's window is live again.
        first.allow("10.0.0.9")

        # The second limiter now sweeps off its stale snapshot.
        second.allow("10.0.0.99")

        persisted = _persisted_ips(db)
        assert "10.0.0.9" in persisted, (
            "a stale snapshot deleted a window another instance had just refreshed"
        )
        assert "10.0.0.99" in persisted

    def test_row_written_exactly_at_the_window_boundary_is_evicted(self, tmp_path):
        """The DELETE boundary must match _sweep's, or the row is orphaned.

        _sweep calls a timestamp stale at `now - t >= window_seconds`
        (inclusive). An exclusive `last_sweep < threshold` in the DELETE
        disagreed at exactly `now - window_seconds`: the IP was dropped from
        `_hits` while its row was spared, so nothing could nominate it again
        and it sat on disk forever -- the unbounded growth this eviction
        exists to remove. Reachable with integer or injected clocks and
        interval-aligned calls.
        """
        db = tmp_path / "rl.db"
        clock = FakeClock()
        rl = RateLimiter(max_requests=5, window_seconds=10, clock=clock, db_path=str(db))
        rl.allow("10.0.0.7")

        # Land exactly on the boundary: last_sweep == now - window_seconds.
        clock.advance(10.0)
        rl.allow("10.0.0.8")

        assert "10.0.0.7" not in rl._hits, "sweep treats the boundary as stale (inclusive)"
        assert _persisted_ips(db) == {"10.0.0.8"}, (
            "the boundary row was dropped from memory but left on disk, where "
            "nothing can ever nominate it again"
        )

    def test_sweep_still_evicts_rows_that_are_stale_on_disk(self, tmp_path):
        """The last_sweep guard must not defeat eviction for genuinely idle IPs."""
        db = tmp_path / "rl.db"
        clock = FakeClock()
        rl = RateLimiter(max_requests=5, window_seconds=2, clock=clock, db_path=str(db))
        for n in range(20):
            rl.allow(f"10.0.0.{n}")
        clock.advance(30.0)
        rl.allow("192.168.1.1")
        assert _row_count(db) == 1, "rows stale on disk must still be evicted"

    def test_failed_backend_delete_keeps_candidates_for_the_next_sweep(self, tmp_path):
        """A raising backend must not strand rows with nothing able to retry.

        `_hits` is the only thing that can nominate a row for deletion. Evicting
        from memory before the backend delete succeeded meant a transient
        failure (sqlite contention, a Postgres blip, a full disk) left those
        rows on disk unreachable by any later sweep -- orphaned until restart,
        the same unbounded growth this eviction exists to remove.
        """
        db = tmp_path / "rl.db"
        clock = FakeClock()
        rl = RateLimiter(max_requests=5, window_seconds=2, clock=clock, db_path=str(db))
        for n in range(5):
            rl.allow(f"10.0.0.{n}")

        boom = RuntimeError("backend unavailable")

        def _raise(_ips, _now):
            raise boom

        rl._delete_rows = _raise  # type: ignore[method-assign]
        clock.advance(30.0)
        with pytest.raises(RuntimeError):
            rl.allow("192.168.1.1")

        # The candidates are still tracked, so a later sweep can retry them.
        assert {f"10.0.0.{n}" for n in range(5)} <= set(rl._hits), (
            "a failed backend delete dropped the candidates from memory, "
            "leaving their rows unreachable by any future sweep"
        )

        # The raising allow() never recorded its own hit, so the five stranded
        # rows are all that is on disk at this point.
        assert _persisted_ips(db) == {f"10.0.0.{n}" for n in range(5)}

        # Once the backend recovers, the retained candidates are swept for real.
        del rl._delete_rows  # type: ignore[attr-defined]
        clock.advance(30.0)
        rl.allow("192.168.1.2")
        assert _persisted_ips(db) == {"192.168.1.2"}, (
            "the retry after recovery must actually clear the stranded rows"
        )

    def test_postgres_batch_delete_goes_through_a_cursor(self, tmp_path):
        """psycopg 3 puts executemany on the cursor, not the connection.

        Connection has execute() but no executemany(), so calling it there
        raises AttributeError on the first sweep that finds a stale IP -- and
        the Postgres tests need a live server, so CI skips them. This stub
        mirrors psycopg's real surface (cursor-only executemany) so the misuse
        fails here instead.
        """
        executed: list[tuple[str, list]] = []

        class _FakeCursor:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def executemany(self, sql, rows):
                executed.append((sql, list(rows)))

        class _FakeConnection:
            # Deliberately no executemany here -- psycopg.Connection has none.
            def cursor(self):
                return _FakeCursor()

        rl = RateLimiter(max_requests=5, window_seconds=10, clock=FakeClock(), db_path=str(tmp_path / "x.db"))
        rl._backend = "postgres"
        rl._ph = "%s"
        rl._pg_conn = _FakeConnection()

        rl._delete_rows(["10.0.0.1", "10.0.0.2"], now=100.0)

        assert len(executed) == 1, "batch delete must issue exactly one statement"
        sql, rows = executed[0]
        assert "DELETE FROM rate_hits" in sql
        assert "last_sweep <" in sql, "the delete must stay conditional on the persisted timestamp"
        assert [r[0] for r in rows] == ["10.0.0.1", "10.0.0.2"]
