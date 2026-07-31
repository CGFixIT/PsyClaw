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
mcp_hybrid_server.py. That isolation is what preserves CyClaw's six security
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
DEFAULT_DEEPAGENT_WORKSPACE_ROOT = "data/agentic/workspaces"
# Prefixes a real-repo candidate must not write into: files that judge the
# candidate's own acceptance. A candidate that rewrites the tests/lints
# grading it would otherwise be "accepted" by construction -- the single most
# common reward-hacking failure mode of a make-the-checks-pass loop. Matched
# as a path-PREFIX (see decide_real_repo_candidate's use), so "tests/" also
# covers "tests/unit/test_x.py".
DEFAULT_PROTECTED_WRITE_PATH_PREFIXES = (
    "tests/", "conftest.py", ".github/", ".git/",
    "pyproject.toml", "setup.cfg", "pytest.ini", ".claude/skills/",
)
# A whole ITERATION's total proposed-write size, not per-file (that's
# RepoWorkspaceTools.max_write_bytes). The planner's own system prompt already
# asks for "the smallest change that satisfies the instruction" -- an
# iteration blowing past this budget is a signal something went wrong, not a
# legitimately large legitimate change.
DEFAULT_MAX_WRITE_BUDGET_BYTES = 100_000
DEFAULT_HARNESS_OUTPUT_DIR = "data/agentic/harness_optimizer/runs"
DEFAULT_HARNESS_MEMORY_DIR = "data/agentic/harness_optimizer/memory"

_VALID_MODES = ("read", "write")
# Post-Ollama migration: "lmstudio" is retired as a provider id. Use "ollama"
# (default) or "openai_compatible" for any other OpenAI-shaped local server
# (including LM Studio's compatibility endpoint if an operator still runs it).
_VALID_DEEPAGENT_PROVIDERS = ("ollama", "openai_compatible")
# Cloud coding-loop providers, keyed by the env var holding their API key. Keys
# live in the environment ONLY -- never in config.yaml, never in a dataclass
# field -- mirroring llm/client.py's discipline for the core graph's fallback.
CLOUD_KEY_ENVS = {"grok": "GROK_API_KEY", "claude": "ANTHROPIC_API_KEY"}
_VALID_CLOUD_PROVIDERS = tuple(CLOUD_KEY_ENVS)
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


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _validate_bool(value: object, field_name: str) -> None:
    if not isinstance(value, bool):
        raise AgenticConfigError(
            f"{field_name} must be a boolean, got: {value!r}",
            details={"field": field_name, "received": value},
        )


def _validate_no_shell_metachars(value: str, field_name: str) -> None:
    bad = sorted(_SHELL_METACHARS & set(value))
    if bad:
        raise AgenticConfigError(
            f"{field_name} contains forbidden characters: {bad!r}",
            details={"field": field_name, "received": value, "forbidden": bad},
        )


def _resolve_data_path(raw_path: str, field_name: str) -> str:
    if not isinstance(raw_path, str) or not raw_path:
        raise AgenticConfigError(
            f"{field_name} is required",
            details={"hint": "Use a path under the repo's data/ tree."},
        )
    expanded = os.path.expanduser(os.path.expandvars(raw_path))
    repo_root = _repo_root()
    path = Path(expanded)
    if not path.is_absolute():
        path = repo_root / path
    resolved = path.resolve()
    data_root = (repo_root / "data").resolve()
    if data_root not in resolved.parents:
        raise AgenticConfigError(
            f"{field_name} must resolve to a path inside the repo's data/ tree",
            details={"data_root": str(data_root), "received": raw_path},
        )
    return str(resolved)


@dataclass
class DeepAgentCloudProviderConfig:
    """Per-provider cloud enable + model name. No key material, ever."""

    enabled: bool = False
    model: str = ""

    def __post_init__(self) -> None:
        _validate_bool(self.enabled, "agentic.deepagent_github.providers.*.enabled")
        if not isinstance(self.model, str):
            raise AgenticConfigError("agentic.deepagent_github.providers.*.model must be a string")
        _validate_no_shell_metachars(self.model, "agentic.deepagent_github.providers.*.model")


