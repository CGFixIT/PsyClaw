"""Two OS processes racing create_user on one SQLite users file (issue #1252 leftover 2).

Integrity is already held by the username PK. Classification tests in
test_authn_manager.py blind the pre-check SELECT. This file pins the real
cross-process path: two AuthManager instances in two processes, one DB file,
no mocked INSERT. The remaining window is intentional constraint arbitration,
not an integrity defect. Do not add ON CONFLICT or a process flock here.
"""

from __future__ import annotations

import multiprocessing as mp
import sqlite3
from queue import Empty

import pytest

from utils.authn_manager import AuthManager
from utils.errors import AuthUserExists

_GOOD_PASSWORD = "correct horse battery staple"
_CHILD_TIMEOUT_SEC = 15
_USERNAME = "bob"


def _create_user_worker(
    db_path: str,
    username: str,
    password: str,
    barrier: object,
    results: object,
) -> None:
    """Child entry: own AuthManager, same file, report ok / exists / error."""
    try:
        mgr = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
    except Exception as exc:  # noqa: BLE001 -- child must always report
        results.put(("error", type(exc).__name__))
        return
    try:
        # Blind the pre-check SELECT so both children reach INSERT. The PK is
        # the arbiter (same trick as test_authn_manager classification tests).
        # Without this, SQLite usually lets the first writer commit before the
        # second SELECT, so the unique-violation branch is never taken.
        mgr._sql_get_user = mgr._sql_get_user + " AND 0 = 1"
        barrier.wait(timeout=_CHILD_TIMEOUT_SEC)
        mgr.create_user(username, password)
        results.put(("ok", None))
    except AuthUserExists as exc:
        results.put(("exists", exc.code))
    except Exception as exc:  # noqa: BLE001 -- untyped IntegrityError is the fail
        results.put(("error", type(exc).__name__))
    finally:
        mgr.close()


def test_two_processes_one_username_exactly_one_row(tmp_path):
    """Winner commits; loser is AuthUserExists; one row. Never a hung connection."""
    db_path = str(tmp_path / "auth.db")
    parent = AuthManager({"auth": {"enabled": True, "db_path": db_path}})
    try:
        parent.bootstrap_if_empty()
    finally:
        parent.close()

    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(2)
    results: mp.Queue = ctx.Queue()
    procs = [
        ctx.Process(
            target=_create_user_worker,
            args=(db_path, _USERNAME, _GOOD_PASSWORD, barrier, results),
        )
        for _ in range(2)
    ]
    for proc in procs:
        proc.start()

    outcomes: list[tuple[str, str | None]] = []
    for _ in range(2):
        try:
            outcomes.append(results.get(timeout=_CHILD_TIMEOUT_SEC))
        except Empty:
            pytest.fail("child did not report within timeout")

    for proc in procs:
        proc.join(timeout=_CHILD_TIMEOUT_SEC)
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
            pytest.fail("child exceeded hard timeout")
        assert proc.exitcode == 0, f"child exit {proc.exitcode}"

    kinds = [kind for kind, _payload in outcomes]
    assert kinds.count("ok") == 1, outcomes
    assert kinds.count("exists") == 1, outcomes
    assert "error" not in kinds, outcomes
    exists_code = next(payload for kind, payload in outcomes if kind == "exists")
    assert exists_code == "AUTH_USER_EXISTS"

    conn = sqlite3.connect(db_path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM users WHERE username = ?",
            (_USERNAME,),
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 1
