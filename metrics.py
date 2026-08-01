"""RAG performance metrics — parses audit.jsonl.

Usage:
   python metrics.py
"""

# This process never imports gate.py, so without this it would inherit
# whatever telemetry env the operator's shell/container/observability agent
# happens to carry. Today's imports below (json/math/collections/pathlib/yaml)
# pull in no telemetry-emitting library, so this is prophylactic -- but every
# other CyClaw entry point applies the same block unconditionally rather than
# betting on what a future edit here does or doesn't import.
from utils.telemetry_kill import apply_telemetry_kill

apply_telemetry_kill()

import json  # noqa: E402 - must follow the telemetry kill above
import math  # noqa: E402 - must follow the telemetry kill above
from collections import Counter  # noqa: E402 - must follow the telemetry kill above
from pathlib import Path  # noqa: E402 - must follow the telemetry kill above

import yaml  # noqa: E402 - must follow the telemetry kill above

# Anchor config.yaml to the repo root, not the process's cwd. print_metrics's
# default config_path="config.yaml" is a bare relative name; `cyclaw-metrics`
# run from any directory other than the repo root previously crashed with
# FileNotFoundError instead of finding the real config. Mirrors
# retrieval/indexer.py::_resolve_config_path exactly -- metrics.py lives at
# the repo root itself, so parent (not parents[1]) is the anchor.
_REPO_ROOT = Path(__file__).resolve().parent


def _resolve_config_path(config_path: str = "config.yaml") -> Path:
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path.resolve()


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
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            # JSON-valid but non-object lines (null, 42, "text", []) parse fine
            # yet crash every consumer's first e.get(...) — same untrusted-file
            # posture as the JSONDecodeError skip and the top_score guard below.
            if isinstance(event, dict):
                yield event


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
            if not isinstance(event, dict):
                # A JSON-valid non-object (null, 42, "text", []) is just as
                # malformed as unparseable text for evidence purposes, and
                # `"query" in event` would TypeError on it below.
                stats["malformed_lines"] += 1
                continue
            if "query" in event:
                stats["events_with_raw_query"] += 1
            if event.get("event") in ("rag_query", "mcp_rag_query") and "query_hash" not in event:
                stats["rag_events_missing_query_hash"] += 1
    return stats


# Injection findings emitted by agentic/context.py over GitHub-sourced text.
# Declared here as literals rather than imported from agentic.context, which
# exports the same two code constants. gate.py calls summarize_audit() to serve
# GET /audit/summary, so importing agentic from this module would pull
# agentic -> guardrails -> utils.personality into the gate process. The I6
# isolation test AST-parses gate.py and only sees DIRECT imports, so that would
# stay green while the invariant's actual intent was broken. test_metrics.py
# imports both modules and asserts these match -- a test may import anything,
# this module may not.
INJECTION_EVENT = "agentic_context_injection_finding"
INJECTION_FINDING_CODE = "github_content_injection_pattern"
SCANNER_UNAVAILABLE_CODE = "github_content_scanner_unavailable"


