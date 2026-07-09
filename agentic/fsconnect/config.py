"""FsConnectConfig dataclass and validating loader for the ``fsconnect:`` block.

Reads the ``fsconnect:`` block from CyClaw's single-source-of-truth ``config.yaml``
via ``utils.logger._get_config`` (shared cached load; tests reset it via
``reset_config_cache``). Purely additive: absence of the block disables the
filesystem connector entirely without perturbing the gateway, graph, or MCP server.

Two independent scopes:
  - ``allowed_roots``   -- READ scope (read-only).
  - ``writable_roots``  -- WRITE scope (a separate list; ``None`` entries expand to an
    OS-appropriate default file-share folder, e.g. ``C:\\CyClaw-FS\\``).

The config validates declared values and computes OS defaults; the actual
``resolve()`` + directory-handle holding happens at runtime in ``pathsafe`` so this
loader stays side-effect free (it never creates directories).

This module is part of a package that is NEVER imported by gate.py, graph.py, or
mcp_hybrid_server.py.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field

from utils.errors import FsConnectConfigError
from utils.logger import _get_config

DEFAULT_TRASH_RETENTION_DAYS = 30
DEFAULT_QUOTA_RECOMPUTE_HOURS = 24
DEFAULT_RATE_MAX_OPS = 30
DEFAULT_RATE_WINDOW_SECONDS = 60.0
DEFAULT_RATE_DB_PATH = "data/fsconnect_rate.db"

DEFAULT_ALLOWED_FS_OPS = ("fs_list", "fs_stat", "fs_read", "fs_grep", "fs_glob")
VALID_FS_OPS = frozenset(DEFAULT_ALLOWED_FS_OPS)
DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB
DEFAULT_MAX_WRITE_BYTES = 10 * 1024 * 1024  # 10 MiB
DEFAULT_INDEX_EXTENSIONS = (".md", ".txt", ".pdf", ".docx", ".csv")
DEFAULT_INDEX_MAX_FILE_BYTES = 10 * 1024 * 1024  # 10 MiB

_WINDOWS = os.name == "nt"


def os_default_writable_root() -> str:
    """OS-appropriate default for the file-share write root.

    Windows -> ``C:\\CyClaw-FS``. Linux/other -> ``/var/lib/cyclaw-fs`` when
    ``/var/lib`` is writable (service install), else ``~/CyClaw-FS`` (no root
    required). Side-effect free: ``os.access`` probes permission without creating
    anything; the directory itself is created later by ``pathsafe``.
    """
    if _WINDOWS:
        return r"C:\CyClaw-FS"
    if os.access("/var/lib", os.W_OK):
        return "/var/lib/cyclaw-fs"
    return os.path.expanduser("~/CyClaw-FS")


def _is_unc(path: str) -> bool:
    """True if ``path`` looks like a UNC share (``\\\\server\\share`` or ``//server``)."""
    return path.startswith("\\\\") or path.startswith("//")


@dataclass(frozen=True)
class QuotaSpec:
    """Per-root capacity limits. ``None`` means unbounded on that axis."""

    quota_bytes: int | None = None
    max_files: int | None = None


@dataclass
class FsConnectConfig:
    """Parsed and validated fsconnect: block from config.yaml."""

    # --- read scope ---
    allowed_roots: list[str] = field(default_factory=list)
    allowed_fs_ops: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_FS_OPS))
    allow_unc_roots: bool = False
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    follow_symlinks: bool = False
    scan_content: bool = True
    # --- write scope (separate, gated, default-disabled) ---
    writes_enabled: bool = False
    writable_roots: list[str | None | dict] = field(default_factory=lambda: [None])
    max_write_bytes: int = DEFAULT_MAX_WRITE_BYTES
    require_confirm_destructive: bool = True
    # --- Phase 2 write-enablement (all default-off / unbounded) ---
    strict_roots: bool = False              # fail closed on _prepare_root fallback
    block_on_injection_flags: bool = False  # refuse a write whose advisory scan flags content
    allow_hard_delete: bool = False         # fifth gate for fs_delete --purge (global)
    trash_retention_days: int = DEFAULT_TRASH_RETENTION_DAYS
    quota_recompute_hours: int = DEFAULT_QUOTA_RECOMPUTE_HOURS
    write_rate_limit: dict = field(default_factory=dict)
    # Parallel to writable_roots: normalized-path-string -> QuotaSpec (only for roots
    # that declared a quota). Populated by _normalize_writable_roots; not a config key.
    write_root_quotas: dict[str, QuotaSpec] = field(init=False, default_factory=dict)
    # --- secondary RAG-indexing of the file-share ---
    index_enabled: bool = False
    index_root: str | None = None
    index_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_INDEX_EXTENSIONS))
    index_max_file_bytes: int = DEFAULT_INDEX_MAX_FILE_BYTES
    # When true, apply() skips re-reading/re-staging files whose size+mtime are
    # unchanged since the last run (a skip-cache kept beside the staged files).
    index_incremental: bool = False

    # --- validation -------------------------------------------------------

    def __post_init__(self) -> None:
        self._validate_ops()
        self._validate_caps()
        self._validate_symlinks()
        self._validate_phase2_scalars()
        self._check_root_list("allowed_roots", self.allowed_roots)
        self._normalize_writable_roots()
        self._validate_rate_limit()
        self._normalize_index()

    def _validate_ops(self) -> None:
        bad = [op for op in self.allowed_fs_ops if op not in VALID_FS_OPS]
        if bad:
            raise FsConnectConfigError(
                f"fsconnect.allowed_fs_ops contains unknown ops: {bad!r}",
                details={"unknown": bad, "valid": sorted(VALID_FS_OPS)},
            )

    def _validate_caps(self) -> None:
        for name in ("max_file_bytes", "max_write_bytes", "index_max_file_bytes"):
            val = getattr(self, name)
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                raise FsConnectConfigError(
                    f"fsconnect.{name} must be a positive integer, got: {val!r}",
                    details={"field": name, "received": val},
                )

    def _validate_symlinks(self) -> None:
        # v0.1 always denies symlinks/reparse points under a root; an opt-in does
        # not exist yet, so a truthy follow_symlinks is a config error rather than a
        # silent no-op that lulls an operator into thinking links are followed.
        if self.follow_symlinks:
            raise FsConnectConfigError(
                "fsconnect.follow_symlinks must be false in v0.1 (symlinks and "
                "reparse points under a root are always denied; no opt-in exists yet)",
                details={"received": self.follow_symlinks},
            )

    def _check_root_list(self, field_name: str, roots: list[str]) -> None:
        for r in roots:
            if not isinstance(r, str) or not r.strip():
                raise FsConnectConfigError(
                    f"fsconnect.{field_name} entries must be non-empty strings",
                    details={"field": field_name, "received": r},
                )
            if "\x00" in r:
                raise FsConnectConfigError(
                    f"fsconnect.{field_name} entry contains a NUL byte",
                    details={"field": field_name},
                )
            if _is_unc(r) and not self.allow_unc_roots:
                raise FsConnectConfigError(
                    f"fsconnect.{field_name} contains a UNC path but allow_unc_roots is false",
                    details={"field": field_name, "path": r},
                )

    def _validate_phase2_scalars(self) -> None:
        for name in ("trash_retention_days", "quota_recompute_hours"):
            val = getattr(self, name)
            if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                raise FsConnectConfigError(
                    f"fsconnect.{name} must be a positive integer, got: {val!r}",
                    details={"field": name, "received": val},
                )

    @staticmethod
    def _validate_quota_field(name: str, val: object) -> int | None:
        if val is None:
            return None
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            raise FsConnectConfigError(
                f"fsconnect.writable_roots {name} must be a positive integer or null, got: {val!r}",
                details={"field": name, "received": val},
            )
        return val

    def _normalize_writable_roots(self) -> None:
        # writable_roots entries are ``str`` (unlimited), ``None`` (OS default,
        # unlimited), or a mapping ``{path, quota_bytes?, max_files?}``. Normalize
        # every entry down to a plain path string (so ScopedRoots stays untouched)
        # and record any declared quota in the parallel ``write_root_quotas`` dict,
        # keyed by that same path string (which is exactly ``SafeRoot.requested`` at
        # runtime, so the writer can look a root's quota up by its requested string).
        normalized: list[str] = []
        quotas: dict[str, QuotaSpec] = {}
        for entry in self.writable_roots:
            if entry is None:
                path_str: str = os_default_writable_root()
                spec: QuotaSpec | None = None
            elif isinstance(entry, str):
                path_str = entry
                spec = None
            elif isinstance(entry, dict):
                raw_path = entry.get("path")
                if raw_path is None:
                    path_str = os_default_writable_root()
                elif isinstance(raw_path, str) and raw_path.strip():
                    path_str = raw_path
                else:
                    raise FsConnectConfigError(
                        "fsconnect.writable_roots mapping 'path' must be a non-empty string or null",
                        details={"received": raw_path},
                    )
                qb = self._validate_quota_field("quota_bytes", entry.get("quota_bytes"))
                mf = self._validate_quota_field("max_files", entry.get("max_files"))
                spec = QuotaSpec(quota_bytes=qb, max_files=mf)
            else:
                raise FsConnectConfigError(
                    "fsconnect.writable_roots entries must be a string, null, or a mapping",
                    details={"received_type": type(entry).__name__},
                )
            normalized.append(path_str)
            if spec is not None and (spec.quota_bytes is not None or spec.max_files is not None):
                quotas[path_str] = spec
        self._check_root_list("writable_roots", normalized)
        self.writable_roots = list(normalized)
        self.write_root_quotas = quotas

    def _validate_rate_limit(self) -> None:
        rl = self.write_rate_limit
        if not isinstance(rl, dict):
            raise FsConnectConfigError(
                f"fsconnect.write_rate_limit must be a mapping, got {type(rl).__name__}",
                details={"received_type": type(rl).__name__},
            )
        for name in ("max_ops", "global_max_ops"):
            if name in rl:
                val = rl[name]
                if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
                    raise FsConnectConfigError(
                        f"fsconnect.write_rate_limit.{name} must be a positive integer",
                        details={"field": name, "received": val},
                    )
        if "window_seconds" in rl:
            ws = rl["window_seconds"]
            if not isinstance(ws, (int, float)) or isinstance(ws, bool) or ws <= 0:
                raise FsConnectConfigError(
                    "fsconnect.write_rate_limit.window_seconds must be a positive number",
                    details={"received": ws},
                )

    def _normalize_index(self) -> None:
        if self.index_root is None:
            # Default the index root to the first writable root (generate->write->index loop).
            write_strs = self.write_root_strs
            self.index_root = write_strs[0] if write_strs else None
        exts: list[str] = []
        for e in self.index_extensions:
            if not isinstance(e, str) or not e.strip():
                raise FsConnectConfigError(
                    "fsconnect.index_extensions entries must be non-empty strings",
                    details={"received": e},
                )
            exts.append((e if e.startswith(".") else "." + e).lower())
        self.index_extensions = exts

    # --- computed ---------------------------------------------------------

    @property
    def write_root_strs(self) -> list[str]:
        """Writable roots as concrete strings (``None`` already normalized away)."""
        return [r for r in self.writable_roots if isinstance(r, str)]

    @property
    def rate_limit_settings(self) -> dict:
        """Normalized write-rate-limit config with defaults filled in.

        ``global_max_ops`` defaults to ``2 x max_ops`` (per-root fairness plus an
        aggregate ceiling). ``db_path`` is a SEPARATE sqlite file from the gateway
        limiter's (``data/fsconnect_rate.db``) so the two subsystems never share
        rate-limit state. Values here are only consulted when ``enabled`` is true.
        """
        rl = self.write_rate_limit or {}
        max_ops = rl.get("max_ops", DEFAULT_RATE_MAX_OPS)
        return {
            "enabled": bool(rl.get("enabled", False)),
            "max_ops": max_ops,
            "window_seconds": rl.get("window_seconds", DEFAULT_RATE_WINDOW_SECONDS),
            "global_max_ops": rl.get("global_max_ops", 2 * max_ops),
            "db_path": rl.get("db_path", DEFAULT_RATE_DB_PATH),
        }

    def to_dict(self) -> dict:
        return asdict(self)


