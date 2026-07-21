"""RcloneConfig dataclass and validating loader for the CyClaw sync: block.

Reads the ``sync:`` block from CyClaw's single-source-of-truth ``config.yaml``
via ``utils.logger._get_config`` (so it shares the same cached load and the same
``reset_config_cache`` test hook). Purely additive: absence of the block disables
sync entirely without perturbing the gateway or indexer.

Hardened defaults (conservative, in line with CyClaw's offline-first /
soul-governance posture):

  - direction:        "pull"      one-way Dropbox -> local; bisync is opt-in
  - include_soul:     False       data/personality/ NOT synced by default
  - max_delete:       20          safety fuse: rclone aborts if > N deletions
  - conflict_resolve: "newer"     bisync only -- newer modtime wins
  - conflict_loser:   "rename"    bisync only -- loser saved as .conflict1

To change defaults, edit the sync: block in config.yaml -- never edit this file
unless you are changing the schema itself.
"""

from __future__ import annotations

import os
import platform
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from utils.errors import SyncConfigError
from utils.logger import _get_config

# Defaults -- every key here can be overridden by config.yaml.
DEFAULT_REMOTE_NAME = "dropbox_cyclaw"
DEFAULT_REMOTE_PATH = "CyClaw/corpus"
DEFAULT_DIRECTION = "pull"  # "pull" (safe default) | "bisync" (opt-in)
DEFAULT_SCHEDULE_HOUR = 2
DEFAULT_SCHEDULE_MIN = 0
DEFAULT_MAX_DELETE = 20
DEFAULT_MAX_TRANSFER = "1G"
DEFAULT_CONFLICT_RESOLVE = "newer"
DEFAULT_CONFLICT_LOSER = "rename"
DEFAULT_INCLUDE_SOUL = False
DEFAULT_REINDEX_ON_CHANGE = True  # exit 10 if corpus files changed
DEFAULT_AUTO_REINDEX = False  # when true, the CLI runs the indexer itself on change
DEFAULT_POST_SYNC_CHECK = False  # when true, run `rclone check` after each successful sync
DEFAULT_CHECKSUM = True
# Wall-clock ceiling on the rclone sync subprocess. A hung rclone (dead remote,
# stalled network) would otherwise block run_sync forever WHILE HOLDING the
# single-instance lock, wedging every future run. 3600s (1h) is far above any
# realistic .md/.txt corpus sync (max_transfer defaults to 1G), so it only ever
# trips on a genuine hang. Set to 0 in config.yaml to restore the old unbounded
# behaviour.
DEFAULT_SYNC_TIMEOUT_SEC = 3600

# Resilience: extra attempts on a *transient* rclone failure (exit code 5,
# "Temporary error -- one that more retries might fix"). 0 keeps the historical
# single-shot behaviour. rclone has its own inner --retries; this is an OUTER
# retry with backoff for longer outages (remote briefly unreachable) that the
# inner retries cannot ride out.
DEFAULT_SYNC_RETRIES = 0
DEFAULT_RETRY_BACKOFF_SEC = 5.0  # base for exponential backoff between attempts

# Performance tuning -- both off/unset by default, so absence is a no-op.
DEFAULT_FAST_LIST = False  # rclone --fast-list: one bulk listing vs per-dir calls
DEFAULT_BWLIMIT = ""  # rclone --bwlimit value (e.g. "8M"); empty = unset

# Validation constants.
_REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
# Shell metacharacters that must never appear in remote_path (defense in depth;
# we never use shell=True, but reject taint at the boundary anyway).
_SHELL_METACHARS = set(";|&$`<>(){}[]!*?\"'\\\n\r\t ")
_VALID_DIRECTIONS = ("pull", "bisync")
_VALID_CONFLICT_RESOLVE = ("newer", "older", "larger", "smaller", "none")
# A single rclone bandwidth rate: a number with an optional unit suffix
# (b/k/M/G/T/P, optional 'i'), or the literal "off". Timetables (which contain
# spaces and colons) are intentionally not accepted -- they would also trip the
# argv-hygiene goal of keeping --bwlimit a single clean token.
_BWLIMIT_RE = re.compile(r"^(?:off|\d+(?:\.\d+)?[bkmgtpi]*)$", re.IGNORECASE)

# Boolean-typed safety fields of RcloneConfig -- strictly validated in
# load_sync_config (quoted YAML strings must not pass). Listed explicitly
# rather than derived from the dataclass: with `from __future__ import
# annotations` the field .type is a string, and an explicit list doubles as
# the checklist of which gates are load-bearing.
_BOOL_FIELDS = (
    "include_soul",
    "reindex_on_change",
    "auto_reindex",
    "post_sync_check",
    "checksum",
    "fast_list",
)


