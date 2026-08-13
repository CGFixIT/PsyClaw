"""Host-independent unit tests for Darwin-only fsconnect policy helpers."""

from __future__ import annotations

import errno
from types import SimpleNamespace

import pytest

from agentic.fsconnect import pathsafe
from utils.errors import FsMacOSPermissionError


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


def test_dataless_flag_requires_zero_size(darwin: None) -> None:
    assert pathsafe._is_macos_dataless(SimpleNamespace(st_size=0, st_flags=0x40000000))
    assert not pathsafe._is_macos_dataless(SimpleNamespace(st_size=1, st_flags=0x40000000))
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
