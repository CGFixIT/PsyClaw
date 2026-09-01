"""Host-independent unit tests for Darwin-only fsconnect policy helpers."""

from __future__ import annotations

import errno
import logging
from types import SimpleNamespace

import pytest

from agentic.fsconnect import pathsafe
from utils.errors import FsMacOSPermissionError, FsPathError


@pytest.fixture
def darwin(monkeypatch) -> None:
    monkeypatch.setattr(pathsafe.sys, "platform", "darwin")


@pytest.mark.parametrize("error_number", [errno.EPERM, errno.EACCES])
def test_permission_mapper_is_typed_and_actionable(darwin: None, error_number: int) -> None:
    with pytest.raises(FsMacOSPermissionError) as caught:
        pathsafe._raise_macos_permission("opening a configured root", PermissionError(error_number, "denied"))
    assert caught.value.code == "FSCONNECT_MACOS_PERMISSION_DENIED"
    assert "Files and Folders" in caught.value.message
    assert "Terminal or iTerm" in caught.value.message


def test_permission_mapper_ignores_other_errors(darwin: None) -> None:
    assert pathsafe._raise_macos_permission("opening a configured root", OSError(errno.ENOENT, "missing")) is None


@pytest.mark.parametrize("name", [".DS_Store", ".localized", "._note.md"])
def test_apple_metadata_names_are_ignored(darwin: None, name: str) -> None:
    assert pathsafe._is_macos_artifact_name(name)


def test_ordinary_dotfile_is_not_ignored(darwin: None) -> None:
    assert not pathsafe._is_macos_artifact_name(".env")


def test_dataless_flag_is_authoritative_regardless_of_logical_size(darwin: None) -> None:
    # A real dataless placeholder's st_size typically reports the file's full
    # logical size (not 0) -- macOS preserves it so Finder/ls can show a
    # correct size without downloading. The flag alone must decide.
    assert pathsafe._is_macos_dataless(SimpleNamespace(st_size=0, st_flags=0x40000000))
    assert pathsafe._is_macos_dataless(SimpleNamespace(st_size=12345, st_flags=0x40000000))
    assert not pathsafe._is_macos_dataless(SimpleNamespace(st_size=0, st_flags=0))


def test_filter_omits_metadata_and_dataless_but_keeps_dotfiles(darwin: None) -> None:
    stats = {
        ".DS_Store": SimpleNamespace(st_size=2, st_flags=0),
        "._note.md": SimpleNamespace(st_size=2, st_flags=0),
        ".env": SimpleNamespace(st_size=2, st_flags=0),
        "placeholder.md": SimpleNamespace(st_size=0, st_flags=0x40000000),
        "visible.md": SimpleNamespace(st_size=2, st_flags=0),
    }
    visible = pathsafe._filter_macos_entries(list(stats), stats.__getitem__)
    assert [name for name, _st in visible] == [".env", "visible.md"]


def test_filter_propagates_permission_error(darwin: None) -> None:
    def denied(_name: str):
        raise PermissionError(errno.EPERM, "denied")

    with pytest.raises(FsMacOSPermissionError):
        pathsafe._filter_macos_entries(["note.md"], denied)


