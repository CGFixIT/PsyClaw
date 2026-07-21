"""Tests for agentic.fsconnect.config -- loader + validators (self-contained)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

from agentic.fsconnect.config import (
    FsConnectConfig,
    load_fsconnect_config,
    os_default_writable_root,
)
from utils.errors import FsConnectConfigError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _write_cfg(tmp_path: Path, fsblock: dict | None) -> str:
    cfg: dict = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}}
    if fsblock is not None:
        cfg["fsconnect"] = fsblock
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return str(path)


def test_defaults_when_minimal(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "allowed_roots": [str(tmp_path)]})
    fc = load_fsconnect_config(path)
    assert fc.enabled is True
    assert fc.allowed_roots == [str(tmp_path)]
    assert fc.writes_enabled is False
    assert fc.allowed_fs_ops == ["fs_list", "fs_stat", "fs_read", "fs_grep", "fs_glob"]
    # null writable root expands to the OS default; index_root defaults to it
    assert fc.write_root_strs == [os_default_writable_root()]
    assert fc.index_root == os_default_writable_root()


def test_absent_block_raises(tmp_path):
    path = _write_cfg(tmp_path, None)
    with pytest.raises(FsConnectConfigError):
        load_fsconnect_config(path)


def test_enabled_defaults_false(tmp_path):
    path = _write_cfg(tmp_path, {"allowed_roots": [str(tmp_path)]})
    fc = load_fsconnect_config(path)
    assert getattr(fc, "enabled", None) is False


def test_unknown_op_rejected(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "allowed_fs_ops": ["fs_list", "fs_delete"]})
    with pytest.raises(FsConnectConfigError):
        load_fsconnect_config(path)


def test_follow_symlinks_true_rejected(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "follow_symlinks": True})
    with pytest.raises(FsConnectConfigError):
        load_fsconnect_config(path)


def test_negative_cap_rejected(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "max_file_bytes": 0})
    with pytest.raises(FsConnectConfigError):
        load_fsconnect_config(path)


def test_unc_root_refused_without_flag(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "allowed_roots": ["\\\\server\\share"]})
    with pytest.raises(FsConnectConfigError):
        load_fsconnect_config(path)


def test_unc_root_allowed_with_flag(tmp_path):
    path = _write_cfg(
        tmp_path,
        {"enabled": True, "allow_unc_roots": True, "allowed_roots": ["\\\\server\\share"]},
    )
    fc = load_fsconnect_config(path)
    assert fc.allowed_roots == ["\\\\server\\share"]


def test_index_extensions_normalized(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "index_extensions": ["MD", ".TXT"]})
    fc = load_fsconnect_config(path)
    assert fc.index_extensions == [".md", ".txt"]


def test_explicit_writable_root(tmp_path):
    wr = str(tmp_path / "share")
    path = _write_cfg(tmp_path, {"enabled": True, "writable_roots": [wr]})
    fc = load_fsconnect_config(path)
    assert fc.write_root_strs == [wr]
    assert fc.index_root == wr


def test_unknown_key_collected_not_fatal(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "typo_field": 1})
    fc = load_fsconnect_config(path)
    assert "typo_field" in fc._unknown_keys


def test_os_default_writable_root_is_os_specific():
    root = os_default_writable_root()
    if os.name == "nt":
        assert root == r"C:\CyClaw-FS"
    else:
        assert root in ("/var/lib/cyclaw-fs", os.path.expanduser("~/CyClaw-FS"))


def test_to_dict_roundtrips():
    fc = FsConnectConfig(allowed_roots=["/tmp/x"])
    d = fc.to_dict()
    assert d["allowed_roots"] == ["/tmp/x"]
    assert "writable_roots" in d


def test_quoted_bool_gates_fail_closed(tmp_path):
    # codex P2: bool("false") is True -- a quoted YAML string would silently
    # OPEN an execution/deletion gate. All safety booleans must be real
    # booleans, including the enabled toggle.
    for key in ("enabled", "writes_enabled", "allow_hard_delete",
                "require_confirm_destructive", "strict_roots",
                "block_on_injection_flags"):
        sub = tmp_path / key
        sub.mkdir()
        block: dict = {"allowed_roots": [str(sub)]}
        block[key] = "false"
        path = _write_cfg(sub, block)
        with pytest.raises(FsConnectConfigError, match=rf"fsconnect\.{key} must be a boolean"):
            load_fsconnect_config(path)


def test_real_bools_still_accepted(tmp_path):
    # Guard against over-correction: genuine booleans load unchanged.
    path = _write_cfg(tmp_path, {
        "enabled": True,
        "allowed_roots": [str(tmp_path)],
        "writes_enabled": False,
        "allow_hard_delete": False,
        "require_confirm_destructive": True,
    })
    fc = load_fsconnect_config(path)
    assert fc.enabled is True
    assert fc.writes_enabled is False
    assert fc.allow_hard_delete is False
    assert fc.require_confirm_destructive is True
