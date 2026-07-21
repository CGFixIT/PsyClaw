"""Focused cross-platform tests for the sync runner's OS-backed lock."""

from __future__ import annotations

import multiprocessing as mp
import os

import pytest

from sync.runner import _SyncLock, _acquire_sync_lock, _release_sync_lock
from utils.errors import SyncRuntimeError


def test_acquire_holds_descriptor_and_writes_metadata(tmp_path):
    lock_path = tmp_path / "sync.lock"
    lock = _acquire_sync_lock(str(lock_path))

    assert isinstance(lock, _SyncLock)
    assert lock.fd is not None
    assert lock.pid == os.getpid()
    assert lock_path.read_text(encoding="utf-8").splitlines() == [
        str(os.getpid()),
        f"{lock.started_at:.3f}",
    ]

    _release_sync_lock(lock)
    assert lock.fd is None
    assert lock_path.exists()
    assert lock_path.read_bytes() == b""


def test_second_acquire_blocks_while_held(tmp_path):
    lock_path = tmp_path / "sync.lock"
    first = _acquire_sync_lock(str(lock_path))
    try:
        with pytest.raises(SyncRuntimeError) as exc:
            _acquire_sync_lock(str(lock_path))
        assert exc.value.details["lock_path"] == str(lock_path)
    finally:
        _release_sync_lock(first)

    second = _acquire_sync_lock(str(lock_path))
    _release_sync_lock(second)


def test_context_manager_releases(tmp_path):
    lock_path = tmp_path / "sync.lock"
    with _acquire_sync_lock(str(lock_path)) as lock:
        assert lock.fd is not None

    assert lock.fd is None
    assert lock_path.read_bytes() == b""


def test_release_is_idempotent(tmp_path):
    lock = _acquire_sync_lock(str(tmp_path / "sync.lock"))
    _release_sync_lock(lock)
    _release_sync_lock(lock)
    _release_sync_lock(None)


def test_unlocked_file_from_previous_version_is_reused(tmp_path):
    lock_path = tmp_path / "sync.lock"
    lock_path.write_text("99999\n1.000\n", encoding="utf-8")

    lock = _acquire_sync_lock(str(lock_path))
    try:
        assert lock_path.read_text(encoding="utf-8").splitlines()[0] == str(os.getpid())
    finally:
        _release_sync_lock(lock)


def _race_worker(
    lock_path: str,
    barrier: mp.Barrier,
    release: mp.Event,
    results: mp.Queue,
) -> None:
    try:
        barrier.wait(timeout=30)
    except Exception:  # noqa: BLE001, S110 -- a broken barrier must not hang CI
        pass
    try:
        lock = _acquire_sync_lock(lock_path)
    except SyncRuntimeError:
        results.put(("lost", os.getpid()))
        return
    results.put(("won", os.getpid()))
    release.wait(timeout=30)
    _release_sync_lock(lock)


@pytest.mark.parametrize("n_procs", [8])
def test_cross_process_race_has_one_holder(tmp_path, n_procs):
    lock_path = tmp_path / "sync.lock"
    ctx = mp.get_context("spawn")
    barrier = ctx.Barrier(n_procs)
    release = ctx.Event()
    results: mp.Queue = ctx.Queue()
    procs = [
        ctx.Process(target=_race_worker, args=(str(lock_path), barrier, release, results))
        for _ in range(n_procs)
    ]

    for proc in procs:
        proc.start()
    outcomes = [results.get(timeout=60) for _ in range(n_procs)]
    release.set()
    for proc in procs:
        proc.join(timeout=60)

    winners = [pid for status, pid in outcomes if status == "won"]
    assert len(winners) == 1, f"expected exactly one holder, got {winners}"
    assert all(proc.exitcode == 0 for proc in procs)


def _crash_worker(lock_path: str, acquired: mp.Event) -> None:
    _acquire_sync_lock(lock_path)
    acquired.set()
    os._exit(0)


def test_process_exit_releases_lock(tmp_path):
    lock_path = tmp_path / "sync.lock"
    ctx = mp.get_context("spawn")
    acquired = ctx.Event()
    proc = ctx.Process(target=_crash_worker, args=(str(lock_path), acquired))
    proc.start()

    assert acquired.wait(timeout=30)
    proc.join(timeout=30)
    assert proc.exitcode == 0

    lock = _acquire_sync_lock(str(lock_path))
    _release_sync_lock(lock)
