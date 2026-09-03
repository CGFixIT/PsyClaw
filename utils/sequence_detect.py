"""Offline sequence detection over joined audit.jsonl + spend.jsonl.

Forensic / CLI only. ``gate.py``, ``graph.py``, and the MCP server must not
import this module — it is not a policy decision point on the ``/query`` path
(issue #966). Join key is the unsalted SHA-256 ``query_hash`` (content
address, not request identity). Spend rows are restricted to ``source ==
"query"``; agentic ledger lines are counted and dropped so the two planes
never mix.

Findings carry hashes, event names, timestamps, and provider/model tags.
They never copy query text, IPs, soul content, or secrets.

The mixed-hash ``window_injection_to_escalation`` rule assumes CyClaw's
shipped loopback single-operator threat model: a 15-minute window on this
host is the operator's own sequence. It is not an actor id.
"""

from __future__ import annotations

import bisect
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

_QUERY_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_DEFAULT_WINDOW = timedelta(minutes=15)
_DEFAULT_MIN_REPEAT = 3
_INJECTION_EVENTS = frozenset(
    {
        "prompt_injection_blocked",
        "memory_apply_injection_blocked",
        "soul_apply_injection_blocked",
    }
)
_RAG_EVENTS = frozenset({"rag_query", "mcp_rag_query"})
_ONLINE_MODELS = frozenset({"grok", "claude"})
_SAFE_KEYS = (
    "event",
    "timestamp",
    "query_hash",
    "model_used",
    "online_escalated",
    "pre_action_hook_denied",
    "provider",
    "model",
    "source",
)


def _normalized_hash(value: object) -> str | None:
    if isinstance(value, str) and _QUERY_HASH_RE.fullmatch(value):
        return value
    return None


def _parse_ts(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _safe_event(row: Mapping[str, Any], *, kind: str) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind}
    for key in _SAFE_KEYS:
        if key in row:
            out[key] = row[key]
    return out


def _is_online_rag(row: Mapping[str, Any]) -> bool:
    if row.get("event") not in _RAG_EVENTS:
        return False
    if row.get("online_escalated") is True:
        return True
    model = row.get("model_used")
    if not isinstance(model, str):
        return False
    lowered = model.strip().lower()
    return lowered in _ONLINE_MODELS or lowered.startswith("grok") or lowered.startswith("claude")


def _is_injection(row: Mapping[str, Any]) -> bool:
    return row.get("event") in _INJECTION_EVENTS


def _is_hook_denied(row: Mapping[str, Any]) -> bool:
    return row.get("pre_action_hook_denied") is True


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts is not None else None


def _finding(
    rule: str,
    *,
    query_hash: str | None,
    events: list[dict[str, Any]],
    start: datetime | None,
    end: datetime | None,
) -> dict[str, Any]:
    return {
        "rule": rule,
        "query_hash": query_hash,
        "window_start": _iso(start),
        "window_end": _iso(end),
        "count": len(events),
        "events": events,
    }


