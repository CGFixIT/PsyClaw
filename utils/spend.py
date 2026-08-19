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
PRICED_AS_OF = "2026-08-19"

# xAI Chat Completions: 100_000_000 ticks per USD cent → 10_000_000_000 ticks / $1.
# https://docs.x.ai/developers/rest-api-reference/inference/chat (verified 2026-08-19)
TICKS_PER_USD = 10_000_000_000

# USD per 1M tokens. Hardcoded; no vendor billing API.
# Rates: https://docs.x.ai/developers/pricing and
# https://platform.claude.com/docs/en/about-claude/pricing (verified 2026-08-19).
# grok-4.5 ≥200k prompt bills the long-context band for ALL tokens in the request.
# Claude cache writes split 5m vs 1h when usage.cache_creation is present.
_RATES: dict[str, dict[str, float]] = {
    "grok-4.5": {
        "input": 2.00,
        "output": 6.00,
        "cached_input": 0.30,
        "long_input": 4.00,
        "long_cached_input": 0.60,
        "long_output": 12.00,
        "long_prompt_threshold": 200_000.0,
    },
    "claude-sonnet-5": {
        "input": 2.00,
        "output": 10.00,
        "cache_creation": 2.50,  # 5m cache write
        "cache_creation_1h": 4.00,
        "cache_read": 0.20,
    },
}

_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "cache_creation_5m_tokens",
    "cache_creation_1h_tokens",
    "reasoning_tokens",
    "vendor_cost_ticks",
)

_EMPTY_TOKENS: dict[str, int | None] = dict.fromkeys(_TOKEN_KEYS)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def _empty_tokens() -> dict[str, int | None]:
    return dict(_EMPTY_TOKENS)


def parse_grok_usage(usage: object) -> dict[str, int | None]:
    """Map xAI Chat Completions ``usage`` to ledger token fields.

    ``completion_tokens`` is visible output only. Reasoning sits in
    ``completion_tokens_details.reasoning_tokens`` and is billed at the output
    rate. Prefer ``cost_in_usd_ticks`` at read time when present.
    """
    tokens = _empty_tokens()
    if not isinstance(usage, Mapping):
        return tokens
    tokens["input_tokens"] = _as_int(usage.get("prompt_tokens"))
    tokens["output_tokens"] = _as_int(usage.get("completion_tokens"))
    details = usage.get("prompt_tokens_details")
    if isinstance(details, Mapping):
        tokens["cached_input_tokens"] = _as_int(details.get("cached_tokens"))
    completion_details = usage.get("completion_tokens_details")
    if isinstance(completion_details, Mapping):
        tokens["reasoning_tokens"] = _as_int(completion_details.get("reasoning_tokens"))
    tokens["vendor_cost_ticks"] = _as_int(usage.get("cost_in_usd_ticks"))
    return tokens


def parse_claude_usage(usage: object) -> dict[str, int | None]:
    """Map Anthropic Messages ``usage`` to ledger token fields.

    ``output_tokens`` is the inclusive billing total. Cache writes split by TTL
    when ``cache_creation`` is present; otherwise price the unsplit write at 5m.
    """
    tokens = _empty_tokens()
    if not isinstance(usage, Mapping):
        return tokens
    tokens["input_tokens"] = _as_int(usage.get("input_tokens"))
    tokens["output_tokens"] = _as_int(usage.get("output_tokens"))
    tokens["cache_creation_input_tokens"] = _as_int(usage.get("cache_creation_input_tokens"))
    tokens["cache_read_input_tokens"] = _as_int(usage.get("cache_read_input_tokens"))
    creation = usage.get("cache_creation")
    if isinstance(creation, Mapping):
        tokens["cache_creation_5m_tokens"] = _as_int(creation.get("ephemeral_5m_input_tokens"))
        tokens["cache_creation_1h_tokens"] = _as_int(creation.get("ephemeral_1h_input_tokens"))
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
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def billed_output_tokens(tokens: Mapping[str, object]) -> int:
    """Visible completion plus Grok reasoning tokens (Claude reasoning is already in output)."""
    return _token_count(tokens, "output_tokens") + _token_count(tokens, "reasoning_tokens")


def estimate_usd(model: str, tokens: Mapping[str, object]) -> dict[str, float | bool | str | None]:
    """Dollars at read time only. Unknown model → usd None, rate_unknown true.

    Prefer xAI ``cost_in_usd_ticks`` when present; otherwise the dated rate table.
    """
    ticks = tokens.get("vendor_cost_ticks")
    if _is_int(ticks) and ticks >= 0:
        return {
            "usd": ticks / TICKS_PER_USD,
            "rate_unknown": False,
            "priced_as_of": PRICED_AS_OF,
            "usd_source": "vendor_ticks",
        }

    rates = _RATES.get(model)
    if rates is None:
        return {
            "usd": None,
            "rate_unknown": True,
            "priced_as_of": PRICED_AS_OF,
            "usd_source": None,
        }

    billed_out = billed_output_tokens(tokens)

    if "cached_input" in rates:
        prompt = _token_count(tokens, "input_tokens")
        threshold = rates.get("long_prompt_threshold")
        use_long = isinstance(threshold, (int, float)) and not isinstance(threshold, bool) and prompt >= threshold
        input_rate = rates["long_input"] if use_long else rates["input"]
        cached_rate = rates["long_cached_input"] if use_long else rates["cached_input"]
        output_rate = rates["long_output"] if use_long else rates["output"]
        cached = _token_count(tokens, "cached_input_tokens")
        uncached = max(prompt - cached, 0)
        usd = (
            uncached * input_rate / 1_000_000
            + cached * cached_rate / 1_000_000
            + billed_out * output_rate / 1_000_000
        )
    else:
        has_ttl_split = _is_int(tokens.get("cache_creation_5m_tokens")) or _is_int(
            tokens.get("cache_creation_1h_tokens")
        )
        if has_ttl_split:
            cache_write_usd = (
                _token_count(tokens, "cache_creation_5m_tokens") * rates["cache_creation"] / 1_000_000
                + _token_count(tokens, "cache_creation_1h_tokens") * rates["cache_creation_1h"] / 1_000_000
            )
        else:
            cache_write_usd = (
                _token_count(tokens, "cache_creation_input_tokens") * rates["cache_creation"] / 1_000_000
            )
        usd = (
            _token_count(tokens, "input_tokens") * rates["input"] / 1_000_000
            + cache_write_usd
            + _token_count(tokens, "cache_read_input_tokens") * rates["cache_read"] / 1_000_000
            + billed_out * rates["output"] / 1_000_000
        )
    return {
        "usd": usd,
        "rate_unknown": False,
        "priced_as_of": PRICED_AS_OF,
        "usd_source": "rate_table",
    }
