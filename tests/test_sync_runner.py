"""Unit tests for sync.runner -- rclone wrapper.

These tests never invoke a real rclone binary: ``subprocess.run`` is mocked and
``shutil.which`` is patched to return a fake absolute path. Log-parsing tests write
real log files to tmp_path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sync.config import RcloneConfig
from sync.runner import (
    FileEvent,
    SyncResult,
    _detect_safety_abort,
    _is_rclone_internal,
    build_bisync_argv,
    build_pull_argv,
    check_rclone_version,
    hash_changed_files,
    parse_log,
    reindex_exit_code_for,
    run_sync,
)
from utils.errors import RcloneNotInstalledError, RcloneVersionError

FAKE_RCLONE = "/usr/bin/rclone"


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_cfg(tmp_path, **overrides) -> RcloneConfig:
    defaults = dict(
        remote="dropbox:CyClaw/corpus",
        local_path=str(tmp_path / "data" / "corpus"),
        direction="pull",
        include_soul=False,
        extra_excludes=(),
        max_delete=10,
        max_transfer="100M",
        bwlimit="",
        conflict_resolve="none",
        conflict_loser="pathname",
        workdir=str(tmp_path / ".bisync-workdir"),
        filter_file=str(tmp_path / "filters.txt"),
        log_path=str(tmp_path / "rclone.log"),
        log_dir=str(tmp_path),
        sync_timeout_sec=3600,
        sync_retries=0,
        retry_backoff_sec=5,
        reindex_on_change=True,
        auto_reindex=False,
        checksum=True,
        fast_list=False,
        post_sync_check=False,
    )
    defaults.update(overrides)
    return RcloneConfig(**defaults)


def _version_mock(version_str: str):
    return MagicMock(returncode=0, stdout=f"rclone v{version_str}\n", stderr="")


# ---------------------------------------------------------------------------
# check_rclone_version
# ---------------------------------------------------------------------------


def test_version_ok():
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=_version_mock("1.68.2")):
        assert check_rclone_version() == (1, 68, 2)


def test_version_newer_minor_ok():
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=_version_mock("1.70.0")):
        assert check_rclone_version() == (1, 70, 0)


def test_version_too_old_raises():
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=_version_mock("1.65.0")):
        with pytest.raises(RcloneVersionError):
            check_rclone_version()


def test_version_missing_binary_raises():
    with patch("sync.runner.shutil.which", return_value=None):
        with pytest.raises(RcloneNotInstalledError):
            check_rclone_version()


def test_version_unparseable_output_raises():
    bad = MagicMock(returncode=0, stdout="hello world", stderr="")
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=bad):
        with pytest.raises(RcloneVersionError):
            check_rclone_version()


# ---------------------------------------------------------------------------
# parse_log
# ---------------------------------------------------------------------------


def _write_log(tmp_path, text: str) -> str:
    p = tmp_path / "rclone.log"
    p.write_text(text, encoding="utf-8")
    return str(p)


def test_parse_log_added_modified_deleted(tmp_path):
    log = _write_log(
        tmp_path,
        "\n".join(
            [
                "2026/05/21 02:10:01 INFO  : notes.md: Copied (new)",
                "2026/05/21 02:10:02 INFO  : readme.md: Copied (replaced existing)",
                "2026/05/21 02:10:03 INFO  : old.md: Deleted",
            ]
        ),
    )
    events, errors = parse_log(log)
    kinds = [(e.kind, e.path) for e in events]
    assert ("added", "notes.md") in kinds
    assert ("modified", "readme.md") in kinds
    assert ("deleted", "old.md") in kinds
    assert errors == []


def test_parse_log_captures_errors(tmp_path):
    log = _write_log(
        tmp_path,
        "2026/05/21 02:10:01 ERROR : dropbox: error reading: rate limited",
    )
    _events, errors = parse_log(log)
    assert any("rate limited" in e for e in errors)


def test_parse_log_ignores_unknown_lines(tmp_path):
    log = _write_log(
        tmp_path,
        "\n".join(
            [
                "2026/05/21 02:10:01 INFO  : Starting daemon",
                "random garbage line",
                "2026/05/21 02:10:02 DEBUG : some debug output",
            ]
        ),
    )
    events, errors = parse_log(log)
    assert events == [] and errors == []


def test_parse_log_missing_file_returns_empty(tmp_path):
    events, errors = parse_log(str(tmp_path / "nope.log"))
    assert events == [] and errors == []


def test_parse_log_dedupes_checksum_double_lines(tmp_path):
    # With --checksum rclone emits BOTH "Copied (replaced existing)" and
    # "Updated modification time" for one file; both match the modified regex.
    # parse_log must dedupe by (kind, path) so event_counts stays truthful and
    # hash_changed_files does not hash the same path twice.
    log = _write_log(
        tmp_path,
        "\n".join(
            [
                "2026/05/21 02:10:01 INFO  : readme.md: Copied (replaced existing)",
                "2026/05/21 02:10:01 INFO  : readme.md: Updated modification time in destination",
                "2026/05/21 02:10:02 INFO  : notes.md: Copied (new)",
            ]
        ),
    )
    events, _ = parse_log(log)
    modified = [e for e in events if e.kind == "modified" and e.path == "readme.md"]
    assert len(modified) == 1
    assert [(e.kind, e.path) for e in events] == [
        ("modified", "readme.md"),
        ("added", "notes.md"),
    ]


# ---------------------------------------------------------------------------
# _is_rclone_internal
# ---------------------------------------------------------------------------


def test_is_rclone_internal_root_level():
    assert _is_rclone_internal(".rclone-checksum") is True
    assert _is_rclone_internal("bisync-lock") is True
    assert _is_rclone_internal("RCLONE_TESTfoo") is True


def test_is_rclone_internal_nested_components():
    assert _is_rclone_internal("sub/.rclone-cache/x") is True
    assert _is_rclone_internal("a/b/bisync-state") is True


def test_is_rclone_internal_windows_separators():
    assert _is_rclone_internal("sub\\.rclone-cache\\x") is True


def test_is_rclone_internal_normal_files_false():
    assert _is_rclone_internal("notes.md") is False
    assert _is_rclone_internal("docs/readme.md") is False
    # A normal file whose name merely CONTAINS the marker is not internal.
    assert _is_rclone_internal("my.rclone-notes.md") is False


# ---------------------------------------------------------------------------
# hash_changed_files
# ---------------------------------------------------------------------------


def test_hash_changed_files_sha256(tmp_path):
    corpus = tmp_path / "data" / "corpus"
    corpus.mkdir(parents=True)
    (corpus / "a.md").write_bytes(b"hello cyclaw")

    events = [FileEvent(kind="added", path="a.md")]
    out = hash_changed_files(events, str(corpus))
    assert out[0].sha256 is not None and len(out[0].sha256) == 64


def test_hash_changed_files_deleted_keeps_none(tmp_path):
    corpus = tmp_path / "data" / "corpus"
    corpus.mkdir(parents=True)
    events = [FileEvent(kind="deleted", path="gone.md")]
    out = hash_changed_files(events, str(corpus))
    assert out[0].sha256 is None


def test_hash_changed_files_missing_file_keeps_none(tmp_path):
    corpus = tmp_path / "data" / "corpus"
    corpus.mkdir(parents=True)
    events = [FileEvent(kind="added", path="notthere.md")]
    out = hash_changed_files(events, str(corpus))
    assert out[0].sha256 is None


def test_hash_changed_files_escape_skipped(tmp_path):
    corpus = tmp_path / "data" / "corpus"
    corpus.mkdir(parents=True)
    outside = tmp_path / "secret.txt"
    outside.write_bytes(b"top secret")
    events = [FileEvent(kind="added", path="../../secret.txt")]
    out = hash_changed_files(events, str(corpus))
    assert out[0].sha256 is None


# ---------------------------------------------------------------------------
# argv builders
# ---------------------------------------------------------------------------


def test_pull_argv_shape(tmp_path):
    cfg = _make_cfg(tmp_path)
    argv = build_pull_argv(cfg, dry_run=False, log_path="/tmp/log.txt", rclone_bin=FAKE_RCLONE)
    assert argv[0] == FAKE_RCLONE
    assert argv[1] == "copy"
    assert cfg.remote in argv and cfg.local_path in argv
    assert "--filter-from" in argv
    assert f"--max-transfer={cfg.max_transfer}" in argv
    # --max-delete is pull-irrelevant (copy never deletes); it must NOT appear.
    assert not any(a.startswith("--max-delete") for a in argv)
    assert "--checksum" in argv  # cfg.checksum=True in fixture


def test_pull_argv_dry_run(tmp_path):
    cfg = _make_cfg(tmp_path)
    argv = build_pull_argv(cfg, dry_run=True, log_path="/tmp/log.txt", rclone_bin=FAKE_RCLONE)
    assert "--dry-run" in argv


def test_pull_argv_no_checksum_when_disabled(tmp_path):
    cfg = _make_cfg(tmp_path, checksum=False)
    argv = build_pull_argv(cfg, dry_run=False, log_path="/tmp/log.txt", rclone_bin=FAKE_RCLONE)
    assert "--checksum" not in argv


def test_pull_argv_fast_list(tmp_path):
    cfg = _make_cfg(tmp_path, fast_list=True)
    argv = build_pull_argv(cfg, dry_run=False, log_path="/tmp/log.txt", rclone_bin=FAKE_RCLONE)
    assert "--fast-list" in argv


def test_bisync_argv_includes_max_delete(tmp_path):
    cfg = _make_cfg(tmp_path, direction="bisync")
    argv = build_bisync_argv(cfg, dry_run=False, log_path="/tmp/log.txt", rclone_bin=FAKE_RCLONE)
    assert argv[1] == "bisync"
    assert f"--max-delete={cfg.max_delete}" in argv
    assert f"--conflict-resolve={cfg.conflict_resolve}" in argv
    assert "--workdir" in argv


def test_bisync_argv_resync(tmp_path):
    cfg = _make_cfg(tmp_path, direction="bisync")
    argv = build_bisync_argv(cfg, dry_run=False, log_path="/tmp/log.txt", resync=True,
                             rclone_bin=FAKE_RCLONE)
    assert "--resync" in argv


def test_argv_is_list_of_str(tmp_path):
    cfg = _make_cfg(tmp_path)
    argv = build_pull_argv(cfg, dry_run=False, log_path="/tmp/log.txt", rclone_bin=FAKE_RCLONE)
    assert isinstance(argv, list)
    assert all(isinstance(a, str) for a in argv)


# ---------------------------------------------------------------------------
# SyncResult / reindex_exit_code_for
# ---------------------------------------------------------------------------


def _result(success: bool, corpus_changed: bool, aborted: bool = False) -> SyncResult:
    return SyncResult(
        success=success,
        direction="pull",
        started_at=0.0,
        finished_at=1.0,
        rclone_exit_code=0 if success else 1,
        corpus_changed=corpus_changed,
        aborted_for_safety=aborted,
    )


def test_reindex_exit_10_when_changed(tmp_path):
    cfg = _make_cfg(tmp_path, reindex_on_change=True)
    assert reindex_exit_code_for(_result(True, True), cfg) == 10


def test_reindex_exit_0_when_unchanged(tmp_path):
    cfg = _make_cfg(tmp_path, reindex_on_change=True)
    assert reindex_exit_code_for(_result(True, False), cfg) == 0


def test_reindex_exit_0_when_disabled(tmp_path):
    cfg = _make_cfg(tmp_path, reindex_on_change=False)
    assert reindex_exit_code_for(_result(True, True), cfg) == 0


def test_reindex_exit_1_on_safety_abort(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert reindex_exit_code_for(_result(False, False, aborted=True), cfg) == 1


def test_reindex_exit_2_on_other_failure(tmp_path):
    cfg = _make_cfg(tmp_path)
    assert reindex_exit_code_for(_result(False, False, aborted=False), cfg) == 2


# ---------------------------------------------------------------------------
# run_sync -- end-to-end with mocked subprocess
# ---------------------------------------------------------------------------


def _patch_audit():
    return patch("sync.runner.audit_log", lambda *_a, **_k: None)


def test_run_sync_success_with_events(tmp_path):
    cfg = _make_cfg(tmp_path)
    corpus = Path(cfg.local_path)
    corpus.mkdir(parents=True)
    (corpus / "notes.md").write_bytes(b"# notes")

    log_text = "2026/05/21 02:10:01 INFO  : notes.md: Copied (new)\n"

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        # sync call: write the log file like rclone would
        Path(cfg.log_path).write_text(log_text, encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    assert result.corpus_changed is True
    assert len(result.events) == 1
    assert result.events[0].kind == "added"
    assert result.events[0].sha256 is not None


def test_run_sync_failure_no_events(tmp_path):
    cfg = _make_cfg(tmp_path)

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=2, stdout="", stderr="something broke")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is False
    assert result.corpus_changed is False
    assert reindex_exit_code_for(result, cfg) == 2


def test_run_sync_safety_abort_detected(tmp_path):
    cfg = _make_cfg(tmp_path)

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=1, stdout="", stderr="max transfer limit reached")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is False
    assert result.aborted_for_safety is True
    assert reindex_exit_code_for(result, cfg) == 1


def test_run_sync_max_transfer_exit8_is_safety_abort(tmp_path):
    # rclone documents exit 8 as "Transfer exceeded - limit set by --max-transfer
    # reached". The fuse message goes to --log-file, NOT captured stderr, so the
    # text heuristics can miss it; the exit code must classify deterministically.
    cfg = _make_cfg(tmp_path)

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=8, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is False
    assert result.aborted_for_safety is True
    assert reindex_exit_code_for(result, cfg) == 1


def test_run_sync_bisync_direction(tmp_path):
    cfg = _make_cfg(tmp_path, direction="bisync")

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        assert argv[1] == "bisync"
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    assert result.direction == "bisync"


def test_run_sync_dry_run_label(tmp_path):
    cfg = _make_cfg(tmp_path)

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, dry_run=True, rclone_bin=FAKE_RCLONE)

    assert result.direction == "dry-run"
    assert result.dry_run is True


def test_detect_safety_abort_variants():
    assert _detect_safety_abort([], "max transfer limit reached") is True
    assert _detect_safety_abort([], "Fatal error: max-delete threshold exceeded") is True
    assert _detect_safety_abort([], "all good") is False
    assert _detect_safety_abort([], "") is False


def test_run_sync_concurrent_run_refused(tmp_path):
    # A second sync while the lock dir exists must refuse (SyncRuntimeError).
    from sync.runner import SyncRuntimeError as _SRE  # noqa: N813

    cfg = _make_cfg(tmp_path)
    lock_dir = Path(cfg.log_dir) / "sync.lock.d"
    lock_dir.mkdir(parents=True)
    # Make the lock look FRESH so it is not reclaimed as stale.
    import os
    import time

    os.utime(lock_dir, (time.time(), time.time()))

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=_version_mock("1.70.0")), \
         _patch_audit():
        with pytest.raises(_SRE):
            run_sync(cfg, rclone_bin=FAKE_RCLONE)


def test_run_sync_stale_lock_reclaimed(tmp_path):
    # A lock dir older than the stale threshold must be reclaimed, not wedge sync.
    cfg = _make_cfg(tmp_path)
    lock_dir = Path(cfg.log_dir) / "sync.lock.d"
    lock_dir.mkdir(parents=True)
    import os
    import time

    old = time.time() - (4 * 60 * 60)  # 4h ago > 3h stale threshold
    os.utime(lock_dir, (old, old))

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    assert not lock_dir.exists()  # released at the end


def test_lock_stale_after_scales_with_timeout(tmp_path):
    from sync.runner import _LOCK_STALE_SEC, _lock_stale_after_sec

    # A bounded run may hold the lock for sync_timeout_sec + margin; the stale
    # threshold must clear that or a live long run looks crashed.
    cfg_default = _make_cfg(tmp_path)  # sync_timeout_sec=3600 (default)
    assert _lock_stale_after_sec(cfg_default) >= 3600
    # A longer configured timeout pushes the threshold out further.
    cfg_long = _make_cfg(tmp_path, sync_timeout_sec=7200)
    assert _lock_stale_after_sec(cfg_long) >= 7200
    # Unbounded (0) keeps the flat 3h floor (degraded, warned at run start).
    cfg_unbounded = _make_cfg(tmp_path, sync_timeout_sec=0)
    assert _lock_stale_after_sec(cfg_unbounded) == _LOCK_STALE_SEC
    # A short timeout never drops below the floor.
    cfg_short = _make_cfg(tmp_path, sync_timeout_sec=60)
    assert _lock_stale_after_sec(cfg_short) >= _LOCK_STALE_SEC


def test_lock_stale_after_doubles_when_post_sync_check_enabled(tmp_path):
    # The check runs under the same lock with its OWN full sync_timeout_sec
    # budget, so the lock-held worst case is 2x the sync budget + margin.
    from sync.runner import _LOCK_STALE_MARGIN_SEC, _lock_stale_after_sec

    cfg = _make_cfg(tmp_path, sync_timeout_sec=3600, post_sync_check=True)
    assert _lock_stale_after_sec(cfg) == 3600 * 2 + _LOCK_STALE_MARGIN_SEC


def test_run_sync_releases_lock_on_success(tmp_path):
    cfg = _make_cfg(tmp_path)
    lock_dir = Path(cfg.log_dir) / "sync.lock.d"

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        assert lock_dir.exists()  # held during the run
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert not lock_dir.exists()


# ---------------------------------------------------------------------------
# Retry loop (sync_retries / retry_backoff_sec)
# ---------------------------------------------------------------------------


def test_run_sync_retries_on_transient_exit5(tmp_path):
    # exit 5 (rclone "temporary error") with retries configured must re-run and
    # succeed on the second attempt.
    cfg = _make_cfg(tmp_path, sync_retries=2, retry_backoff_sec=0)
    calls = {"sync": 0}

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        calls["sync"] += 1
        Path(cfg.log_path).write_text("", encoding="utf-8")
        rc = 5 if calls["sync"] == 1 else 0
        return MagicMock(returncode=rc, stdout="", stderr="transient" if rc else "")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert calls["sync"] == 2
    assert result.success is True


def test_run_sync_no_retry_on_deterministic_failure(tmp_path):
    # exit 2 is NOT transient: even with retries configured it must not re-run.
    cfg = _make_cfg(tmp_path, sync_retries=3, retry_backoff_sec=0)
    calls = {"sync": 0}

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        calls["sync"] += 1
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=2, stdout="", stderr="fatal")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert calls["sync"] == 1
    assert result.success is False


def test_run_sync_retry_exhaustion_surfaces_last_result(tmp_path):
    # All attempts transient -> the final SyncResult reflects the last exit code.
    cfg = _make_cfg(tmp_path, sync_retries=2, retry_backoff_sec=0)
    calls = {"sync": 0}

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        calls["sync"] += 1
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=5, stdout="", stderr="still down")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert calls["sync"] == 3  # 1 + 2 retries
    assert result.success is False
    assert result.rclone_exit_code == 5


def test_run_sync_backoff_sleep_called(tmp_path):
    cfg = _make_cfg(tmp_path, sync_retries=1, retry_backoff_sec=7)
    sleeps: list[float] = []

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(cfg.log_path).write_text("", encoding="utf-8")
        if not sleeps:
            return MagicMock(returncode=5, stdout="", stderr="boom")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         patch("sync.runner.time.sleep", side_effect=lambda s: sleeps.append(s)), \
         _patch_audit():
        run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert sleeps == [7]


def test_run_sync_truncates_log_between_attempts(tmp_path):
    # A successful retry must not inherit the failed attempt's parsed events.
    cfg = _make_cfg(tmp_path, sync_retries=1, retry_backoff_sec=0)
    calls = {"sync": 0}

    def fake_run(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        calls["sync"] += 1
        if calls["sync"] == 1:
            Path(cfg.log_path).write_text(
                "2026/05/21 02:10:01 INFO  : ghost.md: Copied (new)\n", encoding="utf-8"
            )
            return MagicMock(returncode=5, stdout="", stderr="transient")
        # rclone appends; runner must have truncated, so the file starts empty and
        # this attempt writes nothing (no changes on retry).
        existing = Path(cfg.log_path).read_text(encoding="utf-8")
        assert "ghost.md" not in existing  # truncation happened before this attempt
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=fake_run), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    # Change evidence IS cumulative across attempts: ghost.md was copied by
    # attempt 1 before it failed, so it must still surface (see codex finding).
    assert [e.path for e in result.events] == ["ghost.md"]
    assert result.corpus_changed is True


# ---------------------------------------------------------------------------
# Post-sync integrity check
# ---------------------------------------------------------------------------

from sync.runner import CheckResult, build_check_argv, run_post_sync_check  # noqa: E402
from utils.errors import SyncRuntimeError  # noqa: E402


def test_build_check_argv_shape(tmp_path):
    cfg = _make_cfg(tmp_path)
    argv = build_check_argv(cfg, rclone_bin=FAKE_RCLONE)
    assert argv[0] == FAKE_RCLONE
    assert argv[1] == "check"
    assert cfg.remote in argv and cfg.local_path in argv
    assert "--filter-from" in argv
    assert "--checksum" in argv  # fixture has checksum=True


def test_build_check_argv_no_checksum(tmp_path):
    cfg = _make_cfg(tmp_path, checksum=False)
    argv = build_check_argv(cfg, rclone_bin=FAKE_RCLONE)
    assert "--checksum" not in argv


def test_run_post_sync_check_clean(tmp_path):
    cfg = _make_cfg(tmp_path)
    check_out = (
        "2026/06/20 00:00:00 INFO  : 0 differences found\n"
        "2026/06/20 00:00:00 INFO  : Found 0 missing on Local\n"
        "2026/06/20 00:00:00 INFO  : Found 0 missing on Remote\n"
    )
    with patch("sync.runner.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="", stderr=check_out)), \
         _patch_audit():
        cr = run_post_sync_check(cfg, rclone_bin=FAKE_RCLONE)
    assert cr.ok is True
    assert cr.differences == 0
    assert cr.missing_local == 0 and cr.missing_remote == 0


def test_run_post_sync_check_differences_detected(tmp_path):
    cfg = _make_cfg(tmp_path)
    check_out = (
        "2026/06/20 00:00:00 NOTICE: notes.md: sizes differ\n"
        "2026/06/20 00:00:00 INFO  : Found 1 missing on Local\n"
        "2026/06/20 00:00:00 INFO  : 1 differences found\n"
    )
    with patch("sync.runner.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="", stderr=check_out)), \
         _patch_audit():
        cr = run_post_sync_check(cfg, rclone_bin=FAKE_RCLONE)
    assert cr.ok is False
    assert cr.differences == 1
    assert cr.missing_local == 1
    assert "notes.md" in cr.errors[0]


def test_run_post_sync_check_timeout_raises_sync_runtime_error(tmp_path):
    cfg = _make_cfg(tmp_path, sync_timeout_sec=1)
    with patch("sync.runner.subprocess.run",
               side_effect=subprocess.TimeoutExpired(cmd=["rclone"], timeout=1)):
        with pytest.raises(SyncRuntimeError) as exc:
            run_post_sync_check(cfg, rclone_bin=FAKE_RCLONE)
    assert exc.value.details.get("op") == "check"


def test_run_sync_calls_check_on_success_when_configured(tmp_path):
    cfg = _make_cfg(tmp_path, post_sync_check=True)
    log_path = cfg.log_path

    check_output = (
        "2026/06/20 00:00:00 INFO  : 0 differences found\n"
        "2026/06/20 00:00:00 INFO  : Found 0 missing on Local\n"
        "2026/06/20 00:00:00 INFO  : Found 0 missing on Remote\n"
    )

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        if argv[1] == "check":
            return MagicMock(returncode=0, stdout="", stderr=check_output)
        # sync call
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch) as mrun, \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.check_result is not None
    assert result.check_result.ok is True
    # subprocess.run should have been called 3 times: version + sync + check.
    assert mrun.call_count == 3
    check_calls = [c for c in mrun.call_args_list if c.args[0][1] == "check"]
    assert len(check_calls) == 1


def test_run_sync_skips_check_on_dry_run(tmp_path):
    cfg = _make_cfg(tmp_path, post_sync_check=True)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch) as mrun, \
         _patch_audit():
        result = run_sync(cfg, dry_run=True, rclone_bin=FAKE_RCLONE)

    assert result.check_result is None  # dry_run -> no check
    check_calls = [c for c in mrun.call_args_list if c.args[0][1] == "check"]
    assert len(check_calls) == 0


def test_run_sync_skips_check_when_sync_failed(tmp_path):
    cfg = _make_cfg(tmp_path, post_sync_check=True)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        # sync fails with exit 2 (not a transient code -> no retry)
        return MagicMock(returncode=2, stdout="", stderr="fatal error")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch) as mrun, \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is False
    assert result.check_result is None  # failure -> no check
    check_calls = [c for c in mrun.call_args_list if c.args[0][1] == "check"]
    assert len(check_calls) == 0


def test_sync_result_audit_includes_check_summary(tmp_path):
    cr = CheckResult(ok=False, missing_local=1, missing_remote=0, differences=1)
    result = SyncResult(
        success=True, direction="pull", started_at=0.0, finished_at=1.0,
        rclone_exit_code=0, check_result=cr,
    )
    d = result.to_audit_dict()
    assert "check" in d
    assert d["check"]["ok"] is False
    assert d["check"]["differences"] == 1


# ---------------------------------------------------------------------------
# Safety-abort classifier — must accept real rclone fuse phrasings AND reject
# lines that merely mention the flag name with no trip word (the false-positive
# class the pre-fix substring scan misclassified as safety aborts).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "haystack",
    [
        # Real rclone --max-transfer fuse phrasings.
        "max transfer limit reached",
        "max transfer reached as set by --max-transfer",
        # Real rclone --max-delete fuse phrasings.
        "Fatal error: max-delete threshold exceeded",
        "max delete limit reached",
        "abort: too many deletes",
        # Variants with extra prefix/uppercase.
        "ERROR: maximum-transfer limit reached for remote",
    ],
)
def test_detect_safety_abort_accepts_real_phrasings(haystack):
    assert _detect_safety_abort([], haystack) is True


@pytest.mark.parametrize(
    "haystack",
    [
        # An argv print or config dump that mentions the flag without a trip word.
        "rclone copy --max-delete=10 --max-transfer=100M dropbox:/x /tmp/y",
        # A diagnostic that just lists configured limits.
        "Using max-transfer=100M as configured in config.yaml",
        # An unrelated error that happens to contain the substring.
        "max-delete is supported; --transfer-rate=2M was honoured",
        # No mention at all.
        "Fatal error: connection refused",
    ],
)
def test_detect_safety_abort_rejects_false_positives(haystack):
    assert _detect_safety_abort([], haystack) is False


def test_detect_safety_abort_scans_errors_too(tmp_path):
    """The errors list (parsed log lines) must also be scanned, not just stderr."""
    assert _detect_safety_abort(
        ["plain message", "abort: too many deletes during sync"], stderr=""
    ) is True


# ---------------------------------------------------------------------------
# Lock-budget enforcement — when sync_timeout_sec > 0 the wall-clock budget
# spans the ENTIRE retry sequence so the single-instance lock never holds for
# more than the documented ceiling. Pre-fix each retry got its own full
# timeout, so attempts * sync_timeout_sec was possible.
# ---------------------------------------------------------------------------


def test_run_sync_timeout_budget_is_global_across_retries(tmp_path):
    """A transient exit-5 retry path receives a SHRINKING per-attempt timeout
    that cannot exceed the remaining global budget. Pre-fix every attempt
    received the full cfg.sync_timeout_sec, so the lock could be held for
    attempts * sync_timeout_sec + sum(backoff)."""
    cfg = _make_cfg(tmp_path, sync_timeout_sec=10, sync_retries=2, retry_backoff_sec=0)
    log_path = cfg.log_path
    seen_timeouts: list[float] = []

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        timeout_val = kwargs.get("timeout")
        seen_timeouts.append(float(timeout_val) if timeout_val is not None else -1.0)
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        # Two consecutive transient exit-5 results, then a clean exit on the third.
        if len(seen_timeouts) <= 2:
            return MagicMock(returncode=5, stdout="", stderr="transient")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert len(seen_timeouts) == 3
    # Every timeout passed to subprocess.run must be inside the global budget.
    for t in seen_timeouts:
        assert 0 < t <= cfg.sync_timeout_sec
    # And the budget shrinks monotonically across retries (each attempt has
    # less wall-clock budget left than the previous one).
    assert seen_timeouts[0] >= seen_timeouts[1] >= seen_timeouts[2]


def test_run_sync_unbounded_timeout_disables_budget(tmp_path):
    """sync_timeout_sec=0 (the documented unbounded escape hatch) must keep
    its old per-attempt None semantics — no budget tracking, no clipping."""
    cfg = _make_cfg(tmp_path, sync_timeout_sec=0, sync_retries=1, retry_backoff_sec=0)
    log_path = cfg.log_path
    seen_timeouts: list[object] = []

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        seen_timeouts.append(kwargs.get("timeout", "MISSING"))
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        if len(seen_timeouts) == 1:
            return MagicMock(returncode=5, stdout="", stderr="transient")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        run_sync(cfg, rclone_bin=FAKE_RCLONE)

    # Both attempts receive None (unbounded).
    assert seen_timeouts == [None, None]


# ---------------------------------------------------------------------------
# Retry change-evidence accumulation + audit convergence (codex findings)
# ---------------------------------------------------------------------------

def test_run_sync_retry_preserves_change_evidence_across_attempts(tmp_path):
    # Attempt 1 copies notes.md and THEN fails transiently (exit 5); the clean
    # retry sees the file already present and logs nothing. If only the final
    # attempt's log were parsed, corpus_changed would flip back to False and
    # the reindex the copied content needs would never be requested.
    cfg = _make_cfg(tmp_path, sync_retries=1, retry_backoff_sec=0)
    log_path = cfg.log_path
    calls = {"sync": 0}
    captured: list[dict] = []

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        calls["sync"] += 1
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        if calls["sync"] == 1:
            Path(log_path).write_text(
                "2026/06/20 02:10:01 INFO  : notes.md: Copied (new)\n",
                encoding="utf-8",
            )
            return MagicMock(returncode=5, stdout="", stderr="temporary error")
        Path(log_path).write_text("", encoding="utf-8")  # clean retry: nothing to transfer
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         patch("sync.runner.audit_log", side_effect=lambda e: captured.append(e)):
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert calls["sync"] == 2
    assert result.success is True
    assert result.corpus_changed is True  # False when only the final log is parsed
    assert reindex_exit_code_for(result, cfg) == cfg.REINDEX_EXIT_CODE == 10
    assert result.errors == []  # transient noise from attempt 1 stays out
    assert [e.path for e in result.events] == ["notes.md"]
    assert any(
        e.get("event") == "sync_file_added" and e.get("file") == "notes.md" for e in captured
    )


def test_run_sync_retry_dedups_repeat_events_for_same_path(tmp_path):
    # A file touched by two attempts is reported once, with the EARLIEST event
    # (the attempt that actually changed the content), not the retry's replay.
    cfg = _make_cfg(tmp_path, sync_retries=1, retry_backoff_sec=0)
    log_path = cfg.log_path
    calls = {"sync": 0}

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        calls["sync"] += 1
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        if calls["sync"] == 1:
            Path(log_path).write_text(
                "2026/06/20 02:10:01 INFO  : notes.md: Copied (new)\n", encoding="utf-8"
            )
            return MagicMock(returncode=5, stdout="", stderr="temporary error")
        Path(log_path).write_text(
            "2026/06/20 02:10:02 INFO  : notes.md: Copied (replaced existing)\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert [(e.kind, e.path) for e in result.events] == [("added", "notes.md")]


def test_run_sync_exception_emits_terminal_sync_failed_audit(tmp_path):
    # Audit convergence: a run that emitted sync_started must ALSO emit a
    # terminal record when it exits by raising (here: rclone hang ->
    # SyncRuntimeError). The record is sanitized: error type only.
    cfg = _make_cfg(tmp_path, sync_timeout_sec=60)
    captured: list[dict] = []

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         patch("sync.runner.audit_log", side_effect=lambda e: captured.append(e)):
        with pytest.raises(SyncRuntimeError):
            run_sync(cfg, rclone_bin=FAKE_RCLONE)

    events = [e.get("event") for e in captured]
    assert "sync_started" in events
    failed = [e for e in captured if e.get("event") == "sync_failed"]
    assert failed and failed[0]["error_type"] == "SyncRuntimeError"
    assert "rclone_exit_code" not in failed[0]  # no half-built result fields


def test_run_sync_failed_result_emits_exactly_one_terminal_audit(tmp_path):
    # A non-raising failure already ends in the summary sync_failed; the
    # convergence wrapper must not double-emit on top of it.
    cfg = _make_cfg(tmp_path)
    captured: list[dict] = []

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(cfg.log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(cfg.log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=1, stdout="", stderr="generic failure")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         patch("sync.runner.audit_log", side_effect=lambda e: captured.append(e)):
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is False
    terminal = [e for e in captured if e.get("event") in ("sync_completed", "sync_failed")]
    assert len(terminal) == 1 and terminal[0]["event"] == "sync_failed"
