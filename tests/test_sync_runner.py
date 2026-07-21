"""Self-contained unit tests for sync.runner (no network, no real rclone).

Runnable with ``pytest --noconftest tests/test_sync_runner.py`` -- these tests do
NOT depend on tests/conftest.py fixtures (which import chromadb). The rclone
subprocess boundary is mocked via ``sync.runner.subprocess.run`` and the binary
resolution via ``sync.runner.shutil.which``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sync.config import RcloneConfig
from sync.runner import (
    _LOCK_STALE_MARGIN_SEC,
    _LOCK_STALE_SEC,
    MIN_RCLONE_MAJOR,
    MIN_RCLONE_MINOR,
    MIN_RCLONE_PATCH,
    CheckResult,
    FileEvent,
    SyncResult,
    _acquire_sync_lock,
    _detect_safety_abort,
    _lock_stale_after_sec,
    build_bisync_argv,
    build_check_argv,
    build_pull_argv,
    check_rclone_version,
    hash_changed_files,
    parse_log,
    reindex_exit_code_for,
    run_post_sync_check,
    run_sync,
)
from utils.errors import RcloneNotInstalledError, RcloneTimeoutError, RcloneVersionError, SyncRuntimeError
from utils.logger import reset_config_cache

# shutil.which returns a drive-letter absolute path on Windows; POSIX path on Linux.
FAKE_RCLONE = r"C:\Windows\rclone.exe" if sys.platform == "win32" else "/usr/bin/rclone"


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_cfg(tmp_path: Path, **overrides) -> RcloneConfig:
    """Build an RcloneConfig pointed entirely at tmp_path (no repo writes)."""
    corpus = Path(__file__).resolve().parent.parent / "data" / "corpus"
    kwargs: dict = {
        "local_path": str(corpus),
        "filter_file": str(tmp_path / "filters.txt"),
        "log_dir": str(tmp_path / "logs"),
        "workdir": str(tmp_path / "workdir"),
    }
    kwargs.update(overrides)
    return RcloneConfig(**kwargs)


def _version_mock(version: str) -> MagicMock:
    return MagicMock(returncode=0, stdout=f"rclone v{version}\n", stderr="")


# ---------------------------------------------------------------------------
# Version gate
# ---------------------------------------------------------------------------

def test_min_version_floor_is_1_68_2():
    assert (MIN_RCLONE_MAJOR, MIN_RCLONE_MINOR, MIN_RCLONE_PATCH) == (1, 68, 2)


def test_version_ok():
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=_version_mock("1.68.2")):
        assert check_rclone_version() == (1, 68, 2)


def test_version_newer_ok():
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=_version_mock("1.72.1")):
        assert check_rclone_version() == (1, 72, 1)


def test_version_too_old_raises():
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=_version_mock("1.68.1")):
        with pytest.raises(RcloneVersionError):
            check_rclone_version()


def test_version_old_minor_raises():
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=_version_mock("1.65.0")):
        with pytest.raises(RcloneVersionError):
            check_rclone_version()


def test_missing_binary_raises():
    with patch("sync.runner.shutil.which", return_value=None):
        with pytest.raises(RcloneNotInstalledError):
            check_rclone_version()


def test_unparseable_version_raises():
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=MagicMock(returncode=0, stdout="garbage", stderr="")):
        with pytest.raises(RcloneVersionError):
            check_rclone_version()


def test_version_timeout_raises_rclone_timeout_error():
    # rclone is on PATH (which succeeds) but the version check subprocess stalls.
    # Must raise RcloneTimeoutError — NOT RcloneNotInstalledError — so callers
    # report the right diagnosis ("binary stalled") rather than "not installed".
    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=subprocess.TimeoutExpired(cmd=[FAKE_RCLONE, "version"], timeout=10)):
        with pytest.raises(RcloneTimeoutError) as exc_info:
            check_rclone_version()
        assert exc_info.value.code == "RCLONE_TIMEOUT"
        assert "timed out" in exc_info.value.message


# ---------------------------------------------------------------------------
# argv builders -- always a list, no shell, absolute binary
# ---------------------------------------------------------------------------

def test_pull_argv_is_list_no_shell(tmp_path):
    cfg = _make_cfg(tmp_path)
    argv = build_pull_argv(cfg, dry_run=False, log_path="/tmp/x.log", rclone_bin=FAKE_RCLONE)
    assert isinstance(argv, list)
    assert "shell" not in argv
    assert all(isinstance(a, str) for a in argv)
    assert argv[0] == FAKE_RCLONE
    assert Path(argv[0]).is_absolute()
    assert argv[1] == "copy"
    assert cfg.remote in argv
    assert cfg.local_path in argv
    assert "--filter-from" in argv
    assert cfg.filter_file in argv
    # rclone copy never deletes, so --max-delete is bisync-only (not in pull argv)
    assert f"--max-delete={cfg.max_delete}" not in argv
    assert f"--max-transfer={cfg.max_transfer}" in argv
    assert "--check-first" in argv
    assert "--log-file" in argv
    assert "--log-level" in argv


def test_pull_argv_dry_run_flag(tmp_path):
    cfg = _make_cfg(tmp_path)
    argv = build_pull_argv(cfg, dry_run=True, log_path="/tmp/x.log", rclone_bin=FAKE_RCLONE)
    assert "--dry-run" in argv


def test_checksum_toggles_with_cfg(tmp_path):
    cfg_on = _make_cfg(tmp_path, checksum=True)
    cfg_off = _make_cfg(tmp_path, checksum=False)
    assert "--checksum" in build_pull_argv(cfg_on, False, "/tmp/x.log", FAKE_RCLONE)
    assert "--checksum" not in build_pull_argv(cfg_off, False, "/tmp/x.log", FAKE_RCLONE)


def test_fast_list_and_bwlimit_flags_toggle_with_cfg(tmp_path):
    cfg_off = _make_cfg(tmp_path)
    argv_off = build_pull_argv(cfg_off, False, "/tmp/x.log", FAKE_RCLONE)
    assert "--fast-list" not in argv_off
    assert not any(a.startswith("--bwlimit") for a in argv_off)

    cfg_on = _make_cfg(tmp_path, fast_list=True, bwlimit="8M")
    argv_on = build_pull_argv(cfg_on, False, "/tmp/x.log", FAKE_RCLONE)
    assert "--fast-list" in argv_on
    assert "--bwlimit=8M" in argv_on  # single clean token, never split


def test_bisync_argv_is_list_with_conflict_flags(tmp_path):
    cfg = _make_cfg(tmp_path, direction="bisync")
    argv = build_bisync_argv(cfg, dry_run=False, log_path="/tmp/x.log", resync=True, rclone_bin=FAKE_RCLONE)
    assert isinstance(argv, list)
    assert argv[1] == "bisync"
    assert f"--conflict-resolve={cfg.conflict_resolve}" in argv
    assert f"--conflict-loser={cfg.conflict_loser}" in argv
    assert "--workdir" in argv
    assert cfg.workdir in argv
    assert "--resync" in argv
    # --max-delete is bisync-only (rclone copy never deletes)
    assert f"--max-delete={cfg.max_delete}" in argv


# ---------------------------------------------------------------------------
# Log parsing + hashing
# ---------------------------------------------------------------------------

def _write_log(tmp_path: Path) -> str:
    log = tmp_path / "rclone.log"
    log.write_text(textwrap.dedent("""\
        2026/06/20 02:10:01 INFO  : data/corpus/new.md: Copied (new)
        2026/06/20 02:10:02 INFO  : data/corpus/changed.md: Copied (replaced existing)
        2026/06/20 02:10:03 INFO  : data/corpus/gone.md: Deleted
        2026/06/20 02:10:04 ERROR : something went wrong on the wire
        2026/06/20 02:10:05 INFO  : unrelated noise line that should be ignored
    """), encoding="utf-8")
    return str(log)


def test_parse_log_events_and_errors(tmp_path):
    events, errors = parse_log(_write_log(tmp_path))
    kinds = {e.kind for e in events}
    assert kinds == {"added", "modified", "deleted"}
    assert any("something went wrong" in e for e in errors)


def test_parse_log_missing_file_tolerant(tmp_path):
    events, errors = parse_log(str(tmp_path / "nope.log"))
    assert events == []
    assert errors == []


def test_hash_changed_files_streams_and_skips_deleted(tmp_path):
    f = tmp_path / "a.md"
    f.write_text("hello", encoding="utf-8")
    events = [
        FileEvent(kind="added", path="a.md"),
        FileEvent(kind="deleted", path="b.md"),
        FileEvent(kind="modified", path="missing.md"),
    ]
    out = hash_changed_files(events, str(tmp_path))
    by_path = {e.path: e for e in out}
    assert by_path["a.md"].sha256 is not None and len(by_path["a.md"].sha256) == 64
    assert by_path["b.md"].sha256 is None  # deleted -> never hashed
    assert by_path["missing.md"].sha256 is None  # not on disk -> None


def test_hash_changed_files_skips_paths_outside_local_root(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    (tmp_path / "secret.md").write_text("secret", encoding="utf-8")

    out = hash_changed_files([FileEvent(kind="modified", path="../secret.md")], str(root))

    assert out[0].sha256 is None


# ---------------------------------------------------------------------------
# Audit dicts -- "file" key, never "query", no secret fields
# ---------------------------------------------------------------------------

def test_file_event_audit_uses_file_key_not_query():
    ev = FileEvent(kind="added", path="data/corpus/x.md", sha256="abc")
    d = ev.to_audit_dict(base="/repo/data/corpus")
    assert d["event"] == "sync_file_added"
    assert "file" in d
    assert "query" not in d
    assert d["file"] == "data/corpus/x.md"


def test_sync_result_audit_dict_no_secret_fields():
    res = SyncResult(
        success=True, direction="pull", started_at=1.0, finished_at=2.0,
        rclone_exit_code=0, corpus_changed=True,
    )
    d = res.to_audit_dict()
    assert "query" not in d
    keys = set(d.keys())
    assert not (keys & {"token", "refresh_token", "secret", "password", "stderr"})
    assert d["event"] == "sync_completed"


# ---------------------------------------------------------------------------
# run_sync end-to-end (mocked subprocess) + corpus_changed / exit-code wiring
# ---------------------------------------------------------------------------

def _patch_audit():
    return patch("sync.runner.audit_log")


def test_run_sync_corpus_changed_and_exit_10(tmp_path):
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        # argv must always be a list, never shell=True, binary absolute.
        assert isinstance(argv, list)
        assert kwargs.get("shell") is not True
        assert Path(argv[0]).is_absolute()
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        # rclone logs paths relative to the transfer root (data/corpus), e.g.
        # "notes.md" -- NOT "data/corpus/notes.md". corpus_changed must still
        # fire on this realistic shape.
        Path(log_path).write_text(
            "2026/06/20 02:10:01 INFO  : notes.md: Copied (new)\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    assert result.corpus_changed is True
    assert reindex_exit_code_for(result, cfg) == cfg.REINDEX_EXIT_CODE == 10


def test_run_sync_timeout_maps_to_sync_runtime_error(tmp_path):
    # A hung rclone (TimeoutExpired) must surface as a typed SyncRuntimeError
    # carrying the limit that tripped — and must NOT leak the remote spec/argv.
    cfg = _make_cfg(tmp_path, sync_timeout_sec=1)
    seen_timeout = {}

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        # Confirm the wall-clock ceiling is actually wired to the sync call.
        seen_timeout["value"] = kwargs.get("timeout")
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        with pytest.raises(SyncRuntimeError) as exc:
            run_sync(cfg, rclone_bin=FAKE_RCLONE)

    # The wall-clock ceiling is now a SHARED budget across all retry attempts
    # (so the lock can never be held for more than sync_timeout_sec total), so
    # the value passed to subprocess.run is the REMAINING budget. On the very
    # first attempt (no prior backoff) that is just-under the configured
    # ceiling — a few microseconds may have elapsed setting up the call.
    assert 0 < seen_timeout["value"] <= 1
    assert exc.value.details.get("timeout_sec") == 1
    # The remote spec must never leak into the error message.
    assert "dropbox" not in str(exc.value).lower()


def test_run_sync_timeout_zero_passes_none_to_subprocess(tmp_path):
    # sync_timeout_sec=0 is the "unbounded" escape hatch -> timeout=None.
    cfg = _make_cfg(tmp_path, sync_timeout_sec=0)
    log_path = cfg.log_path
    seen_timeout = {}

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        seen_timeout["value"] = kwargs.get("timeout", "MISSING")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    assert seen_timeout["value"] is None  # 0 -> unbounded


def test_run_sync_corpus_changed_ignores_rclone_internal_artifacts(tmp_path):
    # An rclone scratch/state file leaking into the log must NOT trip
    # corpus_changed (it is not corpus content).
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(
            "2026/06/20 02:10:01 INFO  : RCLONE_TEST: Copied (new)\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    assert result.corpus_changed is False


def test_run_sync_corpus_changed_ignores_nested_rclone_internal_artifacts(tmp_path):
    # rclone occasionally logs scratch/state files in a nested path
    # ("sub/.rclone-cache/state"). A root-only startswith() check would miss it
    # and wrongly trip corpus_changed -> a needless reindex. The per-component
    # check must treat a nested artifact as internal too.
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(
            "2026/06/20 02:10:01 INFO  : sub/.rclone-cache/state: Copied (new)\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    assert result.corpus_changed is False


def test_run_sync_corpus_changed_fires_on_nested_corpus_file(tmp_path):
    # A genuine corpus file in a subdirectory ("sub/notes.md") is NOT an rclone
    # artifact and MUST still trip corpus_changed.
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(
            "2026/06/20 02:10:01 INFO  : sub/notes.md: Copied (new)\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    assert result.corpus_changed is True


def test_run_sync_single_instance_lock_blocks_concurrent_run(tmp_path):
    # A pre-existing (fresh) lock directory means another run holds the lock;
    # run_sync must refuse rather than race a second rclone invocation.
    cfg = _make_cfg(tmp_path)
    lock_dir = Path(cfg.log_dir) / "sync.lock.d"
    lock_dir.mkdir(parents=True)

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        raise AssertionError("rclone must not run while the lock is held")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        with pytest.raises(SyncRuntimeError):
            run_sync(cfg, rclone_bin=FAKE_RCLONE)


def test_lock_stale_threshold_tracks_bounded_timeout(tmp_path):
    # A run with sync_timeout_sec above the flat 3h default must extend the
    # stale threshold past its own budget -- otherwise a *live* long run looks
    # stale and a second sync starts underneath it.
    cfg = _make_cfg(tmp_path, sync_timeout_sec=_LOCK_STALE_SEC + 3600)
    assert _lock_stale_after_sec(cfg) == cfg.sync_timeout_sec + _LOCK_STALE_MARGIN_SEC


def test_lock_stale_threshold_doubles_when_post_sync_check_enabled(tmp_path):
    # Regression for the PR #585 review finding: with post_sync_check=True the
    # lock is held through BOTH the sync (up to sync_timeout_sec across retries)
    # AND run_post_sync_check, which independently receives the full
    # sync_timeout_sec as its own subprocess timeout -- so the budget is ~2x,
    # not sync_timeout_sec + margin. Codex's example: 4h timeout -> threshold
    # must approach 9h (2*4h + 1h margin), not 5h.
    four_hours = 4 * 3600
    cfg = _make_cfg(tmp_path, sync_timeout_sec=four_hours, post_sync_check=True)
    assert _lock_stale_after_sec(cfg) == 2 * four_hours + _LOCK_STALE_MARGIN_SEC


def test_lock_stale_threshold_post_sync_check_still_floored(tmp_path):
    # The 3h floor dominates small timeouts even with the 2x multiplier, so the
    # default configuration's behavior is unchanged either way.
    cfg = _make_cfg(tmp_path, sync_timeout_sec=600, post_sync_check=True)
    assert _lock_stale_after_sec(cfg) == _LOCK_STALE_SEC


def test_run_sync_passes_doubled_threshold_when_post_sync_check(tmp_path):
    # End-to-end on the derivation wiring: a configured run with
    # post_sync_check=True must acquire the lock with the 2x threshold --
    # this is the lifecycle the review finding was about (sync + check under
    # one lock), pinned at the acquisition point.
    cfg = _make_cfg(tmp_path, sync_timeout_sec=_LOCK_STALE_SEC + 3600, post_sync_check=True)
    log_path = cfg.log_path
    acquired: list[float] = []

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
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    def spy_acquire(lock_dir, stale_after_sec=_LOCK_STALE_SEC):
        acquired.append(stale_after_sec)
        # This module's own import binding still points at the real function
        # (patch only swaps the attribute on sync.runner).
        _acquire_sync_lock(lock_dir, stale_after_sec)

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         patch("sync.runner._acquire_sync_lock", side_effect=spy_acquire), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.check_result is not None  # the check really ran under the lock
    assert acquired == [2 * cfg.sync_timeout_sec + _LOCK_STALE_MARGIN_SEC]


def test_lock_stale_threshold_floored_at_default(tmp_path):
    # Short bounded runs keep the flat default; the threshold never shrinks.
    cfg = _make_cfg(tmp_path, sync_timeout_sec=600)
    assert _lock_stale_after_sec(cfg) == _LOCK_STALE_SEC


def test_lock_stale_threshold_unbounded_keeps_default(tmp_path):
    # 0 = unbounded: no finite threshold can cover it, so the flat default
    # stays (run_sync logs the degraded protection at run start).
    cfg = _make_cfg(tmp_path, sync_timeout_sec=0)
    assert _lock_stale_after_sec(cfg) == _LOCK_STALE_SEC


def test_acquire_lock_reclaims_only_past_threshold(tmp_path):
    # A lock younger than the supplied threshold blocks; one older than it is
    # reclaimed. Threshold is a parameter so the bounded-timeout derivation
    # above is what actually gates reclamation.
    lock_dir = tmp_path / "sync.lock.d"
    lock_dir.mkdir()
    with pytest.raises(SyncRuntimeError):
        _acquire_sync_lock(str(lock_dir), stale_after_sec=3600)
    old = time.time() - 7200
    os.utime(lock_dir, (old, old))
    _acquire_sync_lock(str(lock_dir), stale_after_sec=3600)  # reclaimed, no raise
    assert lock_dir.exists()


def test_run_sync_releases_lock_after_run(tmp_path):
    # After a normal run the lock directory must be gone so the next run can
    # acquire it.
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("INFO  : nothing to do\n", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert not (Path(cfg.log_dir) / "sync.lock.d").exists()


def test_run_sync_no_change_exit_0(tmp_path):
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("2026/06/20 02:10:01 INFO  : nothing to do\n", encoding="utf-8")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is True
    assert result.corpus_changed is False
    assert reindex_exit_code_for(result, cfg) == 0


def test_run_sync_safety_abort_exit_1(tmp_path):
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=9, stdout="", stderr="Fatal error: max-delete threshold exceeded")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is False
    assert result.aborted_for_safety is True
    assert reindex_exit_code_for(result, cfg) == 1


def test_run_sync_other_failure_exit_2(tmp_path):
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=1, stdout="", stderr="generic failure")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is False
    assert result.aborted_for_safety is False
    assert reindex_exit_code_for(result, cfg) == 2


def test_run_sync_exit_code_8_is_authoritative_safety_abort(tmp_path):
    # rclone exits 8 when --max-transfer trips, but writes the fuse message to
    # --log-file — it may never appear in captured stderr or the parsed errors.
    # The exit code alone must classify the run as a safety abort (CLI exit 1);
    # relying only on the text heuristics misfiled this as a generic failure (2).
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        # No trip words anywhere in stderr/log: exit code is the only signal.
        return MagicMock(returncode=8, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is False
    assert result.aborted_for_safety is True
    assert reindex_exit_code_for(result, cfg) == 1


def test_run_sync_exit_code_7_without_trip_text_stays_generic_failure(tmp_path):
    # Exit 7 is rclone's GENERIC fatal-error code — --max-delete trips report it,
    # but so does any other fatal error. Without trip text it must NOT be
    # classified as a safety abort (that remains the text heuristics' job).
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=7, stdout="", stderr="fatal: unrelated failure")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert result.success is False
    assert result.aborted_for_safety is False
    assert reindex_exit_code_for(result, cfg) == 2


def test_run_sync_raises_when_log_cannot_be_cleared(tmp_path):
    # rclone opens --log-file in append mode, so a previous run's log must be
    # cleared before this run or its FileEvents/ERROR lines would be replayed
    # (a false reindex signal or a phantom safety-abort). Plant a directory where
    # the log file belongs so the clear (open "w") raises IsADirectoryError -- the
    # fix must surface that as SyncRuntimeError, never silently parse stale data.
    cfg = _make_cfg(tmp_path)
    Path(cfg.log_path).mkdir(parents=True, exist_ok=True)

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", return_value=_version_mock("1.70.0")), \
         _patch_audit():
        with pytest.raises(SyncRuntimeError):
            run_sync(cfg, rclone_bin=FAKE_RCLONE)


def test_run_sync_clears_stale_log_before_run(tmp_path):
    # A stale log left from a prior run must NOT leak into this run's result.
    # Pre-seed the log with a deleted-file line + an ERROR; the mocked rclone run
    # writes only a single "Copied (new)" line. After the fix the result reflects
    # ONLY the fresh line -- the stale delete/error are gone.
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path
    Path(log_path).parent.mkdir(parents=True, exist_ok=True)
    Path(log_path).write_text(
        "2026/06/19 01:00:00 INFO  : stale.md: Deleted\n"
        "2026/06/19 01:00:00 ERROR : stale failure from a previous run\n",
        encoding="utf-8",
    )

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        # rclone appends to log_path; the fix truncated it first, so only this
        # fresh line is present when parse_log runs.
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("2026/06/20 02:10:01 INFO  : fresh.md: Copied (new)\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch):
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    kinds = {(ev.kind, ev.path) for ev in result.events}
    assert ("added", "fresh.md") in kinds
    assert ("deleted", "stale.md") not in kinds  # stale event was cleared
    assert result.errors == []  # stale ERROR line was cleared, not replayed


def test_run_sync_retries_on_transient_then_succeeds(tmp_path):
    # rclone exit 5 is "Temporary error (retry might fix it)". With sync_retries>0
    # the run must retry and a later success wins. Backoff 0 keeps the test instant.
    cfg = _make_cfg(tmp_path, sync_retries=2, retry_backoff_sec=0)
    log_path = cfg.log_path
    calls = {"sync": 0}
    captured: list[dict] = []

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        calls["sync"] += 1
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        if calls["sync"] < 2:
            # First attempt: transient failure, empty log, exit 5.
            Path(log_path).write_text("", encoding="utf-8")
            return MagicMock(returncode=5, stdout="", stderr="temporary error")
        # Second attempt: success with a real corpus change.
        Path(log_path).write_text(
            "2026/06/20 02:10:01 INFO  : notes.md: Copied (new)\n", encoding="utf-8"
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         patch("sync.runner.audit_log", side_effect=lambda e: captured.append(e)):
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert calls["sync"] == 2  # one failed attempt + one success
    assert result.success is True
    assert result.corpus_changed is True  # the success attempt's log is what parsed
    assert result.errors == []  # the failed attempt's log was truncated, not replayed
    assert any(e.get("event") == "sync_retry" for e in captured)


def test_run_sync_no_retry_when_disabled(tmp_path):
    # Default sync_retries=0 -> a transient exit 5 is NOT retried (single shot).
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path
    calls = {"sync": 0}

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        calls["sync"] += 1
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=5, stdout="", stderr="temporary error")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert calls["sync"] == 1
    assert result.success is False
    assert reindex_exit_code_for(result, cfg) == 2  # non-safety failure


def test_run_sync_does_not_retry_nontransient_failure(tmp_path):
    # exit 1 (usage error) is deterministic, NOT transient: even with retries
    # configured it must run exactly once -- looping would never help.
    cfg = _make_cfg(tmp_path, sync_retries=3, retry_backoff_sec=0)
    log_path = cfg.log_path
    calls = {"sync": 0}

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        calls["sync"] += 1
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text("", encoding="utf-8")
        return MagicMock(returncode=1, stdout="", stderr="generic failure")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert calls["sync"] == 1
    assert result.success is False


def test_run_sync_audit_events_have_file_key_no_query(tmp_path):
    cfg = _make_cfg(tmp_path)
    log_path = cfg.log_path
    captured: list[dict] = []

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(
            "2026/06/20 02:10:01 INFO  : notes.md: Copied (new)\n",
            encoding="utf-8",
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         patch("sync.runner.audit_log", side_effect=lambda e: captured.append(e)):
        run_sync(cfg, rclone_bin=FAKE_RCLONE)

    file_events = [e for e in captured if e.get("event", "").startswith("sync_file_")]
    assert file_events, "expected at least one per-file audit event"
    for e in file_events:
        assert "file" in e
        assert "query" not in e
    # No secret-looking fields anywhere.
    for e in captured:
        assert not (set(e.keys()) & {"token", "refresh_token", "secret", "password", "stderr"})


# ---------------------------------------------------------------------------
# Post-sync rclone check
# ---------------------------------------------------------------------------

def test_build_check_argv_is_list_with_remote_local_filter(tmp_path):
    cfg = _make_cfg(tmp_path)
    argv = build_check_argv(cfg, rclone_bin=FAKE_RCLONE)
    assert isinstance(argv, list)
    assert argv[1] == "check"
    assert cfg.remote in argv
    assert cfg.local_path in argv
    assert "--filter-from" in argv
    assert cfg.filter_file in argv
    # check is read-only: no --dry-run, no log-file writes.
    assert "--log-file" not in argv


def test_build_check_argv_includes_checksum_flag(tmp_path):
    cfg = _make_cfg(tmp_path, checksum=True)
    assert "--checksum" in build_check_argv(cfg, rclone_bin=FAKE_RCLONE)


def test_build_check_argv_omits_checksum_when_off(tmp_path):
    cfg = _make_cfg(tmp_path, checksum=False)
    assert "--checksum" not in build_check_argv(cfg, rclone_bin=FAKE_RCLONE)


def test_run_post_sync_check_ok_when_zero_differences(tmp_path):
    cfg = _make_cfg(tmp_path)
    ok_output = (
        "2026/06/20 00:00:00 INFO  : 0 differences found\n"
        "2026/06/20 00:00:00 INFO  : Found 0 missing on Local\n"
        "2026/06/20 00:00:00 INFO  : Found 0 missing on Remote\n"
    )
    with patch("sync.runner.subprocess.run",
               return_value=MagicMock(returncode=0, stdout="", stderr=ok_output)), \
         _patch_audit():
        cr = run_post_sync_check(cfg, rclone_bin=FAKE_RCLONE)
    assert cr.ok is True
    assert cr.differences == 0
    assert cr.missing_local == 0
    assert cr.missing_remote == 0
    assert cr.errors == []


def test_run_post_sync_check_not_ok_when_differences_found(tmp_path):
    cfg = _make_cfg(tmp_path)
    diff_output = (
        "2026/06/20 00:00:00 NOTICE: notes.md: sizes differ\n"
        "2026/06/20 00:00:00 INFO  : Found 1 missing on Local\n"
        "2026/06/20 00:00:00 INFO  : Found 0 missing on Remote\n"
        "2026/06/20 00:00:00 INFO  : 1 differences found\n"
    )
    with patch("sync.runner.subprocess.run",
               return_value=MagicMock(returncode=1, stdout="", stderr=diff_output)), \
         _patch_audit():
        cr = run_post_sync_check(cfg, rclone_bin=FAKE_RCLONE)
    assert cr.ok is False
    assert cr.differences == 1
    assert cr.missing_local == 1
    assert cr.missing_remote == 0
    assert len(cr.errors) == 1
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


def test_run_sync_retry_coalesces_to_final_event_per_path(tmp_path):
    # codex #594 P2: a file touched by two attempts is reported once, with the
    # FINAL attempt's operation -- attempt 1 adds notes.md, attempt 2 replaces
    # it, so the surfaced event is 'modified', matching the on-disk state.
    # (Earliest-wins would report the stale 'added'.)
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

    assert [(e.kind, e.path) for e in result.events] == [("modified", "notes.md")]


def test_run_sync_retry_coalesce_reports_delete_over_earlier_add(tmp_path):
    # codex #594 P2 (the correctness case): a file added on attempt 1 and DELETED
    # on the successful retry must report 'deleted' -- earliest-wins would report
    # 'added' for a file that no longer exists, corrupting the audit against the
    # final filesystem state. corpus_changed stays True either way (a delete is a
    # change), so the reindex still fires.
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
            "2026/06/20 02:10:02 INFO  : notes.md: Deleted\n", encoding="utf-8"
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         _patch_audit():
        result = run_sync(cfg, rclone_bin=FAKE_RCLONE)

    assert [(e.kind, e.path) for e in result.events] == [("deleted", "notes.md")]
    assert result.corpus_changed is True


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
    # codex #594 P2: the exceptional record is schema-aligned with a normal
    # sync_failed -- the documented evidence fields are present, not dropped.
    rec = failed[0]
    for key in ("rclone_exit_code", "counts", "errors_n", "aborted_for_safety", "corpus_changed"):
        assert key in rec
    assert rec["aborted_for_safety"] is False   # a hang is not a safety-fuse trip
    assert rec["rclone_exit_code"] is None       # rclone never returned an exit code
    assert rec["corpus_changed"] is False        # nothing was copied before the hang


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


def test_run_sync_timeout_after_partial_copy_preserves_evidence(tmp_path):
    # codex #594 P1: an attempt that copies files and THEN times out must not
    # lose the change evidence. The terminal sync_failed record carries the
    # copied-file counts + corpus_changed=True (schema-aligned), so the
    # staleness is detectable and recoverable instead of only the exception type.
    cfg = _make_cfg(tmp_path, sync_timeout_sec=60)
    log_path = cfg.log_path
    captured: list[dict] = []

    def dispatch(argv, **kwargs):
        if argv[1] == "version":
            return _version_mock("1.70.0")
        # rclone copies a file into the log, THEN hangs past the wall clock.
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(
            "2026/06/20 02:10:01 INFO  : notes.md: Copied (new)\n", encoding="utf-8"
        )
        raise subprocess.TimeoutExpired(cmd=argv, timeout=kwargs.get("timeout"))

    with patch("sync.runner.shutil.which", return_value=FAKE_RCLONE), \
         patch("sync.runner.subprocess.run", side_effect=dispatch), \
         patch("sync.runner.audit_log", side_effect=lambda e: captured.append(e)):
        with pytest.raises(SyncRuntimeError):
            run_sync(cfg, rclone_bin=FAKE_RCLONE)

    failed = [e for e in captured if e.get("event") == "sync_failed"]
    assert failed
    rec = failed[0]
    assert rec["error_type"] == "SyncRuntimeError"
    assert rec["corpus_changed"] is True         # copied-file evidence preserved
    assert rec["counts"]["added"] == 1
    assert rec["dry_run"] is False
    assert rec["aborted_for_safety"] is False
