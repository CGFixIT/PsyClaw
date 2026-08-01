"""Model-driven plan -> patch -> verify -> review loop for ``GitHubCodingRunner``.

Replaces guesswork with a concrete contract: the planner model is asked to
return each changed surface as a delimited block --

    === SURFACE <surface_id> ===
    <the file's full new content>
    === END SURFACE ===

-- which this module parses, writes into the candidate workspace via the
existing ``ProposerWorkspaceTools`` boundary (``write_current_file``/
``finish_proposal`` -- no new write primitive), scores with
``GitHubCodingRunner`` (visible + holdout fixture cases, plus the governance
inspection ``evaluate()`` already runs), and gates acceptance through
``decide_candidate`` -- the same deterministic gate every other candidate in
this scaffold goes through. It iterates up to ``max_iterations``, feeding each
rejection's ``reason`` back into the next prompt as review feedback. This is
what makes ``HarnessOptimizerConfig.max_iterations`` load-bearing for the first
time -- previously declared and validated, but read by no loop.

Deliberately scoped to the existing fixture-based ``GitHubCodingRunner``, not a
real cloned repository: ``RepoWorkspaceTools`` and ``agentic/executor`` target a
real git worktree, and wiring this loop to run against one is future work once
a real-repo runner exists to consume them. Nothing here invokes GitHub, a shell,
or writes outside ``workspace.current_dir``/``proposal_path`` -- the exact
boundary ``ProposerWorkspaceTools`` already enforces. The only "model call" is
``LocalProposerClient.invoke()``, the same OpenAI-compatible chat client phase
4 shipped and every test here drives through ``httpx.MockTransport``, never a
live endpoint.

Not wired to any CLI subcommand or background caller in this change, matching
the deferred-wiring precedent set by the injection scanner (P1), the
deepagent-plan CLI probe (P4), ``RepoWorkspaceTools`` (P5), and the
verification executor (P6): shipped fully tested and standalone, deferring
live wiring to whichever future phase adds a real consumer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentic.harness_optimizer.core import CandidateDecision, Experiment, RunReport, Variant, decide_candidate
from agentic.harness_optimizer.governance import GovernanceFinding
from agentic.harness_optimizer.mcp.tools import ProposerWorkspaceTools
from agentic.harness_optimizer.model_adapter import LocalProposerClient
from agentic.harness_optimizer.runners.github_coding_runner import GitHubCodingRunner
from utils.errors import AgenticError
from utils.logger import audit_log

_SURFACE_BLOCK_RE = re.compile(
    r"=== SURFACE (?P<surface_id>[A-Za-z0-9_.-]+) ===\n(?P<body>.*?)\n=== END SURFACE ===",
    re.DOTALL,
)

PLANNER_SYSTEM_PROMPT = (
    "You are proposing a governed, reviewed change to a CyClaw harness surface. "
    "For every editable surface you want to change, emit exactly:\n"
    "=== SURFACE <surface_id> ===\n<the file's full new content>\n=== END SURFACE ===\n"
    "Only use surface ids from the manifest below. Any text outside those blocks "
    "is treated as your rationale, not code. Never reference a visible train "
    "case id directly -- explain your reasoning in terms of behavior, not case "
    "identity."
)


def _parse_surface_blocks(text: str, editable_surface_ids: frozenset[str]) -> tuple[dict[str, str], str]:
    """Split a planner response into ``{surface_id: content}`` plus rationale.

    Only surface ids the experiment actually declares editable are honored --
    anything else is dropped (not written, not scored) rather than passed
    through to ``GitHubCodingRunner``'s own "unallowed surface" gate, since an
    id this function can't map to a real path isn't a governance violation,
    just an unusable response.
    """

    surfaces: dict[str, str] = {}
    for match in _SURFACE_BLOCK_RE.finditer(text):
        surface_id = match.group("surface_id")
        if surface_id in editable_surface_ids:
            surfaces[surface_id] = match.group("body")
    rationale = _SURFACE_BLOCK_RE.sub("", text).strip()
    return surfaces, rationale or "(no additional rationale provided)"


@dataclass(frozen=True)
class LoopIteration:
    """One planner attempt and its deterministic outcome."""

    variant_id: str
    changed_surfaces: tuple[str, ...]
    decision: CandidateDecision
    findings: tuple[GovernanceFinding, ...] = ()


@dataclass(frozen=True)
class LoopResult:
    """Full outcome of an optimization loop run."""

    accepted: bool
    baseline: RunReport
    iterations: tuple[LoopIteration, ...]

    def __post_init__(self) -> None:
        if not self.iterations:
            raise AgenticError("LoopResult requires at least one iteration")

    @property
    def final_decision(self) -> CandidateDecision:
        return self.iterations[-1].decision


def run_optimization_loop(
    runner: GitHubCodingRunner,
    experiment: Experiment,
    client: LocalProposerClient,
    *,
    instruction: str,
    max_iterations: int,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> LoopResult:
    """Run the plan -> patch -> verify -> review loop until acceptance or exhaustion.

    ``max_iterations`` is the caller's own ``HarnessOptimizerConfig.max_iterations``
    -- taken as a plain int rather than the config dataclass, matching
    ``decide_candidate``'s own "accept a plain value, not a config object"
    convention. ``tools`` is built here from ``runner.workspace``, not accepted
    as a separate parameter, so a caller can never pass a ``ProposerWorkspaceTools``
    pointed at a different workspace than the one ``runner`` actually scores
    against.
    """

    if not isinstance(instruction, str) or not instruction.strip():
        raise AgenticError("loop instruction must be a non-empty string")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations <= 0:
        raise AgenticError("max_iterations must be a positive integer", details={"received": max_iterations})

    tools = ProposerWorkspaceTools(runner.workspace, config_path=config_path, cfg=cfg)
    surfaces_by_id = {surface.surface_id: surface for surface in experiment.surfaces}

    baseline_variant = Variant(
        variant_id=f"{experiment.experiment_id}-baseline",
        changed_surfaces=(),
        proposal_path=str(runner.workspace.proposal_path),
        artifact_dir=str(runner.workspace.current_dir),
    )
    baseline = runner.evaluate(experiment, baseline_variant).report
    audit_log(
        {
            "event": "agentic_harness_loop_started",
            "experiment_id": experiment.experiment_id,
            "max_iterations": max_iterations,
        },
        config_path=config_path,
        cfg=cfg,
    )

    feedback = ""
    iterations: list[LoopIteration] = []
    for step in range(1, max_iterations + 1):
        user_prompt = "\n\n".join(
            part
            for part in (
                f"Instruction:\n{instruction}",
                f"Surface manifest:\n{tools.read_surface_manifest()}",
                f"Visible train failures:\n{tools.read_train_failures()}",
                f"Prior attempt feedback:\n{feedback}" if feedback else "",
            )
            if part
        )
        response = client.invoke(
            system_prompt=PLANNER_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            config_path=config_path,
            cfg=cfg,
        )
        parsed, rationale = _parse_surface_blocks(response.content, experiment.editable_surface_ids)
        for surface_id, content in parsed.items():
            tools.write_current_file(surfaces_by_id[surface_id].path, content)
        tools.finish_proposal(rationale)

        variant = Variant(
            variant_id=f"{experiment.experiment_id}-iter{step}",
            changed_surfaces=tuple(sorted(parsed)),
            proposal_path=str(runner.workspace.proposal_path),
            artifact_dir=str(runner.workspace.current_dir),
        )
        evaluation = runner.evaluate(experiment, variant)
        decision = decide_candidate(
            baseline,
            evaluation.report,
            allowed_surface_ids=experiment.editable_surface_ids,
            proposal_present=True,
        )
        iterations.append(
            LoopIteration(
                variant_id=variant.variant_id,
                changed_surfaces=variant.changed_surfaces,
                decision=decision,
                findings=evaluation.findings,
            )
        )
        audit_log(
            {
                "event": "agentic_harness_loop_iteration",
                "experiment_id": experiment.experiment_id,
                "variant_id": variant.variant_id,
                "step": step,
                "accepted": decision.accepted,
                "rejected_gates": list(decision.rejected_gates),
            },
            config_path=config_path,
            cfg=cfg,
        )
        if decision.accepted:
            audit_log(
                {
                    "event": "agentic_harness_loop_accepted",
                    "experiment_id": experiment.experiment_id,
                    "variant_id": variant.variant_id,
                    "step": step,
                },
                config_path=config_path,
                cfg=cfg,
            )
            return LoopResult(accepted=True, baseline=baseline, iterations=tuple(iterations))
        feedback = decision.reason

    audit_log(
        {
            "event": "agentic_harness_loop_exhausted",
            "experiment_id": experiment.experiment_id,
            "max_iterations": max_iterations,
        },
        config_path=config_path,
        cfg=cfg,
    )
    return LoopResult(accepted=False, baseline=baseline, iterations=tuple(iterations))


__all__ = ["LoopIteration", "LoopResult", "PLANNER_SYSTEM_PROMPT", "run_optimization_loop"]
