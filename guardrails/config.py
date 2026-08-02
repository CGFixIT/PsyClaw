"""GuardrailsConfig dataclass and validating loader for the ``guardrails:`` block.

Reads the ``guardrails:`` block from CyClaw's single-source-of-truth
``config.yaml`` via ``utils.logger._get_config`` (shared cached load; tests reset
it via ``reset_config_cache``). Purely additive: absence of the block disables
the guardrails layer entirely without perturbing the gateway, graph, or MCP
server.

Hardened defaults (conservative, matching CyClaw's offline-first posture):

  - enabled:       False     the whole layer is opt-in; absent key => disabled
  - engine:        "openai"  Ollama exposes an OpenAI-compatible endpoint
  - base_url:      loopback Ollama endpoint   intentional (offline-first)
  - metrics_path:  logs/guardrails.jsonl   SEPARATE from logs/audit.jsonl

This module is part of a package that is NEVER imported by gate.py, graph.py, or
mcp_hybrid_server.py. That isolation is what preserves CyClaw's five security
invariants by construction.
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from guardrails.errors import GuardrailsConfigError
from utils.logger import _get_config

# Defaults -- every key here can be overridden by config.yaml.
DEFAULT_ENGINE = "openai"  # Ollama exposes an OpenAI-compatible API
# Loopback-only binding to local Ollama is a core CyClaw security invariant
# (offline-first, never off-box), not debug code -- suppress the devskim heuristic.
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"  # noqa: S104  # DevSkim: ignore DS162092
DEFAULT_MODEL = "qwen3.6:27b"
DEFAULT_NEMO_CONFIG_DIR = "guardrails/config"
DEFAULT_METRICS_PATH = "logs/guardrails.jsonl"
DEFAULT_BLOCK_MESSAGE = (
    "I can't help with that request. It was stopped by a CyClaw safety guardrail."
)
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _anchor_to_repo_root(raw: str) -> str:
    """Expand a configured path, anchoring a relative one to the repo root.

    Never the process cwd. CyClaw is launched from a service manager, a
    Windows double-click and the CLI from arbitrary directories, so a
    cwd-relative path names a different file on each of them (CLAUDE.md 4).
    """
    expanded = os.path.expanduser(os.path.expandvars(raw))
    path = Path(expanded)
    if not path.is_absolute():
        path = _REPO_ROOT / expanded
    return str(path)


DEFAULT_INPUT_RAILS = ("check_injection", "check_jailbreak", "check_soul_mutation")
DEFAULT_OUTPUT_RAILS = ("check_grounding", "check_soul_leak")
DEFAULT_TOPICAL_RAILS = ("stay_in_local_knowledge", "no_unauthed_external_advice")
# Keywords that flag a query (or answer) as touching the soul / personality /
# identity layer -- the topic class these advanced rails are tailored to.
DEFAULT_SOUL_TOPICS = (
    "soul",
    "personality",
    "identity",
    "who are you",
    "your name",
    "your purpose",
    "system prompt",
    "your instructions",
    "persona",
)

_VALID_ENGINES = ("openai", "ollama", "nim", "nemollm")


@dataclass
class GuardrailsConfig:
    """Parsed and validated ``guardrails:`` block from config.yaml.

    Carries only declarative configuration -- no NeMo objects. The live
    ``LLMRails`` engine is built lazily in ``guardrails.integration`` from these
    values, so importing this module never pulls in ``nemoguardrails``.
    """

    enabled: bool = False
    engine: str = DEFAULT_ENGINE
    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    nemo_config_dir: str = DEFAULT_NEMO_CONFIG_DIR
    metrics_path: str = DEFAULT_METRICS_PATH
    block_message: str = DEFAULT_BLOCK_MESSAGE
    # 0.0..1.0 token-overlap floor below which an answer is flagged as a possible
    # hallucination (ungrounded in retrieved context). Offline heuristic; the
    # NeMo self-check rail is the model-assisted complement (see rails.co).
    hallucination_threshold: float = 0.18
    input_rails: list[str] = field(default_factory=lambda: list(DEFAULT_INPUT_RAILS))
    output_rails: list[str] = field(default_factory=lambda: list(DEFAULT_OUTPUT_RAILS))
    topical_rails: list[str] = field(default_factory=lambda: list(DEFAULT_TOPICAL_RAILS))
    soul_topics: list[str] = field(default_factory=lambda: list(DEFAULT_SOUL_TOPICS))

    # --- Validation -------------------------------------------------------

    def __post_init__(self) -> None:
        self._validate_engine()
        self._validate_base_url()
        self._validate_threshold()
        self._validate_nemo_config_dir()
        self._validate_metrics_path()

    def _validate_engine(self) -> None:
        if self.engine not in _VALID_ENGINES:
            raise GuardrailsConfigError(
                f"guardrails.engine must be one of {_VALID_ENGINES}, got: {self.engine!r}",
                details={"received": self.engine, "valid": list(_VALID_ENGINES)},
            )

    def _validate_base_url(self) -> None:
        if not (self.base_url.startswith("http://") or self.base_url.startswith("https://")):
            raise GuardrailsConfigError(
                f"guardrails.base_url must be an http(s) URL, got: {self.base_url!r}",
                details={"received": self.base_url},
            )

    def _validate_threshold(self) -> None:
        if not (0.0 <= self.hallucination_threshold <= 1.0):
            raise GuardrailsConfigError(
                "guardrails.hallucination_threshold must be within [0.0, 1.0], "
                f"got: {self.hallucination_threshold!r}",
                details={"received": self.hallucination_threshold},
            )

    def _validate_nemo_config_dir(self) -> None:
        if not self.nemo_config_dir:
            raise GuardrailsConfigError(
                "guardrails.nemo_config_dir is required",
                details={"hint": "Directory holding config.yml + rails.co (default: guardrails/config)"},
            )
        self.nemo_config_dir = _anchor_to_repo_root(self.nemo_config_dir)

    def _validate_metrics_path(self) -> None:
        if not self.metrics_path:
            raise GuardrailsConfigError(
                "guardrails.metrics_path is required",
                details={"hint": f"JSONL event stream (default: {DEFAULT_METRICS_PATH}). "
                                 "Use GuardrailMetrics(persist=False) to disable persistence."},
            )
        # Anchored for the same reason nemo_config_dir is: the shipped default
        # is relative, and GuardrailMetrics swallows the resulting OSError, so
        # a cwd-relative path meant guardrail telemetry silently landed in
        # (or vanished from) whichever directory the process happened to start
        # in -- while the rail itself kept enforcing. gate.py:718 already
        # anchors logs/audit.jsonl this way; this brings the second stream in line.
        self.metrics_path = _anchor_to_repo_root(self.metrics_path)

    # --- Computed helpers -------------------------------------------------

    @property
    def config_yml_path(self) -> Path:
        return Path(self.nemo_config_dir) / "config.yml"

    @property
    def rails_co_path(self) -> Path:
        return Path(self.nemo_config_dir) / "rails.co"

    @property
    def nemo_config_present(self) -> bool:
        """True when both NeMo config files exist on disk."""
        return self.config_yml_path.is_file() and self.rails_co_path.is_file()

    def to_dict(self) -> dict:
        return asdict(self)


def load_guardrails_config(config_path: str = "config.yaml") -> GuardrailsConfig:
    """Read config.yaml's ``guardrails:`` block and return a validated config.

    Absence of the block is NOT an error -- it returns a disabled default config
    (the layer is conservatively opt-in, and absence must mean "off", never a
    crash that could ripple into anything that imports this loader). A present
    block that is malformed *does* raise :class:`GuardrailsConfigError`.
    Unknown keys are collected on a non-fatal ``_unknown_keys`` attribute for
    typo visibility.
    """
    cfg = _get_config(config_path) or {}

    block = cfg.get("guardrails")
    if block is None:
        # Absent -> disabled defaults. Opt-in by construction.
        gc = GuardrailsConfig(enabled=False)
        gc._unknown_keys = []  # type: ignore[attr-defined]
        return gc

    if not isinstance(block, dict):
        raise GuardrailsConfigError(
            f"guardrails: block must be a mapping, got {type(block).__name__}",
            details={"received_type": type(block).__name__},
        )

    known_fields = set(GuardrailsConfig.__dataclass_fields__)
    unknown = set(block.keys()) - known_fields
    kwargs = {k: v for k, v in block.items() if k in known_fields}

    try:
        gc = GuardrailsConfig(**kwargs)
    except TypeError as exc:
        raise GuardrailsConfigError(
            f"guardrails: block invalid: {exc}",
            details={"unknown_keys": sorted(unknown)},
        ) from exc

    gc._unknown_keys = sorted(unknown)  # type: ignore[attr-defined]
    return gc
