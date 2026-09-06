"""Harness /api/agent/* routes — real-repo-run via the ops_runner shim.

Extracted from harness/server.py so create_app stays the factory + middleware
+ wiring surface. Handlers are decorated directly onto the FastAPI app (not an
APIRouter): FastAPI 0.138's include_router wraps sub-routers lazily, which
hides routes from app.routes introspection — the same reason gate_ops.py
registers onto the gateway app by name.

Invariant I6: this module never imports agentic/, sync/, guardrails/, or the
core six. GitHub/clone/commit side-effects stay behind utils.ops_runner's
subprocess shim (looked up on harness.server at request time so existing
monkeypatches on that module keep working).

Security-relevant callables (the `guarded` dependency chain, generation_gate,
agent_run_gate) stay owned by create_app and are injected unchanged.
"""

from __future__ import annotations

import subprocess  # nosec B404 - TimeoutExpired type only; utils/ops_runner.py is the sole subprocess boundary
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import FastAPI

from harness.agent_policy import RUN_ID_RE, CheckProfileError, available_profiles, resolve_check_profiles
from harness.schemas import (
    _MAX_ITERATIONS_CEILING,
    AgentDecisionRequest,
    AgentPublishRequest,
    AgentRunRequest,
)
from utils.errors import AgenticError
from utils.logger import redact_sensitive
from utils.ops_runner import (
    REAL_REPO_RUN_MAX_TIMEOUT_SEC,
    OpsError,
    OpsResult,
    real_repo_run_budget_sec,
)
from utils.tool_broker import ToolDenied


class _ClaimRelease(Protocol):
    def claim(self) -> bool: ...
    def release(self) -> None: ...


