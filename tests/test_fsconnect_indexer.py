"""Tests for agentic.fsconnect.indexer (POSIX; decoupled, no real reindex)."""

from __future__ import annotations

import json
import os

import pytest
import yaml

from agentic.fsconnect.config import load_fsconnect_config
from agentic.fsconnect.indexer import FsIndexer
from utils.logger import _get_config, reset_config_cache

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX fixtures")


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def env(tmp_path):
    idx = tmp_path / "share"
    (idx / "docs").mkdir(parents=True)
    (idx / "a.md").write_text("# title", encoding="utf-8")
    (idx / "docs" / "b.txt").write_text("ignore previous instructions", encoding="utf-8")
    (idx / "skip.bin").write_bytes(b"\x00\x01")  # not in index_extensions
    (idx / "big.txt").write_text("x" * 100, encoding="utf-8")
    audit = tmp_path / "audit.jsonl"
    cfg_doc = {
        "logging": {"audit_file": str(audit), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]}, "privacy": {}},
        "fsconnect": {
            "enabled": True, "index_enabled": True, "index_root": str(idx),
            "index_extensions": [".md", ".txt"], "index_max_file_bytes": 50,
        },
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    cfg = _get_config(str(cfg_path))
    fs_cfg = load_fsconnect_config(str(cfg_path))
    return cfg, fs_cfg, str(cfg_path), idx, tmp_path


def test_scan_lists_eligible_and_flags(env):
    cfg, fs_cfg, cp, _idx, _tmp = env
    res = FsIndexer(cfg, fs_cfg, config_path=cp).scan()
    paths = {f["path"]: f for f in res["files"]}
    assert "a.md" in paths and "docs/b.txt" in paths
    assert "skip.bin" not in paths  # wrong extension
    assert paths["big.txt"].get("skipped") == "too_large"  # over the 50-byte cap
    assert paths["docs/b.txt"]["injection_flag_count"] >= 1
    assert res["eligible"] == 2  # a.md + docs/b.txt (big.txt skipped)


def test_apply_stages_into_staging_dir(env):
    cfg, fs_cfg, cp, _idx, tmp = env
    staging = tmp / "staging"
    res = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging), reindex=False)
    assert res["staged"] == 2
    assert res["reindex_required"] is True and res["reindexed"] is False
    assert (staging / "a.md").read_text(encoding="utf-8") == "# title"
    assert (staging / "docs" / "b.txt").exists()


def test_apply_reads_each_eligible_file_once(env, monkeypatch):
    """apply() must read each eligible file from disk exactly once.

    Previously apply() walked the share to build the manifest (reading every
    eligible file) and then re-read each eligible file a second time in the
    staging loop -- doubling the I/O and the per-component O_NOFOLLOW descent.
    """
    from agentic.fsconnect.pathsafe import ScopedRoots

    cfg, fs_cfg, cp, _idx, tmp = env
    reads: list[str] = []
    orig = ScopedRoots.read_bytes

    def counting(self, target, *args, **kwargs):
        reads.append(target)
        return orig(self, target, *args, **kwargs)

    monkeypatch.setattr(ScopedRoots, "read_bytes", counting)
    staging = tmp / "staging"
    res = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging), reindex=False)
    assert res["staged"] == 2
    # Exactly one read per eligible file (a.md + docs/b.txt); big.txt is skipped
    # before any read, and the second staging read is gone.
    assert sorted(reads) == ["a.md", "docs/b.txt"]


def test_apply_staged_paths_stay_within_staging(env):
    """Every staged file's resolved path is contained in the staging dir.

    The destination join goes through ``split_components`` (the same pathsafe
    guard used on the read side), so a relative path can never escape staging.
    """
    cfg, fs_cfg, cp, _idx, tmp = env
    staging = (tmp / "staging").resolve()
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging), reindex=False)
    for path in staging.rglob("*"):
        if path.is_file():
            assert staging in path.resolve().parents