def test_filter_logs_entries_dropped_for_non_permission_errors(
    darwin: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-EACCES/EPERM stat failure still skips the entry, but audibly.

    _raise_macos_permission re-raises only a Darwin EACCES/EPERM denial;
    every other OSError fell through to a bare `continue`, so an EIO on a
    failing external volume produced a listing shorter than the directory
    with no exception, no log line and no counter -- indistinguishable from
    a genuinely smaller directory. list_dir feeds corpus staging, so files
    could silently leave the index.
    """
    stats = {
        "ok.md": SimpleNamespace(st_size=2, st_flags=0),
        "flaky.md": OSError(errno.EIO, "Input/output error"),
        "vanished.md": FileNotFoundError(errno.ENOENT, "No such file or directory"),
    }

    def stat_entry(name: str):
        value = stats[name]
        if isinstance(value, OSError):
            raise value
        return value

    with caplog.at_level(logging.WARNING, logger=pathsafe.__name__):
        visible = pathsafe._filter_macos_entries(list(stats), stat_entry)

    # Fail-soft is preserved: the readable entry still comes back.
    assert [name for name, _st in visible] == ["ok.md"]

    messages = [r.getMessage() for r in caplog.records]
    assert any("Skipped 2 unreadable directory entries" in m for m in messages), messages
    joined = " ".join(messages)
    assert "flaky.md" in joined and "vanished.md" in joined


def test_filter_logs_nothing_when_every_entry_is_readable(
    darwin: None, caplog: pytest.LogCaptureFixture
) -> None:
    """No skips means no warning -- the summary must not fire on the happy path."""
    stats = {"a.md": SimpleNamespace(st_size=2, st_flags=0)}
    with caplog.at_level(logging.WARNING, logger=pathsafe.__name__):
        pathsafe._filter_macos_entries(list(stats), stats.__getitem__)
    assert not caplog.records


# _is_macos_volume_path is ground-truth (filesystem-identity) based, not a
# string/case-folding guess -- see its docstring for why. These tests mock
# os.stat to simulate a real /Volumes directory (this sandbox has none) and
# real ancestor relationships, exactly what the real filesystem lookups
# would produce on an actual Mac.

def _stat_lookup(stats: dict[str, SimpleNamespace]):
    def fake_stat(path, *_args, **_kwargs):
        try:
            return stats[path]
        except KeyError:
            raise OSError() from None

    return fake_stat


def test_macos_volume_path_true_for_volumes_itself(darwin: None, monkeypatch) -> None:
    volumes_stat = SimpleNamespace(st_dev=1, st_ino=100)
    monkeypatch.setattr(pathsafe.os, "stat", _stat_lookup({"/Volumes": volumes_stat}))
    assert pathsafe._is_macos_volume_path("/Volumes") is True


def test_macos_volume_path_true_for_real_descendant(darwin: None, monkeypatch) -> None:
    volumes_stat = SimpleNamespace(st_dev=1, st_ino=100)
    stats = {
        "/Volumes": volumes_stat,
        "/Volumes/External": SimpleNamespace(st_dev=1, st_ino=200),
        "/Volumes/External/share": SimpleNamespace(st_dev=1, st_ino=300),
    }
    monkeypatch.setattr(pathsafe.os, "stat", _stat_lookup(stats))
    assert pathsafe._is_macos_volume_path("/Volumes/External/share") is True


def test_macos_volume_path_true_for_case_insensitive_alias(darwin: None, monkeypatch) -> None:
    # Simulates an actually-case-insensitive lookup: a differently-spelled
    # path that os.stat resolves to the SAME real /Volumes entity -- not a
    # blanket "fold case on Darwin" assumption, filesystem ground truth. Both
    # spellings map to one shared stat, exactly what a real case-insensitive
    # volume's directory lookup produces for either spelling.
    volumes_stat = SimpleNamespace(st_dev=1, st_ino=100)
    monkeypatch.setattr(pathsafe.os, "stat", _stat_lookup({"/Volumes": volumes_stat, "/volumes": volumes_stat}))
    assert pathsafe._is_macos_volume_path("/volumes") is True


def test_macos_volume_path_false_for_unrelated_sibling_directory(darwin: None, monkeypatch) -> None:
    # "/VolumesLike" is a real, different directory -- not /Volumes and not
    # inside it -- proven by genuinely different filesystem identity.
    volumes_stat = SimpleNamespace(st_dev=1, st_ino=100)
    stats = {
        "/Volumes": volumes_stat,
        "/VolumesLike": SimpleNamespace(st_dev=1, st_ino=900),
        "/VolumesLike/share": SimpleNamespace(st_dev=1, st_ino=901),
    }
    monkeypatch.setattr(pathsafe.os, "stat", _stat_lookup(stats))
    assert pathsafe._is_macos_volume_path("/VolumesLike/share") is False


def test_macos_volume_path_false_for_relative_path(darwin: None) -> None:
    assert pathsafe._is_macos_volume_path("Volumes/External") is False


def test_macos_volume_path_requires_real_volumes_directory(darwin: None, monkeypatch) -> None:
    # No /Volumes exists at all (e.g. this Linux sandbox) -- nothing can be
    # "inside" a mount point that isn't there.
    monkeypatch.setattr(pathsafe.os, "stat", _stat_lookup({}))
    assert pathsafe._is_macos_volume_path("/Volumes/External/share") is False


def test_resolved_macos_volume_alias_is_refused_at_runtime(darwin: None, monkeypatch) -> None:
    monkeypatch.setattr(pathsafe.Path, "resolve", lambda self, *, strict: self)
    monkeypatch.setattr(pathsafe, "_is_macos_volume_path", lambda _path: True)
    with pytest.raises(FsPathError, match="allow_macos_volume_roots is false"):
        pathsafe.ScopedRoots(
            ["/private/alias"],
            create=False,
            allow_macos_volume_roots=False,
        )


def test_scoped_roots_refuses_macos_volume_by_default(darwin: None, monkeypatch) -> None:
    monkeypatch.setattr(pathsafe.Path, "resolve", lambda self, *, strict: self)
    monkeypatch.setattr(pathsafe, "_is_macos_volume_path", lambda _path: True)
    with pytest.raises(FsPathError, match="allow_macos_volume_roots is false"):
        pathsafe.ScopedRoots(["/private/alias"], create=False)


@pytest.mark.parametrize("error_number", [errno.EPERM, errno.EACCES])
def test_root_stat_permission_is_typed(
    darwin: None,
    monkeypatch,
    error_number: int,
) -> None:
    monkeypatch.setattr(pathsafe.Path, "resolve", lambda self, *, strict: self)

    def denied(_self):
        raise PermissionError(error_number, "denied")

    monkeypatch.setattr(pathsafe.Path, "stat", denied)
    with pytest.raises(FsMacOSPermissionError):
        pathsafe.ScopedRoots(["configured-root"], create=False)


def test_open_fd_is_rechecked_for_dataless_state(darwin: None, monkeypatch) -> None:
    closed: list[int] = []
    monkeypatch.setattr(
        pathsafe.os,
        "fstat",
        lambda _fd: SimpleNamespace(st_mode=pathsafe.statmod.S_IFREG, st_size=0, st_flags=0x40000000),
    )
    monkeypatch.setattr(pathsafe.os, "close", closed.append)

    with pytest.raises(FsPathError, match="dataless placeholder"):
        pathsafe.ScopedRoots._read_fd(123, 1024, skip_macos_metadata=True)
    assert closed == [123]
