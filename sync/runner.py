"""rclone subprocess wrapper for CyClaw corpus sync.

Responsibilities:
  - Locate and version-check the rclone binary (floor 1.68.2 -- CVE-2024-52522).
  - Build the right argv for pull (``rclone copy``) or bisync (``rclone bisync``).
  - Run rclone with a structured log file.
  - Parse the log to derive per-file events (added / modified / deleted).
  - Hash each touched file under data/corpus/ with SHA-256 for audit.
  - Emit audit events via ``utils.logger.audit_log``.
  - Decide whether to signal "corpus changed -> reindex" via exit code.

This module does NOT import anything from gate.py, graph.py, or the FastAPI /
MCP layer. It runs strictly out-of-band and writes only to the local filesystem
and the audit log. That isolation is what keeps CyClaw's five security
invariants intact: it cannot bypass retrieval, alter graph topology, or modify
soul state (soul is excluded by default -- see filters.py).

argv is ALWAYS a list and rclone is ALWAYS resolved to an absolute path via
``shutil.which`` -- never ``shell=True``, never a string command. Every argv
element comes from validated config plus a fixed flag list, so there is no taint
path into the subprocess.
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import os
import re
import shutil
import subprocess  # noqa: S404 -- argv-list rclone invocation only; never shell=True (see run_sync)
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from sync.config import RcloneConfig
from sync.filters import write_filter_file
from utils.errors import (
    RcloneNotInstalledError,
    RcloneTimeoutError,
    RcloneVersionError,
    SyncRuntimeError,
)
from utils.logger import audit_log

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Version handling
# ---------------------------------------------------------------------------

MIN_RCLONE_MAJOR, MIN_RCLONE_MINOR, MIN_RCLONE_PATCH = 1, 68, 2

_RCLONE_VERSION_RE = re.compile(r"rclone\s+v?(\d+)\.(\d+)(?:\.(\d+))?", re.IGNORECASE)


def check_rclone_version(rclone_bin: str = "rclone") -> tuple[int, int, int]:
    """Confirm rclone is installed and >= 1.68.2.

    Returns the parsed ``(major, minor, patch)`` tuple. Raises
    ``RcloneNotInstalledError`` if the binary is not on PATH, or
    ``RcloneVersionError`` if the version is too old (CVE-2024-52522 floor).
    """
    binary = shutil.which(rclone_bin)
    if binary is None:
        raise RcloneNotInstalledError(
            "rclone binary not found on PATH",
            details={
                "looked_for": rclone_bin,
                "install_hint_linux": "curl https://rclone.org/install.sh | sudo bash",
                "install_hint_windows": "winget install Rclone.Rclone",
            },
        )

    try:
        # argv list; binary is an absolute path from shutil.which; no shell.
        result = subprocess.run(  # noqa: S603 -- argv list, absolute binary, no shell
            [binary, "version"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        # rclone is installed (shutil.which succeeded) but hung on "rclone version";
        # this is an environment stall, not a missing binary.
        raise RcloneTimeoutError(
            f"rclone version check timed out: {exc}",
            details={"binary": binary},
        ) from exc

    output = (result.stdout or "") + (result.stderr or "")
    match = _RCLONE_VERSION_RE.search(output)
    if not match:
        raise RcloneVersionError(
            "Could not parse rclone version output",
            details={"binary": binary, "output": output[:500]},
        )

    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3)) if match.group(3) else 0

    if (major, minor, patch) < (MIN_RCLONE_MAJOR, MIN_RCLONE_MINOR, MIN_RCLONE_PATCH):
        raise RcloneVersionError(
            f"rclone {major}.{minor}.{patch} is too old; need >= "
            f"{MIN_RCLONE_MAJOR}.{MIN_RCLONE_MINOR}.{MIN_RCLONE_PATCH} "
            "(CVE-2024-52522 fix)",
            details={
                "found": f"{major}.{minor}.{patch}",
                "required": f">={MIN_RCLONE_MAJOR}.{MIN_RCLONE_MINOR}.{MIN_RCLONE_PATCH}",
                "binary": binary,
            },
        )

    return (major, minor, patch)


# ---------------------------------------------------------------------------
# Log parsing
# ---------------------------------------------------------------------------

# rclone's default log verbs are stable; we anchor on the trailing verb only:
#   2026/05/21 02:10:01 INFO  : file.md: Copied (new)
#   2026/05/21 02:10:01 INFO  : file.md: Copied (replaced existing)
#   2026/05/21 02:10:01 INFO  : file.md: Deleted
_LOG_ADDED_RE = re.compile(r":\s*([^:]+?):\s*Copied\s*\(new\)", re.IGNORECASE)
_LOG_MODIFIED_RE = re.compile(
    r":\s*([^:]+?):\s*(?:Copied \(replaced existing\)|Updated modification time)",
    re.IGNORECASE,
)
_LOG_DELETED_RE = re.compile(r":\s*([^:]+?):\s*Deleted(?:\s|$)", re.IGNORECASE)
_LOG_ERROR_RE = re.compile(r"\bERROR\b\s*:\s*(.+)", re.IGNORECASE)

# rclone check summary + mismatch patterns. check outputs to stderr (or stdout
# depending on version/flags); we capture both and scan for these anchors.
#   2026/01/01 00:00:00 INFO  : Found 2 missing on Local
#   2026/01/01 00:00:00 INFO  : Found 1 missing on Remote
#   2026/01/01 00:00:00 INFO  : 3 differences found
#   2026/01/01 00:00:00 NOTICE: file.md: sizes differ
_CHECK_MISSING_LOCAL_RE = re.compile(r"Found (\d+) missing on Local", re.IGNORECASE)
_CHECK_MISSING_REMOTE_RE = re.compile(r"Found (\d+) missing on Remote", re.IGNORECASE)
_CHECK_DIFFS_RE = re.compile(r"(\d+) differences?\s+found", re.IGNORECASE)
_CHECK_MISMATCH_RE = re.compile(r"(?:NOTICE|ERROR)\s*:\s*([^\n]+?):\s*(.+)", re.IGNORECASE)

# rclone's own scratch / state artifacts that may appear in a log line but are
# NOT corpus content. They should never trip the "corpus changed -> reindex"
# signal even if one ever leaks past the filter file.
_RCLONE_INTERNAL_PREFIXES = (".rclone-", "bisync-", ".tmp-", "RCLONE_TEST")


def _is_rclone_internal(path: str) -> bool:
    """True if any component of *path* is an rclone scratch/state artifact.

    rclone logs paths relative to the transfer root, usually flat ("notes.md")
    but occasionally nested ("sub/.rclone-cache/x"). A plain
    ``path.startswith(_RCLONE_INTERNAL_PREFIXES)`` only catches root-level
    artifacts, so a nested scratch file would slip through and wrongly trip the
    "corpus changed -> reindex" signal. Inspect every path component instead.
    Backslashes are normalised so a Windows-style log path is handled too.
    """
    return any(
        part.startswith(_RCLONE_INTERNAL_PREFIXES)
        for part in path.replace("\\", "/").split("/")
        if part
    )


@dataclass
class FileEvent:
    """A single per-file event derived from the rclone log."""

    kind: str  # "added" | "modified" | "deleted"
    path: str  # path relative to the local repo root
    sha256: str | None = None  # populated by hash_changed_files(); None for deletions

    def to_audit_dict(self, base: str) -> dict:
        # Use a "file" key, never "query": audit_log() SHA-256-hashes any field
        # named "query" and we want this path to stay human-readable.
        return {
            "event": f"sync_file_{self.kind}",
            "file": self.path,
            "sha256": self.sha256 or "",
            "base": base,
        }


@dataclass
class CheckResult:
    """Outcome of a post-sync ``rclone check`` integrity run."""

    ok: bool              # True when rclone check exits 0 and no differences found
    missing_local: int    # files on remote not present locally (anomaly after pull)
    missing_remote: int   # files locally not present on remote (extra local files)
    differences: int      # total mismatches (includes missing + hash/size diffs)
    errors: list[str] = field(default_factory=list)  # individual mismatch lines (capped)

    def to_audit_dict(self) -> dict:
        return {
            "ok": self.ok,
            "missing_local": self.missing_local,
            "missing_remote": self.missing_remote,
            "differences": self.differences,
            "errors_n": len(self.errors),
        }


@dataclass
class SyncResult:
    """Outcome of a single sync run."""

    success: bool
    direction: str  # "pull" | "bisync" | "dry-run"
    started_at: float  # epoch seconds
    finished_at: float  # epoch seconds
    rclone_exit_code: int
    events: list[FileEvent] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    log_path: str | None = None
    aborted_for_safety: bool = False  # True if --max-delete / --max-transfer tripped
    dry_run: bool = False
    corpus_changed: bool = False  # True if any event hit data/corpus/**
    check_result: CheckResult | None = None  # populated when post_sync_check=True

    @property
    def duration_sec(self) -> float:
        return max(0.0, self.finished_at - self.started_at)

    def event_counts(self) -> dict:
        counts = {"added": 0, "modified": 0, "deleted": 0}
        for ev in self.events:
            counts[ev.kind] = counts.get(ev.kind, 0) + 1
        return counts

    def to_audit_dict(self) -> dict:
        d: dict = {
            "event": "sync_completed" if self.success else "sync_failed",
            "direction": self.direction,
            "duration_sec": round(self.duration_sec, 3),
            "rclone_exit_code": self.rclone_exit_code,
            "counts": self.event_counts(),
            "errors_n": len(self.errors),
            "aborted_for_safety": self.aborted_for_safety,
            "dry_run": self.dry_run,
            "corpus_changed": self.corpus_changed,
        }
        if self.check_result is not None:
            d["check"] = self.check_result.to_audit_dict()
        return d


def parse_log(log_path: str) -> tuple[list[FileEvent], list[str]]:
    """Parse an rclone log file into ``(events, errors)``.

    Tolerant: any line that does not match a known pattern is ignored. Errors
    are captured as raw strings.

    Scope note: the regexes target the ``rclone copy`` execution verbs
    (``Copied (new)``, ``Copied (replaced existing)``, ``Deleted``). ``rclone
    bisync`` also emits these verbs during its execution phase, so deletions and
    copies are still captured for bisync. Its *pre-sync* structured-diff lines
    (``- Path1   File is new   - file.md``) are intentionally not parsed: bisync
    is opt-in/discouraged here, and the execution-phase verbs are sufficient to
    derive ``corpus_changed``. Per-file event counts for bisync may therefore be
    a lower bound; rely on the run's exit status, not the count, for bisync.
    """
    events: list[FileEvent] = []
    errors: list[str] = []

    try:
        with open(log_path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = _LOG_ADDED_RE.search(line)
                if m:
                    events.append(FileEvent(kind="added", path=m.group(1).strip()))
                    continue
                m = _LOG_MODIFIED_RE.search(line)
                if m:
                    events.append(FileEvent(kind="modified", path=m.group(1).strip()))
                    continue
                m = _LOG_DELETED_RE.search(line)
                if m:
                    events.append(FileEvent(kind="deleted", path=m.group(1).strip()))
                    continue
                m = _LOG_ERROR_RE.search(line)
                if m:
                    errors.append(m.group(1).strip())
    except FileNotFoundError:
        # No log file == nothing happened. Caller decides whether that is an error.
        pass

    # Dedupe by (kind, path), preserving first-seen order. With --checksum on the
    # default pull path, rclone can emit both a "Copied (replaced existing)" line
    # and a separate "Updated modification time" line for the same file -- both
    # match _LOG_MODIFIED_RE. Without dedup that double-counts `modified` in
    # event_counts() and makes hash_changed_files() SHA the same path twice.
    seen: set[tuple[str, str]] = set()
    deduped: list[FileEvent] = []
    for ev in events:
        key = (ev.kind, ev.path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(ev)

    return deduped, errors


def hash_changed_files(events: Sequence[FileEvent], local_root: str) -> list[FileEvent]:
    """Populate ``FileEvent.sha256`` for added/modified entries still on disk.

    Deleted files keep ``sha256=None`` (the bytes are gone -- nothing to hash).
    Files missing from disk are also left as ``None``. Hashing is streamed in
    64 KiB chunks with stdlib ``hashlib.sha256``. Returns a NEW list; the input
    is untouched.
    """
    out: list[FileEvent] = []
    root = os.path.abspath(local_root)
    root_norm = os.path.normcase(root)
    for ev in events:
        if ev.kind == "deleted":
            out.append(ev)
            continue
        abs_path = os.path.abspath(os.path.join(root, ev.path))
        abs_norm = os.path.normcase(abs_path)
        try:
            if os.path.commonpath([root_norm, abs_norm]) != root_norm:
                out.append(ev)
                continue
        except ValueError:
            out.append(ev)
            continue
        try:
            with open(abs_path, "rb") as f:
                h = hashlib.sha256()
                for chunk in iter(lambda f=f: f.read(65536), b""):
                    h.update(chunk)
                out.append(dataclasses.replace(ev, sha256=h.hexdigest()))
        except OSError:
            out.append(ev)
    return out


# ---------------------------------------------------------------------------
# Post-sync integrity check
# ---------------------------------------------------------------------------

def build_check_argv(cfg: RcloneConfig, rclone_bin: str = "rclone") -> list[str]:
    """Build the argv for ``rclone check`` (read-only diff of remote vs local).

    Uses the same filter file as the sync run so only corpus files in scope are
    compared. ``--checksum`` and ``--fast-list`` mirror the sync config so the
    check uses the same comparison strategy.
    """
    argv = [
        rclone_bin, "check",
        cfg.remote, cfg.local_path,
        "--filter-from", cfg.filter_file,
    ]
    if cfg.checksum:
        argv.append("--checksum")
    if cfg.fast_list:
        argv.append("--fast-list")
    return argv


def run_post_sync_check(cfg: RcloneConfig, rclone_bin: str = "rclone") -> CheckResult:
    """Run ``rclone check`` after a successful sync and return a :class:`CheckResult`.

    ``rclone check`` exits 0 when remote and local are identical (filtered),
    non-zero when differences exist. This function NEVER raises on differences --
    it returns a ``CheckResult(ok=False)`` so callers can react. Only raises
    :class:`SyncRuntimeError` on subprocess failure (binary gone, OS error).

    Timeout reuses ``cfg.sync_timeout_sec`` (0 = unbounded). Audit event is emitted
    whether the check passes or not so the operator has a verifiable record.
    """
    argv = build_check_argv(cfg, rclone_bin)
    timeout = cfg.sync_timeout_sec if cfg.sync_timeout_sec > 0 else None
    try:
        completed = subprocess.run(  # noqa: S603 -- argv list, absolute binary, no shell
            argv,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise SyncRuntimeError(
            f"rclone check timed out after {cfg.sync_timeout_sec}s",
            details={"op": "check"},
        ) from exc
    except (FileNotFoundError, subprocess.SubprocessError) as exc:
        raise SyncRuntimeError(
            f"rclone check subprocess failed: {type(exc).__name__}",
            details={"op": "check"},
        ) from exc

    # rclone check writes to stderr (text mode). Combine stdout+stderr defensively
    # in case a future rclone version changes the target stream.
    combined = (completed.stdout or "") + (completed.stderr or "")
    missing_local = 0
    missing_remote = 0
    differences = 0
    errors: list[str] = []

    for line in combined.splitlines():
        m = _CHECK_MISSING_LOCAL_RE.search(line)
        if m:
            missing_local = int(m.group(1))
            continue
        m = _CHECK_MISSING_REMOTE_RE.search(line)
        if m:
            missing_remote = int(m.group(1))
            continue
        m = _CHECK_DIFFS_RE.search(line)
        if m:
            differences = int(m.group(1))
            continue
        m = _CHECK_MISMATCH_RE.search(line)
        if m:
            errors.append(f"{m.group(1).strip()}: {m.group(2).strip()}"[:200])

    ok = completed.returncode == 0 and differences == 0
    result = CheckResult(
        ok=ok,
        missing_local=missing_local,
        missing_remote=missing_remote,
        differences=differences,
        errors=errors[:20],
    )
    audit_log({
        "event": "sync_check_ok" if ok else "sync_check_differences",
        **result.to_audit_dict(),
    })
    return result


# ---------------------------------------------------------------------------
# Argv builders -- always lists; every element is a fixed flag or validated cfg.
# ---------------------------------------------------------------------------

def _common_args(cfg: RcloneConfig, log_path: str) -> list[str]:
    """Args shared between ``copy`` and ``bisync``.

    Note: ``--max-delete`` is intentionally excluded here. ``rclone copy``
    never deletes destination files, so the flag would be a no-op for pull
    mode. It is added only in ``build_bisync_argv`` where deletions can occur.
    """
    args: list[str] = [
        "--filter-from", cfg.filter_file,
        f"--max-transfer={cfg.max_transfer}",
        "--check-first",
        "--log-file", log_path,
        "--log-level", "INFO",
    ]
    if cfg.checksum:
        args.append("--checksum")
    # --fast-list trades a little memory for one bulk remote listing instead of a
    # per-directory walk -- a large speedup on Dropbox corpora with many folders.
    if cfg.fast_list:
        args.append("--fast-list")
    # bwlimit is validated to a single clean rate (or "off") in RcloneConfig, so
    # this stays a single untainted argv token.
    if cfg.bwlimit:
        args.append(f"--bwlimit={cfg.bwlimit}")
    return args


def build_pull_argv(
    cfg: RcloneConfig,
    dry_run: bool,
    log_path: str,
    rclone_bin: str = "rclone",
) -> list[str]:
    """Argv for one-way pull (Dropbox -> local). ``rclone copy`` never deletes."""
    argv = [
        rclone_bin, "copy",
        cfg.remote, cfg.local_path,
        *_common_args(cfg, log_path),
    ]
    if dry_run:
        argv.append("--dry-run")
    return argv


def build_bisync_argv(
    cfg: RcloneConfig,
    dry_run: bool,
    log_path: str,
    resync: bool = False,
    rclone_bin: str = "rclone",
) -> list[str]:
    """Argv for bidirectional ``bisync`` (opt-in, discouraged)."""
    argv = [
        rclone_bin, "bisync",
        cfg.remote, cfg.local_path,
        f"--conflict-resolve={cfg.conflict_resolve}",
        f"--conflict-loser={cfg.conflict_loser}",
        "--workdir", cfg.workdir,
        f"--max-delete={cfg.max_delete}",  # only meaningful where deletions occur
        *_common_args(cfg, log_path),
    ]
    if resync:
        argv.append("--resync")
    if dry_run:
        argv.append("--dry-run")
    return argv


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# rclone "too many deletes" is its own fuse phrasing without a max-delete prefix,
# so it gets a dedicated pattern. The main pattern requires both half-anchors:
#   1. an instance of "max[ -](delete|transfer)" (with optional plural / "imum"),
#   2. followed within the same line by a trip word (limit/reached/exceeded/
#      threshold/abort).
# A bare argv print like "--max-delete=10" appears WITHOUT any of those trip
# words, so it no longer triggers a false safety-abort classification. Real
# rclone fuse messages ("max transfer limit reached", "max-delete threshold
# exceeded", etc.) all carry one of the trip words.
_SAFETY_ABORT_LINE_RE = re.compile(
    r"max(?:imum)?[\s-]+(?:delete|transfer)[s]?\b[^\r\n]*?"
    r"(?:limit|reached|exceeded|threshold|abort)",
    re.IGNORECASE,
)
_SAFETY_ABORT_TOO_MANY_DELETES_RE = re.compile(r"too\s+many\s+deletes", re.IGNORECASE)


def _detect_safety_abort(errors: Sequence[str], stderr: str) -> bool:
    """True if rclone tripped the --max-delete or --max-transfer safety fuse.

    Previous implementation looked for the bare substrings ``"max-delete"`` or
    ``"max-transfer"`` anywhere in errors / stderr, which misclassified any
    unrelated log line that happened to mention the flag name (e.g. a config
    diagnostic that prints the full argv on startup, or any user-facing error
    that references the flag). Anchor instead on rclone's actual fuse-trip
    phrasings — those lines always carry both the "max-delete"/"max-transfer"
    half AND a trip word (limit/reached/exceeded/threshold/abort) on the same
    line. Bare argv prints carry the flag without a trip word, so they no
    longer trigger a false safety-abort classification (and a wrong route to
    CLI exit code 1 instead of the truthful 2).
    """
    haystacks = list(errors)
    if stderr:
        haystacks.append(stderr)
    for h in haystacks:
        if _SAFETY_ABORT_LINE_RE.search(h) or _SAFETY_ABORT_TOO_MANY_DELETES_RE.search(h):
            return True
    return False


# rclone's documented exit code for "Temporary error (one that more retries might
# fix)". It is the ONLY code we retry on: usage errors (1), fatal errors (7) and
# the safety-fuse abort (8 / max-transfer) are deterministic and must not loop.
_RCLONE_TRANSIENT_EXIT = 5

# rclone's documented exit code for "Transfer exceeded - limit set by
# --max-transfer reached". Deterministic and authoritative: unlike the log-text
# heuristics in _detect_safety_abort (which depend on the trip line landing in
# captured stderr/parsed errors — rclone writes fuse messages to --log-file, so
# it may not), the process exit code always survives. Without this check a real
# max-transfer abort whose message never reached the parsed text was
# misclassified as a generic failure (CLI exit 2 instead of the safety exit 1),
# silencing the oversight signal the fuse exists to raise. The regex heuristics
# remain as the secondary signal — they are still the only detector for the
# --max-delete trip, which rclone reports via exit 7 (generic fatal), a code
# too ambiguous to treat as safety-specific.
_RCLONE_SAFETY_FUSE_EXIT = 8


def _truncate_log(log_path: str, direction: str) -> None:
    """Clear the rclone log so parsing sees ONLY the upcoming attempt's lines.

    rclone opens --log-file in append mode; a stale or prior-attempt log left in
    place would be re-parsed in full (replaying old FileEvents/ERRORs and risking
    a false safety-abort). Truncate (open "w") rather than unlink so the file
    still clears when the inode cannot be removed but is writable.
    """
    try:
        with open(log_path, "w", encoding="utf-8"):
            pass
    except OSError as exc:
        raise SyncRuntimeError(
            "could not clear previous rclone log before sync",
            details={"direction": direction},
        ) from exc


# A daily scheduled run and a manual run must never drive rclone against the
# same remote/destination at once: their log writes would interleave and corrupt
# parsing, and concurrent filter-file writes would race. ``os.mkdir`` is atomic
# on every platform, so it doubles as a zero-dependency, cross-platform lock --
# no fcntl/msvcrt branching, no third-party dep.
_LOCK_STALE_SEC = 3 * 60 * 60  # reclaim a lock left by a crashed run after 3h
# Headroom added on top of a configured sync timeout when deriving the stale
# threshold: covers version check, filter write, hashing, and audit I/O around
# the rclone subprocess itself.
_LOCK_STALE_MARGIN_SEC = 60 * 60


def _lock_stale_after_sec(cfg: RcloneConfig) -> float:
    """Stale-lock threshold for a run configured by ``cfg``.

    The retry loop bounds total sync time by ``sync_timeout_sec`` (global
    deadline), so a bounded run can legitimately hold the lock for that long --
    the threshold must exceed the budget or a *live* long run looks "stale" and
    a second sync starts underneath it. When ``post_sync_check`` is enabled the
    lock is ALSO held through ``run_post_sync_check``, which independently
    receives the full ``sync_timeout_sec`` as its own subprocess timeout, so
    the complete lock-held budget is ~2x the sync budget (review finding on
    PR #585). Unbounded (0) keeps the flat 3h default; run_sync warns about
    the degraded protection in that case.
    """
    if cfg.sync_timeout_sec > 0:
        multiplier = 2 if getattr(cfg, "post_sync_check", False) else 1
        return max(
            _LOCK_STALE_SEC,
            cfg.sync_timeout_sec * multiplier + _LOCK_STALE_MARGIN_SEC,
        )
    return _LOCK_STALE_SEC


def _acquire_sync_lock(lock_dir: str, stale_after_sec: float = _LOCK_STALE_SEC) -> None:
    """Acquire the single-instance lock, or raise ``SyncRuntimeError``.

    Reclaims a lock older than ``stale_after_sec`` (a prior run that crashed
    without releasing it) so a stale directory can never wedge sync forever.
    """
    try:
        os.mkdir(lock_dir)
        return
    except FileExistsError:
        pass
    try:
        age = time.time() - os.path.getmtime(lock_dir)
    except OSError:
        age = 0.0
    if age > stale_after_sec:
        try:
            os.rmdir(lock_dir)
            os.mkdir(lock_dir)
            log.info(
                "Reclaimed stale sync lock at %s (age %.0f s > threshold %.0f s)",
                lock_dir, age, stale_after_sec,
            )
            return
        except OSError:
            log.warning("Stale sync lock reclamation failed at %s (age %.0f s); raising", lock_dir, age)
    raise SyncRuntimeError(
        "Another CyClaw sync appears to be running",
        details={
            "lock_dir": lock_dir,
            "hint": "Wait for the other run to finish, or remove the lock dir if it is stale.",
        },
    )


def _release_sync_lock(lock_dir: str) -> None:
    """Release the single-instance lock; tolerant if it is already gone."""
    try:
        os.rmdir(lock_dir)
    except OSError:
        pass


def run_sync(
    cfg: RcloneConfig,
    dry_run: bool = False,
    resync: bool = False,
    rclone_bin: str = "rclone",
) -> SyncResult:
    """Execute one sync run end-to-end.

    1. Confirm rclone is installed and recent enough (>= 1.68.2).
    2. (Re)write the rclone filter file.
    3. Build argv for pull or bisync.
    4. Run rclone, capturing exit code and the log file.
    5. Parse the log into FileEvents.
    6. Hash any added/modified files under data/corpus/ for the audit row.
    7. Emit audit events: sync_started, sync_file_*, sync_completed | sync_failed.
    8. Return a SyncResult.

    Raises ``RcloneNotInstalledError`` / ``RcloneVersionError`` on environment
    failure, ``SyncRuntimeError`` on unexpected subprocess failure. Normal rclone
    non-zero exits (e.g. a tripped safety fuse) do NOT raise -- they return a
    ``SyncResult`` with ``success=False`` and ``aborted_for_safety=True`` so the
    caller can react cleanly.

    Security: argv is a list, the binary is absolute, ``shell`` is never set, and
    only metadata is logged -- never raw stderr that could echo a token.

    Concurrency: a process-wide single-instance lock (an atomically created lock
    directory under ``log_dir``) prevents a manual run and the scheduled run from
    driving rclone against the same remote at once. A second concurrent run
    raises ``SyncRuntimeError``; a lock left by a crashed run is reclaimed after
    ``_lock_stale_after_sec(cfg)`` -- at least ``_LOCK_STALE_SEC``, extended past
    the full lock-held budget (``sync_timeout_sec``, doubled when
    ``post_sync_check`` is enabled since the check also runs under the lock with
    its own full timeout), so a live long run is never mistaken for a crashed
    one. With ``sync_timeout_sec: 0`` (unbounded) the threshold stays at the
    flat 3h default and a run exceeding it loses mutual-exclusion protection;
    a warning is logged at run start in that case.
    """
    check_rclone_version(rclone_bin)
    resolved_rclone = shutil.which(rclone_bin)
    if resolved_rclone is None:  # pragma: no cover -- check_rclone_version already guards this
        raise RcloneNotInstalledError("rclone binary disappeared after version check")

    os.makedirs(cfg.log_dir or ".", exist_ok=True)
    lock_dir = os.path.join(cfg.log_dir or ".", "sync.lock.d")
    if cfg.sync_timeout_sec == 0:
        log.warning(
            "sync_timeout_sec is 0 (unbounded): a run exceeding %ds may have its "
            "single-instance lock reclaimed as stale while still alive, allowing a "
            "concurrent sync. Set a finite sync_timeout_sec for full protection.",
            _LOCK_STALE_SEC,
        )
    _acquire_sync_lock(lock_dir, _lock_stale_after_sec(cfg))
    try:
        return _run_sync_locked(cfg, dry_run, resync, resolved_rclone)
    finally:
        _release_sync_lock(lock_dir)


def _run_sync_locked(
    cfg: RcloneConfig,
    dry_run: bool,
    resync: bool,
    rclone_bin: str,
) -> SyncResult:
    """Body of ``run_sync`` executed while holding the single-instance lock."""
    write_filter_file(cfg)

    log_path = cfg.log_path

    if cfg.direction == "bisync":
        argv = build_bisync_argv(
            cfg, dry_run=dry_run, log_path=log_path, resync=resync, rclone_bin=rclone_bin
        )
    else:
        argv = build_pull_argv(cfg, dry_run=dry_run, log_path=log_path, rclone_bin=rclone_bin)

    started_at = time.time()
    audit_log({
        "event": "sync_started",
        "direction": cfg.direction,
        "dry_run": dry_run,
        "remote": cfg.remote,
        "local_path": cfg.local_path,
        "include_soul": cfg.include_soul,
    })

    # Accumulators hoisted ABOVE the try so the evidence-preserving except
    # (below) can always reference them -- even if an exception fires before the
    # loop body binds them.
    #
    # Change evidence is coalesced across attempts BY PATH, and the FINAL attempt
    # to touch a path wins (codex #594 P2): a file added then replaced or DELETED
    # on a later attempt reports its final operation against the on-disk state,
    # not the earliest (earliest-wins would mis-report a since-deleted file as
    # 'added'). This accumulation is also load-bearing for the reindex: a
    # transient attempt can copy files and only THEN fail (exit 5); the clean
    # retry sees them already present and logs nothing, so without it
    # corpus_changed would flip back to False and suppress the reindex the copied
    # content needs. Errors stay final-attempt-only: they feed safety-abort
    # detection and must describe the attempt that produced the surfaced exit
    # code, not a stale mid-retry ERROR line.
    completed: subprocess.CompletedProcess[str] | None = None
    events_by_path: dict[str, FileEvent] = {}
    final_errors: list[str] = []

    # Audit convergence (invariant): the sync_started record above must ALWAYS be
    # paired with a terminal record. Exceptional exits -- wall-clock timeout,
    # retry-budget exhaustion, subprocess failure, or a post-sync-check error --
    # previously re-raised past the summary audit and left the run open-ended.
    # The except below emits a schema-aligned sync_failed that (a) preserves the
    # change evidence harvested so far (codex #594 P1: a partial-copy-then-raise
    # must not lose the copied-file record and strand the index stale), and
    # (b) sanitizes the failure to the exception TYPE name only, never the
    # message (kept enforced at the audit boundary rather than by convention).
    try:

        # A 0 timeout means "unbounded" (subprocess.run treats timeout=None that way).
        # When a budget IS set (the common safe case) it is the WALL-CLOCK budget for
        # the whole retry sequence under the single-instance lock, not just one
        # attempt -- otherwise a transient-exit-5 retry path could hold the lock for
        # attempts * sync_timeout_sec + sum(retry_backoff_sec*2^k), a many-times
        # multiple of the documented per-attempt ceiling.
        has_budget = cfg.sync_timeout_sec > 0
        deadline = (time.time() + cfg.sync_timeout_sec) if has_budget else None

        # Outer retry loop: re-run rclone on a *transient* failure (exit code 5) up to
        # cfg.sync_retries extra times, with exponential backoff. The log is truncated
        # before EACH attempt, so each attempt's evidence is harvested into
        # events_by_path before the next truncation wipes it. With the default
        # sync_retries=0 this loop runs exactly once (historical behaviour).
        attempts = cfg.sync_retries + 1
        for attempt in range(1, attempts + 1):
            # Per-attempt timeout shrinks toward the global deadline. If we already
            # have <=0 seconds left, stop now and surface the same RcloneTimeoutError
            # the inner TimeoutExpired path raises. has_budget implies deadline is
            # not None (set together above), so the type check is structural.
            if has_budget and deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    raise SyncRuntimeError(
                        f"rclone sync timed out after {cfg.sync_timeout_sec}s (budget exhausted by retries)",
                        details={"direction": cfg.direction, "timeout_sec": cfg.sync_timeout_sec},
                    )
                run_timeout: float | None = remaining
            else:
                run_timeout = None

            _truncate_log(log_path, cfg.direction)
            try:
                # argv is a list of a fixed flag set + validated config; never shell=True.
                completed = subprocess.run(  # noqa: S603 -- argv list, validated inputs, no shell
                    argv,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=run_timeout,
                )
            except subprocess.TimeoutExpired as exc:
                # rclone hung past the wall-clock ceiling. subprocess.run has already
                # killed the child and reaped it by the time TimeoutExpired propagates,
                # so the single-instance lock is released cleanly when we raise. Do not
                # echo argv (it carries the remote spec) — surface only the direction and
                # the limit that tripped. A hang is not transient; never retry it.
                raise SyncRuntimeError(
                    f"rclone sync timed out after {cfg.sync_timeout_sec}s",
                    details={"direction": cfg.direction, "timeout_sec": cfg.sync_timeout_sec},
                ) from exc
            except FileNotFoundError as exc:
                # rclone disappeared between the version check and now (race). Do not
                # include argv (would leak the remote spec into the error string).
                raise RcloneNotInstalledError(
                    "rclone binary disappeared during execution",
                    details={"direction": cfg.direction},
                ) from exc
            except subprocess.SubprocessError as exc:
                raise SyncRuntimeError(
                    f"rclone subprocess failed: {type(exc).__name__}",
                    details={"direction": cfg.direction},
                ) from exc

            # Harvest this attempt's change evidence before the next _truncate_log
            # wipes it (see the coalescing note above the loop): last attempt to
            # touch a path wins; dict preserves first-seen order for determinism.
            attempt_events, final_errors = parse_log(log_path)
            for ev in attempt_events:
                events_by_path[ev.path] = ev

            if completed.returncode != _RCLONE_TRANSIENT_EXIT or attempt == attempts:
                break

            # Transient failure with attempts remaining: audit, back off, retry.
            audit_log({
                "event": "sync_retry",
                "direction": cfg.direction,
                "attempt": attempt,
                "max_attempts": attempts,
                "rclone_exit_code": completed.returncode,
            })
            backoff = cfg.retry_backoff_sec * (2 ** (attempt - 1))
            # Clip the backoff to whatever budget remains; if the budget is exhausted,
            # break out so the FAILED attempt's result is surfaced (parsed + audited)
            # rather than raising a bare timeout that drops the retry's stderr/events.
            if has_budget and deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    break
                if backoff > remaining:
                    backoff = remaining
            time.sleep(backoff)

        finished_at = time.time()
        if completed is None:  # pragma: no cover -- attempts >= 1 guarantees one run
            # Unreachable: the loop runs at least once and either binds `completed`
            # or raises. Kept as a typed guard so the type checker can narrow Optional.
            raise SyncRuntimeError("rclone never executed", details={"direction": cfg.direction})
        exit_code = completed.returncode

        # Hash -> audit. Events are the coalesced set across all attempts (a
        # partial-copy-then-clean-retry still requests the reindex); errors are the
        # final attempt's only, so transient retry noise stays out of the result.
        events = hash_changed_files(list(events_by_path.values()), cfg.local_path)
        errors = final_errors

        aborted_for_safety = (
            exit_code == _RCLONE_SAFETY_FUSE_EXIT
            or _detect_safety_abort(errors, completed.stderr or "")
        )

        # rclone logs file paths RELATIVE TO THE TRANSFER ROOT (the destination
        # directory), not relative to the repo root -- e.g. "notes.md", never
        # "data/corpus/notes.md". Since cfg.local_path is validated to resolve under
        # data/corpus (RcloneConfig.__post_init__), every parsed file event is by
        # construction a corpus change. We still defensively skip rclone's own
        # scratch/state artifacts in case one ever slips past the filter file.
        corpus_changed = any(not _is_rclone_internal(ev.path) for ev in events)

        result = SyncResult(
            success=(exit_code == 0),
            direction=("dry-run" if dry_run else cfg.direction),
            started_at=started_at,
            finished_at=finished_at,
            rclone_exit_code=exit_code,
            events=events,
            errors=errors,
            log_path=log_path,
            aborted_for_safety=aborted_for_safety,
            dry_run=dry_run,
            corpus_changed=corpus_changed,
        )

        # Post-sync integrity check: confirm remote and local agree after a successful
        # non-dry-run sync. Skipped on failure or dry-run because there is nothing to
        # verify. Does not raise on differences -- sets check_result.ok=False instead
        # so the caller can surface the discrepancy without masking the sync result.
        if result.success and not dry_run and getattr(cfg, "post_sync_check", False):
            result.check_result = run_post_sync_check(cfg, rclone_bin)

        # Per-file audit events -- one row per file, with sha256 when available.
        for ev in events:
            audit_log(ev.to_audit_dict(base=cfg.local_path))

        # Summary audit event -- sync_completed (success) or sync_failed (otherwise).
        audit_log(result.to_audit_dict())

        return result
    except Exception as exc:
        # Preserve change evidence before propagating (codex #594 P1): an attempt
        # can copy files and only THEN raise (timeout, budget exhaustion,
        # subprocess failure), so harvest the current log one final time. The
        # copied-file record would otherwise be lost and the RAG index could stay
        # stale permanently with nothing in the audit to explain why. Never let
        # this best-effort harvest mask the original failure.
        try:
            tail_events, _tail_errors = parse_log(log_path)
            for ev in tail_events:
                events_by_path[ev.path] = ev
        except OSError:
            pass
        partial = list(events_by_path.values())
        counts = {"added": 0, "modified": 0, "deleted": 0}
        for ev in partial:
            counts[ev.kind] = counts.get(ev.kind, 0) + 1
        # Schema-aligned with SyncResult.to_audit_dict (codex #594 P2): the
        # terminal record for an exceptional exit carries the same evidence
        # fields a normal failure would, plus error_type. rclone_exit_code is the
        # last observed code (None if rclone never returned one); aborted_for_safety
        # is False -- an exceptional exit is not a --max-delete/--max-transfer trip.
        audit_log({
            "event": "sync_failed",
            "direction": cfg.direction,
            "duration_sec": round(max(0.0, time.time() - started_at), 3),
            "rclone_exit_code": completed.returncode if completed is not None else None,
            "counts": counts,
            "errors_n": len(final_errors),
            "aborted_for_safety": False,
            "dry_run": dry_run,
            "corpus_changed": any(not _is_rclone_internal(ev.path) for ev in partial),
            "error_type": type(exc).__name__,
        })
        raise


def reindex_exit_code_for(result: SyncResult, cfg: RcloneConfig) -> int:
    """Return the CLI exit code to use after a sync run.

    0   -- sync OK, no corpus change (or reindex_on_change disabled).
    10  -- sync OK, corpus changed; caller should run ``python -m retrieval.indexer``.
    1   -- sync failed for safety reasons (--max-delete / --max-transfer tripped).
    2   -- sync failed for any other reason.
    """
    if not result.success:
        return 1 if result.aborted_for_safety else 2
    if cfg.reindex_on_change and result.corpus_changed:
        return cfg.REINDEX_EXIT_CODE
    return 0