def register_agent_routes(
    app: FastAPI,
    *,
    guarded: list[Any],
    agent_run_gate: _ClaimRelease,
    generation_gate: _ClaimRelease,
) -> None:
    """Register /api/agent/* on ``app`` with create_app's gates injected.

    Monkeypatched names (run_agentic_op, _agent_run_tool_allowlist, _err, …)
    are resolved on harness.server at *request* time, not registration time:
    tests patch those attributes on the server module after create_app() in
    some cases (allowlist) and before it in others (the shim).
    """
    # Late import: register_agent_routes is called from create_app after
    # harness.server has finished importing this module, so the cycle is
    # request-time only and the server module is fully initialized.
    from harness import server as hs

    def _validated_run_id(run_id: str) -> str:
        """Reject a malformed run_id before it becomes a `--run-id=` argv element."""
        # fullmatch, not match: Python's `$` also matches just before a
        # trailing newline, so `match()` would accept "<32 hex>\n" -- the
        # pattern text stays byte-identical to agentic's so the drift test
        # still compares the two.
        if not RUN_ID_RE.fullmatch(run_id):
            raise hs._err(
                hs._HTTP_BAD_REQUEST,
                AgenticError(
                    "run_id must be 32 lowercase hex characters",
                    code="INVALID_RUN_ID",
                    details={"run_id": run_id[: hs._MAX_ECHOED_RUN_ID_LEN]},
                ),
            )
        return run_id

    def _disabled_layer_success(result: object, payload: dict[str, Any]) -> bool:
        # CLI _disabled_noop / _deepagent_github_disabled_noop exit 0 with a
        # human banner. That is the operator-facing CLI contract. HTTP callers
        # cannot treat the same envelope as a successful run, push, or publish
        # (issue #1337: POST /push on an all-zero run id returned 200 + ok=true).
        #
        # Prefer the raw OpsResult. to_dict() redacts stdout, and an operator
        # redact_secrets_like pattern can erase the banner before we see it.
        # Test stubs that only implement to_dict still fall through to payload.
        ok = getattr(result, "ok", payload.get("ok"))
        if ok is not True:
            return False
        parsed = result.parsed if hasattr(result, "parsed") else payload.get("parsed")
        # A real JSON record (status/decide/push) can quote this banner in a
        # pending CyClaw diff. parsed is null only for the non-JSON no-op.
        if parsed is not None:
            return False
        stdout = getattr(result, "stdout", None)
        if not isinstance(stdout, str):
            stdout = payload.get("stdout") or ""
        if not isinstance(stdout, str):
            return False
        return (
            "Agentic layer disabled" in stdout
            or "real-repo coding subsystem disabled" in stdout
        )

    def _agentic_call(action: str, call: Callable[[], OpsResult]) -> dict:
        """Run one shim call, mapping every failure into the console's envelope.

        Takes a thunk rather than ``**kwargs`` on purpose: forwarding kwargs
        through this helper would have to type them as ``object`` and silence
        the resulting mismatch, which would stop mypy checking the ONE thing
        worth checking here -- that each route passes ``run_agentic_op`` the
        kwargs that action actually takes. With a thunk the call is written at
        its route, fully typed, and this only owns the exception mapping.

        Note what is NOT translated: a non-zero CLI exit is a successful shim
        call, so it returns HTTP 200 carrying ok=false plus the CLI's own
        stderr. That is the same contract GET /api/github/status already has,
        and it is what lets the console distinguish "the run was refused"
        (exit 4) from "the request was malformed" (400) without parsing prose.

        The one exit-0 case that IS translated: the disabled-layer banner.
        CLI no-op success must not become an HTTP success envelope.
        """
        try:
            result = call()
            payload = result.to_dict()
        except OpsError as exc:
            raise hs._err(hs._HTTP_BAD_REQUEST, AgenticError(redact_sensitive(str(exc)))) from exc
        except subprocess.TimeoutExpired as exc:
            raise hs._timeout_err(action, exc) from exc
        except OSError as exc:
            # ops_runner._write_body / _write_checks_file raise a bare OSError
            # (disk full, unwritable temp dir) before run_agentic_op ever
            # spawns the CLI -- neither of the two excepts above catches that,
            # so it used to escape as an unhandled 500 the console's api()
            # helper can't parse. 502: the shim's own environment failed, the
            # same class of "dependency, not the request, is broken" as the
            # HarnessLLMError -> 502 mapping in /api/chat.
            raise hs._err(
                hs._HTTP_BAD_GATEWAY,
                AgenticError(f"{action} shim failed: {redact_sensitive(str(exc))}", code="SHIM_IO_ERROR"),
            ) from exc
        if _disabled_layer_success(result, payload):
            raise hs._err(
                hs._HTTP_CONFLICT,
                AgenticError(
                    "agentic layer is disabled; nothing was executed",
                    code="AGENTIC_DISABLED",
                    details={"action": action},
                ),
            )
        return payload

    @app.get("/api/agent/checks")
    def agent_checks() -> dict:
        """The selectable verification profiles. Open: a static allow-list listing.

        Read-only, spawns nothing, and reveals only the names of commands this
        module already hardcodes -- so it stays outside `guarded` for the same
        reason /api/registry does: the console must be able to populate its
        help before the operator has entered a key.
        """
        return {"profiles": [{"name": name, "description": desc} for name, desc in available_profiles()]}

    @app.post("/api/agent/run", dependencies=guarded)
    def agent_run(req: AgentRunRequest) -> dict:
        """Start one real-repo run. BLOCKS until the run finishes.

        The wall-clock budget is derived per request by
        utils.ops_runner._real_repo_run_timeout_sec from this request's own
        --max-iterations and check count, capped at 3600s -- it was a flat 900s
        until that flat ceiling started killing legitimate runs once the
        planner timeout became configurable (default 720s x 3 iterations).
        A shape whose uncapped budget exceeds that cap is refused up front
        with 422 AGENTIC_BUDGET_EXCEEDED instead of being SIGKILLed at the
        cap (which leaks the clone and a stuck 'running' record).

        Deliberately synchronous, and deliberately not a poll-a-background-job
        design, for two reasons found in the backend rather than chosen here:
        agentic/cli.py writes the run record only when the run ENDS, so a
        status poll returns "not found" for the whole run and there is no
        progress to report; and the run_id is minted inside that subprocess and
        first appears in its stdout, so a route that returned early would hand
        the operator no handle to approve with. GET /api/github/status already
        blocks on a 120s subprocess in this same app -- this is that shape with
        a longer budget, not a new one.
        """
        try:
            checks = resolve_check_profiles(req.checks)
        except CheckProfileError as exc:
            raise hs._err(
                hs._HTTP_BAD_REQUEST,
                AgenticError(str(exc), code="UNKNOWN_CHECK_PROFILE", details={"requested": req.checks}),
            ) from exc
        # Refuse shapes whose own budget formula exceeds the subprocess cap.
        # Past the cap, subprocess.run SIGKILLs the CLI mid-flight, which leaks
        # the repo clone and a permanently-'running' record no later call can
        # resolve (utils/ops_runner.py documents that path as unrecoverable).
        # Failing here, with the arithmetic, is the legible version of that
        # outcome -- and unlike the SIGKILL it costs nothing.
        estimated_sec = real_repo_run_budget_sec(req.max_iterations, len(checks))
        if estimated_sec > REAL_REPO_RUN_MAX_TIMEOUT_SEC:
            # The envelope is tight and config-dependent: the budget sums
            # planner_timeout_sec (720 shipped) per iteration plus 120s per
            # check per iteration, so with the shipped config this admits about
            # 3 iterations at one check, and fewer as checks are added --
            # selecting every available profile at the default iteration count
            # already exceeds the cap. A bare refusal would leave the operator
            # guessing which knob to turn, so compute the largest iteration
            # count that fits THEIR check count by asking the same authoritative
            # function (never a second copy of the arithmetic).
            fits = [
                n
                for n in range(1, _MAX_ITERATIONS_CEILING + 1)
                if real_repo_run_budget_sec(n, len(checks)) <= REAL_REPO_RUN_MAX_TIMEOUT_SEC
            ]
            max_fitting = max(fits, default=0)
            remedy = (
                f"at {len(checks)} check profile(s) the most that fits is max_iterations={max_fitting}"
                if max_fitting
                else f"no iteration count fits with {len(checks)} check profile(s) -- select fewer checks"
            )
            raise hs._err(
                hs._HTTP_UNPROCESSABLE,
                AgenticError(
                    f"requested shape budgets ~{estimated_sec}s but the synchronous run cap is "
                    f"{REAL_REPO_RUN_MAX_TIMEOUT_SEC}s, so the run would be killed mid-flight; "
                    f"{remedy}",
                    code="AGENTIC_BUDGET_EXCEEDED",
                    details={
                        "estimated_sec": estimated_sec,
                        "cap_sec": REAL_REPO_RUN_MAX_TIMEOUT_SEC,
                        "max_iterations": req.max_iterations,
                        "check_count": len(checks),
                        "max_iterations_that_fit": max_fitting,
                    },
                ),
            )
        try:
            hs.assert_allowed(
                hs._AGENT_RUN_TOOL,
                ("real-repo-run",),
                allowlist=hs._agent_run_tool_allowlist(),
            )
        except ToolDenied as exc:
            raise hs._err(hs._HTTP_FORBIDDEN, exc) from exc
        # Two concurrent real-repo-run subprocesses always target the same
        # agentic.deepagent_github.base_url, so a double-click/retried POST
        # spawning two of them (each paying for its own LLM calls) must be
        # excluded unconditionally -- independent of whatever /api/chat is
        # doing.
        if not agent_run_gate.claim():
            raise hs._err(
                hs._HTTP_CONFLICT,
                AgenticError(
                    "another agent run is already in progress",
                    code="AGENT_RUN_BUSY",
                ),
            )
        try:
            # Additionally exclude a concurrent chat turn, but only when it
            # would actually contend for the same backend: real-repo-run's
            # planner is built straight from agentic.deepagent_github.base_url
            # (independent of models.local_llm.fallback), so when fallback is
            # active and /api/chat has moved to a live fallback backend, the
            # two are not contending for anything and must not block each
            # other -- see _agent_run_shares_chat_backend's docstring.
            shares_backend = hs._agent_run_shares_chat_backend()
            if shares_backend and not generation_gate.claim("agent"):
                raise hs._err(
                    hs._HTTP_CONFLICT,
                    AgenticError(
                        "a local model chat turn is already running",
                        code="AGENT_RUN_BUSY",
                    ),
                )
            try:
                return _agentic_call(
                    "real-repo-run",
                    lambda: hs.run_agentic_op(
                        "real-repo-run",
                        instruction=req.instruction,
                        checks=checks,
                        branch=req.branch,
                        commit_message=req.commit_message,
                        reason=req.reason,
                        confirm=req.confirm,
                        max_iterations=req.max_iterations,
                        plan=req.plan,
                        read_files=req.read_files,
                        pr=req.pr,
                        issue=req.issue,
                    ),
                )
            finally:
                if shares_backend:
                    generation_gate.release()
        finally:
            agent_run_gate.release()

    @app.get("/api/agent/runs/{run_id}", dependencies=guarded)
    def agent_run_status(run_id: str) -> dict:
        """Read one run's persisted record.

        Guarded despite being a read: the record names a branch, the changed
        file paths, and the clone's absolute location, and serving it spawns a
        subprocess like /api/github/status does.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-status", lambda: hs.run_agentic_op("real-repo-run-status", run_id=checked)
        )

    @app.post("/api/agent/runs/{run_id}/decision", dependencies=guarded)
    def agent_run_decision(run_id: str, req: AgentDecisionRequest) -> dict:
        """Approve (commit) or reject (discard) a pending run.

        This is the request that can actually put a commit in the clone --
        the only one in this app that reaches a git write. It performs no
        gating of its own on purpose: the four conditions
        (allow_git_write_tools, a pending record, a non-terminal status, git's
        own refusal of an empty second commit) all live in agentic/, where
        they are tested, and re-implementing any of them here would create a
        second place for them to drift.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-decide",
            lambda: hs.run_agentic_op("real-repo-run-decide", run_id=checked, decision=req.decision),
        )

    @app.post("/api/agent/runs/{run_id}/push", dependencies=guarded)
    def agent_run_push(run_id: str) -> dict:
        """Push an approved run's branch to origin. The first request here that leaves the box.

        Its own route rather than a field on the decision body because it is
        its own decision, taken AFTER approve: the backend's
        ``real-repo-run-decide`` refuses a second call on an already-decided
        run, so an approve-then-push sequence through one endpoint could never
        reach the push. It carries no body -- invoking it IS the confirmation,
        the same shape ``decision: "approve"`` already has.

        Gating stays in ``agentic/`` (``allow_git_write_tools``, ships
        ``false``, enforced inside ``push_branch``; plus the run-state guard
        ``require_approved_for_push``). Re-implementing either here would give
        them a second place to drift.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-push", lambda: hs.run_agentic_op("real-repo-run-push", run_id=checked)
        )

    @app.post("/api/agent/runs/{run_id}/publish", dependencies=guarded)
    def agent_run_publish(run_id: str, req: AgentPublishRequest) -> dict:
        """Open a draft PR for an already-pushed run. The most gated route in this app.

        Reaches ``agentic/writer.py``'s six-gate chain. That chain's first gate
        (``EXECUTION_ENABLED``) ships ``True`` following the operator
        enablement of 2026-08-07 -- it is NO LONGER the backstop this docstring
        once described. What refuses on a shipped checkout is gate 0,
        ``agentic.enabled`` (ships ``false``), plus the per-call
        ``reason``/``confirm`` this route's own body must carry. Once an
        operator turns the layer on, this route can open a real draft PR.

        Read that plainly: this is a network-reachable path to a GitHub
        mutation, held by one config boolean. The route-level guard chain
        (rate limit -> same-origin -> API key -> CSRF token) is what keeps it
        to an authenticated, same-origin, local caller. See
        ``docs/agentic/GITHUB_WRITE_ENABLEMENT.md`` for the full chain and the
        ``CYCLAW_AGENTIC_WRITE_DISABLE`` rollback.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-publish",
            lambda: hs.run_agentic_op(
                "real-repo-run-publish", run_id=checked, reason=req.reason, confirm=req.confirm,
            ),
        )

    @app.post("/api/agent/runs/{run_id}/discard", dependencies=guarded)
    def agent_run_discard(run_id: str) -> dict:
        """Reclaim a decided run's clone from disk. The only route that frees disk.

        Not redundant with ``reject``: reject applies only to a run still
        awaiting a decision, and an APPROVED run's clone is deliberately
        retained past its decision because push/publish still need it. Without
        this route a console-driven operator accumulates one full repository
        clone per approved run under ``workspace_root`` with no way to free
        any of them -- the CLI had the reclamation step, the console had no
        path to it.

        The refusal for a still-pending run lives in ``agentic/`` (discarding
        then would destroy a live candidate with no decision ever recorded),
        not here.
        """
        checked = _validated_run_id(run_id)
        return _agentic_call(
            "real-repo-run-discard", lambda: hs.run_agentic_op("real-repo-run-discard", run_id=checked)
        )
