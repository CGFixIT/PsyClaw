"""Tests for agentic.fsconnect.osutil.reveal -- root-scope containment (POSIX).

Exercises the REAL reveal() logic (existence check, root containment, argv launch)
with only subprocess.run / shutil.which stubbed. Previously the sole reveal test
replaced the whole function body, so the scope check was never exercised.
"""

from __future__ import annotations

import os

import pytest

from agentic.fsconnect import osutil
from utils.errors import FsConnectRuntimeError

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX fixtures")


@pytest.fixture
def stub_launch(monkeypatch):
    """Stub the file-manager resolution + launch so no real process starts."""
    calls: list[list[str]] = []
    monkeypatch.setattr(osutil.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(osutil.subprocess, "run", lambda argv, **kw: calls.append(argv))
    return calls


def test_reveal_accepts_path_under_writable_root(tmp_path, stub_launch):
    wz = tmp_path / "wz"
    sub = wz / "sub"
    sub.mkdir(parents=True)
    res = osutil.reveal(str(sub), [str(wz)])
    assert res["revealed"] == str(sub)
    assert len(stub_launch) == 1  # the file manager was launched


def test_reveal_accepts_root_itself(tmp_path, stub_launch):
    wz = tmp_path / "wz"
    wz.mkdir()
    res = osutil.reveal(str(wz), [str(wz)])
    assert res["revealed"] == str(wz)


def test_reveal_refuses_path_outside_every_root(tmp_path, stub_launch):
    wz = tmp_path / "wz"
    wz.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(FsConnectRuntimeError) as ei:
        osutil.reveal(str(outside), [str(wz)])
    assert "outside the configured roots" in ei.value.message
    assert not stub_launch  # never launched


def test_reveal_refuses_sibling_prefix(tmp_path, stub_launch):
    # /tmp/x/wz must NOT admit a sibling /tmp/x/wz_evil (segment-aware containment).
    base = tmp_path / "x"
    wz = base / "wz"
    sibling = base / "wz_evil"
    wz.mkdir(parents=True)
    sibling.mkdir(parents=True)
    with pytest.raises(FsConnectRuntimeError):
        osutil.reveal(str(sibling), [str(wz)])
    assert not stub_launch


def test_reveal_refuses_symlink_escape(tmp_path, stub_launch):
    # A symlink inside the root that points outside resolves outside -> refused.
    wz = tmp_path / "wz"
    wz.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    link = wz / "link"
    link.symlink_to(outside)
    with pytest.raises(FsConnectRuntimeError):
        osutil.reveal(str(link), [str(wz)])
    assert not stub_launch


def test_reveal_nonexistent_path_raises(tmp_path, stub_launch):
    wz = tmp_path / "wz"
    wz.mkdir()
    with pytest.raises(FsConnectRuntimeError) as ei:
        osutil.reveal(str(wz / "nope"), [str(wz)])
    assert "does not exist" in ei.value.message
    assert not stub_launch


def test_reveal_rejects_leading_dash_target(tmp_path, stub_launch):
    with pytest.raises(FsConnectRuntimeError):
        osutil.reveal("-rf", [str(tmp_path)])
    assert not stub_launch


# --- filesystem-identity fallback (not a blanket "fold case on Darwin" guess) --
#
# _within_roots' fallback (pathsafe._is_real_descendant) asks the filesystem
# directly via device+inode rather than assuming any platform-wide case
# policy -- not every APFS volume is case-insensitive. These tests simulate
# an actually-case-insensitive lookup by mocking os.stat to resolve a
# differently-spelled path to the same real entity, exactly what a real
# case-insensitive volume's directory lookup would produce.

def test_within_roots_admits_filesystem_identity_alias(monkeypatch, tmp_path):
    root = tmp_path / "CyClaw-FS"
    root.mkdir()
    root_stat = os.stat(root)
    alias_root = str(root).replace("CyClaw-FS", "cyclaw-fs")  # never actually created
    target = root / "File.TXT"
    target.write_text("x", encoding="utf-8")
    original_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == alias_root:
            return root_stat
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(osutil.os, "stat", fake_stat)
    canonical = osutil._within_roots(target, [alias_root])
    assert canonical == str(target)


def test_within_roots_refuses_genuinely_different_directory(tmp_path):
    # On a real (case-sensitive) filesystem, two similarly-spelled directories
    # are genuinely different real entities and must never be conflated.
    root = tmp_path / "CyClaw-FS"
    other = tmp_path / "cyclaw-fs"
    root.mkdir()
    other.mkdir()
    target = root / "File.TXT"
    target.write_text("x", encoding="utf-8")
    canonical = osutil._within_roots(target, [str(other)])
    assert canonical is None


def test_reveal_end_to_end_admits_filesystem_identity_alias(monkeypatch, tmp_path, stub_launch):
    root = tmp_path / "CyClaw-FS"
    root.mkdir()
    root_stat = os.stat(root)
    alias_root = str(root).replace("CyClaw-FS", "cyclaw-fs")
    target = root / "File.TXT"
    target.write_text("x", encoding="utf-8")
    original_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == alias_root:
            return root_stat
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(osutil.os, "stat", fake_stat)
    res = osutil.reveal(str(target), [alias_root])
    assert res["revealed"] == str(target)
    assert len(stub_launch) == 1
