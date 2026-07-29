"""Detailed, self-contained guardrail metrics -- separate from metrics.py.

By design this layer writes its own JSONL stream (default ``logs/guardrails.jsonl``)
and ships its own analyzer, so the existing ``metrics.py`` / ``GET /audit/summary``
surface is left completely untouched. The two streams can be cross-referenced
later by ``query_hash`` (both use the same ``utils.logger.hash_query``), but the
guardrails stream is the authoritative source for:

  * agentic / tool-call activity            -> event "tool_call"
  * blocked generations (input or output)   -> event "blocked_generation"
  * logged hallucinations (ungrounded)      -> event "hallucination_flagged"
  * individual rail firings                  -> event "rail_triggered"
  * allowed / skipped generations           -> events "generation_allowed" / "guardrail_skipped"

Raw query/answer text is NEVER written -- only SHA-256 hashes, mirroring the
audit log's privacy posture. This module is out-of-band and never imported by
gate.py, graph.py, or mcp_hybrid_server.py.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from utils.logger import hash_query

logger = logging.getLogger("cyclaw.guardrails.metrics")

# Canonical event types. Kept as constants so producers and the analyzer agree.
EVENT_TOOL_CALL = "tool_call"
EVENT_BLOCKED = "blocked_generation"
EVENT_HALLUCINATION = "hallucination_flagged"
EVENT_RAIL_TRIGGERED = "rail_triggered"
EVENT_ALLOWED = "generation_allowed"
EVENT_SKIPPED = "guardrail_skipped"
EVENT_SOUL_TOPIC = "soul_topic"


class GuardrailMetrics:
    """Append-only recorder for guardrail events.

    Holds lightweight in-memory counters (handy for a single CLI run or test)
    and, unless ``persist=False``, appends one JSONL line per event to
    ``metrics_path``. Construction never touches the disk; the parent directory
    is created lazily on first write.
    """

    def __init__(self, metrics_path: str | Path = "logs/guardrails.jsonl", *, persist: bool = True) -> None:
        self.metrics_path = Path(metrics_path)
        self.persist = persist
        self.counters: Counter[str] = Counter()
        self.rails_fired: Counter[str] = Counter()
        self.tools_called: Counter[str] = Counter()

    # --- Recording --------------------------------------------------------

    def _record(self, event: str, *, query: str | None = None, **fields: Any) -> dict:
        record: dict[str, Any] = {"event": event, **fields}
        if query is not None:
            record["query_hash"] = hash_query(query)
        record["timestamp"] = datetime.now(UTC).isoformat()
        self.counters[event] += 1
        if self.persist:
            try:
                self.metrics_path.parent.mkdir(parents=True, exist_ok=True)
                with open(self.metrics_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record) + "\n")
            except (OSError, TypeError, ValueError, RecursionError) as exc:
                # Metrics are telemetry, never policy. Letting a disk-full or
                # serialization failure escape causes graph.guardrail_input_node
                # to fail open and discard an already-computed block verdict.
                # Log only the exception class; never paths, fields, or queries.
                logger.warning(
                    "Guardrail metrics persistence skipped (%s)",
                    type(exc).__name__,
                )
        return record

    def record_tool_call(self, tool: str, *, ok: bool = True, query: str | None = None, **fields: Any) -> dict:
        """Record an agentic / external tool invocation."""
        self.tools_called[tool] += 1
        return self._record(EVENT_TOOL_CALL, tool=tool, ok=ok, query=query, **fields)

    def record_blocked(self, *, stage: str, rail: str | None = None, reason: str = "",
                       query: str | None = None, **fields: Any) -> dict:
        """Record a generation blocked by an input or output rail.

        ``stage`` is "input" or "output"; ``rail`` is the firing rail's name.
        """
        if rail:
            self.rails_fired[rail] += 1
        return self._record(EVENT_BLOCKED, stage=stage, rail=rail, reason=reason, query=query, **fields)

    def record_hallucination(self, *, score: float, threshold: float,
                            query: str | None = None, **fields: Any) -> dict:
        """Record an answer flagged as a likely hallucination (ungrounded)."""
        return self._record(
            EVENT_HALLUCINATION, grounding_score=score, threshold=threshold, query=query, **fields
        )

    def record_rail(self, rail: str, *, stage: str = "", query: str | None = None, **fields: Any) -> dict:
        """Record an individual rail firing (not necessarily a block)."""
        self.rails_fired[rail] += 1
        return self._record(EVENT_RAIL_TRIGGERED, rail=rail, stage=stage, query=query, **fields)

    def record_allowed(self, *, score: float | None = None, query: str | None = None, **fields: Any) -> dict:
        """Record a generation that passed all rails."""
        return self._record(EVENT_ALLOWED, grounding_score=score, query=query, **fields)

    def record_skipped(self, *, reason: str, query: str | None = None, **fields: Any) -> dict:
        """Record a turn where guardrails did not run (disabled / dep missing)."""
        return self._record(EVENT_SKIPPED, reason=reason, query=query, **fields)

    def record_soul_topic(self, *, query: str | None = None, **fields: Any) -> dict:
        """Record that a turn touched the soul / personality topic class."""
        return self._record(EVENT_SOUL_TOPIC, query=query, **fields)


# --- Analyzer (standalone -- does not import or call metrics.py) -------------


def load_events(metrics_path: str | Path) -> list[dict]:
    events: list[dict] = []
    path = Path(metrics_path)
    if not path.exists():
        return events
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # Gracefully skip malformed JSONL lines to allow partial metrics
                # analysis even when the stream contains bad entries.
                continue
            if isinstance(event, dict):
                events.append(event)
    return events


def compute_guardrail_metrics(events: list[dict]) -> dict:
    """Aggregate guardrail events into a JSON-serializable summary.

    Aggregates only -- never raw text (the stream stores hashes). Safe to surface
    in an operator dashboard or a future, separately-gated endpoint.
    """
    summary: dict[str, Any] = {
        "total_events": len(events),
        "event_breakdown": {},
        "tool_calls": 0,
        "tool_call_failures": 0,
        "tools_by_name": {},
        "blocked_generations": 0,
        "blocks_by_stage": {},
        "hallucinations_flagged": 0,
        "rails_triggered": 0,
        "rails_by_name": {},
        "soul_topic_hits": 0,
        "generations_allowed": 0,
        "guardrail_skipped": 0,
        "grounding": {"avg": None, "min": None, "max": None},
        "block_rate": None,
    }
    if not events:
        return summary

    summary["event_breakdown"] = dict(Counter(e.get("event", "unknown") for e in events).most_common())

    tool_events = [e for e in events if e.get("event") == EVENT_TOOL_CALL]
    summary["tool_calls"] = len(tool_events)
    summary["tool_call_failures"] = sum(1 for e in tool_events if e.get("ok") is False)
    summary["tools_by_name"] = dict(
        Counter(e.get("tool", "unknown") for e in tool_events).most_common()
    )

    blocked = [e for e in events if e.get("event") == EVENT_BLOCKED]
    summary["blocked_generations"] = len(blocked)
    summary["blocks_by_stage"] = dict(
        Counter(e.get("stage", "unknown") for e in blocked).most_common()
    )

    summary["hallucinations_flagged"] = sum(1 for e in events if e.get("event") == EVENT_HALLUCINATION)
    summary["soul_topic_hits"] = sum(1 for e in events if e.get("event") == EVENT_SOUL_TOPIC)
    summary["generations_allowed"] = sum(1 for e in events if e.get("event") == EVENT_ALLOWED)
    summary["guardrail_skipped"] = sum(1 for e in events if e.get("event") == EVENT_SKIPPED)

    # Rail firings come from explicit rail_triggered events AND the rail field on
    # block events (a block is itself a rail firing).
    rail_names = [e.get("rail") for e in events if e.get("event") == EVENT_RAIL_TRIGGERED and e.get("rail")]
    rail_names += [e.get("rail") for e in blocked if e.get("rail")]
    summary["rails_triggered"] = len(rail_names)
    summary["rails_by_name"] = dict(Counter(rail_names).most_common())

    scores = [
        e["grounding_score"]
        for e in events
        if isinstance(e.get("grounding_score"), (int, float))
    ]
    if scores:
        summary["grounding"] = {
            "avg": sum(scores) / len(scores),
            "min": min(scores),
            "max": max(scores),
        }

    # Block rate over decided generations (allowed + blocked); skipped turns and
    # tool calls are excluded from the denominator.
    decided = summary["generations_allowed"] + summary["blocked_generations"]
    if decided:
        summary["block_rate"] = summary["blocked_generations"] / decided

    return summary


def print_metrics(metrics_path: str | Path = "logs/guardrails.jsonl") -> None:
    events = load_events(metrics_path)
    if not events:
        print(f"No guardrail events found at {metrics_path}.")
        return
    s = compute_guardrail_metrics(events)
    print(f"Total guardrail events: {s['total_events']}")
    print("\nEvent breakdown:")
    for event, count in s["event_breakdown"].items():
        print(f"  {event}: {count}")
    print(f"\nTool calls: {s['tool_calls']} (failures: {s['tool_call_failures']})")
    if s["tools_by_name"]:
        for tool, count in s["tools_by_name"].items():
            print(f"  {tool}: {count}")
    print(f"\nBlocked generations: {s['blocked_generations']}  (stages: {s['blocks_by_stage']})")
    print(f"Hallucinations flagged: {s['hallucinations_flagged']}")
    print(f"Soul-topic hits: {s['soul_topic_hits']}")
    print(f"Rails triggered: {s['rails_triggered']}")
    if s["rails_by_name"]:
        for rail, count in s["rails_by_name"].items():
            print(f"  {rail}: {count}")
    g = s["grounding"]
    if g["avg"] is not None:
        print(f"\nGrounding score — avg: {g['avg']:.3f}, min: {g['min']:.3f}, max: {g['max']:.3f}")
    if s["block_rate"] is not None:
        print(f"Block rate (of decided generations): {s['block_rate']:.1%}")


def main() -> None:
    """Console entry point: summarize the guardrail metrics stream.

    Reads ``guardrails.metrics_path`` from config.yaml when present so the CLI
    and config stay in sync; falls back to the module default otherwise.
    """
    try:
        from guardrails.config import load_guardrails_config

        path: str | Path = load_guardrails_config().metrics_path
    except Exception:  # noqa: BLE001 - never let config issues block a read-only summary
        path = "logs/guardrails.jsonl"
    print_metrics(path)


if __name__ == "__main__":
    main()
