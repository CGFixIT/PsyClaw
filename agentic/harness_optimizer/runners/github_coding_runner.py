"""Fixture-only GitHub coding evaluator for governed harness experiments.

The runner copies a local fixture repository to a temporary directory, overlays
only declared candidate surfaces, and checks deterministic file expectations.
It never shells out, touches the real repository, or writes GitHub.
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from agentic.config import AgenticConfig
from agentic.context import fetch_issue_context, fetch_pr_context, fetch_repo_context
from agentic.deepagent_github.builder import DeepAgentBuildResult, build_deepagent_github
from agentic.harness_optimizer.core import Experiment, RunReport, Variant
from agentic.harness_optimizer.governance import (
    GovernanceFinding,
    detect_visible_case_hardcoding,
    governance_gate_strings,
    inspect_candidate_text,
)
from agentic.harness_optimizer.mcp.tools import ProposerWorkspaceTools
from agentic.harness_optimizer.proposer import ProposerWorkspace
from agentic.harness_optimizer.scoring import CaseResult, build_run_report
from agentic.registry import SkillRegistry
from utils.errors import AgenticError
from utils.logger import audit_log


def _safe_child(root: Path, relative: str) -> Path:
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise AgenticError("fixture path must be a non-empty relative string")
    if Path(relative).is_absolute() or PureWindowsPath(relative).is_absolute():
        raise AgenticError("fixture path must be relative", details={"path": relative})
    parts = tuple(part for part in relative.replace("\\", "/").split("/") if part not in {"", "."})
    if not parts or any(part == ".." or ":" in part for part in parts):
        raise AgenticError("fixture path escaped its root", details={"path": relative})
    resolved_root = root.resolve()
    candidate = resolved_root.joinpath(*parts).resolve()
    if candidate == resolved_root or resolved_root not in candidate.parents:
        raise AgenticError("fixture path escaped its root", details={"path": relative})
    return candidate


@dataclass(frozen=True)
class FixtureCase:
    """One deterministic file-content check in a copied fixture repository."""

    case_id: str
    split: str
    path: str
    expected_text: str

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise AgenticError("fixture case_id must be a non-empty string")
        if self.split not in {"train_visible", "holdout_hidden"}:
            raise AgenticError("fixture split must be train_visible or holdout_hidden")
        _safe_child(Path.cwd(), self.path)
        if not isinstance(self.expected_text, str) or not self.expected_text:
            raise AgenticError("fixture expected_text must be non-empty")


@dataclass(frozen=True)
class GitHubCodingEvaluation:
    """No-network artifact for one fixture-based GitHub coding evaluation."""

    report: RunReport
    context: dict
    findings: tuple[GovernanceFinding, ...]
    selected_commands: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "variant_id": self.report.variant_id,
            "score": self.report.score,
            "train_passed": self.report.train_passed,
            "holdout_passed": self.report.holdout_passed,
            "governance_findings": list(self.report.governance_findings),
            "selected_commands": list(self.selected_commands),
        }


def fetch_github_task_context(
    cfg: AgenticConfig,
    *,
    issue_number: int | None = None,
    pr_number: int | None = None,
) -> dict:
    """Use the existing read-only context wrappers for a task's declared source."""

    if issue_number is not None and pr_number is not None:
        raise AgenticError("a GitHub coding task may reference either an issue or a PR, not both")
    if pr_number is not None:
        return fetch_pr_context(cfg, pr_number)
    if issue_number is not None:
        return fetch_issue_context(cfg, issue_number)
    return fetch_repo_context(cfg)