def _bucket_key(value: object, default: str = "unknown") -> str:
    """Coerce an audit-event label to a hashable Counter key.

    event / retrieval_mode / model_used are strings in every path that writes
    audit.jsonl, but this module treats the file as untrusted evidence: one
    corrupt or hand-edited line carrying a JSON list/dict where a label belongs
    would make ``Counter[value]`` raise ``TypeError: unhashable type`` and take
    down summarize_audit -> GET /audit/summary and the cyclaw-metrics CLI for
    every caller. Anything that is not a plain string falls to the default
    bucket instead — the same posture as the top_score guard below.
    """
    return value if isinstance(value, str) else default


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
    injection_total = 0
    injection_codes: Counter = Counter()
    injection_fields: Counter = Counter()
    injection_repos: Counter = Counter()
    injection_patterns: Counter = Counter()

    for e in events:
        total += 1
        event_counts[_bucket_key(e.get("event"))] += 1

        # Folded into this loop rather than given its own function: summarize_audit
        # passes iter_events(...), a generator, so a second aggregator would either
        # receive an exhausted iterator or force a third full file pass on top of
        # compute_audit_integrity's second one. Single-pass is this function's
        # stated design (see the docstring).
        if e.get("event") == INJECTION_EVENT:
            injection_total += 1
            # Every bucket key goes through _bucket_key for the reason documented
            # there: audit.jsonl is untrusted evidence, and one hand-edited line
            # carrying a list where a label belongs would raise
            # "TypeError: unhashable type" and take down GET /audit/summary.
            injection_codes[_bucket_key(e.get("code"))] += 1
            injection_fields[_bucket_key(e.get("field"))] += 1
            injection_repos[_bucket_key(e.get("repo"))] += 1
            # patterns names which banned_patterns rule matched, never the text it
            # matched. Secondary to `code`: a pattern source containing a literal
            # dotted quad or api_key=... would itself be rewritten to
            # [REDACTED_IP]/[REDACTED_SECRET] by utils.logger's recursive redaction
            # on the way to disk, so two such rules would merge into one bucket.
            # The `code` values are fixed literals and cannot be rewritten.
            patterns = e.get("patterns")
            if isinstance(patterns, list):
                for pattern in patterns:
                    injection_patterns[_bucket_key(pattern)] += 1

        if e.get("event") in ("rag_query", "mcp_rag_query"):
            rag_query_count += 1
            # audit.jsonl is append-only evidence this module already treats as
            # untrusted (load_events skips non-JSON lines; "query" presence is
            # checked, not assumed). Extend the same posture to top_score: a
            # JSON-valid line carrying ``top_score: null`` (or a string) would
            # otherwise TypeError here and take down GET /audit/summary and the
            # cyclaw-metrics CLI. bool is excluded because it is an int subclass
            # and True would silently count as a 1.0 score.
            s = e.get("top_score")
            # isfinite excludes NaN/inf: a JSON-valid ``top_score: NaN`` would
            # flow into the average and make JSONResponse.render raise (Starlette
            # serializes with allow_nan=False), 500-ing GET /audit/summary.
            if isinstance(s, (int, float)) and not isinstance(s, bool) and math.isfinite(s):
                score_sum += s
                score_n += 1
                score_min = s if score_min is None or s < score_min else score_min
                score_max = s if score_max is None or s > score_max else score_max
            # Both graph and MCP audit paths now record the retrieval mode under
            # "retrieval_mode"; the "mode" fallback only serves audit history
            # written before the MCP server was normalized to the same key.
            mode_counts[_bucket_key(e.get("retrieval_mode") or e.get("mode"))] += 1
            # model_used is only meaningful for answered queries. Scope it to rag
            # queries so non-answer events — notably the graph audit node's
            # "user_gate_pause", which is still stamped model_used="unknown"
            # (graph.audit_logger_node) — don't pollute the model-usage breakdown
            # shown at GET /audit/summary with a bogus "unknown" bucket.
            model_used = e.get("model_used")
            if isinstance(model_used, str) and model_used:
                model_counts[model_used] += 1

        # An escalation to an external LLM (grok or claude). Prefer the explicit
        # boolean the graph audit node already records (audit_logger_node sets
        # online_escalated = answer_model in {"grok", "claude"}) as the source of
        # truth; fall back to user_confirmed_online / the model-name heuristic for
        # older or MCP events that predate the explicit field. Relying on
        # user_confirmed_online alone undercounted real escalations because the
        # graph never writes that key. The model-name heuristic checks both
        # provider prefixes so a legacy Claude event isn't missed the same way a
        # legacy Grok event wouldn't be.
        if (
            e.get("online_escalated") is True
            or e.get("user_confirmed_online") is True
            or str(e.get("model_used", "")).lower().startswith(("grok", "claude"))
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
        "injection_findings": {
            "total": injection_total,
            "by_code": dict(injection_codes.most_common()),
            "by_field": dict(injection_fields.most_common()),
            "by_repo": dict(injection_repos.most_common()),
            "by_pattern": dict(injection_patterns.most_common()),
        },
    }


def summarize_audit(audit_file: str) -> dict:
    """Summarize audit metrics and evidence-quality counters in bounded memory."""
    summary = compute_metrics(iter_events(audit_file))
    summary["audit_integrity"] = compute_audit_integrity(audit_file)
    return summary


def print_metrics(config_path: str = "config.yaml"):
    with open(_resolve_config_path(config_path), encoding="utf-8") as f:
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
    # Deliberately OUTSIDE the `if summary["rag_query_count"]` block above. These
    # findings come from the out-of-band agentic context fetchers, so the audit log
    # that contains them typically has zero RAG queries -- nesting this section
    # there would hide it on exactly the logs it exists to describe.
    # by_repo is in the summary dict for GET /audit/summary but not printed: the
    # threat model is single-operator, so the CLI would print one constant line.
    findings = summary["injection_findings"]
    if findings["total"]:
        print(f"\nGitHub content injection findings: {findings['total']}")
        for label, key in (("By code", "by_code"), ("By field", "by_field"), ("By pattern", "by_pattern")):
            if findings[key]:
                print(f"\n{label}:")
                for name, count in findings[key].items():
                    print(f"  {name}: {count}")
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
