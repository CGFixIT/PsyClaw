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

  * **Windows (documented fallback).** ``os.open`` supports neither ``dir_fd`` nor
    ``O_NOFOLLOW`` on Windows, so we canonicalize (``realpath``), enforce
    segment-aware containment with ``normcase``, and deny **any** reparse point
    (symlink *or* junction/mount point) along the path via the
    ``FILE_ATTRIBUTE_REPARSE_POINT`` / ``st_reparse_tag`` check -- which is the
    primary defense there, since ``realpath`` is not reliable for junctions. File
    opens additionally re-assert containment via ``GetFinalPathNameByHandle`` on the
    open handle when available. These branches are not exercised by the Linux CI and
    are marked ``# pragma: no cover``.

Early rejection (both platforms) refuses: empty/NUL targets, absolute or
drive/UNC-prefixed targets (targets are always *relative* to a root), ``..``
components, ``:`` in a component (Alternate Data Streams / drive), trailing dot or
space (Windows aliasing), and ``\\?\`` / ``\\.\`` device spellings.

Never imported by gate.py / graph.py / mcp_hybrid_server.py.
"""

from __future__ import annotations

import errno
import hashlib
import os
import re
import stat as statmod
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path

from utils.errors import FsConnectRuntimeError, FsPathError

_POSIX = os.name != "nt"
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400

_SEP_RE = re.compile(r"[\\/]+")


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
        strict_roots: bool = False,
        on_fallback: Callable[[str, str], None] | None = None,
    ) -> None:
        self.allow_unc = allow_unc
        self.strict_roots = strict_roots
        self._on_fallback = on_fallback
        self._roots: list[SafeRoot] = []
        seen: list[str] = []
        for raw in root_strs:
            resolved = self._prepare_root(raw, create=create)
            norm = os.path.normcase(str(resolved))
            for other in seen:
                if _norm_contains(other, norm) or _norm_contains(norm, other):
                    raise FsPathError(
                        f"overlapping roots are not allowed: {raw!r}",
                        details={"root": raw},
                    )
            seen.append(norm)
            dir_fd = os.open(str(resolved), os.O_RDONLY | _O_DIRECTORY) if _POSIX else -1
            self._roots.append(SafeRoot(requested=raw, path=resolved, normcase=norm, dir_fd=dir_fd))

    def _prepare_root(self, raw: str, *, create: bool) -> Path:
        path = Path(os.path.expanduser(os.path.expandvars(raw)))
        if create:
            try:
                path.mkdir(parents=True, exist_ok=True)
            except PermissionError as exc:
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
        except (OSError, RuntimeError) as exc:
            raise FsPathError(
                f"root does not exist or cannot be resolved: {raw!r}",
                details={"error": str(exc)},
            ) from exc
        if not resolved.is_dir():
            raise FsPathError(f"root is not a directory: {raw!r}", details={"resolved": str(resolved)})
        return resolved

    # --- lifecycle --------------------------------------------------------

    def close(self) -> None:
        for r in self._roots:
            if r.dir_fd >= 0:
                with suppress(OSError):
                    os.close(r.dir_fd)
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
        norm = os.path.normcase(str(Path(os.path.expanduser(os.path.expandvars(root_arg)))))
        for r in self._roots:
            if r.requested == root_arg or r.normcase == norm:
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

    def read_bytes(self, target: str, *, root: str | None = None, max_bytes: int) -> bytes:
        comps = split_components(target)
        sr = self.pick_root(root)
        if _POSIX:
            with self._descend_posix(sr, comps) as (pfd, leaf):
                try:
                    fd = os.open(leaf, os.O_RDONLY | _O_NOFOLLOW, dir_fd=pfd)
                except OSError as exc:
                    raise FsPathError(
                        f"cannot open {leaf!r} for reading",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                return self._read_fd(fd, max_bytes)
        return self._read_win(sr, comps, max_bytes)  # pragma: no cover - Windows only

    @staticmethod
    def _read_fd(fd: int, max_bytes: int) -> bytes:
        try:
            st = os.fstat(fd)
            if not statmod.S_ISREG(st.st_mode):
                raise FsPathError("target is not a regular file")
            if st.st_size > max_bytes:
                raise FsConnectRuntimeError(
                    f"file exceeds max_file_bytes ({max_bytes})",
                    details={"size": st.st_size, "max": max_bytes},
                )
        except BaseException:
            os.close(fd)
            raise
        with os.fdopen(fd, "rb", closefd=True) as f:
            data = f.read(max_bytes + 1)
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
                return self._stat_to_dict(".", os.fstat(sr.dir_fd))
            with self._descend_posix(sr, comps) as (pfd, leaf):
                try:
                    st = os.stat(leaf, dir_fd=pfd, follow_symlinks=False)
                except OSError as exc:
                    raise FsPathError(
                        f"cannot stat {leaf!r}",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                return self._stat_to_dict(leaf, st)
        return self._stat_win(sr, comps)  # pragma: no cover - Windows only

    def list_dir(self, target: str, *, root: str | None = None) -> list[dict]:
        comps = split_components(target)
        sr = self.pick_root(root)
        if _POSIX:
            if not comps:
                return self._listdir_fd(sr.dir_fd)
            with self._descend_posix(sr, comps) as (pfd, leaf):
                try:
                    lfd = os.open(leaf, os.O_RDONLY | _O_NOFOLLOW | _O_DIRECTORY, dir_fd=pfd)
                except OSError as exc:
                    raise FsPathError(
                        f"cannot open directory {leaf!r}",
                        details={"errno": exc.errno, "strerror": exc.strerror},
                    ) from exc
                try:
                    return self._listdir_fd(lfd)
                finally:
                    with suppress(OSError):
                        os.close(lfd)
        return self._list_win(sr, comps)  # pragma: no cover - Windows only

    def _listdir_fd(self, dir_fd: int) -> list[dict]:
        entries: list[dict] = []
        for name in sorted(os.listdir(dir_fd)):
            try:
                st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
            except OSError:
                continue
            entries.append(self._stat_to_dict(name, st))
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
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp, dir_fd=pfd)
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

    def _unlink_win(self, sr: SafeRoot, comps: list[str], sha_max_bytes: int | None) -> dict:  # pragma: no cover - Windows only
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
        real = self._win_resolve(sr, comps, must_exist=True)
        size = real.stat().st_size
        if size > max_bytes:
            raise FsConnectRuntimeError(f"file exceeds max_file_bytes ({max_bytes})", details={"size": size})
        return real.read_bytes()

    def _stat_win(self, sr: SafeRoot, comps: list[str]) -> dict:  # pragma: no cover
        real = self._win_resolve(sr, comps, must_exist=True)
        return self._stat_to_dict(comps[-1] if comps else ".", real.stat())

    def _list_win(self, sr: SafeRoot, comps: list[str]) -> list[dict]:  # pragma: no cover
        real = self._win_resolve(sr, comps, must_exist=True)
        out: list[dict] = []
        for entry in sorted(os.scandir(real), key=lambda e: e.name):
            out.append(self._stat_to_dict(entry.name, entry.stat(follow_symlinks=False)))
        return out

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
        real.mkdir()
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
