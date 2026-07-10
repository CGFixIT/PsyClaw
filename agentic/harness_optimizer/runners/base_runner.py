"""Base runner contracts and a deterministic mock runner.

The mock runner is the phase-3 integration seam: tests can evaluate optimizer
decisions without shelling out, cloning repos, calling GitHub, or invoking a
model.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from agentic.harness_optimizer.core import Experiment, RunReport, Variant
from agentic.harness_optimizer.scoring import CaseResult, build_run_report
from utils.errors import AgenticError


class HarnessRunner(Protocol):
    """Runner interface for one baseline or candidate variant."""

    def run(self, experiment: Experiment, variant: Variant) -> RunReport:
        """Evaluate a variant and return a deterministic report."""


@dataclass(frozen=True)
class MockRunnerCase:
    """Static case result used by MockHarnessRunner."""

    case_id: str
    split: str
    passed: bool
    score: float

    def to_case_result(self) -> CaseResult:
        return CaseResult(case_id=self.case_id, passed=self.passed, score=self.score)


@dataclass(frozen=True)
class MockHarnessRunner:
    """Deterministic in-memory runner for phase-3 tests and docs examples."""

    cases: tuple[MockRunnerCase, ...]
    governance_findings: tuple[str, ...] = ()

    def run(self, experiment: Experiment, variant: Variant) -> RunReport:
        train: list[CaseResult] = []
        holdout: list[CaseResult] = []
        known_visible = set(experiment.train_visible)
        known_hidden = set(experiment.holdout_hidden)
        for case in self.cases:
            if case.split == "train_visible":
                if known_visible and case.case_id not in known_visible:
                    raise AgenticError(
                        "mock runner case is not declared in train_visible",
                        details={"case_id": case.case_id},
                    )
                train.append(case.to_case_result())
            elif case.split == "holdout_hidden":
                if known_hidden and case.case_id not in known_hidden:
                    raise AgenticError(
                        "mock runner case is not declared in holdout_hidden",
                        details={"case_id": case.case_id},
                    )
                holdout.append(case.to_case_result())
            else:
                raise AgenticError(
                    "mock runner split must be train_visible or holdout_hidden",
                    details={"split": case.split},
                )

        return build_run_report(
            variant.variant_id,
            train_cases=tuple(train),
            holdout_cases=tuple(holdout),
            changed_surfaces=variant.changed_surfaces,
            governance_findings=self.governance_findings,
        )