def load_fsconnect_config(config_path: str = "config.yaml") -> FsConnectConfig:
    """Read config.yaml's fsconnect: block and return a validated FsConnectConfig.

    Raises ``FsConnectConfigError`` if the block is absent, malformed, or any value
    fails validation. ``enabled`` is read out as a plain attribute (default False --
    the connector is conservatively opt-in) and enforced by the CLI. Unknown keys
    are collected on a non-fatal ``_unknown_keys`` attribute for typo visibility.
    """
    cfg = _get_config(config_path) or {}

    block = cfg.get("fsconnect")
    if not block:
        raise FsConnectConfigError(
            "fsconnect: block missing from config.yaml",
            details={
                "hint": "Append the fsconnect: block to config.yaml. "
                "See agentic/fsconnect/config.py for the schema."
            },
        )
    if not isinstance(block, dict):
        raise FsConnectConfigError(
            f"fsconnect: block must be a mapping, got {type(block).__name__}",
            details={"received_type": type(block).__name__},
        )

    known_fields = set(FsConnectConfig.__dataclass_fields__)
    unknown = set(block.keys()) - known_fields
    unknown.discard("enabled")  # CyClaw's own on/off toggle, not a config field
    kwargs = {k: v for k, v in block.items() if k in known_fields}

    try:
        fc = FsConnectConfig(**kwargs)
    except TypeError as exc:
        raise FsConnectConfigError(
            f"fsconnect: block invalid: {exc}",
            details={"unknown_keys": sorted(unknown)},
        ) from exc

    fc.enabled = bool(block.get("enabled", False))  # type: ignore[attr-defined]
    fc._unknown_keys = sorted(unknown)  # type: ignore[attr-defined]
    return fc
