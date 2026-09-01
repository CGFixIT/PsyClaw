r"""Path-validation security core for the filesystem connector.

This module is the load-bearing security boundary for both reads and writes. It is
standalone and dependency-free (stdlib only) so it can be unit-tested in isolation
against an adversarial symlink/junction/traversal fixture matrix.

Design (red-team-hardened):

  * **POSIX (the authority).** Each allowed root is opened once and its directory
    file descriptor is held for the lifetime of the ``ScopedRoots`` object. Every
    request descends component-by-component from that held fd using
    ``os.open(comp, O_RDONLY|O_NOFOLLOW|O_DIRECTORY, dir_fd=parent_fd)`` -- so the
    kernel walks handles we already hold, ``..`` is rejected up front, and
    ``O_NOFOLLOW`` on **every** hop means a symlink anywhere in the path raises
    ``ELOOP``. There is no validate-then-reopen-by-string step, so the TOCTOU window
    is zero and the result is provably inside the root (you cannot ``openat`` your
    way out of a directory fd without following a link or ``..``). This also makes
    the root immutable for the process: swapping the root's path later does not
    change the inode our held fd points at.

  * **Windows (read authority).** ``os.open`` supports neither ``dir_fd`` nor
    ``O_NOFOLLOW`` on Windows. Each read/stat/list target is therefore opened once
    with ``CreateFileW``. The resulting handle must be non-reparse, its
    ``GetFinalPathNameByHandleW`` path must remain inside the allow-listed root, and
    the root's file identity must still match the identity held at construction.
    Reads and stats use that same handle; directory enumeration uses
    ``GetFileInformationByHandleEx`` on that same directory handle. Windows writes
    remain hard-refused by ``writer.py`` because their legacy name-based helpers do
    not yet provide equivalent handle-relative mutation authority.

Early rejection (both platforms) refuses: empty/NUL targets, absolute or
drive/UNC-prefixed targets (targets are always *relative* to a root), ``..``
components, ``:`` in a component (Alternate Data Streams / drive), trailing dot or
space (Windows aliasing), and ``\\?\`` / ``\\.\`` device spellings.

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

import ctypes
import errno
import hashlib
import logging
import os
import posixpath
import re
import stat as statmod
import sys
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from utils.errors import FsConnectRuntimeError, FsMacOSPermissionError, FsPathError

logger = logging.getLogger(__name__)

# How many skipped entry names to name individually before summarising. A
# failing volume can error on every entry, so the per-directory summary is one
# log line regardless of directory size.
_SKIP_LOG_SAMPLE = 5

_POSIX = os.name != "nt"
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_FILE_ATTRIBUTE_DIRECTORY = 0x10
_FILE_ATTRIBUTE_READONLY = 0x1
_SF_DATALESS = getattr(statmod, "SF_DATALESS", 0x40000000)

_WIN_GENERIC_READ = 0x80000000
_WIN_FILE_LIST_DIRECTORY = 0x0001
_WIN_FILE_READ_ATTRIBUTES = 0x0080
_WIN_FILE_SHARE_READ = 0x00000001
_WIN_FILE_SHARE_WRITE = 0x00000002
_WIN_FILE_SHARE_DELETE = 0x00000004
_WIN_OPEN_EXISTING = 3
_WIN_FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
_WIN_FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
_WIN_FILE_ATTRIBUTE_TAG_INFO_CLASS = 9
_WIN_FILE_FULL_DIRECTORY_INFO_CLASS = 14
_WIN_FILE_FULL_DIRECTORY_RESTART_INFO_CLASS = 15
_WIN_ERROR_NO_MORE_FILES = 18
_WIN_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
_WIN_DIR_BUFFER_BYTES = 64 * 1024
_WIN_EPOCH_OFFSET_100NS = 116_444_736_000_000_000

_SEP_RE = re.compile(r"[\\/]+")


class _WinFileAttributeTagInfo(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("reparse_tag", wintypes.DWORD),
    ]


class _WinByHandleFileInformation(ctypes.Structure):
    _fields_ = [
        ("file_attributes", wintypes.DWORD),
        ("creation_time", wintypes.FILETIME),
        ("last_access_time", wintypes.FILETIME),
        ("last_write_time", wintypes.FILETIME),
        ("volume_serial_number", wintypes.DWORD),
        ("file_size_high", wintypes.DWORD),
        ("file_size_low", wintypes.DWORD),
        ("number_of_links", wintypes.DWORD),
        ("file_index_high", wintypes.DWORD),
        ("file_index_low", wintypes.DWORD),
    ]


class _WinFileFullDirectoryInfo(ctypes.Structure):
    _fields_ = [
        ("next_entry_offset", wintypes.DWORD),
        ("file_index", wintypes.DWORD),
        ("creation_time", ctypes.c_longlong),
        ("last_access_time", ctypes.c_longlong),
        ("last_write_time", ctypes.c_longlong),
        ("change_time", ctypes.c_longlong),
        ("end_of_file", ctypes.c_longlong),
        ("allocation_size", ctypes.c_longlong),
        ("file_attributes", wintypes.DWORD),
        ("file_name_length", wintypes.DWORD),
        ("ea_size", wintypes.DWORD),
        ("file_name", ctypes.c_wchar * 1),
    ]


_WIN_DIRECTORY_NAME_OFFSET = _WinFileFullDirectoryInfo.file_name.offset
_WIN_KERNEL32: Any | None = None
if os.name == "nt":  # pragma: no branch - selected at import time
    _WIN_KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _WIN_KERNEL32.CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.c_void_p,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _WIN_KERNEL32.CreateFileW.restype = wintypes.HANDLE
    _WIN_KERNEL32.CloseHandle.argtypes = [wintypes.HANDLE]
    _WIN_KERNEL32.CloseHandle.restype = wintypes.BOOL
    _WIN_KERNEL32.GetFinalPathNameByHandleW.argtypes = [
        wintypes.HANDLE,
        wintypes.LPWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
    ]
    _WIN_KERNEL32.GetFinalPathNameByHandleW.restype = wintypes.DWORD
    _WIN_KERNEL32.GetFileInformationByHandle.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(_WinByHandleFileInformation),
    ]
    _WIN_KERNEL32.GetFileInformationByHandle.restype = wintypes.BOOL
    _WIN_KERNEL32.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    _WIN_KERNEL32.GetFileInformationByHandleEx.restype = wintypes.BOOL


_WinFileIdentity = tuple[int, int, int]


def _win_api() -> Any:  # pragma: no cover - Windows only
    if _WIN_KERNEL32 is None:
        raise FsPathError("Windows handle APIs are unavailable on this platform")
    return _WIN_KERNEL32


def _win_error(action: str, path: str) -> FsPathError:  # pragma: no cover - Windows only
    error = ctypes.get_last_error()
    return FsPathError(
        f"Windows denied {action}",
        details={"path": path, "winerror": error, "error": ctypes.FormatError(error)},
    )


def _win_close_handle(handle: int) -> None:  # pragma: no cover - Windows only
    if handle >= 0:
        _win_api().CloseHandle(wintypes.HANDLE(handle))


def _win_create_handle(
    path: Path,
    access: int,
    *,
    share_delete: bool = True,
) -> int:  # pragma: no cover - Windows only
    share_mode = _WIN_FILE_SHARE_READ | _WIN_FILE_SHARE_WRITE
    if share_delete:
        share_mode |= _WIN_FILE_SHARE_DELETE
    ctypes.set_last_error(0)
    raw_handle = _win_api().CreateFileW(
        str(path),
        access,
        share_mode,
        None,
        _WIN_OPEN_EXISTING,
        _WIN_FILE_FLAG_OPEN_REPARSE_POINT | _WIN_FILE_FLAG_BACKUP_SEMANTICS,
        None,
    )
    if raw_handle is None or raw_handle == _WIN_INVALID_HANDLE_VALUE:
        raise _win_error("opening a filesystem target", str(path))
    return int(raw_handle)


def _win_handle_attributes(handle: int) -> int:  # pragma: no cover - Windows only
    info = _WinFileAttributeTagInfo()
    ctypes.set_last_error(0)
    ok = _win_api().GetFileInformationByHandleEx(
        wintypes.HANDLE(handle),
        _WIN_FILE_ATTRIBUTE_TAG_INFO_CLASS,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise _win_error("inspecting an open filesystem target", "<open handle>")
    return int(info.file_attributes)


def _win_handle_identity(handle: int) -> _WinFileIdentity:  # pragma: no cover - Windows only
    info = _WinByHandleFileInformation()
    ctypes.set_last_error(0)
    ok = _win_api().GetFileInformationByHandle(wintypes.HANDLE(handle), ctypes.byref(info))
    if not ok:
        raise _win_error("identifying an open filesystem target", "<open handle>")
    return (
        int(info.volume_serial_number),
        int(info.file_index_high),
        int(info.file_index_low),
    )


def _win_normalize_final_path(path: str) -> str:  # pragma: no cover - Windows only
    if path.startswith("\\\\?\\UNC\\"):
        path = "\\\\" + path[8:]
    elif path.startswith("\\\\?\\"):
        path = path[4:]
    return os.path.normpath(path)


def _win_final_path(handle: int) -> str:  # pragma: no cover - Windows only
    size = 512
    while size <= 32_768:
        buffer = ctypes.create_unicode_buffer(size)
        ctypes.set_last_error(0)
        length = _win_api().GetFinalPathNameByHandleW(
            wintypes.HANDLE(handle), buffer, size, 0
        )
        if length == 0:
            raise _win_error("resolving an open filesystem target", "<open handle>")
        if length < size:
            return _win_normalize_final_path(buffer.value)
        size = int(length) + 1
    raise FsPathError("Windows returned an overlong final filesystem path")


def _win_lock_ancestors(
    final_path: str,
    root_identity: _WinFileIdentity,
) -> tuple[int, ...]:  # pragma: no cover - Windows only
    """Lock the canonical root's ancestors against rename, then recheck the root."""
    handles: list[int] = []
    try:
        for ancestor in reversed(Path(final_path).parents):
            handle = _win_create_handle(
                ancestor,
                _WIN_FILE_READ_ATTRIBUTES,
                share_delete=False,
            )
            handles.append(handle)
            if not _win_handle_attributes(handle) & _FILE_ATTRIBUTE_DIRECTORY:
                raise FsPathError("configured Windows root has a non-directory ancestor")

        # Ancestors are acquired top-down. Once all are held without delete
        # sharing, a swap cannot be restored behind this final identity check.
        current = _win_create_handle(Path(final_path), _WIN_FILE_READ_ATTRIBUTES)
        try:
            if _win_handle_identity(current) != root_identity:
                raise FsPathError("configured Windows root changed while its ancestors were locked")
        finally:
            _win_close_handle(current)
        return tuple(handles)
    except BaseException:
        for handle in reversed(handles):
            _win_close_handle(handle)
        raise


