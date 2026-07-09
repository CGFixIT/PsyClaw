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

    def test_summary_includes_integrity_without_raw_query(self, tmp_path):
        audit_file = _write_audit(
            tmp_path,
            [{"event": "rag_query", "query": "raw-secret-text", "top_score": 0.5}],
        )

        summary = summarize_audit(audit_file)

        assert summary["audit_integrity"]["events_with_raw_query"] == 1
        assert summary["audit_integrity"]["rag_events_missing_query_hash"] == 1
        assert "query" not in summary


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
        """``main()`` takes no args and reads ``config.yaml`` from the CWD;
        run it for real against a temp corpus to prove the wiring holds."""
        audit_file = _write_audit(
            tmp_path, [{"event": "rag_query", "top_score": 0.42, "retrieval_mode": "hybrid"}]
        )
        with open(tmp_path / "config.yaml", "w", encoding="utf-8") as f:
            yaml.dump({"logging": {"audit_file": audit_file}}, f)
        monkeypatch.chdir(tmp_path)
        metrics.main()
        out = capsys.readouterr().out
        assert "Total events: 1" in out
        assert "hybrid: 1" in out
