"""Unit tests for metrics.py — audit.jsonl parsing + reporting.

Focus: the retrieval-mode breakdown must count BOTH audit event shapes.
The graph audit path writes the mode under ``retrieval_mode``; the MCP server
(``mcp_hybrid_server._handle_search``) writes it under ``mode``. A regression
here previously bucketed every ``mcp_rag_query`` as "unknown".
"""

import json

import yaml

import metrics
from metrics import compute_audit_integrity, compute_metrics, load_events, print_metrics, summarize_audit


def _write_audit(tmp_path, events):
    audit_file = tmp_path / "audit.jsonl"
    with open(audit_file, "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")
    return str(audit_file)


def _write_config(tmp_path, audit_file):
    cfg = {"logging": {"audit_file": audit_file}}
    config_path = tmp_path / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        yaml.dump(cfg, f)
    return str(config_path)


class TestLoadEvents:
    def test_missing_file_returns_empty(self, tmp_path):
        assert load_events(str(tmp_path / "nope.jsonl")) == []

    def test_skips_malformed_lines(self, tmp_path):
        p = tmp_path / "audit.jsonl"
        p.write_text('{"event": "rag_query"}\nNOT JSON\n{"event": "x"}\n', encoding="utf-8")
        events = load_events(str(p))
        assert len(events) == 2
        assert events[0]["event"] == "rag_query"

    def test_skips_json_valid_non_dict_lines(self, tmp_path):
        """null / 42 / "text" / [] parse as valid JSON but are not events —
        yielded through, compute_metrics' first e.get(...) would crash and
        take down GET /audit/summary and the cyclaw-metrics CLI."""
        p = tmp_path / "audit.jsonl"
        p.write_text(
            '{"event": "rag_query"}\nnull\n42\n"text"\n[]\n{"event": "x"}\n',
            encoding="utf-8",
        )
        events = load_events(str(p))
        assert [e["event"] for e in events] == ["rag_query", "x"]
        # The end-to-end guarantee: aggregation over the same file must not raise.
        assert compute_metrics(events)["total_events"] == 2


class TestAuditIntegrity:
    def test_counts_malformed_raw_query_and_missing_hash(self, tmp_path):
        p = tmp_path / "audit.jsonl"
        p.write_text(
            "\n".join([
                json.dumps({"event": "rag_query", "query": "raw", "top_score": 0.3}),
                json.dumps({"event": "mcp_rag_query", "query_hash": "abc"}),
                "NOT JSON",
            ]) + "\n",
            encoding="utf-8",
        )

        assert compute_audit_integrity(str(p)) == {
            "malformed_lines": 1,
            "events_with_raw_query": 1,
            "rag_events_missing_query_hash": 1,
        }

    def test_json_valid_non_dict_lines_count_as_malformed(self, tmp_path):
        """A JSON-valid non-object line ("query" in None would TypeError) must
        be counted as malformed evidence, not crash the integrity pass."""
        p = tmp_path / "audit.jsonl"
        p.write_text(
            "\n".join([
                json.dumps({"event": "rag_query", "query_hash": "abc"}),
                "null", "42", '"text"', "[]",
            ]) + "\n",
            encoding="utf-8",
        )
        assert compute_audit_integrity(str(p)) == {
            "malformed_lines": 4,
            "events_with_raw_query": 0,
            "rag_events_missing_query_hash": 0,
        }

    def test_blank_lines_are_not_counted_as_malformed(self, tmp_path):
        """A blank/whitespace-only line (manual editing, log rotation, an
        interleaved partial write from a concurrent writer) is not corruption
        and must not trip the same alarm as genuinely bad JSON."""
        p = tmp_path / "audit.jsonl"
        p.write_text(
            "\n".join([
                json.dumps({"event": "rag_query", "query_hash": "abc"}),
                "",
                "   ",
                json.dumps({"event": "mcp_rag_query", "query_hash": "def"}),
                "NOT JSON",
            ]) + "\n",
            encoding="utf-8",
        )
        assert compute_audit_integrity(str(p)) == {
            "malformed_lines": 1,
            "events_with_raw_query": 0,
            "rag_events_missing_query_hash": 0,
        }

    def test_summarize_audit_blank_lines_are_not_counted_as_malformed(self, tmp_path):
        """summarize_audit's _events() duplicates compute_audit_integrity's
        loop for a single-pass optimization; the blank-line guard must hold
        in both copies."""
        p = tmp_path / "audit.jsonl"
        p.write_text(
            "\n".join([
                json.dumps({"event": "rag_query", "query_hash": "abc"}),
                "",
                "   ",
                "NOT JSON",
            ]) + "\n",
            encoding="utf-8",
        )
        summary = summarize_audit(str(p))
        assert summary["audit_integrity"]["malformed_lines"] == 1

    def test_summary_includes_integrity_without_raw_query(self, tmp_path):
        audit_file = _write_audit(
            tmp_path,
            [{"event": "rag_query", "query": "raw-secret-text", "top_score": 0.5}],
        )

        summary = summarize_audit(audit_file)

        assert summary["audit_integrity"]["events_with_raw_query"] == 1
        assert summary["audit_integrity"]["rag_events_missing_query_hash"] == 1
        assert "query" not in summary


