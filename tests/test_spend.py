"""Unit tests for the append-only Grok/Claude spend ledger."""

from __future__ import annotations

import inspect
import json
import threading
import time
from datetime import date
from pathlib import Path

import pytest

from utils import spend

GROK_USAGE = {
    "prompt_tokens": 41,
    "completion_tokens": 104,
    "total_tokens": 145,
    "prompt_tokens_details": {"cached_tokens": 0, "text_tokens": 41},
}

# Official xAI Chat Completions example (docs 2026-08-19): reasoning is
# outside completion_tokens (9 + 94 = 103 billed output; total_tokens 135).
GROK_REASONING_USAGE = {
    "prompt_tokens": 32,
    "completion_tokens": 9,
    "total_tokens": 135,
    "prompt_tokens_details": {
        "text_tokens": 32,
        "audio_tokens": 0,
        "image_tokens": 0,
        "cached_tokens": 6,
    },
    "completion_tokens_details": {
        "reasoning_tokens": 94,
        "audio_tokens": 0,
        "accepted_prediction_tokens": 0,
        "rejected_prediction_tokens": 0,
    },
}

CLAUDE_USAGE = {
    "input_tokens": 2095,
    "output_tokens": 503,
    "cache_creation_input_tokens": 2051,
    "cache_read_input_tokens": 2051,
}

CLAUDE_TTL_SPLIT_USAGE = {
    "input_tokens": 2048,
    "cache_read_input_tokens": 1800,
    "cache_creation_input_tokens": 248,
    "output_tokens": 503,
    "cache_creation": {
        "ephemeral_5m_input_tokens": 148,
        "ephemeral_1h_input_tokens": 100,
    },
}

_FORBIDDEN = frozenset({"query", "prompt", "content", "messages", "api_key", "authorization"})


def test_parse_grok_usage_maps_ints() -> None:
    parsed = spend.parse_grok_usage(GROK_USAGE)
    assert parsed["input_tokens"] == 41
    assert parsed["output_tokens"] == 104
    assert parsed["cached_input_tokens"] == 0
    assert parsed["cache_creation_input_tokens"] is None
    assert parsed["cache_read_input_tokens"] is None
    assert parsed["reasoning_tokens"] is None
    assert parsed["vendor_cost_ticks"] is None


def test_parse_grok_usage_maps_reasoning_and_ticks() -> None:
    parsed = spend.parse_grok_usage({**GROK_REASONING_USAGE, "cost_in_usd_ticks": 123456789})
    assert parsed["input_tokens"] == 32
    assert parsed["output_tokens"] == 9
    assert parsed["cached_input_tokens"] == 6
    assert parsed["reasoning_tokens"] == 94
    assert parsed["vendor_cost_ticks"] == 123456789


def test_as_int_rejects_negatives() -> None:
    parsed = spend.parse_grok_usage(
        {
            "prompt_tokens": -1,
            "completion_tokens": 4,
            "completion_tokens_details": {"reasoning_tokens": -8},
            "cost_in_usd_ticks": -3,
        }
    )
    assert parsed["input_tokens"] is None
    assert parsed["output_tokens"] == 4
    assert parsed["reasoning_tokens"] is None
    assert parsed["vendor_cost_ticks"] is None


def test_parse_claude_usage_maps_ints_and_cache() -> None:
    parsed = spend.parse_claude_usage(CLAUDE_USAGE)
    assert parsed["input_tokens"] == 2095
    assert parsed["output_tokens"] == 503
    assert parsed["cache_creation_input_tokens"] == 2051
    assert parsed["cache_read_input_tokens"] == 2051
    assert parsed["cached_input_tokens"] is None
    assert parsed["cache_creation_5m_tokens"] is None
    assert parsed["cache_creation_1h_tokens"] is None


def test_parse_claude_usage_maps_cache_ttl_split() -> None:
    parsed = spend.parse_claude_usage(CLAUDE_TTL_SPLIT_USAGE)
    assert parsed["cache_creation_input_tokens"] == 248
    assert parsed["cache_creation_5m_tokens"] == 148
    assert parsed["cache_creation_1h_tokens"] == 100
    assert parsed["cache_read_input_tokens"] == 1800


def test_record_missing_usage_sets_usage_missing(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage=None,
        spend_file=ledger,
    )
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert record["usage_missing"] is True
    assert record["input_tokens"] is None
    assert record["output_tokens"] is None
    assert record["source"] == "query"