def _win_open_root(
    path: Path,
) -> tuple[int, _WinFileIdentity, str, tuple[int, ...]]:  # pragma: no cover - Windows only
    # The scope-lifetime root handle deliberately withholds FILE_SHARE_DELETE.
    # Windows then prevents renaming/replacing the root namespace entry while
    # reads are active, closing a swap-and-swap-back race between identity checks.
    handle = _win_create_handle(
        path,
        _WIN_FILE_LIST_DIRECTORY | _WIN_FILE_READ_ATTRIBUTES,
        share_delete=False,
    )
    try:
        attrs = _win_handle_attributes(handle)
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise FsPathError("configured root is a reparse point")
        if not attrs & _FILE_ATTRIBUTE_DIRECTORY:
            raise FsPathError("configured root is not a directory")
        identity = _win_handle_identity(handle)
        final_path = _win_final_path(handle)
        ancestor_handles = _win_lock_ancestors(final_path, identity)
        return handle, identity, final_path, ancestor_handles
    except BaseException:
        _win_close_handle(handle)
        raise


def _win_assert_root_current(sr: SafeRoot) -> None:  # pragma: no cover - Windows only
    if sr.win_handle < 0 or sr.win_identity is None or sr.win_final_path is None:
        raise FsPathError("configured Windows root has no held identity")
    if _win_handle_identity(sr.win_handle) != sr.win_identity:
        raise FsPathError("held Windows root identity changed")
    current = _win_create_handle(Path(sr.win_final_path), _WIN_FILE_READ_ATTRIBUTES)
    try:
        attrs = _win_handle_attributes(current)
        if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
            raise FsPathError("configured Windows root was replaced by a reparse point")
        if not attrs & _FILE_ATTRIBUTE_DIRECTORY:
            raise FsPathError("configured Windows root is no longer a directory")
        if _win_handle_identity(current) != sr.win_identity:
            raise FsPathError("configured Windows root was replaced")
        if os.path.normcase(_win_final_path(current)) != sr.normcase:
            raise FsPathError("configured Windows root moved")
    finally:
        _win_close_handle(current)


def _win_handle_to_fd(handle: int) -> int:  # pragma: no cover - Windows only
    import msvcrt

    try:
        return msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except OSError as exc:
        _win_close_handle(handle)
        raise FsPathError(
            "cannot attach a Python file descriptor to the validated Windows handle",
            details={"errno": exc.errno, "strerror": exc.strerror},
        ) from exc


