"""Per-root quota accounting + enforcement tests (Phase 2 item 6, POSIX).

Proves: a root without a declared quota is unlimited (no ledger file); a root with a
quota_bytes ceiling refuses the write that would breach it (fail-closed) and the
ledger tracks usage across ops; quota_status reports usage; recompute reconciles the
ledger with on-disk reality.
"""

from __future__ import annotations

import os

import pytest
import yaml

from agentic.fsconnect import quota
from agentic.fsconnect.config import load_fsconnect_config
from agentic.fsconnect.writer import FsWriter
from utils.errors import FsWriteRefused
from utils.logger import _get_config, reset_config_cache

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX fixtures")


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _make(tmp_path, writable_roots):
    audit = tmp_path / "audit.jsonl"
    cfg_doc = {
        "logging": {"audit_file": str(audit), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": []}, "privacy": {}},
        "fsconnect": {"enabled": True, "writes_enabled": True,
                      "writable_roots": writable_roots},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    cfg = _get_config(str(cfg_path))
    fs_cfg = load_fsconnect_config(str(cfg_path))
    return cfg, fs_cfg, str(cfg_path)


def test_unlimited_root_writes_no_ledger(tmp_path):
    wz = tmp_path / "wz"
    cfg, fs_cfg, cp = _make(tmp_path, [str(wz)])
    with FsWriter(cfg, fs_cfg, config_path=cp) as w:
        note, ledger = w._check_quota("fs_write", w._roots.pick_root(None), 100, 1)
        w.fs_write("a.txt", b"x" * 100, reason="save")
    assert note == "quota unlimited"
    assert ledger is None  # no spec -> nothing loaded, nothing to thread forward
    assert not (wz / quota.QUOTA_FILE).exists()  # no ledger for an unlimited root


def test_quota_bytes_enforced(tmp_path):
    wz = tmp_path / "wz"
    cfg, fs_cfg, cp = _make(
        tmp_path, [{"path": str(wz), "quota_bytes": 100}])
    with FsWriter(cfg, fs_cfg, config_path=cp) as w:
        w.fs_write("a.txt", b"X" * 60, reason="under cap")
        with pytest.raises(FsWriteRefused) as ei:
            w.fs_write("b.txt", b"Y" * 60, reason="would breach")
    assert ei.value.details["failed_gate"] == "quota"
    assert not (wz / "b.txt").exists()


def test_ledger_tracks_across_ops(tmp_path):
    wz = tmp_path / "wz"
    cfg, fs_cfg, cp = _make(tmp_path, [{"path": str(wz), "quota_bytes": 10000}])
    with FsWriter(cfg, fs_cfg, config_path=cp) as w:
        w.fs_write("a.txt", b"X" * 40, reason="one")
        w.fs_write("b.txt", b"Y" * 60, reason="two")
        status = w.quota_status()
    assert status["used_bytes"] >= 100
    assert status["file_count"] >= 2
    assert (wz / quota.QUOTA_FILE).exists()


def test_max_files_enforced(tmp_path):
    wz = tmp_path / "wz"
    cfg, fs_cfg, cp = _make(tmp_path, [{"path": str(wz), "max_files": 1}])
    with FsWriter(cfg, fs_cfg, config_path=cp) as w:
        w.fs_write("a.txt", b"x", reason="first")
        with pytest.raises(FsWriteRefused) as ei:
            w.fs_write("b.txt", b"y", reason="second")
    assert ei.value.details["failed_gate"] == "quota"


def test_append_loop_eventually_denied(tmp_path):
    wz = tmp_path / "wz"
    cfg, fs_cfg, cp = _make(tmp_path, [{"path": str(wz), "quota_bytes": 50}])
    with FsWriter(cfg, fs_cfg, config_path=cp) as w:
        w.fs_write("log.txt", b"X" * 20, reason="seed")
        with pytest.raises(FsWriteRefused):
            for i in range(10):
                w.fs_append("log.txt", b"Y" * 20, reason=f"append {i}")


def test_quota_status_recompute(tmp_path):
    wz = tmp_path / "wz"
    cfg, fs_cfg, cp = _make(tmp_path, [{"path": str(wz), "quota_bytes": 10000}])
    with FsWriter(cfg, fs_cfg, config_path=cp) as w:
        w.fs_write("a.txt", b"X" * 30, reason="seed")
        # write an out-of-band file the ledger doesn't know about
        (wz / "oob.txt").write_bytes(b"Z" * 70)
        status = w.quota_status(recompute=True)
    assert status["used_bytes"] == 100  # 30 + 70 reconciled by the walk
    assert status["file_count"] == 2


def test_quota_bytes_allows_exact_boundary(tmp_path):
    # _check_quota uses strict > (not >=): landing exactly on the quota is
    # allowed. Pinning this deliberate choice so it can't silently flip.
    wz = tmp_path / "wz"
    cfg, fs_cfg, cp = _make(tmp_path, [{"path": str(wz), "quota_bytes": 100}])
    with FsWriter(cfg, fs_cfg, config_path=cp) as w:
        w.fs_write("a.txt", b"X" * 100, reason="exactly at quota")
    assert (wz / "a.txt").read_bytes() == b"X" * 100


def test_trash_empty_credits_freed_bytes_to_ledger(tmp_path):
    wz = tmp_path / "wz"
    cfg, fs_cfg, cp = _make(tmp_path, [{"path": str(wz), "quota_bytes": 10000}])
    fs_cfg.allow_hard_delete = True
    with FsWriter(cfg, fs_cfg, config_path=cp) as w:
        w.fs_write("a.txt", b"X" * 100, reason="seed")
        before = w.quota_status()["used_bytes"]
        w.fs_delete("a.txt", reason="trash it", confirm=True)
        w.trash_empty(reason="empty all", confirm=True, all_entries=True)
        after = w.quota_status()["used_bytes"]
    assert after <= before - 100
