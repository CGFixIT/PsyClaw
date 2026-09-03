"""Tests for guardrails.metrics -- the separate recorder + analyzer."""

from __future__ import annotations

import json

from guardrails.metrics import (
    EVENT_ALLOWED,
    EVENT_BLOCKED,
    EVENT_HALLUCINATION,
    EVENT_RAIL_TRIGGERED,
    EVENT_SKIPPED,
    EVENT_SOUL_TOPIC,
    EVENT_TOOL_CALL,
    FORBIDDEN_METRIC_KEYS,
    GuardrailMetrics,
    compute_guardrail_metrics,
    load_events,
    sanitize_metric_fields,
)

_ALL_EVENT_TYPES = (
    EVENT_TOOL_CALL,
    EVENT_BLOCKED,
    EVENT_HALLUCINATION,
    EVENT_RAIL_TRIGGERED,
    EVENT_ALLOWED,
    EVENT_SKIPPED,
    EVENT_SOUL_TOPIC,
)


def test_record_persists_jsonl_and_hashes_query(tmp_path):
    path = tmp_path / "guardrails.jsonl"
    m = GuardrailMetrics(path)
    m.record_blocked(stage="input", rail="check_injection", reason="x", query="secret query")
    events = load_events(path)
    assert len(events) == 1
    rec = events[0]
    assert rec["event"] == EVENT_BLOCKED
    assert rec["stage"] == "input"
    # Raw text must never be persisted -- only the hash.
    assert "query" not in rec
    assert len(rec["query_hash"]) == 64
    assert rec["query_hash"] != "secret query"


def test_persist_false_does_not_write(tmp_path):
    path = tmp_path / "guardrails.jsonl"
    m = GuardrailMetrics(path, persist=False)
    m.record_tool_call("gh_pr_view")
    assert not path.exists()
    assert m.counters[EVENT_TOOL_CALL] == 1
    assert m.tools_called["gh_pr_view"] == 1


def test_persistence_failure_does_not_break_metrics_call(tmp_path, caplog):
    blocked_parent = tmp_path / "not-a-directory"
    blocked_parent.write_text("occupied", encoding="utf-8")
    m = GuardrailMetrics(blocked_parent / "guardrails.jsonl")

    record = m.record_blocked(
        stage="input",
        rail="check_soul_mutation",
        reason="offline heuristic",
        query="rewrite your soul",
    )

    assert record["event"] == EVENT_BLOCKED
    assert m.counters[EVENT_BLOCKED] == 1
    assert "Guardrail metrics persistence skipped (FileExistsError)" in caplog.text
    assert str(blocked_parent) not in caplog.text


def test_serialization_failure_does_not_log_metric_fields(tmp_path, caplog):
    # After allowlist, unknown NonSerializable kwargs are stripped so json.dumps
    # succeeds; payload/secret markers must not appear in the persisted record.
    secret_marker = "do-not-log-this-metric-field"

    class NonSerializable:
        def __repr__(self):
            return secret_marker

    path = tmp_path / "guardrails.jsonl"
    m = GuardrailMetrics(path)
    record = m.record_blocked(
        stage="input",
        rail="check_injection",
        query="private query",
        payload=NonSerializable(),
    )

    assert record["event"] == EVENT_BLOCKED
    assert m.counters[EVENT_BLOCKED] == 1
    assert "payload" not in record
    events = load_events(path)
    assert len(events) == 1
    assert "payload" not in events[0]
    raw = path.read_text(encoding="utf-8")
    assert secret_marker not in raw
    assert "private query" not in raw
    assert secret_marker not in caplog.text
    assert "private query" not in caplog.text


def test_forbidden_keys_stripped_from_persist_and_sanitize(tmp_path):
    path = tmp_path / "guardrails.jsonl"
    m = GuardrailMetrics(path)
    m.record_blocked(
        stage="input",
        rail="check_injection",
        reason="x",
        query="hashed-only",
        prompt="secret",
        **{"token": "leak-token"},
    )
    events = load_events(path)
    assert len(events) == 1
    rec = events[0]
    assert "prompt" not in rec
    assert "token" not in rec
    assert "query" not in rec
    assert "query_hash" in rec
    assert len(rec["query_hash"]) == 64
    raw = path.read_text(encoding="utf-8")
    assert "secret" not in raw
    assert "leak-token" not in raw

    nested = sanitize_metric_fields({"auth": {"api_key": "sk-x"}, "ok": 1, "prompt": "nope"})
    assert nested == {"auth": {}, "ok": 1}
    assert "api_key" not in nested["auth"]
    assert "prompt" not in nested

    under_allowed = sanitize_metric_fields({"ok": {"prompt": "secret", "ok": 1}})
    assert under_allowed == {"ok": {"ok": 1}}

    kept = sanitize_metric_fields({"query_hash": "abc", "token_hash": "def", "token": "drop"})
    assert kept == {"query_hash": "abc", "token_hash": "def"}
    assert "token" in FORBIDDEN_METRIC_KEYS


