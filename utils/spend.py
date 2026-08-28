"""Append-only Grok/Claude token ledger. Dollars are derived at read time.

Not routed through ``audit_log`` — spend.jsonl is a separate stream so
metrics can reprice history without polluting the audit trail. Never persist
query/prompt/content/messages or credentials.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Mapping
from datetime import UTC, date, datetime
from functools import lru_cache
from pathlib import Path

from utils.logger import _anchor, _get_config

logger = logging.getLogger("cyclaw.spend")

_SPEND_WRITE_LOCK = threading.Lock()
_DEFAULT_SPEND_FILE = "logs/spend.jsonl"
# The OLDEST "verified" date across the rate rows below -- deliberately the
# oldest, not the most recent. rates_are_stale() alarms off this value, so
# taking the minimum means the alarm tracks the stalest rate in the table.
# Bumping it when only SOME rows are re-verified would mask the rest going
# stale, which is the failure this constant exists to catch: raise it only
# once EVERY row has been re-checked. tests/test_spend.py pins the invariant
# against the dates in the comments below.
PRICED_AS_OF = "2026-08-19"
STALE_AFTER_DAYS = 30

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
    # config.yaml's grok.model comment explicitly invites pinning grok-4.3 for
    # cost/window; without this row every such line priced as rate_unknown.
    # $1.25/$2.50 matches that comment; cached + ≥200k long band from
    # https://docs.x.ai/developers/pricing (verified 2026-08-27).
    "grok-4.3": {
        "input": 1.25,
        "output": 2.50,
        "cached_input": 0.20,
        "long_input": 2.50,
        "long_cached_input": 0.40,
        "long_output": 5.00,
        "long_prompt_threshold": 200_000.0,
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

_TOKEN_COUNT_KEYS = tuple(key for key in _TOKEN_KEYS if key != "vendor_cost_ticks")

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
    return tokens["input_tokens"] is None or tokens["output_tokens"] is None


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


_ALLOWED_SOURCES = frozenset({"query", "agentic", "eval"})
_QUERY_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_ROUTE_TOKEN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_MAX_ROUTE_HOPS = 16


def _normalize_provider(provider: str) -> str:
    if isinstance(provider, str):
        normalized = provider.strip().lower()
        if normalized:
            return normalized
    return "unknown"


def _normalize_source(source: str) -> str:
    if isinstance(source, str):
        normalized = source.strip().lower()
        if normalized in _ALLOWED_SOURCES:
            return normalized
    return "unknown"


def _normalized_query_hash(value: object) -> str | None:
    if isinstance(value, str) and _QUERY_HASH_RE.fullmatch(value):
        return value
    return None


def _normalized_route_path(value: object) -> list[str] | None:
    if not isinstance(value, list) or not value or len(value) > _MAX_ROUTE_HOPS:
        return None
    hops: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _ROUTE_TOKEN_RE.fullmatch(item):
            return None
        hops.append(item)
    return hops


def record_external_usage(
    *,
    provider: str,
    model: str,
    usage: object | None,
    spend_file: Path | None = None,
    source: str = "query",
    query_hash: str | None = None,
    route_path: list[str] | None = None,
    served_model: str | None = None,
) -> None:
    """Append one JSON line. Write failures log WARNING and do not raise.

    ``model`` is the configured tag sent in the request; ``served_model`` is the
    vendor-resolved id echoed back in the response. Both are kept because an
    unpinned alias (e.g. ``claude-sonnet-5``) can be re-pointed upstream, and
    the ledger must show what actually served/billed alongside what was asked.
    """
    _warn_once_if_stale()
    normalized = _normalize_provider(provider)
    tokens = _tokens_for_provider(normalized, usage)
    record: dict[str, object] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "provider": normalized,
        "model": model,
        **tokens,
        "usage_missing": _usage_is_missing(usage, tokens),
        "source": _normalize_source(source),
    }
    hashed = _normalized_query_hash(query_hash)
    if hashed is not None:
        record["query_hash"] = hashed
    hops = _normalized_route_path(route_path)
    if hops is not None:
        record["route_path"] = hops
    # Additive optional field like query_hash/route_path: absent when the
    # response carried no usable model id, so old readers see no shape change.
    if isinstance(served_model, str) and served_model.strip():
        record["served_model"] = served_model
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


def _token_fields_missing(tokens: Mapping[str, object]) -> bool:
    """True when every token count is absent (None), not when counts are zero."""
    return all(tokens.get(key) is None for key in _TOKEN_COUNT_KEYS)


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def billed_output_tokens(tokens: Mapping[str, object]) -> int:
    """Visible completion plus Grok reasoning tokens (Claude reasoning is already in output)."""
    return _token_count(tokens, "output_tokens") + _token_count(tokens, "reasoning_tokens")


def rates_are_stale(now: datetime | None = None) -> bool:
    """True when ``PRICED_AS_OF`` is more than ``STALE_AFTER_DAYS`` behind ``now``."""
    try:
        priced = date.fromisoformat(PRICED_AS_OF)
    except ValueError:
        return True
    if now is None:
        current = datetime.now(UTC)
    elif now.tzinfo is None:
        current = now.replace(tzinfo=UTC)
    else:
        current = now.astimezone(UTC)
    return (current.date() - priced).days > STALE_AFTER_DAYS


@lru_cache(maxsize=1)
def _warn_once_if_stale() -> None:
    """Emit the stale-rate warning at most once per process.

    record_external_usage() prices live Grok/Claude calls; before this, a server
    billing against a >STALE_AFTER_DAYS table emitted no signal at all until an
    operator separately ran metrics.py. metrics.py still warns on every run --
    it is an operator-invoked report -- but the server records a line per
    external call, and a per-call warning there is spam that gets filtered out
    exactly when it matters.

    lru_cache is the latch (a nullary cached call runs its body once), which
    keeps the state out of a module global and gives tests a public reset:
    ``_warn_once_if_stale.cache_clear()``. A thread race can emit one duplicate
    line, never a missed one.
    """
    warn_if_priced_as_of_stale()


def warn_if_priced_as_of_stale(now: datetime | None = None) -> bool:
    stale = rates_are_stale(now)
    if stale:
        logger.warning(
            "spend rate table priced_as_of %s is older than %s days",
            PRICED_AS_OF,
            STALE_AFTER_DAYS,
        )
    return stale


def estimate_usd(model: str, tokens: Mapping[str, object]) -> dict[str, float | bool | str | None]:
    """Dollars at read time only. Unknown model → usd None, rate_unknown true.

    Prefer xAI ``cost_in_usd_ticks`` when present; otherwise the dated rate table.
    All token counts None (not zero) → usd None with ``usd_source`` incomplete.
    """
    ticks = tokens.get("vendor_cost_ticks")
    if _is_int(ticks) and ticks >= 0:
        return {
            "usd": ticks / TICKS_PER_USD,
            "rate_unknown": False,
            "priced_as_of": PRICED_AS_OF,
            "usd_source": "vendor_ticks",
        }

    if _token_fields_missing(tokens):
        return {
            "usd": None,
            "rate_unknown": False,
            "priced_as_of": PRICED_AS_OF,
            "usd_source": "incomplete",
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
            split_5m = _token_count(tokens, "cache_creation_5m_tokens")
            split_1h = _token_count(tokens, "cache_creation_1h_tokens")
            total_write = _token_count(tokens, "cache_creation_input_tokens")
            residual = max(total_write - split_5m - split_1h, 0)
            cache_write_usd = (
                split_5m * rates["cache_creation"] / 1_000_000
                + split_1h * rates["cache_creation_1h"] / 1_000_000
                + residual * rates["cache_creation"] / 1_000_000
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


def compare_vendor_cost(model: str, tokens: Mapping[str, object]) -> dict[str, float | bool | str | None]:
    """Rate-table USD vs xAI ``cost_in_usd_ticks`` when the ledger has ticks.

    Claude has no vendor dollar field — ``vendor_usd`` is then None.
    """
    table_tokens = dict(tokens)
    table_tokens["vendor_cost_ticks"] = None
    table = estimate_usd(model, table_tokens)
    ticks = tokens.get("vendor_cost_ticks")
    vendor_usd: float | None = None
    if _is_int(ticks) and ticks >= 0:
        vendor_usd = ticks / TICKS_PER_USD
    table_usd = table["usd"]
    delta: float | None = None
    if vendor_usd is not None and isinstance(table_usd, (int, float)) and not isinstance(table_usd, bool):
        delta = float(table_usd) - vendor_usd
    return {
        "table_usd": table_usd,
        "vendor_usd": vendor_usd,
        "delta_usd": delta,
        "rate_unknown": table["rate_unknown"],
        "priced_as_of": table["priced_as_of"],
    }


DELTA_REL_FAIL = 0.05
DELTA_ABS_FLOOR = 1e-8
DELTA_ABS_CAP = 0.01


def ticks_mismatch(delta_usd: object, vendor_usd: object) -> bool:
    """True when rate-table USD disagrees with vendor ticks beyond the live-probe gate.

    Relative 5% when ``vendor_usd > 0``, ignoring sub-tick dust under
    ``DELTA_ABS_FLOOR``. Absolute ``DELTA_ABS_CAP`` still trips catastrophic misses.
    """
    if isinstance(delta_usd, bool) or not isinstance(delta_usd, (int, float)):
        return False
    if isinstance(vendor_usd, bool) or not isinstance(vendor_usd, (int, float)):
        return False
    abs_delta = abs(float(delta_usd))
    vendor = float(vendor_usd)
    if abs_delta <= DELTA_ABS_FLOOR:
        return False
    if abs_delta > DELTA_ABS_CAP:
        return True
    return vendor > 0 and abs_delta / vendor > DELTA_REL_FAIL
