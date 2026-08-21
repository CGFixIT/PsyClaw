"""Tests for the mainline audit-trail -> Numbat NDJSON projection.

utils/logger.audit_log() dual-writes: the legacy logs/audit.jsonl line stays
authoritative (shape unchanged), and each redacted record is also projected
through utils/numbat_emitter.project_audit_record() into the Numbat stream.
Covered here: the mapping table, schema discipline (no illegal top-level
keys), privacy (no raw query text in either stream), the disabled switch,
and independent fail-soft behavior.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from utils.logger import audit_log, close_audit_handles, reset_config_cache
from utils.numbat_emitter import (
    _AUDIT_ACTION_PLANE_EVENTS,
    _KNOWN_FIELDS,
    project_audit_record,
)


@pytest.fixture(autouse=True)
def _clean_state():
    reset_config_cache()
    yield
    close_audit_handles()
    reset_config_cache()


@pytest.fixture
def proj_cfg(tmp_path: Path) -> tuple[dict, Path, Path]:
    audit = tmp_path / "audit.jsonl"
    out = tmp_path / "numbat-events.ndjsonl"
    cfg = {
        "logging": {"audit_file": str(audit)},
        "numbat": {"enabled": True, "output_path": str(out)},
    }
    return cfg, audit, out


def _lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_rag_query_projects_prompt_user(proj_cfg) -> None:
    cfg, audit, out = proj_cfg
    audit_log({
        "event": "rag_query",
        "query": "what is my email alice@example.com",
        "top_score": 0.04,
        "retrieval_mode": "hybrid",
        "online_escalated": False,
        "model_used": "local",
        "llm_model": "qwen3.8:27b-mlx",
        "hit_count": 3,
        "guardrail_blocked": False,
        "guardrail_rails": [],
        "sources": [{"source": "docs/a.md", "chunk_id": 0, "rrf_score": 0.03}],
        "error": None,
    }, cfg=cfg)
    close_audit_handles()

    # Legacy stream unchanged and authoritative.
    legacy = _lines(audit)
    assert len(legacy) == 1
    assert legacy[0]["event"] == "rag_query"
    assert "query" not in legacy[0]
    assert "query_hash" in legacy[0]

    records = _lines(out)
    assert len(records) == 1
    rec = records[0]
    assert rec["schema_version"] == "0.3.0"
    assert rec["record_type"] == "event"
    assert rec["source_agent"] == "unknown"
    assert rec["source_type"] == "hook"
    assert rec["event_type"] == "prompt.user"
    assert rec["actor"] == "user"
    assert rec["confidence"] == "high"
    assert rec["entrypoint"] == "cyclaw"
    assert rec["model_provider"] == "ollama"
    assert rec["model"] == "qwen3.8:27b-mlx"
    assert "cyclaw" in rec["tags"] and "rag_query" in rec["tags"]
    assert rec["evidence"]["artifact_type"] == "cyclaw_audit_jsonl"
    # additionalProperties:false — no CyClaw forensics as top-level keys.
    assert set(rec.keys()) <= _KNOWN_FIELDS
    assert "query" not in rec
    assert "query_hash" not in rec
    # Forensics ride inside content_preview, built from the REDACTED record:
    # hashed query, PII-redacted anything else.
    preview = json.loads(rec["content_preview"])
    assert preview["cyclaw_event"] == "rag_query"
    assert "query_hash" in preview
    assert "alice@example.com" not in rec["content_preview"]


def test_guardrail_blocked_rag_query_tagged(proj_cfg) -> None:
    cfg, _, out = proj_cfg
    audit_log({"event": "rag_query", "query": "q", "guardrail_blocked": True,
               "guardrail_rails": ["input"], "model_used": "local"}, cfg=cfg)
    close_audit_handles()
    rec = _lines(out)[0]
    assert "guardrail_blocked" in rec["tags"]
    # prompt.user cannot carry `decision` (CLI allowlist) — verdict stays in
    # the preview.
    assert "decision" not in rec
    assert json.loads(rec["content_preview"])["guardrail_blocked"] is True


def test_permission_denied_mapping(proj_cfg) -> None:
    cfg, _, out = proj_cfg
    audit_log({"event": "prompt_injection_blocked", "query": "ignore rules"}, cfg=cfg)
    close_audit_handles()
    rec = _lines(out)[0]
    assert rec["event_type"] == "permission.denied"
    assert rec["decision"] == "denied"
    assert "query" not in rec
    assert "ignore rules" not in json.dumps(rec)


def test_soul_and_mcp_mappings(proj_cfg) -> None:
    cfg, _, out = proj_cfg
    for event in ("soul_drift_detected", "soul_evolution_applied",
                  "soul_apply_injection_blocked"):
        audit_log({"event": event, "reason": "r"}, cfg=cfg)
    audit_log({"event": "mcp_rag_query", "query": "q", "retrieval_mode": "hybrid"},
              cfg=cfg)
    audit_log({"event": "mcp_rag_error", "query": "q", "error": "boom"}, cfg=cfg)
    close_audit_handles()
    records = _lines(out)
    assert [r["event_type"] for r in records] == [
        "config.agent", "config.agent", "permission.denied",
        "tool.call", "tool.result",
    ]
    mcp = records[3]
    assert mcp["mcp_server"] == "cyclaw-hybrid-rag"
    assert mcp["mcp_tool"] == "hybrid_search"
    assert mcp["tool_name"] == "hybrid_search"
    err = records[4]
    assert "boom" in err["content_preview"]


def test_model_provider_roles(proj_cfg) -> None:
    cfg, _, out = proj_cfg
    for role, expected in (("grok", "xai"), ("claude", "anthropic")):
        audit_log({"event": f"{role}_prompt_truncated", "model_used": role}, cfg=cfg)
    close_audit_handles()
    records = _lines(out)
    assert [r["model_provider"] for r in records] == ["xai", "anthropic"]
    assert all(r["event_type"] == "message.assistant" for r in records)


def test_unknown_event_low_confidence_tool_call(proj_cfg) -> None:
    cfg, _, out = proj_cfg
    audit_log({"event": "some_future_event", "detail": "x"}, cfg=cfg)
    close_audit_handles()
    rec = _lines(out)[0]
    assert rec["event_type"] == "tool.call"
    assert rec["confidence"] == "low"
    assert rec["tool_name"] == "some_future_event"
    assert "some_future_event" in rec["tags"]


def test_numbat_disabled_no_projection(proj_cfg) -> None:
    cfg, audit, out = proj_cfg
    cfg["numbat"]["enabled"] = False
    audit_log({"event": "rag_query", "query": "q"}, cfg=cfg)
    close_audit_handles()
    assert not out.exists()
    assert len(_lines(audit)) == 1  # legacy stream unaffected


def test_projection_never_raises_on_bad_record(proj_cfg) -> None:
    cfg, audit, out = proj_cfg
    # No event identity -> projection silently skips; legacy still writes.
    project_audit_record({"note": "no event key"}, cfg=cfg)
    assert not out.exists()
    # Unserializable junk inside the record must not escape either.
    project_audit_record({"event": "rag_query", "blob": object()}, cfg=cfg)
    close_audit_handles()
    records = _lines(out)
    assert len(records) == 1  # serialized via default=str fallback
    assert records[0]["event_type"] == "prompt.user"


def test_projection_fail_soft_on_disk_error(proj_cfg, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg, audit, out = proj_cfg
    import utils.numbat_emitter as emitter

    def _boom(record, path):
        raise OSError("disk full")

    monkeypatch.setattr(emitter, "write_ndjson", _boom)
    # Must not raise, and the legacy stream must still have its line.
    audit_log({"event": "rag_query", "query": "q"}, cfg=cfg)
    close_audit_handles()
    assert len(_lines(audit)) == 1


# Audit events whose own code path calls audit_log(...) AND an emit_numbat_*
# helper for the same action. Spelled out here rather than read from
# _AUDIT_ACTION_PLANE_EVENTS on purpose: deriving the cases from the constant
# under test would make this vacuous -- dropping an entry would delete its own
# test case instead of failing it.
_DUAL_EMIT_EVENTS = {
    "agentic_executor_check_result": "agentic/executor/runner.py",
    "fsconnect_read": "agentic/fsconnect/client.py::_audit",
    "sqlconnect_read": "agentic/sqlconnect/client.py::_audit_sql",
    "agentic_real_repo_change_decided": "agentic/real_repo_loop.py",
    "agentic_real_repo_change_approved": "agentic/real_repo_loop.py",
}


@pytest.mark.parametrize("event_name", sorted(_DUAL_EMIT_EVENTS))
def test_action_plane_events_are_not_double_projected(proj_cfg, event_name: str) -> None:
    """Every action-plane event's own code path already emitted directly.

    Projecting it again from the mainline audit trail would put two records
    for one action into the stream, out of order. The legacy audit line must
    still be written -- only the derived projection is suppressed.
    """
    cfg, audit, out = proj_cfg
    audit_log({"event": event_name, "op": "x"}, cfg=cfg)
    close_audit_handles()
    assert not out.exists(), (
        f"{event_name} was double-projected; it is already emitted directly by "
        f"{_DUAL_EMIT_EVENTS[event_name]}"
    )
    assert len(_lines(audit)) == 1  # authoritative stream unaffected


def test_skip_set_matches_known_dual_emitters() -> None:
    """The constant and the real emit sites must stay in step, both ways.

    Under-inclusion double-writes the stream; over-inclusion silently drops a
    mainline event that has no direct emit to fall back on.
    """
    assert _AUDIT_ACTION_PLANE_EVENTS == frozenset(_DUAL_EMIT_EVENTS)


def test_non_action_plane_event_still_projects(proj_cfg) -> None:
    """Control for the skip set: suppression must stay narrow.

    A mainline event with no direct emit of its own still has to reach the
    Numbat stream, so an over-broad skip set fails here rather than silently
    dropping the plane the projection exists to add.
    """
    cfg, _, out = proj_cfg
    audit_log({"event": "rag_query", "query": "q"}, cfg=cfg)
    close_audit_handles()
    assert len(_lines(out)) == 1


def test_audit_log_survives_projection_import_failure(
    proj_cfg, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken projection must never reach the caller.

    audit_log runs in the terminal audit_logger node (I4), after the answer is
    already computed -- the same rationale that guards record-building and the
    disk write covers the projection, including its lazy import.
    """
    import utils.numbat_emitter as emitter

    def _boom(*_args, **_kwargs):
        raise RuntimeError("projection exploded")

    monkeypatch.setattr(emitter, "project_audit_record", _boom)
    cfg, audit, _ = proj_cfg
    audit_log({"event": "rag_query", "query": "q"}, cfg=cfg)  # must not raise
    close_audit_handles()
    assert len(_lines(audit)) == 1  # answer + audit trail both survive


def test_existing_emitter_kwargs_schema_clean(proj_cfg) -> None:
    """build_event with the new context/action kwargs stays inside the
    schema allowlist and honors per-type action-field stripping."""
    from utils.numbat_emitter import build_event

    cfg, _, _ = proj_cfg
    rec = build_event(
        "tool.call",
        tool_name="hybrid_search",
        mcp_server="cyclaw-hybrid-rag",
        mcp_tool="hybrid_search",
        model="qwen3.8:27b-mlx",
        model_provider="ollama",
        entrypoint="cyclaw",
        content_preview="{}",
        cfg=cfg,
    )
    assert set(rec.keys()) <= _KNOWN_FIELDS
    # prompt.user strips ALL action fields, keeps context fields.
    rec2 = build_event(
        "prompt.user",
        tool_name="nope",
        mcp_server="nope",
        url="https://nope.invalid",
        model="m",
        model_provider="ollama",
        entrypoint="cyclaw",
        content_preview="{}",
        cfg=cfg,
    )
    assert set(rec2.keys()) <= _KNOWN_FIELDS
    assert "tool_name" not in rec2 and "mcp_server" not in rec2 and "url" not in rec2
    assert rec2["model"] == "m" and rec2["entrypoint"] == "cyclaw"
