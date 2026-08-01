"""Tests for guardrails.metrics -- the separate recorder + analyzer."""

from __future__ import annotations

import builtins
import json
import threading

import pytest

from guardrails.metrics import (
    EVENT_BLOCKED,
    EVENT_HALLUCINATION,
    EVENT_TOOL_CALL,
    GuardrailMetrics,
    close_metrics_handles,
    compute_guardrail_metrics,
    load_events,
)


@pytest.fixture(autouse=True)
def _close_metrics_handles():
    # The recorder now keeps one cached append handle per path. Release them
    # between tests so tmp_path teardown never races an open descriptor (the
    # same reason tests/conftest-style suites call close_audit_handles()).
    yield
    close_metrics_handles()


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
    secret_marker = "do-not-log-this-metric-field"

    class NonSerializable:
        def __repr__(self):
            return secret_marker

    m = GuardrailMetrics(tmp_path / "guardrails.jsonl")
    record = m.record_blocked(
        stage="input",
        rail="check_injection",
        query="private query",
        payload=NonSerializable(),
    )

    assert record["event"] == EVENT_BLOCKED
    assert m.counters[EVENT_BLOCKED] == 1
    assert "Guardrail metrics persistence skipped (TypeError)" in caplog.text
    assert secret_marker not in caplog.text
    assert "private query" not in caplog.text


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


def test_concurrent_records_never_interleave_a_line(tmp_path):
    # The recorder is called from FastAPI's threadpool once the input rail is
    # enabled, and it now writes through ONE shared cached handle instead of a
    # per-call file object. A shared BufferedWriter is not documented as safe
    # for concurrent writes, and a torn line is invisible in production --
    # load_events() drops it via its JSONDecodeError handler, so the event is
    # simply lost with no error anywhere. This asserts the round-trip invariant
    # every line must satisfy.
    #
    # Honest scope note: on CPython/Linux the GIL plus a single buffered write
    # already makes this hold even without the lock, so this test does NOT fail
    # on a lock-free variant here -- it fails once a write is split into
    # multiple syscalls (verified locally by chunking the write: 278/400 lines
    # survived). It is a guard on the contract, not a reproduction of a Linux
    # bug: the lock exists because sharing the handle makes it required, the
    # same pairing utils/logger.py uses for the audit stream.
    path = tmp_path / "guardrails.jsonl"
    m = GuardrailMetrics(path)
    threads = 16
    per_thread = 25
    filler = "x" * 8192

    def worker(n: int) -> None:
        for i in range(per_thread):
            m.record_tool_call(f"tool-{n}", query=f"q-{n}-{i}", filler=filler)

    workers = [threading.Thread(target=worker, args=(n,)) for n in range(threads)]
    for t in workers:
        t.start()
    for t in workers:
        t.join()

    raw_lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(raw_lines) == threads * per_thread
    # load_events() silently skips malformed lines, so compare against the raw
    # count rather than trusting its output alone.
    assert len(load_events(path)) == len(raw_lines)


def test_handle_is_reused_across_records(tmp_path, monkeypatch):
    # One cached handle per path -- not a fresh open() per event. Guards the
    # hot-path regression this replaced (open+write+close on every query).
    path = tmp_path / "guardrails.jsonl"
    m = GuardrailMetrics(path)
    opened: list[str] = []
    real_open = builtins.open

    def counting_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", counting_open)
    for i in range(10):
        m.record_allowed(query=f"q{i}")
    monkeypatch.undo()

    assert opened.count(str(path)) == 1, f"expected one open(), got {opened.count(str(path))}"
    assert len(load_events(path)) == 10
