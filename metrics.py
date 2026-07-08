"""RAG performance metrics — parses audit.jsonl.

Usage:
   python metrics.py
"""

import json
from collections import Counter
from pathlib import Path

import yaml


def iter_events(audit_file: str):
    """Yield parsed audit events one line at a time (constant memory).

    ``audit.jsonl`` is append-only and unbounded; streaming keeps
    ``GET /audit/summary`` and the ``cyclaw-metrics`` CLI at O(1) memory as
    history grows instead of materializing the whole file.
    """
    if not Path(audit_file).exists():
        return
    with open(audit_file, encoding="utf-8") as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                pass


def load_events(audit_file: str):
    """Materialized list form of :func:`iter_events` (kept for existing callers)."""
    return list(iter_events(audit_file))


def compute_audit_integrity(audit_file: str) -> dict:
    """Count audit-log issues that weaken evidence quality without exposing data."""
    stats = {
        "malformed_lines": 0,
        "events_with_raw_query": 0,
        "rag_events_missing_query_hash": 0,
    }
    path = Path(audit_file)
    if not path.exists():
        return stats
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                stats["malformed_lines"] += 1
                continue
            if "query" in event:
                stats["events_with_raw_query"] += 1
            if event.get("event") in ("rag_query", "mcp_rag_query") and "query_hash" not in event:
                stats["rag_events_missing_query_hash"] += 1
    return stats


def compute_metrics(events) -> dict:
    """Aggregate audit events into a JSON-serializable summary.

    Accepts any iterable of event dicts (list or generator) and aggregates in a
    single pass — previously this made ~5 separate passes over a fully
    materialized list, so cost and memory grew with audit history.

    Returns aggregates only — never raw query text. The audit log stores
    SHA-256 query hashes (not plaintext) by design, so this summary is safe to
    expose over the API-key-gated ``GET /audit/summary`` endpoint for regulated
    SMBs that need audit evidence (query volume, external-LLM usage, score
    distribution) without leaking the underlying queries.
    """
    total = 0
    event_counts: Counter = Counter()
    rag_query_count = 0
    score_sum = 0.0
    score_n = 0
    score_min: float | None = None
    score_max: float | None = None
    mode_counts: Counter = Counter()
    model_counts: Counter = Counter()
    online_escalated = 0

    for e in events:
        total += 1
        event_counts[e.get("event", "unknown")] += 1

        if e.get("event") in ("rag_query", "mcp_rag_query"):
            rag_query_count += 1
            if "top_score" in e:
                s = e["top_score"]
                score_sum += s
                score_n += 1
                score_min = s if score_min is None or s < score_min else score_min
                score_max = s if score_max is None or s > score_max else score_max
            # The graph audit path records the retrieval mode under "retrieval_mode";
            # the MCP server (mcp_hybrid_server._handle_search) records it under "mode".
            # Reading only "retrieval_mode" silently bucketed every mcp_rag_query as
            # "unknown" even though its mode was right there under the other key.
            mode_counts[e.get("retrieval_mode") or e.get("mode") or "unknown"] += 1
            # model_used is only meaningful for answered queries. Scope it to rag
            # queries so non-answer events — notably the graph audit node's
            # "user_gate_pause", which is still stamped model_used="unknown"
            # (graph.audit_logger_node) — don't pollute the model-usage breakdown
            # shown at GET /audit/summary with a bogus "unknown" bucket.
            if e.get("model_used"):
                model_counts[e["model_used"]] += 1

        # An escalation to the external LLM. Prefer the explicit boolean the graph
        # audit node already records (audit_logger_node sets
        # online_escalated = answer_model == "grok") as the source of truth; fall back
        # to user_confirmed_online / the model-name heuristic for older or MCP events
        # that predate the explicit field. Relying on user_confirmed_online alone
        # undercounted real escalations because the graph never writes that key.
        if (
            e.get("online_escalated") is True
            or e.get("user_confirmed_online") is True
            or str(e.get("model_used", "")).lower().startswith("grok")
        ):
            online_escalated += 1

    return {
        "total_events": total,
        "event_breakdown": dict(event_counts.most_common()),
        "rag_query_count": rag_query_count,
        "scores": (
            {"avg": score_sum / score_n, "min": score_min, "max": score_max}
            if score_n
            else {"avg": None, "min": None, "max": None}
        ),
        "retrieval_modes": dict(mode_counts.most_common()),
        "model_used": dict(model_counts.most_common()),
        "online_escalated": online_escalated,
    }


def summarize_audit(audit_file: str) -> dict:
    """Summarize audit metrics and evidence-quality counters in bounded memory."""
    summary = compute_metrics(iter_events(audit_file))
    summary["audit_integrity"] = compute_audit_integrity(audit_file)
    return summary


def print_metrics(config_path: str = "config.yaml"):
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    audit_file = cfg["logging"]["audit_file"]
    summary = summarize_audit(audit_file)
    integrity = summary["audit_integrity"]
    if not summary["total_events"]:
        print("No audit events found.")
        if any(integrity.values()):
            print("\nAudit integrity:")
            for name, count in integrity.items():
                if count:
                    print(f"  {name}: {count}")
        return
    print(f"Total events: {summary['total_events']}")
    print("\nEvent breakdown:")
    for event, count in summary["event_breakdown"].items():
        print(f"  {event}: {count}")
    if summary["rag_query_count"]:
        print(f"\nRAG queries: {summary['rag_query_count']}")
        s = summary["scores"]
        if s["avg"] is not None:
            print(f"\nRAG scores — avg: {s['avg']:.3f}, min: {s['min']:.3f}, max: {s['max']:.3f}")
        if summary["retrieval_modes"]:
            print("\nRetrieval modes:")
            for mode, count in summary["retrieval_modes"].items():
                print(f"  {mode}: {count}")
        # model_used and online_escalated are computed by compute_metrics() and
        # surfaced at GET /audit/summary, but the CLI dropped them on the floor.
        # Print them so `cyclaw-metrics` shows which model answered and how many
        # queries escalated to the external (paid) LLM.
        if summary["model_used"]:
            print("\nModel used:")
            for model, count in summary["model_used"].items():
                print(f"  {model}: {count}")
        print(f"\nOnline escalations (external LLM): {summary['online_escalated']}")
    if any(integrity.values()):
        print("\nAudit integrity:")
        for name, count in integrity.items():
            if count:
                print(f"  {name}: {count}")

def main() -> None:
    """Console entry point for ``cyclaw-metrics`` (see pyproject [project.scripts]).

    Thin wrapper over :func:`print_metrics`. The declared
    ``cyclaw-metrics = "metrics:main"`` script previously raised AttributeError
    because this module only defined ``print_metrics``, not ``main``.
    """
    print_metrics()


if __name__ == "__main__":
    main()
