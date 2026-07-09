"""Write rate-limiting tests (Phase 2 item 7, POSIX).

Proves: the per-root (``fs:<root>``) and global (``fs:*``) limits throttle writes;
the sqlite backend makes the limit real ACROSS short-lived CLI invocations (two
separate FsWriter lifecycles sharing one db_path); dry-runs and refusals are never
counted; the global ceiling fires independently of any single root's budget.
"""

from __future__ import annotations

import os

import pytest
import yaml

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


def _make(tmp_path, rl, writable_roots=None):
    wz = tmp_path / "wz"
    audit = tmp_path / "audit.jsonl"
    cfg_doc = {
        "logging": {"audit_file": str(audit), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": []}, "privacy": {}},
        "fsconnect": {"enabled": True, "writes_enabled": True,
                      "writable_roots": writable_roots or [str(wz)],
                      "write_rate_limit": rl},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    cfg = _get_config(str(cfg_path))
    fs_cfg = load_fsconnect_config(str(cfg_path))
    return cfg, fs_cfg, str(cfg_path), wz


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_per_root_limit_throttles(tmp_path):
    db = str(tmp_path / "rate.db")
    rl = {"enabled": True, "max_ops": 2, "global_max_ops": 100,
          "window_seconds": 60, "db_path": db}
    cfg, fs_cfg, cp, wz = _make(tmp_path, rl)
    clock = _Clock()
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=clock) as w:
        w.fs_write("a.txt", b"1", reason="one")
        w.fs_write("b.txt", b"2", reason="two")
        with pytest.raises(FsWriteRefused) as ei:
            w.fs_write("c.txt", b"3", reason="three")
    assert ei.value.details["failed_gate"] == "rate_limit"
    assert not (wz / "c.txt").exists()


def test_limit_persists_across_invocations(tmp_path):
    """Two separate FsWriter lifecycles share one sqlite db_path -> the second
    invocation sees the first's consumed budget (short-lived CLI reality)."""
    db = str(tmp_path / "rate.db")
    rl = {"enabled": True, "max_ops": 2, "global_max_ops": 100,
          "window_seconds": 60, "db_path": db}
    cfg, fs_cfg, cp, wz = _make(tmp_path, rl)
    clock = _Clock()
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=clock) as w:
        w.fs_write("a.txt", b"1", reason="one")
        w.fs_write("b.txt", b"2", reason="two")
    # brand-new writer, same db, same window -> budget already exhausted
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=clock) as w:
        with pytest.raises(FsWriteRefused) as ei:
            w.fs_write("c.txt", b"3", reason="three")
    assert ei.value.details["failed_gate"] == "rate_limit"


def test_dryrun_not_counted(tmp_path):
    """A dry-run (writes_enabled false) must never consume rate budget."""
    db = str(tmp_path / "rate.db")
    rl = {"enabled": True, "max_ops": 1, "global_max_ops": 100,
          "window_seconds": 60, "db_path": db}
    cfg, fs_cfg, cp, wz = _make(tmp_path, rl)
    clock = _Clock()
    fs_cfg.writes_enabled = False
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=clock) as w:
        for i in range(5):
            r = w.fs_write(f"d{i}.txt", b"x", reason="plan")
            assert r["status"] == "dry_run_plan"
    # now enable: the single-op budget is still fully available
    fs_cfg.writes_enabled = True
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=clock) as w:
        r = w.fs_write("real.txt", b"x", reason="real")
    assert r["executed"] is True


def test_refusal_not_counted(tmp_path):
    """A gate refusal (missing reason) fires before the rate gate, so it burns
    no budget: after several refusals the full budget remains."""
    db = str(tmp_path / "rate.db")
    rl = {"enabled": True, "max_ops": 1, "global_max_ops": 100,
          "window_seconds": 60, "db_path": db}
    cfg, fs_cfg, cp, wz = _make(tmp_path, rl)
    clock = _Clock()
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=clock) as w:
        for _ in range(3):
            with pytest.raises(FsWriteRefused):
                w.fs_write("x.txt", b"x", reason="")  # reason gate
        r = w.fs_write("ok.txt", b"x", reason="now valid")
    assert r["executed"] is True


def test_global_ceiling_across_roots(tmp_path):
    """The fs:* global ceiling fires even when per-root budgets are unspent."""
    wz1 = tmp_path / "r1"
    wz2 = tmp_path / "r2"
    db = str(tmp_path / "rate.db")
    rl = {"enabled": True, "max_ops": 100, "global_max_ops": 2,
          "window_seconds": 60, "db_path": db}
    cfg, fs_cfg, cp, _ = _make(tmp_path, rl, writable_roots=[str(wz1), str(wz2)])
    clock = _Clock()
    with FsWriter(cfg, fs_cfg, config_path=cp, clock=clock) as w:
        w.fs_write("a.txt", b"1", reason="one", root=str(wz1))
        w.fs_write("b.txt", b"2", reason="two", root=str(wz2))
        with pytest.raises(FsWriteRefused) as ei:
            w.fs_write("c.txt", b"3", reason="three", root=str(wz1))
    assert ei.value.details["failed_gate"] == "rate_limit"
    assert ei.value.details["key"] == "fs:*"
