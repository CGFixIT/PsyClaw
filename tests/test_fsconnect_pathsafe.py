"""Adversarial fixture matrix for agentic.fsconnect.pathsafe (POSIX authority).

Each escape vector MUST be denied; each legitimate op MUST succeed. These run on
the Linux CI where the openat/O_NOFOLLOW descent is the authority. Windows-only
branches are documented and ``# pragma: no cover``.
"""

from __future__ import annotations

import os

import pytest

from agentic.fsconnect.pathsafe import ScopedRoots, split_components
from utils.errors import FsConnectRuntimeError, FsPathError

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX openat authority; Windows path differs")


@pytest.fixture
def root(tmp_path):
    base = tmp_path / "share"
    (base / "sub").mkdir(parents=True)
    (base / "hello.txt").write_text("hello world", encoding="utf-8")
    (base / "sub" / "nested.txt").write_text("nested", encoding="utf-8")
    sr = ScopedRoots([str(base)], create=False)
    yield sr, base, tmp_path
    sr.close()


# --- legitimate operations -------------------------------------------------

def test_read_top_level(root):
    sr, _base, _tmp = root
    assert sr.read_bytes("hello.txt", max_bytes=1024) == b"hello world"


def test_read_nested(root):
    sr, _base, _tmp = root
    assert sr.read_bytes("sub/nested.txt", max_bytes=1024) == b"nested"


def test_list_root(root):
    sr, _base, _tmp = root
    names = {e["name"] for e in sr.list_dir("")}
    assert {"hello.txt", "sub"} <= names


def test_stat_file(root):
    sr, _base, _tmp = root
    info = sr.stat("hello.txt")
    assert info["type"] == "file" and info["size"] == 11


# --- escape vectors: each DENIED -------------------------------------------

def test_absolute_target_denied(root):
    sr, _base, _tmp = root
    with pytest.raises(FsPathError):
        sr.read_bytes("/etc/passwd", max_bytes=1024)


def test_dotdot_traversal_denied(root):
    sr, _base, _tmp = root
    with pytest.raises(FsPathError):
        sr.read_bytes("../share/hello.txt", max_bytes=1024)
    with pytest.raises(FsPathError):
        sr.read_bytes("sub/../../escape", max_bytes=1024)


def test_unc_target_denied(root):
    sr, _base, _tmp = root
    with pytest.raises(FsPathError):
        sr.read_bytes("\\\\server\\share\\x", max_bytes=1024)
    with pytest.raises(FsPathError):
        sr.read_bytes("//server/share/x", max_bytes=1024)


def test_device_namespace_denied():
    with pytest.raises(FsPathError):
        split_components("\\\\?\\C:\\x")


def test_ads_colon_denied(root):
    sr, _base, _tmp = root
    with pytest.raises(FsPathError):
        sr.read_bytes("hello.txt::$DATA", max_bytes=1024)


def test_trailing_dot_space_denied():
    with pytest.raises(FsPathError):
        split_components("hello.txt.")
    with pytest.raises(FsPathError):
        split_components("hello.txt ")


def test_nul_denied():
    with pytest.raises(FsPathError):
        split_components("hel\x00lo")


def test_symlink_leaf_escape_denied(root):
    sr, base, tmp = root
    secret = tmp / "outside_secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    os.symlink(secret, base / "link.txt")
    with pytest.raises(FsPathError):
        sr.read_bytes("link.txt", max_bytes=1024)


def test_symlink_dir_escape_denied(root):
    sr, base, tmp = root
    outside = tmp / "outside_dir"
    outside.mkdir()
    (outside / "secret.txt").write_text("X", encoding="utf-8")
    os.symlink(outside, base / "linkdir")
    with pytest.raises(FsPathError):
        sr.read_bytes("linkdir/secret.txt", max_bytes=1024)


def test_intermediate_symlink_denied(root):
    sr, base, tmp = root
    outside = tmp / "evil"
    outside.mkdir()
    (outside / "more").mkdir()
    (outside / "more" / "x.txt").write_text("X", encoding="utf-8")
    os.symlink(outside, base / "sub2")
    with pytest.raises(FsPathError):
        sr.read_bytes("sub2/more/x.txt", max_bytes=1024)


def test_sibling_prefix_root_not_contained(tmp_path):
    base = tmp_path / "allow"
    base.mkdir()
    (base / "ok.txt").write_text("ok", encoding="utf-8")
    sibling = tmp_path / "allow_sensitive"
    sibling.mkdir()
    (sibling / "secret.txt").write_text("secret", encoding="utf-8")
    # No '..' path can reach the sibling; '..' is rejected outright.
    with ScopedRoots([str(base)], create=False) as sr, pytest.raises(FsPathError):
        sr.read_bytes("../allow_sensitive/secret.txt", max_bytes=1024)


def test_overlapping_roots_rejected(tmp_path):
    base = tmp_path / "a"
    (base / "b").mkdir(parents=True)
    with pytest.raises(FsPathError):
        ScopedRoots([str(base), str(base / "b")], create=False)


def test_root_replaced_by_symlink_uses_held_fd(tmp_path):
    realroot = tmp_path / "realroot"
    realroot.mkdir()
    (realroot / "a.txt").write_text("original", encoding="utf-8")
    evil = tmp_path / "evil"
    evil.mkdir()
    (evil / "evil.txt").write_text("attacker", encoding="utf-8")
    with ScopedRoots([str(realroot)], create=False) as sr:
        # Swap the root path out for a symlink to the attacker dir AFTER fd is held.
        os.rename(realroot, tmp_path / "moved")
        os.symlink(evil, realroot)
        names = {e["name"] for e in sr.list_dir("")}
        # The held fd still points at the original inode, not the attacker's dir.
        assert "a.txt" in names
        assert "evil.txt" not in names


