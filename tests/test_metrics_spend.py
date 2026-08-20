"""Unit tests for metrics.py spend ledger reporting (read-time USD)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml

from metrics import compute_spend, iter_spend, print_metrics
from utils.spend import compare_vendor_cost, estimate_usd

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)


def _ts(days_ago: int) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def _record(
    *,
    days_ago: int,
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    usage_missing: bool = False,
    extra: dict | None = None,
) -> dict:
    row: dict = {
        "timestamp": _ts(days_ago),
        "provider": provider,
        "model": model,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cached_input_tokens": None,
        "cache_creation_input_tokens": None,
        "cache_read_input_tokens": None,
        "usage_missing": usage_missing,
    }
    if extra:
        row.update(extra)
    return row


def _write_ledger(path: Path, rows: list[object]) -> str:
    lines: list[str] = []
    for row in rows:
        lines.append(row if isinstance(row, str) else json.dumps(row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def test_iter_spend_missing_file_is_empty(tmp_path: Path) -> None:
    assert list(iter_spend(str(tmp_path / "nope.jsonl"))) == []


def test_iter_spend_skips_malformed_and_non_dict_lines(tmp_path: Path) -> None:
    ledger = tmp_path / "spend.jsonl"
    good = _record(
        days_ago=0,
        provider="grok",
        model="grok-4.5",
        input_tokens=41,
        output_tokens=104,
    )
    _write_ledger(
        ledger,
        [
            good,
            "NOT JSON",
            "null",
            "42",
            '"text"',
            "[]",
            {"provider": "claude", "model": "claude-sonnet-5", "timestamp": _ts(0)},
        ],
    )
    events = list(iter_spend(str(ledger)))
    assert len(events) == 2
    assert events[0]["provider"] == "grok"
    assert events[1]["provider"] == "claude"
    assert "usd" not in events[0]


def test_daily_and_last_7d_token_and_usd_math(tmp_path: Path) -> None:
    today_grok = _record(
        days_ago=0,
        provider="grok",
        model="grok-4.5",
        input_tokens=1_000_000,
        output_tokens=500_000,
    )
    midweek_claude = _record(
        days_ago=3,
        provider="claude",
        model="claude-sonnet-5",
        input_tokens=1_000_000,
        output_tokens=100_000,
    )
    window_edge = _record(
        days_ago=6,
        provider="grok",
        model="grok-4.5",
        input_tokens=1_000_000,
        output_tokens=0,
    )
    too_old = _record(
        days_ago=7,
        provider="grok",
        model="grok-4.5",
        input_tokens=9_000_000,
        output_tokens=9_000_000,
    )
    ledger = tmp_path / "spend.jsonl"
    events = list(iter_spend(_write_ledger(ledger, [today_grok, midweek_claude, window_edge, too_old])))
    summary = compute_spend(events, now=NOW)

    today_usd = estimate_usd("grok-4.5", today_grok)["usd"]
    edge_usd = estimate_usd("grok-4.5", window_edge)["usd"]
    claude_usd = estimate_usd("claude-sonnet-5", midweek_claude)["usd"]
    # 1M prompt tokens is ≥200k, so grok-4.5 uses the long-context band for all tokens.
    assert today_usd == pytest.approx(1_000_000 * 4.00 / 1_000_000 + 500_000 * 12.00 / 1_000_000)
    assert claude_usd == pytest.approx(1_000_000 * 2.00 / 1_000_000 + 100_000 * 10.00 / 1_000_000)

    assert summary["today"]["tokens_in"] == 1_000_000
    assert summary["today"]["tokens_out"] == 500_000
    assert summary["today"]["usd"] == pytest.approx(today_usd)
    assert summary["today"]["by_provider"] == {"grok": 1}

    assert summary["last_7d"]["tokens_in"] == 3_000_000
    assert summary["last_7d"]["tokens_out"] == 600_000
    assert summary["last_7d"]["usd"] == pytest.approx(today_usd + claude_usd + edge_usd)
    assert summary["last_7d"]["by_provider"] == {"grok": 2, "claude": 1}
    assert summary["usage_missing"] == 0
    assert summary["rate_unknown"] == 0


def test_delta_usd_uses_only_ticked_rows() -> None:
    ticked = _record(
        days_ago=0,
        provider="grok",
        model="grok-4.5",
        input_tokens=32,
        output_tokens=9,
        extra={"reasoning_tokens": 94, "vendor_cost_ticks": 1_000_000},
    )
    unticked = _record(
        days_ago=0,
        provider="grok",
        model="grok-4.5",
        input_tokens=41,
        output_tokens=104,
    )
    summary = compute_spend([ticked, unticked], now=NOW)
    paired = compare_vendor_cost("grok-4.5", ticked)
    assert summary["today"]["vendor_rows"] == 1
    assert summary["today"]["vendor_usd"] == pytest.approx(paired["vendor_usd"])
    assert summary["today"]["ticked_table_usd"] == pytest.approx(paired["table_usd"])
    assert summary["today"]["delta_usd"] == pytest.approx(paired["delta_usd"])
    assert summary["today"]["table_usd"] != pytest.approx(paired["table_usd"])


def test_vendor_ticks_print_table_vs_vendor() -> None:
    events = [
        _record(
            days_ago=0,
            provider="grok",
            model="grok-4.5",
            input_tokens=32,
            output_tokens=9,
            extra={"reasoning_tokens": 94, "vendor_cost_ticks": 1_000_000},
        )
    ]
    summary = compute_spend(events, now=NOW)
    assert summary["today"]["vendor_rows"] == 1
    assert summary["today"]["vendor_usd"] == pytest.approx(1_000_000 / 10_000_000_000)
    assert summary["today"]["table_usd"] is not None
    assert summary["today"]["delta_usd"] == pytest.approx(
        summary["today"]["table_usd"] - summary["today"]["vendor_usd"]
    )


def test_reasoning_tokens_count_as_tokens_out() -> None:
    events = [
        _record(
            days_ago=0,
            provider="grok",
            model="grok-4.5",
            input_tokens=32,
            output_tokens=9,
            extra={"reasoning_tokens": 94, "cached_input_tokens": 6},
        )
    ]
    summary = compute_spend(events, now=NOW)
    assert summary["today"]["tokens_out"] == 103
    billed = estimate_usd("grok-4.5", events[0])
    assert summary["today"]["usd"] == pytest.approx(billed["usd"])


def test_unknown_model_usd_null_and_rate_unknown_counted() -> None:
    events = [
        _record(
            days_ago=0,
            provider="grok",
            model="not-a-real-model",
            input_tokens=41,
            output_tokens=104,
        )
    ]
    summary = compute_spend(events, now=NOW)
    assert summary["today"]["tokens_in"] == 41
    assert summary["today"]["tokens_out"] == 104
    assert summary["today"]["usd"] is None
    assert summary["last_7d"]["usd"] is None
    assert summary["rate_unknown"] == 1
    assert summary["usage_missing"] == 0


def test_stale_rate_table_warns_once(caplog: pytest.LogCaptureFixture) -> None:
    now = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
    events = [
        _record(
            days_ago=0,
            provider="grok",
            model="grok-4.5",
            input_tokens=1_000,
            output_tokens=2_000,
        )
    ]
    events[0]["timestamp"] = now.isoformat()
    with caplog.at_level("WARNING", logger="cyclaw.spend"):
        summary = compute_spend(events, now=now)
    assert summary["today"]["tokens_in"] == 1_000
    assert "priced_as_of" in caplog.text
    assert caplog.text.count("priced_as_of") == 1


def test_usage_missing_counted() -> None:
    events = [
        _record(
            days_ago=1,
            provider="claude",
            model="claude-sonnet-5",
            input_tokens=None,
            output_tokens=None,
            usage_missing=True,
        ),
        _record(
            days_ago=0,
            provider="grok",
            model="grok-4.5",
            input_tokens=1_000,
            output_tokens=2_000,
            usage_missing=False,
        ),
    ]
    summary = compute_spend(events, now=NOW)
    priced_today = 1_000 * 2.00 / 1_000_000 + 2_000 * 6.00 / 1_000_000
    assert summary["usage_missing"] == 1
    assert summary["rate_unknown"] == 0
    assert summary["today"]["tokens_in"] == 1_000
    assert summary["today"]["tokens_out"] == 2_000
    assert summary["today"]["usd"] == pytest.approx(priced_today)
    assert summary["last_7d"]["tokens_in"] == 1_000
    assert summary["last_7d"]["usd"] is None


def test_in_window_usage_missing_makes_usd_none() -> None:
    events = [
        _record(
            days_ago=0,
            provider="claude",
            model="claude-sonnet-5",
            input_tokens=None,
            output_tokens=None,
            usage_missing=True,
        ),
        _record(
            days_ago=0,
            provider="grok",
            model="grok-4.5",
            input_tokens=1_000,
            output_tokens=2_000,
        ),
    ]
    summary = compute_spend(events, now=NOW)
    assert summary["usage_missing"] == 1
    assert summary["rate_unknown"] == 0
    assert summary["today"]["usd"] is None
    assert summary["last_7d"]["usd"] is None
    assert summary["today"]["tokens_in"] == 1_000
    assert summary["today"]["tokens_out"] == 2_000


def test_out_of_window_usage_missing_does_not_taint_today() -> None:
    events = [
        _record(
            days_ago=7,
            provider="claude",
            model="claude-sonnet-5",
            input_tokens=None,
            output_tokens=None,
            usage_missing=True,
        ),
        _record(
            days_ago=0,
            provider="grok",
            model="grok-4.5",
            input_tokens=1_000,
            output_tokens=2_000,
        ),
    ]
    summary = compute_spend(events, now=NOW)
    priced_today = 1_000 * 2.00 / 1_000_000 + 2_000 * 6.00 / 1_000_000
    assert summary["usage_missing"] == 1
    assert summary["today"]["usd"] == pytest.approx(priced_today)
    assert summary["last_7d"]["usd"] == pytest.approx(priced_today)


def test_print_metrics_spend_section_after_online_escalations(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    audit = tmp_path / "audit.jsonl"
    audit.write_text(
        json.dumps(
            {
                "event": "rag_query",
                "top_score": 0.40,
                "retrieval_mode": "hybrid",
                "model_used": "grok-4.5",
                "online_escalated": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    ledger = tmp_path / "spend.jsonl"
    live = _record(
        days_ago=0,
        provider="grok",
        model="grok-4.5",
        input_tokens=1_000_000,
        output_tokens=500_000,
    )
    live["timestamp"] = datetime.now(UTC).isoformat()
    _write_ledger(ledger, [live])
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        yaml.dump(
            {
                "logging": {
                    "audit_file": str(audit),
                    "spend_file": str(ledger),
                }
            }
        ),
        encoding="utf-8",
    )
    print_metrics(str(config_path))
    out = capsys.readouterr().out
    escalations_at = out.index("Online escalations (external LLM): 1")
    spend_at = out.index("\nSpend:")
    assert spend_at > escalations_at
    assert "today: tokens_in=1000000 tokens_out=500000 usd=10.000000" in out
    assert "grok: 1" in out
    assert "usd=" in out
    dumped = ledger.read_text(encoding="utf-8")
    assert "usd" not in dumped
    assert "5.000000" not in dumped