def test_record_outcome_is_additive_and_absent_by_default(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(provider="grok", model="grok-4.5", usage=None, spend_file=ledger)
    spend.record_external_usage(
        provider="grok", model="grok-4.5", usage=None, spend_file=ledger, outcome="failed_after_billing",
    )
    plain, marked = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines())
    assert "outcome" not in plain
    assert marked["outcome"] == "failed_after_billing"


def test_record_partial_usage_sets_usage_missing(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage={"prompt_tokens": 41},
        spend_file=ledger,
    )
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert record["usage_missing"] is True
    assert record["input_tokens"] == 41
    assert record["output_tokens"] is None
    priced = spend.estimate_usd("grok-4.5", spend.parse_grok_usage({"prompt_tokens": 41}))
    assert priced["usd"] == pytest.approx(41 * 2.00 / 1_000_000)
    assert priced["usd_source"] == "rate_table"


def test_record_malformed_usage_sets_usage_missing(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="claude",
        model="claude-sonnet-5",
        usage="not-a-mapping",
        spend_file=ledger,
    )
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert record["usage_missing"] is True
    assert record["input_tokens"] is None


def test_two_records_grow_file_by_two_lines(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage=GROK_USAGE,
        spend_file=ledger,
    )
    spend.record_external_usage(
        provider="claude",
        model="claude-sonnet-5",
        usage=CLAUDE_USAGE,
        spend_file=ledger,
    )
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first, second = (json.loads(line) for line in lines)
    assert first["provider"] == "grok"
    assert first["input_tokens"] == 41
    assert second["provider"] == "claude"
    assert second["cache_read_input_tokens"] == 2051


def test_record_persists_reasoning_ticks_and_cache_split(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage={**GROK_REASONING_USAGE, "cost_in_usd_ticks": 50},
        spend_file=ledger,
    )
    spend.record_external_usage(
        provider="claude",
        model="claude-sonnet-5",
        usage=CLAUDE_TTL_SPLIT_USAGE,
        spend_file=ledger,
    )
    grok_line, claude_line = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines())
    assert grok_line["reasoning_tokens"] == 94
    assert grok_line["vendor_cost_ticks"] == 50
    assert "usd" not in grok_line
    assert claude_line["cache_creation_5m_tokens"] == 148
    assert claude_line["cache_creation_1h_tokens"] == 100


def test_written_json_has_no_forbidden_keys(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage={**GROK_USAGE, "query": "secret", "prompt": "nope"},
        spend_file=ledger,
    )
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert _FORBIDDEN.isdisjoint(record)
    # `source` may be the plane name "query"; scan values other than that field.
    for field, value in record.items():
        if field == "source":
            continue
        dumped = json.dumps(value)
        for forbidden in _FORBIDDEN:
            assert forbidden not in dumped


def test_record_query_hash_and_route_path(tmp_path: Path) -> None:
    from utils.logger import hash_query

    ledger = tmp_path / "spend.jsonl"
    hashed = hash_query("what is RRF?")
    path = [
        "retrieve",
        "route_by_score",
        "user_gate",
        "pre_action_hook_grok",
        "grok_fallback",
    ]
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage=GROK_USAGE,
        spend_file=ledger,
        query_hash=hashed,
        route_path=path,
    )
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert record["query_hash"] == hashed
    assert record["route_path"] == path
    assert "query" not in record


def test_record_omits_invalid_query_hash_and_route_path(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage=GROK_USAGE,
        spend_file=ledger,
        query_hash="not-a-sha256",
        route_path=["retrieve", "not valid", "grok_fallback"],
    )
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert "query_hash" not in record
    assert "route_path" not in record
    assert record["input_tokens"] == GROK_USAGE["prompt_tokens"]


def test_record_source_agentic_eval_and_unknown(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage=GROK_USAGE,
        spend_file=ledger,
        source="agentic",
    )
    spend.record_external_usage(
        provider="claude",
        model="claude-sonnet-5",
        usage=CLAUDE_USAGE,
        spend_file=ledger,
        source="eval",
    )
    spend.record_external_usage(
        provider="claude",
        model="claude-sonnet-5",
        usage=CLAUDE_USAGE,
        spend_file=ledger,
        source="not-a-plane",
    )
    first, second, third = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines())
    assert first["source"] == "agentic"
    assert second["source"] == "eval"
    assert third["source"] == "unknown"


