"""Adversarial fixture matrix for agentic.fsconnect.pathsafe (POSIX authority).

Each escape vector MUST be denied; each legitimate op MUST succeed. These run on
the Linux CI where the openat/O_NOFOLLOW descent is the authority. Windows-only
branches are documented and ``# pragma: no cover``.
"""

from __future__ import annotations

import errno
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import agentic.fsconnect.pathsafe as pathsafe
from agentic.fsconnect.pathsafe import ScopedRoots, split_components
from utils.errors import FsConnectRuntimeError, FsMacOSPermissionError, FsPathError

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


# --- filesystem-identity fallback (pathsafe._is_same_entity / _is_real_descendant) --
#
# Not every case-insensitive-looking alias is actually the same real directory
# (only a genuinely case-insensitive volume makes that true), so these are
# platform-agnostic: they test filesystem TRUTH (device+inode), never a
# blanket "fold case on this OS" assumption. The two tests that simulate an
# actually-case-insensitive lookup do so by mocking os.stat to return the
# same stat_result for two different spellings -- exactly what a real
# case-insensitive volume's directory lookup would produce.

def test_is_same_entity_true_for_identical_path(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    assert pathsafe._is_same_entity(str(d), str(d)) is True


def test_is_same_entity_false_for_distinct_real_directories(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    assert pathsafe._is_same_entity(str(a), str(b)) is False


def test_is_same_entity_false_when_a_path_is_missing(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    assert pathsafe._is_same_entity(str(d), str(tmp_path / "missing")) is False


def test_is_real_descendant_true_for_nested_target(tmp_path):
    base = tmp_path / "root"
    nested = base / "a" / "b"
    nested.mkdir(parents=True)
    assert pathsafe._is_real_descendant(str(nested), str(base)) is True


def test_is_real_descendant_true_for_root_itself(tmp_path):
    base = tmp_path / "root"
    base.mkdir()
    assert pathsafe._is_real_descendant(str(base), str(base)) is True


def test_is_real_descendant_false_for_sibling_directory(tmp_path):
    base = tmp_path / "root"
    sibling = tmp_path / "sibling"
    base.mkdir()
    sibling.mkdir()
    assert pathsafe._is_real_descendant(str(sibling), str(base)) is False


def test_is_real_descendant_false_when_outer_root_is_missing(tmp_path):
    assert pathsafe._is_real_descendant(str(tmp_path / "x"), str(tmp_path / "missing")) is False


def test_case_insensitive_alias_roots_rejected_as_overlapping(monkeypatch, tmp_path):
    """Simulates a genuinely case-insensitive volume (os.stat resolving a
    differently-spelled path to the same real entity) rather than assuming
    every volume behaves that way -- two configured roots that a case-
    insensitive filesystem would treat as the same directory are refused as
    overlapping via the filesystem-identity fallback."""
    monkeypatch.setattr(pathsafe, "_POSIX", False)
    monkeypatch.setattr(ScopedRoots, "_prepare_root", lambda _self, raw, *, create: Path(raw))
    real = tmp_path / "Share"
    real.mkdir()
    real_stat = os.stat(real)
    alias = tmp_path / "share"  # different case spelling; never actually created
    original_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == str(alias):
            return real_stat
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(pathsafe.os, "stat", fake_stat)
    with pytest.raises(FsPathError, match="overlapping roots"):
        ScopedRoots([str(real), str(alias)], create=False)


def test_case_insensitive_alias_selects_configured_held_root(monkeypatch, tmp_path):
    configured = tmp_path / "Share"
    configured.mkdir()
    (configured / "inside.txt").write_text("held fd", encoding="utf-8")
    configured_stat = os.stat(configured)
    alias = str(configured).replace("Share", "share")
    original_stat = os.stat

    def fake_stat(path, *args, **kwargs):
        if str(path) == alias:
            return configured_stat
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(pathsafe.os, "stat", fake_stat)
    with ScopedRoots([str(configured)], create=False) as roots:
        assert roots.read_bytes("inside.txt", root=alias, max_bytes=1024) == b"held fd"


def _tmp_is_case_sensitive(tmp_path: Path) -> bool:
    """Runtime probe, not a platform guess -- same technique as CPython's own
    Lib/test/support/os_helper.fs_is_case_insensitive (see pathsafe's
    _is_real_descendant docstring for why guessing by platform is wrong)."""
    probe = tmp_path / "CaseProbe.tmp"
    probe.write_text("x", encoding="utf-8")
    try:
        return not (tmp_path / "caseprobe.tmp").exists()
    finally:
        probe.unlink()


def test_case_sensitive_alias_roots_remain_distinct(tmp_path):
    """On a case-sensitive filesystem, two similarly-named-but-actually-
    different directories are genuinely different real entities and must
    never be merged into one root. Skipped on a case-insensitive filesystem
    (e.g. the default macOS CI runner) -- there, "Note.md" and "note.md"
    cannot coexist as two directories at all, so the scenario doesn't apply."""
    if not _tmp_is_case_sensitive(tmp_path):
        pytest.skip("tmp_path filesystem is case-insensitive; case-variant dirs can't coexist")
    first = tmp_path / "Note.md"
    alias = tmp_path / "note.md"
    first.mkdir()
    alias.mkdir()
    with ScopedRoots([str(first), str(alias)], create=False) as roots:
        assert len(roots.roots) == 2


@pytest.mark.parametrize("error_number", [errno.EPERM, errno.EACCES])
def test_darwin_root_permission_error_is_typed(monkeypatch, tmp_path, error_number):
    monkeypatch.setattr(sys, "platform", "darwin")
    share = tmp_path / "share"
    share.mkdir()

    def denied_open(*_args, **_kwargs):
        raise PermissionError(error_number, "denied")

    monkeypatch.setattr(os, "open", denied_open)
    with pytest.raises(FsMacOSPermissionError) as caught:
        ScopedRoots([str(share)], create=False)
    assert caught.value.code == "FSCONNECT_MACOS_PERMISSION_DENIED"
    assert "Files and Folders" in caught.value.message
    assert "Terminal or iTerm" in caught.value.message


def test_darwin_permission_error_never_falls_back(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    fallbacks: list[tuple[str, str]] = []

    def denied_mkdir(*_args, **_kwargs):
        raise PermissionError(errno.EPERM, "denied")

    monkeypatch.setattr(Path, "mkdir", denied_mkdir)
    with pytest.raises(FsMacOSPermissionError):
        ScopedRoots(
            [str(tmp_path / "denied")],
            create=True,
            strict_roots=False,
            on_fallback=lambda requested, fallback: fallbacks.append((requested, fallback)),
        )
    assert fallbacks == []


def test_darwin_list_permission_error_is_typed(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    share = tmp_path / "share"
    share.mkdir()
    with ScopedRoots([str(share)], create=False) as roots:
        def denied_listdir(_fd):
            raise PermissionError(errno.EPERM, "denied")

        monkeypatch.setattr(os, "listdir", denied_listdir)
        with pytest.raises(FsMacOSPermissionError):
            roots.list_dir("")


def test_darwin_list_skips_apple_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    share = tmp_path / "share"
    share.mkdir()
    for name in (".DS_Store", ".localized", "._note.md", "visible.md"):
        (share / name).write_text(name, encoding="utf-8")
    with ScopedRoots([str(share)], create=False) as roots:
        names = {entry["name"] for entry in roots.list_dir("", skip_macos_metadata=True)}
    assert names == {"visible.md"}


def test_darwin_dataless_flag_is_authoritative_regardless_of_logical_size(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    # A real dataless placeholder's st_size typically reports the file's full
    # logical size (not 0) -- macOS preserves it so Finder/ls can show a
    # correct size without downloading. The flag alone must decide.
    assert pathsafe._is_macos_dataless(SimpleNamespace(st_size=0, st_flags=0x40000000))
    assert pathsafe._is_macos_dataless(SimpleNamespace(st_size=12345, st_flags=0x40000000))
    assert not pathsafe._is_macos_dataless(SimpleNamespace(st_size=0, st_flags=0))


def test_darwin_dataless_read_refused_before_file_open(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "platform", "darwin")
    share = tmp_path / "share"
    share.mkdir()
    (share / "placeholder.md").touch()
    with ScopedRoots([str(share)], create=False) as roots:
        original_open = os.open
        opened_leaf: list[str] = []

        def track_open(path, *args, **kwargs):
            if path == "placeholder.md":
                opened_leaf.append(path)
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr(pathsafe, "_is_macos_dataless", lambda st: st.st_size == 0)
        monkeypatch.setattr(os, "open", track_open)
        with pytest.raises(FsPathError, match="dataless placeholder"):
            roots.read_bytes("placeholder.md", max_bytes=1024, skip_macos_metadata=True)
        assert opened_leaf == []


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
