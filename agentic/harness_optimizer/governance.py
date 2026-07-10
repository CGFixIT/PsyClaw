"""Deterministic governance helpers for harness optimizer candidates.

Phase 3 only: local checks over candidate text and declared surface changes.
No model, shell, GitHub, MCP, or request-path imports.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from utils.errors import AgenticError
from utils.personality import OWASP_INJECTION_PATTERNS


@dataclass(frozen=True)
class GovernanceFinding:
    """A local governance finding emitted before candidate acceptance."""

    severity: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.severity not in {"info", "warning", "critical"}:
            raise AgenticError("severity must be info, warning, or critical", details={"severity": self.severity})

    def as_gate_string(self) -> str:
        return f"{self.severity}: {self.code}: {self.message}"


def detect_visible_case_hardcoding(candidate_text: str, visible_case_ids: tuple[str, ...]) -> bool:
    """Return true when a candidate appears to key directly on visible case ids.

    Matches on whole case-id tokens (word-boundary anchored) so a shorter id like
    "case-1" does not falsely fire on unrelated ids that merely share its prefix,
    e.g. "case-10" or "case-1b" — this feeds the hard rejection gate in
    decide_candidate, so a substring false positive would reject a legitimate
    candidate.
    """

    if not candidate_text or not visible_case_ids:
        return False
    return any(
        re.search(rf"\b{re.escape(case_id.strip())}\b", candidate_text, re.IGNORECASE)
        for case_id in visible_case_ids
        if case_id.strip()
    )


def governance_gate_strings(findings: tuple[GovernanceFinding, ...]) -> tuple[str, ...]:
    """Serialize findings into the existing RunReport governance string format."""

    return tuple(finding.as_gate_string() for finding in findings)


def inspect_candidate_text(candidate_text: str, cfg: dict | None = None) -> tuple[GovernanceFinding, ...]:
    """Flag prompt-injection-shaped candidate content before acceptance or apply."""

    patterns = list(OWASP_INJECTION_PATTERNS)
    for pattern in ((cfg or {}).get("policy", {}).get("prompt_filter", {}).get("banned_patterns", []) or []):
        if isinstance(pattern, str) and pattern not in patterns:
            patterns.append(pattern)
    for pattern in patterns:
        try:
            if re.search(pattern, candidate_text, re.IGNORECASE):
                return (
                    GovernanceFinding(
                        "critical",
                        "candidate_injection_pattern",
                        "candidate content matches a governed injection pattern",
                    ),
                )
        except re.error:
            continue
    return ()