def test_estimate_usd_known_models() -> None:
    grok = spend.estimate_usd("grok-4.5", spend.parse_grok_usage(GROK_USAGE))
    assert grok["rate_unknown"] is False
    assert grok["usd"] == pytest.approx(41 * 2.00 / 1_000_000 + 104 * 6.00 / 1_000_000)
    assert grok["priced_as_of"] == spend.PRICED_AS_OF
    assert grok["usd_source"] == "rate_table"

    claude = spend.estimate_usd("claude-sonnet-5", spend.parse_claude_usage(CLAUDE_USAGE))
    assert claude["rate_unknown"] is False
    assert claude["usd"] == pytest.approx(
        2095 * 2.00 / 1_000_000
        + 503 * 10.00 / 1_000_000
        + 2051 * 2.50 / 1_000_000
        + 2051 * 0.20 / 1_000_000
    )


def test_estimate_usd_grok_reasoning_tokens_bill_as_output() -> None:
    parsed = spend.parse_grok_usage(GROK_REASONING_USAGE)
    priced = spend.estimate_usd("grok-4.5", parsed)
    uncached = 32 - 6
    billed_out = 9 + 94
    assert spend.billed_output_tokens(parsed) == billed_out
    assert priced["usd"] == pytest.approx(
        uncached * 2.00 / 1_000_000 + 6 * 0.30 / 1_000_000 + billed_out * 6.00 / 1_000_000
    )
    assert priced["usd_source"] == "rate_table"


def test_estimate_usd_prefers_vendor_ticks() -> None:
    parsed = spend.parse_grok_usage({**GROK_REASONING_USAGE, "cost_in_usd_ticks": spend.TICKS_PER_USD})
    priced = spend.estimate_usd("grok-4.5", parsed)
    assert priced["usd"] == pytest.approx(1.0)
    assert priced["usd_source"] == "vendor_ticks"
    assert priced["rate_unknown"] is False


def test_compare_vendor_cost_splits_table_and_ticks() -> None:
    parsed = spend.parse_grok_usage({**GROK_REASONING_USAGE, "cost_in_usd_ticks": spend.TICKS_PER_USD})
    compared = spend.compare_vendor_cost("grok-4.5", parsed)
    table = spend.estimate_usd("grok-4.5", spend.parse_grok_usage(GROK_REASONING_USAGE))
    assert compared["vendor_usd"] == pytest.approx(1.0)
    assert compared["table_usd"] == pytest.approx(table["usd"])
    assert compared["delta_usd"] == pytest.approx(table["usd"] - 1.0)
    assert table["usd_source"] == "rate_table"


def test_ticks_mismatch_relative_floor_and_cap() -> None:
    assert spend.ticks_mismatch(1e-9, 0.0001) is False
    assert spend.ticks_mismatch(0.00002, 0.0001) is True
    assert spend.ticks_mismatch(0.004, 0.1) is False
    assert spend.ticks_mismatch(0.02, 1.0) is True
    assert spend.ticks_mismatch(None, 1.0) is False
    assert spend.ticks_mismatch(0.1, None) is False
    assert spend.ticks_mismatch(0.1, True) is False


def test_compare_vendor_cost_claude_has_no_ticks() -> None:
    parsed = spend.parse_claude_usage(CLAUDE_USAGE)
    compared = spend.compare_vendor_cost("claude-sonnet-5", parsed)
    assert compared["vendor_usd"] is None
    assert compared["delta_usd"] is None
    assert compared["table_usd"] == pytest.approx(
        spend.estimate_usd("claude-sonnet-5", parsed)["usd"]
    )


def test_estimate_usd_grok_long_context_band() -> None:
    tokens = {
        "input_tokens": 200_000,
        "output_tokens": 10,
        "cached_input_tokens": 1_000,
        "reasoning_tokens": 5,
    }
    priced = spend.estimate_usd("grok-4.5", tokens)
    uncached = 199_000
    billed_out = 15
    assert priced["usd"] == pytest.approx(
        uncached * 4.00 / 1_000_000 + 1_000 * 0.60 / 1_000_000 + billed_out * 12.00 / 1_000_000
    )


def test_estimate_usd_claude_cache_ttl_split() -> None:
    parsed = spend.parse_claude_usage(CLAUDE_TTL_SPLIT_USAGE)
    priced = spend.estimate_usd("claude-sonnet-5", parsed)
    assert priced["usd"] == pytest.approx(
        2048 * 2.00 / 1_000_000
        + 148 * 2.50 / 1_000_000
        + 100 * 4.00 / 1_000_000
        + 1800 * 0.20 / 1_000_000
        + 503 * 10.00 / 1_000_000
    )


