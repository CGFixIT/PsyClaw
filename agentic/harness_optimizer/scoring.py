"""Deterministic scoring helpers for harness optimizer runner reports."""

from __future__ import annotations

from dataclasses import dataclass

from agentic.harness_optimizer.core import CandidateDecision, RunReport
from utils.errors import AgenticError


@dataclass(frozen=True)
class CaseResult:
    """One deterministic train or holdout case result."""

    case_id: str
    passed: bool
    score: float
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise AgenticError("case_id must be a non-empty string")
        if not isinstance(self.passed, bool):
            raise AgenticError("case result passed must be a boolean", details={"case_id": self.case_id})
        if not isinstance(self.score, int | float) or isinstance(self.score, bool):
            raise AgenticError("case result score must be numeric", details={"case_id": self.case_id})
        if self.score < 0.0 or self.score > 1.0:
            raise AgenticError("case result score must be between 0 and 1", details={"case_id": self.case_id})


@dataclass(frozen=True)
class Scorecard:
    """Final baseline-vs-candidate validation artifact."""

    baseline: RunReport
    candidate: RunReport
    decision: CandidateDecision

    def to_markdown(self) -> str:
        status = "accepted" if self.decision.accepted else "rejected"
        gates = ", ".join(self.decision.rejected_gates) or "none"
        return "\n".join(
            (
                "# Harness Optimizer Scorecard",
                "",
                f"- status: {status}",
                f"- baseline_score: {self.baseline.score:.4f}",
                f"- candidate_score: {self.candidate.score:.4f}",
                f"- rejected_gates: {gates}",
                "",
            )
        )


def score_cases(cases: tuple[CaseResult, ...]) -> float:
    """Average deterministic case scores.

    Empty suites score 0 so a missing suite cannot accidentally improve a
    candidate. Acceptance still separately requires train and holdout pass.
    """

    if not cases:
        return 0.0
    return sum(case.score for case in cases) / len(cases)


def build_run_report(
    variant_id: str,
    *,
    train_cases: tuple[CaseResult, ...],
    holdout_cases: tuple[CaseResult, ...],
    changed_surfaces: tuple[str, ...] = (),
    governance_findings: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
) -> RunReport:
    """Build a RunReport from visible train and hidden holdout case results."""

    train_passed = bool(train_cases) and all(case.passed for case in train_cases)
    holdout_passed = bool(holdout_cases) and all(case.passed for case in holdout_cases)
    combined = train_cases + holdout_cases
    return RunReport(
        variant_id=variant_id,
        train_passed=train_passed,
        holdout_passed=holdout_passed,
        score=score_cases(combined),
        changed_surfaces=changed_surfaces,
        governance_findings=governance_findings,
        notes=notes,
    )
