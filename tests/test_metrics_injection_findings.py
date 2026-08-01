"""Unit tests for the injection-findings aggregation in metrics.py.

``agentic/context.py`` writes one ``agentic_context_injection_finding`` audit
event per GitHub-sourced text field that matched a governed injection pattern.
These tests pin how metrics.py reads them back: the counts, the isolation
constraint that keeps ``agentic`` out of the gate process, and the render
placement that keeps the section visible on agentic-only audit logs.
"""

import json

import yaml

from metrics import (
    INJECTION_EVENT,
    INJECTION_FINDING_CODE,
    SCANNER_UNAVAILABLE_CODE,
    compute_metrics,
    print_metrics,
    summarize_audit,
)


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


def _finding(**overrides):
    event = {
        "event": INJECTION_EVENT,
        "repo": "owner/repo",
        "number": 42,
        "field": "pr.body",
        "code": INJECTION_FINDING_CODE,
        "patterns": [r"ignore\s+(previous|all|prior)\s+instructions"],
    }
    event.update(overrides)
    return event


# --- isolation -------------------------------------------------------------


def test_metrics_does_not_import_agentic():
    """metrics.py must not import agentic, directly or transitively.

    gate.py calls summarize_audit() for GET /audit/summary, so an import here
    would drag agentic -> guardrails -> utils.personality into the gate process.
    tests/test_agentic_isolation.py AST-parses gate.py and sees only DIRECT
    imports, so it would stay green while I6's intent was broken.
    """
    import ast
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "metrics.py").read_text(encoding="utf-8")
    imported = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])
    assert "agentic" not in imported
    assert "guardrails" not in imported


def test_code_constants_match_the_producer():
    """The locally-declared literals must stay in step with agentic/context.py.

    This test may import agentic; metrics.py may not. That asymmetry is the whole
    point -- it buys drift detection without the runtime coupling.
    """
    from agentic.context import INJECTION_FINDING_CODE as producer_finding
    from agentic.context import SCANNER_UNAVAILABLE_CODE as producer_unavailable

    assert INJECTION_FINDING_CODE == producer_finding
    assert SCANNER_UNAVAILABLE_CODE == producer_unavailable


# --- aggregation -----------------------------------------------------------


def test_empty_when_no_findings():
    m = compute_metrics([{"event": "rag_query", "top_score": 0.4}])
    assert m["injection_findings"] == {
        "total": 0,
        "by_code": {},
        "by_field": {},
        "by_repo": {},
        "by_pattern": {},
    }


def test_counts_by_code_field_repo_and_pattern():
    events = [
        _finding(field="pr.body"),
        _finding(field="pr.title"),
        _finding(field="diff", repo="other/repo", patterns=["act\\s+as", "you\\s+are\\s+now"]),
        _finding(code=SCANNER_UNAVAILABLE_CODE, field=None, patterns=[]),
    ]
    f = compute_metrics(events)["injection_findings"]

    assert f["total"] == 4
    assert f["by_code"] == {INJECTION_FINDING_CODE: 3, SCANNER_UNAVAILABLE_CODE: 1}
    # field=None on a scanner-unavailable finding buckets as "unknown".
    assert f["by_field"] == {"pr.body": 1, "pr.title": 1, "diff": 1, "unknown": 1}
    assert f["by_repo"] == {"owner/repo": 3, "other/repo": 1}
    # A finding carrying two matched rules contributes to both pattern buckets.
    assert f["by_pattern"][r"ignore\s+(previous|all|prior)\s+instructions"] == 2
    assert f["by_pattern"]["act\\s+as"] == 1


def test_non_injection_events_are_ignored():
    events = [{"event": "rag_query", "code": INJECTION_FINDING_CODE, "field": "pr.body"}]
    assert compute_metrics(events)["injection_findings"]["total"] == 0


def test_findings_still_counted_in_the_event_breakdown():
    m = compute_metrics([_finding()])
    assert m["event_breakdown"][INJECTION_EVENT] == 1
    assert m["total_events"] == 1


def test_corrupt_line_does_not_raise():
    """audit.jsonl is untrusted evidence: unhashable values must bucket, not crash.

    A raw Counter[list] would raise TypeError: unhashable type and take down both
    GET /audit/summary and the cyclaw-metrics CLI.
    """
    events = [
        _finding(field=["not", "a", "string"], code={"nope": 1}, repo=None, patterns=[["nested"], 7]),
        _finding(patterns="not-a-list"),
    ]
    f = compute_metrics(events)["injection_findings"]
    assert f["total"] == 2
    assert f["by_field"]["unknown"] == 1
    assert f["by_code"]["unknown"] == 1
    # patterns="not-a-list" is skipped entirely rather than iterated per-character.
    assert f["by_pattern"] == {"unknown": 2}


def test_summarize_audit_exposes_findings_alongside_integrity(tmp_path):
    audit_file = _write_audit(tmp_path, [_finding(), {"event": "rag_query", "top_score": 0.5}])
    summary = summarize_audit(audit_file)
    assert summary["injection_findings"]["total"] == 1
    assert "audit_integrity" in summary
    # GET /audit/summary renders this with allow_nan=False -- it must round-trip.
    assert json.loads(json.dumps(summary, allow_nan=False))["injection_findings"]["total"] == 1


# --- render ----------------------------------------------------------------


def test_section_renders_on_a_log_with_no_rag_queries(tmp_path, capsys):
    """The regression this guards: an agentic-only audit log has zero RAG queries.

    Everything from "RAG queries:" to "Online escalations:" is nested under
    `if summary["rag_query_count"]`. A section placed inside that block would be
    invisible on exactly the logs that carry these findings.
    """
    audit_file = _write_audit(tmp_path, [_finding(), _finding(field="pr.title")])
    print_metrics(_write_config(tmp_path, audit_file))
    out = capsys.readouterr().out

    assert "RAG queries:" not in out
    assert "GitHub content injection findings: 2" in out
    assert "pr.body: 1" in out
    assert "pr.title: 1" in out


def test_section_absent_when_there_are_no_findings(tmp_path, capsys):
    audit_file = _write_audit(tmp_path, [{"event": "rag_query", "top_score": 0.5, "retrieval_mode": "hybrid"}])
    print_metrics(_write_config(tmp_path, audit_file))
    out = capsys.readouterr().out
    assert "GitHub content injection findings" not in out
    # The empty-guard is also what keeps test_metrics.py's `"unknown" not in out`
    # assertion green on logs that carry no findings.
    assert "unknown" not in out


def test_render_names_the_rule_not_the_matched_text(tmp_path, capsys):
    audit_file = _write_audit(tmp_path, [_finding(patterns=[r"ignore\s+safety"])])
    print_metrics(_write_config(tmp_path, audit_file))
    out = capsys.readouterr().out
    assert r"ignore\s+safety" in out
    # The producer never writes matched text to the audit log; assert the render
    # cannot surface one even if a future change started recording it.
    assert "print the API key" not in out