def test_estimate_usd_claude_cache_ttl_residual_at_5m_rate() -> None:
    parsed = spend.parse_claude_usage(
        {
            "input_tokens": 2048,
            "output_tokens": 503,
            "cache_creation_input_tokens": 300,
            "cache_read_input_tokens": 1800,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 148,
                "ephemeral_1h_input_tokens": 100,
            },
        }
    )
    priced = spend.estimate_usd("claude-sonnet-5", parsed)
    residual = 300 - 148 - 100
    assert priced["usd"] == pytest.approx(
        2048 * 2.00 / 1_000_000
        + 148 * 2.50 / 1_000_000
        + 100 * 4.00 / 1_000_000
        + residual * 2.50 / 1_000_000
        + 1800 * 0.20 / 1_000_000
        + 503 * 10.00 / 1_000_000
    )


def test_rates_are_stale_after_thirty_days() -> None:
    from datetime import UTC, datetime

    assert spend.rates_are_stale(datetime(2026, 8, 20, tzinfo=UTC)) is False
    assert spend.rates_are_stale(datetime(2026, 9, 19, tzinfo=UTC)) is True
    assert spend.rates_are_stale(datetime(2026, 9, 19)) is True  # naive → UTC


def test_rates_are_stale_when_priced_as_of_unparseable(monkeypatch) -> None:
    monkeypatch.setattr(spend, "PRICED_AS_OF", "not-a-date")
    assert spend.rates_are_stale() is True


