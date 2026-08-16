"""Append-only Grok/Claude token ledger. Dollars are derived at read time.

Not routed through ``audit_log`` — spend.jsonl is a separate stream so
metrics can reprice history without polluting the audit trail. Never persist
query/prompt/content/messages or credentials.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

from utils.logger import _anchor, _get_config

logger = logging.getLogger("cyclaw.spend")

_SPEND_WRITE_LOCK = threading.Lock()
_DEFAULT_SPEND_FILE = "logs/spend.jsonl"
PRICED_AS_OF = "2026-08-16"

# USD per 1M tokens. Hardcoded; no vendor billing API.
# ponytail: grok-4.5 ≥200k context is $4/$12; we only have prompt_tokens, no
# position, and shipped fallbacks stay well under 200k — bill the short rate.
# ponytail: Claude cache_creation is unsplit 5m vs 1h ($2.50 vs $4); price at 5m.
_RATES: dict[str, dict[str, float]] = {
    "grok-4.5": {
        "input": 2.00,
        "output": 6.00,
        "cached_input": 0.30,
    },
    "claude-sonnet-5": {
        "input": 2.00,
        "output": 10.00,
        "cache_creation": 2.50,  # Anthropic 5m cache write (list 2026-08-16)
        "cache_read": 0.20,  # Anthropic cache hit (list 2026-08-16)
    },
}

_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
)

_EMPTY_TOKENS: dict[str, int | None] = dict.fromkeys(_TOKEN_KEYS)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _empty_tokens() -> dict[str, int | None]:
    return dict(_EMPTY_TOKENS)


def parse_grok_usage(usage: object) -> dict[str, int | None]:
    """Map xAI Chat Completions ``usage`` to ledger token fields."""
    tokens = _empty_tokens()
    if not isinstance(usage, Mapping):
        return tokens
    tokens["input_tokens"] = _as_int(usage.get("prompt_tokens"))
    tokens["output_tokens"] = _as_int(usage.get("completion_tokens"))
    details = usage.get("prompt_tokens_details")
    if isinstance(details, Mapping):
        tokens["cached_input_tokens"] = _as_int(details.get("cached_tokens"))
    return tokens


def parse_claude_usage(usage: object) -> dict[str, int | None]:
    """Map Anthropic Messages ``usage`` to ledger token fields."""
    tokens = _empty_tokens()
    if not isinstance(usage, Mapping):
        return tokens
    tokens["input_tokens"] = _as_int(usage.get("input_tokens"))
    tokens["output_tokens"] = _as_int(usage.get("output_tokens"))
    tokens["cache_creation_input_tokens"] = _as_int(usage.get("cache_creation_input_tokens"))
    tokens["cache_read_input_tokens"] = _as_int(usage.get("cache_read_input_tokens"))
    return tokens


def _tokens_for_provider(provider: str, usage: object | None) -> dict[str, int | None]:
    if provider == "claude":
        return parse_claude_usage(usage)
    return parse_grok_usage(usage)


def _usage_is_missing(usage: object | None, tokens: dict[str, int | None]) -> bool:
    if usage is None or not isinstance(usage, Mapping):
        return True
    return tokens["input_tokens"] is None and tokens["output_tokens"] is None


def _resolve_spend_path(spend_file: Path | None) -> Path:
    if spend_file is not None:
        path = Path(spend_file)
        return path if path.is_absolute() else _anchor(str(path))
    try:
        cfg = _get_config()
        raw = cfg.get("logging", {}).get("spend_file") or _DEFAULT_SPEND_FILE
    except (OSError, TypeError, ValueError, KeyError):
        raw = _DEFAULT_SPEND_FILE
    return _anchor(str(raw))


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def _normalize_provider(provider: str) -> str:
    if isinstance(provider, str):
        normalized = provider.strip().lower()
        if normalized:
            return normalized
    return "unknown"


def record_external_usage(
    *,
    provider: str,
    model: str,
    usage: object | None,
    spend_file: Path | None = None,
) -> None:
    """Append one JSON line. Write failures log WARNING and do not raise."""
    normalized = _normalize_provider(provider)
    tokens = _tokens_for_provider(normalized, usage)
    record: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "provider": normalized,
        "model": model,
        **tokens,
        "usage_missing": _usage_is_missing(usage, tokens),
    }
    line = json.dumps(record) + "\n"
    path = _resolve_spend_path(spend_file)
    try:
        with _SPEND_WRITE_LOCK:
            _append_line(path, line)
    except OSError as exc:
        logger.warning("spend write failed for %s: %s", path, exc)


def _token_count(tokens: Mapping[str, object], key: str) -> int:
    value = tokens.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def estimate_usd(model: str, tokens: Mapping[str, object]) -> dict[str, float | bool | str | None]:
    """Dollars at read time only. Unknown model → usd None, rate_unknown true."""
    rates = _RATES.get(model)
    if rates is None:
        return {"usd": None, "rate_unknown": True, "priced_as_of": PRICED_AS_OF}

    output = _token_count(tokens, "output_tokens")
    output_usd = output * rates["output"] / 1_000_000

    if "cached_input" in rates:
        cached = _token_count(tokens, "cached_input_tokens")
        billed_in = _token_count(tokens, "input_tokens")
        uncached = max(billed_in - cached, 0)
        usd = (
            uncached * rates["input"] / 1_000_000
            + cached * rates["cached_input"] / 1_000_000
            + output_usd
        )
    else:
        usd = (
            _token_count(tokens, "input_tokens") * rates["input"] / 1_000_000
            + _token_count(tokens, "cache_creation_input_tokens") * rates["cache_creation"] / 1_000_000
            + _token_count(tokens, "cache_read_input_tokens") * rates["cache_read"] / 1_000_000
            + output_usd
        )
    return {"usd": usd, "rate_unknown": False, "priced_as_of": PRICED_AS_OF}
