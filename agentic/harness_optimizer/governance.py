"""Deterministic governance helpers for harness optimizer candidates.

Phase 3 only: local checks over candidate text and declared surface changes.
No model, shell, GitHub, MCP, or request-path imports.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GovernanceFinding:
    """A local governance finding emitted before candidate acceptance."""

    severity: str
    code: str
    message: str

    def __post_init__(self) -> None:
        if self.severity not in {"info", "warning", "critical"}:
            raise ValueError("severity must be info, warning, or critical")

    def as_gate_string(self) -> str:
        return f"{self.severity}: {self.code}: {self.message}"


def detect_visible_case_hardcoding(candidate_text: str, visible_case_ids: tuple[str, ...]) -> bool:
    """Return true when a candidate appears to key directly on visible case ids."""

    if not candidate_text or not visible_case_ids:
        return False
    lower = candidate_text.lower()
    return any(case_id.lower() in lower for case_id in visible_case_ids if case_id.strip())


def governance_gate_strings(findings: tuple[GovernanceFinding, ...]) -> tuple[str, ...]:
    """Serialize findings into the existing RunReport governance string format."""

    return tuple(finding.as_gate_string() for finding in findings)