class TestResolveConfigPath:
    """cwd-independence for print_metrics's default config_path="config.yaml".

    Regression: print_metrics previously did open(config_path, ...) directly,
    resolving a relative path against the process cwd -- ``cyclaw-metrics`` run
    from anywhere but the repo root crashed with FileNotFoundError instead of
    finding the real config. Mirrors retrieval/indexer.py::_resolve_config_path
    (same anchoring) and tests/test_logger.py::TestAnchor (same test shape).
    """

    def test_relative_path_anchored_to_repo_root(self):
        assert metrics._resolve_config_path("config.yaml") == (metrics._REPO_ROOT / "config.yaml").resolve()

    def test_absolute_path_passed_through(self, tmp_path):
        absolute = tmp_path / "config.yaml"
        assert metrics._resolve_config_path(str(absolute)) == absolute.resolve()


class TestPrintMetrics:
    def test_no_events_message(self, tmp_path, capsys):
        audit_file = _write_audit(tmp_path, [])
        config_path = _write_config(tmp_path, audit_file)
        print_metrics(config_path)
        assert "No audit events found." in capsys.readouterr().out

    def test_mcp_and_graph_modes_both_counted(self, tmp_path, capsys):
        """Regression: mcp_rag_query stores the mode under 'mode', not
        'retrieval_mode'. Both must be counted, and neither shows as 'unknown'."""
        events = [
            # graph audit path → "retrieval_mode"
            {"event": "rag_query", "top_score": 0.40, "retrieval_mode": "hybrid"},
            {"event": "rag_query", "top_score": 0.20, "retrieval_mode": "semantic"},
            # MCP path → "mode"
            {"event": "mcp_rag_query", "top_score": 0.50, "mode": "hybrid"},
            {"event": "mcp_rag_query", "top_score": 0.10, "mode": "keyword"},
        ]
        audit_file = _write_audit(tmp_path, events)
        config_path = _write_config(tmp_path, audit_file)
        print_metrics(config_path)
        out = capsys.readouterr().out

        # hybrid appears in both a graph and an MCP event → 2
        assert "hybrid: 2" in out
        assert "semantic: 1" in out
        assert "keyword: 1" in out
        # The MCP events must NOT fall through to the "unknown" bucket.
        assert "unknown" not in out

    def test_model_used_and_online_escalations_are_printed(self, tmp_path, capsys):
        """compute_metrics() aggregates model_used + online_escalated (both shown
        at GET /audit/summary); the CLI must surface them, not drop them."""
        events = [
            {"event": "rag_query", "model_used": "qwen", "top_score": 0.40,
             "retrieval_mode": "hybrid", "online_escalated": False},
            {"event": "rag_query", "model_used": "grok-4.3", "top_score": 0.30,
             "retrieval_mode": "hybrid", "online_escalated": True},
        ]
        audit_file = _write_audit(tmp_path, events)
        config_path = _write_config(tmp_path, audit_file)
        print_metrics(config_path)
        out = capsys.readouterr().out
        assert "Model used:" in out
        assert "qwen: 1" in out
        assert "grok-4.3: 1" in out
        assert "Online escalations (external LLM): 1" in out

    def test_integrity_warnings_are_printed(self, tmp_path, capsys):
        p = tmp_path / "audit.jsonl"
        p.write_text(
            "\n".join([
                json.dumps({"event": "rag_query", "query": "raw-secret-text"}),
                "NOT JSON",
            ]) + "\n",
            encoding="utf-8",
        )
        config_path = _write_config(tmp_path, str(p))
        print_metrics(config_path)
        out = capsys.readouterr().out

        assert "Audit integrity:" in out
        assert "malformed_lines: 1" in out
        assert "events_with_raw_query: 1" in out
        assert "rag_events_missing_query_hash: 1" in out
        assert "raw-secret-text" not in out

    def test_score_stats_span_both_event_types(self, tmp_path, capsys):
        events = [
            {"event": "rag_query", "top_score": 0.40, "retrieval_mode": "hybrid"},
            {"event": "mcp_rag_query", "top_score": 0.60, "mode": "hybrid"},
        ]
        audit_file = _write_audit(tmp_path, events)
        config_path = _write_config(tmp_path, audit_file)
        print_metrics(config_path)
        out = capsys.readouterr().out
        # avg (0.5), min (0.4), max (0.6) computed across both event shapes.
        assert "avg: 0.500" in out
        assert "min: 0.400" in out
        assert "max: 0.600" in out


