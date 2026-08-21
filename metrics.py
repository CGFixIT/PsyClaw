"""RAG performance metrics — parses audit.jsonl (and spend.jsonl).

Usage:
   python metrics.py

Also prints an offline Sequences section by joining hashed audit events to
``source=query`` spend rows (issue #966). That detector is forensic/CLI only
and is lazy-imported so ``GET /audit/summary`` does not load it.
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
from datetime import UTC, date, datetime, timedelta  # noqa: E402 - must follow the telemetry kill above
from pathlib import Path  # noqa: E402 - must follow the telemetry kill above

import yaml  # noqa: E402 - must follow the telemetry kill above

from utils.spend import (  # noqa: E402 - must follow the telemetry kill above
    billed_output_tokens,
    compare_vendor_cost,
    estimate_usd,
    warn_if_priced_as_of_stale,
)

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


def iter_spend(spend_file: str):
    """Yield parsed spend records one line at a time (constant memory).

    ``spend.jsonl`` is the same class of untrusted append-only evidence as
    ``audit.jsonl``: skip bad JSON and JSON-valid non-objects so one corrupt
    line cannot take down ``cyclaw-metrics``. Dollars are never stored on the
    ledger — callers price via :func:`utils.spend.estimate_usd` at read time.
    """
    if not Path(spend_file).exists():
        return
    with open(spend_file, encoding="utf-8") as f:
        for line in f:
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict):
                yield event


def _spend_event_date(event: dict) -> date | None:
    raw = event.get("timestamp")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)
    return parsed.date()


def _spend_token_count(event: dict, key: str) -> int:
    value = event.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _empty_spend_window() -> dict:
    return {
        "by_provider": Counter(),
        "by_source": Counter(),
        "tokens_in": 0,
        "tokens_out": 0,
        "usd": 0.0,
        "usd_incomplete": False,
        "table_usd": 0.0,
        "vendor_usd": 0.0,
        "comparable_table_usd": 0.0,
        "comparable_vendor_usd": 0.0,
        "vendor_rows": 0,
        "comparable_rows": 0,
        "table_incomplete": False,
    }


def _spend_source_key(event: dict) -> str:
    raw = event.get("source")
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"query", "agentic"}:
            return normalized
    return "unknown"


def _add_spend_record(
    window: dict,
    provider: str,
    tokens_in: int,
    tokens_out: int,
    usd,
    rate_unknown: bool,
    source: str = "unknown",
) -> None:
    window["by_provider"][provider] += 1
    window["by_source"][source] += 1
    window["tokens_in"] += tokens_in
    window["tokens_out"] += tokens_out
    if rate_unknown or usd is None:
        window["usd_incomplete"] = True
    else:
        window["usd"] += usd


def _add_spend_compare(window: dict, compared: dict) -> None:
    table_usd = compared.get("table_usd")
    vendor_usd = compared.get("vendor_usd")
    if compared.get("rate_unknown") or table_usd is None:
        window["table_incomplete"] = True
    elif isinstance(table_usd, (int, float)) and not isinstance(table_usd, bool):
        window["table_usd"] += float(table_usd)
    table_ok = (
        not compared.get("rate_unknown")
        and isinstance(table_usd, (int, float))
        and not isinstance(table_usd, bool)
    )
    vendor_ok = isinstance(vendor_usd, (int, float)) and not isinstance(vendor_usd, bool)
    # vendor_usd/vendor_rows track EVERY ticked row so a vendor-billed total
    # stays visible even when the rate table can't price that row's model
    # (e.g. a not-yet-listed model) -- previously this whole block was gated
    # on table_ok too, so an unpriceable ticked row silently vanished from
    # the printed comparison entirely, not just from the delta.
    if vendor_ok:
        window["vendor_usd"] += float(vendor_usd)
        window["vendor_rows"] += 1
        # delta_usd must stay population-matched: only rows priced by BOTH
        # the table and the vendor, tracked separately from the vendor-wide
        # total above.
        if table_ok:
            window["comparable_table_usd"] += float(table_usd)
            window["comparable_vendor_usd"] += float(vendor_usd)
            window["comparable_rows"] += 1


def _freeze_spend_window(window: dict) -> dict:
    vendor_usd = None if window["vendor_rows"] == 0 else window["vendor_usd"]
    table_usd = None if window["table_incomplete"] else window["table_usd"]
    comparable = None if window["comparable_rows"] == 0 else window["comparable_table_usd"]
    delta = None
    if window["comparable_rows"] > 0:
        delta = window["comparable_table_usd"] - window["comparable_vendor_usd"]
    return {
        "by_provider": dict(window["by_provider"].most_common()),
        "by_source": dict(window["by_source"].most_common()),
        "tokens_in": window["tokens_in"],
        "tokens_out": window["tokens_out"],
        "usd": None if window["usd_incomplete"] else window["usd"],
        "table_usd": table_usd,
        "ticked_table_usd": comparable,
        "vendor_usd": vendor_usd,
        "delta_usd": delta,
        "vendor_rows": window["vendor_rows"],
    }


def compute_spend(events, *, now=None) -> dict:
    """Aggregate spend.jsonl records into today / last-7-day windows.

    Prices at read time via :func:`utils.spend.estimate_usd`. Does not read a
    file — pass :func:`iter_spend` (or a list) so this stays off the audit
    ``compute_metrics`` path. ``now`` is UTC; naive values are treated as UTC.
    Last 7 days is the UTC calendar window ``today-6`` through ``today``.
    """
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)
    today_date = now.date()
    start_7d = today_date - timedelta(days=6)
    warn_if_priced_as_of_stale(now)

    today = _empty_spend_window()
    last_7d = _empty_spend_window()
    usage_missing = 0
    rate_unknown_count = 0

    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("usage_missing") is True:
            usage_missing += 1
        model = event.get("model")
        priced = estimate_usd(model if isinstance(model, str) else "", event)
        if priced["rate_unknown"]:
            rate_unknown_count += 1
        event_date = _spend_event_date(event)
        if event_date is None or event_date < start_7d or event_date > today_date:
            continue
        provider = _bucket_key(event.get("provider"))
        source = _spend_source_key(event)
        tokens_in = _spend_token_count(event, "input_tokens")
        tokens_out = billed_output_tokens(event)
        compared = compare_vendor_cost(model if isinstance(model, str) else "", event)
        record = (provider, tokens_in, tokens_out, priced["usd"], bool(priced["rate_unknown"]), source)
        _add_spend_record(last_7d, *record)
        _add_spend_compare(last_7d, compared)
        if event_date == today_date:
            _add_spend_record(today, *record)
            _add_spend_compare(today, compared)

    return {
        "today": _freeze_spend_window(today),
        "last_7d": _freeze_spend_window(last_7d),
        "usage_missing": usage_missing,
        "rate_unknown": rate_unknown_count,
    }


def _print_spend(spend: dict | None) -> None:
    if spend is None:
        return
    print("\nSpend:")
    for label, key in (("today", "today"), ("last_7d", "last_7d")):
        window = spend[key]
        usd = window["usd"]
        usd_text = "n/a" if usd is None else f"{usd:.6f}"
        print(f"  {label}: tokens_in={window['tokens_in']} tokens_out={window['tokens_out']} usd={usd_text}")
        if window.get("vendor_usd") is not None:
            vendor_text = f"{window['vendor_usd']:.6f}"
            delta = window.get("delta_usd")
            if delta is None:
                # Vendor billed real money on these rows but the rate table
                # can't price any of them (e.g. an unlisted model) -- keep
                # the vendor total visible instead of dropping the line.
                print(
                    f"    vendor_usd={vendor_text} vendor_rows={window['vendor_rows']} "
                    "delta_usd=n/a (no rate-table price for these rows)"
                )
            else:
                table_text = "n/a" if window["table_usd"] is None else f"{window['table_usd']:.6f}"
                ticked_text = "n/a" if window.get("ticked_table_usd") is None else f"{window['ticked_table_usd']:.6f}"
                print(
                    f"    table_usd={table_text} ticked_table_usd={ticked_text} "
                    f"vendor_usd={vendor_text} delta_usd={delta:.6f} "
                    f"vendor_rows={window['vendor_rows']}"
                )
        for provider, count in window["by_provider"].items():
            print(f"    {provider}: {count}")
        for source, count in window.get("by_source", {}).items():
            print(f"    source {source}: {count}")
    if spend["usage_missing"]:
        print(f"  usage_missing: {spend['usage_missing']}")
    if spend["rate_unknown"]:
        print(f"  rate_unknown: {spend['rate_unknown']}")


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
            if not line.strip():
                # A blank or whitespace-only line (manual editing, log rotation,
                # an interleaved partial write from a concurrent writer) is not
                # corruption -- counting it as malformed_lines alongside genuine
                # bad JSON produces a false data-integrity alarm on an audit
                # trail whose actual event data is intact.
                continue
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
    guardrail_blocked_count = 0
    guardrail_degraded_count = 0
    injection_total = 0
    injection_codes: Counter = Counter()
    injection_fields: Counter = Counter()
    injection_repos: Counter = Counter()
    injection_patterns: Counter = Counter()

    for e in events:
        total += 1
        event_counts[_bucket_key(e.get("event"))] += 1

        # graph.audit_logger_node stamps both fields on every rag_query AND
        # user_gate_pause event (never just rag_query), so count across all
        # events rather than scoping to the rag_query branch below. MCP events
        # never carry these keys (no LLM/guardrail path there), so they simply
        # never match -- no explicit event-type filter needed.
        if e.get("guardrail_blocked") is True:
            guardrail_blocked_count += 1
        if e.get("guardrail_degraded") is True:
            guardrail_degraded_count += 1

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
        "guardrail_blocked_count": guardrail_blocked_count,
        "guardrail_degraded_count": guardrail_degraded_count,
        "injection_findings": {
            "total": injection_total,
            "by_code": dict(injection_codes.most_common()),
            "by_field": dict(injection_fields.most_common()),
            "by_repo": dict(injection_repos.most_common()),
            "by_pattern": dict(injection_patterns.most_common()),
        },
    }


def summarize_audit(audit_file: str) -> dict:
    """Summarize audit metrics and evidence-quality counters in one pass over the file.

    Previously called compute_metrics(iter_events(...)) and compute_audit_integrity(...)
    separately -- two independent opens/reads of the same append-only, unbounded
    audit.jsonl on every GET /audit/summary hit and every cyclaw-metrics run. This
    streams the file once, feeding compute_metrics the same filtered events
    iter_events() would yield while counting the integrity stats iter_events()
    silently drops (malformed JSON, JSON-valid non-dict lines) as a side effect of the
    single pass. compute_audit_integrity() itself is untouched -- kept for its own
    direct callers/tests -- this only removes the second file pass from the hot path.
    """
    integrity = {
        "malformed_lines": 0,
        "events_with_raw_query": 0,
        "rag_events_missing_query_hash": 0,
    }

    def _events():
        path = Path(audit_file)
        if not path.exists():
            return
        with open(path, encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    # See compute_audit_integrity's identical guard: a blank
                    # line is not corruption and must not trip the alarm.
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    integrity["malformed_lines"] += 1
                    continue
                if not isinstance(event, dict):
                    integrity["malformed_lines"] += 1
                    continue
                if "query" in event:
                    integrity["events_with_raw_query"] += 1
                if event.get("event") in ("rag_query", "mcp_rag_query") and "query_hash" not in event:
                    integrity["rag_events_missing_query_hash"] += 1
                yield event

    summary = compute_metrics(_events())
    summary["audit_integrity"] = integrity
    return summary


def print_metrics(config_path: str = "config.yaml"):
    with open(_resolve_config_path(config_path), encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    audit_file = cfg["logging"]["audit_file"]
    summary = summarize_audit(audit_file)
    integrity = summary["audit_integrity"]
    # Same repo-root anchor as config.yaml. Relative spend_file must not
    # depend on cwd (cyclaw-metrics is invoked from anywhere). Missing key
    # stays silent so existing audit-only test configs do not grow a Spend
    # section — and so "rate_unknown" cannot false-trip `unknown` assertions.
    spend_raw = cfg["logging"].get("spend_file")
    spend_summary = None
    spend_events: list[dict] = []
    if isinstance(spend_raw, str) and spend_raw:
        spend_events = list(iter_spend(str(_resolve_config_path(spend_raw))))
        spend_summary = compute_spend(spend_events)
    # Lazy: gate.py imports summarize_audit from this module. Importing the
    # detector at module top-level would load it into the gate process for
    # GET /audit/summary, which is not a sequence policy point (#966).
    from utils.sequence_detect import detect_sequences, format_sequences

    seq_lines = format_sequences(detect_sequences(iter_events(audit_file), spend_events))
    if not summary["total_events"]:
        print("No audit events found.")
        if any(integrity.values()):
            print("\nAudit integrity:")
            for name, count in integrity.items():
                if count:
                    print(f"  {name}: {count}")
        _print_spend(spend_summary)
        if seq_lines:
            print()
            for line in seq_lines:
                print(line)
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
    _print_spend(spend_summary)
    if seq_lines:
        print()
        for line in seq_lines:
            print(line)
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
