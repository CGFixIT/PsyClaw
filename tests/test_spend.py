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

CLAUDE_USAGE = {
    "input_tokens": 2095,
    "output_tokens": 503,
    "cache_creation_input_tokens": 2051,
    "cache_read_input_tokens": 2051,
}

_FORBIDDEN = frozenset({"query", "prompt", "content", "messages", "api_key", "authorization"})


def test_parse_grok_usage_maps_ints() -> None:
    parsed = spend.parse_grok_usage(GROK_USAGE)
    assert parsed["input_tokens"] == 41
    assert parsed["output_tokens"] == 104
    assert parsed["cached_input_tokens"] == 0
    assert parsed["cache_creation_input_tokens"] is None
    assert parsed["cache_read_input_tokens"] is None


def test_parse_claude_usage_maps_ints_and_cache() -> None:
    parsed = spend.parse_claude_usage(CLAUDE_USAGE)
    assert parsed["input_tokens"] == 2095
    assert parsed["output_tokens"] == 503
    assert parsed["cache_creation_input_tokens"] == 2051
    assert parsed["cache_read_input_tokens"] == 2051
    assert parsed["cached_input_tokens"] is None


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
    dumped = json.dumps(record)
    for key in _FORBIDDEN:
        assert key not in dumped


def test_estimate_usd_known_models() -> None:
    grok = spend.estimate_usd("grok-4.5", spend.parse_grok_usage(GROK_USAGE))
    assert grok["rate_unknown"] is False
    assert grok["usd"] == pytest.approx(41 * 2.00 / 1_000_000 + 104 * 6.00 / 1_000_000)
    assert grok["priced_as_of"] == spend.PRICED_AS_OF

    claude = spend.estimate_usd("claude-sonnet-5", spend.parse_claude_usage(CLAUDE_USAGE))
    assert claude["rate_unknown"] is False
    assert claude["usd"] == pytest.approx(
        2095 * 2.00 / 1_000_000
        + 503 * 10.00 / 1_000_000
        + 2051 * 2.50 / 1_000_000
        + 2051 * 0.20 / 1_000_000
    )


def test_estimate_usd_unknown_model_usd_none() -> None:
    tokens = spend.parse_grok_usage(GROK_USAGE)
    result = spend.estimate_usd("not-a-real-model", tokens)
    assert result["usd"] is None
    assert result["rate_unknown"] is True
    assert tokens["input_tokens"] == 41


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
