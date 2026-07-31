"""Command-line entry point: ``python -m agentic.cli <subcommand>``.

Subcommands:

    status         Print agentic config + gh availability + registry summary.
    context        Fetch read-only GitHub context (--pr N | --issue N | --repo).
    propose-skill  Preview a skills-registry change (never writes).
    apply-skill    Apply a skills-registry change (governed; needs --reason).
    deepagent-plan Probe the Deep Agents harness (read-only; --provider needs
                   --confirm-online before any cloud egress).
    real-repo-run        Clone a real repo and run plan/patch/verify against it
                          (governed by --reason/--confirm; never commits).
    real-repo-run-status  Report a real-repo run's persisted status.
    real-repo-run-decide  Approve (commit) or reject (discard) a pending run.
    real-repo-run-discard Reclaim a decided (or orphaned) run's clone from disk.
    test           Run the pre-flight self-test.

Exit codes:
    0    success (also the clean no-op when agentic.enabled is false)
    2    operation failed (gh error, registry error)
    3    config / environment problem (gh missing, config invalid)
    4    a write was refused by the gate

This module never imports gate.py, graph.py, or mcp_hybrid_server.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING

from agentic.config import AgenticConfig, load_agentic_config
from utils.errors import (
    AgenticConfigError,
    AgenticError,
    AgenticWriteRefused,
    GhNotInstalledError,
    GhVersionError,
    PromptInjectionError,
    SkillRegistryError,
)
from utils.logger import _get_config, audit_log

if TYPE_CHECKING:
    # Only agentic.executor's Check TYPE is needed at module scope, for
    # _load_checks_file's return annotation -- the actual import stays
    # lazy, inside the function, matching every other agentic.* import in
    # this file (each subcommand's own imports load only when that
    # subcommand runs, not at --help time).
    from agentic.executor import Check

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3
EXIT_REFUSED = 4


def _heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def _kv(key: str, value: object) -> None:
    print(f"  {key:.<22} {value}")


def _err(text: str) -> None:
    print(f"  [ERR ] {text}", file=sys.stderr)


def _ok(text: str) -> None:
    print(f"  [OK  ] {text}")


def _load(args: argparse.Namespace) -> AgenticConfig | None:
    try:
        return load_agentic_config(args.config)
    except AgenticConfigError as exc:
        _err(f"Config error: {exc.message}")
        for k, v in (exc.details or {}).items():
            _err(f"   {k}: {v}")
        return None


def _disabled_noop() -> int:
    _heading("Agentic layer disabled")
    print("  agentic.enabled is false in config.yaml; nothing to do.")
    print("  Set agentic.enabled: true to use this layer.")
    return EXIT_OK


def _deepagent_github_disabled_noop() -> int:
    _heading("Deep Agents / real-repo coding subsystem disabled")
    print("  agentic.deepagent_github.enabled is false in config.yaml; nothing to do.")
    print("  Set agentic.deepagent_github.enabled: true to use this subsystem.")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV

    _heading("CyClaw Agentic Status")
    _kv("enabled", getattr(cfg, "enabled", False))
    _kv("repo", cfg.repo)
    _kv("mode", cfg.mode)
    _kv("writes_enabled", cfg.writes_enabled)
    _kv("gh_min_version", cfg.gh_min_version)
    _kv("registry_path", cfg.registry_path)
    _kv("allowed_read_ops", ", ".join(cfg.allowed_read_ops))

    from agentic.gh_client import check_gh_version
    try:
        v = check_gh_version(min_version=cfg.gh_min_tuple)
        _ok(f"gh {v[0]}.{v[1]}.{v[2]}")
    except (GhNotInstalledError, GhVersionError) as exc:
        _err(exc.message)

    try:
        from agentic.registry import SkillRegistry
        reg = SkillRegistry(_get_config(args.config), cfg)
        _kv("registry_version", reg.version())
        _kv("skills", ", ".join(reg.list_skills()) or "(none)")
    except SkillRegistryError as exc:
        _err(f"Registry: {exc.message}")
    return EXIT_OK


def cmd_context(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()

    from agentic import context
    # Pass the full config so the injection scan over GitHub-sourced text sees the
    # operator's banned_patterns, not just the OWASP baseline.
    app_cfg = _get_config(args.config)
    try:
        if args.pr is not None:
            bundle = context.fetch_pr_context(cfg, args.pr, include_diff=not args.no_diff, app_cfg=app_cfg)
        elif args.issue is not None:
            bundle = context.fetch_issue_context(cfg, args.issue, app_cfg=app_cfg)
        else:
            bundle = context.fetch_repo_context(cfg, app_cfg=app_cfg)
    except (GhNotInstalledError, GhVersionError) as exc:
        _err(exc.message)
        return EXIT_ENV
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL

    print(json.dumps(bundle, indent=2, default=str))
    return EXIT_OK


def _read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            return f.read()
    return args.body or ""


def cmd_deepagent_plan(args: argparse.Namespace) -> int:
    """Live-fire probe for the Deep Agents harness. Read-only, writes nothing.

    Three things happen, in order: the six-condition cloud chain is asserted when
    a provider is named, GitHub context is fetched through the injection-scanned
    path, and the harness build is probed so its real gate state is reported.

    Deliberately does NOT invoke the agent. Invocation needs a scoped
    ProposerWorkspaceTools instance, and the real-repo workspace surface does not
    exist yet -- so a --invoke flag here would either fake a workspace or fail. It
    lands with that surface instead.
    """
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()

    from agentic import context
    from agentic.deepagent_github.builder import build_deepagent_github
    from agentic.deepagent_github.core import DeepAgentGitHubTask
    from agentic.deepagent_github.model_adapter import cloud_key_available
    from agentic.deepagent_github.runners import draft_plan

    app_cfg = _get_config(args.config)
    provider: str | None = args.provider
    if provider:
        # Gates 3 and 4. cloud_provider() returns None unless BOTH
        # allow_cloud_providers and the provider's own enabled flag are true.
        if cfg.deepagent_github.cloud_provider(provider) is None:
            _err(f"cloud provider {provider!r} is not enabled (gates 3/4)")
            return EXIT_ENV
        # Gate 5: key presence only, no network probe.
        if not cloud_key_available(provider):
            _err(f"cloud provider {provider!r} has no API key set (gate 5)")
            return EXIT_ENV
        # Gate 6: per-run human confirmation, the agentic analog of
        # user_confirmed_online. Same shape as apply-skill's --confirm.
        if not args.confirm_online:
            _err(f"--confirm-online is required to drive the loop with {provider!r} (gate 6)")
            return EXIT_REFUSED
        audit_log({"event": "agentic_deepagent_cloud_confirmed", "provider": provider}, cfg=app_cfg)

    try:
        if args.pr is not None:
            bundle = context.fetch_pr_context(cfg, args.pr, app_cfg=app_cfg)
        elif args.issue is not None:
            bundle = context.fetch_issue_context(cfg, args.issue, app_cfg=app_cfg)
        else:
            bundle = context.fetch_repo_context(cfg, app_cfg=app_cfg)
    except (GhNotInstalledError, GhVersionError) as exc:
        _err(exc.message)
        return EXIT_ENV
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL

    # An injection finding means GitHub-sourced text carries an injection shape.
    # The inbound scan is advisory (a PR may legitimately DISCUSS injection), but
    # a planner is exactly the consumer that must not act on one.
    findings = bundle.get("governance_findings") or []
    blocking = _blocking_context_findings(bundle)
    if blocking:
        _err(
            f"refusing to plan: {len(blocking)} injection finding(s) in the fetched context "
            f"({_describe_findings(blocking)})"
        )
        return EXIT_FAIL

    task = DeepAgentGitHubTask(
        task_id=args.task_id,
        repo=cfg.repo,
        instruction=args.instruction,
        issue_number=args.issue,
        pr_number=args.pr,
    )
    try:
        plan = draft_plan(task)
        # No workspace_tools: the probe reports the real gate state rather than
        # constructing an agent. "workspace_required" is the expected best case.
        build = build_deepagent_github(cfg, cloud_provider=provider, config_path=args.config, cfg=app_cfg)
    except AgenticWriteRefused as exc:
        _err(exc.message)
        return EXIT_REFUSED
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL

    print(json.dumps({
        "task_id": task.task_id,
        "repo": task.repo,
        "provider": provider or cfg.deepagent_github.provider,
        "governance_findings": findings,
        "plan": asdict(plan),
        "build": {
            "created": build.created,
            "status": build.status,
            "reason": build.reason,
            "subagents": list(build.subagent_names),
            "interrupt_on": sorted(build.interrupt_on),
        },
    }, indent=2, default=str))
    return EXIT_OK


# Bounds the task-context text folded into the real-repo planner's prompt
# (title/body/diff), distinct from agentic.gh_client.MAX_DIFF_CHARS (200_000):
# that constant bounds what gh_client fetches for DISPLAY/audit purposes, sized
# for a human or a log line. This bounds what actually goes into a single-shot
# LOCAL model prompt, where CLAUDE.md's own documented footgun applies --
# Ollama's num_ctx must clear max_context_tokens + max_tokens + ~1500 headroom,
# and a small local model has far less room than a fetched diff can fill.
_MAX_LOOP_CONTEXT_CHARS = 8_000


def _bundle_context_text(bundle: dict[str, object]) -> str | None:
    """Bounded, already-governance-scanned task context for the real-repo planner.

    Without this, ``run_real_repo_loop`` saw only the operator's free-text
    ``--instruction`` and prior rejection feedback -- the PR/issue title,
    body, and diff that ``cmd_real_repo_run`` fetches and injection-scans were
    discarded before ever reaching the model call, so the planner had to
    propose complete replacement files without seeing the task that motivated
    them. Every field pulled here already went through
    ``bundle["governance_findings"]`` at fetch time, and ``cmd_real_repo_run``
    has already refused to proceed if any of them carried an injection finding
    by the time this is called (see ``_blocking_context_findings``).

    Returns ``None`` for a ``--repo``-mode bundle (repo overview + shortlists,
    no single target) rather than manufacture marginal-value context from it;
    the operator's own instruction is expected to be self-contained for that
    mode. Read tool access to the clone's actual file contents is deliberately
    NOT provided here -- see ``run_real_repo_loop``'s ``context`` parameter
    docstring for why that would be a materially different design.
    """
    parts: list[str] = []
    pr = bundle.get("pr")
    if isinstance(pr, dict):
        if pr.get("title"):
            parts.append(f"PR title: {pr['title']}")
        if pr.get("body"):
            parts.append(f"PR body:\n{pr['body']}")
    issue = bundle.get("issue")
    if isinstance(issue, dict):
        if issue.get("title"):
            parts.append(f"Issue title: {issue['title']}")
        if issue.get("body"):
            parts.append(f"Issue body:\n{issue['body']}")
    diff = bundle.get("diff")
    if isinstance(diff, str) and diff:
        parts.append(f"Diff:\n{diff}")
    if not parts:
        return None
    text = "\n\n".join(parts)
    if len(text) > _MAX_LOOP_CONTEXT_CHARS:
        text = text[:_MAX_LOOP_CONTEXT_CHARS] + f"\n... [context truncated at {_MAX_LOOP_CONTEXT_CHARS} chars]"
    return text


def _blocking_context_findings(bundle: dict[str, object]) -> list[dict]:
    """The fetched-context findings that must stop a run before a model sees it.

    Selects on the finding CODE, never on a severity string.
    ``agentic.context._injection_findings`` documents that it emits
    ``"warning"`` and never ``"critical"`` -- deliberately, because it is a READ
    path and a PR that merely discusses injection must stay fetchable, so it
    reports and leaves the refusal to whichever layer feeds a model. Both
    planner entry points nonetheless filtered for ``severity == "critical"``,
    which no producer ever sets, so the refusal could not fire for any input and
    attacker-authored PR text reached the planner prompt ahead of the operator's
    own instruction. Gating on the code is what makes that documented division
    of labour real.

    ``SCANNER_UNAVAILABLE_CODE`` blocks as well, fail-closed: it means the
    pattern set compiled empty, so the text was never actually scanned, and
    forwarding unscanned third-party text into a planner is the exact outcome
    the scan exists to prevent. Reads stay permissive (``cmd_context`` does not
    call this); only the model-feeding paths refuse.
    """
    from agentic.context import INJECTION_FINDING_CODE, SCANNER_UNAVAILABLE_CODE

    blocking = {INJECTION_FINDING_CODE, SCANNER_UNAVAILABLE_CODE}
    findings = bundle.get("governance_findings") or []
    if not isinstance(findings, list):
        return []
    return [f for f in findings if isinstance(f, dict) and f.get("code") in blocking]


def _describe_findings(findings: list[dict]) -> str:
    # Names the rule and the field that fired, never the text that fired it --
    # the same discipline agentic/context.py:81-86 applies when it records
    # pattern SOURCES rather than match objects.
    return ", ".join(f"{f.get('code')}:{f.get('field') or 'bundle'}" for f in findings)


def _load_checks_file(path: str) -> tuple[Check, ...]:
    """Parse a JSON manifest of verification checks into `Check` objects.

    Format: a non-empty JSON list of ``{"name": str, "argv": [str, ...],
    "timeout_sec": int (optional)}``. Required, not optional, and never
    defaulted to ``agentic.executor.default_checks()`` -- that function
    assumes THIS repository's own toolchain (a CyClaw-specific invariant-guard
    path); guessing a test/lint command for an arbitrary configured repo would
    be exactly the kind of invented default this codebase avoids. ``argv`` is
    already a JSON list of literal strings, never a single command string --
    there is no shell-splitting anywhere in this path.
    """
    from agentic.executor import Check

    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise AgenticError(f"cannot read checks file: {exc}", details={"path": path}) from exc
    if not isinstance(data, list) or not data:
        raise AgenticError("checks file must contain a non-empty JSON list", details={"path": path})
    checks: list[Check] = []
    for entry in data:
        if not isinstance(entry, dict) or "name" not in entry or "argv" not in entry:
            raise AgenticError("each check entry needs 'name' and 'argv'", details={"path": path, "entry": entry})
        argv = entry["argv"]
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) for item in argv):
            raise AgenticError("check 'argv' must be a non-empty list of strings", details={"path": path})
        kwargs: dict[str, int] = {}
        if "timeout_sec" in entry:
            kwargs["timeout_sec"] = int(entry["timeout_sec"])
        checks.append(Check(entry["name"], tuple(argv), **kwargs))
    return tuple(checks)


def _real_repo_runs_dir(cfg: AgenticConfig) -> Path:
    return Path(cfg.deepagent_github.workspace_root) / "runs"


def cmd_real_repo_run(args: argparse.Namespace) -> int:
    """Start a real-repo coding run: clone, plan/patch/verify, but never commit.

    This command's own exit code reflects whether the RUN completed, not
    whether a candidate was accepted -- check the printed record's "status"
    field for that (``pending_decision`` vs ``exhausted``). A candidate that
    passes still waits for ``real-repo-run-decide`` before anything is
    committed; see ``agentic.real_repo_loop``'s module docstring for why.
    """
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()
    # Checked here, before any network I/O, not only inside
    # run_real_repo_loop's own _require_run_gates (which only ever checks
    # allow_git_write_tools/reason/confirm -- it has no view of this switch).
    # Without this, a config with agentic.enabled: true and
    # deepagent_github.enabled: false still performed a live GitHub context
    # fetch and a full network `gh repo clone` before failing -- the switch
    # was dead config on this, the subsystem's own newest entry point, while
    # build_deepagent_github (the other consumer) correctly composes both:
    # `bool(getattr(agentic_config, "enabled", False) and deep_cfg.enabled)`.
    # This check is deliberately NOT mirrored in real-repo-run-status/-decide/
    # -discard: those resolve or inspect work THIS command already started,
    # and gating them the same way would strand a pending_decision run with no
    # path forward the moment the switch flips off (see cmd_real_repo_run_decide's
    # own comment for why that's actively harmful, not just inconsistent).
    if not cfg.deepagent_github.enabled:
        return _deepagent_github_disabled_noop()
    # Checked eagerly, before the context fetch or the clone -- both real
    # network I/O -- matching the existing eager validation of --checks-file
    # below. cfg.deepagent_github.model ships "" (config.yaml), which passes
    # config validation (an empty string is still a valid string), so the only
    # place this used to surface was LocalProposerClient.invoke() raising
    # AgenticError -- reached only AFTER a full GitHub context fetch and a
    # full network `gh repo clone` had already run. An operator following
    # GITHUB_WRITE_ENABLEMENT.md's enablement steps, which never mention
    # setting this value, got zero successful runs and paid for a clone every
    # single time before finding out why.
    if not cfg.deepagent_github.model.strip():
        _err("agentic.deepagent_github.model must be configured before real-repo-run")
        return EXIT_ENV

    from agentic import context
    from agentic.deepagent_github.repo_workspace import RepoWorkspaceTools
    from agentic.harness_optimizer.model_adapter import LocalProposerClient
    from agentic.real_repo_loop import run_real_repo_loop
    from agentic.real_repo_run_store import RealRepoRunRecord, new_run_id, save_run

    app_cfg = _get_config(args.config)
    try:
        if args.pr is not None:
            bundle = context.fetch_pr_context(cfg, args.pr, app_cfg=app_cfg)
        elif args.issue is not None:
            bundle = context.fetch_issue_context(cfg, args.issue, app_cfg=app_cfg)
        else:
            bundle = context.fetch_repo_context(cfg, app_cfg=app_cfg)
    except (GhNotInstalledError, GhVersionError) as exc:
        _err(exc.message)
        return EXIT_ENV
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL

    blocking = _blocking_context_findings(bundle)
    if blocking:
        _err(
            f"refusing to run: {len(blocking)} injection finding(s) in the fetched context "
            f"({_describe_findings(blocking)})"
        )
        return EXIT_FAIL
    # Safe to forward from here on: the refusal above covers every field this
    # pulls from (title/body/diff), and it gates on the finding CODE the scanner
    # actually emits rather than a severity string it documents it never sets --
    # so unlike the check this replaced, it can fire.
    context_text = _bundle_context_text(bundle)

    try:
        checks = _load_checks_file(args.checks_file)
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_ENV

    runs_dir = _real_repo_runs_dir(cfg)
    run_id = new_run_id()

    try:
        tools = RepoWorkspaceTools.clone(cfg, config_path=args.config, cfg=app_cfg)
    except (GhNotInstalledError, GhVersionError) as exc:
        _err(exc.message)
        return EXIT_ENV
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL

    # Persisted BEFORE the loop runs, not only after it finishes. This command
    # runs under a hard wall-clock budget (see utils/ops_runner.py's
    # real-repo-run action timeout); a kill on overrun previously left this
    # process with no chance to reach any save_run call below, so the clone
    # under workspace_root had NOTHING on disk pointing at it -- unreachable
    # by real-repo-run-status, and indistinguishable from garbage by anything
    # that might reclaim it later (see real-repo-run-discard). This also makes
    # the "running" state agentic/real_repo_run_store.py's own docstring
    # already documents an actual, observable state rather than one nothing
    # ever writes.
    save_run(
        runs_dir,
        RealRepoRunRecord(run_id=run_id, repo=cfg.repo, dest=str(tools.worktree), status="running"),
    )

    client = LocalProposerClient(base_url=cfg.deepagent_github.base_url, model=cfg.deepagent_github.model)
    try:
        result = run_real_repo_loop(
            tools,
            client,
            instruction=args.instruction,
            checks=checks,
            branch_name=args.branch,
            commit_message=args.commit_message,
            max_iterations=args.max_iterations,
            reason=args.reason,
            confirm=args.confirm,
            context=context_text,
            read_paths=args.read_file,
            protected_write_paths=cfg.deepagent_github.protected_write_paths,
            max_write_budget_bytes=cfg.deepagent_github.max_write_budget_bytes,
            config_path=args.config,
            cfg=app_cfg,
        )
    except AgenticWriteRefused as exc:
        tools.close()
        _err(exc.message)
        return EXIT_REFUSED
    except AgenticError as exc:
        record = RealRepoRunRecord(
            run_id=run_id, repo=cfg.repo, dest=str(tools.worktree), status="failed", error=exc.message,
        )
        save_run(runs_dir, record)
        tools.close()
        _err(exc.message)
        return EXIT_FAIL
    finally:
        client.close()

    if result.accepted:
        record = RealRepoRunRecord(
            run_id=run_id,
            repo=cfg.repo,
            dest=str(tools.worktree),
            status="pending_decision",
            branch_name=result.branch_name,
            commit_message=result.commit_message,
            # The cumulative set across every iteration, not just the accepted
            # one's own: write_file mutates the same persistent clone across
            # attempts, so a rejected earlier iteration's file can still be on
            # disk and required for the accepted iteration's checks to have
            # passed. Staging only the last iteration's list could silently
            # drop it from the approved commit. See RealRepoLoopResult.changed_files.
            changed_files=list(result.changed_files),
            iterations=len(result.iterations),
        )
    else:
        record = RealRepoRunRecord(
            run_id=run_id, repo=cfg.repo, dest=str(tools.worktree), status="exhausted",
            iterations=len(result.iterations),
        )
        tools.close()  # nothing accepted -- nothing worth keeping the clone for
    save_run(runs_dir, record)
    print(json.dumps(record.to_dict(), indent=2))
    return EXIT_OK


_MAX_STATUS_DIFF_CHARS = 20_000


def _render_pending_diff(cfg: AgenticConfig, dest: str, config_path: str, changed_files: Sequence[str]) -> str:
    """Render a pending candidate's worktree diff, or say plainly why not.

    This is the ONLY point a human decides approve/reject -- real-repo-run-decide
    itself claimed all along that this review happens ("a human can review the
    diff first (tools.diff())"), but nothing anywhere actually called it. A
    status check must never crash just because the clone became unreachable
    (an operator deleted it by hand, a permission changed) between the run
    and this query -- it explains why the diff is unavailable instead.

    ``git diff`` alone shows nothing for a brand-new untracked file (see
    ``RepoWorkspaceTools.diff``'s own docstring) -- a create-only candidate
    would otherwise render an empty diff, which is actively misleading
    alongside a non-empty ``changed_files`` list: it reads as "nothing to
    review" when a new file is in fact about to be committed unseen. New
    files among ``changed_files`` (restricted to exactly that list, not
    every untracked path in the clone, in case unrelated cruft exists there)
    are appended as their full current content.

    Deliberately does NOT call ``tools.close()`` on the reattached instance:
    that method always removes the clone from disk regardless of whether it
    came from ``clone()`` or ``attach()``, and this is a read-only peek, not
    ownership of the clone's lifecycle. The unreleased directory descriptor
    is reclaimed by the OS when this short-lived CLI-subprocess-per-call
    process exits (see I6) -- not a leak in the way it would be in a
    long-running process.
    """
    from agentic.deepagent_github.repo_workspace import RepoWorkspaceTools

    try:
        tools = RepoWorkspaceTools.attach(cfg, Path(dest), config_path=config_path)
        tracked_diff = tools.diff()
        parts = [tracked_diff] if tracked_diff else []
        untracked = set(tools.untracked_files()) & set(changed_files)
        for path in sorted(untracked):
            content = tools.read_file(path)
            parts.append(f"--- new file: {path} ---\n{content}")
    except AgenticError as exc:
        return f"[diff unavailable: {exc.message}]"
    diff_text = "\n\n".join(parts)
    if not diff_text:
        return "[no diff to show -- the candidate reported changed files, but none were tracked or new]"
    if len(diff_text) > _MAX_STATUS_DIFF_CHARS:
        return diff_text[:_MAX_STATUS_DIFF_CHARS] + f"\n... [diff truncated at {_MAX_STATUS_DIFF_CHARS} chars]"
    return diff_text


def cmd_real_repo_run_status(args: argparse.Namespace) -> int:
    """Report a real-repo run's persisted status. Read-only, no side effects."""
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()

    from agentic.real_repo_run_store import PENDING_DECISION, load_run

    try:
        record = load_run(_real_repo_runs_dir(cfg), args.run_id)
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL
    payload = record.to_dict()
    # Only rendered when a decision is actually open: every other status
    # (running/approved/rejected/exhausted/failed/discarded) would show
    # something stale or meaningless -- omitted outright rather than a
    # confusing placeholder.
    if record.status == PENDING_DECISION:
        payload["diff"] = _render_pending_diff(cfg, record.dest, args.config, record.changed_files)
    print(json.dumps(payload, indent=2))
    return EXIT_OK


def cmd_real_repo_run_decide(args: argparse.Namespace) -> int:
    """Approve (commit) or reject (discard) a pending real-repo run.

    This IS the human gate agentic.real_repo_loop.finalize_real_repo_change
    exists for -- see that function's docstring. Reattaches the clone by the
    path the run record persisted (RepoWorkspaceTools.attach), since this is
    necessarily a separate process from the one that ran the loop.
    """
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()
    # deepagent_github.enabled is deliberately NOT checked here (or in
    # real-repo-run-status/-discard). cmd_real_repo_run checks it because
    # that command STARTS new work and would otherwise waste a real GitHub
    # fetch and clone before finding out the subsystem is off. This command
    # RESOLVES work a prior run already started: a pending_decision run has
    # exactly one legitimate next action (approve or reject via this command)
    # and no other path forward -- gating it on the master switch would strand
    # that run with no way to even reject it (real-repo-run-discard correctly
    # refuses a pending_decision run, on the theory a human still needs to
    # decide it) the moment an operator flips the switch off for an unrelated
    # reason. The low-level gate that DOES still apply here,
    # allow_git_write_tools, is checked independently by checkout_branch/add/
    # commit themselves on the approve path.
    from agentic.deepagent_github.repo_workspace import RepoWorkspaceTools
    from agentic.real_repo_loop import finalize_real_repo_change
    from agentic.real_repo_run_store import load_run, require_pending_decision, save_run

    app_cfg = _get_config(args.config)
    runs_dir = _real_repo_runs_dir(cfg)
    try:
        record = load_run(runs_dir, args.run_id)
        require_pending_decision(record)
        # A pending_decision record is only ever written with both set (see
        # cmd_real_repo_run); a JSON record can in principle be hand-edited
        # or corrupted in a way an in-memory RealRepoLoopResult cannot, so
        # this is a real validation, not just a mypy-narrowing assert.
        if record.branch_name is None or record.commit_message is None:
            raise AgenticError(
                "run record is pending decision but missing branch_name/commit_message",
                details={"run_id": args.run_id},
            )
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL

    try:
        tools = RepoWorkspaceTools.attach(cfg, Path(record.dest), config_path=args.config, cfg=app_cfg)
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL

    try:
        outcome = finalize_real_repo_change(
            tools,
            branch_name=record.branch_name,
            commit_message=record.commit_message,
            changed_files=record.changed_files,
            decision=args.decision,
            config_path=args.config,
            cfg=app_cfg,
        )
    except AgenticWriteRefused as exc:
        _err(exc.message)
        return EXIT_REFUSED
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL
    finally:
        if args.decision == "reject":
            tools.close()  # nothing kept -- discard the clone now

    record.status = outcome["status"]
    save_run(runs_dir, record)
    print(json.dumps(record.to_dict(), indent=2))
    return EXIT_OK


def cmd_real_repo_run_discard(args: argparse.Namespace) -> int:
    """Reclaim a run's clone from disk once its outcome no longer needs it.

    Every accepted run's clone survives past ``real-repo-run-decide --decision
    approve`` on purpose (see ``docs/agentic/GITHUB_WRITE_ENABLEMENT.md`` --
    a future push step would need the same clone, and closing it eagerly would
    foreclose that before it exists), but nothing wired anywhere calls
    ``RepoWorkspaceTools.close()`` on that path -- so it was retained forever,
    not merely "for a while." This is the missing explicit reclamation step,
    not a change to when approve/reject themselves clean up.

    Refuses a run still ``pending_decision``: that status means a human has
    not yet approved or rejected a live candidate, and discarding then would
    destroy it with no decision ever recorded. Every other status is eligible
    -- including ``running``, which only survives past its own command's exit
    if the owning process was killed before finishing (e.g. by the wall-clock
    timeout wrapping this action -- see ``utils/ops_runner.py``), leaving a
    clone with nothing else pointing at it.

    Idempotent: the clone directory may already be gone (an already-rejected
    run closes its own clone; discarding twice is harmless), in which case
    this only updates the record.
    """
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()

    from agentic.deepagent_github.repo_workspace import RepoWorkspaceTools
    from agentic.real_repo_run_store import PENDING_DECISION, load_run, save_run

    app_cfg = _get_config(args.config)
    runs_dir = _real_repo_runs_dir(cfg)
    try:
        record = load_run(runs_dir, args.run_id)
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL

    if record.status == PENDING_DECISION:
        _err(f"run {args.run_id} is still pending a decision -- run real-repo-run-decide first")
        return EXIT_FAIL

    dest = Path(record.dest)
    if dest.is_dir():
        try:
            tools = RepoWorkspaceTools.attach(cfg, dest, config_path=args.config, cfg=app_cfg)
        except AgenticError as exc:
            _err(exc.message)
            return EXIT_FAIL
        tools.close()

    record.status = "discarded"
    save_run(runs_dir, record)
    print(json.dumps(record.to_dict(), indent=2))
    return EXIT_OK


def cmd_propose_skill(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    # Honor the master switch: agentic.enabled=false means "the layer is off", so a
    # skills-registry op must not run while the operator believes it is inert
    # (matching cmd_context). propose is read-only, but gating it keeps the switch
    # consistent across all registry operations.
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()
    spec = {"name": args.name, "description": args.desc, "body": _read_body(args)}
    from agentic.registry import SkillRegistry
    try:
        reg = SkillRegistry(_get_config(args.config), cfg)
        proposal = reg.propose_skill(spec, reason=args.reason or "")
    except SkillRegistryError as exc:
        _err(exc.message)
        return EXIT_FAIL
    print(json.dumps(proposal, indent=2))
    return EXIT_OK


def cmd_apply_skill(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    # Master switch first: a registry WRITE must never run while agentic.enabled is
    # false. Previously apply-skill ignored the flag, so a confirmed write reached
    # the registry JSON even with the layer "disabled" (also reachable via the
    # API-key-gated POST /ops/agentic console) — a leaky off-switch. The per-write
    # governance (reason + confirm + injection gate) still applies once enabled.
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()
    if not args.confirm:
        _err("apply-skill requires --confirm")
        return EXIT_REFUSED
    spec = {"name": args.name, "description": args.desc, "body": _read_body(args)}
    from agentic.registry import SkillRegistry
    try:
        reg = SkillRegistry(_get_config(args.config), cfg)
        result = reg.apply_skill(spec, reason=args.reason or "")
    except PromptInjectionError as exc:
        _err(f"Injection blocked: {exc.message}")
        return EXIT_REFUSED
    except SkillRegistryError as exc:
        _err(exc.message)
        return EXIT_FAIL
    print(json.dumps(result, indent=2))
    return EXIT_OK


def cmd_test(args: argparse.Namespace) -> int:
    from agentic.selftest import run_self_test
    passed, total, lines = run_self_test(args.config)
    _heading(f"Self-test: {passed}/{total} passed")
    for line in lines:
        print(line)
    return EXIT_OK if passed == total else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic.cli",
        description="CyClaw agentic layer -- read-only GitHub context + governed skills, out-of-band.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print agentic config + gh + registry status.")
    p_status.set_defaults(func=cmd_status)

    p_ctx = sub.add_parser("context", help="Fetch read-only GitHub context.")
    g = p_ctx.add_mutually_exclusive_group()
    g.add_argument("--pr", type=int, help="Fetch a PR's metadata + diff.")
    g.add_argument("--issue", type=int, help="Fetch an issue's metadata.")
    g.add_argument("--repo", action="store_true", help="Fetch a repo overview (default).")
    p_ctx.add_argument("--no-diff", action="store_true", help="Omit the PR diff.")
    p_ctx.set_defaults(func=cmd_context)

    p_prop = sub.add_parser("propose-skill", help="Preview a skills-registry change (no write).")
    p_prop.add_argument("--name", required=True)
    p_prop.add_argument("--desc", required=True)
    p_prop.add_argument("--body")
    p_prop.add_argument("--body-file")
    p_prop.add_argument("--reason", help="Advisory; required at apply time.")
    p_prop.set_defaults(func=cmd_propose_skill)

    p_apply = sub.add_parser("apply-skill", help="Apply a skills-registry change (governed).")
    p_apply.add_argument("--name", required=True)
    p_apply.add_argument("--desc", required=True)
    p_apply.add_argument("--body")
    p_apply.add_argument("--body-file")
    p_apply.add_argument("--reason", required=True, help="Human reason string (required).")
    p_apply.add_argument("--confirm", action="store_true", help="Required to actually write.")
    p_apply.set_defaults(func=cmd_apply_skill)

    p_plan = sub.add_parser("deepagent-plan", help="Probe the Deep Agents harness (read-only, no writes).")
    g_plan = p_plan.add_mutually_exclusive_group()
    g_plan.add_argument("--pr", type=int, help="Plan against a PR's metadata + diff.")
    g_plan.add_argument("--issue", type=int, help="Plan against an issue.")
    g_plan.add_argument("--repo", action="store_true", help="Plan against a repo overview (default).")
    p_plan.add_argument("--instruction", required=True, help="What the agent is being asked to do.")
    p_plan.add_argument("--task-id", default="deepagent-plan", help="Correlation id (default: %(default)s).")
    p_plan.add_argument("--provider", choices=("grok", "claude"), help="Drive the loop with a cloud provider.")
    p_plan.add_argument("--confirm-online", action="store_true",
                        help="Required with --provider: per-run confirmation before any cloud egress.")
    p_plan.set_defaults(func=cmd_deepagent_plan)

    p_run = sub.add_parser(
        "real-repo-run",
        help="Clone a real repo and run plan/patch/verify (never commits -- see real-repo-run-decide).",
    )
    g_run = p_run.add_mutually_exclusive_group()
    g_run.add_argument("--pr", type=int, help="Fetch a PR's metadata + diff as task context.")
    g_run.add_argument("--issue", type=int, help="Fetch an issue as task context.")
    g_run.add_argument("--repo", action="store_true", help="Fetch a repo overview as task context (default).")
    p_run.add_argument("--instruction", required=True, help="What the planner is being asked to do.")
    p_run.add_argument(
        "--read-file", action="append", default=[], metavar="PATH",
        help="Repo-relative path to show the planner before it proposes changes (repeatable). "
             "Declare every existing file an edit task needs -- the planner cannot browse the clone.",
    )
    p_run.add_argument("--checks-file", required=True, help="Path to a JSON verification-checks manifest.")
    p_run.add_argument("--branch", required=True, help="Branch name to use on acceptance (claude/<topic>).")
    p_run.add_argument("--commit-message", required=True, help="Commit message to use on acceptance.")
    p_run.add_argument("--max-iterations", type=int, default=3, help="Planner attempts before giving up.")
    p_run.add_argument("--reason", required=True, help="Human reason string (required).")
    p_run.add_argument("--confirm", action="store_true", help="Required to actually run.")
    p_run.set_defaults(func=cmd_real_repo_run)

    p_run_status = sub.add_parser("real-repo-run-status", help="Report a real-repo run's persisted status.")
    p_run_status.add_argument("--run-id", required=True)
    p_run_status.set_defaults(func=cmd_real_repo_run_status)

    p_run_decide = sub.add_parser(
        "real-repo-run-decide", help="Approve (commit) or reject (discard) a pending real-repo run.",
    )
    p_run_decide.add_argument("--run-id", required=True)
    p_run_decide.add_argument("--decision", required=True, choices=("approve", "reject"))
    p_run_decide.set_defaults(func=cmd_real_repo_run_decide)

    p_run_discard = sub.add_parser(
        "real-repo-run-discard", help="Reclaim a decided (or orphaned) run's clone from disk.",
    )
    p_run_discard.add_argument("--run-id", required=True)
    p_run_discard.set_defaults(func=cmd_real_repo_run_discard)

    p_test = sub.add_parser("test", help="Run the pre-flight self-test.")
    p_test.set_defaults(func=cmd_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
