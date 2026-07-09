"""AgenticConfig dataclass and validating loader for the CyClaw agentic: block.

Reads the ``agentic:`` block from CyClaw's single-source-of-truth ``config.yaml``
via ``utils.logger._get_config`` (shared cached load; tests reset it via
``reset_config_cache``). Purely additive: absence of the block disables the
agentic layer entirely without perturbing the gateway, graph, or MCP server.

Hardened defaults (conservative, matching CyClaw's offline-first posture):

  - enabled:        False     the whole layer is opt-in; absent key => disabled
  - mode:           "read"    read-only GitHub context; "write" is opt-in
  - writes_enabled: False     even in write mode, writes need this flag too
  - registry_path:  data/agentic/skills_registry.json   (must resolve under data/)

This module is part of a package that is NEVER imported by gate.py, graph.py, or
mcp_hybrid_server.py. That isolation is what preserves CyClaw's five security
invariants by construction.
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from utils.errors import AgenticConfigError
from utils.logger import _get_config

# Defaults -- every key here can be overridden by config.yaml.
DEFAULT_REPO = "CGFixIT/CyClaw"
DEFAULT_MODE = "read"  # "read" (safe default) | "write" (opt-in, still gated)
DEFAULT_WRITES_ENABLED = False
DEFAULT_GH_MIN_VERSION = "2.40.0"
DEFAULT_REGISTRY_PATH = "data/agentic/skills_registry.json"
DEFAULT_GH_TIMEOUT_SEC = 30  # wall-clock ceiling per gh read subprocess
DEFAULT_GH_RETRIES = 2  # extra attempts on a TRANSIENT gh failure (matches models.*.retry)
DEFAULT_ALLOWED_READ_OPS = (
    "pr_view",
    "pr_list",
    "pr_diff",
    "issue_view",
    "issue_list",
    "repo_view",
)

_VALID_MODES = ("read", "write")
# owner/name -- GitHub slugs allow alphanumerics, hyphen, underscore, dot, but the
# FIRST character of each segment must be alphanumeric. Anchoring it (rather than
# the looser ``[A-Za-z0-9_.-]+``) closes a flag-injection gap: a slug like
# "-x/y" otherwise validated and flowed positionally into ``gh repo view <repo>``,
# where ``gh`` would parse the leading "-" as an option. ``_SHELL_METACHARS`` does
# not list "-", so this regex is the boundary that rejects it. Same hardening the
# skills-registry name validator applies to its first character.
_REPO_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*$")
_GH_MIN_VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
# Shell metacharacters rejected at the boundary (defense in depth; argv is never
# passed through a shell, but taint is rejected here anyway).
_SHELL_METACHARS = set(";|&$`<>(){}[]!*?\"'\\\n\r\t ")


@dataclass
class AgenticConfig:
    """Parsed and validated agentic: block from config.yaml."""

    repo: str = DEFAULT_REPO
    mode: str = DEFAULT_MODE  # "read" | "write"
    writes_enabled: bool = DEFAULT_WRITES_ENABLED
    gh_min_version: str = DEFAULT_GH_MIN_VERSION
    registry_path: str = DEFAULT_REGISTRY_PATH
    allowed_read_ops: list[str] = field(
        default_factory=lambda: list(DEFAULT_ALLOWED_READ_OPS)
    )
    # gh read-path resilience knobs (threaded into gh_client.run_read).
    gh_timeout_sec: int = DEFAULT_GH_TIMEOUT_SEC
    gh_retries: int = DEFAULT_GH_RETRIES

    # --- Validation -------------------------------------------------------

    def __post_init__(self) -> None:
        self._validate_repo()
        self._validate_mode()
        self._validate_gh_min_version()
        self._validate_registry_path()
        self._validate_gh_runtime()

    def _validate_repo(self) -> None:
        if not _REPO_RE.match(self.repo):
            raise AgenticConfigError(
                f"agentic.repo must match 'owner/name', got: {self.repo!r}",
                details={"received": self.repo},
            )
        bad = sorted(_SHELL_METACHARS & set(self.repo))
        if bad:
            raise AgenticConfigError(
                f"agentic.repo contains forbidden characters: {bad!r}",
                details={"received": self.repo, "forbidden": bad},
            )

    def _validate_mode(self) -> None:
        if self.mode not in _VALID_MODES:
            raise AgenticConfigError(
                f"agentic.mode must be 'read' or 'write', got: {self.mode!r}",
                details={"received": self.mode, "valid": list(_VALID_MODES)},
            )

    def _validate_gh_min_version(self) -> None:
        if not _GH_MIN_VERSION_RE.match(self.gh_min_version):
            raise AgenticConfigError(
                f"agentic.gh_min_version must be 'X.Y.Z', got: {self.gh_min_version!r}",
                details={"received": self.gh_min_version},
            )

    def _validate_registry_path(self) -> None:
        if not self.registry_path:
            raise AgenticConfigError(
                "agentic.registry_path is required",
                details={"hint": "A path under the repo's data/ tree, e.g. data/agentic/skills_registry.json"},
            )
        expanded = os.path.expanduser(os.path.expandvars(self.registry_path))
        repo_root = Path(__file__).resolve().parent.parent
        path = Path(expanded)
        if not path.is_absolute():
            path = repo_root / path
        resolved = path.resolve()
        data_root = (repo_root / "data").resolve()
        # Must resolve to a path inside the repo's data/ tree. resolve() collapses
        # ".." and follows symlinks, so an escape cannot land inside data_root.
        if data_root not in resolved.parents:
            raise AgenticConfigError(
                "agentic.registry_path must resolve to a path inside the repo's data/ tree",
                details={"data_root": str(data_root)},
            )
        self.registry_path = str(resolved)

    def _validate_gh_runtime(self) -> None:
        t = self.gh_timeout_sec
        if not isinstance(t, int) or isinstance(t, bool) or t <= 0:
            raise AgenticConfigError(
                f"agentic.gh_timeout_sec must be a positive integer, got: {t!r}",
                details={"received": t},
            )
        r = self.gh_retries
        if not isinstance(r, int) or isinstance(r, bool) or r < 0:
            raise AgenticConfigError(
                f"agentic.gh_retries must be an integer >= 0 (0 = no retry), got: {r!r}",
                details={"received": r},
            )

    # --- Computed properties ---------------------------------------------

    @property
    def gh_min_tuple(self) -> tuple[int, int, int]:
        # gh_min_version is validated as X.Y.Z at construction, so split is safe.
        major, minor, patch = self.gh_min_version.split(".")
        return (int(major), int(minor), int(patch))

    @property
    def is_write_mode(self) -> bool:
        return self.mode == "write"

    def to_dict(self) -> dict:
        return asdict(self)


def load_agentic_config(config_path: str = "config.yaml") -> AgenticConfig:
    """Read config.yaml's agentic: block and return a validated AgenticConfig.

    Raises ``AgenticConfigError`` if the block is absent, malformed, or any value
    fails validation. The ``enabled`` toggle is read out as a plain attribute
    (defaulting to False when absent -- the agentic layer is conservatively
    opt-in) and enforced by the CLI. Unknown keys are collected on a non-fatal
    ``_unknown_keys`` attribute for typo visibility.
    """
    cfg = _get_config(config_path) or {}

    block = cfg.get("agentic")
    if not block:
        raise AgenticConfigError(
            "agentic: block missing from config.yaml",
            details={
                "hint": "Append the agentic: block to config.yaml. "
                "See docs/agentic/AGENTIC_README.md or agentic/config.py for the schema."
            },
        )

    if not isinstance(block, dict):
        raise AgenticConfigError(
            f"agentic: block must be a mapping, got {type(block).__name__}",
            details={"received_type": type(block).__name__},
        )

    known_fields = set(AgenticConfig.__dataclass_fields__)
    unknown = set(block.keys()) - known_fields
    # "enabled" is CyClaw's own on/off toggle, not a config field -- not a typo.
    unknown.discard("enabled")
    kwargs = {k: v for k, v in block.items() if k in known_fields}

    try:
        ac = AgenticConfig(**kwargs)
    except TypeError as exc:
        raise AgenticConfigError(
            f"agentic: block invalid: {exc}",
            details={"unknown_keys": sorted(unknown)},
        ) from exc

    # Conservative default: disabled unless explicitly enabled. Stored as a plain
    # attribute so it never leaks into the argv / to_dict surface.
    ac.enabled = bool(block.get("enabled", False))  # type: ignore[attr-defined]
    ac._unknown_keys = sorted(unknown)  # type: ignore[attr-defined]
    return ac
