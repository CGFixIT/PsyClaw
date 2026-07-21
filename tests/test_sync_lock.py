"""Focused tests for the O_EXCL single-instance sync lock (issue #587).

These pin the concurrency contract of ``sync.runner``'s lock primitive:
ownership is decided ONLY by an atomic ``os.open(O_CREAT | O_EXCL)``, and the
stale-reclaim path can never let two holders coexist. They are self-contained
(no chromadb) and runnable with ``pytest --noconftest tests/test_sync_lock.py``.

The multiprocessing race test (``test_stale_reclaim_race_single_winner``) is the
real regression for the TOCTOU: many OS processes race to reclaim one stale lock
and exactly one may win. It uses ``pathlib`` and the stdlib ``multiprocessing``
module, so it runs identically on Ubuntu and Windows.
"""

from __future__ import annotations

import multiprocessing as mp
import os
import time
from pathlib import Path

import pytest

from sync.runner import (
    _SyncLock,
    _acquire_sync_lock,
    _lock_age_sec,
    _read_lock_started_at,
    _release_sync_lock,
)
from utils.errors import SyncRuntimeError


# ---------------------------------------------------------------------------
# Single-process semantics
# ---------------------------------------------------------------------------

def test_acquire_returns_handle_and_writes_metadata(tmp_path):
    lock_path = tmp_path / "sync.lock"
    lock = _acquire_sync_lock(str(lock_path))
    assert isinstance(lock, _SyncLock)
    assert lock.path == str(lock_path)
    assert lock.pid == os.getpid()
    # pid + start ts are persisted so a later run can judge staleness from them.
    assert lock_path.read_text(encoding="utf-8").splitlines()[0] == str(os.getpid())
    assert _read_lock_started_at(str(lock_path)) == pytest.approx(lock.started_at)
    _release_sync_lock(lock)
    assert not lock_path.exists()


def test_second_acquire_blocks_while_held(tmp_path):
    lock_path = tmp_path / "sync.lock"
    first = _acquire_sync_lock(str(lock_path))
    with pytest.raises(SyncRuntimeError) as exc:
        _acquire_sync_lock(str(lock_path))
    # A fresh lock reports the path, not a "lock_dir" (the old dir-lock key).
    assert exc.value.details["lock_path"] == str(lock_path)
    _release_sync_lock(first)
    # Released -> a subsequent acquire succeeds.
    second = _acquire_sync_lock(str(lock_path))
    assert isinstance(second, _SyncLock)
    _release_sync_lock(second)


def test_context_manager_releases(tmp_path):
    lock_path = tmp_path / "sync.lock"
    with _acquire_sync_lock(str(lock_path)) as lock:
        assert isinstance(lock, _SyncLock)
        assert lock_path.exists()
    assert not lock_path.exists()


def test_release_is_idempotent_and_tolerant(tmp_path):
    lock_path = tmp_path / "sync.lock"
    lock = _acquire_sync_lock(str(lock_path))
    _release_sync_lock(lock)
    _release_sync_lock(lock)   # already gone -> no raise
    _release_sync_lock(None)   # tolerated
    _release_sync_lock(str(lock_path))  # bare-path form -> no raise


def test_stale_lock_reclaimed_via_mtime_fallback(tmp_path):
    # An empty lock file (no embedded ts) ages out via mtime; past the threshold
    # it is reclaimed without raising, and the reclaimer holds a fresh lock.
    lock_path = tmp_path / "sync.lock"
    lock_path.write_text("", encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock_path, (old, old))
    lock = _acquire_sync_lock(str(lock_path), stale_after_sec=3600)
    assert lock.pid == os.getpid()
    # Fresh lock now carries this process's metadata (age ~ 0).
    assert _lock_age_sec(str(lock_path)) < 60
    _release_sync_lock(lock)


def test_fresh_lock_not_reclaimed(tmp_path):
    # A lock younger than the threshold is a live run: never reclaimed.
    lock_path = tmp_path / "sync.lock"
    _acquire_sync_lock(str(lock_path))  # fresh, embedded ts ~ now
    with pytest.raises(SyncRuntimeError):
        _acquire_sync_lock(str(lock_path), stale_after_sec=3600)


def test_stale_reclaim_leaves_no_takeover_file(tmp_path):
    # The ephemeral takeover file used to serialize reclaim must be cleaned up.
    lock_path = tmp_path / "sync.lock"
    lock_path.write_text("", encoding="utf-8")
    old = time.time() - 7200
    os.utime(lock_path, (old, old))
    lock = _acquire_sync_lock(str(lock_path), stale_after_sec=3600)
    assert not (tmp_path / "sync.lock.takeover").exists()
    _release_sync_lock(lock)


def test_embedded_timestamp_beats_mtime_for_staleness(tmp_path):
    # A lock whose mtime looks ancient but whose embedded start ts is recent is
    # NOT stale: the recorded ts wins, so a slow filesystem/backup touch cannot
    # make a live lock look reclaimable (nor an old one look fresh).
    lock_path = tmp_path / "sync.lock"
    lock = _acquire_sync_lock(str(lock_path))  # embedded ts ~ now
    old = time.time() - 7200
    os.utime(lock_path, (old, old))            # mtime ancient, embedded ts fresh
    assert _lock_age_sec(str(lock_path)) < 60
    with pytest.raises(SyncRuntimeError):
        _acquire_sync_lock(str(lock_path), stale_after_sec=3600)
    _release_sync_lock(lock)


# ---------------------------------------------------------------------------
# Cross-process race (the #587 regression)
# ---------------------------------------------------------------------------

def _race_worker(lock_path: str, barrier: mp.Barrier, results: mp.Queue) -> None:
    """One racer: wait on the barrier, then try to reclaim the stale lock once.

    Reports ("won", pid) if it acquired the lock (and holds it briefly so a
    double-hold would overlap in time), else ("lost", pid).
    """
    try:
        barrier.wait(timeout=30)
    except Exception:  # noqa: BLE001, S110 -- barrier broke; still attempt so we don't hang
        pass
    try:
        lock = _acquire_sync_lock(lock_path, stale_after_sec=1)
    except SyncRuntimeError:
        results.put(("lost", os.getpid()))
        return
    # Hold long enough that any second concurrent winner would overlap us.
    time.sleep(0.25)
    results.put(("won", os.getpid()))
    _release_sync_lock(lock)


@pytest.mark.parametrize("n_procs", [8])
def test_stale_reclaim_race_single_winner(tmp_path, n_procs):
    # Plant a definitively-stale lock (embedded ts far in the past), then launch
    # N processes that all judge it stale and race to reclaim. Exactly ONE may
    # win -- the pre-#587 rmdir/mkdir path allowed >1 here.
    lock_path = tmp_path / "sync.lock"
    lock_path.write_text(f"{99999}\n{time.time() - 10_000:.3f}\n", encoding="utf-8")

    ctx = mp.get_context("spawn")  # spawn == identical behavior on Ubuntu + Windows
    barrier = ctx.Barrier(n_procs)
    results: mp.Queue = ctx.Queue()
    procs = [
        ctx.Process(target=_race_worker, args=(str(lock_path), barrier, results))
        for _ in range(n_procs)
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=60)

    outcomes = [results.get() for _ in range(n_procs)]
    winners = [pid for status, pid in outcomes if status == "won"]
    assert len(winners) == 1, f"expected exactly one winner, got {winners}"
