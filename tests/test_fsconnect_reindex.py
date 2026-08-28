"""Cross-platform unit tests for FsIndexer._run_reindex (no POSIX share fixtures)."""
from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agentic.fsconnect.indexer import FsIndexer
from utils.telemetry_kill import SCRUBBED_ENV_KEYS, TELEMETRY_KILL


def test_run_reindex_forwards_config_path(monkeypatch: pytest.MonkeyPatch) -> None:
    # apply() stages with a custom config_path; reindex must rebuild that same
    # config's chroma/bm25 — not CWD config.yaml.
    fs_cfg = MagicMock()
    fs_cfg.index_root = "C:/share" if sys.platform == "win32" else "/share"
    fs_cfg.scan_content = False
    indexer = FsIndexer(cfg={}, fs_cfg=fs_cfg, config_path="/alt/cfg.yaml")
    seen: dict[str, object] = {}

    def fake_run(argv, **_kw):
        seen["argv"] = list(argv)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with patch("agentic.fsconnect.indexer.audit_log"):
        assert indexer._run_reindex() is True
    argv = seen["argv"]
    assert isinstance(argv, list)
    assert argv[1:3] == ["-m", "retrieval.indexer"]
    assert "--config" in argv
    assert argv[argv.index("--config") + 1] == "/alt/cfg.yaml"


def test_run_reindex_nonzero_exit_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    fs_cfg = MagicMock()
    fs_cfg.index_root = "C:/share" if sys.platform == "win32" else "/share"
    fs_cfg.scan_content = False
    indexer = FsIndexer(cfg={}, fs_cfg=fs_cfg, config_path="config.yaml")
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="boom"),
    )
    with patch("agentic.fsconnect.indexer.audit_log"):
        assert indexer._run_reindex() is False


def test_run_reindex_child_gets_telemetry_safe_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # The reindex child runs `python -m retrieval.indexer`, which reaches
    # ChromaDB/LangChain without ever importing gate.py -- so it must receive
    # the canonical telemetry-kill block via env= (the same overlay sync/cli.py
    # passes its own indexer child), not bare ambient inheritance.
    fs_cfg = MagicMock()
    fs_cfg.index_root = "C:/share" if sys.platform == "win32" else "/share"
    fs_cfg.scan_content = False
    indexer = FsIndexer(cfg={}, fs_cfg=fs_cfg, config_path="config.yaml")
    seen: dict[str, object] = {}

    def fake_run(argv, **kw):
        seen.update(kw)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with patch("agentic.fsconnect.indexer.audit_log"):
        assert indexer._run_reindex() is True
    env = seen.get("env")
    assert isinstance(env, dict)
    for key, value in TELEMETRY_KILL.items():
        assert env.get(key) == value
    for key in SCRUBBED_ENV_KEYS:
        assert key not in env