@dataclass(frozen=True)
class GitHubCodingRunner:
    """Evaluate a scoped candidate against local fixture cases only."""

    fixture_repo: Path
    workspace: ProposerWorkspace
    cases: tuple[FixtureCase, ...]
    config_path: str = "config.yaml"
    cfg: dict | None = None

    def __post_init__(self) -> None:
        if not self.fixture_repo.is_dir():
            raise AgenticError("fixture repository must be an existing directory")
        if not self.cases:
            raise AgenticError("GitHub coding runner requires at least one fixture case")

    def _overlay_candidate(
        self,
        repo_root: Path,
        experiment: Experiment,
        variant: Variant,
    ) -> tuple[GovernanceFinding, ...]:
        if not variant.changed_surfaces:
            return ()
        surfaces = {surface.surface_id: surface for surface in experiment.surfaces}
        findings: list[GovernanceFinding] = []
        declared_paths = {surface.path.replace("\\", "/") for surface in experiment.surfaces}
        current = self.workspace.current_dir.resolve()
        for path in current.rglob("*"):
            if path.is_file() and path.relative_to(current).as_posix() not in declared_paths:
                findings.append(
                    GovernanceFinding("critical", "unallowed_candidate_file", "candidate wrote an undeclared surface")
                )
        for surface_id in variant.changed_surfaces:
            surface = surfaces.get(surface_id)
            if surface is None or not surface.editable:
                findings.append(
                    GovernanceFinding("critical", "unallowed_surface", "candidate changed an undeclared surface")
                )
                continue
            source = _safe_child(current, surface.path)
            if not source.is_file():
                findings.append(
                    GovernanceFinding(
                        "critical",
                        "missing_candidate_file",
                        "candidate did not provide its declared surface",
                    )
                )
                continue
            target = _safe_child(repo_root, surface.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        return tuple(findings)

    def evaluate(
        self,
        experiment: Experiment,
        variant: Variant,
        *,
        context: dict | None = None,
    ) -> GitHubCodingEvaluation:
        """Run deterministic visible and holdout checks against a temporary fixture copy."""

        event = (
            "agentic_harness_baseline_started"
            if not variant.changed_surfaces
            else "agentic_harness_candidate_evaluated"
        )
        audit_log(
            {
                "event": event,
                "variant_id": variant.variant_id,
            },
            config_path=self.config_path,
            cfg=self.cfg,
        )
        with tempfile.TemporaryDirectory(prefix="cyclaw-github-coding-") as temp_dir:
            copied_root = Path(temp_dir) / "fixture"
            shutil.copytree(self.fixture_repo, copied_root)
            findings = list(self._overlay_candidate(copied_root, experiment, variant))
            if variant.changed_surfaces:
                proposal_text = self.workspace.proposal_path.read_text(encoding="utf-8")
                findings.extend(inspect_candidate_text(proposal_text, self.cfg))
                if detect_visible_case_hardcoding(proposal_text, experiment.train_visible):
                    findings.append(
                        GovernanceFinding(
                            "critical",
                            "visible_case_hardcoding",
                            "candidate references a visible train case",
                        )
                    )

            train_cases: list[CaseResult] = []
            holdout_cases: list[CaseResult] = []
            for case in self.cases:
                if case.split == "train_visible" and case.case_id not in experiment.train_visible:
                    raise AgenticError(
                        "train fixture case is not declared by the experiment",
                        details={"case_id": case.case_id},
                    )
                if case.split == "holdout_hidden" and case.case_id not in experiment.holdout_hidden:
                    raise AgenticError(
                        "holdout fixture case is not declared by the experiment",
                        details={"case_id": case.case_id},
                    )
                path = _safe_child(copied_root, case.path)
                text = path.read_text(encoding="utf-8") if path.is_file() else ""
                result = CaseResult(case.case_id, case.expected_text in text, float(case.expected_text in text))
                (train_cases if case.split == "train_visible" else holdout_cases).append(result)

        report = build_run_report(
            variant.variant_id,
            train_cases=tuple(train_cases),
            holdout_cases=tuple(holdout_cases),
            changed_surfaces=variant.changed_surfaces,
            governance_findings=governance_gate_strings(tuple(findings)),
        )
        event = (
            "agentic_harness_baseline_finished"
            if not variant.changed_surfaces
            else "agentic_harness_candidate_evaluated"
        )
        audit_log(
            {
                "event": event,
                "variant_id": variant.variant_id,
                "score": report.score,
                "passed": report.train_passed and report.holdout_passed,
            },
            config_path=self.config_path,
            cfg=self.cfg,
        )
        return GitHubCodingEvaluation(report=report, context=context or {}, findings=tuple(findings))

    def run(self, experiment: Experiment, variant: Variant) -> RunReport:
        """Implement the existing HarnessRunner protocol."""

        return self.evaluate(experiment, variant).report

    def build_optional_deepagent(
        self,
        agentic_config: AgenticConfig,
        *,
        skill_registry: SkillRegistry | None = None,
        repo_root: Path | None = None,
    ) -> DeepAgentBuildResult:
        """Route optional Deep Agents construction through the governed builder."""

        return build_deepagent_github(
            agentic_config,
            workspace_tools=ProposerWorkspaceTools(self.workspace, config_path=self.config_path, cfg=self.cfg),
            skill_registry=skill_registry,
            repo_root=repo_root,
            config_path=self.config_path,
            cfg=self.cfg,
        )