@dataclass
class DeepAgentGitHubConfig:
    """Optional Deep Agents GitHub harness config. Disabled by default."""

    enabled: bool = False
    provider: str = "ollama"
    base_url: str = "http://localhost:11434/v1"
    model: str = ""
    allow_deepagents_dependency: bool = False
    allow_filesystem_write_tools: bool = False
    allow_shell_execution: bool = False
    allow_github_writes: bool = False
    workspace_root: str = DEFAULT_DEEPAGENT_WORKSPACE_ROOT
    allow_cloud_providers: bool = False
    providers: dict = field(default_factory=dict)
    # Deliberately its OWN flag, not a reuse of allow_shell_execution: that flag
    # already means something different and stricter -- it gates whether the
    # deep-agent gets a generic host-shell TOOL it can invoke with any command
    # (agentic/deepagent_github/permissions.py::refuse_unsupported_write_policy
    # hard-refuses the whole build when it's true, since that tool remains
    # unimplemented). RepoWorkspaceTools' git methods run four fixed,
    # non-attacker-chosen subcommands (checkout -b/add/commit/diff) against a
    # clone this layer made itself, never pushed -- a materially narrower
    # capability that deserves its own default-off gate rather than colliding
    # with a flag whose current meaning is "give the model a shell."
    allow_git_write_tools: bool = False
    protected_write_paths: list[str] = field(
        default_factory=lambda: list(DEFAULT_PROTECTED_WRITE_PATH_PREFIXES)
    )
    max_write_budget_bytes: int = DEFAULT_MAX_WRITE_BUDGET_BYTES

    def cloud_provider(self, name: str) -> DeepAgentCloudProviderConfig | None:
        """Return a provider's config, or None when it is not configured/enabled.

        Returns None unless BOTH allow_cloud_providers and the provider's own
        enabled flag are true, so a caller cannot accidentally treat a configured
        but un-gated provider as usable.
        """
        if not self.allow_cloud_providers:
            return None
        provider = self.providers.get(name)
        if isinstance(provider, DeepAgentCloudProviderConfig) and provider.enabled:
            return provider
        return None

    def __post_init__(self) -> None:
        for field_name in (
            "agentic.deepagent_github.enabled",
            "agentic.deepagent_github.allow_deepagents_dependency",
            "agentic.deepagent_github.allow_filesystem_write_tools",
            "agentic.deepagent_github.allow_shell_execution",
            "agentic.deepagent_github.allow_github_writes",
            "agentic.deepagent_github.allow_cloud_providers",
            "agentic.deepagent_github.allow_git_write_tools",
        ):
            attr = field_name.rsplit(".", 1)[-1]
            _validate_bool(getattr(self, attr), field_name)
        if self.provider not in _VALID_DEEPAGENT_PROVIDERS:
            raise AgenticConfigError(
                # The prose and the tuple must be edited together; the tuple is the
                # source of truth and the message is what an operator actually reads.
                f"agentic.deepagent_github.provider must be one of {list(_VALID_DEEPAGENT_PROVIDERS)}",
                details={"received": self.provider, "valid": list(_VALID_DEEPAGENT_PROVIDERS)},
            )
        self._coerce_providers()
        if not isinstance(self.base_url, str) or not self.base_url:
            raise AgenticConfigError("agentic.deepagent_github.base_url must be a non-empty string")
        if not isinstance(self.model, str):
            raise AgenticConfigError("agentic.deepagent_github.model must be a string")
        _validate_no_shell_metachars(self.base_url, "agentic.deepagent_github.base_url")
        _validate_no_shell_metachars(self.model, "agentic.deepagent_github.model")
        self.workspace_root = _resolve_data_path(
            self.workspace_root,
            "agentic.deepagent_github.workspace_root",
        )
        if not isinstance(self.protected_write_paths, list) or not all(
            isinstance(p, str) and p for p in self.protected_write_paths
        ):
            raise AgenticConfigError(
                "agentic.deepagent_github.protected_write_paths must be a list of non-empty strings",
                details={"received": self.protected_write_paths},
            )
        if not isinstance(self.max_write_budget_bytes, int) or isinstance(
            self.max_write_budget_bytes, bool
        ) or self.max_write_budget_bytes <= 0:
            raise AgenticConfigError(
                "agentic.deepagent_github.max_write_budget_bytes must be a positive integer",
                details={"received": self.max_write_budget_bytes},
            )

    def _coerce_providers(self) -> None:
        # Nested blocks arrive as plain dicts from yaml. Unlike the top-level
        # agentic: block, unknown keys here are NOT filtered upstream, so an
        # unrecognised provider name would silently become dead config -- reject it
        # loudly instead.
        if not isinstance(self.providers, dict):
            raise AgenticConfigError(
                "agentic.deepagent_github.providers must be a mapping",
                details={"received": type(self.providers).__name__},
            )
        coerced: dict[str, DeepAgentCloudProviderConfig] = {}
        for name, block in self.providers.items():
            if name not in _VALID_CLOUD_PROVIDERS:
                raise AgenticConfigError(
                    f"unknown agentic.deepagent_github.providers entry {name!r}",
                    details={"received": name, "valid": list(_VALID_CLOUD_PROVIDERS)},
                )
            if isinstance(block, DeepAgentCloudProviderConfig):
                coerced[name] = block
                continue
            if not isinstance(block, dict):
                raise AgenticConfigError(
                    f"agentic.deepagent_github.providers.{name} must be a mapping",
                    details={"received": type(block).__name__},
                )
            try:
                coerced[name] = DeepAgentCloudProviderConfig(**block)
            except TypeError as exc:
                raise AgenticConfigError(
                    f"agentic.deepagent_github.providers.{name} invalid: {exc}",
                    details={"provider": name},
                ) from exc
        # Fail LOUD, not silently inert: a provider marked enabled while the master
        # cloud gate is off almost certainly means the operator believes cloud is
        # on. Refusing to boot is the honest response; quietly running local-only
        # would look identical to a working cloud setup.
        if not self.allow_cloud_providers:
            live = sorted(name for name, provider in coerced.items() if provider.enabled)
            if live:
                raise AgenticConfigError(
                    "agentic.deepagent_github.providers enabled while allow_cloud_providers is false",
                    details={"enabled": live},
                )
        self.providers = coerced


