"""Tests for agentic.fsconnect.osutil.reveal -- root-scope containment (POSIX).

Exercises the REAL reveal() logic (existence check, root containment, argv launch)
with only subprocess.run / shutil.which stubbed. Previously the sole reveal test
replaced the whole function body, so the scope check was never exercised.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

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


def test_darwin_path_identity_collapses_case_and_format_controls(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    assert osutil._path_identity("/tmp/Note.md") == osutil._path_identity("/tmp/note.MD")
    assert osutil._path_identity("/tmp/Note.md") == osutil._path_identity("/tmp/no‌te.md")


def test_linux_path_identity_preserves_case_and_format_controls(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert osutil._path_identity("/tmp/Note.md") != osutil._path_identity("/tmp/note.MD")
    assert osutil._path_identity("/tmp/Note.md") != osutil._path_identity("/tmp/no‌te.md")


def test_within_roots_darwin_admits_case_varied_alias(monkeypatch):
    # os.path.realpath is stubbed to identity so this exercises the comparison
    # logic without needing an actual case-insensitive filesystem in CI.
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(osutil.os.path, "realpath", lambda p: p)
    canonical = osutil._within_roots(Path("/Users/x/CyClaw-FS/sub/File.TXT"), ["/users/x/cyclaw-fs"])
    assert canonical == "/Users/x/CyClaw-FS/sub/File.TXT"


def test_within_roots_linux_refuses_case_varied_alias(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(osutil.os.path, "realpath", lambda p: p)
    canonical = osutil._within_roots(Path("/Users/x/CyClaw-FS/sub/File.TXT"), ["/users/x/cyclaw-fs"])
    assert canonical is None