def _win_filetime_to_unix_seconds(value: int) -> int:  # pragma: no cover - Windows only
    return max(0, (value - _WIN_EPOCH_OFFSET_100NS) // 10_000_000)


def _win_directory_entry(name: str, info: _WinFileFullDirectoryInfo) -> dict[str, object]:
    attrs = int(info.file_attributes)
    if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
        kind = "symlink"
        mode = statmod.S_IFLNK | 0o777
    elif attrs & _FILE_ATTRIBUTE_DIRECTORY:
        kind = "dir"
        mode = statmod.S_IFDIR | (0o555 if attrs & _FILE_ATTRIBUTE_READONLY else 0o777)
    else:
        kind = "file"
        mode = statmod.S_IFREG | (0o444 if attrs & _FILE_ATTRIBUTE_READONLY else 0o666)
    return {
        "name": name,
        "type": kind,
        "size": int(info.end_of_file),
        "mode": statmod.filemode(mode),
        "mtime": _win_filetime_to_unix_seconds(int(info.last_write_time)),
    }


def _win_list_handle(handle: int) -> list[dict[str, object]]:  # pragma: no cover - Windows only
    entries: list[dict[str, object]] = []
    info_class = _WIN_FILE_FULL_DIRECTORY_RESTART_INFO_CLASS
    while True:
        buffer = ctypes.create_string_buffer(_WIN_DIR_BUFFER_BYTES)
        ctypes.set_last_error(0)
        ok = _win_api().GetFileInformationByHandleEx(
            wintypes.HANDLE(handle),
            info_class,
            buffer,
            len(buffer),
        )
        if not ok:
            error = ctypes.get_last_error()
            if error == _WIN_ERROR_NO_MORE_FILES:
                break
            raise _win_error("enumerating a validated directory handle", "<open handle>")
        info_class = _WIN_FILE_FULL_DIRECTORY_INFO_CLASS
        cursor = 0
        while True:
            if cursor + _WIN_DIRECTORY_NAME_OFFSET > len(buffer):
                raise FsPathError("Windows returned malformed directory information")
            info = _WinFileFullDirectoryInfo.from_buffer_copy(buffer, cursor)
            name_length = int(info.file_name_length)
            if name_length % 2 or cursor + _WIN_DIRECTORY_NAME_OFFSET + name_length > len(buffer):
                raise FsPathError("Windows returned malformed directory entry name data")
            name = ctypes.wstring_at(
                ctypes.addressof(buffer) + cursor + _WIN_DIRECTORY_NAME_OFFSET,
                name_length // ctypes.sizeof(ctypes.c_wchar),
            )
            if name not in {".", ".."}:
                entries.append(_win_directory_entry(name, info))
            next_offset = int(info.next_entry_offset)
            if next_offset == 0:
                break
            if next_offset < _WIN_DIRECTORY_NAME_OFFSET + name_length or cursor + next_offset >= len(buffer):
                raise FsPathError("Windows returned malformed directory entry offsets")
            cursor += next_offset
    return sorted(entries, key=lambda entry: str(entry["name"]))


def _is_same_entity(a: str, b: str) -> bool:
    """True if ``a`` and ``b`` name the same real filesystem entity.

    Compares (st_dev, st_ino) rather than the path strings -- true filesystem
    identity, independent of any case-folding or Unicode-normalization rule a
    given volume may or may not apply. Returns ``False`` (never raises) if
    either path can't be stat'd; callers should treat that as "not a match",
    not as an error.
    """
    try:
        return os.path.samestat(os.stat(a), os.stat(b))
    except OSError:
        return False


def _is_real_descendant(inner_real: str, outer_real: str) -> bool:
    """True if ``inner_real`` is ``outer_real`` itself, or a real (on-disk)
    descendant of it -- judged by filesystem identity, not string spelling.

    Not every macOS/APFS volume is case-insensitive: it's a per-volume,
    format-time choice (a case-sensitive external/network volume can be
    mounted alongside a case-insensitive boot volume), and CPython does not
    expose Darwin's ``_PC_CASE_SENSITIVE`` pathconf key to ask which applies
    at runtime -- verified directly against ``Modules/posixmodule.c`` across
    several CPython versions: no such entry exists in
    ``posix_constants_pathconf``, so ``os.pathconf(path, 'PC_CASE_SENSITIVE')``
    raises ``KeyError`` on every current CPython, including on macOS. So this
    doesn't guess at case-folding rules at all: it walks ``inner_real``'s
    already-``realpath``-resolved (symlink-free) ancestor chain comparing
    filesystem identity against ``outer_real`` at each level via
    ``_is_same_entity``. Because ``inner_real`` has no symlinks left in it,
    every ``dirname``-truncated prefix of it is itself already a canonical,
    symlink-free path, so this can't be tricked by a symlink planted partway
    up the chain. This is the same fallback technique CPython's own test
    suite (``Lib/test/support/os_helper.py``'s ``fs_is_case_insensitive``)
    and git's repository-init code (``setup.c``, probing a scrambled-case
    ``CoNfIg`` spelling) use for the same class of problem.

    Callers should try the cheap lexical ``_norm_contains`` check first and
    call this only as a fallback when that fails -- it costs one or more
    ``stat`` calls.
    """
    try:
        outer_stat = os.stat(outer_real)
    except OSError:
        return False
    current = inner_real
    while True:
        try:
            current_stat = os.stat(current)
        except OSError:
            return False
        if os.path.samestat(current_stat, outer_stat):
            return True
        parent = os.path.dirname(current)
        if parent == current:
            return False
        current = parent


def _is_macos_volume_path(resolved_path: str) -> bool:
    """Ground-truth (post-resolve) check: does ``resolved_path`` name
    ``/Volumes`` itself, or a real (on-disk) descendant of it?

    Called only after ``Path.resolve(strict=True)`` has confirmed the path
    exists, so filesystem identity (device+inode) is available and is the
    ground truth -- this asks the filesystem directly via
    ``os.stat``/``os.path.samestat`` (via ``_is_real_descendant``) rather
    than guessing at a case-folding rule. Not every APFS volume is
    case-insensitive, and Python has no reliable way to ask which applies to
    a given path (CPython does not expose Darwin's ``_PC_CASE_SENSITIVE``
    pathconf key). Walking ``resolved_path``'s already-symlink-free ancestor
    chain comparing filesystem identity against ``/Volumes`` sidesteps the
    guess entirely -- correct regardless of what case rule (if any) the
    volume applies.
    """
    if sys.platform != "darwin" or not posixpath.isabs(resolved_path):
        return False
    return _is_real_descendant(resolved_path, "/Volumes")


def _raise_macos_permission(action: str, exc: OSError) -> None:
    if sys.platform != "darwin" or exc.errno not in {errno.EACCES, errno.EPERM}:
        return
    raise FsMacOSPermissionError(
        f"macOS denied filesystem access while {action}. Grant Files and Folders "
        "access to Terminal or iTerm, whichever launches CyClaw, under System "
        "Settings > Privacy & Security.",
        details={"errno": exc.errno, "strerror": exc.strerror, "action": action},
    ) from exc


def _is_macos_artifact_name(name: str) -> bool:
    return sys.platform == "darwin" and (
        name in {".DS_Store", ".localized"} or name.startswith("._")
    )


def _is_macos_dataless(st: os.stat_result) -> bool:
    """True if ``st`` names a non-resident (iCloud-evicted) file.

    ``SF_DATALESS`` alone is authoritative. A real dataless placeholder's
    ``st_size`` typically still reports the file's full logical size (macOS
    preserves it so Finder/ls can show a correct size without downloading),
    not 0 -- requiring ``st_size == 0`` in addition to the flag would miss
    the common case and let a subsequent read materialize (download) it,
    which is exactly what a read on an offline-first system must not do.
    """
    return sys.platform == "darwin" and bool(getattr(st, "st_flags", 0) & _SF_DATALESS)


def _report_skipped_entries(where: str, count: int, sample: list[tuple[str, str]]) -> None:
    """Log entries a directory walk dropped because stat() failed.

    _raise_macos_permission re-raises only a Darwin EACCES/EPERM denial. Every
    other OSError -- ENOENT from a concurrent delete, EIO on a failing external
    or network volume, ELOOP, ENAMETOOLONG -- falls through to a bare
    ``continue``. Skipping is deliberate (the walk is fail-soft), but it used
    to be silent, so a truncated listing was indistinguishable from a
    genuinely smaller directory. list_dir feeds both the fs_list read op and
    corpus staging via fsconnect/indexer.py, so a silent drop can quietly
    remove files from the index.
    """
    if not count:
        return
    rendered = ", ".join(f"{name!r} ({reason})" for name, reason in sample)
    if count > len(sample):
        rendered += f", ... and {count - len(sample)} more"
    logger.warning("Skipped %d unreadable directory entries in %s: %s", count, where, rendered)


def _filter_macos_entries(
    names: list[str],
    stat_entry: Callable[[str], os.stat_result],
) -> list[tuple[str, os.stat_result]]:
    """Apply the Darwin read/index visibility policy to directory entries."""
    visible: list[tuple[str, os.stat_result]] = []
    # Count everything, retain only the first few names: a failing volume can
    # error on every entry, and holding an OSError per entry would make the
    # bounded one-line diagnostic cost memory proportional to directory size.
    skipped_count = 0
    skipped_sample: list[tuple[str, str]] = []
    for name in names:
        if _is_macos_artifact_name(name):
            continue
        try:
            st = stat_entry(name)
        except OSError as exc:
            _raise_macos_permission(f"checking directory entry {name!r}", exc)
            skipped_count += 1
            if len(skipped_sample) < _SKIP_LOG_SAMPLE:
                skipped_sample.append((name, exc.strerror or exc.__class__.__name__))
            continue
        if _is_macos_dataless(st):
            continue
        visible.append((name, st))
    _report_skipped_entries("the macOS entry filter", skipped_count, skipped_sample)
    return visible


def _norm_contains(parent_norm: str, child_norm: str) -> bool:
    """Segment-aware: is ``child_norm`` inside (or equal to) ``parent_norm``?

    Both arguments must already be ``os.path.normcase``-normalized absolute paths.
    Uses a trailing-separator check, never a bare ``startswith`` -- so
    ``/allow_dir_sensitive`` is NOT considered inside ``/allow_dir``
    (closes the CVE-2025-53110 sibling-prefix bypass).
    """
    if parent_norm == child_norm:
        return True
    prefix = parent_norm if parent_norm.endswith(os.sep) else parent_norm + os.sep
    return child_norm.startswith(prefix)


def split_components(target: str) -> list[str]:
    """Validate ``target`` and split it into safe relative path components.

    Returns ``[]`` for a target that refers to the root itself (``""`` / ``"."``).
    Raises ``FsPathError`` on anything that could escape a root or alias a file.
    """
    if not isinstance(target, str):
        raise FsPathError("target must be a string", details={"type": type(target).__name__})
    if "\x00" in target:
        raise FsPathError("target contains a NUL byte")
    if target == "" or target == ".":
        return []
    if target.startswith(("\\\\", "//")):
        raise FsPathError("UNC targets are not allowed; use a path relative to a root")
    if os.path.isabs(target):
        raise FsPathError("absolute targets are not allowed; use a path relative to a root")
    if "\\\\?\\" in target or "\\\\.\\" in target:
        raise FsPathError("device-namespace (\\\\?\\ / \\\\.\\) targets are not allowed")

    comps: list[str] = []
    for raw in _SEP_RE.split(target):
        if raw == "" or raw == ".":
            continue
        if raw == "..":
            raise FsPathError("'..' is not allowed in a target")
        if ":" in raw:
            raise FsPathError("':' is not allowed in a path component (drive letter / ADS)")
        if raw != raw.rstrip(" ."):
            raise FsPathError("trailing dot or space is not allowed in a path component")
        comps.append(raw)
    return comps


@dataclass
class SafeRoot:
    """A single allow-listed root: resolved canonical path + held directory fd."""

    requested: str
    path: Path
    normcase: str
    dir_fd: int  # POSIX: a held O_DIRECTORY fd; Windows: -1 (unused)
    win_handle: int = -1
    win_identity: _WinFileIdentity | None = None
    win_final_path: str | None = None
    win_ancestor_handles: tuple[int, ...] = ()


class ScopedRoots:
    """A set of allow-listed roots that validates targets against them.

    Use as a context manager (or call :meth:`close`) so the held directory fds are
    released. ``create=True`` (write scope) makes missing roots; ``create=False``
    (read scope) requires them to exist.
    """

    def __init__(
        self,
        root_strs: list[str],
        *,
        create: bool = False,
        allow_unc: bool = False,
        allow_macos_volume_roots: bool = False,
        strict_roots: bool = False,
        on_fallback: Callable[[str, str], None] | None = None,
    ) -> None:
        self.allow_unc = allow_unc
        self.allow_macos_volume_roots = allow_macos_volume_roots
        self.strict_roots = strict_roots
        self._on_fallback = on_fallback
        self._roots: list[SafeRoot] = []
        try:
            self._open_roots(root_strs, create=create)
        except BaseException:
            # A root that fails validation aborts __init__, so the caller never
            # receives the object and can never reach close()/__exit__ -- every
            # directory fd opened by an EARLIER iteration would leak for the
            # life of the process. Release what was opened, then let the
            # original error propagate untouched.
            self.close()
            raise

    def _open_roots(self, root_strs: list[str], *, create: bool) -> None:
        """Validate each root and record it with a held directory fd.

        Partial failure is the caller's to clean up: see __init__, which is the
        only caller and which closes whatever this managed to open.
        """
        seen: list[tuple[str, str]] = []  # (normcase, resolved-str) pairs
        for raw in root_strs:
            resolved = self._prepare_root(raw, create=create)
            norm = os.path.normcase(str(resolved))
            resolved_str = str(resolved)
            for other_norm, other_resolved in seen:
                if (
                    _norm_contains(other_norm, norm)
                    or _norm_contains(norm, other_norm)
                    or _is_real_descendant(resolved_str, other_resolved)
                    or _is_real_descendant(other_resolved, resolved_str)
                ):
                    raise FsPathError(
                        f"overlapping roots are not allowed: {raw!r}",
                        details={"root": raw},
                    )
            seen.append((norm, resolved_str))
            win_handle = -1
            win_identity: _WinFileIdentity | None = None
            win_final_path: str | None = None
            win_ancestor_handles: tuple[int, ...] = ()
            if _POSIX:
                try:
                    dir_fd = os.open(str(resolved), os.O_RDONLY | _O_DIRECTORY)
                except OSError as exc:
                    _raise_macos_permission(f"opening configured root {raw!r}", exc)
                    raise FsPathError(
                        f"cannot open configured root {raw!r}",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
            else:
                dir_fd = -1
                if os.name == "nt":
                    win_handle, win_identity, win_final_path, win_ancestor_handles = _win_open_root(resolved)
                    norm = os.path.normcase(win_final_path)
            self._roots.append(
                SafeRoot(
                    requested=raw,
                    path=resolved,
                    normcase=norm,
                    dir_fd=dir_fd,
                    win_handle=win_handle,
                    win_identity=win_identity,
                    win_final_path=win_final_path,
                    win_ancestor_handles=win_ancestor_handles,
                )
            )

    def _prepare_root(self, raw: str, *, create: bool) -> Path:
        path = Path(os.path.expanduser(os.path.expandvars(raw)))
        if create:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except PermissionError as exc:
                _raise_macos_permission(f"preparing configured root {raw!r}", exc)
                # Documented fallback for the default service path (/var/lib/cyclaw-fs):
                # if we cannot create it, fall back to a home-dir share that needs no
                # root. Phase 2 makes this fallback (a) refusable and (b) audited: with
                # strict_roots the misconfiguration halts (fail closed) instead of
                # silently relocating writes; otherwise the fallback fires an
                # fsconnect_root_fallback audit event via the on_fallback callback so
                # the operator can detect config drift. (R-7.)
                if self.strict_roots:
                    raise FsPathError(
                        f"cannot prepare writable root {raw!r} and strict_roots is set; "
                        "refusing the ~/CyClaw-FS fallback (fail closed)",
                        details={"root": raw, "error": str(exc)},
                    ) from exc
                fallback = Path(os.path.expanduser("~/CyClaw-FS"))
                fallback.mkdir(parents=True, exist_ok=True)
                if self._on_fallback is not None:
                    self._on_fallback(raw, str(fallback))
                path = fallback
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            _raise_macos_permission(f"resolving configured root {raw!r}", exc)
            raise FsPathError(
                f"root does not exist or cannot be resolved: {raw!r}",
                details={"error": str(exc)},
            ) from exc
        except RuntimeError as exc:
            raise FsPathError(
                f"root does not exist or cannot be resolved: {raw!r}",
                details={"error": str(exc)},
            ) from exc
        if _is_macos_volume_path(str(resolved)) and not self.allow_macos_volume_roots:
            raise FsPathError(
                f"configured root resolves under macOS /Volumes but allow_macos_volume_roots is false: {raw!r}",
                details={"root": raw, "resolved": str(resolved)},
            )
        try:
            resolved_stat = resolved.stat()
        except OSError as exc:
            _raise_macos_permission(f"checking configured root {raw!r}", exc)
            raise FsPathError(
                f"cannot stat configured root {raw!r}",
                details={"errno": exc.errno, "strerror": exc.strerror},
            ) from exc
        if not statmod.S_ISDIR(resolved_stat.st_mode):
            raise FsPathError(f"root is not a directory: {raw!r}", details={"resolved": str(resolved)})
        return resolved

    # --- lifecycle --------------------------------------------------------

    def close(self) -> None:
        for r in self._roots:
            if r.dir_fd >= 0:
                with suppress(OSError):
                    os.close(r.dir_fd)
            if r.win_handle >= 0:
                _win_close_handle(r.win_handle)
            for handle in reversed(r.win_ancestor_handles):
                _win_close_handle(handle)
        self._roots = []

    def __enter__(self) -> ScopedRoots:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    @property
    def roots(self) -> list[SafeRoot]:
        return list(self._roots)

    def pick_root(self, root_arg: str | None) -> SafeRoot:
        if not self._roots:
            raise FsPathError("no roots configured for this scope")
        if root_arg is None:
            if len(self._roots) == 1:
                return self._roots[0]
            raise FsPathError(
                "multiple roots configured; specify which root",
                details={"roots": [r.requested for r in self._roots]},
            )
        expanded_arg = str(Path(os.path.expanduser(os.path.expandvars(root_arg))))
        norm_arg = os.path.normcase(expanded_arg)
        for r in self._roots:
            if r.requested == root_arg or r.normcase == norm_arg or _is_same_entity(expanded_arg, str(r.path)):
                return r
        raise FsPathError(
            f"root not in the configured allow-list: {root_arg!r}",
            details={"allowed": [r.requested for r in self._roots]},
        )

    # --- POSIX core (authority) ------------------------------------------

    @contextmanager
    def _descend_posix(self, root: SafeRoot, comps: list[str]) -> Iterator[tuple[int, str]]:
        """Yield ``(parent_dir_fd, leaf_name)`` for a non-empty ``comps``.

        Descends from the held root fd with ``O_NOFOLLOW`` on every directory hop.
        Intermediate fds are closed on exit; the root fd is never closed here.
        """
        if not comps:
            raise FsPathError("operation requires a file/dir name under the root, not the root itself")
        intermediates: list[int] = []
        dir_fd = root.dir_fd
        try:
            for comp in comps[:-1]:
                try:
                    nfd = os.open(comp, os.O_RDONLY | _O_NOFOLLOW | _O_DIRECTORY, dir_fd=dir_fd)
                except OSError as exc:
                    _raise_macos_permission(f"descending into {comp!r}", exc)
                    raise FsPathError(
                        f"cannot descend into {comp!r}",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                intermediates.append(nfd)
                dir_fd = nfd
            yield dir_fd, comps[-1]
        finally:
            for fd in reversed(intermediates):
                with suppress(OSError):
                    os.close(fd)

    # --- public operations ------------------------------------------------

    def read_bytes(
        self,
        target: str,
        *,
        root: str | None = None,
        max_bytes: int,
        skip_macos_metadata: bool = False,
    ) -> bytes:
        comps = split_components(target)
        sr = self.pick_root(root)
        if skip_macos_metadata and comps and _is_macos_artifact_name(comps[-1]):
            raise FsPathError(f"macOS metadata file is not exposed: {comps[-1]!r}")
        if _POSIX:
            with self._descend_posix(sr, comps) as (pfd, leaf):
                if skip_macos_metadata:
                    try:
                        pre_open_stat = os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
                    except OSError as exc:
                        _raise_macos_permission(f"checking {leaf!r} before reading", exc)
                        raise FsPathError(
                            f"cannot stat {leaf!r} before reading",
                            details={"errno": exc.errno, "strerror": exc.strerror},
                        ) from exc
                    if _is_macos_dataless(pre_open_stat):
                        raise FsPathError(f"iCloud dataless placeholder is not read: {leaf!r}")
                try:
                    fd = os.open(leaf, os.O_RDONLY | _O_NOFOLLOW, dir_fd=pfd)
                except OSError as exc:
                    _raise_macos_permission(f"opening {leaf!r} for reading", exc)
                    raise FsPathError(
                        f"cannot open {leaf!r} for reading",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                return self._read_fd(fd, max_bytes, skip_macos_metadata=skip_macos_metadata)
        return self._read_win(sr, comps, max_bytes)  # pragma: no cover - Windows only

    @staticmethod
    def _read_fd(fd: int, max_bytes: int, *, skip_macos_metadata: bool = False) -> bytes:
        try:
            st = os.fstat(fd)
            if not statmod.S_ISREG(st.st_mode):
                raise FsPathError("target is not a regular file")
            if skip_macos_metadata and _is_macos_dataless(st):
                raise FsPathError("iCloud dataless placeholder is not read")
            if st.st_size > max_bytes:
                raise FsConnectRuntimeError(
                    f"file exceeds max_file_bytes ({max_bytes})",
                    details={"size": st.st_size, "max": max_bytes},
                )
        except OSError as exc:
            os.close(fd)
            _raise_macos_permission("checking an open file", exc)
            raise FsPathError(
                "cannot inspect the open file",
                details={"errno": exc.errno, "strerror": exc.strerror},
            ) from exc
        except BaseException:
            os.close(fd)
            raise
        try:
            with os.fdopen(fd, "rb", closefd=True) as f:
                data = f.read(max_bytes + 1)
        except OSError as exc:
            _raise_macos_permission("reading an open file", exc)
            raise FsConnectRuntimeError(
                "failed while reading an open file",
                details={"errno": exc.errno, "strerror": exc.strerror},
            ) from exc
        if len(data) > max_bytes:
            raise FsConnectRuntimeError(
                f"file exceeds max_file_bytes ({max_bytes})",
                details={"max": max_bytes},
            )
        return data

    def stat(self, target: str, *, root: str | None = None) -> dict:
        comps = split_components(target)
        sr = self.pick_root(root)
        if _POSIX:
            if not comps:
                try:
                    return self._stat_to_dict(".", os.fstat(sr.dir_fd))
                except OSError as exc:
                    _raise_macos_permission("checking the configured root", exc)
                    raise FsPathError(
                        "cannot stat the configured root",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
            with self._descend_posix(sr, comps) as (pfd, leaf):
                try:
                    st = os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
                except OSError as exc:
                    _raise_macos_permission(f"checking {leaf!r}", exc)
                    raise FsPathError(
                        f"cannot stat {leaf!r}",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                return self._stat_to_dict(leaf, st)
        return self._stat_win(sr, comps)  # pragma: no cover - Windows only

    def list_dir(
        self,
        target: str,
        *,
        root: str | None = None,
        skip_macos_metadata: bool = False,
    ) -> list[dict]:
        comps = split_components(target)
        sr = self.pick_root(root)
        if _POSIX:
            if not comps:
                return self._listdir_fd(sr.dir_fd, skip_macos_metadata=skip_macos_metadata)
            with self._descend_posix(sr, comps) as (pfd, leaf):
                try:
                    lfd = os.open(leaf, os.O_RDONLY | _O_NOFOLLOW | _O_DIRECTORY, dir_fd=pfd)
                except OSError as exc:
                    _raise_macos_permission(f"opening directory {leaf!r}", exc)
                    raise FsPathError(
                        f"cannot open directory {leaf!r}",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                try:
                    return self._listdir_fd(lfd, skip_macos_metadata=skip_macos_metadata)
                finally:
                    with suppress(OSError):
                        os.close(lfd)
        return self._list_win(sr, comps)  # pragma: no cover - Windows only

    def _listdir_fd(self, dir_fd: int, *, skip_macos_metadata: bool = False) -> list[dict]:
        entries: list[dict] = []
        try:
            names = sorted(os.listdir(dir_fd))
        except OSError as exc:
            _raise_macos_permission("listing a configured directory", exc)
            raise FsPathError(
                "cannot list a configured directory",
                details={"errno": exc.errno, "strerror": exc.strerror},
            ) from exc
        if skip_macos_metadata:
            filtered = _filter_macos_entries(
                names,
                lambda name: os.stat(name, dir_fd=dir_fd, follow_symlinks=False),
            )
            return [self._stat_to_dict(name, st) for name, st in filtered]
        skipped_count = 0
        skipped_sample: list[tuple[str, str]] = []
        for name in names:
            try:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except OSError as exc:
                _raise_macos_permission(f"checking directory entry {name!r}", exc)
                skipped_count += 1
                if len(skipped_sample) < _SKIP_LOG_SAMPLE:
                    skipped_sample.append((name, exc.strerror or exc.__class__.__name__))
                continue
            entries.append(self._stat_to_dict(name, st))
        _report_skipped_entries("list_dir", skipped_count, skipped_sample)
        return entries

    @staticmethod
    def _stat_to_dict(name: str, st: os.stat_result) -> dict:
        if statmod.S_ISDIR(st.st_mode):
            kind = "dir"
        elif statmod.S_ISREG(st.st_mode):
            kind = "file"
        elif statmod.S_ISLNK(st.st_mode):
            kind = "symlink"
        else:
            kind = "other"
        return {
            "name": name,
            "type": kind,
            "size": int(st.st_size),
            "mode": statmod.filemode(st.st_mode),
            "mtime": int(st.st_mtime),
        }

    def write_bytes(
        self, target: str, data: bytes, *, root: str | None = None, overwrite: bool
    ) -> dict:
        comps = split_components(target)
        if not comps:
            raise FsPathError("write target must be a file under the root")
        sr = self.pick_root(root)
        sha = hashlib.sha256(data).hexdigest()
        if _POSIX:
            with self._descend_posix(sr, comps) as (pfd, leaf):
                self._guard_clobber_posix(pfd, leaf, overwrite)
                self._atomic_write_posix(pfd, leaf, data)
            return {"bytes": len(data), "sha256": sha, "path": self._display(sr, comps)}
        return self._write_win(sr, comps, data, overwrite, sha)  # pragma: no cover

    @staticmethod
    def _guard_clobber_posix(pfd: int, leaf: str, overwrite: bool) -> None:
        if overwrite:
            return
        try:
            os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise FsConnectRuntimeError(
            f"{leaf!r} already exists; pass overwrite to replace it",
            details={"clobber": leaf},
        )

    def _atomic_write_posix(self, pfd: int, leaf: str, data: bytes) -> None:
        tmp = f".{leaf}.{os.getpid()}.cyclaw-tmp"
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | _O_NOFOLLOW, 0o600, dir_fd=pfd)
        except OSError as exc:
            raise FsConnectRuntimeError(
                "could not create temp file for atomic write",
                details={"errno": exc.errno, "strerror": exc.strerror},
            ) from exc
        try:
            with os.fdopen(fd, "wb", closefd=True) as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, leaf, src_dir_fd=pfd, dst_dir_fd=pfd)
            self._fsync_dir(pfd)
        except BaseException as exc:
            with suppress(OSError):
                os.unlink(tmp, dir_fd=pfd)
            # An OSError from the write/replace/fsync becomes a typed error, the
            # same way the os.open three lines above already does. Without this
            # the commonest case -- os.replace onto an existing DIRECTORY, i.e.
            # one mistyped --path -- escaped as a raw IsADirectoryError, which is
            # outside the 0/2/3/4 exit contract AND left an
            # fsconnect_write_intent with no matching _applied, the exact shape
            # writer.py's docstring defines as the crash/tamper signal.
            # BaseException is kept so KeyboardInterrupt still unlinks the temp.
            if isinstance(exc, OSError):
                raise FsConnectRuntimeError(
                    "could not complete atomic write",
                    details={"errno": exc.errno, "strerror": exc.strerror},
                ) from exc
            raise

    @staticmethod
    def _fsync_dir(dir_fd: int) -> None:
        """Best-effort fsync of a directory fd so a rename/unlink is crash-durable.

        Completes the atomicity story the module docstring claims: ``os.replace`` is
        atomic w.r.t. concurrent readers, but the rename itself is not guaranteed
        durable across a power loss until the *parent directory* is fsynced. Suppress
        OSError -- some filesystems reject directory fsync (EINVAL); durability is
        best-effort there and the atomic-visibility guarantee is unaffected. (R-6.)
        """
        with suppress(OSError):
            os.fsync(dir_fd)

    def append_bytes(self, target: str, data: bytes, *, root: str | None = None) -> dict:
        comps = split_components(target)
        if not comps:
            raise FsPathError("append target must be a file under the root")
        sr = self.pick_root(root)
        if _POSIX:
            with self._descend_posix(sr, comps) as (pfd, leaf):
                try:
                    fd = os.open(
                        leaf, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _O_NOFOLLOW, 0o600, dir_fd=pfd
                    )
                except OSError as exc:
                    raise FsPathError(
                        f"cannot open {leaf!r} for append",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                with os.fdopen(fd, "ab", closefd=True) as f:
                    f.write(data)
            return {"bytes": len(data), "path": self._display(sr, comps)}
        return self._append_win(sr, comps, data)  # pragma: no cover

    def mkdir(self, target: str, *, root: str | None = None) -> dict:
        comps = split_components(target)
        if not comps:
            raise FsPathError("mkdir target must be a name under the root")
        sr = self.pick_root(root)
        if _POSIX:
            with self._descend_posix(sr, comps) as (pfd, leaf):
                try:
                    os.mkdir(leaf, 0o755, dir_fd=pfd)
                except FileExistsError as exc:
                    raise FsConnectRuntimeError(
                        f"{leaf!r} already exists", details={"path": leaf}
                    ) from exc
                except OSError as exc:
                    raise FsPathError(
                        f"cannot mkdir {leaf!r}",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
            return {"created": self._display(sr, comps)}
        return self._mkdir_win(sr, comps)  # pragma: no cover

    def move(self, src: str, dst: str, *, root: str | None = None, overwrite: bool = False) -> dict:
        scomps = split_components(src)
        dcomps = split_components(dst)
        if not scomps or not dcomps:
            raise FsPathError("move source and destination must both name files under a root")
        sr = self.pick_root(root)
        if _POSIX:
            with self._descend_posix(sr, scomps) as (spfd, sleaf), \
                 self._descend_posix(sr, dcomps) as (dpfd, dleaf):
                if not overwrite:
                    try:
                        os.stat(dleaf, dir_fd=dpfd, follow_symlinks=False)
                    except FileNotFoundError:
                        pass
                    else:
                        raise FsConnectRuntimeError(
                            f"destination {dleaf!r} exists; pass overwrite",
                            details={"clobber": dleaf},
                        )
                try:
                    os.replace(sleaf, dleaf, src_dir_fd=spfd, dst_dir_fd=dpfd)
                except OSError as exc:
                    raise FsConnectRuntimeError(
                        "move failed",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                self._fsync_dir(dpfd)
            return {"from": self._display(sr, scomps), "to": self._display(sr, dcomps)}
        return self._move_win(sr, scomps, dcomps, overwrite)  # pragma: no cover

    def unlink(self, target: str, *, root: str | None = None, sha_max_bytes: int | None = None) -> dict:
        """Hard-delete a regular file (``os.unlink`` with ``dir_fd``). Refuses directories.

        Mechanism only -- policy (gates, allow_hard_delete) lives in the writer. The
        leaf is opened only via the held descent fds (``O_NOFOLLOW``), so a symlink
        leaf is refused, never followed. Returns the pre-delete size and (for regular
        files at/under ``sha_max_bytes``) a content sha256 for the purge audit record.
        """
        comps = split_components(target)
        if not comps:
            raise FsPathError("unlink target must be a file under the root")
        sr = self.pick_root(root)
        if _POSIX:
            with self._descend_posix(sr, comps) as (pfd, leaf):
                try:
                    st = os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
                except OSError as exc:
                    raise FsPathError(
                        f"cannot stat {leaf!r} for unlink",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                if statmod.S_ISDIR(st.st_mode):
                    raise FsPathError("unlink target is a directory; use rmdir")
                size = int(st.st_size)
                sha = self._sha_leaf_posix(pfd, leaf, st, size, sha_max_bytes)
                try:
                    os.unlink(leaf, dir_fd=pfd)
                except OSError as exc:
                    raise FsPathError(
                        f"cannot unlink {leaf!r}",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                self._fsync_dir(pfd)
            return {"removed": self._display(sr, comps), "size": size, "sha256": sha}
        return self._unlink_win(sr, comps, sha_max_bytes)  # pragma: no cover - Windows only

    @staticmethod
    def _sha_leaf_posix(
        pfd: int, leaf: str, st: os.stat_result, size: int, sha_max_bytes: int | None
    ) -> str | None:
        """Stream a regular file's content sha256 through the held descent fd.

        Returns ``None`` for non-regular files or when ``size`` exceeds
        ``sha_max_bytes`` (so a purge of a huge file never buffers it whole).
        """
        if not statmod.S_ISREG(st.st_mode):
            return None
        if sha_max_bytes is not None and size > sha_max_bytes:
            return None
        try:
            fd = os.open(leaf, os.O_RDONLY | _O_NOFOLLOW, dir_fd=pfd)
        except OSError:
            return None
        h = hashlib.sha256()
        with os.fdopen(fd, "rb", closefd=True) as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()

    def rmdir(self, target: str, *, root: str | None = None) -> dict:
        """Remove an EMPTY directory (``os.rmdir`` with ``dir_fd``). Refuses files.

        ``ENOTEMPTY`` surfaces as ``FsConnectRuntimeError`` -- the writer maps it to a
        typed ``FsWriteRefused(failed_gate='non_empty_dir')``; recursive hard delete is
        deliberately not offered in Phase 2.
        """
        comps = split_components(target)
        if not comps:
            raise FsPathError("rmdir target must be a directory under the root")
        sr = self.pick_root(root)
        if _POSIX:
            with self._descend_posix(sr, comps) as (pfd, leaf):
                try:
                    st = os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
                except OSError as exc:
                    raise FsPathError(
                        f"cannot stat {leaf!r} for rmdir",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                if not statmod.S_ISDIR(st.st_mode):
                    raise FsPathError("rmdir target is not a directory; use unlink")
                try:
                    os.rmdir(leaf, dir_fd=pfd)
                except OSError as exc:
                    if exc.errno == errno.ENOTEMPTY:
                        raise FsConnectRuntimeError(
                            f"directory {leaf!r} is not empty",
                            details={"errno": exc.errno, "non_empty": True},
                        ) from exc
                    raise FsPathError(
                        f"cannot rmdir {leaf!r}",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                self._fsync_dir(pfd)
            return {"removed": self._display(sr, comps), "kind": "dir"}
        return self._rmdir_win(sr, comps)  # pragma: no cover - Windows only

    def _unlink_win(  # pragma: no cover - Windows only
        self, sr: SafeRoot, comps: list[str], sha_max_bytes: int | None
    ) -> dict:
        real = self._win_resolve(sr, comps, must_exist=True)
        if real.is_dir():
            raise FsPathError("unlink target is a directory; use rmdir")
        size = real.stat().st_size
        sha: str | None = None
        if sha_max_bytes is None or size <= sha_max_bytes:
            sha = hashlib.sha256(real.read_bytes()).hexdigest()
        real.unlink()
        return {"removed": str(real), "size": int(size), "sha256": sha}

    def _rmdir_win(self, sr: SafeRoot, comps: list[str]) -> dict:  # pragma: no cover - Windows only
        real = self._win_resolve(sr, comps, must_exist=True)
        if not real.is_dir():
            raise FsPathError("rmdir target is not a directory; use unlink")
        try:
            real.rmdir()
        except OSError as exc:
            if exc.errno == errno.ENOTEMPTY:
                raise FsConnectRuntimeError(
                    f"directory {real} is not empty", details={"non_empty": True}
                ) from exc
            raise
        return {"removed": str(real), "kind": "dir"}

    @staticmethod
    def _display(sr: SafeRoot, comps: list[str]) -> str:
        return str(sr.path.joinpath(*comps)) if comps else str(sr.path)

    # --- Windows fallbacks (not exercised by Linux CI) -------------------

    def _win_open_checked(  # pragma: no cover - Windows only
        self,
        sr: SafeRoot,
        comps: list[str],
        *,
        access: int,
    ) -> tuple[int, int]:
        _win_assert_root_current(sr)
        if sr.win_final_path is None:
            raise FsPathError("configured Windows root has no canonical handle path")
        candidate = Path(sr.win_final_path).joinpath(*comps)
        handle = _win_create_handle(candidate, access)
        try:
            attrs = _win_handle_attributes(handle)
            if attrs & _FILE_ATTRIBUTE_REPARSE_POINT:
                raise FsPathError("reparse point (symlink/junction) target is not allowed")
            final_path = _win_final_path(handle)
            if not _norm_contains(sr.normcase, os.path.normcase(final_path)):
                raise FsPathError("open handle resolves outside the allowed root")
            # Containment alone would still permit an intermediate junction that
            # redirects to another directory *inside* the root.  The config
            # contract is stricter: follow_symlinks is hard-false.  A canonical
            # handle path that differs from the requested path proves that some
            # alias/reparse traversal occurred, so refuse it before consumption.
            candidate_norm = os.path.normcase(os.path.normpath(str(candidate)))
            if os.path.normcase(final_path) != candidate_norm:
                raise FsPathError("open handle traversed a reparse point or path alias")
            _win_assert_root_current(sr)
            return handle, attrs
        except BaseException:
            _win_close_handle(handle)
            raise

    def _win_resolve(self, sr: SafeRoot, comps: list[str], *, must_exist: bool) -> Path:  # pragma: no cover
        candidate = sr.path
        for c in comps:
            candidate = candidate / c
        real = Path(os.path.realpath(str(candidate)))
        if not _norm_contains(sr.normcase, os.path.normcase(str(real))):
            raise FsPathError("resolved path escapes the allowed root")
        probe = sr.path
        for c in comps:
            probe = probe / c
            try:
                st = os.lstat(str(probe))
            except FileNotFoundError:
                break
            tag = getattr(st, "st_reparse_tag", 0)
            attrs = getattr(st, "st_file_attributes", 0)
            if tag or (attrs & _FILE_ATTRIBUTE_REPARSE_POINT):
                raise FsPathError("reparse point (symlink/junction) in path is not allowed")
        if must_exist and not real.exists():
            raise FsPathError("target does not exist")
        return real

    def _read_win(self, sr: SafeRoot, comps: list[str], max_bytes: int) -> bytes:  # pragma: no cover
        if not comps:
            raise FsPathError("target is a directory")
        handle, attrs = self._win_open_checked(sr, comps, access=_WIN_GENERIC_READ)
        if attrs & _FILE_ATTRIBUTE_DIRECTORY:
            _win_close_handle(handle)
            raise FsPathError("target is not a regular file")
        return self._read_fd(_win_handle_to_fd(handle), max_bytes)

    def _stat_win(self, sr: SafeRoot, comps: list[str]) -> dict:  # pragma: no cover
        handle, _attrs = self._win_open_checked(sr, comps, access=_WIN_FILE_READ_ATTRIBUTES)
        fd = _win_handle_to_fd(handle)
        try:
            st = os.fstat(fd)
        except OSError as exc:
            raise FsPathError(
                "cannot stat the validated Windows handle",
                details={"errno": exc.errno, "strerror": exc.strerror},
            ) from exc
        finally:
            with suppress(OSError):
                os.close(fd)
        return self._stat_to_dict(comps[-1] if comps else ".", st)

    def _list_win(self, sr: SafeRoot, comps: list[str]) -> list[dict]:  # pragma: no cover
        handle, attrs = self._win_open_checked(
            sr,
            comps,
            access=_WIN_FILE_LIST_DIRECTORY | _WIN_FILE_READ_ATTRIBUTES,
        )
        try:
            if not attrs & _FILE_ATTRIBUTE_DIRECTORY:
                raise FsPathError("target is not a directory")
            return _win_list_handle(handle)
        finally:
            _win_close_handle(handle)

    def _write_win(  # pragma: no cover
        self, sr: SafeRoot, comps: list[str], data: bytes, overwrite: bool, sha: str
    ) -> dict:
        real = self._win_resolve(sr, comps, must_exist=False)
        if real.exists() and not overwrite:
            raise FsConnectRuntimeError(f"{real} already exists; pass overwrite")
        tmp = real.with_name(f".{real.name}.{os.getpid()}.cyclaw-tmp")
        tmp.write_bytes(data)
        os.replace(tmp, real)
        return {"bytes": len(data), "sha256": sha, "path": str(real)}

    def _append_win(self, sr: SafeRoot, comps: list[str], data: bytes) -> dict:  # pragma: no cover
        real = self._win_resolve(sr, comps, must_exist=False)
        with open(real, "ab") as f:
            f.write(data)
        return {"bytes": len(data), "path": str(real)}

    def _mkdir_win(self, sr: SafeRoot, comps: list[str]) -> dict:  # pragma: no cover
        real = self._win_resolve(sr, comps, must_exist=False)
        try:
            real.mkdir()
        except FileExistsError as exc:
            # Mirror the POSIX branch: callers (e.g. writer._ensure_trash) suppress
            # FsConnectRuntimeError for the already-exists case, not raw OSError.
            raise FsConnectRuntimeError(
                f"{real} already exists", details={"path": str(real)}
            ) from exc
        return {"created": str(real)}

    def _move_win(  # pragma: no cover
        self, sr: SafeRoot, scomps: list[str], dcomps: list[str], overwrite: bool
    ) -> dict:
        s = self._win_resolve(sr, scomps, must_exist=True)
        d = self._win_resolve(sr, dcomps, must_exist=False)
        if d.exists() and not overwrite:
            raise FsConnectRuntimeError(f"destination {d} exists; pass overwrite")
        os.replace(s, d)
        return {"from": str(s), "to": str(d)}


__all__ = ["ScopedRoots", "SafeRoot", "split_components"]