class TestComputeMetrics:
    """Direct coverage of the aggregate fields surfaced at GET /audit/summary."""

    def test_non_numeric_top_score_is_skipped_not_fatal(self):
        """A JSON-valid audit line whose top_score is null/string/bool must be
        excluded from the score stats instead of raising TypeError — one
        malformed line must never take down GET /audit/summary or the
        cyclaw-metrics CLI (load_events already tolerates non-JSON lines; this
        extends the same posture to field types)."""
        events = [
            {"event": "rag_query", "top_score": 0.40, "retrieval_mode": "hybrid"},
            {"event": "rag_query", "top_score": None, "retrieval_mode": "hybrid"},
            {"event": "rag_query", "top_score": "0.9", "retrieval_mode": "hybrid"},
            {"event": "mcp_rag_query", "top_score": True, "mode": "hybrid"},
            {"event": "mcp_rag_query", "top_score": 0.60, "mode": "hybrid"},
        ]
        m = compute_metrics(events)
        # All five still count as rag queries; only the two numeric scores
        # feed the stats (bool excluded — it is an int subclass and True
        # would otherwise count as a 1.0 score).
        assert m["rag_query_count"] == 5
        assert m["scores"]["min"] == 0.40
        assert m["scores"]["max"] == 0.60
        assert abs(m["scores"]["avg"] - 0.50) < 1e-9

    def test_unhashable_label_values_do_not_crash_counters(self):
        """A corrupt audit line carrying a JSON list/dict where event /
        retrieval_mode / model_used expect a string must not raise
        TypeError('unhashable type') — Counter keys must stay hashable so one
        bad line can't 500 GET /audit/summary or the cyclaw-metrics CLI."""
        events = [
            {"event": "rag_query", "model_used": "qwen", "retrieval_mode": "hybrid", "top_score": 0.4},
            {"event": ["corrupt"]},                                       # unhashable event label
            {"event": "rag_query", "retrieval_mode": ["hybrid"], "top_score": 0.3},   # unhashable mode
            {"event": "rag_query", "model_used": {"x": 1}, "retrieval_mode": "keyword", "top_score": 0.2},
        ]
        m = compute_metrics(events)
        assert m["total_events"] == 4
        # The corrupt event label falls to the "unknown" bucket, not a crash.
        assert m["event_breakdown"]["unknown"] == 1
        # The one corrupt retrieval_mode (list) falls to "unknown"; the two valid
        # string modes are counted normally.
        assert m["retrieval_modes"] == {"hybrid": 1, "unknown": 1, "keyword": 1}
        # The dict model_used is skipped, not counted; the one valid string stays.
        assert m["model_used"] == {"qwen": 1}

    def test_nan_top_score_excluded_so_summary_is_json_renderable(self):
        """A JSON-valid top_score: NaN must be excluded from the average — a NaN
        avg makes Starlette's JSONResponse (allow_nan=False) raise at render,
        500-ing GET /audit/summary. The summary must serialize with allow_nan
        disabled, exactly as the endpoint renders it."""
        events = [
            {"event": "rag_query", "top_score": 0.5, "retrieval_mode": "hybrid"},
            {"event": "rag_query", "top_score": float("nan"), "retrieval_mode": "hybrid"},
            {"event": "rag_query", "top_score": float("inf"), "retrieval_mode": "hybrid"},
        ]
        m = compute_metrics(events)
        assert m["scores"]["avg"] == 0.5
        # Would raise ValueError("Out of range float values...") if NaN/inf leaked in.
        json.dumps(m, allow_nan=False)

    def test_model_used_excludes_non_answer_events(self):
        """The graph stamps model_used="unknown" on user_gate_pause events.
        Those must not appear in the model-usage breakdown — only answered
        rag_query / mcp_rag_query events count."""
        events = [
            {"event": "rag_query", "model_used": "qwen", "top_score": 0.4},
            {"event": "rag_query", "model_used": "qwen", "top_score": 0.3},
            # paused (score too low, awaiting confirm) — model_used is "unknown"
            {"event": "user_gate_pause", "model_used": "unknown", "top_score": 0.01},
        ]
        summary = compute_metrics(events)
        assert summary["model_used"] == {"qwen": 2}
        assert "unknown" not in summary["model_used"]

    def test_online_escalated_uses_explicit_field(self):
        """online_escalated is the boolean the graph audit node writes; it is the
        source of truth even when user_confirmed_online is absent (the graph never
        writes that key)."""
        events = [
            {"event": "rag_query", "online_escalated": True, "model_used": "grok-4.3"},
            {"event": "rag_query", "online_escalated": False, "model_used": "qwen"},
        ]
        assert compute_metrics(events)["online_escalated"] == 1

    def test_online_escalated_falls_back_to_model_heuristic(self):
        """Older events without the explicit field still count via the grok
        model-name heuristic."""
        events = [{"event": "rag_query", "model_used": "grok-4.3"}]
        assert compute_metrics(events)["online_escalated"] == 1

    def test_online_escalated_falls_back_to_claude_model_heuristic(self):
        """The model-name heuristic must recognize claude too, not just grok —
        both are external providers gated the same way (graph.py's
        audit_logger_node sets online_escalated = answer_model in
        {"grok", "claude"}); an older/legacy Claude event without the explicit
        field deserves the same fallback recognition a legacy Grok event gets."""
        events = [{"event": "rag_query", "model_used": "claude-sonnet-5"}]
        assert compute_metrics(events)["online_escalated"] == 1

    def test_guardrail_blocked_and_degraded_counters(self):
        """graph.audit_logger_node stamps guardrail_blocked/guardrail_degraded on
        every rag_query AND user_gate_pause event -- count both event types, and
        only count True (present-but-false and absent must not increment)."""
        events = [
            {"event": "rag_query", "guardrail_blocked": True, "guardrail_degraded": False},
            {"event": "rag_query", "guardrail_blocked": False, "guardrail_degraded": True},
            {"event": "user_gate_pause", "guardrail_blocked": True, "guardrail_degraded": True},
            {"event": "rag_query"},  # legacy event predating both fields
        ]
        summary = compute_metrics(events)
        assert summary["guardrail_blocked_count"] == 2
        assert summary["guardrail_degraded_count"] == 2

    def test_guardrail_counters_zero_when_absent(self):
        events = [{"event": "rag_query", "model_used": "qwen"}]
        summary = compute_metrics(events)
        assert summary["guardrail_blocked_count"] == 0
        assert summary["guardrail_degraded_count"] == 0