def _default_rclone_state_dir() -> Path:
    """Return rclone's state directory, honouring XDG_CONFIG_HOME."""
    base = os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / "rclone"
    return Path.home() / ".config" / "rclone"


@dataclass
class RcloneConfig:
    """Parsed and validated sync: block from config.yaml."""

    # Required (validated: absolute, under repo data/corpus tree).
    local_path: str

    # Remote identity (rclone remote name and path inside Dropbox).
    remote_name: str = DEFAULT_REMOTE_NAME
    remote_path: str = DEFAULT_REMOTE_PATH

    # Sync behaviour.
    direction: str = DEFAULT_DIRECTION  # "pull" | "bisync"
    include_soul: bool = DEFAULT_INCLUDE_SOUL
    reindex_on_change: bool = DEFAULT_REINDEX_ON_CHANGE
    # When true AND reindex_on_change fires, `sync.cli sync` runs the indexer
    # itself (a child process) instead of just signalling exit 10 to the caller.
    auto_reindex: bool = DEFAULT_AUTO_REINDEX
    # When true, run `rclone check` after each successful non-dry-run sync to
    # verify the local corpus matches the remote. Differences are audited and
    # surfaced in SyncResult.check_result; they do NOT flip the sync to failed.
    post_sync_check: bool = DEFAULT_POST_SYNC_CHECK
    checksum: bool = DEFAULT_CHECKSUM

    # Safety fuses.
    max_delete: int = DEFAULT_MAX_DELETE
    max_transfer: str = DEFAULT_MAX_TRANSFER
    # Wall-clock ceiling (seconds) on the rclone subprocess; 0 disables it.
    sync_timeout_sec: int = DEFAULT_SYNC_TIMEOUT_SEC

    # Transient-failure resilience (outer retry on rclone exit code 5).
    sync_retries: int = DEFAULT_SYNC_RETRIES
    retry_backoff_sec: float = DEFAULT_RETRY_BACKOFF_SEC

    # Performance tuning (passed straight through to rclone when set).
    fast_list: bool = DEFAULT_FAST_LIST
    bwlimit: str = DEFAULT_BWLIMIT

    # bisync-only knobs (ignored when direction == "pull").
    conflict_resolve: str = DEFAULT_CONFLICT_RESOLVE
    conflict_loser: str = DEFAULT_CONFLICT_LOSER

    # Scheduling (cron / systemd / launchd / Task Scheduler).
    schedule_hour: int = DEFAULT_SCHEDULE_HOUR
    schedule_min: int = DEFAULT_SCHEDULE_MIN

    # File locations (defaults computed at load time, all overridable).
    # Empty string means "unset" -> _fill_default_paths() computes the default
    # in __post_init__, after which all three are guaranteed non-empty strings.
    workdir: str = ""  # bisync state dir
    filter_file: str = ""  # cyclaw_filters.txt path
    log_dir: str = ""  # rclone log dir

    # Extra exclusions appended AFTER the built-in hardened defaults.
    extra_excludes: list[str] = field(default_factory=list)

    # Reindex exit code -- caller uses this to detect "corpus changed".
    REINDEX_EXIT_CODE: int = 10

    # --- Validation -------------------------------------------------------

    def __post_init__(self) -> None:
        self._validate_local_path()
        self._validate_remote_name()
        self._validate_remote_path()

        if self.direction not in _VALID_DIRECTIONS:
            raise SyncConfigError(
                f"sync.direction must be 'pull' or 'bisync', got: {self.direction!r}",
                details={"received": self.direction},
            )

        if self.max_delete < 0:
            raise SyncConfigError(
                f"sync.max_delete must be >= 0, got: {self.max_delete}",
                details={"received": self.max_delete},
            )

        if self.sync_timeout_sec < 0:
            raise SyncConfigError(
                f"sync.sync_timeout_sec must be >= 0 (0 disables the timeout), got: {self.sync_timeout_sec}",
                details={"received": self.sync_timeout_sec},
            )

        if self.sync_retries < 0:
            raise SyncConfigError(
                f"sync.sync_retries must be >= 0 (0 = no retry), got: {self.sync_retries}",
                details={"received": self.sync_retries},
            )

        if self.retry_backoff_sec < 0:
            raise SyncConfigError(
                f"sync.retry_backoff_sec must be >= 0, got: {self.retry_backoff_sec}",
                details={"received": self.retry_backoff_sec},
            )

        self._validate_bwlimit()

        if not 0 <= self.schedule_hour <= 23:
            raise SyncConfigError(
                f"sync.schedule_hour must be 0-23, got: {self.schedule_hour}",
                details={"received": self.schedule_hour},
            )

        if not 0 <= self.schedule_min <= 59:
            raise SyncConfigError(
                f"sync.schedule_min must be 0-59, got: {self.schedule_min}",
                details={"received": self.schedule_min},
            )

        if self.conflict_resolve not in _VALID_CONFLICT_RESOLVE:
            raise SyncConfigError(
                f"sync.conflict_resolve invalid: {self.conflict_resolve!r}",
                details={"valid": list(_VALID_CONFLICT_RESOLVE)},
            )

        self._fill_default_paths()

    def _validate_local_path(self) -> None:
        if not self.local_path:
            raise SyncConfigError(
                "sync.local_path is required",
                details={"hint": "Set sync.local_path to a path under the repo's data/corpus tree."},
            )

        # Expand ~ and env vars early so downstream code does not have to.
        expanded = os.path.expanduser(os.path.expandvars(self.local_path))

        # Repo root is two levels up from this file: sync/config.py -> repo/.
        repo_root = Path(__file__).resolve().parent.parent
        # A relative default like "data/corpus" is resolved against the repo
        # root, not the caller's cwd, so the CLI works from any launch dir.
        path = Path(expanded)
        if not path.is_absolute():
            path = repo_root / path
        resolved = path.resolve()
        corpus_root = (repo_root / "data" / "corpus").resolve()

        # Must resolve to corpus_root itself or a path inside it. resolve()
        # collapses ".." and follows symlinks, so an escape via ".." or a
        # symlink cannot land inside corpus_root.
        if resolved != corpus_root and corpus_root not in resolved.parents:
            raise SyncConfigError(
                "sync.local_path must resolve to a path inside the repo's data/corpus tree",
                details={"corpus_root": str(corpus_root)},
            )

        # After resolution, store an absolute path so callers never see a
        # relative value or an unresolved "..".
        self.local_path = str(resolved)
        # Canonical repo root for spawned work (the scheduler's cd target).
        # Derived from the CODE location -- never from local_path depth -- so
        # a local_path nested below data/corpus cannot shift it (codex P2:
        # two-parents-up from a nested local_path resolves to repo/data).
        self.repo_root = str(repo_root)

    def _validate_remote_name(self) -> None:
        # Reject a leading '-' first: remote_name is composed into the rclone
        # remote spec ``{remote_name}:{remote_path}`` (see the `remote` property)
        # and handed to rclone as a bare argv element. A value like "-foo" yields
        # "-foo:path", which rclone parses as a flag, not a remote -- the same
        # argument-injection vector already guarded against in remote_path. The
        # regex below would otherwise accept it ('-' is in the character class).
        if self.remote_name.startswith("-"):
            raise SyncConfigError(
                f"sync.remote_name must not start with '-' (would be parsed as an rclone flag): {self.remote_name!r}",
                details={"received": self.remote_name},
            )
        if not _REMOTE_NAME_RE.match(self.remote_name):
            raise SyncConfigError(
                f"sync.remote_name must match ^[A-Za-z0-9_.-]+$, got: {self.remote_name!r}",
                details={"received": self.remote_name},
            )

    def _validate_remote_path(self) -> None:
        if self.remote_path.startswith("-"):
            raise SyncConfigError(
                f"sync.remote_path must not start with '-' (would be parsed as a flag): {self.remote_path!r}",
                details={"received": self.remote_path},
            )
        bad = sorted(_SHELL_METACHARS & set(self.remote_path))
        if bad:
            raise SyncConfigError(
                f"sync.remote_path contains forbidden characters: {bad!r}",
                details={"received": self.remote_path, "forbidden": bad},
            )

    def _validate_bwlimit(self) -> None:
        # bwlimit reaches rclone as a single ``--bwlimit={value}`` token. Reject a
        # leading '-' (would be parsed as a flag if the '=' form were ever split)
        # and anything that is not a clean single rate, so no taint reaches argv.
        value = (self.bwlimit or "").strip()
        if not value:
            self.bwlimit = ""
            return
        if value.startswith("-"):
            raise SyncConfigError(
                f"sync.bwlimit must not start with '-' (would be parsed as a flag): {self.bwlimit!r}",
                details={"received": self.bwlimit},
            )
        if not _BWLIMIT_RE.match(value):
            raise SyncConfigError(
                f"sync.bwlimit must be a single rate like '8M', '512k', or 'off', got: {self.bwlimit!r}",
                details={"received": self.bwlimit},
            )
        self.bwlimit = value

    def _fill_default_paths(self) -> None:
        state_dir = _default_rclone_state_dir()
        # Treat blank/whitespace-only overrides as "unset" so a value like
        # "  " falls back to the default rather than being passed verbatim.
        if not (self.workdir or "").strip():
            self.workdir = str(state_dir / "bisync_state")
        if not (self.filter_file or "").strip():
            self.filter_file = str(state_dir / "cyclaw_filters.txt")
        if not (self.log_dir or "").strip():
            self.log_dir = str(state_dir / "logs")

        self.workdir = os.path.expanduser(os.path.expandvars(self.workdir))
        self.filter_file = os.path.expanduser(os.path.expandvars(self.filter_file))
        self.log_dir = os.path.expanduser(os.path.expandvars(self.log_dir))

        # Post-condition: these paths are handed verbatim to rclone as argv
        # values (--filter-from, --workdir, --log-file). An empty value -- e.g.
        # from an override that expands to "" -- would reach rclone as "" and
        # surface as a cryptic "file not found: ''" failure. Fail fast here with
        # a clear config error so the invariant downstream code relies on holds.
        for field_name, value in (
            ("workdir", self.workdir),
            ("filter_file", self.filter_file),
            ("log_dir", self.log_dir),
        ):
            if not value.strip():
                raise SyncConfigError(
                    f"sync.{field_name} resolved to an empty path after expansion",
                    details={"hint": f"Leave sync.{field_name} unset for the default, or set a non-empty path."},
                )

    # --- Computed properties ---------------------------------------------

    @property
    def remote(self) -> str:
        """Combined remote spec for rclone, e.g. 'dropbox_cyclaw:CyClaw/corpus'."""
        return f"{self.remote_name}:{self.remote_path}"

    @property
    def log_path(self) -> str:
        """Full path to the active rclone log file."""
        # log_dir is guaranteed non-empty by _fill_default_paths (run in
        # __post_init__), so no empty-string fallback is needed here.
        return os.path.join(self.log_dir, "rclone_cyclaw.log")

    @property
    def is_windows(self) -> bool:
        return platform.system() == "Windows"

    # --- Serialization ---------------------------------------------------

    def to_dict(self) -> dict:
        return asdict(self)


