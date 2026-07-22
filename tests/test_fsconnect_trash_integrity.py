"""Trash-integrity tests for agentic.fsconnect.writer (cross-platform).

Covers the two failure modes around ``_to_trash``:
  - a sidecar-write failure must leave no untracked payload in ``.cyclaw-trash``
    (``trash.list_entries`` only reads sidecars, so a payload without one would be
    invisible to trash-list/trash-empty forever), and
  - two deletes of the SAME path within the SAME second must both succeed and both
    appear in trash-list (the old name digest disambiguated nothing in that case).

Unlike test_fsconnect_writer.py these run on Windows too: the platform refusal is
monkeypatched off so the writer exercises the pathsafe name-based fallback inside a
tmp_path root.
"""

from __future__ import annotations

import pytest
import yaml

from agentic.fsconnect import trash
from agentic.fsconnect import writer as writer_mod
from agentic.fsconnect.config import load_fsconnect_config
from agentic.fsconnect.writer import FsWriter
from utils.errors import FsConnectRuntimeError
from utils.logger import _get_config, reset_config_cache

FIXED_TS = 1_783_000_000.0  # fixed clock => both deletes land in the same second


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Allow construction/execution on Windows (name-based fallback) so the trash
    # mechanics are exercised on every host; a no-op on POSIX.
    monkeypatch.setattr(writer_mod, "_writes_refused_platform", lambda: False)
    wz = tmp_path / "writezone"
    audit = tmp_path / "audit.jsonl"
    cfg_doc = {
        "logging": {"audit_file": str(audit), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]},
                   "privacy": {}},
        "fsconnect": {"enabled": True, "writable_roots": [str(wz)], "writes_enabled": True},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    cfg = _get_config(str(cfg_path))
    fs_cfg = load_fsconnect_config(str(cfg_path))
    return cfg, fs_cfg, str(cfg_path), wz


def test_sidecar_write_failure_leaves_no_untracked_payload(env, monkeypatch):
    cfg, fs_cfg, cp, wz = env
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=lambda: FIXED_TS) as w:
        w.fs_write("victim.txt", b"precious", reason="seed")
        real_write_bytes = w._roots.write_bytes

        def fail_sidecar(target, data, *, root=None, overwrite=False):
            if target.endswith(trash.META_SUFFIX):
                raise FsConnectRuntimeError("simulated sidecar write failure")
            return real_write_bytes(target, data, root=root, overwrite=overwrite)

        monkeypatch.setattr(w._roots, "write_bytes", fail_sidecar)
        with pytest.raises(FsConnectRuntimeError):
            w.fs_delete("victim.txt", reason="clean up", confirm=True)

    # Payload never moved: the original is intact and the trash dir holds nothing
    # (no sidecar-less payload that trash-list/trash-empty could never see).
    assert (wz / "victim.txt").read_bytes() == b"precious"
    trash_dir = wz / trash.TRASH_DIR
    leftovers = list(trash_dir.iterdir()) if trash_dir.exists() else []
    assert leftovers == []


def test_same_path_same_second_deletes_both_succeed(env):
    cfg, fs_cfg, cp, wz = env
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=lambda: FIXED_TS) as w:
        w.fs_write("dup.txt", b"v1", reason="seed v1")
        d1 = w.fs_delete("dup.txt", reason="delete v1", confirm=True)
        w.fs_write("dup.txt", b"v2", reason="seed v2")
        d2 = w.fs_delete("dup.txt", reason="delete v2", confirm=True)

        assert d1["trash_entry"] != d2["trash_entry"]
        entries = trash.list_entries(w._roots, None)
        names = [e.name for e in entries]
        assert sorted(names) == sorted([d1["trash_entry"], d2["trash_entry"]])
        assert all(e.original_path.endswith("dup.txt") for e in entries)

    # Both payloads physically exist under their distinct entry names.
    trash_dir = wz / trash.TRASH_DIR
    contents = {d1["trash_entry"]: b"v1", d2["trash_entry"]: b"v2"}
    for entry_name, expected in contents.items():
        assert (trash_dir / entry_name).read_bytes() == expected
        assert (trash_dir / f"{entry_name}{trash.META_SUFFIX}").exists()


def test_orphan_sidecar_reclaimed_by_trash_empty(env):
    # Crash window in _to_trash: sidecar written, payload move never happened.
    # trash_empty must still reclaim the entry (unlink the sidecar) instead of
    # skipping it forever as permanent litter.
    cfg, fs_cfg, cp, wz = env
    fs_cfg.allow_hard_delete = True
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=lambda: FIXED_TS) as w:
        entry = trash.make_entry(
            "ghost.txt", w._now_dt(), reason="simulated crash", sha256=None,
            size=3, kind="file", retention_days=fs_cfg.trash_retention_days)
        sidecar_rel = f"{trash.TRASH_DIR}/{entry.name}{trash.META_SUFFIX}"
        w._ensure_trash(None)
        w._roots.write_bytes(sidecar_rel, entry.meta_bytes(), root=None, overwrite=False)
        assert entry.name in [e.name for e in trash.list_entries(w._roots, None)]

        res = w.trash_empty(reason="empty all", confirm=True, all_entries=True)
        assert entry.name in res["purged"]
        assert trash.list_entries(w._roots, None) == []

    assert not (wz / trash.TRASH_DIR / f"{entry.name}{trash.META_SUFFIX}").exists()
