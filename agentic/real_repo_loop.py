"""Real-repo coding loop: plan -> patch -> verify -> (human decides) -> commit.

Fuses three pieces that, until now, existed independently and never called
each other (see ``docs/THREAT_MODEL.md``'s executor amendments): the
planner's model call (``agentic.harness_optimizer.model_adapter.LocalProposerClient``,
already proven via the fixture-based loop driver), a real jailed clone with
local git write ops (``agentic.deepagent_github.repo_workspace.RepoWorkspaceTools``),
and the sandboxed verification executor (``agentic.executor.run_verification``).
This module is the first live caller of ``run_verification``, and the first
thing in this codebase that can turn a model's proposal into an actual git
commit against a real repository -- still local only: no push, no PR, no
GitHub API call of any kind.

``run_real_repo_loop`` stops the moment a candidate passes its own gates --
it does NOT commit. Committing is a separate, later call to
:func:`finalize_real_repo_change`, driven by an explicit human
``approve``/``reject`` decision. This is deliberate: every other write path
in this codebase (``agentic/writer.py``, ``apply_skill``) puts a human
confirmation between "this passed its checks" and "this is now written," and
a real git commit against a real repository is exactly the write this
discipline should apply to least of all skip. The split also happens to be
what makes this pipeline usable from a stateless CLI-subprocess-per-call
caller (see ``RepoWorkspaceTools.attach``): the process that ran the loop can
exit after persisting where the clone lives, and a LATER process can
reattach to it once a human decides.

Deliberately a TOP-LEVEL ``agentic`` module, not nested inside either
``agentic.harness_optimizer`` or ``agentic.deepagent_github``: it imports from
both, and putting it inside either package's own ``__init__.py`` import chain
would risk the exact circular import ``agentic/harness_optimizer/loop_driver.py``'s
own docstring documents finding and fixing (``agentic.deepagent_github.tools``
imports ``agentic.harness_optimizer.mcp.tools``, so anything either
subpackage's ``__init__.py`` imports that reaches back into the other blows up
the moment something imports ``agentic.deepagent_github``). Living outside
both, this module can safely import from either without ever being imported
BY either at package-init time.

Governance mirrors ``agentic/writer.py``'s shape rather than
``harness_optimizer.core.decide_candidate``: this is a genuinely different
kind of acceptance decision. ``decide_candidate`` assumes a declared
``Experiment`` with ``train_visible``/``holdout_hidden`` fixture cases and
would always reject a real-repo candidate outright (empty case tuples make
``train_passed``/``holdout_passed`` permanently ``False``) -- there is no such
thing as a "holdout case" for an arbitrary real repository, only "did the
repo's own tests/lints pass." So acceptance here is a new, smaller, separately
tested decision function (``decide_real_repo_candidate``) with its own gate
vocabulary, not a repurposing of ``decide_candidate``.

Gated on THREE things, checked once up front, mirroring ``agentic/writer.py``'s
"no anonymous mutations" governance principle rather than
``RepoWorkspaceTools``'s own lower-level per-call check (which still applies
too, redundantly, at every ``write_file``/``add``/``commit`` call):

    1. ``deepagent_github.allow_git_write_tools`` is ``True``
    2. a non-empty human ``reason`` string
    3. explicit ``confirm=True``

The planner model is local-only for now (``LocalProposerClient``, whatever
``agentic.deepagent_github``'s ``provider``/``base_url``/``model`` config
names -- Ollama by default). Cloud-provider wiring (the six-gate chain from an
earlier phase) is explicit future work, not attempted here.

Not wired to any CLI subcommand, HTTP route, or background caller in this
change -- matching the deferred-wiring precedent set by every prior phase in
this effort: shipped fully tested and standalone, deferring live wiring to
whichever future phase adds a real consumer (a CLI probe, or the harness
run-trigger surface).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from agentic.deepagent_github.repo_workspace import RepoWorkspaceTools
from agentic.executor import Check, VerificationReport, run_verification
from agentic.harness_optimizer.governance import CRITICAL_SEVERITY, GovernanceFinding, inspect_candidate_text
from agentic.harness_optimizer.model_adapter import LocalProposerClient
from utils.errors import AgenticError, AgenticWriteRefused
from utils.logger import audit_log

_FILE_BLOCK_RE = re.compile(
    r"=== FILE (?P<path>[^\n]+?) ===\n(?P<body>.*?)\n=== END FILE ===",
    re.DOTALL,
)

PLANNER_SYSTEM_PROMPT = (
    "You are proposing a governed, reviewed change to a real repository. For "
    "every file you want to create or change, emit exactly:\n"
    "=== FILE <repo-relative-path> ===\n<the file's full new content>\n=== END FILE ===\n"
    "Any text outside those blocks is rationale, not code. Propose the smallest "
    "change that satisfies the instruction."
)


def _parse_file_blocks(text: str) -> dict[str, str]:
    """Extract ``{path: content}`` blocks from a planner response.

    Path safety is NOT this function's job -- every parsed path still goes
    through ``RepoWorkspaceTools.write_file``'s own validation before
    anything touches disk, so a malformed/malicious path surfaces as a
    rejected iteration (``file_write_failed``), not a crash here.
    """
    return {match.group("path").strip(): match.group("body") for match in _FILE_BLOCK_RE.finditer(text)}


@dataclass(frozen=True)
class RealRepoDecision:
    """Deterministic acceptance decision for one real-repo candidate."""

    accepted: bool
    reason: str
    rejected_gates: tuple[str, ...] = ()


def decide_real_repo_candidate(
    *,
    changed_files: tuple[str, ...],
    verification: VerificationReport | None,
    governance_findings: tuple[GovernanceFinding, ...],
    write_failed: bool = False,
) -> RealRepoDecision:
    """Apply the real-repo acceptance gate.

    Rejects when no file was actually changed, a write was refused (a bad or
    malicious path, e.g.), any governance finding is critical, or (when
    verification ran) it failed. ``verification`` is ``None`` when a
    candidate was already rejected before verification was worth running (no
    files changed, or a critical finding already present) -- skipping the
    executor call in that case, never treating a skipped check as a pass.
    ``write_failed`` is a plain bool, not derived from ``changed_files``,
    mirroring ``harness_optimizer.core.decide_candidate``'s own convention of
    taking out-of-band signals (like ``visible_case_hardcoding_detected``) as
    flat booleans rather than re-deriving them here.
    """
    rejected: list[str] = []
    if not changed_files:
        rejected.append("no_files_changed")
    if write_failed:
        rejected.append("file_write_failed")
    if any(finding.severity == CRITICAL_SEVERITY for finding in governance_findings):
        rejected.append("critical_governance_finding")
    if verification is not None and not verification.ok:
        rejected.append("verification_failed")
    accepted = not rejected
    reason = "accepted" if accepted else "rejected: " + ", ".join(rejected)
    return RealRepoDecision(accepted=accepted, reason=reason, rejected_gates=tuple(rejected))


@dataclass(frozen=True)
class RealRepoLoopIteration:
    """One planner attempt against the real clone and its outcome."""

    step: int
    changed_files: tuple[str, ...]
    decision: RealRepoDecision
    governance_findings: tuple[GovernanceFinding, ...] = ()


@dataclass(frozen=True)
class RealRepoLoopResult:
    """Full outcome of a real-repo coding loop run.

    An ``accepted`` result is NOT yet committed -- ``branch_name``/
    ``commit_message`` are carried forward so a separate, later call to
    :func:`finalize_real_repo_change` can materialize (or discard) it once a
    human decides. See that function's docstring for why the commit is a
    distinct step rather than something this function does itself.
    """

    accepted: bool
    branch_name: str | None
    commit_message: str | None
    iterations: tuple[RealRepoLoopIteration, ...]

    def __post_init__(self) -> None:
        if not self.iterations:
            raise AgenticError("RealRepoLoopResult requires at least one iteration")
        if self.accepted and (self.branch_name is None or self.commit_message is None):
            raise AgenticError("an accepted RealRepoLoopResult must carry branch_name and commit_message")


def _require_run_gates(tools: RepoWorkspaceTools, *, reason: str, confirm: bool) -> None:
    if not tools.allow_git_write_tools:
        raise AgenticWriteRefused(
            "real-repo coding run refused: deepagent_github.allow_git_write_tools is False",
            details={"failed_gate": "allow_git_write_tools"},
        )
    if not isinstance(reason, str) or not reason.strip():
        raise AgenticWriteRefused(
            "real-repo coding run refused: a non-empty human reason is required",
            details={"failed_gate": "reason"},
        )
    if confirm is not True:
        raise AgenticWriteRefused(
            "real-repo coding run refused: explicit confirm=True is required",
            details={"failed_gate": "confirm"},
        )


def run_real_repo_loop(
    tools: RepoWorkspaceTools,
    client: LocalProposerClient,
    *,
    instruction: str,
    checks: Sequence[Check],
    branch_name: str,
    commit_message: str,
    max_iterations: int,
    reason: str,
    confirm: bool,
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> RealRepoLoopResult:
    """Run plan -> patch -> verify -> commit against a real, jailed clone.

    ``tools`` must already be an open ``RepoWorkspaceTools.clone(...)`` result
    -- this function does not own its lifecycle (unlike the fixture-based
    loop driver, a real clone is an expensive, disk-consuming resource the
    caller constructed and must eventually ``close()``, whether this run
    accepts or not).

    ``checks`` is REQUIRED, not defaulted: ``agentic.executor.default_checks``
    assumes THIS repo's own toolchain (pytest/ruff/the invariant guard at a
    CyClaw-specific path) and is only appropriate when the configured target
    happens to be this same repository. For any other repository, guessing a
    test/lint command would be exactly the kind of invented default this
    codebase avoids -- the caller must state what "passing" means for the
    repo it configured. An empty sequence is rejected outright: it would make
    ``run_verification`` vacuously report ``ok=True`` regardless of what was
    actually written, silently defeating the entire gate.

    ``commit_message`` is a caller-supplied, fixed string, never raw planner
    output -- never derived from a model response, only from what the caller
    (a human-reviewed task description) provides.
    """
    _require_run_gates(tools, reason=reason, confirm=confirm)
    if not isinstance(instruction, str) or not instruction.strip():
        raise AgenticError("loop instruction must be a non-empty string")
    if not isinstance(max_iterations, int) or isinstance(max_iterations, bool) or max_iterations <= 0:
        raise AgenticError("max_iterations must be a positive integer", details={"received": max_iterations})
    if not checks:
        raise AgenticError("checks must not be empty -- an empty check list vacuously accepts every candidate")

    audit_log(
        {"event": "agentic_real_repo_loop_started", "max_iterations": max_iterations},
        config_path=config_path,
        cfg=cfg,
    )

    feedback = ""
    iterations: list[RealRepoLoopIteration] = []
    for step in range(1, max_iterations + 1):
        user_prompt = "\n\n".join(
            part
            for part in (
                f"Instruction:\n{instruction}",
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
        proposed_files = _parse_file_blocks(response.content)

        governance_findings: list[GovernanceFinding] = []
        written: list[str] = []
        write_failed = False
        for path, content in proposed_files.items():
            governance_findings.extend(inspect_candidate_text(content, cfg))
            try:
                tools.write_file(path, content)
            except AgenticError:
                write_failed = True
                continue
            written.append(path)

        has_critical = any(finding.severity == CRITICAL_SEVERITY for finding in governance_findings)
        verification: VerificationReport | None = None
        if written and not has_critical and not write_failed:
            verification = run_verification(tools.worktree, checks)

        decision = decide_real_repo_candidate(
            changed_files=tuple(written),
            verification=verification,
            governance_findings=tuple(governance_findings),
            write_failed=write_failed,
        )

        iterations.append(
            RealRepoLoopIteration(
                step=step,
                changed_files=tuple(written),
                decision=decision,
                governance_findings=tuple(governance_findings),
            )
        )
        audit_log(
            {
                "event": "agentic_real_repo_loop_iteration",
                "step": step,
                "accepted": decision.accepted,
                "rejected_gates": list(decision.rejected_gates),
                "files_changed": len(written),
            },
            config_path=config_path,
            cfg=cfg,
        )

        if decision.accepted:
            audit_log(
                {"event": "agentic_real_repo_loop_accepted_pending_decision", "step": step, "branch": branch_name},
                config_path=config_path,
                cfg=cfg,
            )
            return RealRepoLoopResult(
                accepted=True,
                branch_name=branch_name,
                commit_message=commit_message,
                iterations=tuple(iterations),
            )
        feedback = decision.reason

    audit_log(
        {"event": "agentic_real_repo_loop_exhausted", "max_iterations": max_iterations},
        config_path=config_path,
        cfg=cfg,
    )
    return RealRepoLoopResult(accepted=False, branch_name=None, commit_message=None, iterations=tuple(iterations))


def finalize_real_repo_change(
    tools: RepoWorkspaceTools,
    result: RealRepoLoopResult,
    *,
    decision: Literal["approve", "reject"],
    config_path: str = "config.yaml",
    cfg: dict | None = None,
) -> dict:
    """Materialize or discard an already-accepted real-repo candidate.

    This is the human gate: ``run_real_repo_loop`` already ran the caller's
    own verification checks and decided the candidate passes, but it stops
    short of committing so a human can review the diff first (``tools.diff()``)
    -- exactly the same shape every other write path in this codebase uses
    (``agentic/writer.py``'s reason+confirm gate, ``apply_skill``'s
    ``--confirm``): the model's own proposal is never enough on its own,
    regardless of what tests it passed.

    Only call this with a ``result`` where ``result.accepted`` is ``True`` --
    calling it on an exhausted (never-accepted) result is a caller error, not
    something to silently no-op.

    ``decision="reject"`` is an audited no-op: it does not touch git at all.
    The caller is responsible for eventually discarding the clone (e.g.
    ``tools.close()``); this function only records the human's decision.
    """
    if not result.accepted:
        raise AgenticError("cannot finalize a real-repo loop result that was never accepted")
    if decision not in {"approve", "reject"}:
        raise AgenticError("decision must be 'approve' or 'reject'", details={"received": decision})
    # RealRepoLoopResult.__post_init__ already guarantees both are set whenever
    # accepted is True; asserting it here (rather than just trusting that) is
    # what lets mypy narrow str | None -> str for the calls below.
    assert result.branch_name is not None  # noqa: S101 - narrows for mypy, guaranteed by __post_init__
    assert result.commit_message is not None  # noqa: S101

    audit_log(
        {"event": "agentic_real_repo_change_decided", "decision": decision, "branch": result.branch_name},
        config_path=config_path,
        cfg=cfg,
    )
    if decision == "reject":
        return {"status": "rejected", "branch": result.branch_name}

    changed_files = list(result.iterations[-1].changed_files)
    tools.checkout_branch(result.branch_name)
    tools.add(changed_files)
    tools.commit(result.commit_message)
    audit_log(
        {"event": "agentic_real_repo_change_approved", "branch": result.branch_name},
        config_path=config_path,
        cfg=cfg,
    )
    return {"status": "approved", "branch": result.branch_name}


__all__ = [
    "PLANNER_SYSTEM_PROMPT",
    "RealRepoDecision",
    "RealRepoLoopIteration",
    "RealRepoLoopResult",
    "decide_real_repo_candidate",
    "finalize_real_repo_change",
    "run_real_repo_loop",
]
