"""GitHub WRITE gate for the agentic layer -- implemented, shipped disarmed.

Until P10 this module could not mutate GitHub state at all: ``execute_write``
was a stub that raised ``NotImplementedError``. It is now implemented for
exactly one operation (``pr_create``, always ``--draft``), behind the same gate
this module has always documented. ``pr_comment`` and ``issue_comment`` remain
plan-only: they are describable but not executable, and reaching them through
:func:`execute_write` is a typed refusal, not a silent no-op.

The capability ships DISARMED. ``EXECUTION_ENABLED`` is still ``False`` -- see
its comment for why P10 implemented the executor without flipping it -- and
three independent ``config.yaml`` gates fail closed on a shipped checkout. Arming
it is a documented operator procedure, not a code change:
``docs/agentic/GITHUB_WRITE_ENABLEMENT.md``.

The gate is the out-of-band analogue of CyClaw's "Triple-Gated External Access"
invariant for the Grok path. A write is permitted only when ALL of:

    1. cfg.mode == "write"            (operator put the layer in write mode)
    2. cfg.writes_enabled is True     (explicit second flag, defaults False)
    3. a non-empty human ``reason``   (governance: no anonymous mutations)
    4. confirm is True                (explicit per-call confirmation)

are satisfied simultaneously. The shipped ``config.yaml`` fails 1 and 2
independently (``mode: "read"``, ``writes_enabled: false``) and the whole layer
is off besides (``agentic.enabled: false``), so enabling a write is three
deliberate operator edits, not one.

WHY :func:`execute_write` RE-RUNS THE GATE. Before P10 the gate lived only in
:func:`plan_write`, and :func:`execute_write` took a plain ``dict`` and checked
nothing but ``EXECUTION_ENABLED``. That was sound only while the flag was a
hard ``False`` -- with it flipped, "holding a plan dict" would have BECOME the
authority to write, and a plan is just data: it can be built by hand, come back
from JSON, or cross a process boundary. So the executor now takes the config
and re-runs all four gates itself, and rebuilds the argv from the plan's
semantic fields rather than executing the ``would_run`` list it was handed. A
tampered ``would_run`` is inert.

Any gate failure raises ``AgenticWriteRefused`` and is audited.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404 - argv-list only, no shell, absolute binary, fixed subcommand

from agentic.config import AgenticConfig
from utils.errors import AgenticError, AgenticWriteRefused
from utils.logger import audit_log

# EXECUTION SWITCH -- ships False, and P10 deliberately did NOT flip it.
#
# Through P9 this flag carried the whole safety story: the executor behind it
# was unimplemented, so "flipping the flag is not sufficient" was literally
# true. P10 implements the executor for pr_create, which makes that sentence
# false and leaves this flag as a real switch rather than a label on a stub.
#
# It stays False because flipping it is the one step this repo's own governance
# reserves for a human. docs/agentic/DEEP_AGENT_HARNESS_PHASES_6_9.md requires
# "a separate human security review for any request to add ... GitHub
# mutation", and the directly analogous fsconnect enablement shipped its code
# with the flag off plus a checklist stating "no sign-off, no flag flip". The
# same shape is honored here: see docs/agentic/GITHUB_WRITE_ENABLEMENT.md.
#
# Flipping this to True is now genuinely sufficient to arm the capability --
# but not to perform a write. Three shipped config gates still fail closed
# (agentic.enabled, agentic.mode, agentic.writes_enabled), and execute_write()
# re-checks all four on every call. See the module docstring.
EXECUTION_ENABLED = False

# Write ops the planner knows how to *describe*.
_WRITE_OPS = frozenset({"pr_comment", "issue_comment", "pr_create"})

# Of those, the ops execute_write() will actually run. Deliberately a SEPARATE,
# smaller set: describing an op and being allowed to perform it are different
# authorities, and P10 only reviewed the pr_create path. An op in _WRITE_OPS but
# not here is refused at execution with a message naming the reason, rather than
# falling through to a generic "unknown op" that reads like a typo.
_EXECUTABLE_WRITE_OPS = frozenset({"pr_create"})

# Mirrors agentic/deepagent_github/repo_workspace.py's BRANCH_NAME_RE.
# Duplicated rather than imported: that module lives in the deepagent_github
# subpackage, whose __init__ pulls the optional deepagents/langchain stack, and
# this module must stay importable without it. tests/test_agentic_writer.py
# asserts the two patterns still match -- the test may import that module, this
# one may not afford to.
_HEAD_BRANCH_RE = re.compile(r"^claude/[A-Za-z0-9][A-Za-z0-9._/-]{0,79}$")

# gh pr create is not idempotent: a second run against the same head branch
# either opens a duplicate PR or errors. run_read's transient-retry loop is
# therefore NOT reused here -- both of its retry branches (TimeoutExpired and a
# transient non-zero exit) fire AFTER the request has already left the machine,
# so a retry can duplicate a mutation GitHub already accepted. One attempt, and
# a timeout is reported as an indeterminate outcome rather than retried.
DEFAULT_WRITE_TIMEOUT_SEC = 60


def _require_int_number(op: str, params: dict) -> str:
    """Return params['number'] as a decimal string, raising typed errors.

    Mirrors gh_client.build_read_argv's guard: a caller-supplied non-integer
    'number' must surface as AgenticError (the contract plan_write documents and
    the CLI maps to EXIT_FAIL), never as a bare ValueError/TypeError traceback.
    """
    if "number" not in params:
        raise AgenticError(f"op {op!r} requires 'number' in params", details={"op": op})
    number = params["number"]
    try:
        return str(int(number))
    except (TypeError, ValueError) as exc:
        raise AgenticError(
            f"'number' must be an integer, got {type(number).__name__}: {number!r}",
            details={"op": op},
        ) from exc


def _require_head_branch(op: str, params: dict) -> str:
    """Return params['head'], validated as a claude/ branch name.

    Anchored to the claude/ namespace for the same two reasons
    repo_workspace.BRANCH_NAME_RE is: it is the repo's own branch convention,
    and no string starting with 'claude/' can start with '-', so the value
    cannot be reparsed by ``gh`` as an option instead of a branch.
    """
    head = params.get("head")
    if not isinstance(head, str) or not _HEAD_BRANCH_RE.match(head):
        raise AgenticError(
            f"op {op!r} requires 'head' to be a claude/<topic> branch name",
            details={"op": op, "head": str(head)[:120]},
        )
    return head


def _build_write_argv(op: str, repo: str, params: dict, gh_bin: str = "gh") -> list[str]:
    """Build the argv a write WOULD use, for display in the dry-run plan only."""
    if op == "pr_comment":
        return [gh_bin, "pr", "comment", _require_int_number(op, params),
                "--repo", repo, "--body", str(params.get("body", ""))]
    if op == "issue_comment":
        return [gh_bin, "issue", "comment", _require_int_number(op, params),
                "--repo", repo, "--body", str(params.get("body", ""))]
    if op == "pr_create":
        # --head is REQUIRED, never inferred. Without it `gh` picks the head
        # branch from the git state of whatever directory the process happens
        # to be in -- which on the ops_runner path is the operator's own CyClaw
        # checkout, so an omitted --head would open a PR from whatever branch
        # they had checked out at the time. --base is likewise explicit rather
        # than left to the repo default.
        head = _require_head_branch(op, params)
        return [gh_bin, "pr", "create", "--repo", repo,
                "--head", head, "--base", str(params.get("base", "main")),
                "--title", str(params.get("title", "")), "--body", str(params.get("body", "")),
                "--draft"]
    raise AgenticError(f"Unknown write op: {op!r}", details={"op": op, "allowed": sorted(_WRITE_OPS)})


def _refuse(
    reason_msg: str,
    *,
    op: str,
    gate: str,
    reason: str,
    config_path: str = "config.yaml",
) -> AgenticWriteRefused:
    audit_log({
        "event": "agentic_write_refused",
        "op": op,
        "gate": gate,
        "reason": reason or "",
    }, config_path)
    return AgenticWriteRefused(reason_msg, details={"op": op, "failed_gate": gate})


def _require_gates(
    cfg: AgenticConfig,
    op: str,
    reason: str,
    *,
    confirm: bool,
    config_path: str,
) -> None:
    """The four-gate chain, in order. Raises ``AgenticWriteRefused`` on the first failure.

    Extracted so :func:`plan_write` and :func:`execute_write` enforce ONE
    implementation rather than two that can drift. Before P10 only the planner
    ran it, which was survivable while the executor was a hard-disabled stub;
    it stops being survivable the moment the executor works.
    """
    if op not in _WRITE_OPS:
        raise AgenticError(
            f"Unknown write op: {op!r}",
            details={"op": op, "allowed": sorted(_WRITE_OPS)},
        )
    # Gate 1: write mode.
    if not cfg.is_write_mode:
        raise _refuse("agentic.mode is not 'write'", op=op, gate="mode", reason=reason, config_path=config_path)
    # Gate 2: explicit writes_enabled flag.
    if not cfg.writes_enabled:
        raise _refuse(
            "agentic.writes_enabled is False",
            op=op,
            gate="writes_enabled",
            reason=reason,
            config_path=config_path,
        )
    # Gate 3: human reason string.
    if not (isinstance(reason, str) and reason.strip()):
        raise _refuse(
            "a non-empty human reason is required",
            op=op,
            gate="reason",
            reason=reason,
            config_path=config_path,
        )
    # Gate 4: per-call confirmation.
    if confirm is not True:
        raise _refuse(
            "explicit confirm=True is required",
            op=op,
            gate="confirm",
            reason=reason,
            config_path=config_path,
        )


def plan_write(
    cfg: AgenticConfig,
    op: str,
    reason: str,
    *,
    confirm: bool = False,
    gh_bin: str = "gh",
    config_path: str = "config.yaml",
    **params: object,
) -> dict:
    """Validate the write gate and return a DRY-RUN plan. Never executes.

    Raises ``AgenticWriteRefused`` if any gate fails, ``AgenticError`` for an
    unknown op. On full gate satisfaction returns a dict describing the argv that
    a (future) executor would run -- but nothing is executed in v0.1.
    """
    _require_gates(cfg, op, reason, confirm=confirm, config_path=config_path)

    # All gates satisfied -- planning is still a dry run. Executing is a
    # separate call that re-runs this same gate against the same config.
    argv = _build_write_argv(op, cfg.repo, dict(params), gh_bin=gh_bin)
    plan = {
        "status": "dry_run_plan",
        "op": op,
        "repo": cfg.repo,
        "reason": reason,
        # Carried so execute_write can REBUILD the argv rather than run the one
        # below. would_run stays for display and for drift comparison.
        "params": dict(params),
        "would_run": argv,
        "executed": False,
        "note": "plan only; execute_write() re-runs the full gate before performing this.",
    }
    audit_log({
        "event": "agentic_write_dryrun",
        "op": op,
        "repo": cfg.repo,
        "reason": reason,
    }, config_path)
    return plan


def execute_write(
    plan: dict,
    *,
    cfg: AgenticConfig,
    config_path: str = "config.yaml",
    gh_bin: str = "gh",
    timeout_sec: int = DEFAULT_WRITE_TIMEOUT_SEC,
) -> dict:
    """Perform one gate-satisfied write. Currently ``pr_create`` (draft) only.

    ``cfg`` is REQUIRED, not optional, and that is the security design rather
    than an ergonomic accident: this function re-runs the same four gates
    :func:`plan_write` did, against the live config, so possessing a plan dict
    is not itself authority to write. A plan is data -- hand-buildable, JSON
    round-trippable, capable of crossing a process boundary -- and before P10
    the only thing standing between such a dict and a real mutation was
    ``EXECUTION_ENABLED`` being a hard ``False``.

    The argv is REBUILT here from the plan's ``op``/``params`` via the same
    builder the planner used; ``plan["would_run"]`` is never executed. It is
    compared against the rebuilt argv and a mismatch is a typed refusal, so a
    tampered or stale plan fails loudly instead of running something the gate
    never saw.

    No retry. See ``DEFAULT_WRITE_TIMEOUT_SEC``: a timeout here means the
    outcome is INDETERMINATE (the request may well have been accepted), so it
    is reported as such rather than retried into a duplicate PR.
    """
    op = plan.get("op") if isinstance(plan, dict) else None
    if not EXECUTION_ENABLED:
        audit_log({
            "event": "agentic_write_execution_blocked",
            "op": op,
            "gate": "execution_enabled",
        }, config_path)
        raise AgenticWriteRefused(
            "Agentic write execution is disabled (EXECUTION_ENABLED is False)",
            details={"failed_gate": "execution_enabled", "op": op},
        )
    if not isinstance(plan, dict) or not isinstance(op, str):
        raise AgenticError("execute_write requires a plan dict carrying an 'op'", details={"op": op})

    reason = plan.get("reason")
    _require_gates(cfg, op, reason if isinstance(reason, str) else "", confirm=True, config_path=config_path)

    if op not in _EXECUTABLE_WRITE_OPS:
        audit_log({"event": "agentic_write_execution_blocked", "op": op, "gate": "executable_op"}, config_path)
        raise AgenticWriteRefused(
            f"write op {op!r} can be planned but not executed; "
            f"executable ops are {sorted(_EXECUTABLE_WRITE_OPS)}",
            details={"failed_gate": "executable_op", "op": op},
        )

    # The plan's repo is advisory; the CONFIG's repo is authoritative. A plan
    # naming a different repository than the one the operator configured is a
    # tampered plan, not a targeting option.
    if plan.get("repo") != cfg.repo:
        raise AgenticWriteRefused(
            "plan repo does not match the configured repo",
            details={"failed_gate": "repo_match", "op": op},
        )

    # Rebuild and integrity-check BEFORE touching the environment: a tampered
    # plan should be refused identically whether or not gh happens to be
    # installed, and a refusal must not depend on machine state.
    params = plan.get("params")
    argv = _build_write_argv(op, cfg.repo, dict(params) if isinstance(params, dict) else {}, gh_bin=gh_bin)
    declared = plan.get("would_run")
    if isinstance(declared, list) and declared[1:] != argv[1:]:
        # argv[0] is excluded from the comparison: the plan may have been built
        # with the default "gh" while a caller passes something else. Everything
        # after it is the actual command and must agree exactly.
        raise AgenticWriteRefused(
            "plan would_run does not match the argv rebuilt from its own params",
            details={"failed_gate": "plan_integrity", "op": op},
        )

    binary = shutil.which(gh_bin)
    if binary is None:
        raise AgenticError("gh binary not found on PATH", details={"op": op, "looked_for": gh_bin})
    argv[0] = binary

    audit_log({
        "event": "agentic_write_execute_start",
        "op": op,
        "repo": cfg.repo,
        "reason": reason,
    }, config_path)
    try:
        completed = subprocess.run(  # noqa: S603  # nosec B603 - argv list, absolute binary, no shell
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        audit_log({"event": "agentic_write_execute_timeout", "op": op, "repo": cfg.repo}, config_path)
        raise AgenticError(
            f"gh {op} timed out after {timeout_sec}s; the outcome is INDETERMINATE "
            "and was deliberately not retried (a retry can duplicate an accepted mutation)",
            details={"op": op, "repo": cfg.repo, "indeterminate": True},
        ) from exc

    audit_log({
        "event": "agentic_write_executed",
        "op": op,
        "repo": cfg.repo,
        "exit_code": completed.returncode,
    }, config_path)
    if completed.returncode != 0:
        raise AgenticError(
            f"gh {op} failed with exit code {completed.returncode}",
            details={"op": op, "repo": cfg.repo, "stderr": (completed.stderr or "")[-2000:]},
        )
    return {
        "status": "executed",
        "op": op,
        "repo": cfg.repo,
        "reason": reason,
        "executed": True,
        "exit_code": completed.returncode,
        "stdout": (completed.stdout or "").strip(),
    }


__all__ = ["DEFAULT_WRITE_TIMEOUT_SEC", "EXECUTION_ENABLED", "plan_write", "execute_write"]
