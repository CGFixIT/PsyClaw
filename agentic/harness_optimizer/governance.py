"""Deterministic governance helpers for harness optimizer candidates.

Phase 3 only: local checks over candidate text and declared surface changes.
No model, shell, GitHub, MCP, or request-path imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from guardrails.rails import build_injection_pattern_sources, compile_injection_patterns
from utils.errors import AgenticError

# Shared with core.RunReport.has_critical_governance_finding: the gate there
# recognizes this exact severity string by convention (parsed back out of
# as_gate_string()'s "severity: code: message" format). Importing the constant
# instead of a bare literal means a rename here is at least a visible import,
# not a silent two-file drift.
CRITICAL_SEVERITY = "critical"


@dataclass(frozen=True)
class GovernanceFinding:
    """A local governance finding emitted before candidate acceptance."""

    severity: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.severity not in {"info", "warning", CRITICAL_SEVERITY}:
            raise AgenticError("severity must be info, warning, or critical", details={"severity": self.severity})

    def as_gate_string(self) -> str:
        return f"{self.severity}: {self.code}: {self.message}"


def detect_visible_case_hardcoding(candidate_text: str, visible_case_ids: tuple[str, ...]) -> bool:
    """Return true when a candidate appears to key directly on visible case ids.

    Matches on whole case-id tokens (negative-lookaround anchored on `[\\w-]`) so
    a shorter id like "case-1" does not falsely fire on unrelated ids that merely
    share its prefix, e.g. "case-10" or "case-1b" — nor on unrelated ids that
    happen to contain it as a dash-delimited substring, e.g. "test-case-1" or
    "hard-case-1" (a plain `\\b` boundary treats hyphen as a boundary character
    and would wrongly match those). This feeds the hard rejection gate in
    decide_candidate, so a substring false positive would reject a legitimate
    candidate.
    """

    if not candidate_text or not visible_case_ids:
        return False
    return any(
        re.search(rf"(?<![\w-]){re.escape(case_id.strip())}(?![\w-])", candidate_text, re.IGNORECASE)
        for case_id in visible_case_ids
        if case_id.strip()
    )


def governance_gate_strings(findings: tuple[GovernanceFinding, ...]) -> tuple[str, ...]:
    """Serialize findings into the existing RunReport governance string format."""

    return tuple(finding.as_gate_string() for finding in findings)


def inspect_candidate_text(candidate_text: str, cfg: dict | None = None) -> tuple[GovernanceFinding, ...]:
    """Flag prompt-injection-shaped candidate content before acceptance or apply."""

    # Pattern union + compile are shared with agentic.registry and
    # agentic.fsconnect.client via guardrails.rails; the non-string guard this
    # module carried is now part of that shared builder, so all three sites get
    # it. The finding severity below stays local -- guardrails owns the pattern
    # set, this module owns what a match means for a candidate artifact.
    sources = build_injection_pattern_sources(cfg or {})
    for _src, compiled in compile_injection_patterns(tuple(sources)):
        if compiled.search(candidate_text):
            return (
                GovernanceFinding(
                    "critical",
                    "candidate_injection_pattern",
                    "candidate content matches a governed injection pattern",
                ),
            )
    return ()