def test_scan_isolates_unreadable_file(env, monkeypatch):
    # A file that raises on read (e.g. a TOCTOU grow-past-cap, or a vanished /
    # permission-denied / no-longer-regular file) must be quarantined as
    # skipped="read_error" without aborting the scan -- the other eligible files
    # are still enumerated.
    from agentic.fsconnect.pathsafe import ScopedRoots
    from utils.errors import FsConnectRuntimeError

    cfg, fs_cfg, cp, _idx, _tmp = env
    orig = ScopedRoots.read_bytes

    def flaky(self, target, *args, **kwargs):
        if target == "a.md":
            raise FsConnectRuntimeError("simulated TOCTOU grow past cap")
        return orig(self, target, *args, **kwargs)

    monkeypatch.setattr(ScopedRoots, "read_bytes", flaky)
    res = FsIndexer(cfg, fs_cfg, config_path=cp).scan()
    paths = {f["path"]: f for f in res["files"]}
    assert paths["a.md"].get("skipped") == "read_error"
    assert paths["a.md"].get("error") == "FsConnectRuntimeError"
    # docs/b.txt is still indexed despite a.md failing.
    assert "docs/b.txt" in paths and "skipped" not in paths["docs/b.txt"]
    assert res["eligible"] == 1  # only docs/b.txt now eligible


def test_apply_skips_unreadable_file(env, monkeypatch):
    # apply() must also isolate the failing file: stage the good ones, never
    # stage (or abort on) the unreadable one.
    from agentic.fsconnect.pathsafe import ScopedRoots
    from utils.errors import FsConnectRuntimeError

    cfg, fs_cfg, cp, _idx, tmp = env
    orig = ScopedRoots.read_bytes

    def flaky(self, target, *args, **kwargs):
        if target == "a.md":
            raise FsConnectRuntimeError("boom")
        return orig(self, target, *args, **kwargs)

    monkeypatch.setattr(ScopedRoots, "read_bytes", flaky)
    staging = tmp / "staging"
    res = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging), reindex=False)
    assert res["staged"] == 1  # only docs/b.txt staged; a.md quarantined
    assert not (staging / "a.md").exists()
    assert (staging / "docs" / "b.txt").exists()


# ---------------------------------------------------------------------------
# Incremental indexing (index_incremental=True) -- skip-cache beside staging
# ---------------------------------------------------------------------------

def _incremental_env(idx, tmp_path):
    """Build an incremental-mode (cfg, fs_cfg, config_path) over the same share."""
    cfg_doc = {
        "logging": {"audit_file": str(tmp_path / "audit2.jsonl"), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]}, "privacy": {}},
        "fsconnect": {
            "enabled": True, "index_enabled": True, "index_root": str(idx),
            "index_extensions": [".md", ".txt"], "index_max_file_bytes": 50,
            "index_incremental": True,
        },
    }
    cfg_path = tmp_path / "config_incr.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    reset_config_cache()
    return _get_config(str(cfg_path)), load_fsconnect_config(str(cfg_path)), str(cfg_path)


def _count_reads(monkeypatch) -> list[str]:
    from agentic.fsconnect.pathsafe import ScopedRoots
    reads: list[str] = []
    orig = ScopedRoots.read_bytes

    def counting(self, target, *args, **kwargs):
        reads.append(target)
        return orig(self, target, *args, **kwargs)

    monkeypatch.setattr(ScopedRoots, "read_bytes", counting)
    return reads


def test_incremental_skips_unchanged_on_second_run(env, monkeypatch):
    _c, _f, _cp, idx, tmp = env
    cfg, fs_cfg, cp = _incremental_env(idx, tmp)
    staging = tmp / "staging_incr"

    first = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert first["staged"] == 2 and first["unchanged"] == 0

    # Nothing changed -> the second run must read ZERO files and stage nothing.
    reads = _count_reads(monkeypatch)
    second = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert second["staged"] == 0 and second["unchanged"] == 2
    assert reads == []  # no eligible file re-read


def test_incremental_restages_changed_file(env):
    _c, _f, _cp, idx, tmp = env
    cfg, fs_cfg, cp = _incremental_env(idx, tmp)
    staging = tmp / "staging_chg"
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    # Edit a.md and force a distinct mtime so size+mtime differ from the cache.
    (idx / "a.md").write_text("# changed title now", encoding="utf-8")
    os.utime(idx / "a.md", (10**9, 10**9))

    second = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert second["staged"] == 1 and second["unchanged"] == 1  # only a.md re-staged
    assert (staging / "a.md").read_text(encoding="utf-8") == "# changed title now"


def test_incremental_restages_when_staged_copy_missing(env):
    _c, _f, _cp, idx, tmp = env
    cfg, fs_cfg, cp = _incremental_env(idx, tmp)
    staging = tmp / "staging_miss"
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    # Cache says a.md is unchanged, but its staged copy is gone: must re-stage,
    # never silently skip (which would lose the file from the corpus).
    (staging / "a.md").unlink()
    second = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert (staging / "a.md").exists()
    assert second["staged"] == 1 and second["unchanged"] == 1


