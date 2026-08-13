"""Native Windows authority tests for fsconnect read/list/stat.

These tests deliberately run only on Windows. They exercise real NTFS handles and
junctions; they do not monkeypatch ``os.name`` or pretend POSIX temp directories
have Windows reparse semantics.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from agentic.fsconnect.pathsafe import SafeRoot, ScopedRoots, split_components
from utils.errors import FsConnectRuntimeError, FsPathError

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires native Windows handles and junctions")


def _create_junction(link: Path, target: Path) -> None:
    command = shutil.which("cmd.exe")
    if command is None:
        pytest.fail("cmd.exe is unavailable; native junction security coverage cannot run")
    assert command is not None
    completed = subprocess.run(  # noqa: S603 - fixed cmd.exe + mklink argv, pytest-owned temp paths
        [command, "/d", "/c", "mklink", "/J", str(link), str(target)],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        pytest.fail(
            "cannot create an unprivileged NTFS junction; refusing to hide native coverage: "
            f"stdout={completed.stdout.strip()!r}, stderr={completed.stderr.strip()!r}"
        )


@contextmanager
def _replace_directory_with_junction(directory: Path, target: Path) -> Iterator[None]:
    parked = directory.with_name(f"{directory.name}-parked")
    directory.rename(parked)
    try:
        _create_junction(directory, target)
    except BaseException:
        parked.rename(directory)
        raise
    try:
        yield
    finally:
        if directory.exists():
            os.rmdir(directory)
        parked.rename(directory)


def test_native_windows_read_stat_and_same_handle_list(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "note.txt").write_bytes(b"hello")
    sub = root / "sub"
    sub.mkdir()
    (sub / "nested.bin").write_bytes(b"abc")

    with ScopedRoots([str(root)]) as roots:
        assert roots.read_bytes("note.txt", max_bytes=5) == b"hello"
        note_stat = roots.stat("note.txt")
        assert (note_stat["name"], note_stat["type"], note_stat["size"]) == ("note.txt", "file", 5)
        assert roots.stat(".")["type"] == "dir"
        assert [(entry["name"], entry["type"], entry["size"]) for entry in roots.list_dir(".")] == [
            ("note.txt", "file", 5),
            ("sub", "dir", 0),
        ]
        assert roots.list_dir("sub")[0]["name"] == "nested.bin"

    with pytest.raises(FsPathError, match="overlapping roots"):
        ScopedRoots([str(root), str(root).swapcase()])


def test_native_windows_read_cap_uses_open_handle_size(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "large.bin").write_bytes(b"12345")

    with ScopedRoots([str(root)]) as roots, pytest.raises(
        FsConnectRuntimeError,
        match="max_file_bytes",
    ) as raised:
        roots.read_bytes("large.bin", max_bytes=4)
    assert raised.value.details == {"size": 5, "max": 4}


def test_native_windows_directory_enumeration_continues_across_buffers(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    names = [f"entry-{index:04d}-{'x' * 40}.txt" for index in range(700)]
    names.append("Ångström.txt")
    for name in names:
        (root / name).touch()

    with ScopedRoots([str(root)]) as roots:
        listed = [entry["name"] for entry in roots.list_dir(".")]
    assert listed == sorted(names)


@pytest.mark.parametrize(
    ("operation", "target", "legacy_probe", "legacy_expected"),
    [
        (
            lambda roots, value: roots.read_bytes(value, max_bytes=64),
            "sub/secret.txt",
            lambda path: path.read_text(encoding="utf-8"),
            "outside-secret",
        ),
        (
            lambda roots, value: roots.stat(value),
            "sub/secret.txt",
            lambda path: path.stat().st_size,
            14,
        ),
        (
            lambda roots, value: roots.list_dir(value),
            "sub",
            lambda path: sorted(entry.name for entry in os.scandir(path)),
            ["secret.txt"],
        ),
    ],
    ids=["read", "stat", "list"],
)
def test_post_resolve_junction_swap_is_denied(
    tmp_path: Path,
    operation: Callable[[ScopedRoots, str], object],
    target: str,
    legacy_probe: Callable[[Path], object],
    legacy_expected: object,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    (root / "sub").mkdir(parents=True)
    outside.mkdir()
    (root / "sub" / "secret.txt").write_text("inside", encoding="utf-8")
    (outside / "secret.txt").write_text("outside-secret", encoding="utf-8")

    with ScopedRoots([str(root)]) as roots:
        scoped_root = roots.roots[0]
        resolved = roots._win_resolve(scoped_root, split_components(target), must_exist=True)
        with _replace_directory_with_junction(root / "sub", outside):
            legacy_result = legacy_probe(resolved)
            assert legacy_result == legacy_expected
            with pytest.raises(FsPathError, match="outside|reparse"):
                operation(roots, target)


@pytest.mark.parametrize(
    "target",
    [
        r"..\escape.txt",
        r"C:\escape.txt",
        r"\\server\share\file.txt",
        r"note.txt:secret",
        "note.txt.",
        "note.txt ",
        "bad\x00name",
        r"\\?\C:\escape.txt",
    ],
)
def test_windows_hostile_targets_are_rejected(target: str) -> None:
    with pytest.raises(FsPathError):
        split_components(target)


def test_windows_writes_remain_hard_refused_before_root_creation(tmp_path: Path) -> None:
    import yaml

    from agentic.fsconnect.config import load_fsconnect_config
    from agentic.fsconnect.writer import FsWriter
    from utils.errors import FsWriteRefused
    from utils.logger import _get_config, reset_config_cache

    write_root = tmp_path / "write-root-must-not-exist"
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
                "policy": {"prompt_filter": {"banned_patterns": []}, "privacy": {}},
                "fsconnect": {
                    "enabled": True,
                    "writable_roots": [str(write_root)],
                    "writes_enabled": True,
                },
            }
        ),
        encoding="utf-8",
    )
    reset_config_cache()
    try:
        config = _get_config(str(config_path))
        fs_config = load_fsconnect_config(str(config_path))
        with pytest.raises(FsWriteRefused, match="refused on Windows") as raised:
            FsWriter(config, fs_config, config_path=str(config_path))
        assert raised.value.details["failed_gate"] == "platform"
        assert not write_root.exists()
    finally:
        reset_config_cache()


def test_final_junction_object_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    _create_junction(root / "link", outside)
    try:
        with ScopedRoots([str(root)]) as roots:
            with pytest.raises(FsPathError, match="reparse"):
                roots.stat("link")
            with pytest.raises(FsPathError, match="reparse"):
                roots.list_dir("link")
    finally:
        os.rmdir(root / "link")


def test_intermediate_junction_inside_root_is_rejected(tmp_path: Path) -> None:
    root = tmp_path / "root"
    actual = root / "actual"
    actual.mkdir(parents=True)
    (actual / "note.txt").write_text("inside", encoding="utf-8")
    _create_junction(root / "alias", actual)
    try:
        with ScopedRoots([str(root)]) as roots:
            with pytest.raises(FsPathError, match="reparse point|path alias"):
                roots.read_bytes("alias/note.txt", max_bytes=64)
            with pytest.raises(FsPathError, match="reparse point|path alias"):
                roots.stat("alias/note.txt")
            with pytest.raises(FsPathError, match="reparse point|path alias"):
                roots.list_dir("alias")
    finally:
        os.rmdir(root / "alias")


def test_held_root_prevents_directory_replacement(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    (root / "note.txt").write_text("held", encoding="utf-8")
    parked = tmp_path / "root-parked"

    with ScopedRoots([str(root)]) as roots:
        with pytest.raises(OSError):
            root.rename(parked)
        assert not parked.exists()
        assert roots.read_bytes("note.txt", max_bytes=64) == b"held"
        assert roots.stat("note.txt")["size"] == 4
        assert [entry["name"] for entry in roots.list_dir(".")] == ["note.txt"]


def test_held_ancestors_prevent_parent_replacement(tmp_path: Path) -> None:
    parent = tmp_path / "parent"
    root = parent / "root"
    root.mkdir(parents=True)
    (root / "note.txt").write_text("held", encoding="utf-8")
    parked = tmp_path / "parent-parked"

    with ScopedRoots([str(root)]) as roots:
        with pytest.raises(OSError):
            parent.rename(parked)
        assert not parked.exists()
        assert roots.read_bytes("note.txt", max_bytes=64) == b"held"


def test_read_keeps_validated_handle_when_filename_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "note.txt"
    target.write_bytes(b"validated")
    replacement = root / "replacement.tmp"
    replacement.write_bytes(b"new-name")
    parked = root / "validated-open-file"

    with ScopedRoots([str(root)]) as roots:
        original_open = roots._win_open_checked

        def open_then_replace(
            scoped_root: SafeRoot,
            components: list[str],
            *,
            access: int,
        ) -> tuple[int, int]:
            handle_and_attrs = original_open(scoped_root, components, access=access)
            target.rename(parked)
            replacement.rename(target)
            return handle_and_attrs

        monkeypatch.setattr(roots, "_win_open_checked", open_then_replace)
        assert roots.read_bytes("note.txt", max_bytes=64) == b"validated"
        assert target.read_bytes() == b"new-name"


def test_stat_keeps_validated_handle_when_filename_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    target = root / "note.txt"
    target.write_bytes(b"validated")
    replacement = root / "replacement.tmp"
    replacement.write_bytes(b"replacement-is-longer")
    parked = root / "validated-open-file"

    with ScopedRoots([str(root)]) as roots:
        original_open = roots._win_open_checked

        def open_then_replace(
            scoped_root: SafeRoot,
            components: list[str],
            *,
            access: int,
        ) -> tuple[int, int]:
            handle_and_attrs = original_open(scoped_root, components, access=access)
            target.rename(parked)
            replacement.rename(target)
            return handle_and_attrs

        monkeypatch.setattr(roots, "_win_open_checked", open_then_replace)
        assert roots.stat("note.txt")["size"] == len(b"validated")
        assert target.stat().st_size == len(b"replacement-is-longer")


def test_list_keeps_validated_directory_handle_when_name_is_replaced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    old_dir = root / "docs"
    new_dir = root / "replacement"
    old_dir.mkdir(parents=True)
    new_dir.mkdir()
    (old_dir / "old.txt").write_text("old", encoding="utf-8")
    (new_dir / "new.txt").write_text("new", encoding="utf-8")
    parked = root / "docs-open-handle"

    with ScopedRoots([str(root)]) as roots:
        original_open = roots._win_open_checked

        def open_then_replace(
            scoped_root: SafeRoot,
            components: list[str],
            *,
            access: int,
        ) -> tuple[int, int]:
            handle_and_attrs = original_open(scoped_root, components, access=access)
            old_dir.rename(parked)
            new_dir.rename(old_dir)
            return handle_and_attrs

        monkeypatch.setattr(roots, "_win_open_checked", open_then_replace)
        assert [entry["name"] for entry in roots.list_dir("docs")] == ["old.txt"]
        assert [entry.name for entry in os.scandir(old_dir)] == ["new.txt"]