class TestMain:
    """Cover the ``cyclaw-metrics`` console entry point (``metrics:main``).

    Regression guard: the declared ``cyclaw-metrics = "metrics:main"`` script
    once raised ``AttributeError`` at invocation because the module defined only
    ``print_metrics`` and no ``main``. These tests fail loudly if the entry
    point is removed or stops delegating to ``print_metrics``.
    """

    def test_main_delegates_to_print_metrics(self, monkeypatch):
        calls: list[tuple] = []
        monkeypatch.setattr(metrics, "print_metrics", lambda *a, **k: calls.append((a, k)))
        assert metrics.main() is None
        assert len(calls) == 1

    def test_main_runs_end_to_end_with_default_config(self, tmp_path, monkeypatch, capsys):
        """``main()`` takes no args and reads ``config.yaml`` anchored to the repo
        root, not the process cwd; run it for real against a temp corpus to prove
        the wiring holds. ``_REPO_ROOT`` is monkeypatched to ``tmp_path`` (mirroring
        ``tests/test_logger.py``'s identical pattern) so this stays isolated from
        the real repo config instead of coupling the test to its contents."""
        audit_file = _write_audit(
            tmp_path, [{"event": "rag_query", "top_score": 0.42, "retrieval_mode": "hybrid"}]
        )
        with open(tmp_path / "config.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"logging": {"audit_file": audit_file}}, f)
        monkeypatch.setattr(metrics, "_REPO_ROOT", tmp_path)
        # A foreign cwd (not tmp_path/_REPO_ROOT) proves resolution is genuinely
        # cwd-independent, not just accidentally correct because cwd == _REPO_ROOT.
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)
        metrics.main()
        out = capsys.readouterr().out
        assert "Total events: 1" in out
        assert "hybrid: 1" in out


