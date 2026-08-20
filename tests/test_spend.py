"""Unit tests for the append-only Grok/Claude spend ledger."""

from __future__ import annotations

import inspect
import json
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


def test_record_source_agentic_and_unknown(tmp_path: Path) -> None:
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
        source="not-a-plane",
    )
    first, second = (json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines())
    assert first["source"] == "agentic"
    assert second["source"] == "unknown"


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
