"""Local data models for governed harness optimization.

Clean-room scaffold only. The acceptance logic here is deterministic and local:
it compares runner reports and rejects unsafe candidates. It does not invoke an
LLM, a shell, GitHub, MCP, or any request-path module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from utils.errors import AgenticError, require_non_empty


class SurfaceType(StrEnum):
    """CyClaw-owned surfaces the optimizer may reason about in future phases."""

    REGISTRY_SKILL = "registry_skill"
    SOUL_FRAGMENT = "soul_fragment"
    GITHUB_CODING_PROMPT = "github_coding_prompt"
    DEEPAGENT_SYSTEM_PROMPT = "deepagent_system_prompt"
    DEEPAGENT_SUBAGENT_PROMPT = "deepagent_subagent_prompt"
    DEEPAGENT_SKILL_FILE = "deepagent_skill_file"
    DEEPAGENT_TOOL_POLICY = "deepagent_tool_policy"
    DEEPAGENT_PERMISSIONS_POLICY = "deepagent_permissions_policy"
    MCP_TOOL_CATALOG = "mcp_tool_catalog"
    HARNESS_OPTIMIZER_PROMPT = "harness_optimizer_prompt"
    EVALUATION_RUNNER_POLICY = "evaluation_runner_policy"


@dataclass(frozen=True)
class Surface:
    """An editable or read-only harness surface declared by an experiment."""

    surface_id: str
    surface_type: SurfaceType | str
    path: str
    description: str = ""
    editable: bool = True

    def __post_init__(self) -> None:
        require_non_empty(self.surface_id, "surface.surface_id")
        require_non_empty(self.path, "surface.path")
        if not isinstance(self.editable, bool):
            raise AgenticError("surface.editable must be a boolean", details={"surface_id": self.surface_id})
        if not isinstance(self.surface_type, SurfaceType):
            try:
                object.__setattr__(self, "surface_type", SurfaceType(str(self.surface_type)))
            except ValueError as exc:
                raise AgenticError(
                    "surface.surface_type is not a valid SurfaceType",
                    details={"surface_id": self.surface_id, "surface_type": str(self.surface_type)},
                ) from exc

    def to_dict(self) -> dict:
        data = asdict(self)
        data["surface_type"] = self.surface_type.value
        return data


@dataclass(frozen=True)
class Experiment:
    """A planned optimization experiment and its allowed surfaces."""

    experiment_id: str
    target_workspace: str
    surfaces: tuple[Surface, ...]
    train_visible: tuple[str, ...] = ()
    holdout_hidden: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty(self.experiment_id, "experiment.experiment_id")
        require_non_empty(self.target_workspace, "experiment.target_workspace")
        if not self.surfaces:
            raise AgenticError("experiment.surfaces must not be empty")
        seen: set[str] = set()
        for surface in self.surfaces:
            if surface.surface_id in seen:
                raise AgenticError(
                    "experiment surface ids must be unique",
                    details={"surface_id": surface.surface_id},
                )
            seen.add(surface.surface_id)
        for case_id in self.train_visible:
            require_non_empty(case_id, "experiment.train_visible")
        if len(set(self.train_visible)) != len(self.train_visible):
            raise AgenticError(
                "experiment.train_visible must not contain duplicate case ids",
                details={"experiment_id": self.experiment_id},
            )
        for case_id in self.holdout_hidden:
            require_non_empty(case_id, "experiment.holdout_hidden")
        if len(set(self.holdout_hidden)) != len(self.holdout_hidden):
            raise AgenticError(
                "experiment.holdout_hidden must not contain duplicate case ids",
                details={"experiment_id": self.experiment_id},
            )
        # This is a "the paperwork disagrees with itself" check, not the actual
        # security boundary — ProposerWorkspaceTools separately hard-denies any
        # holdout_hidden/ read regardless of what an Experiment claims here. But
        # if a case is declared visible AND hidden, downstream code (runners,
        # governance checks) that assumes those two sets are disjoint would be
        # working from a contradiction, so we fail fast here instead of letting
        # that quietly propagate.
        overlap = set(self.train_visible) & set(self.holdout_hidden)
        if overlap:
            raise AgenticError(
                "experiment case ids must not be in both train_visible and holdout_hidden",
                details={"case_ids": sorted(overlap)},
            )

    @property
    def editable_surface_ids(self) -> frozenset[str]:
        return frozenset(surface.surface_id for surface in self.surfaces if surface.editable)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Variant:
    """A candidate proposal produced in a local proposer workspace."""

    variant_id: str
    changed_surfaces: tuple[str, ...]
    proposal_path: str
    artifact_dir: str

    def __post_init__(self) -> None:
        require_non_empty(self.variant_id, "variant.variant_id")
        require_non_empty(self.proposal_path, "variant.proposal_path")
        require_non_empty(self.artifact_dir, "variant.artifact_dir")


@dataclass(frozen=True)
class RunReport:
    """Deterministic runner result for one baseline or candidate variant."""

    variant_id: str
    train_passed: bool
    holdout_passed: bool
    score: float
    changed_surfaces: tuple[str, ...] = ()
    governance_findings: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        require_non_empty(self.variant_id, "run_report.variant_id")
        if not isinstance(self.train_passed, bool) or not isinstance(self.holdout_passed, bool):
            raise AgenticError("run_report pass fields must be booleans", details={"variant_id": self.variant_id})
        if not isinstance(self.score, int | float) or isinstance(self.score, bool):
            raise AgenticError("run_report.score must be numeric", details={"variant_id": self.variant_id})

    @property
    def has_critical_governance_finding(self) -> bool:
        # governance_findings arrives here as plain strings, not GovernanceFinding
        # objects -- governance_gate_strings() flattens them via
        # GovernanceFinding.as_gate_string() into "severity: code: message". This
        # check is only recognizing that same "critical:" prefix by convention, so
        # if that serialization format in governance.py ever changes, this needs to
        # change with it -- nothing enforces the two stay in sync.
        return any(finding.lower().startswith("critical:") for finding in self.governance_findings)


@dataclass(frozen=True)
class CandidateDecision:
    """Acceptance decision for a candidate variant."""

    accepted: bool
    reason: str
    baseline_score: float
    candidate_score: float
    rejected_gates: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict:
        return asdict(self)


def decide_candidate(
    baseline: RunReport,
    candidate: RunReport,
    *,
    allowed_surface_ids: set[str] | frozenset[str],
    proposal_present: bool,
    visible_case_hardcoding_detected: bool = False,
) -> CandidateDecision:
    """Apply the phase-2 deterministic acceptance gate.

    A future optimizer may add richer scoring, but acceptance still needs these
    hard gates: train+holdout pass, score improves, no critical governance
    finding, only allowed surfaces changed, a proposal exists, and no visible
    case hardcoding is detected.
    """
    rejected: list[str] = []
    if not candidate.train_passed:
        rejected.append("candidate_train_failed")
    if not candidate.holdout_passed:
        rejected.append("candidate_holdout_failed")
    if candidate.score <= baseline.score:
        rejected.append("score_not_improved")
    if candidate.has_critical_governance_finding:
        rejected.append("critical_governance_finding")
    if not proposal_present:
        rejected.append("proposal_missing")
    if visible_case_hardcoding_detected:
        rejected.append("visible_case_hardcoding")
    unallowed = sorted(set(candidate.changed_surfaces) - set(allowed_surface_ids))
    if unallowed:
        rejected.append("unallowed_surface_changed")

    accepted = not rejected
    reason = "accepted" if accepted else "rejected: " + ", ".join(rejected)
    return CandidateDecision(
        accepted=accepted,
        reason=reason,
        baseline_score=float(baseline.score),
        candidate_score=float(candidate.score),
        rejected_gates=tuple(rejected),
    )
