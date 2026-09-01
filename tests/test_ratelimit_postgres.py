"""Live Postgres-backend tests for utils.ratelimit.RateLimiter.

These exercise the Postgres write-through persistence path (the `# pragma`-style
branches that need a real server). They are SKIPPED unless CYCLAW_DB_URL points at
a reachable Postgres — so the default offline suite stays green with zero extra
deps, and the dedicated `postgres-backend` CI job runs them for real.

Imports only utils.ratelimit (no gate/fastapi) so the file collects in a minimal
Postgres CI environment.
"""

import json
import os

import pytest

from utils.ratelimit import RateLimiter

DSN = os.environ.get("CYCLAW_DB_URL")
pytestmark = pytest.mark.skipif(
    not (DSN and DSN.startswith("postgres")),
    reason="CYCLAW_DB_URL not set to a Postgres DSN; skipping live Postgres rate-limit tests",
)


@pytest.fixture
def clean_table():
    """Drop rate_hits before each test so cases are isolated."""
    import psycopg

    from utils.personality_db import _harden_pg_conninfo

    with psycopg.connect(_harden_pg_conninfo(DSN), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS rate_hits")
    yield
    with psycopg.connect(_harden_pg_conninfo(DSN), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS rate_hits")


def test_pg_backend_selected_and_window(clean_table):
    t = [1000.0]
    rl = RateLimiter(max_requests=3, window_seconds=60, clock=lambda: t[0], db_url=DSN)
    try:
        assert rl._backend == "postgres"
        assert rl._ph == "%s"
        assert [rl.allow("1.2.3.4") for _ in range(4)] == [True, True, True, False]
    finally:
        rl.close()


def test_pg_restart_survival(clean_table):
    """A fresh limiter loads persisted per-IP windows from Postgres."""
    t = [2000.0]
    rl1 = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: t[0], db_url=DSN)
    try:
        assert rl1.allow("9.9.9.9") is True
        assert rl1.allow("9.9.9.9") is True
        assert rl1.allow("9.9.9.9") is False  # limit reached, persisted
    finally:
        rl1.close()

    rl2 = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: t[0], db_url=DSN)
    try:
        # State survived the "restart": IP is still at the cap.
        assert rl2.allow("9.9.9.9") is False
    finally:
        rl2.close()


def test_pg_corrupt_row_recovery(clean_table):
    """A garbled timestamps cell resets just that IP's window, with a warning."""
    import psycopg

    from utils.personality_db import _harden_pg_conninfo

    # Seed the table via a limiter so the schema exists, then corrupt one row.
    seed = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: 3000.0, db_url=DSN)
    seed.close()
    with psycopg.connect(_harden_pg_conninfo(DSN), autocommit=True) as conn:
        conn.execute(
            "INSERT INTO rate_hits (ip, timestamps, last_sweep) VALUES (%s, %s, %s) "
            "ON CONFLICT (ip) DO UPDATE SET timestamps = EXCLUDED.timestamps",
            ("7.7.7.7", "{not valid json", 3000.0),
        )

    rl = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: 3000.0, db_url=DSN)
    try:
        # Corrupt window was reset to empty on load → request is allowed again.
        assert rl.allow("7.7.7.7") is True
    finally:
        rl.close()


def test_pg_hardening_applied(clean_table):
    """The held Postgres connection carries the hardened session settings (WS1)."""
    rl = RateLimiter(max_requests=2, window_seconds=60, clock=lambda: 4000.0, db_url=DSN)
    try:
        conn = rl._pg_connection()
        app_name = conn.execute("SELECT current_setting('application_name')").fetchone()[0]
        assert app_name == "cyclaw"
        stmt_timeout = conn.execute("SHOW statement_timeout").fetchone()[0]
        # 5000ms server-side statement_timeout, rendered as "5s" by Postgres.
        assert stmt_timeout in ("5000ms", "5s")
        # sanity: persisted JSON round-trips
        rl.allow("5.5.5.5")
        row = conn.execute("SELECT timestamps FROM rate_hits WHERE ip = %s", ("5.5.5.5",)).fetchone()
        assert isinstance(json.loads(row[0]), list)
    finally:
        rl.close()


def test_pg_migrates_a_table_created_before_the_window_seconds_column(clean_table):
    """A pre-existing (old-schema) rate_hits table must migrate, not crash.

    Simulates a Postgres deployment that already ran this limiter before
    window_seconds was added: creates the OLD 3-column shape directly, seeds
    one row the way the old code would have, then constructs a RateLimiter
    against it exactly as a real upgraded process would on its next boot.
    """
    import psycopg

    from utils.personality_db import _harden_pg_conninfo

    with psycopg.connect(_harden_pg_conninfo(DSN), autocommit=True) as conn:
        conn.execute("DROP TABLE IF EXISTS rate_hits")
        conn.execute(
            "CREATE TABLE rate_hits (ip TEXT PRIMARY KEY, timestamps TEXT NOT NULL, "
            "last_sweep DOUBLE PRECISION NOT NULL)"
        )
        conn.execute(
            "INSERT INTO rate_hits (ip, timestamps, last_sweep) VALUES (%s, %s, %s)",
            ("legacy.ip", "[500.0]", 500.0),
        )

    rl = RateLimiter(max_requests=5, window_seconds=60, clock=lambda: 1000.0, db_url=DSN)
    try:
        assert "legacy.ip" in rl._hits, "the pre-existing row must still load"
        conn = rl._pg_connection()
        row = conn.execute(
            "SELECT window_seconds FROM rate_hits WHERE ip = %s", ("legacy.ip",)
        ).fetchone()
        assert row == (0.0,), "a migrated legacy row must default to window_seconds=0"

        # Constructing a SECOND limiter against the now-migrated table must not
        # raise on the column already existing (idempotent migration).
        rl2 = RateLimiter(max_requests=5, window_seconds=60, clock=lambda: 1000.0, db_url=DSN)
        rl2.close()
    finally:
        rl.close()


