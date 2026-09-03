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
from urllib.parse import urlparse

from guardrails.errors import GuardrailsConfigError
from utils.logger import _get_config

# Defined locally rather than imported from llm/client.py: that module is the
# core request path, and guardrails must not import it (out-of-band isolation).
# harness/ollama.py keeps its own copy for the same reason.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_loopback_url(url: str) -> bool:
    return (urlparse(url).hostname or "").lower() in _LOOPBACK_HOSTS

# Defaults -- every key here can be overridden by config.yaml.
DEFAULT_ENGINE = "openai"  # Ollama exposes an OpenAI-compatible API
# Loopback-only binding to local Ollama is a core CyClaw security invariant
# (offline-first, never off-box), not debug code -- suppress the devskim heuristic.
DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"  # noqa: S104  # DevSkim: ignore DS162092
DEFAULT_MODEL = "qwen3.8:27b-mlx"
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


# input_rails/output_rails gate guardrails/integration.py's offline floor (see
# _offline_checks/check_output below): a rail name absent from the configured
# list is skipped. topical_rails has no offline implementation -- it is
# reserved for a future live-NeMo topical-rail flow and is not consulted by
# the offline floor; it is currently display-only (guardrails/cli.py status).
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
    # Ollama's OpenAI-compatible reasoning control. Deliberately NOT read from
    # the guardrails: block -- load_guardrails_config sources it from
    # models.local_llm.reasoning_effort so there is one key, not two that can
    # disagree (config-guard C11 already keeps guardrails.model in step with
    # local_llm.model for the same reason). None means "omit the field".
    reasoning_effort: str | None = None
    input_rails: list[str] = field(default_factory=lambda: list(DEFAULT_INPUT_RAILS))
    output_rails: list[str] = field(default_factory=lambda: list(DEFAULT_OUTPUT_RAILS))
    topical_rails: list[str] = field(default_factory=lambda: list(DEFAULT_TOPICAL_RAILS))
    soul_topics: list[str] = field(default_factory=lambda: list(DEFAULT_SOUL_TOPICS))

    # --- Validation -------------------------------------------------------

    def __post_init__(self) -> None:
        self._validate_enabled()
        self._validate_engine()
        self._validate_base_url()
        self._validate_threshold()
        self._validate_nemo_config_dir()
        self._validate_metrics_path()
        self._validate_rail_lists()

    def _validate_enabled(self) -> None:
        # YAML can load enabled: "false" as a string; truthy strings would turn
        # the opt-in layer ON. Same fail-closed pattern as sqlconnect/sync bools.
        if not isinstance(self.enabled, bool):
            raise GuardrailsConfigError(
                f"guardrails.enabled must be a boolean true/false, got: {self.enabled!r}",
                details={"field": "enabled", "received": repr(self.enabled)},
            )

    def _validate_rail_lists(self) -> None:
        # Dataclasses don't enforce field types at runtime, so a config.yaml
        # typo like `input_rails: check_injection` (a bare string instead of
        # a one-item list) would otherwise pass construction silently, then
        # have callers that iterate the field (cli.py's status display,
        # _offline_checks/check_output below) treat it as a sequence of
        # individual characters instead of failing fast at config load.
        for field_name in ("input_rails", "output_rails", "topical_rails", "soul_topics"):
            value = getattr(self, field_name)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise GuardrailsConfigError(
                    f"guardrails.{field_name} must be a list of strings, got: {value!r}",
                    details={"received": value},
                )

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
        raw = self.nemo_config_dir
        if ".." in Path(os.path.expanduser(os.path.expandvars(raw))).parts:
            raise GuardrailsConfigError(
                "guardrails.nemo_config_dir must not contain '..'",
                details={"received": raw},
            )
        anchored = Path(_anchor_to_repo_root(raw))
        try:
            resolved = anchored.resolve()
            resolved.relative_to(_REPO_ROOT.resolve())
        except ValueError as exc:
            raise GuardrailsConfigError(
                "guardrails.nemo_config_dir must stay inside the repository",
                details={"received": raw, "resolved": str(anchored)},
            ) from exc
        posix = resolved.as_posix().lower()
        if "/agentic/" in f"/{posix}/" or posix.rstrip("/").endswith("/agentic"):
            raise GuardrailsConfigError(
                "guardrails.nemo_config_dir must not be an agent-writable root",
                details={"resolved": str(resolved)},
            )
        _FORBIDDEN_EXEC = {".py", ".exe", ".bat", ".cmd", ".ps1", ".sh"}
        if resolved.is_dir():
            for child in resolved.rglob("*"):
                if child.is_file() and child.suffix.lower() in _FORBIDDEN_EXEC:
                    raise GuardrailsConfigError(
                        "unexpected executable in nemo_config_dir",
                        details={"file": str(child.relative_to(resolved))},
                    )
        self.nemo_config_dir = str(resolved)

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
    # Single source of truth: models.local_llm owns this value, so a stray
    # guardrails.reasoning_effort is overwritten rather than allowed to diverge.
    # resolve_reasoning_effort raises ConfigError on an invalid value.
    #
    #
    # Gated HERE rather than at the NeMo call site so the value carried on the
    # config object is, by construction, already safe to put on the wire (same
    # principle as ResolvedLocalBackend.reasoning_effort in llm/client.py):
    # reasoning_effort is Ollama-only, so an engine that is not OpenAI-speaking
    # or an endpoint repointed off loopback at a real OpenAI server resolves to
    # None instead of leaking an unknown field to a third party.
    from utils.config_validation import resolve_reasoning_effort

    models = cfg.get("models")
    effective_engine = kwargs.get("engine", DEFAULT_ENGINE)
    effective_base_url = kwargs.get("base_url", DEFAULT_BASE_URL)
    kwargs["reasoning_effort"] = (
        resolve_reasoning_effort(models.get("local_llm"))
        if isinstance(models, dict)
        and effective_engine in ("openai", "ollama")
        and _is_loopback_url(effective_base_url)
        else None
    )

    try:
        gc = GuardrailsConfig(**kwargs)
    except TypeError as exc:
        raise GuardrailsConfigError(
            f"guardrails: block invalid: {exc}",
            details={"unknown_keys": sorted(unknown)},
        ) from exc

    gc._unknown_keys = sorted(unknown)  # type: ignore[attr-defined]
    return gc
