"""GitHub WRITE scaffold for the agentic layer -- DISABLED and STUBBED in v0.1.

This module deliberately CANNOT mutate GitHub state. It exists to (a) document
the exact gate a future write capability must pass, and (b) let tests prove that
the gate refuses every under-satisfied request. Wiring real execution is a
separate, individually-reviewed change behind the same gate.

The gate is the out-of-band analogue of CyClaw's "Triple-Gated External Access"
invariant for the Grok path. A write *plan* is produced only when ALL of:

    1. cfg.mode == "write"            (operator put the layer in write mode)
    2. cfg.writes_enabled is True     (explicit second flag, defaults False)
    3. a non-empty human ``reason``   (governance: no anonymous mutations)
    4. confirm is True                (explicit per-call confirmation)

are satisfied simultaneously. Even then, ``plan_write`` returns a DRY-RUN plan
(the argv it *would* run) and never executes it -- ``EXECUTION_ENABLED`` is a
hard ``False`` and the one place that would execute raises ``NotImplementedError``.
Any gate failure raises ``AgenticWriteRefused`` and is audited.
"""

from __future__ import annotations

from agentic.config import AgenticConfig
from utils.errors import AgenticError, AgenticWriteRefused
from utils.logger import audit_log

# HARD KILL SWITCH. v0.1 never executes a write. Flipping this to True is NOT
# sufficient to enable writes -- the executor below is intentionally unimplemented
# so that a future enablement is a deliberate, reviewed code change, not a flag.
EXECUTION_ENABLED = False

# Write ops the planner knows how to *describe* (not execute).
_WRITE_OPS = frozenset({"pr_comment", "issue_comment", "pr_create"})


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


def _build_write_argv(op: str, repo: str, params: dict, gh_bin: str = "gh") -> list[str]:
    """Build the argv a write WOULD use, for display in the dry-run plan only."""
    if op == "pr_comment":
        return [gh_bin, "pr", "comment", _require_int_number(op, params),
                "--repo", repo, "--body", str(params.get("body", ""))]
    if op == "issue_comment":
        return [gh_bin, "issue", "comment", _require_int_number(op, params),
                "--repo", repo, "--body", str(params.get("body", ""))]
    if op == "pr_create":
        return [gh_bin, "pr", "create", "--repo", repo,
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

    # All gates satisfied -- still a dry run in v0.1.
    argv = _build_write_argv(op, cfg.repo, dict(params), gh_bin=gh_bin)
    plan = {
        "status": "dry_run_plan",
        "op": op,
        "repo": cfg.repo,
        "reason": reason,
        "would_run": argv,
        "executed": False,
        "note": "v0.1 never executes writes; EXECUTION_ENABLED is False.",
    }
    audit_log({
        "event": "agentic_write_dryrun",
        "op": op,
        "repo": cfg.repo,
        "reason": reason,
    }, config_path)
    return plan


def execute_write(plan: dict, *, config_path: str = "config.yaml") -> dict:
    """Placeholder executor, gated by the ``EXECUTION_ENABLED`` kill switch.

    The kill switch is now *enforced* here, not merely documented. While
    ``EXECUTION_ENABLED`` is ``False`` (its shipped state) this refuses every
    request with ``AgenticWriteRefused`` and audits it -- even a caller that
    hand-builds a fully gate-satisfied plan cannot run a write. Flipping the flag
    to ``True`` is still not sufficient: the executor below remains unimplemented,
    so enabling real writes stays a deliberate, separately-reviewed change.
    """
    if not EXECUTION_ENABLED:
        op = plan.get("op") if isinstance(plan, dict) else None
        audit_log({
            "event": "agentic_write_execution_blocked",
            "op": op,
            "gate": "execution_enabled",
        }, config_path)
        raise AgenticWriteRefused(
            "Agentic write execution is disabled (EXECUTION_ENABLED is False)",
            details={"failed_gate": "execution_enabled", "op": op},
        )
    raise NotImplementedError(  # pragma: no cover - only reachable with the flag flipped
        "Agentic write execution is intentionally unimplemented even with "
        "EXECUTION_ENABLED set; wiring it is a separate, reviewed change."
    )


__all__ = ["EXECUTION_ENABLED", "plan_write", "execute_write"]