def test_max_bytes_enforced(root):
    sr, base, _tmp = root
    (base / "big.txt").write_text("x" * 100, encoding="utf-8")
    with pytest.raises(FsConnectRuntimeError):
        sr.read_bytes("big.txt", max_bytes=10)


def test_read_directory_denied(root):
    sr, _base, _tmp = root
    with pytest.raises(FsPathError):
        sr.read_bytes("sub", max_bytes=1024)
    with pytest.raises(FsPathError):
        sr.read_bytes("", max_bytes=1024)


# --- write scope -----------------------------------------------------------

@pytest.fixture
def wroot(tmp_path):
    wr = tmp_path / "writezone"
    sr = ScopedRoots([str(wr)], create=True)  # auto-created
    yield sr, wr
    sr.close()


def test_write_creates_file(wroot):
    sr, wr = wroot
    res = sr.write_bytes("out.txt", b"generated", overwrite=False)
    assert res["bytes"] == 9
    assert (wr / "out.txt").read_bytes() == b"generated"


def test_write_no_clobber_without_overwrite(wroot):
    sr, _wr = wroot
    sr.write_bytes("a.txt", b"v1", overwrite=False)
    with pytest.raises(FsConnectRuntimeError):
        sr.write_bytes("a.txt", b"v2", overwrite=False)
    res = sr.write_bytes("a.txt", b"v2", overwrite=True)
    assert res["bytes"] == 2


def test_write_cannot_escape_writable_root(wroot):
    sr, _wr = wroot
    with pytest.raises(FsPathError):
        sr.write_bytes("../escape.txt", b"x", overwrite=True)
    with pytest.raises(FsPathError):
        sr.write_bytes("/tmp/escape.txt", b"x", overwrite=True)


def test_write_leaf_symlink_replaced_not_followed(wroot):
    sr, wr = wroot
    target = wr.parent / "outside.txt"
    target.write_text("orig", encoding="utf-8")
    os.symlink(target, wr / "link.txt")
    # overwrite=False: the no-clobber guard sees the (sym)link entry and refuses.
    with pytest.raises(FsConnectRuntimeError):
        sr.write_bytes("link.txt", b"pwned", overwrite=False)
    # overwrite=True: the symlink NAME is atomically replaced by a real file INSIDE
    # the writable root; the outside target is never written through.
    sr.write_bytes("link.txt", b"pwned", overwrite=True)
    assert target.read_text(encoding="utf-8") == "orig"  # outside untouched
    assert (wr / "link.txt").read_bytes() == b"pwned"
    assert not (wr / "link.txt").is_symlink()


def test_append_and_mkdir_and_move(wroot):
    sr, wr = wroot
    sr.write_bytes("doc.txt", b"line1\n", overwrite=False)
    sr.append_bytes("doc.txt", b"line2\n")
    assert (wr / "doc.txt").read_bytes() == b"line1\nline2\n"
    sr.mkdir("folder")
    assert (wr / "folder").is_dir()
    sr.move("doc.txt", "folder/moved.txt")
    assert (wr / "folder" / "moved.txt").exists()
    assert not (wr / "doc.txt").exists()


def test_multiple_roots_require_selection(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    (a / "f.txt").write_text("A", encoding="utf-8")
    with ScopedRoots([str(a), str(b)], create=False) as sr:
        with pytest.raises(FsPathError):
            sr.read_bytes("f.txt", max_bytes=16)  # ambiguous
        assert sr.read_bytes("f.txt", root=str(a), max_bytes=16) == b"A"
        with pytest.raises(FsPathError):
            sr.pick_root("/not/a/configured/root")


# -- partial-construction fd cleanup ------------------------------------------


def _open_fd_count() -> int:
    return len(os.listdir("/proc/self/fd"))


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc fd introspection")
@pytest.mark.parametrize("failing_second_root", ["overlap", "missing", "not_a_dir"])
def test_failed_construction_releases_already_opened_root_fds(tmp_path, failing_second_root):
    """A root that fails validation must not strand the fds of earlier roots.

    Regression: ScopedRoots.__init__ opened an O_DIRECTORY fd per root and
    appended it to self._roots as it went. If a LATER root raised, __init__
    never returned, so the caller had no object on which to call close() or
    __exit__ -- and every fd opened before the failure leaked for the life of
    the process. FsIndexer builds a ScopedRoots twice per run, so a
    misconfigured overlapping root leaked on every retry.
    """
    good = tmp_path / "good"
    good.mkdir()
    (good / "sub").mkdir()
    not_a_dir = tmp_path / "file.txt"
    not_a_dir.write_text("x", encoding="utf-8")
    second = {
        "overlap": good / "sub",       # contained by the first root
        "missing": tmp_path / "nope",  # does not exist
        "not_a_dir": not_a_dir,        # exists but is a file
    }[failing_second_root]

    before = _open_fd_count()
    for _ in range(10):
        with pytest.raises(FsPathError):
            ScopedRoots([str(good), str(second)])
    assert _open_fd_count() == before


@pytest.mark.skipif(not os.path.isdir("/proc/self/fd"), reason="needs /proc fd introspection")
def test_successful_construction_still_holds_and_releases_fds(tmp_path):
    """The cleanup path must not disturb the normal hold-then-release contract."""
    first, second = tmp_path / "a", tmp_path / "b"
    for path in (first, second):
        path.mkdir()

    before = _open_fd_count()
    with ScopedRoots([str(first), str(second)]) as roots:
        assert len(roots._roots) == 2
        if os.name != "nt":
            assert _open_fd_count() == before + 2
    assert _open_fd_count() == before