def test_response_never_persisted_for_any_event_type(tmp_path):
    path = tmp_path / "guardrails.jsonl"
    m = GuardrailMetrics(path)
    m.record_tool_call("gh_pr_view", ok=True, response="RAW")
    m.record_blocked(stage="input", rail="check_injection", reason="x", response="RAW")
    m.record_hallucination(score=0.1, threshold=0.2, response="RAW")
    m.record_rail("check_injection", stage="input", response="RAW")
    m.record_allowed(score=0.9, response="RAW")
    m.record_skipped(reason="disabled", response="RAW")
    m.record_soul_topic(response="RAW")

    events = load_events(path)
    assert len(events) == len(_ALL_EVENT_TYPES)
    seen = {e["event"] for e in events}
    assert seen == set(_ALL_EVENT_TYPES)
    for rec in events:
        assert "response" not in rec
    assert "RAW" not in path.read_text(encoding="utf-8")


def test_compute_summary_aggregates(tmp_path):
    path = tmp_path / "guardrails.jsonl"
    m = GuardrailMetrics(path)
    m.record_tool_call("gh_pr_view", ok=True)
    m.record_tool_call("gh_issue_view", ok=False)
    m.record_blocked(stage="input", rail="check_soul_mutation", reason="mutation")
    m.record_blocked(stage="output", rail="check_grounding", reason="ungrounded")
    m.record_hallucination(score=0.05, threshold=0.18)
    m.record_allowed(score=0.9)
    m.record_soul_topic()
    m.record_skipped(reason="disabled")

    summary = compute_guardrail_metrics(load_events(path))
    assert summary["tool_calls"] == 2
    assert summary["tool_call_failures"] == 1
    assert summary["tools_by_name"]["gh_pr_view"] == 1
    assert summary["blocked_generations"] == 2
    assert summary["blocks_by_stage"] == {"input": 1, "output": 1}
    assert summary["hallucinations_flagged"] == 1
    assert summary["soul_topic_hits"] == 1
    assert summary["generations_allowed"] == 1
    assert summary["guardrail_skipped"] == 1
    # rails_by_name draws from rail_triggered events AND block rails.
    assert summary["rails_by_name"]["check_soul_mutation"] == 1
    assert summary["rails_by_name"]["check_grounding"] == 1
    # block_rate = blocked / (allowed + blocked) = 2 / 3.
    assert summary["block_rate"] == 2 / 3
    g = summary["grounding"]
    assert g["min"] == 0.05
    assert g["max"] == 0.9


def test_compute_summary_empty():
    summary = compute_guardrail_metrics([])
    assert summary["total_events"] == 0
    assert summary["block_rate"] is None
    assert summary["grounding"]["avg"] is None


def test_load_events_missing_file(tmp_path):
    assert load_events(tmp_path / "nope.jsonl") == []


def test_load_events_skips_invalid_event_lines(tmp_path):
    path = tmp_path / "guardrails.jsonl"
    path.write_text(
        json.dumps({"event": EVENT_HALLUCINATION, "grounding_score": 0.1})
        + '\nnull\n[]\n"text"\n1\nnot json\n\n',
        encoding="utf-8",
    )
    events = load_events(path)
    assert len(events) == 1
    assert compute_guardrail_metrics(events)["hallucinations_flagged"] == 1


def test_sanitize_metric_fields_list_and_tuple():
    from guardrails.metrics import sanitize_metric_fields

    cleaned = sanitize_metric_fields(
        [{"query": "secret", "ok": 1}, ({"response": "nope", "n": 2},)]
    )
    assert cleaned == [{"ok": 1}, [{"n": 2}]]


def test_print_metrics_empty_and_rich(tmp_path, capsys):
    from guardrails.metrics import EVENT_ALLOWED, EVENT_TOOL_CALL, print_metrics

    missing = tmp_path / "missing.jsonl"
    print_metrics(missing)
    assert "No guardrail events found" in capsys.readouterr().out

    path = tmp_path / "guardrails.jsonl"
    events = [
        {"event": EVENT_ALLOWED, "grounding_score": 0.5},
        {"event": EVENT_TOOL_CALL, "tool": "web_fetch", "ok": True},
        {"event": EVENT_TOOL_CALL, "tool": "web_fetch", "ok": True},
    ]
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n", encoding="utf-8")
    print_metrics(path)
    out = capsys.readouterr().out
    assert "Total guardrail events" in out
    assert "web_fetch" in out
    assert "Grounding score" in out


def test_metrics_main_uses_config_and_fallback(monkeypatch, capsys):
    from guardrails import metrics as metrics_mod

    class _Cfg:
        metrics_path = "logs/from-config.jsonl"

    monkeypatch.setattr(
        "guardrails.config.load_guardrails_config",
        lambda: _Cfg(),
    )
    seen: list[str] = []
    monkeypatch.setattr(metrics_mod, "print_metrics", lambda path: seen.append(str(path)))
    metrics_mod.main()
    assert seen == ["logs/from-config.jsonl"]

    def _boom():
        raise RuntimeError("no config")

    monkeypatch.setattr("guardrails.config.load_guardrails_config", _boom)
    seen.clear()
    metrics_mod.main()
    assert seen == ["logs/guardrails.jsonl"]
