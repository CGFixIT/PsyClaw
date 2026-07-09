"""Prompt injection filter and input sanitization.

Strips known injection patterns and validates input length.
Also used at index time to sanitize corpus chunks.

The filter is driven entirely by ``config.yaml`` (``policy.prompt_filter``):
``enabled``, ``banned_patterns`` and ``max_input_chars``. Patterns are
compiled once per config file and cached, so the hot path (every /query and
every chunk at index time) does not recompile regexes on each call.
"""

import logging
import re
from functools import lru_cache
from pathlib import Path
from re import Pattern

import yaml

from utils.errors import PromptInjectionError

logger = logging.getLogger("cyclaw.sanitizer")

# Fallback used only when config.yaml omits policy.prompt_filter entirely.
_DEFAULT_MAX_INPUT_CHARS = 4000

# Anchor a relative config_path to the repo root, mirroring utils/logger.py's
# _REPO_ROOT and utils/health.py. The default "config.yaml" is resolved against
# the CWD by open(), so a caller that does not pass an absolute path (gate.py's
# /query hot path calls check_input(req.query) with no path) would crash with
# FileNotFoundError whenever cyclaw-server is launched from outside the repo root
# — the exact Windows double-click failure mode gate.py's _BASE_DIR exists to
# prevent. Anchoring here keeps the injection filter CWD-independent like the
# rest of the config readers.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_config_path(config_path: str) -> Path:
    """Resolve ``config_path`` against the repo root when it is not absolute."""
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path.resolve()


@lru_cache(maxsize=8)
def _load_filter(config_path: str) -> tuple[bool, int, tuple[Pattern, ...]]:
    """Load and compile the prompt filter from config (cached per path).

    Returns ``(enabled, max_input_chars, compiled_patterns)``.
    """
    with open(_resolve_config_path(config_path), encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    # ``or {}`` at each level: a present-but-empty ``policy:`` or
    # ``prompt_filter:`` key parses to None, and chaining .get() on None would
    # raise AttributeError. Fall back to defaults instead of crashing.
    pf = (cfg.get("policy") or {}).get("prompt_filter") or {}
    enabled = pf.get("enabled", True)
    max_chars = pf.get("max_input_chars", _DEFAULT_MAX_INPUT_CHARS)
    patterns = tuple(
        re.compile(p, re.IGNORECASE) for p in pf.get("banned_patterns", [])
    )
    if enabled and not patterns:
        # Enabled with zero patterns silently degrades to a length-only check —
        # surface it rather than letting injection filtering become a no-op.
        logger.warning(
            "prompt_filter is enabled but no banned_patterns are configured in "
            "%s; injection filtering is disabled (length check only).",
            config_path,
        )
    return enabled, max_chars, patterns


def check_input(query: str, config_path: str = "config.yaml") -> str:
    """Validate user input against length and injection rules.

    Returns the (unmodified) query when it passes so callers can use it inline.
    Raises :class:`PromptInjectionError` when the input is too long or matches a
    banned pattern. When the filter is disabled in config, input passes through.
    """
    enabled, max_chars, patterns = _load_filter(config_path)
    if not enabled:
        return query

    if len(query) > max_chars:
        raise PromptInjectionError(
            f"Input exceeds maximum length: {len(query)} chars (max {max_chars})",
            details={"length": len(query), "max": max_chars},
        )

    for pattern in patterns:
        if pattern.search(query):
            raise PromptInjectionError(
                "Potential prompt injection detected",
                details={},
            )
    return query


def sanitize_chunk(text: str, config_path: str = "config.yaml") -> str:
    """Replace banned patterns in a corpus chunk with ``[FILTERED]``.

    Used at index time so injected instructions stored in the corpus cannot
    later be surfaced as retrieved context. No-op when the filter is disabled.
    """
    enabled, _max_chars, patterns = _load_filter(config_path)
    if not enabled:
        return text

    for pattern in patterns:
        text = pattern.sub("[FILTERED]", text)
    return text