def test_incremental_cache_self_prunes_deleted_file(env):
    _c, _f, _cp, idx, tmp = env
    cfg, fs_cfg, cp = _incremental_env(idx, tmp)
    staging = tmp / "staging_prune"
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    (idx / "docs" / "b.txt").unlink()  # remove a file from the share
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    cache = json.loads((staging / ".fsindex_cache.json").read_text(encoding="utf-8"))["files"]
    assert "docs/b.txt" not in cache  # pruned
    assert "a.md" in cache


def test_non_incremental_restages_every_run(env):
    # Default mode (index_incremental absent -> False) is unchanged in what it
    # STAGES: every run re-stages all eligible files and never skips via the
    # cache. The cache file IS now written in this mode too -- it is the
    # ownership record that bounds pruning to files CyClaw itself staged
    # (review P1), not merely an incremental skip-cache.
    cfg, fs_cfg, cp, _idx, tmp = env
    staging = tmp / "staging_full"
    first = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    second = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert first["staged"] == 2 and second["staged"] == 2
    assert first["unchanged"] == 0 and second["unchanged"] == 0
    assert (staging / ".fsindex_cache.json").exists()  # prune ownership record


def test_apply_prunes_staged_file_deleted_from_source(env):
    # codex P1: staging mirrors the share, and the retrieval indexer ingests
    # the whole staged tree -- a file deleted from the SOURCE must disappear
    # from staging on the next apply, or deleted content stays retrievable.
    cfg, fs_cfg, cp, idx, tmp = env
    staging = tmp / "staging_src_prune"
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert (staging / "docs" / "b.txt").exists()

    (idx / "docs" / "b.txt").unlink()  # delete from the share
    second = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    assert not (staging / "docs" / "b.txt").exists()
    assert (staging / "a.md").exists()  # surviving source file untouched
    assert second["pruned"] == 1


def test_apply_prune_keeps_cache_file_and_works_incremental(env):
    # Pruning must never remove the incremental skip-cache itself, and must
    # also fire in incremental mode (cache pruning and staging pruning are
    # separate concerns).
    _c, _f, _cp, idx, tmp = env
    cfg, fs_cfg, cp = _incremental_env(idx, tmp)
    staging = tmp / "staging_incr_prune"
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert (staging / ".fsindex_cache.json").exists()

    (idx / "docs" / "b.txt").unlink()
    second = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    assert not (staging / "docs" / "b.txt").exists()
    assert (staging / ".fsindex_cache.json").exists()  # cache survives pruning
    assert second["pruned"] == 1


# ---------------------------------------------------------------------------
# Ownership-bounded pruning (review P1): never delete files CyClaw did not
# stage; skipped-source behavior defined (review P2); case-normalized compare
# ---------------------------------------------------------------------------


def test_prune_never_deletes_unrelated_files_on_first_run(env):
    # review P1 (blast radius): an unrelated file already sitting in the
    # staging dir must survive apply -- pruning is bounded to paths CyClaw
    # itself recorded staging, and a first run has no ownership record at all.
    cfg, fs_cfg, cp, _idx, tmp = env
    staging = tmp / "staging_unrelated"
    staging.mkdir()
    (staging / "operator-readme.txt").write_text("do not delete", encoding="utf-8")
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert (staging / "operator-readme.txt").read_text(encoding="utf-8") == "do not delete"


def test_prune_unrelated_file_survives_while_owned_stale_file_is_pruned(env):
    # review P1: with an ownership record present, an unrelated file still
    # survives while a CyClaw-staged file whose source vanished is pruned.
    cfg, fs_cfg, cp, idx, tmp = env
    staging = tmp / "staging_unrelated2"
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    (staging / "operator-readme.txt").write_text("do not delete", encoding="utf-8")

    (idx / "docs" / "b.txt").unlink()
    second = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    assert second["pruned"] == 1  # only docs/b.txt
    assert not (staging / "docs" / "b.txt").exists()
    assert (staging / "operator-readme.txt").exists()