def load_sync_config(config_path: str = "config.yaml") -> RcloneConfig:
    """Read config.yaml's sync: block and return a validated RcloneConfig.

    Loads through ``utils.logger._get_config`` (cached; tests reset via
    ``reset_config_cache``). Raises ``SyncConfigError`` if the block is absent,
    malformed, or any value fails validation. Unknown keys are FATAL (fail
    closed): a typo'd safety fuse must never silently revert to its default.
    Safety booleans must be real YAML booleans -- a quoted ``"false"`` is
    truthy in Python and would fail the gate OPEN.
    """
    cfg = _get_config(config_path) or {}

    block = cfg.get("sync")
    if not block:
        raise SyncConfigError(
            "sync: block missing from config.yaml",
            details={
                "hint": "Append the sync: block to config.yaml. "
                "See docs/SYNC_README.md or sync/config.py for the schema."
            },
        )

    if not isinstance(block, dict):
        raise SyncConfigError(
            f"sync: block must be a mapping, got {type(block).__name__}",
            details={"received_type": type(block).__name__},
        )

    # Pass through only fields RcloneConfig knows about (excluding the constant
    # REINDEX_EXIT_CODE). Unknown keys are FATAL: with lenient collection a
    # typo like ``max_delte: 5`` parses fine while the deletion fuse silently
    # stays at its default -- the operator believes a safety control is set
    # when it is not. "enabled" is CyClaw's own on/off toggle, not an rclone
    # parameter, so it is not an RcloneConfig field and not a typo.
    known_fields = {f for f in RcloneConfig.__dataclass_fields__ if f != "REINDEX_EXIT_CODE"}
    unknown = set(block.keys()) - known_fields
    unknown.discard("enabled")
    if unknown:
        raise SyncConfigError(
            f"sync: unknown keys (typo?): {sorted(unknown)}",
            details={"unknown_keys": sorted(unknown)},
        )

    # Safety booleans must be REAL booleans. bool("false") is True, so a quoted
    # YAML string would silently ENABLE the very gate it was meant to disable
    # (master gate fails open on quoted booleans -- codex finding).
    for name in (*_BOOL_FIELDS, "enabled"):
        if name in block and not isinstance(block[name], bool):
            raise SyncConfigError(
                f"sync.{name} must be a boolean true/false, got: {block[name]!r}",
                details={"field": name, "received": repr(block[name])},
            )
    kwargs = {k: v for k, v in block.items() if k in known_fields}

    try:
        rc = RcloneConfig(**kwargs)
    except TypeError as exc:
        raise SyncConfigError(
            f"sync: block invalid: {exc}",
            details={"unknown_keys": sorted(unknown)},
        ) from exc

    # Default to enabled when the key is absent (a present sync: block is opt-in
    # already). Stored as a plain attribute, not a dataclass field, to keep it
    # out of the rclone-parameter surface (to_dict / argv).
    rc.enabled = block.get("enabled", True)  # type: ignore[attr-defined]  # validated bool above
    # One propagated config identity for all spawned work: the scheduler's
    # generated command re-invokes the CLI with this exact path, so a schedule
    # installed via `--config /alt/path.yaml` keeps reading THAT file.
    rc._config_path = os.path.abspath(config_path)  # type: ignore[attr-defined]
    return rc