def test_resolve_spend_path_falls_back_when_config_unreadable(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(spend, "_get_config", lambda: (_ for _ in ()).throw(OSError("gone")))
    path = spend._resolve_spend_path(None)
    assert path.name == "spend.jsonl"


def test_resolve_spend_path_reads_config_spend_file(monkeypatch) -> None:
    monkeypatch.setattr(
        spend,
        "_get_config",
        lambda: {"logging": {"spend_file": "logs/custom_spend.jsonl"}},
    )
    path = spend._resolve_spend_path(None)
    assert path.name == "custom_spend.jsonl"


def test_normalize_provider_unknown_for_blank() -> None:
    assert spend._normalize_provider("") == "unknown"
    assert spend._normalize_provider("  ") == "unknown"
    assert spend._normalize_provider(None) == "unknown"  # type: ignore[arg-type]


def test_estimate_usd_unknown_model_usd_none() -> None:
    tokens = spend.parse_grok_usage(GROK_USAGE)
    result = spend.estimate_usd("not-a-real-model", tokens)
    assert result["usd"] is None
    assert result["rate_unknown"] is True
    assert tokens["input_tokens"] == 41


def test_estimate_usd_all_none_tokens_is_incomplete() -> None:
    result = spend.estimate_usd("grok-4.5", spend.parse_grok_usage(None))
    assert result["usd"] is None
    assert result["rate_unknown"] is False
    assert result["usd_source"] == "incomplete"


def test_estimate_usd_all_zero_tokens_is_zero_dollars() -> None:
    result = spend.estimate_usd(
        "grok-4.5",
        spend.parse_grok_usage({"prompt_tokens": 0, "completion_tokens": 0}),
    )
    assert result["usd"] == 0
    assert result["rate_unknown"] is False
    assert result["usd_source"] == "rate_table"


def test_estimate_usd_ticks_win_when_tokens_missing() -> None:
    tokens = spend.parse_grok_usage({"cost_in_usd_ticks": spend.TICKS_PER_USD})
    result = spend.estimate_usd("grok-4.5", tokens)
    assert result["usd"] == pytest.approx(1.0)
    assert result["usd_source"] == "vendor_ticks"
    assert result["rate_unknown"] is False


def test_spend_write_error_is_swallowed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    ledger = tmp_path / "spend.jsonl"

    def _boom(_path: Path, _line: str) -> None:
        raise OSError("simulated disk full")

    monkeypatch.setattr(spend, "_append_line", _boom)
    with caplog.at_level("WARNING", logger="cyclaw.spend"):
        spend.record_external_usage(
            provider="grok",
            model="grok-4.5",
            usage=GROK_USAGE,
            spend_file=ledger,
        )
    assert "spend write failed" in caplog.text
    assert not ledger.exists()


def test_no_update_or_delete_helpers() -> None:
    names = {name for name, _ in inspect.getmembers(spend, inspect.isfunction)}
    assert not any(name.startswith(("update", "delete", "rewrite")) for name in names)


def test_live_probe_refuses_unpriced_model() -> None:
    import importlib.util

    path = Path(__file__).resolve().parent / "spend_live_probe.py"
    spec = importlib.util.spec_from_file_location("spend_live_probe_unpriced", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    with pytest.raises(SystemExit, match="rate table cannot price"):
        mod._refuse_unpriced({"rate_unknown": True, "table_usd": None}, "grok")


def test_live_probe_refuses_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib.util

    monkeypatch.delenv("CYCLAW_SPEND_LIVE", raising=False)
    path = Path(__file__).resolve().parent / "spend_live_probe.py"
    spec = importlib.util.spec_from_file_location("spend_live_probe", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main() == 2


def test_estimate_usd_prices_config_endorsed_grok43() -> None:
    """config.yaml's grok.model comment invites pinning grok-4.3 ($1.25/$2.50
    per Mtok); following that advice must not price every row rate_unknown."""
    tokens = spend.parse_grok_usage({"prompt_tokens": 100_000, "completion_tokens": 100_000})
    priced = spend.estimate_usd("grok-4.3", tokens)
    assert priced["rate_unknown"] is False
    assert priced["usd"] == pytest.approx(100_000 * 1.25 / 1_000_000 + 100_000 * 2.50 / 1_000_000)
    assert priced["usd_source"] == "rate_table"


def test_estimate_usd_grok43_long_context_band() -> None:
    """grok-4.3 bills the long band for ALL tokens once the prompt hits 200k."""
    tokens = spend.parse_grok_usage({"prompt_tokens": 200_000, "completion_tokens": 100_000})
    priced = spend.estimate_usd("grok-4.3", tokens)
    assert priced["usd"] == pytest.approx(200_000 * 2.50 / 1_000_000 + 100_000 * 5.00 / 1_000_000)


def test_estimate_usd_prices_grok46() -> None:
    """Operator-pinned grok-4.6 must not price as rate_unknown."""
    tokens = spend.parse_grok_usage({"prompt_tokens": 100_000, "completion_tokens": 100_000})
    priced = spend.estimate_usd("grok-4.6", tokens)
    assert priced["rate_unknown"] is False
    assert priced["usd"] == pytest.approx(100_000 * 2.00 / 1_000_000 + 100_000 * 6.00 / 1_000_000)
    assert priced["usd_source"] == "rate_table"


def test_estimate_usd_grok46_cached_and_long_band() -> None:
    """Official grok-4.6 cached input is $0.50, long cached $1.00, threshold 200k."""
    row = spend._RATES["grok-4.6"]
    assert row["cached_input"] == 0.50
    assert row["long_cached_input"] == 1.00
    assert row["long_input"] == 4.00
    assert row["long_output"] == 12.00
    assert row["long_prompt_threshold"] == 200_000.0
    assert spend._RATE_VERIFIED["grok-4.6"] == "2026-09-02"

    short = {
        "input_tokens": 10_000,
        "output_tokens": 0,
        "cached_input_tokens": 10_000,
        "reasoning_tokens": 0,
    }
    assert spend.estimate_usd("grok-4.6", short)["usd"] == pytest.approx(10_000 * 0.50 / 1_000_000)

    long = {
        "input_tokens": 200_000,
        "output_tokens": 100_000,
        "cached_input_tokens": 50_000,
        "reasoning_tokens": 0,
    }
    priced = spend.estimate_usd("grok-4.6", long)
    uncached = 150_000
    assert priced["usd"] == pytest.approx(
        uncached * 4.00 / 1_000_000 + 50_000 * 1.00 / 1_000_000 + 100_000 * 12.00 / 1_000_000
    )


def test_record_persists_served_model_when_given(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="claude",
        model="claude-sonnet-5",
        usage=CLAUDE_USAGE,
        spend_file=ledger,
        served_model="claude-sonnet-5-20260701",
    )
    record = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert record["model"] == "claude-sonnet-5"
    assert record["served_model"] == "claude-sonnet-5-20260701"


def test_record_omits_served_model_when_absent_or_blank(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage=GROK_USAGE,
        spend_file=ledger,
    )
    spend.record_external_usage(
        provider="grok",
        model="grok-4.5",
        usage=GROK_USAGE,
        spend_file=ledger,
        served_model="   ",
    )
    for line in ledger.read_text(encoding="utf-8").splitlines():
        assert "served_model" not in json.loads(line)


# --- rate-table provenance -----------------------------------------------------


def test_every_rate_row_carries_a_verification_date():
    """A rate row with no verification date is a silent staleness hole.

    PRICED_AS_OF is derived from the OLDEST date in _RATE_VERIFIED, so a row
    that carries no date does not lower it -- that row can go stale without
    ever tripping rates_are_stale(). Comparing the key sets (rather than
    scanning the source for "verified YYYY-MM-DD" prose, which an undated row
    simply would not match) is what makes an undated row fail here instead of
    passing quietly.
    """
    undated = sorted(set(spend._RATES) - set(spend._RATE_VERIFIED))
    orphaned = sorted(set(spend._RATE_VERIFIED) - set(spend._RATES))
    assert not undated, f"rate rows with no verification date in _RATE_VERIFIED: {undated}"
    assert not orphaned, f"_RATE_VERIFIED names models absent from _RATES: {orphaned}"
    for model, verified in spend._RATE_VERIFIED.items():
        date.fromisoformat(verified)  # raises ValueError on a malformed date
        assert verified <= date.today().isoformat(), f"{model} verified in the future: {verified}"


def test_priced_as_of_stays_derived_from_the_oldest_verified_date():
    """PRICED_AS_OF must remain computed, not re-hardcoded to a literal.

    rates_are_stale() alarms off this single constant, so a value bumped when
    only some rows were re-verified would silently mask the rest going stale --
    the exact failure the constant exists to catch. Deriving it makes that
    unfalsifiable, so what is worth pinning is the derivation: this fails if
    someone replaces the min() with a date string again, which is how the drift
    would come back.
    """
    assert spend.PRICED_AS_OF == min(spend._RATE_VERIFIED.values()), (
        f"PRICED_AS_OF ({spend.PRICED_AS_OF}) is no longer the oldest verified "
        f"rate row ({min(spend._RATE_VERIFIED.values())}) -- it has been "
        f"hardcoded away from the derivation and can now mask a stale row"
    )


def test_recording_warns_once_when_rates_are_stale(tmp_path, monkeypatch, caplog):
    """The hot path must signal a stale rate table, and only once per process.

    Before this, a server billing against a >30-day-stale table emitted nothing
    until an operator separately ran metrics.py.
    """
    spend._emit_stale_rate_warning_once.cache_clear()
    monkeypatch.setattr(spend, "rates_are_stale", lambda now=None: True)
    ledger = tmp_path / "spend.jsonl"

    with caplog.at_level("WARNING", logger="cyclaw.spend"):
        for _ in range(3):
            spend.record_external_usage(
                provider="grok", model="grok-4.5",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                spend_file=ledger,
            )

    stale_warnings = [r for r in caplog.records if "priced_as_of" in r.getMessage()]
    assert len(stale_warnings) == 1, "expected exactly one stale-rate warning across three calls"
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == 3, "recording must still happen"


def test_recording_is_silent_when_rates_are_fresh(tmp_path, monkeypatch, caplog):
    spend._emit_stale_rate_warning_once.cache_clear()
    monkeypatch.setattr(spend, "rates_are_stale", lambda now=None: False)
    ledger = tmp_path / "spend.jsonl"

    with caplog.at_level("WARNING", logger="cyclaw.spend"):
        spend.record_external_usage(
            provider="grok", model="grok-4.5",
            usage={"prompt_tokens": 1, "completion_tokens": 1},
            spend_file=ledger,
        )

    assert not [r for r in caplog.records if "priced_as_of" in r.getMessage()]


def test_staleness_is_re_evaluated_on_every_call_not_frozen_at_first(tmp_path, monkeypatch, caplog):
    """Regression: the latch must cover the WARNING, not the staleness TEST.

    A server that starts before the table goes stale evaluates "fresh" on its
    first external call. If that verdict were cached, the process would never
    warn again -- and a long-running server billing against a stale table is
    exactly the case this warning exists for. Simulates the table going stale
    while the process is up.
    """
    spend._emit_stale_rate_warning_once.cache_clear()
    ledger = tmp_path / "spend.jsonl"
    stale = {"value": False}
    monkeypatch.setattr(spend, "rates_are_stale", lambda now=None: stale["value"])

    def _record() -> None:
        spend.record_external_usage(
            provider="grok", model="grok-4.5",
            usage={"prompt_tokens": 1, "completion_tokens": 1}, spend_file=ledger,
        )

    with caplog.at_level("WARNING", logger="cyclaw.spend"):
        _record()                       # fresh at boot -- must not warn
        assert not [r for r in caplog.records if "priced_as_of" in r.getMessage()]
        stale["value"] = True           # the table ages out while the process runs
        _record()
        _record()

    warnings = [r for r in caplog.records if "priced_as_of" in r.getMessage()]
    assert len(warnings) == 1, "must warn once after going stale -- and exactly once"


# Threads for the concurrency test below, and how long the emission body is
# held open. The delay only has to outlast thread start-up, so the racing
# threads are provably inside the latch together; it does not gate the
# assertion -- see the docstring.
_RACE_THREADS = 12
_RACE_BODY_DELAY_SEC = 0.05


def test_the_stale_warning_emits_once_under_concurrent_recorders(tmp_path, monkeypatch, caplog):
    """The LOCK, not lru_cache, is what makes the emission single-shot.

    lru_cache alone does not serialize: CPython documents that a cached
    function "can be called more than once if another thread makes an
    additional call before the initial call has been completed and cached".
    gate.py runs each graph invocation through asyncio.to_thread, so concurrent
    /query requests reach record_external_usage on separate threadpool threads
    and hit this latch together -- emitting one line per racing thread at the
    one moment an operator needs the signal to be legible.

    The three sibling tests above are all single-threaded, so every one of them
    still passes with _STALE_WARNING_LOCK deleted. This is the test that does
    not.

    Two things make this deterministic rather than timing-dependent:

    - The assertion holds for any delay. The lock guarantees one emission
      however long the body takes, so this cannot flake in the passing
      direction. The delay only widens the window that the *mutation*
      direction needs, i.e. it is what makes the test non-vacuous.
    - A barrier inside the body would deadlock the correct implementation --
      the first thread would hold the lock waiting for peers the lock is
      keeping out -- so the window is widened with a delay instead.

    Exercised through record_external_usage rather than _warn_once_if_stale
    directly, because that is the real hot path and _SPEND_WRITE_LOCK cannot
    mask the race: the warn runs on the function's first line, before any
    ledger write is serialized.
    """
    spend._emit_stale_rate_warning_once.cache_clear()
    monkeypatch.setattr(spend, "rates_are_stale", lambda now=None: True)

    # Wrap, never replace: the real warn still emits the real record that
    # caplog counts below, so this measures production's emission, not a stub's.
    real_warn = spend.warn_if_priced_as_of_stale

    def _slow_warn(now=None):
        time.sleep(_RACE_BODY_DELAY_SEC)
        return real_warn(now)

    monkeypatch.setattr(spend, "warn_if_priced_as_of_stale", _slow_warn)

    ledger = tmp_path / "spend.jsonl"
    start = threading.Barrier(_RACE_THREADS)
    failures: list[Exception] = []

    def _record() -> None:
        try:
            start.wait(timeout=10)
            spend.record_external_usage(
                provider="grok", model="grok-4.5",
                usage={"prompt_tokens": 1, "completion_tokens": 1}, spend_file=ledger,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced in the main thread below
            # Exception, not BaseException: the only failures worth reporting here
            # are BrokenBarrierError (a RuntimeError) and anything record_external_usage
            # raises, both Exception subclasses. Catching BaseException would also
            # swallow KeyboardInterrupt/SystemExit in a worker thread (CodeQL
            # py/catch-base-exception).
            failures.append(exc)

    with caplog.at_level("WARNING", logger="cyclaw.spend"):
        threads = [threading.Thread(target=_record) for _ in range(_RACE_THREADS)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert not failures, f"recorder threads raised: {failures!r}"
    assert not [t for t in threads if t.is_alive()], "a recorder thread did not finish"

    warnings = [r for r in caplog.records if "priced_as_of" in r.getMessage()]
    assert len(warnings) == 1, (
        f"expected exactly 1 stale-rate warning across {_RACE_THREADS} concurrent "
        f"recorders, got {len(warnings)} -- _STALE_WARNING_LOCK is not holding"
    )
    # Every caller must still be recorded: the latch guards the warning only.
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == _RACE_THREADS
