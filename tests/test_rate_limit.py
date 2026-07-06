"""Unit tests for the production rate limiter (utils/ratelimit.RateLimiter).

These tests import the REAL limiter used by gate.py — not a re-implemented
copy — so a regression in the production limiter makes them fail. Timing is
driven by an injected fake clock; there is no time.sleep and no wall-clock
dependence.
"""

import threading
import time
from collections import defaultdict

import pytest

from utils.ratelimit import RateLimiter
import gate


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