class TestSpendPrintAndWindows:
    def test_print_spend_none_is_noop(self, capsys):
        metrics._print_spend(None)
        assert capsys.readouterr().out == ""

    def test_spend_event_date_and_token_guards(self):
        assert metrics._spend_event_date({}) is None
        assert metrics._spend_event_date({"timestamp": "not-a-date"}) is None
        naive = metrics._spend_event_date({"timestamp": "2026-08-16T12:00:00"})
        assert naive is not None
        aware = metrics._spend_event_date({"timestamp": "2026-08-16T12:00:00+00:00"})
        assert aware is not None
        assert metrics._spend_token_count({"input_tokens": True}, "input_tokens") == 0
        assert metrics._spend_token_count({"input_tokens": 4}, "input_tokens") == 4

    def test_compute_spend_now_normalization(self):
        from datetime import UTC, datetime

        events = [{
            "timestamp": "2026-08-16T12:00:00+00:00",
            "provider": "grok",
            "model": "not-a-real-model",
            "input_tokens": 1,
            "output_tokens": 1,
        }]
        naive = datetime(2026, 8, 16, 12, 0)
        summary = metrics.compute_spend(events, now=naive)
        assert summary["rate_unknown"] == 1
        aware = datetime(2026, 8, 16, 14, 0, tzinfo=UTC)
        metrics.compute_spend(events, now=aware)
        metrics.compute_spend([{"not": "usable"}], now=aware)

    def test_print_metrics_no_events_with_integrity_and_vendor_na(self, tmp_path, capsys, monkeypatch):
        from datetime import UTC, datetime

        now = datetime.now(UTC)
        audit = tmp_path / "audit.jsonl"
        audit.write_text("NOT JSON\n", encoding="utf-8")
        spend = tmp_path / "spend.jsonl"
        spend.write_text(
            json.dumps({
                "timestamp": now.isoformat(),
                "provider": "grok",
                "model": "not-a-real-model",
                "input_tokens": 1,
                "output_tokens": 1,
                "vendor_cost_ticks": 1_000_000,
                "usage_missing": True,
            })
            + "\n",
            encoding="utf-8",
        )
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump({
                "logging": {"audit_file": str(audit), "spend_file": str(spend)},
            }, f)
        monkeypatch.setattr(metrics, "_REPO_ROOT", tmp_path)
        import utils.sequence_detect as seq

        monkeypatch.setattr(seq, "format_sequences", lambda *_a, **_k: ["Sequences: demo"])
        print_metrics(str(config_path))
        out = capsys.readouterr().out
        assert "No audit events found." in out
        assert "malformed_lines: 1" in out
        assert "delta_usd=n/a" in out
        assert "usage_missing:" in out
        assert "Sequences: demo" in out

    def test_print_metrics_prints_source_and_rate_unknown(self, tmp_path, capsys, monkeypatch):
        from datetime import UTC, datetime

        audit = _write_audit(
            tmp_path,
            [{"event": "rag_query", "top_score": 0.4, "retrieval_mode": "hybrid", "model_used": "qwen"}],
        )
        spend = tmp_path / "spend.jsonl"
        spend.write_text(
            json.dumps({
                "timestamp": datetime.now(UTC).isoformat(),
                "provider": "grok",
                "model": "not-a-real-model",
                "input_tokens": 3,
                "output_tokens": 1,
                "source": "query",
            })
            + "\n",
            encoding="utf-8",
        )
        config_path = tmp_path / "config.yaml"
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump({"logging": {"audit_file": audit, "spend_file": str(spend)}}, f)
        monkeypatch.setattr(metrics, "_REPO_ROOT", tmp_path)
        print_metrics(str(config_path))
        out = capsys.readouterr().out
        assert "source query:" in out
        assert "rate_unknown:" in out

    def test_compute_spend_skips_non_dict_events(self):
        from datetime import UTC, datetime

        summary = metrics.compute_spend(
            ["not-a-dict", 42, None],
            now=datetime(2026, 8, 16, tzinfo=UTC),
        )
        assert summary["today"]["tokens_in"] == 0
        assert summary["usage_missing"] == 0

    def test_print_spend_prints_delta_when_both_sides_priced(self, capsys):
        spend = {
            "today": {
                "tokens_in": 1,
                "tokens_out": 1,
                "usd": 0.001,
                "table_usd": 0.001,
                "ticked_table_usd": 0.001,
                "vendor_usd": 0.002,
                "delta_usd": -0.001,
                "vendor_rows": 1,
                "by_provider": {"grok": 1},
                "by_source": {},
            },
            "last_7d": {
                "tokens_in": 1,
                "tokens_out": 1,
                "usd": 0.001,
                "table_usd": 0.001,
                "ticked_table_usd": 0.001,
                "vendor_usd": 0.002,
                "delta_usd": -0.001,
                "vendor_rows": 1,
                "by_provider": {"grok": 1},
                "by_source": {},
            },
            "usage_missing": 0,
            "rate_unknown": 0,
        }
        metrics._print_spend(spend)
        out = capsys.readouterr().out
        assert "delta_usd=-0.001000" in out
        assert "ticked_table_usd=" in out

    def test_compute_audit_integrity_missing_file_is_empty(self, tmp_path):
        assert compute_audit_integrity(str(tmp_path / "nope.jsonl")) == {
            "malformed_lines": 0,
            "events_with_raw_query": 0,
            "rag_events_missing_query_hash": 0,
        }

    def test_summarize_audit_missing_file_and_non_dict_lines(self, tmp_path):
        missing = summarize_audit(str(tmp_path / "nope.jsonl"))
        assert missing["audit_integrity"]["malformed_lines"] == 0
        p = tmp_path / "audit.jsonl"
        p.write_text('{"event": "rag_query", "query_hash": "abc"}\nnull\n42\n', encoding="utf-8")
        summary = summarize_audit(str(p))
        assert summary["audit_integrity"]["malformed_lines"] == 2
        assert summary["total_events"] == 1
