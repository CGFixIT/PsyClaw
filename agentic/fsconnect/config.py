"""FsConnectConfig dataclass and validating loader for the fsconnect: block.

Reads ``fsconnect:`` from CyClaw's ``config.yaml`` via ``utils.logger._get_config``
(same cached load + ``reset_config_cache`` test hook as ``sync.config``).

Design intent: the block is OFF by default and every dangerous capability is a
separate explicit flag, so presence of the block alone exposes nothing.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from utils.errors import FsConnectConfigError
from utils.logger import _get_config

# Defaults (all overridable in config.yaml).
DEFAULT_MAX_FILE_BYTES = 1024 * 1024  # 1 MiB -- Markdown notes are tiny
DEFAULT_MAX_GREP_MATCHES = 200
DEFAULT_MAX_LIST_ENTRIES = 1000
DEFAULT_QUOTA_BYTES = 50 * 1024 * 1024  # 50 MiB per writable root
DEFAULT_SCAN_BYTES = 64 * 1024  # first 64 KiB scanned for injection patterns
DEFAULT_TRASH_DIRNAME = ".cyclaw-trash"
DEFAULT_TRASH_RETENTION_DAYS = 30
DEFAULT_INDEX_MAX_FILE_BYTES = 256 * 1024  # corpus-sized files only
DEFAULT_RATE_LIMIT_OPS_PER_MIN = 60  # 0 disables the limiter
DEFAULT_QUOTA_RECOMPUTE_HOURS = 24.0  # floor between full du scans (0 = every op)
DEFAULT_INDEX_EXTENSIONS = [".md", ".txt"]  # mirrors corpus.extensions
DEFAULT_WRAPPER_CAP = 500  # matches mcp_hybrid_server._WRAPPER_CAP

READ_OPS = ("fs_list", "fs_stat", "fs_read", "fs_grep", "fs_glob")
WRITE_OPS = ("fs_write", "fs_move", "fs_delete")
# Ops reachable via terminal/MCP WITHOUT any per-op config opt-in. Index ops are
# intentionally absent: they require ``index_enabled: true`` (checked again in
# the terminal handler, not just here).
_DEFAULT_SAFE_OPS = READ_OPS


# Boolean-typed gate fields of FsConnectConfig, strictly validated in
# __post_init__. Explicit list (not derived): with `from __future__ import
# annotations` field types are strings, and the list doubles as the checklist
# of which gates are load-bearing.
_BOOL_FIELDS = (
    "allow_unc_roots",
    "follow_symlinks",
    "scan_content",
    "writes_enabled",
    "require_confirm_destructive",
    "strict_roots",
    "block_on_injection_flags",
    "allow_hard_delete",
    "index_enabled",
    "index_incremental",
)


def os_default_writable_root() -> str:
    """OS-specific default share root (used when writable_roots: [null])."""
    if os.name == "nt":
        return r"C:\CyClaw-FS"
    return "/var/lib/cyclaw-fs" if os.geteuid() == 0 else os.path.expanduser("~/CyClaw-FS")


@dataclass
class FsConnectConfig:
    """Parsed and validated fsconnect: block from config.yaml."""

    # Read surface.
    allowed_roots: list[str] = field(default_factory=list)
    allowed_fs_ops: list[str] = field(default_factory=lambda: list(_DEFAULT_SAFE_OPS))
    allow_unc_roots: bool = False
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES
    max_grep_matches: int = DEFAULT_MAX_GREP_MATCHES
    max_list_entries: int = DEFAULT_MAX_LIST_ENTRIES

    # Path handling.
    follow_symlinks: bool = False
    scan_content: bool = True

    # Write surface (Phase 2 -- all default off / safest).
    # Each entry is either a plain path str or {path: str, quota_bytes: int | null}
    # for a per-root quota override (null inherits the global quota_bytes).
    writable_roots: list = field(default_factory=list)
    writes_enabled: bool = False
    require_confirm_destructive: bool = True
    strict_roots: bool = True
    block_on_injection_flags: bool = True
    allow_hard_delete: bool = False
    quota_bytes: int = DEFAULT_QUOTA_BYTES
    trash_dirname: str = DEFAULT_TRASH_DIRNAME
    trash_retention_days: int = DEFAULT_TRASH_RETENTION_DAYS

    # Abuse throttling (Phase 3.5 -- 0 disables; off by default).
    rate_limit_ops_per_min: int = DEFAULT_RATE_LIMIT_OPS_PER_MIN

    # Write-root quota accounting (Phase 4.5). On the first write op after
    # startup, or after the cached usage is older than quota_recompute_hours,
    # usage is recomputed with a full directory walk; otherwise it adjusts the
    # cache incrementally per op.
    quota_recompute_hours: float = DEFAULT_QUOTA_RECOMPUTE_HOURS

    # RAG-corpus indexing of the share (Phase 5 -- decoupled).
    index_enabled: bool = False
    index_root: str = ""  # default: first writable root
    index_extensions: list[str] = field(default_factory=lambda: list(DEFAULT_INDEX_EXTENSIONS))
    index_max_file_bytes: int = DEFAULT_INDEX_MAX_FILE_BYTES
    index_incremental: bool = False

    # MCP wrapper parity (Phase 7).
    wrapper_cap: int = DEFAULT_WRAPPER_CAP

    # --- Validation --------------------------------------------------------

    def __post_init__(self) -> None:
        self._validate_booleans()
        self._validate_ops()
        self._validate_caps()
        self._validate_symlinks()
        self._validate_phase2_scalars()
        self._check_root_list("allowed_roots", self.allowed_roots)
        self._normalize_writable_roots()
        self._validate_rate_limit()
        self._normalize_index()

    def _validate_booleans(self) -> None:
        # Every execution/deletion gate must be a REAL boolean: bool("false")
        # is True, so a quoted YAML string would silently OPEN the gate it was
        # meant to close (codex P2: quoted writes_enabled / allow_hard_delete).
        for name in _BOOL_FIELDS:
            val = getattr(self, name)
            if not isinstance(val, bool):
                raise FsConnectConfigError(
                    f"fsconnect.{name} must be a boolean true/false, got: {val!r}",
                    details={"field": name, "received": repr(val)},
                )

    def _validate_ops(self) -> None:
        unknown = sorted(set(self.allowed_fs_ops) - set(READ_OPS) - set(WRITE_OPS))
        if unknown:
            raise FsConnectConfigError(
                f"fsconnect.allowed_fs_ops: unknown ops: {unknown!r}",
                details={"valid": sorted(READ_OPS + WRITE_OPS)},
            )

    def _validate_caps(self) -> None:
        for name, val in (
            ("max_file_bytes", self.max_file_bytes),
            ("max_grep_matches", self.max_grep_matches),
            ("max_list_entries", self.max_list_entries),
            ("quota_bytes", self.quota_bytes),
            ("index_max_file_bytes", self.index_max_file_bytes),
            ("wrapper_cap", self.wrapper_cap),
        ):
            if val < 1:
                raise FsConnectConfigError(
                    f"fsconnect.{name} must be >= 1, got: {val!r}",
                    details={"received": val},
                )

    def _validate_symlinks(self) -> None:
        if self.follow_symlinks:
            raise FsConnectConfigError(
                "fsconnect.follow_symlinks: true is unsupported (symlink-escape risk); "
                "the security core always walks O_NOFOLLOW",
                details={"received": True},
            )

    def _validate_phase2_scalars(self) -> None:
        if not isinstance(self.trash_retention_days, int) or self.trash_retention_days < 0:
            raise FsConnectConfigError(
                f"fsconnect.trash_retention_days must be a non-negative int, got: {self.trash_retention_days!r}",
                details={"received": self.trash_retention_days},
            )
        if isinstance(self.quota_recompute_hours, bool) or not isinstance(
            self.quota_recompute_hours, (int, float)
        ):
            raise FsConnectConfigError(
                "fsconnect.quota_recompute_hours must be a number, "
                f"got: {self.quota_recompute_hours!r}",
                details={"received": self.quota_recompute_hours},
            )
        if self.quota_recompute_hours < 0:
            raise FsConnectConfigError(
                "fsconnect.quota_recompute_hours must be >= 0, "
                f"got: {self.quota_recompute_hours!r}",
                details={"received": self.quota_recompute_hours},
            )

    def _validate_rate_limit(self) -> None:
        if isinstance(self.rate_limit_ops_per_min, bool) or not isinstance(self.rate_limit_ops_per_min, int):
            raise FsConnectConfigError(
                f"fsconnect.rate_limit_ops_per_min must be an int, got: {self.rate_limit_ops_per_min!r}",
                details={"received": self.rate_limit_ops_per_min},
            )
        if self.rate_limit_ops_per_min < 0:
            raise FsConnectConfigError(
                f"fsconnect.rate_limit_ops_per_min must be >= 0 (0 disables), got: {self.rate_limit_ops_per_min!r}",
                details={"received": self.rate_limit_ops_per_min},
            )

    def _check_root_list(self, name: str, roots: list[str]) -> None:
        if not isinstance(roots, list) or any(not isinstance(r, str) for r in roots):
            raise FsConnectConfigError(
                f"fsconnect.{name} must be a list of path strings",
                details={"received": repr(roots)},
            )
        for root in roots:
            if root.startswith("\\\\") and not self.allow_unc_roots:
                raise FsConnectConfigError(
                    f"fsconnect.{name}: UNC root {root!r} requires allow_unc_roots: true",
                    details={"root": root},
                )

    def _normalize_writable_roots(self) -> None:
        """Validate writable_roots entries; expand the single null placeholder."""
        roots = self.writable_roots
        if roots is None:
            roots = []
        if not isinstance(roots, list):
            raise FsConnectConfigError(
                "fsconnect.writable_roots must be a list",
                details={"received": repr(roots)},
            )
        normalized: list[dict] = []
        for entry in roots:
            if entry is None:
                # Explicit [null] expands to the OS default share root.
                normalized.append({"path": os_default_writable_root(), "quota_bytes": None})
                continue
            if isinstance(entry, str):
                if not entry:
                    raise FsConnectConfigError(
                        "fsconnect.writable_roots: empty path entry",
                        details={"received": entry},
                    )
                normalized.append({"path": entry, "quota_bytes": None})
                continue
            if isinstance(entry, dict):
                path = entry.get("path")
                if not isinstance(path, str) or not path:
                    raise FsConnectConfigError(
                        "fsconnect.writable_roots: {path: ...} entry needs a non-empty string path",
                        details={"received": repr(entry)},
                    )
                quota = entry.get("quota_bytes")
                if quota is not None and (isinstance(quota, bool) or not isinstance(quota, int) or quota < 1):
                    raise FsConnectConfigError(
                        f"fsconnect.writable_roots: quota_bytes must be an int >= 1 or null, got: {quota!r}",
                        details={"received": repr(entry)},
                    )
                normalized.append({"path": path, "quota_bytes": quota})
                continue
            raise FsConnectConfigError(
                "fsconnect.writable_roots entries must be path strings, {path, quota_bytes} mappings, or null",
                details={"received": repr(entry)},
            )
        self._writable_root_entries = normalized
        self._check_root_list("writable_roots", [e["path"] for e in normalized])

    def _normalize_index(self) -> None:
        exts = []
        for e in self.index_extensions:
            if not isinstance(e, str) or not e:
                raise FsConnectConfigError(
                    f"fsconnect.index_extensions: bad entry: {e!r}",
                    details={"received": repr(self.index_extensions)},
                )
            exts.append(e if e.startswith(".") else f".{e}")
        self.index_extensions = [e.lower() for e in exts]
        if not self.index_root and self._writable_root_entries:
            self.index_root = self._writable_root_entries[0]["path"]

    # --- Accessors ----------------------------------------------------------

    @property
    def write_root_strs(self) -> list[str]:
        return [e["path"] for e in getattr(self, "_writable_root_entries", [])]

    def root_quota(self, root: str) -> int:
        """Effective quota for *root* (per-root override or the global default)."""
        for e in getattr(self, "_writable_root_entries", []):
            if e["path"] == root and e["quota_bytes"] is not None:
                return e["quota_bytes"]
        return self.quota_bytes

    def to_dict(self) -> dict:
        return asdict(self)


def load_fsconnect_config(config_path: str = "config.yaml") -> FsConnectConfig:
    """Read config.yaml's fsconnect: block and return a validated FsConnectConfig.

    The block is optional: when absent we raise FsConnectConfigError so callers
    can treat "connector not configured" as a clean no-op (feature off).
    """
    cfg = _get_config(config_path) or {}
    block = cfg.get("fsconnect")
    if block is None:
        raise FsConnectConfigError(
            "fsconnect: block missing from config.yaml",
            details={"hint": "Add an fsconnect: block to config.yaml (see agentic/fsconnect/)."},
        )
    if not isinstance(block, dict):
        raise FsConnectConfigError(
            f"fsconnect: block must be a mapping, got {type(block).__name__}",
            details={"received_type": type(block).__name__},
        )

    known = set(FsConnectConfig.__dataclass_fields__)
    kwargs = {k: v for k, v in block.items() if k in known}
    try:
        fc = FsConnectConfig(**kwargs)
    except TypeError as exc:
        raise FsConnectConfigError(
            f"fsconnect: block invalid: {exc}",
            details={"keys": sorted(block)},
        ) from exc

    if "enabled" in block and not isinstance(block["enabled"], bool):
        raise FsConnectConfigError(
            f"fsconnect.enabled must be a boolean true/false, got: {block['enabled']!r}",
            details={"field": "enabled", "received": repr(block["enabled"])},
        )
    fc.enabled = block.get("enabled", False)  # type: ignore[attr-defined]  # validated bool above
    fc._unknown_keys = sorted(set(block) - known - {"enabled"})  # type: ignore[attr-defined]
    return fc
