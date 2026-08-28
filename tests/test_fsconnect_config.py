"""Tests for agentic.fsconnect.config -- loader + validators (self-contained)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
import yaml

import agentic.fsconnect.config as fsconfig
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
    fc = fsconfig.load_fsconnect_config(path)
    assert fc.enabled is True
    assert fc.allowed_roots == [str(tmp_path)]
    assert fc.writes_enabled is False
    assert fc.allow_macos_volume_roots is False
    assert fc.allowed_fs_ops == [
        "fs_list",
        "fs_stat",
        "fs_read",
        "fs_grep",
        "fs_glob",
        "fs_largest",
    ]
    assert fc.largest_max_entries == 100_000
    # null writable root expands to the OS default; index_root defaults to it
    assert fc.write_root_strs == [fsconfig.os_default_writable_root()]
    assert fc.index_root == fsconfig.os_default_writable_root()


def test_absent_block_raises(tmp_path):
    path = _write_cfg(tmp_path, None)
    with pytest.raises(FsConnectConfigError):
        fsconfig.load_fsconnect_config(path)


def test_enabled_defaults_false(tmp_path):
    path = _write_cfg(tmp_path, {"allowed_roots": [str(tmp_path)]})
    fc = fsconfig.load_fsconnect_config(path)
    assert getattr(fc, "enabled", None) is False


def test_unknown_op_rejected(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "allowed_fs_ops": ["fs_list", "fs_delete"]})
    with pytest.raises(FsConnectConfigError):
        fsconfig.load_fsconnect_config(path)


def test_follow_symlinks_true_rejected(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "follow_symlinks": True})
    with pytest.raises(FsConnectConfigError):
        fsconfig.load_fsconnect_config(path)


def test_negative_cap_rejected(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "max_file_bytes": 0})
    with pytest.raises(FsConnectConfigError):
        fsconfig.load_fsconnect_config(path)


def test_non_positive_largest_walk_cap_rejected(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "largest_max_entries": 0})
    with pytest.raises(FsConnectConfigError):
        fsconfig.load_fsconnect_config(path)


def test_unc_root_refused_without_flag(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "allowed_roots": ["\\\\server\\share"]})
    with pytest.raises(FsConnectConfigError):
        fsconfig.load_fsconnect_config(path)


def test_unc_root_allowed_with_flag(tmp_path):
    path = _write_cfg(
        tmp_path,
        {"enabled": True, "allow_unc_roots": True, "allowed_roots": ["\\\\server\\share"]},
    )
    fc = fsconfig.load_fsconnect_config(path)
    assert fc.allowed_roots == ["\\\\server\\share"]


@pytest.mark.parametrize("field_name", ["allowed_roots", "writable_roots"])
def test_macos_volume_roots_refused_by_default(monkeypatch, tmp_path, field_name):
    monkeypatch.setattr(fsconfig.sys, "platform", "darwin")
    path = _write_cfg(tmp_path, {"enabled": True, field_name: ["/Volumes/External/share"]})
    with pytest.raises(FsConnectConfigError, match="allow_macos_volume_roots is false"):
        fsconfig.load_fsconnect_config(path)


@pytest.mark.parametrize("root", ["/volumes/External/share", "/Vol\u200dumes/External/share"])
def test_macos_volume_case_or_cf_alias_not_refused_at_config_time(monkeypatch, tmp_path, root):
    """config.py's check is a fast, existence-independent lexical heuristic
    (the path may not be mounted yet), so it can't reliably catch a
    differently-cased or Unicode-format-control spelling without guessing at
    a case-folding rule -- that would repeat the exact mistake this fix
    corrects elsewhere. The ground-truth, filesystem-identity check in
    pathsafe.py (exercised in tests/test_fsconnect_macos_policy.py) is the
    real authority and still refuses these at actual ScopedRoots-open time."""
    monkeypatch.setattr(fsconfig.sys, "platform", "darwin")
    path = _write_cfg(tmp_path, {"enabled": True, "allowed_roots": [root]})
    fc = fsconfig.load_fsconnect_config(path)
    assert fc.allowed_roots == [root]


def test_macos_volume_index_root_refused_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(fsconfig.sys, "platform", "darwin")
    path = _write_cfg(tmp_path, {"enabled": True, "index_root": "/Volumes/External/share"})
    with pytest.raises(FsConnectConfigError, match="allow_macos_volume_roots is false"):
        fsconfig.load_fsconnect_config(path)


def test_macos_volume_roots_require_separate_opt_in(monkeypatch, tmp_path):
    monkeypatch.setattr(fsconfig.sys, "platform", "darwin")
    root = "/Volumes/External/share"
    path = _write_cfg(
        tmp_path,
        {
            "enabled": True,
            "allow_unc_roots": True,
            "allow_macos_volume_roots": True,
            "allowed_roots": [root],
            "writable_roots": [root],
            "index_root": root,
        },
    )
    fc = fsconfig.load_fsconnect_config(path)
    assert fc.allowed_roots == [root]
    assert fc.write_root_strs == [root]
    assert fc.index_root == root


def test_unc_opt_in_does_not_allow_macos_volumes(monkeypatch, tmp_path):
    monkeypatch.setattr(fsconfig.sys, "platform", "darwin")
    path = _write_cfg(
        tmp_path,
        {
            "enabled": True,
            "allow_unc_roots": True,
            "allowed_roots": ["/Volumes/External/share"],
        },
    )
    with pytest.raises(FsConnectConfigError, match="allow_macos_volume_roots is false"):
        fsconfig.load_fsconnect_config(path)


def test_macos_volumes_sibling_prefix_is_not_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(fsconfig.sys, "platform", "darwin")
    path = _write_cfg(tmp_path, {"enabled": True, "allowed_roots": ["/VolumesLike/share"]})
    assert fsconfig.load_fsconnect_config(path).allowed_roots == ["/VolumesLike/share"]


def test_macos_volume_symlink_alias_is_refused(monkeypatch):
    monkeypatch.setattr(fsconfig.sys, "platform", "darwin")
    monkeypatch.setattr(fsconfig.os.path, "realpath", lambda _path: "/Volumes/External/share")
    with pytest.raises(FsConnectConfigError, match="allow_macos_volume_roots is false"):
        fsconfig.FsConnectConfig(allowed_roots=["/private/mounted-share"])


def test_index_extensions_normalized(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "index_extensions": ["MD", ".TXT"]})
    fc = fsconfig.load_fsconnect_config(path)
    assert fc.index_extensions == [".md", ".txt"]


def test_explicit_writable_root(tmp_path):
    wr = str(tmp_path / "share")
    path = _write_cfg(tmp_path, {"enabled": True, "writable_roots": [wr]})
    fc = fsconfig.load_fsconnect_config(path)
    assert fc.write_root_strs == [wr]
    assert fc.index_root == wr


def test_unknown_key_collected_not_fatal(tmp_path):
    path = _write_cfg(tmp_path, {"enabled": True, "typo_field": 1})
    fc = fsconfig.load_fsconnect_config(path)
    assert "typo_field" in fc._unknown_keys


def test_os_default_writable_root_darwin_never_probes_var_lib(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(os.path, "expanduser", lambda value: "/Users/test/CyClaw-FS")

    def fail_access(*_args):
        pytest.fail("Darwin must not probe /var/lib")

    monkeypatch.setattr(os, "access", fail_access)
    assert fsconfig.os_default_writable_root() == "/Users/test/CyClaw-FS"


@pytest.mark.parametrize(
    ("var_lib_writable", "expected"),
    [(True, "/var/lib/cyclaw-fs"), (False, "/home/test/CyClaw-FS")],
)
def test_os_default_writable_root_linux(monkeypatch, var_lib_writable, expected):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr("agentic.fsconnect.config._WINDOWS", False)
    monkeypatch.setattr(os, "access", lambda path, mode: path == "/var/lib" and var_lib_writable)
    monkeypatch.setattr(os.path, "expanduser", lambda value: "/home/test/CyClaw-FS")
    assert fsconfig.os_default_writable_root() == expected


def test_os_default_writable_root_windows_never_probes_var_lib(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("agentic.fsconnect.config._WINDOWS", True)

    def fail_access(*_args):
        pytest.fail("Windows must not probe /var/lib")

    monkeypatch.setattr(os, "access", fail_access)
    assert fsconfig.os_default_writable_root() == r"C:\CyClaw-FS"


def test_to_dict_roundtrips():
    fc = fsconfig.FsConnectConfig(allowed_roots=["/tmp/x"])
    d = fc.to_dict()
    assert d["allowed_roots"] == ["/tmp/x"]
    assert "writable_roots" in d


def test_quoted_bool_gates_fail_closed(tmp_path):
    # codex P2: bool("false") is True -- a quoted YAML string would silently
    # OPEN an execution/deletion gate. All safety booleans must be real
    # booleans, including the enabled toggle.
    for key in ("enabled", "writes_enabled", "allow_hard_delete",
                "allow_macos_volume_roots",
                "require_confirm_destructive", "strict_roots",
                "block_on_injection_flags"):
        sub = tmp_path / key
        sub.mkdir()
        block: dict = {"allowed_roots": [str(sub)]}
        block[key] = "false"
        path = _write_cfg(sub, block)
        with pytest.raises(FsConnectConfigError, match=rf"fsconnect\.{key} must be a boolean"):
            fsconfig.load_fsconnect_config(path)


def test_real_bools_still_accepted(tmp_path):
    # Guard against over-correction: genuine booleans load unchanged.
    path = _write_cfg(tmp_path, {
        "enabled": True,
        "allowed_roots": [str(tmp_path)],
        "writes_enabled": False,
        "allow_hard_delete": False,
        "require_confirm_destructive": True,
    })
    fc = fsconfig.load_fsconnect_config(path)
    assert fc.enabled is True
    assert fc.writes_enabled is False
    assert fc.allow_hard_delete is False
    assert fc.require_confirm_destructive is True