def test_pg_a_row_survives_a_shorter_window_sweep_from_another_process(clean_table):
    """A long-window process's row must not be evicted by a short-window one.

    Two RateLimiter instances against the same backend with DIFFERENT
    window_seconds -- the shape of a rolling config change -- must each
    respect the OTHER's persisted policy on the shared table, not just their
    own. The long-window row must survive a sweep from the short-window
    instance even though it looks stale under the short instance's own
    window.
    """
    clock = {"t": 1000.0}

    long_proc = RateLimiter(max_requests=5, window_seconds=600, clock=lambda: clock["t"], db_url=DSN)
    try:
        long_proc.allow("10.0.0.9")
    finally:
        long_proc.close()

    clock["t"] += 70.0  # stale under a 10s window, nowhere near stale under 600s
    short_proc = RateLimiter(max_requests=5, window_seconds=10, clock=lambda: clock["t"], db_url=DSN)
    try:
        short_proc.allow("192.168.1.1")  # triggers short_proc's own sweep
        conn = short_proc._pg_connection()
        remaining = {row[0] for row in conn.execute("SELECT ip FROM rate_hits").fetchall()}
        assert "10.0.0.9" in remaining, (
            "a short-window sweep evicted a row still live under the long-window "
            "policy that actually wrote it"
        )
    finally:
        short_proc.close()


def test_pg_an_unknown_window_row_survives_a_mixed_policy_rolling_upgrade(clean_table):
    """A row with unknown (non-positive) window_seconds must never be evicted
    by elapsed time alone -- only once rewritten with a real value.

    Mirrors the sqlite-side test of the same name. window_seconds can be 0 on
    a live row (not just a freshly-migrated one) whenever a process unaware of
    the column writes it -- Postgres's own upsert only overwrites the columns
    it lists, so simulate that directly by zeroing an existing row's
    window_seconds while leaving its last_sweep fresh, exactly the state a
    coexisting pre-migration writer would leave behind. A SHORT-window (10s)
    sweeper whose in-memory snapshot predates that zeroing must not delete a
    row a LONG-window (600s) writer actually owns, at any elapsed time --
    two review rounds landed on this method before the final shape: the
    sweeper's own window as a fallback reproduced the module's original
    cross-policy bug (round 2), and a fixed grace-period fallback was still
    wrong because a legacy writer's real window can exceed any fixed
    constant this module could reasonably pick (round 3). It converges only
    once an upgraded process actually writes a real window_seconds for it.
    """
    clock = {"t": 1000.0}

    writer = RateLimiter(max_requests=5, window_seconds=600, clock=lambda: clock["t"], db_url=DSN)
    try:
        writer.allow("10.0.0.9")  # persists last_sweep=1000, window_seconds=600
    finally:
        writer.close()

    short = RateLimiter(max_requests=5, window_seconds=10, clock=lambda: clock["t"], db_url=DSN)
    assert short._hits["10.0.0.9"] == [1000.0]  # snapshot taken before the zeroing below

    # Simulate a coexisting old-code writer: fresh last_sweep, window_seconds
    # reset to 0 (the column it doesn't know exists).
    conn = short._pg_connection()
    conn.execute(
        "UPDATE rate_hits SET last_sweep = %s, window_seconds = 0 WHERE ip = %s",
        (1010.0, "10.0.0.9"),
    )

    try:
        clock["t"] = 1015.0  # 15s after short's snapshot -> its own 10s sweep gate opens
        short.allow("192.168.1.1")  # triggers short's first sweep
        remaining = {row[0] for row in conn.execute("SELECT ip FROM rate_hits").fetchall()}
        assert "10.0.0.9" in remaining, (
            "a zero-window row was evicted almost immediately after being "
            "touched, instead of surviving unconditionally while its window "
            "is unknown"
        )

        # A huge elapsed time must not evict it either -- there is no fixed
        # grace period to outlast; the row is unconditionally protected while
        # window_seconds stays non-positive.
        clock["t"] = 10_000_000.0
        short.allow("192.168.1.2")
        remaining = {row[0] for row in conn.execute("SELECT ip FROM rate_hits").fetchall()}
        assert "10.0.0.9" in remaining, (
            "an unknown-window row was evicted by elapsed time alone -- it "
            "must survive until rewritten with a real value, however long "
            "that takes"
        )

        # It converges once an upgraded process actually persists a real
        # window_seconds for it.
        writer2 = RateLimiter(max_requests=5, window_seconds=5, clock=lambda: clock["t"], db_url=DSN)
        try:
            writer2.allow("10.0.0.9")  # persists a real (positive) window_seconds
        finally:
            writer2.close()
        clock["t"] += 100.0  # well past writer2's own 5s window
        short.allow("192.168.1.3")
        remaining = {row[0] for row in conn.execute("SELECT ip FROM rate_hits").fetchall()}
        assert "10.0.0.9" not in remaining, (
            "a row must converge promptly once rewritten with a real "
            "window_seconds -- it must not still be treated as unknown"
        )
    finally:
        short.close()