def detect_sequences(
    audit_events: Iterable[object],
    spend_events: Iterable[object],
    *,
    window: timedelta = _DEFAULT_WINDOW,
    min_repeat: int = _DEFAULT_MIN_REPEAT,
) -> dict[str, Any]:
    """Join hashed audit events with ``source=query`` spend and emit sequences."""
    audit: list[tuple[datetime, str | None, dict[str, Any]]] = []
    for raw in audit_events:
        if not isinstance(raw, dict):
            continue
        ts = _parse_ts(raw.get("timestamp"))
        if ts is None:
            continue
        hashed = _normalized_hash(raw.get("query_hash"))
        audit.append((ts, hashed, dict(raw)))

    spend: list[tuple[datetime, str | None, dict[str, Any]]] = []
    agentic_skipped = 0
    unjoinable = 0
    for raw in spend_events:
        if not isinstance(raw, dict):
            continue
        source = raw.get("source")
        if isinstance(source, str) and source.strip().lower() == "agentic":
            agentic_skipped += 1
            continue
        if not (isinstance(source, str) and source.strip().lower() == "query"):
            continue
        ts = _parse_ts(raw.get("timestamp"))
        hashed = _normalized_hash(raw.get("query_hash"))
        if hashed is None:
            unjoinable += 1
            continue
        if ts is None:
            continue
        spend.append((ts, hashed, dict(raw)))

    by_hash_audit: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for ts, hashed, row in audit:
        if hashed is None:
            continue
        by_hash_audit.setdefault(hashed, []).append((ts, row))
    by_hash_spend: dict[str, list[tuple[datetime, dict[str, Any]]]] = {}
    for ts, hashed, row in spend:
        if hashed is None:  # pragma: no cover -- spend rows are appended only after hash filter
            continue
        by_hash_spend.setdefault(hashed, []).append((ts, row))

    findings: list[dict[str, Any]] = []
    all_hashes = set(by_hash_audit) | set(by_hash_spend)
    for hashed in sorted(all_hashes):
        a_rows = sorted(by_hash_audit.get(hashed, []), key=lambda item: item[0])
        s_rows = sorted(by_hash_spend.get(hashed, []), key=lambda item: item[0])
        if len(a_rows) >= min_repeat:
            events = [_safe_event(row, kind="audit") for _, row in a_rows]
            findings.append(
                _finding(
                    "repeat_hash",
                    query_hash=hashed,
                    events=events,
                    start=a_rows[0][0],
                    end=a_rows[-1][0],
                )
            )
        injections = [(ts, row) for ts, row in a_rows if _is_injection(row)]
        online = [(ts, row) for ts, row in a_rows if _is_online_rag(row)]
        denied = [(ts, row) for ts, row in a_rows if _is_hook_denied(row)]
        if injections and online:
            first_inj = injections[0][0]
            later = [(ts, row) for ts, row in online if ts > first_inj]
            if later:
                chain = [_safe_event(injections[0][1], kind="audit"), _safe_event(later[0][1], kind="audit")]
                findings.append(
                    _finding(
                        "injection_then_online_rag",
                        query_hash=hashed,
                        events=chain,
                        start=injections[0][0],
                        end=later[0][0],
                    )
                )
        if injections and s_rows:
            first_inj = injections[0][0]
            later_spend = [(ts, row) for ts, row in s_rows if ts > first_inj]
            if later_spend:
                chain = [
                    _safe_event(injections[0][1], kind="audit"),
                    _safe_event(later_spend[0][1], kind="spend"),
                ]
                findings.append(
                    _finding(
                        "injection_then_external_spend",
                        query_hash=hashed,
                        events=chain,
                        start=injections[0][0],
                        end=later_spend[0][0],
                    )
                )
        if denied and s_rows:
            first_denied = denied[0][0]
            later_spend = [(ts, row) for ts, row in s_rows if ts > first_denied]
            if later_spend:
                chain = [
                    _safe_event(denied[0][1], kind="audit"),
                    _safe_event(later_spend[0][1], kind="spend"),
                ]
                findings.append(
                    _finding(
                        "hook_denied_then_spend",
                        query_hash=hashed,
                        events=chain,
                        start=denied[0][0],
                        end=later_spend[0][0],
                    )
                )

    injections_all = [(ts, hashed, row) for ts, hashed, row in audit if hashed is not None and _is_injection(row)]
    escalations: list[tuple[datetime, str, dict[str, Any], str]] = []
    for ts, hashed, row in audit:
        if hashed is None or not _is_online_rag(row):
            continue
        escalations.append((ts, hashed, row, "audit"))
    for ts, hashed, row in spend:
        if hashed is None:  # pragma: no cover -- spend rows are appended only after hash filter
            continue
        escalations.append((ts, hashed, row, "spend"))
    escalations.sort(key=lambda item: item[0])
    # escalations is sorted ascending by timestamp, so for a given injection
    # every candidate with esc_ts <= inj_ts is a fixed prefix -- bisect_right
    # locates its end in O(log E) instead of walking past it one entry at a
    # time for every injection (this ran as O(injections * escalations) over
    # an unbounded, append-only audit.jsonl history).
    escalation_timestamps = [item[0] for item in escalations]
    for inj_ts, inj_hash, inj_row in injections_all:
        match: tuple[datetime, str, dict[str, Any], str] | None = None
        start_idx = bisect.bisect_right(escalation_timestamps, inj_ts)
        for idx in range(start_idx, len(escalations)):
            esc_ts, esc_hash, esc_row, kind = escalations[idx]
            if esc_hash == inj_hash:
                continue
            if esc_ts - inj_ts > window:
                # Also sort-order-safe to stop here: every later entry is
                # further outside the window too, so scanning the remainder
                # of the list would find nothing.
                break
            match = (esc_ts, esc_hash, esc_row, kind)
            break
        if match is None:
            continue
        esc_ts, _esc_hash, esc_row, kind = match
        findings.append(
            _finding(
                "window_injection_to_escalation",
                query_hash=None,
                events=[_safe_event(inj_row, kind="audit"), _safe_event(esc_row, kind=kind)],
                start=inj_ts,
                end=esc_ts,
            )
        )

    if unjoinable:
        findings.append(
            _finding(
                "unjoinable_query_spend",
                query_hash=None,
                events=[],
                start=None,
                end=None,
            )
        )
        findings[-1]["count"] = unjoinable

    findings.sort(key=lambda item: (item["window_start"] or "", item["rule"], item["query_hash"] or ""))
    return {
        "findings": findings,
        "agentic_spend_skipped": agentic_skipped,
        "unjoinable_query_spend": unjoinable,
    }


def format_sequences(result: Mapping[str, Any]) -> list[str]:
    """CLI lines for ``cyclaw-metrics``. Empty when there is nothing to report."""
    findings = result.get("findings") or []
    skipped = int(result.get("agentic_spend_skipped") or 0)
    if not findings:
        return []
    lines = ["Sequences:"]
    by_rule: dict[str, list[Mapping[str, Any]]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        rule = str(finding.get("rule") or "unknown")
        by_rule.setdefault(rule, []).append(finding)
    for rule in sorted(by_rule):
        group = by_rule[rule]
        lines.append(f"  {rule}: {len(group)}")
        for finding in group:
            hashed = finding.get("query_hash")
            shown = f"{hashed[:12]}…" if isinstance(hashed, str) and len(hashed) >= 12 else "(no hash)"
            start = finding.get("window_start") or "?"
            end = finding.get("window_end") or "?"
            count = finding.get("count") or 0
            if start == "?" and end == "?":
                lines.append(f"    {shown}  count={count}")
            else:
                lines.append(f"    {shown}  count={count}  {start} -> {end}")
    if skipped:
        lines.append(f"  agentic_spend_skipped: {skipped}")
    return lines