@dataclass
class HarnessOptimizerConfig:
    """Governed harness optimizer config. Disabled by default."""

    enabled: bool = False
    max_iterations: int = 3
    require_human_confirm_for_accept: bool = True
    output_dir: str = DEFAULT_HARNESS_OUTPUT_DIR
    memory_dir: str = DEFAULT_HARNESS_MEMORY_DIR
    allow_local_model_judge: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "agentic.harness_optimizer.enabled",
            "agentic.harness_optimizer.require_human_confirm_for_accept",
            "agentic.harness_optimizer.allow_local_model_judge",
        ):
            attr = field_name.rsplit(".", 1)[-1]
            _validate_bool(getattr(self, attr), field_name)
        if (
            not isinstance(self.max_iterations, int)
            or isinstance(self.max_iterations, bool)
            or self.max_iterations <= 0
        ):
            raise AgenticConfigError(
                "agentic.harness_optimizer.max_iterations must be a positive integer",
                details={"received": self.max_iterations},
            )
        self.output_dir = _resolve_data_path(
            self.output_dir,
            "agentic.harness_optimizer.output_dir",
        )
        self.memory_dir = _resolve_data_path(
            self.memory_dir,
            "agentic.harness_optimizer.memory_dir",
        )


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
    deepagent_github: DeepAgentGitHubConfig | dict = field(default_factory=DeepAgentGitHubConfig)
    harness_optimizer: HarnessOptimizerConfig | dict = field(default_factory=HarnessOptimizerConfig)

    # --- Validation -------------------------------------------------------

    def __post_init__(self) -> None:
        self._coerce_nested_configs()
        self._validate_top_level_bools()
        self._validate_repo()
        self._validate_mode()
        self._validate_gh_min_version()
        self._validate_registry_path()
        self._validate_gh_runtime()

    def _coerce_nested_configs(self) -> None:
        if isinstance(self.deepagent_github, dict):
            self.deepagent_github = DeepAgentGitHubConfig(**self.deepagent_github)
        if isinstance(self.harness_optimizer, dict):
            self.harness_optimizer = HarnessOptimizerConfig(**self.harness_optimizer)

    def _validate_top_level_bools(self) -> None:
        _validate_bool(self.writes_enabled, "agentic.writes_enabled")

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
        self.registry_path = _resolve_data_path(self.registry_path, "agentic.registry_path")

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
    enabled = block.get("enabled", False)
    _validate_bool(enabled, "agentic.enabled")
    ac.enabled = enabled  # type: ignore[attr-defined]
    ac._unknown_keys = sorted(unknown)  # type: ignore[attr-defined]
    return ac