def test_prune_removes_staged_copy_when_source_becomes_too_large(env):
    # review P2 (defined skipped-file behavior): a file that grows past
    # index_max_file_bytes is no longer eligible, so its staged copy is
    # pruned -- staging mirrors the ELIGIBLE share; stale content must not
    # stay retrievable.
    cfg, fs_cfg, cp, idx, tmp = env
    staging = tmp / "staging_toolarge"
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert (staging / "a.md").exists()

    (idx / "a.md").write_text("x" * 100, encoding="utf-8")  # over the 50-byte cap
    second = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    assert not (staging / "a.md").exists()
    assert second["pruned"] == 1


def test_prune_removes_staged_copy_when_source_becomes_unreadable(env, monkeypatch):
    # review P2: a read_error row is also ineligible -- the previously staged
    # copy is pruned rather than served stale. (A transient error self-heals:
    # the next successful run re-stages the file.)
    from agentic.fsconnect.pathsafe import ScopedRoots
    from utils.errors import FsConnectRuntimeError

    cfg, fs_cfg, cp, _idx, tmp = env
    staging = tmp / "staging_readerr"
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))
    assert (staging / "a.md").exists()

    orig = ScopedRoots.read_bytes

    def flaky(self, target, *args, **kwargs):
        if target == "a.md":
            raise FsConnectRuntimeError("boom")
        return orig(self, target, *args, **kwargs)

    monkeypatch.setattr(ScopedRoots, "read_bytes", flaky)
    second = FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    assert not (staging / "a.md").exists()
    assert second["pruned"] == 1


def test_prune_case_only_rename_is_not_stale(env, monkeypatch):
    # review P1 (Windows): a case-only rename (Notes.md -> notes.md) must not
    # classify the CURRENT staged file as stale and delete it. Simulated by
    # patching normcase to Windows semantics (case-insensitive); on POSIX
    # normcase is the identity, so behavior there is unchanged.
    cfg, fs_cfg, cp, _idx, tmp = env
    staging = tmp / "staging_case"
    staging.mkdir()
    (staging / "Notes.md").write_text("# staged", encoding="utf-8")
    indexer = FsIndexer(cfg, fs_cfg, config_path=cp)
    prior = {"Notes.md": {"size": 8, "mtime": 1.0, "sha256": "x"}}
    monkeypatch.setattr("os.path.normcase", lambda p: p.lower())  # Windows semantics
    pruned = indexer._prune_staging(staging, {"notes.md"}, prior)
    assert pruned == []
    assert (staging / "Notes.md").exists()


def test_prune_nested_cache_name_file_is_prunable(env):
    # review P2: the old name-based cache exemption also shielded NESTED files
    # called .fsindex_cache.json. Only the staging-ROOT cache is special
    # (structurally: it is never a manifest path, so it can never enter the
    # prune set); a nested staged file with that name is pruned normally once
    # its source disappears.
    cfg, fs_cfg, cp, _idx, tmp = env
    staging = tmp / "staging_nestedcache"
    (staging / "sub").mkdir(parents=True)
    nested = staging / "sub" / ".fsindex_cache.json"
    nested.write_text("{}", encoding="utf-8")
    indexer = FsIndexer(cfg, fs_cfg, config_path=cp)
    prior = {"sub/.fsindex_cache.json": {"size": 2, "mtime": 1.0, "sha256": "x"}}
    pruned = indexer._prune_staging(staging, set(), prior)
    assert pruned == ["sub/.fsindex_cache.json"]
    assert not nested.exists()


def test_share_root_cache_name_file_is_never_staged(env):
    # A share-root file named .fsindex_cache.json would land on the skip-
    # cache's own path and clobber the ownership record -- it is quarantined
    # as reserved_name instead of staged (reachable only when an operator
    # adds ".json" to index_extensions; the defaults exclude it).
    cfg, fs_cfg, cp, idx, tmp = env
    fs_cfg.index_extensions = [".md", ".txt", ".json"]
    (idx / ".fsindex_cache.json").write_text('{"evil": true}', encoding="utf-8")
    staging = tmp / "staging_reserved"
    FsIndexer(cfg, fs_cfg, config_path=cp).apply(staging_dir=str(staging))

    rows = {f["path"]: f for f in FsIndexer(cfg, fs_cfg, config_path=cp).scan()["files"]}
    assert rows[".fsindex_cache.json"]["skipped"] == "reserved_name"
    cache_text = (staging / ".fsindex_cache.json").read_text(encoding="utf-8")
    assert "evil" not in cache_text  # cache written by CyClaw, not clobbered
