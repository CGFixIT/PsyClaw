"""Tests for agentic.fsconnect.cli -- subcommands + exit codes (POSIX)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from agentic.fsconnect import cli
from agentic.fsconnect import osutil
from utils.logger import reset_config_cache

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX fixtures")


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg(tmp_path: Path, fsblock: dict) -> str:
    doc = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": ["ignore previous instructions"]}, "privacy": {}},
        "fsconnect": fsblock,
    }
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


def test_status_runs(tmp_path, capsys):
    cp = _cfg(tmp_path, {"enabled": False})
    assert cli.main(["--config", cp, "status"]) == 0
    assert "Filesystem Connector Status" in capsys.readouterr().out


def test_bad_config_exit_env(tmp_path):
    # fsconnect block present but with an invalid value (unknown op).
    cp = _cfg(tmp_path, {"enabled": True, "allowed_fs_ops": ["fs_bogus"]})
    assert cli.main(["--config", cp, "status"]) == 3


def test_disabled_read_noop(tmp_path):
    cp = _cfg(tmp_path, {"enabled": False})
    assert cli.main(["--config", cp, "list"]) == 0


def test_read_enabled(tmp_path, capsys):
    share = tmp_path / "share"
    share.mkdir()
    (share / "f.txt").write_text("hello", encoding="utf-8")
    cp = _cfg(tmp_path, {"enabled": True, "allowed_roots": [str(share)]})
    assert cli.main(["--config", cp, "read", "--path", "f.txt"]) == 0
    assert "hello" in capsys.readouterr().out


def test_glob_enabled(tmp_path, capsys):
    share = tmp_path / "share"
    (share / "sub").mkdir(parents=True)
    (share / "a.md").write_text("x", encoding="utf-8")
    (share / "sub" / "b.md").write_text("y", encoding="utf-8")
    (share / "c.txt").write_text("z", encoding="utf-8")
    cp = _cfg(tmp_path, {"enabled": True, "allowed_roots": [str(share)]})
    assert cli.main(["--config", cp, "glob", "--pattern", "*.md"]) == 0
    out = capsys.readouterr().out
    assert "a.md" in out and "sub/b.md" in out
    assert "c.txt" not in out  # different extension


def test_write_dryrun_when_disabled(tmp_path, capsys):
    wz = tmp_path / "wz"
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(wz)], "writes_enabled": False})
    rc = cli.main(["--config", cp, "write", "--path", "out.txt", "--body", "x", "--reason", "r"])
    assert rc == 0
    assert "dry_run_plan" in capsys.readouterr().out
    assert not (wz / "out.txt").exists()


def test_write_refused_exit_4(tmp_path):
    wz = tmp_path / "wz"
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(wz)], "writes_enabled": True})
    # writes enabled but no reason => gate refuses => exit 4
    rc = cli.main(["--config", cp, "write", "--path", "out.txt", "--body", "x"])
    assert rc == 4


def test_write_applies(tmp_path):
    wz = tmp_path / "wz"
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(wz)], "writes_enabled": True})
    rc = cli.main(["--config", cp, "write", "--path", "out.txt", "--body", "qwen output", "--reason", "save"])
    assert rc == 0
    assert (wz / "out.txt").read_text(encoding="utf-8") == "qwen output"


def test_delete_to_trash_exit_0(tmp_path, capsys):
    wz = tmp_path / "wz"
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(wz)], "writes_enabled": True})
    cli.main(["--config", cp, "write", "--path", "g.txt", "--body", "x", "--reason", "seed"])
    rc = cli.main(["--config", cp, "delete", "--path", "g.txt", "--reason", "cleanup", "--confirm"])
    assert rc == 0
    assert not (wz / "g.txt").exists()
    assert (wz / ".cyclaw-trash").is_dir()


def test_delete_purge_refused_exit_4(tmp_path):
    wz = tmp_path / "wz"
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(wz)], "writes_enabled": True})
    cli.main(["--config", cp, "write", "--path", "h.txt", "--body", "x", "--reason", "seed"])
    # allow_hard_delete defaults false => --purge refused => exit 4
    rc = cli.main(["--config", cp, "delete", "--path", "h.txt", "--reason", "hard",
                   "--confirm", "--purge"])
    assert rc == 4
    assert (wz / "h.txt").exists()


def test_trash_restore_exit_0(tmp_path, capsys):
    wz = tmp_path / "wz"
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(wz)], "writes_enabled": True})
    cli.main(["--config", cp, "write", "--path", "r.txt", "--body", "keep", "--reason", "seed"])
    cli.main(["--config", cp, "delete", "--path", "r.txt", "--reason", "oops", "--confirm"])
    capsys.readouterr()
    entry = next(p.name for p in (wz / ".cyclaw-trash").iterdir()
                 if not p.name.endswith(".meta.json"))
    rc = cli.main(["--config", cp, "trash-restore", "--entry", entry,
                   "--reason", "undo", "--confirm"])
    assert rc == 0
    assert (wz / "r.txt").read_text(encoding="utf-8") == "keep"


def test_quota_status_exit_0(tmp_path, capsys):
    wz = tmp_path / "wz"
    cp = _cfg(tmp_path, {"enabled": True,
                         "writable_roots": [{"path": str(wz), "quota_bytes": 10000}],
                         "writes_enabled": True})
    cli.main(["--config", cp, "write", "--path", "a.txt", "--body", "hello", "--reason", "seed"])
    capsys.readouterr()
    rc = cli.main(["--config", cp, "quota-status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "used_bytes" in out and "quota_bytes" in out


def test_index_disabled_noop(tmp_path, capsys):
    cp = _cfg(tmp_path, {"enabled": True, "index_enabled": False})
    assert cli.main(["--config", cp, "index"]) == 0
    assert "Indexing disabled" in capsys.readouterr().out


def test_reveal_monkeypatched(tmp_path, capsys, monkeypatch):
    wz = tmp_path / "wz"
    wz.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "writable_roots": [str(wz)]})
    monkeypatch.setattr(osutil, "reveal", lambda p, roots: {"revealed": p, "via": "stub"})
    assert cli.main(["--config", cp, "reveal"]) == 0
    assert "revealed" in capsys.readouterr().out


def test_self_test_command(tmp_path):
    share = tmp_path / "share"
    share.mkdir()
    cp = _cfg(tmp_path, {"enabled": True, "allowed_roots": [str(share)]})
    assert cli.main(["--config", cp, "test"]) == 0
