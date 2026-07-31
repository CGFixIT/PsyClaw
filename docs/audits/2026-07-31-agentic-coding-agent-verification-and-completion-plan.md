# Agentic coding agent — external-review verification and completion plan

**Date:** 2026-07-31
**Tree audited:** `claude/cyclaw-agentic-assessment-55cnyp` @ `171b988` (PR #727)
**Method:** 12 independent agents (8 adversarially refuting an external review's claims,
4 sweeping for what it missed), every finding re-verified by hand against the tree.
Four findings were additionally proven by execution, not inference.

This document verifies an external review of PR #727 conducted against `2050d0e`,
corrects it where it is stale or miscalibrated, records eleven defects the review did
not find, and sets the order of work to finish the pipeline.

---

## Scope note: the reviewed commit was one behind

The external review audited `2050d0e`. Commit `171b988` landed afterward and changed
`agentic/writer.py`, `agentic/real_repo_loop.py`, `agentic/cli.py` and their tests.
Three of the review's claims are therefore stale in whole or in part, and one of its
line citations (`writer.py:70` for `EXECUTION_ENABLED`) is off by seven lines — accurate
when written, now `writer.py:77`. None of the staleness reverses the review's
conclusions; the corrections are recorded so the numbers are not propagated.

---

## Verdict on the external review

The review is substantially accurate and its engineering judgment is sound. Of its ten
claims, four are confirmed outright, five are partially confirmed (correct core,
imprecise framing or stale detail), and none is refuted at its core. Two of its
framings are calibrated backwards, and its single largest omission is that it audited
capability gaps without auditing the new code for defects — where the most serious
problem on the branch turns out to live.

| Claim | Verdict | Severity for the goal |
|---|---|---|
| Planner codes blind (its stated #1 blocker) | Partially confirmed — half fixed by `171b988`, surviving half is worse than stated | **Blocker** |
| Feedback is a verdict, not evidence | Confirmed, understated | Major |
| Last mile has no road to it | Partially confirmed — terminus is real, "throwaway clone" is refuted | Major |
| Planner is local-model-only and unproven | Partially confirmed — hardcoding is refuted, model id is wrong | Major |
| Verification thinner than the CI bar | Confirmed, materially understated | Major |
| Documented containment gaps | Confirmed — severity of item H calibrated backwards | Major |
| Operational scale limits | Partially confirmed — the PR-template conclusion is refuted | Major |
| Shipped posture (flags, test count) | Partially confirmed — gate list incomplete, test count off by 5.5x | Minor |

### Where the review is calibrated backwards

**Checklist item H is lower severity than implied, for the reason given — and higher
severity for a reason not given.** The review treats an operator-supplied
`{"argv": ["git","push",...]}` as the exposure. Traced across all three supply paths,
that argv is unreachable by anything model- or network-controlled: `gate.py`'s
`/ops/agentic` route cannot reach the executor at all (`schemas/api.py:96`'s `Literal`
excludes `real-repo-run`), the harness HTTP route sends profile names against a
two-entry frozen allow-list (`harness/agent_policy.py:77-114`), and the CLI's
`--checks-file` is a local JSON file the operator authors by hand. An operator running
their own argv on their own box already has a shell; that is not escalation.

The real exposure is on the other axis. Item H's stated mitigation — "those argv are
operator-supplied, not model-supplied" — reasons about the *harness* while the
*payload* is model-authored. `agentic/real_repo_loop.py:313` writes model-authored
content into the clone; line 322 runs pytest over that same worktree; pytest
auto-collects `conftest.py` and `test_*.py` from it. So the default check profile
executes model-authored code, and that path *is* reachable from the authenticated
harness route the review cleared as safe. Item H's mitigation sentence needs rewriting,
and `docs/THREAT_MODEL.md:301-304` already contradicts it.

**The "throwaway clone" is refuted, and the truth is the opposite defect.** The clone is
`tempfile.mkdtemp` (`repo_workspace.py:179`), not `TemporaryDirectory` — no finalizer,
no `atexit`. `agentic/cli.py:556-558` closes the workspace only on *reject*. An approved
run's commit therefore survives indefinitely and is reattachable
(`RepoWorkspaceTools.attach`), so the missing push is plumbing rather than a persistence
problem. The cost is the inverse of what the review feared: **every approved run leaks a
full repository clone under `data/agentic/workspaces/` forever**, with no TTL, no bound,
and no reaper anywhere in the tree.

**The PR-template conclusion is refuted on its own evidence.** The lane exists —
`.github/workflows/pr-template-check.yml:36` is literally named "Check PR body against
template" — but it carries no `core.setFailed`, declares `permissions: {}`, is headed
"Non-blocking by design," and posts a comment stating it will not block merge. An
autonomous draft PR with an empty body gets an advisory comment, not a red lane. The
narrow true residue: `writer.py:160` defaults `body` to `""`.

### Corrections to the factual claims

- `EXECUTION_ENABLED = False` is at `agentic/writer.py:77`, not `:70`.
- The config-gate list is correct but incomplete. It omits eight further gates in the
  same block, most importantly **`deepagent_github.enabled: false`**
  (`config.yaml:488`) — the master switch for the entire coding harness — plus
  `allow_deepagents_dependency`, `allow_filesystem_write_tools`, `allow_shell_execution`,
  `providers.grok.enabled`, `providers.claude.enabled`, `harness_optimizer.enabled`, and
  `allow_local_model_judge`. The review understates the depth of the disarming; it does
  not overstate it.
- "399 tests" is a targeted subset. The full suite at this HEAD is **2196 collected,
  2183 passed, 13 skipped, 0 failed** across 100 test files.
- "Python 3.12.12" does not match this checkout (venv is 3.12.3) and should not be
  repeated as a property of the repo.
- The planner model is **not** `qwen2.5:7b`. The loop reads
  `agentic.deepagent_github.model`, which ships as `""` (`config.yaml:491`);
  `qwen2.5:7b` is `models.local_llm.model`, a different subsystem. Nothing hardcodes a
  7B model — the operator chooses it.
- `LocalProposerClient` is **not** hardcoded in any enforcing sense. It is a static
  annotation with no `isinstance` check, and mypy is not a CI gate. The runtime contract
  is duck-typed: `client.invoke(system_prompt=, user_prompt=, config_path=, cfg=)`
  returning an object with `.content`. Substituting a cloud model is a ~40-line adapter,
  not a loop rewrite.
- The gate count at the execution boundary is **eight**, not four: `_require_gates`
  enforces five (gate 0 `agentic.enabled` plus the four numbered), and `execute_write`
  layers on `EXECUTION_ENABLED`, the `_EXECUTABLE_WRITE_OPS` narrowing to `{"pr_create"}`,
  and an argv rebuild-and-compare.

---

## Defects the external review did not find

The external review audited architecture, not code. Eleven defects were found in the
branch's own new modules. Four were proven by execution.

### DEF-1 — Model-authored content can write into `.git/`, yielding arbitrary command execution at approve time (BLOCKER, proven)

`_validate_write_path` (`agentic/deepagent_github/repo_workspace.py:341-352`) rejects
absolute paths, leading `-`, `..`, `:` and NUL, but has **no exclusion for the clone's
own git metadata**. For target `.git/config` the parts filter drops only `""` and `"."`,
so `parts == (".git", "config")`, the resolved candidate is under `_dest`, and
`write_file` writes model-authored bytes there.

Verified by execution in a scratch repo:

```
[filter "pwn"]
	clean = sh -c "id > PWNED; cat"
```
written to `.git/config`, plus `* filter=pwn` in `.gitattributes`, then a plain
`git add -- tracked.txt .gitattributes` — exactly what `finalize_real_repo_change`
runs — **executed the command** (`PWNED` written, containing `uid=0(root)`).

Two aggravating facts:

1. **The execution happens at approve time**, in the CLI process — after the human
   decision, with the operator's real `PATH` and `HOME`. The executor's scrubbed env
   (`runner.py:64-79`) does not apply; `_GIT_ENV_ALLOWLIST` is a different path.
2. **`git diff` cannot show it.** In the same test the review surface listed only
   `.gitattributes` and `tracked.txt`. `.git/config` is invisible to every diff-based
   review by construction.

No exec bit is required (the filter runs via `sh -c`), so `write_text`'s lack of one is
not a mitigation. Fix: reject any target whose first path segment is `.git`, in the
existing parts loop. Add `--no-verify` to `commit()` as defense in depth.

### DEF-2 — The critical-governance refusal guarding the planner prompt is structurally dead (BLOCKER, verified)

`agentic/cli.py:393` refuses the run when any bundle finding has
`severity == "critical"`. The only producer of those findings,
`agentic/context.py::_injection_findings`, hardcodes `"severity": "warning"` at
lines 74 and 85 and nowhere else — its own docstring says so explicitly. **The filter
can never be non-empty.**

`171b988` then built on that dead guard. It added `_bundle_context_text` and the comment
at `cli.py:397-399` asserting "the critical-finding refusal above already covers every
field this pulls from … so nothing reaching the planner's prompt has an unreviewed
critical finding." That assertion is false for every input. Attacker-authored GitHub
text — a PR body from anyone who can open a PR — is scanned, flagged, audited, and then
forwarded verbatim (up to 8,000 chars) into the planner's prompt, positioned *ahead of*
the operator's own instruction.

This one is mine: I wrote that comment and that wiring in the previous round, justified
by a gate I did not verify fires. Fix: gate on the code the scanner actually emits
(`INJECTION_FINDING_CODE` / `SCANNER_UNAVAILABLE_CODE`), or delete the unreachable
filter and both docstring claims that depend on it. Do not leave the claim and the dead
filter both standing.

### DEF-3 — Critical-flagged content is written to disk and then staged into the approved commit (BLOCKER, verified)

In `agentic/real_repo_loop.py:310-322` the write is unconditional and precedes the
verdict: `inspect_candidate_text` accumulates findings, `tools.write_file` runs
regardless, and `has_critical` is computed only after the loop. `governance_findings`
resets per iteration, so a later accepted iteration never sees the earlier critical
finding. Because the clone persists across iterations with no reset, and because
`171b988` changed `RealRepoLoopResult.changed_files` to union *every* iteration's
writes, a file the loop's own hard gate flagged as critical is now on disk, in the
union, persisted into the run record, and passed to `tools.add()` on approve.

The union fix was correct for its stated purpose and converted a latent residue into a
committed artifact. It needs a companion: scan all proposed files first and skip
`write_file` entirely for a critical iteration, and exclude critical-rejected
iterations from the union.

The same ordering yields a second path: a critical-flagged `conftest.py` written in
iteration 1 is auto-collected by iteration 2's pytest, so the critical gate is a
verification-*skip*, not a quarantine.

### DEF-4 — Check timeout crashes the run on Windows (MAJOR, proven)

`agentic/executor/runner.py:144` calls `.decode()` on `exc.stdout` under `text=True`.
Verified against CPython's `subprocess.run`: the POSIX branch leaves `exc.stdout` as raw
`bytes` (confirmed empirically — `b'partial output\n'`), but the `_mswindows` branch
runs `exc.stdout, exc.stderr = process.communicate()`, which in text mode returns `str`.
`str` has no `.decode`, so the handler whose own comment says "a hung check must not
crash the whole verification run" becomes an uncaught `AttributeError` on exactly that
case. Invisible on the ubuntu CI leg; `harness/` is the PowerShell/Windows operator
surface. Fix: drop the decode.

### DEF-5 — A missing check binary crashes the run outside the exit-code contract (MAJOR)

`runner.py:127-136` catches only `subprocess.TimeoutExpired`. `FileNotFoundError` /
`PermissionError` (an unresolvable or non-executable `argv[0]`) are `OSError`, caught
nowhere in the chain — not in `run_verification`, `run_real_repo_loop`, `cmd_real_repo_run`'s
`AgenticError`-only handler, or `main()`. Result: traceback, exit 1 (outside the
documented 0/2/3/4 API), no run record written, clone leaked. Fix: catch `OSError`
alongside `TimeoutExpired` and return `ok=False`.

### DEF-6 — `load_run` raises bare `TypeError` for a structurally invalid record (MAJOR)

`agentic/real_repo_run_store.py:109-113` guards only `json.loads`; the
`RealRepoRunRecord(**data)` splat sits outside the try. A schema-drifted or hand-edited
record raises `TypeError`, escapes both callers' `except AgenticError`, and exits 1 —
which `utils/ops_runner.py:388` maps to an unclassifiable `"unknown"` in the console.
`_atomic_write_json` also lacks an fsync before `os.replace`. Fix: move the construction
inside the try, add `TypeError`, validate `status` and `changed_files` shapes.

### DEF-7 — `run_verification` drops `config_path`/`cfg`, splitting acceptance evidence off the audit stream (MINOR)

`real_repo_loop.py:322` is the one audited call in the loop that threads neither. With a
non-default `--config`, the `agentic_executor_check_result` lines — the only record of
what the acceptance decision actually observed — land in a different audit file from the
rest of the run. Fix: pass both; `run_verification` already accepts them.

### DEF-8 — A CRLF planner response parses to zero files and reports a misleading gate (MINOR)

`_FILE_BLOCK_RE` (`real_repo_loop.py:84-87`) hardcodes bare `\n`. A `\r\n` response
matches nothing, `_parse_file_blocks` returns `{}` with no signal, and the iteration
rejects as `no_files_changed` — pointing the operator at the model's willingness to
propose files rather than at a line-ending mismatch. Every iteration repeats it until
`max_iterations` is burned. Windows is the operator surface. Duplicate paths in one
response also silently collapse to the last. Fix: normalize line endings; distinguish
"parse failed" from "proposed nothing"; reject duplicate paths.

### DEF-9 — Approved-run clone leak and timeout orphans (MAJOR)

Two paths, one root cause. On approve, `cli.py:556-558` never calls `close()`, so the
clone is retained permanently — by design, to preserve the commit for a future push, but
with no reclamation step anywhere. On a 900s `subprocess.run` timeout the child is
killed before *any* `save_run` call, so no run record exists at all and the clone is
orphaned with nothing on disk pointing at it. Fix: write the record at run *start*
(making the documented-but-dead `running` state real), add a `real-repo-run-discard`
subcommand, and add an age-bounded sweep skipping any dest referenced by a non-terminal
record.

### DEF-10 — `cmd_real_repo_run` never checks `deepagent_github.enabled` (MAJOR)

The newest entry point into the coding harness gates only on the top-level master
switch (`cli.py:368`). Nothing on the path reads the subsystem's own switch —
contrast `builder.py:130`, which correctly requires both. With `agentic.enabled: true`
and `deepagent_github.enabled: false` the command still performs a live GitHub context
fetch and a full network clone before `_require_run_gates` refuses. Writes stay refused,
so this is not a write bypass, but it makes a documented switch dead config on its
newest consumer and does real work before failing. Fix: compose both switches, and add a
`__post_init__` cross-check rejecting `allow_git_write_tools: true` under
`enabled: false`, matching the `allow_cloud_providers` precedent.

### DEF-11 — Shipped `deepagent_github.model` is `""`, failing only after a full clone (MINOR)

`config.yaml:491` ships an empty model name, which passes config validation. The failure
surfaces inside the first loop iteration — after the GitHub fetch and the network clone
have already run. An operator following `GITHUB_WRITE_ENABLEMENT.md`'s seven steps, which
never mention setting the model, gets zero successful runs. Fix: hoist a non-empty check
next to the existing eager checks-file validation at `cli.py:402`, and add the step to
the enablement procedure.

---

## The capability gap that actually blocks the goal

The external review named planner blindness as its #1 blocker and was right, but half
its evidence is stale and the surviving half is worse than it stated.

**What `171b988` fixed:** PR/issue title, body, and diff now reach the prompt via
`_bundle_context_text` → `run_real_repo_loop(context=…)`. That is the complete list of
what is threaded, bounded to 8,000 chars.

**What remains, and why it is worse than "the model will confabulate paths":** the
planner still cannot read the repository. `RepoWorkspaceTools.read_file` / `list_dir` /
`stat_file` exist (`repo_workspace.py:409,425,438`) with **zero production callers**.
Meanwhile `PLANNER_SYSTEM_PROMPT` (`real_repo_loop.py:89-95`) demands *the file's full
new content* "for every file you want to create **or change**" — whole-file replacement
for edits, not just creates. And `write_file` (`:480-506`) makes no create-vs-overwrite
distinction: `must_exist=False` is passed purely for symlink resolution, and line 499 is
a bare `path.write_text` that truncates. No existence check, no pre-read, no backup, no
size-delta gate; `inspect_candidate_text` scans only the new text.

So a model that has never seen a 900-line file and emits a 60-line replacement for it
**silently destroys it**. The only backstop is the caller's `checks`, which fire *after*
the destructive write with no rollback — and, per DEF-3, the clobbered file is now
staged into the approved commit. Recoverable via git only because the original is in
HEAD; nothing in this path does so, and nothing surfaces it.

Three further consequences the review did not draw:

- **In `--repo` mode the planner gets nothing at all.** `fetch_repo_context` returns no
  `pr`/`issue`/`diff` key, so `_bundle_context_text` returns `None` by design.
- **The diff is the first thing truncated.** It is concatenated last, and
  `gh_client.py:192` caps a fetched diff at 200,000 chars against an 8,000-char prompt
  budget — up to ~96% of the only code-bearing field can vanish behind a trailing marker.
- **A bigger model does not fix this.** No model, 7B or frontier, can reliably rewrite a
  file it has never read. Provider parity is not the unblocker; a read path is.

**The human review surface does not exist either.** `real_repo_loop.py:388-389` states
the commit is split from the loop "so a human can review the diff first (`tools.diff()`)",
but `tools.diff()` has **zero callers** in `cli.py`, `harness/server.py`, or
`static/harness.html`. The operator approves against a *file list*, not a diff — which is
what makes DEF-1 and DEF-3 approvable in practice.

### Verification is self-defeating, not merely thin

The review framed the two-profile allow-list as a wasted-review cost. The sharper problem
is that the planner writes into the same worktree the checks then run against, with no
protected-path gate. It can overwrite `tests/`, `conftest.py`, `pytest.ini`,
`pyproject.toml`, or drop a `# ruff: noqa` — inside the clone — and both profiles then
pass by construction. `_validate_write_path` is a jail, not a scope gate;
`decide_real_repo_candidate` never inspects *which* files changed. A candidate that
rewrites the tests judging it is `accepted`, and `decision.reason` is fed back as
feedback, actively steering the planner toward whatever makes `verification_failed` go
away. This is the single most common reward-hacking failure mode of a make-the-tests-pass
loop, and nothing in the tree gates it.

Two corrections to the review's framing here. Its premise that `config.yaml` references
`agentic.executor.default_checks` is false — that symbol has no production caller and is
reference code. And the documented reason `invariant-guard` was excluded from the profile
list rests on a false premise: it assumes an absolute path is required, but
`run_verification` pins `cwd` to the worktree and `check_invariants.py` self-anchors, so a
repo-relative argv works and correctly checks the clone. Adding it is roughly three lines,
not a redesign.

---

## Completion plan

Ordered so that each tier is independently shippable and nothing downstream is built on
an unverified assumption. Tiers 0–1 are defect and hardening work that passes the feature
freeze on its face. Tier 3 requires the signed security review.

### Tier 0 — Defects (blocks merge of the current branch)

One PR per cluster, each with a regression test.

1. **DEF-1** `.git/**` write refusal in `_validate_write_path` + `--no-verify` on commit.
   Test: `write_file(".git/hooks/pre-commit", …)` and `write_file(".git/config", …)` both
   raise. There is currently zero `.git/` coverage in the workspace tests.
2. **DEF-2 + DEF-3** together — they share the root cause that a scan result is computed
   and then not enforced. Gate the context refusal on the emitted finding *code*; move the
   write behind the governance verdict; exclude critical-rejected iterations from the
   `changed_files` union.
3. **DEF-4 + DEF-5 + DEF-7** — executor robustness: drop the decode, catch `OSError`,
   thread `config_path`/`cfg`.
4. **DEF-6 + DEF-9** — run-store durability: guard the splat, fsync before replace, write
   the record at run start, add `real-repo-run-discard` and an age-bounded sweep.
5. **DEF-8 + DEF-10 + DEF-11** — input and config hygiene: CRLF normalization and a
   distinct parse-failure gate; compose `deepagent_github.enabled`; pre-flight the model
   name.

### Tier 1 — Make "verified change" mean something

6. **Give the planner a bounded read path.** Add `read_paths: Sequence[str]` to
   `run_real_repo_loop`, call the already-jailed `tools.read_file()` on each, and fold the
   current contents into the prompt beside the existing `context` block — the same shape
   `171b988` used. This turns "reproduce this file from memory" into "here is the file,
   return it edited," which is what makes a whole-file protocol survivable. **Ship the
   interim guard in the same PR:** refuse a write whose target already exists on disk
   unless that file's current text was in the prompt. That single rule closes the data-loss
   hazard even before the read path is tuned.
7. **Plumb evidence into feedback.** The `VerificationReport` is already a live local in
   the same scope as `feedback = decision.reason`, carrying per-check stdout/stderr,
   exit code, and `timed_out`; `failed_names()` exists and is unused. Emit the failing
   check's name and the *tail* of its output (pytest and ruff print failures last), capped
   per check and globally. **Scan it before forwarding** with the same
   `compile_injection_patterns`/`scan_injection_patterns` primitive `context.py` already
   uses — this is model-authored code's output re-entering the prompt — and forward only
   the check name and a redaction marker on a match, auditing the event. Bind the
   currently-swallowed write error at `:314` while here.
8. **Add the diff-scope gate.** Two keyword-only params on
   `decide_real_repo_candidate` in the existing flat-boolean style:
   `out_of_scope_files` → gate `out_of_scope_write`, and a byte budget →
   `diff_budget_exceeded`. Default-deny a protected set — `tests/`, `conftest.py`,
   `.github/`, `.git/`, `pyproject.toml`, `setup.cfg`, `pytest.ini`, `.claude/skills/` —
   sourced from `config.yaml`, never hardcoded.
9. **Render the diff at the decision point.** `tools.diff()` exists and is uncalled;
   surface it in `real-repo-run-status`, the `GET /api/agent/runs/{id}` payload, and the
   console. Without it the human gate is approval-by-filename.
10. **Widen the check profiles.** Add `invariant-guard` (repo-relative argv), `config-guard`,
    and a WPS/flake8 profile to `harness/agent_policy.py`'s allow-list — still name-only,
    no argv over the wire. `gitleaks`/OSV/CodeQL structurally cannot run here (external
    binaries, and `PIP_NO_INDEX=1` + proxy scrubbing deliberately prevent fetching a vuln
    database); those stay CI-only, correctly. Note the interpreter caveat: `sys.executable`
    is the *harness's* interpreter, so `pytest` is only meaningful when the target repo is
    dependency-compatible with CyClaw.

### Tier 2 — Provider choice and the live-fire finding

11. **Protocol-ize the client.** Replace the `LocalProposerClient` annotation with a
    `ProposerClient` `Protocol` declaring the invoke signature and a `.content` response.
    Pure typing; makes the existing duck-typing contractual.
12. **Add a cloud proposer.** ~45 lines wrapping `build_chat_model`, behind the existing
    six-gate chain, calling `sanitize_handoff` **first** on the combined prompt. Two things
    to get right: emit the *real* provider in the audit events (`LocalProposerClient`
    hardcodes `provider="ollama"` in all three, so a naive reuse produces audit records
    claiming local inference while bytes leave the machine), and coerce `BaseMessage.content`
    explicitly rather than `str()`-ing it.
13. **Live-fire once and record the result.** With Tier 1 shipped, run the loop against a
    scratch repo in dry-run posture with both a local and a cloud model, and write up
    whether either drives the file-block protocol. That is the empirical answer to the open
    question the roadmap has carried since the start, and it decides whether the local-only
    path is viable at all.

**Also resolve the two-stacks question here.** The deepagents subgraph has never been
constructed on a shipped path — `cli.py:238` omits `workspace_tools`, so `builder.py`
returns `created=False`. `deepagent_github/` is not uniformly dead (`repo_workspace.py`
and `model_adapter.py` are live dependencies of the real loop), but
`builder`/`subagents`/`runners`/`tools`/`memory`/`skills`/`permissions` are unreachable.
Either commit to invoking that stack or mark those modules reference-only in the module
table. Maintaining two agent architectures where one has never run is a standing tax.

### Tier 3 — The last mile (requires the signed security review)

Do not plumb this silently. Adding any CLI or route caller for `push_branch` /
`execute_write` **inverts checklist item A**, which asserts zero reachability as a signed
property; item A must be explicitly re-opened and re-signed.

14. **File the checklist** in `docs/agentic/GITHUB_WRITE_ENABLEMENT.md` with sign-off,
    having first rewritten item H's mitigation sentence (see the calibration section) and
    added the new items DEF-1 and DEF-3 imply.
15. **Extend the run-store schema.** This is new capability, not plumbing: `pr_create`
    requires `head`/`base`/`title`/`body` and the record persists none of `base`, `title`,
    or `body`. `_TERMINAL_STATUSES` also forecloses the transition — `approved` is terminal,
    so a `real-repo-run-push` reusing the existing guard would be rejected on every approved
    run. Needs non-terminal `pushed`/`pr_opened` states with their own guard.
16. **Wire publish** as an opt-in `publish: bool = False` kwarg on
    `finalize_real_repo_change` (push → `plan_write` → `execute_write` with a fresh
    caller-supplied `confirm`), defaulting off so today's behavior is byte-identical.
17. **Flip the gates in the documented order**, rehearsing the rollback before relying on it.

### Tier 4 — Scale

18. Background run + polling transport (`static/terminal.html`'s `setTimeout` + backoff and
    `AbortController` deadlines are the in-repo precedent), replacing the 900s synchronous
    block. Note this is a documented ceiling, not a bug — the route returns a clean 504 and
    the console warns and disables the send button.
19. Only if the target ever widens beyond the operator's own configured repo: stage-5
    containment per `docs/THREAT_MODEL.md` §6. Note that the executor's `NO_PROXY="*"` is
    worse than insufficient — it *instructs* HTTP libraries to connect directly, so plain
    `urllib.request.urlopen` egresses fine without any raw socket. Either drop the pretense
    or point the proxy vars at an unroutable sink.

---

## Documentation reconciliation required alongside the above

All five repo gates pass clean at this HEAD — `ruff`, `invariant-guard` (33/33),
`config-guard`, `dep-guard`, `doc-sync` (0 drift), plus the blocking WPS lane run exactly
as CI computes it. Every new module has both a `--cov=` flag and a
`[tool.coverage.run] source` entry. The drift below is what those gates do not look at:

- `docs/THREAT_MODEL.md:184-186` and `:210-220` still assert "no live caller produces a
  worktree with a model-authored diff" and that the planner-proposes-a-diff pipeline "does
  not exist yet." `agentic/real_repo_loop.py` **is** that pipeline and says so at `:8-11`.
  The document now contradicts itself.
- `docs/THREAT_MODEL.md:311-313` claims "the executor itself performs no writes of any
  kind." Its cwd is a writable worktree and pytest plainly can write there.
- `agentic/real_repo_loop.py:63-67` still says "Not wired to any CLI subcommand, HTTP
  route, or background caller" — stale since the run/status/decide wiring.
- `CLAUDE.md:563` omits `harness` from the coverage-source list (11 vs pyproject's 12);
  `:565` says "~72 files" against an actual 100; the §2 module table omits `utils/auth.py`,
  `agentic/executor/`, and `agentic/deepagent_github/`.
- Consider a `doc-sync` D7 check asserting each `[tool.coverage.run] source` entry appears
  in CLAUDE.md §8, which would make that drift class self-detecting.

---

## On merging PR #727

The external review recommends merging as-is on the grounds that it ships disarmed and the
security review gates the flag flip, not the merge. That reasoning is sound for the
*capability* gaps, and the disarmed posture is genuine and deep — eight execution-boundary
gates and fourteen shipped-false config switches.

It does not extend to Tier 0. DEF-1, DEF-2, and DEF-3 are defects *in code this branch
introduces*, and two of them are latent security holes that the enablement checklist does
not mention and would not catch. Merging them means the signed review — the control this
whole design leans on — gets filed against a tree containing a `.git/` write path to
arbitrary command execution that no diff-based review can see. The recommendation here is
to land Tier 0 on this branch first, then merge.
