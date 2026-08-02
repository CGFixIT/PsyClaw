# CyClaw Agentic Coding Harness — Claim Audit + Production Implementation Plan

**Date:** 2026-07-30.
**Repo state audited:** `origin/main` @ `bf26a46` (2026-07-30 15:35 -0400). Working
tree at that commit. All findings below are file:line-verified against that tree.

---

## Context

The request has two halves:

1. **Audit** a set of claims about CyClaw's current GitHub agentic-coding capability
   and correct them where wrong.
2. **Plan** the remainder of a production-grade GitHub coding agent harness —
   branch from main → verified diff → draft PR — surfaced through the PowerShell
   harness (`harness/`, `powershell/`) and `static/harness.html`.

The load-bearing premise worth testing up front: the claims frame the work as
"six gaps, sequence A–F, mostly wiring." That framing is ~80% right, but it
understates three things that change the plan's shape — the threat-model
amendment, the model-capability prerequisite, and the harness auth gap. Those are
called out below and drive the recommended sequence.

Also note `CLAUDE.md` §1: the repo is in **FEATURE FREEZE**. This is a new
capability, not polish. Proceeding on the user's explicit request as the
justification, but every phase below is scoped to ship as its own draft PR under
the repo's existing phase discipline rather than as one large feature branch.

---

## Part 1 — Claim audit (verified against `bf26a46`)

### Scorecard

| # | Claim | Verdict |
|---|---|---|
| 1 | Main @ `bf26a46`, 51 commits since yesterday, #707–#724 landed | **CONFIRMED (approx.)** — 56 commits since 2026-07-29; merges #704–#724 |
| 2 | GitHub reads are production-grade (allow-listed ops, injection guards, retries, audit) | **PARTIALLY TRUE** — see correction C1 |
| 3 | GitHub writes deliberately dead: 4-gate + `EXECUTION_ENABLED=False` + `NotImplementedError`; no branch/push/commit ops | **CONFIRMED** |
| 4 | `harness_optimizer` is a complete deterministic loop; runner only substring-matches a 4-file fixture; never executes code | **CONFIRMED** + correction C2 |
| 5 | `deepagent_github` fully wired but dormant: 8 subagents, 5 tools, HITL approve/reject/timeout, `draft_plan()` canned, shell/GH writes hard-refused | **PARTIALLY TRUE** — see corrections C3, C4, C5 |
| 6 | `harness/` is a control plane with no run-trigger endpoint and no HITL surface | **CONFIRMED** |
| 7 | Phases 0–9 all on main, all flag-disabled; PR #515 = phases 6–9 | **CONFIRMED** + correction C6 |
| 8 | Provider parity (6-gate chain + `HandoffEnvelope`) is owner-approved and unimplemented | **CONFIRMED** + correction C7 |
| 9 | Killswitch landed via PR #707/#709 | **FALSE** — see correction C8 |
| 10 | Containment pattern already exists to clone a real repo | **PARTIALLY TRUE** — see correction C9 |

### Confirmed with evidence

**Writes are genuinely dead.** `agentic/writer.py:110-139` implements the four
gates in order — `mode` (`:111`), `writes_enabled` (`:114`), `reason` (`:123`),
`confirm` (`:132`) — each failing through `_refuse()` (`:71-85`) which audits
`agentic_write_refused` and raises `AgenticWriteRefused`. `EXECUTION_ENABLED = False`
at `writer.py:31` is checked *before* anything else in `execute_write`
(`:171-181`); past it sits `NotImplementedError` (`:182-185`). Even a
fully-satisfied gate chain returns `{"status": "dry_run_plan", "executed": False}`
(`:141-158`). Verified: **no `git` subprocess exists anywhere in `agentic/`** —
the only `subprocess` calls in the package are `gh version` / `gh <read op>`
(`gh_client.py:95,316`), a file-manager launch (`fsconnect/osutil.py:93`), and a
reindex trigger (`fsconnect/indexer.py:309`).

**No code execution anywhere in the agentic loop.** `grep -rn "subprocess|os.system|Popen"`
over `agentic/harness_optimizer/` and `agentic/deepagent_github/` returns zero
hits. Variant scoring is literally two lines — `github_coding_runner.py:232-233`
does `case.expected_text in text`, scored `1.0`/`0.0`. The fixture is exactly
4 files (`tests/fixtures/github_coding_repo/`: `README.md`, `planner.py` (2 lines),
`scheduler.py` (2 lines), `docs/usage.md`), whose own README states "nothing here
is executed."

**Module isolation (I6) holds bidirectionally and is machine-enforced.**
`tests/test_agentic_isolation.py:35-41` (forward, AST) and `:74-90` (reverse over
`rglob("*.py")`), with **negative self-tests** at `:44-59` and `:62-71` that plant
violating source and assert the detector fires. `tests/test_harness_isolation.py:60-85`
does the same for `harness/`. `harness/server.py:383` reaches `agentic` only via
`utils.ops_runner.run_agentic_op("status")`, which builds
`[sys.executable, "-m", "agentic.cli", ...]` (`utils/ops_runner.py:260`).

**Harness has no run-trigger and no HITL surface.** All 13 routes are in
`harness/server.py:227-403`. The only POSTs are session-create, session-rename,
soul-toggle, model-select, and chat. `GET /api/harness/runs` (`:394-403`) is a
bare `Path.iterdir()` directory listing. The GitHub action is the hardcoded string
literal `"status"` (`:383`), and `utils/ops_runner.py:55` whitelists only
`{status, test, context, propose-skill, apply-skill}` — the shim physically cannot
reach a coding action. No approve/reject vocabulary exists in `harness/` or
`static/harness.html`.

### Corrections

**C1 — "injection guards" on the read path is argv-injection only.** The guard is
`_REPO_RE` (`gh_client.py:52`, re-validated at the argv boundary `:235-239`),
which blocks leading-dash argument injection, plus a shell-metachar deny set at
`agentic/config.py:66`. Neither `gh_client.py` nor `context.py` imports any
sanitizer. **PR bodies, issue bodies, comments, and diffs returned by
`run_read` (`gh_client.py:369-382`) and bundled by `fetch_pr_context`
(`context.py:62-70`) are never prompt-injection scanned.** The only injection
scanning in `agentic/` is on *locally authored* text (`registry.py:213-214,360-365`;
`harness_optimizer/governance.py:67-85`). This is a real gap the moment GitHub
content feeds a model — an attacker-authored PR body is untrusted input crossing
into a trusted planning loop. It is a prerequisite, not a nice-to-have.

**C2 — the "complete loop" is library primitives with no caller.**
`GitHubCodingRunner` is not in `agentic/harness_optimizer/__init__.py`'s `__all__`
(`:40-68`), and no non-test code imports it. `agentic/cli.py` (`:200-239`) has zero
`harness`/`optimizer` subcommands. The design doc concedes it:
`GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md:1017` — "no CLI or background
executor is added by phases 6-9."

**C3 — "5 scoped tool callables" is a ceiling, not the shipped default.**
`deepagent_github/tools.py:108-114` defines 5, but `:115` filters by allow-list and
`default_tool_specs` (`:44-52`) marks the two write tools
`allowed=policy.allow_filesystem_write_tools`, which ships `false`
(`config.yaml:490`). **Under shipped config exactly 3 callables materialize** —
and since HITL `interrupt_on` covers only tools that are both `allowed` and
`sensitive` (`builder.py:76-83`), the shipped config produces **zero interrupts**.

**C4 — HITL "timeout" is not a third graph decision.** `runners.py:96` collapses it:
`resolved = "reject" if decision == "timeout" else decision`. Fail-closed, correct,
but the agent-side vocabulary is two decisions, not three.

**C5 — CI does construct a real `ChatOpenAI`-backed agent.** The `deepagents-harness`
job (`.github/workflows/ci.yml:363-403`) installs real `deepagents` +
`langchain-openai` and calls the real `build_deepagent_github`
(`tests/test_agentic_deepagent_optional.py:51-59`). It never calls `.invoke()`.
So: real construction, zero inference. "Never invoked against a live model" is
right in substance.

Also, `draft_plan()` (`runners.py:13-43`) is *templated*, not fully canned: `steps`
and `proposed_tests` are hardcoded tuples, but `source` (`:18-20`) and `task.repo`
are interpolated. It ignores `task.instruction` entirely past an emptiness check.

**C6 — Phase 9 is a review checklist, not shipped capability.**
`DEEP_AGENT_HARNESS_PHASES_6_9.md:67-82` defines Phase 9 as a list of gates to
satisfy *before any future executor is considered*. "Phases 0–9 all on main" is
true as documents-and-tests; it should not be read as "nine phases of capability
shipped." Separately, `LangChain_Deep_Agentic_Harness_latest_roadmap.md:179-186`
still carries a stale **"Phases 6–9: NOT STARTED"** heading with a blockers table
naming files that now exist; the correction is dated below it at `:209-212`, but a
skimming reader gets the wrong answer.

**C7 — one element of provider parity did land.** The design's "close the `xai-`
gap" requirement (`roadmap:794-810`) is satisfied: `config.yaml:340` now carries
`- "xai-[a-zA-Z0-9]{20,}"` in `redact_secrets_like`. Everything else —
`allow_cloud_providers`, a `providers:` block, `sanitize_handoff`,
`HandoffEnvelope`, `langchain_xai`/`ChatXAI`, the `agentic-deepagents-cloud`
extra — returns zero grep hits across `*.py`/`*.yaml`/`*.toml`.

Note the design doc has **drifted from main**: it specifies
`provider: "lmstudio"` / `http://localhost:1234/v1` / `grok-4.3`
(`roadmap:723-733`), while shipped `config.yaml:486-487` is `"ollama"` /
`http://127.0.0.1:11434/v1` and `CLAUDE.md` names `grok-4.5`.

**C8 — the killswitch attribution is wrong.** PR #707 is harness hardening
(`harness/server.py`, `metrics.py`, `retrieval/clear_cache.py`) with zero
`agentic/` files. PR #709 is the **telemetry** kill switch —
`agentic/__init__.py:24-32` calling `apply_telemetry_kill()` at package import.
The GitHub-write killswitch is `EXECUTION_ENABLED` (`writer.py:31`), which landed
in **PR #248**, ~450 PRs earlier. There is also a second, unmentioned killswitch:
`FS_WRITE_HARD_DISABLE` (`agentic/fsconnect/writer.py:56`).

**C9 — containment exists; cloning does not.** The copy-into-tempdir pattern is
real (`github_coding_runner.py:202-204`: `TemporaryDirectory` + `copytree`, every
path through `_safe_child()` at `:33-56`). But **there is no `git clone` anywhere
in the codebase** — grep for `clone` across all `.py` returns nothing, and
`gh_client.py`'s `_READ_OPS` has no `repo_clone`. The plan doc concedes it as
future work (`OPTIMIZER_PLAN.md:431`).

### The claims' largest omission

The assessment characterises "writes are deliberately dead" as a property of the
agentic layer. It is a property of the **GitHub** path only.
`agentic/fsconnect/writer.py` is **814 lines of fully-implemented local filesystem
write capability** — gated, default-off, with its own killswitch, quota, rate
limit, trash-instead-of-delete, and a hard Windows refusal for a name-based TOCTOU
gap (`:60-77`). And `agentic/harness_optimizer/patching.py:142-229` is a **live,
working write path** that actually puts bytes on disk after 8 sequential gates.
Any threat-model statement about the agentic layer needs to say "GitHub writes are
dead; local governed writes are shipped and default-off."

---

## Part 2 — The three findings that reshape the recommended sequence

**F1 — A code-execution executor invalidates the stated reason microVM isolation
is not required.** `docs/THREAT_MODEL.md:94-96` lists "untrusted multi-tenant code
execution" as an explicit non-goal and says "the agentic layer is deliberately
*non-executing*." §5 (`:114-146`) then argues microVM containment is unnecessary
*because* of that — leading with "GitHub writes are hard-killed" and closing with
"the residual blast radius is … **not** untrusted code execution." The hardening
ladder (`:150-164`) puts gVisor/Firecracker at stage 5, "Conditional — only if the
untrusted-exec threat appears."

Running `pytest` over a model-authored diff **is** that threat appearing. The
executor is not just the biggest engineering gap; it is the one that requires a
threat-model amendment and a containment decision before a line is written.
Whatever posture is chosen, `docs/THREAT_MODEL.md` §4/§5/§6 must be amended in the
same PR as the executor, not after.

**F2 — the model is a prerequisite, not step E.** The claims sequence provider
parity fifth (after the executor). But `roadmap:117-120` records, source-verified,
that `deepagents` 0.6.12's own suggested-model guidance "starts at
frontier/open-weight models well above 7B," and whether `qwen2.5:7b` can drive the
todo/task/filesystem tool-calling loop is **unverified**. If it cannot, a
Phase-A live-fire against the local model produces a null result and every
downstream phase is built on an untested assumption. Provider parity should move
*forward* — a cloud model is the instrument that makes live-fire informative.

**F3 — the harness control plane has no auth, and a HITL endpoint is a privileged
decision surface.** There is zero auth under `harness/` — no `CYCLAW_API_KEY`, no
`require_api_key`, no credential check. `harness/server.py:82-88,370-379` documents
this as a deliberate single-operator tradeoff substituted by loopback bind
(`:413-415`) + `TrustedHostMiddleware` (`:221`). That is defensible for chat and a
status read. It is not defensible for "approve this agent's write to my
repository": `TrustedHostMiddleware` validates the Host *name*, which a DNS-rebinding
page satisfies, and a `text/plain` simple-request POST is not preflighted. Adding
an approve endpoint under the current model would let any local process — and
plausibly a visited web page — approve an agent action. Auth/CSRF must land
*before* the HITL surface, not with it.

---

## Part 3 — Gap map (verified)

| Gap | Status on main | Blocking evidence |
|---|---|---|
| **G1** No code execution / verification loop | Absent by design | zero subprocess in `harness_optimizer/`+`deepagent_github/`; scoring is `in`-substring at `github_coding_runner.py:232` |
| **G2** No real-repo working surface, no git ops | Absent | no `git clone` in repo; `_READ_OPS` is 6 read ops; no git subprocess in `agentic/` |
| **G3** Stub planner, no loop driver | Absent | `draft_plan()` templated (`runners.py:13-43`); `GitHubCodingRunner` unexported, no CLI |
| **G4** Local-only model; provider parity unimplemented | Absent | zero hits for `allow_cloud_providers`/`ChatXAI`/`HandoffEnvelope` |
| **G5** No executable GitHub write path | Absent *by design* | `EXECUTION_ENABLED=False` + `NotImplementedError` (`writer.py:31,182`) |
| **G6** No operator surface for HITL | Absent | 13 harness routes, none decision-bearing |
| **G7** *(new)* GitHub-sourced content never injection-scanned | Absent | no sanitizer import in `gh_client.py`/`context.py` |
| **G8** *(new)* Harness control plane unauthenticated | Present-and-known | no auth under `harness/`; `server.py:370-379` |

---

## Part 4 — Decisions taken (owner, 2026-07-30)

| Decision | Choice | Consequence for the plan |
|---|---|---|
| Executor containment | **Host subprocess + threat-model amendment** | No Docker/microVM dependency on the Windows harness path; `docs/THREAT_MODEL.md` §4/§5/§6 amended in the executor's own PR |
| Write path endpoint | **Full draft-PR enablement** | `EXECUTION_ENABLED` flips and `execute_write` is implemented for `pr_create --draft`, behind the existing four gates, as its own phase-9-reviewed PR (last in sequence) |
| Provider parity | **Before live-fire** | Parity moves from step E to step 3; the first live agent run uses a model known to drive `deepagents` |
| Harness auth | **API key + CSRF before HITL** | A dedicated auth PR lands before any run-trigger or approve/reject route |

---

## Part 5 — Implementation sequence

Eleven draft PRs, each one reviewable concern, each cut fresh from `main`. Every
PR re-runs `python3 .claude/skills/invariant-guard/check_invariants.py` and
`GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q`; doc-touching PRs re-run
`python3 .claude/skills/doc-sync/doc_sync.py`. Numbering below is plan-local, not
repo phase numbers.

### P0 — Doc reconciliation (Low risk, no code)

Fixes drift found during this audit so later PRs are not planned against stale text.

- `docs/work/LangChain_Deep_Agentic_Harness_latest_roadmap.md` (moved from
  `docs/LangChain_Deep_Agentic_Harness_latest_roadmap.md` on 2026-08-02): the config skeleton at
  `:719-737` still says `provider: "lmstudio"` / `http://localhost:1234/v1` /
  `grok-4.3`; main is `"ollama"` / `http://127.0.0.1:11434/v1`, and `CLAUDE.md`
  names `grok-4.5`. Add a dated correction (the doc's own convention, `:67-71`) —
  do not silently rewrite.
- Same file `:179-186`: the **"Phases 6–9: NOT STARTED"** heading and its blockers
  table name files that exist on main. Add the dated correction inline at the
  heading, not only at `:209`.
- `docs/THREAT_MODEL.md:120-135`: §5's bullet list describes the agentic layer's
  write posture but does not state that `agentic/fsconnect/writer.py` (814 lines)
  and `agentic/harness_optimizer/patching.py` are shipped, working, default-off
  write paths. Add that sentence — it is the omission flagged in Part 1.

**Verify:** `python3 .claude/skills/doc-sync/doc_sync.py` shows no new drift.

### P1 — Injection-scan GitHub-sourced content (closes G7)

The first genuinely load-bearing prerequisite: today an attacker-authored PR body
crosses from `gh` straight into whatever consumes it, unscanned.

- Extend `agentic/context.py` (`fetch_pr_context`, `:62-70`) to run every
  GitHub-sourced text field — PR/issue title, body, comments, diff — through
  **`agentic/harness_optimizer/governance.py::inspect_candidate_text` (`:67-85`)**,
  not `utils/sanitizer.check_input`. Rationale: `check_input` raises
  `PromptInjectionError` and would hard-reject a legitimate PR that merely
  *discusses* injection; `inspect_candidate_text` returns findings, so the correct
  behavior is annotate-and-quarantine.
- Attach findings to the context payload as a `governance_findings` list and emit
  an `agentic_context_injection_finding` audit event per hit
  (`utils/logger.py::audit_log`).
- Downstream consumers (P4 onward) treat any `critical:` finding as
  plan-refusing — mirroring `core.py::decide_candidate`'s
  `critical_governance_finding` reject (`:183-223`).

**Critical files:** `agentic/context.py`, `agentic/harness_optimizer/governance.py`
(reuse only), new `tests/test_agentic_context_injection.py`.
**Verify:** a fixture PR body containing a documented `banned_patterns` phrase
produces a finding and does not raise; a clean body produces none.

### P2 — Harness auth + CSRF (closes G8)

- Port `gate.py::require_api_key` (`:95-117`) — `CYCLAW_API_KEY`, fail-closed 401
  when unset, `hmac.compare_digest`, HTTP Bearer — into `harness/server.py` as a
  dependency on every **state-changing** route (the five existing POSTs plus
  everything P9 adds). Keep `GET /`, `/api/status`, `/api/registry`, `/api/sessions`
  open so the console still boots and can tell the operator it needs a key.
- Add an `Origin`/`Sec-Fetch-Site` check on state-changing routes: `TrustedHostMiddleware`
  (`server.py:221`) validates the Host *name* only, which a DNS-rebinding page
  satisfies, and a `text/plain` simple-request POST is not preflighted.
- `static/harness.html`: `api()` (`:237-248`) gains an `Authorization: Bearer`
  header sourced from a key the operator pastes once (stored in-memory only, never
  `localStorage`). Also fix the 429 path while here — `api()` reads `detail.message`
  but `server.py:188-195` emits `detail.error`, so a throttled user currently sees
  a bare `HTTP 429`.
- `powershell/Invoke-CyClaw.ps1:50-52`: pass `CYCLAW_API_KEY` through to the server
  process alongside the existing `CYCLAW_HOME`/`CYCLAW_REPO`/`CYCLAW_HARNESS_PORT`
  exports.

**Tests to update (all found during audit):**
`tests/test_harness.py` (route tests now need the header),
`tests/test_harness_console_contract.py` (the `api()` extraction at `:36-71` must
still parse after the header argument is added — this is the fragile one),
`.claude/skills/CyClaw-Sandbox/harness_runtime_check.py:77-86`,
`.claude/skills/CyClaw-Sandbox/harness_emulation.py:12-26`.

### P3 — Grok/Claude provider parity (closes G4)

Implements the 2026-07-11 owner-approved design at
`docs/work/LangChain_Deep_Agentic_Harness_latest_roadmap.md:686-911`, corrected for the
Ollama migration.

- **Config** (`config.yaml` + `agentic/config.py:115-123`): add
  `allow_cloud_providers: false` and a `providers:` block with per-provider
  `enabled: false` + `model`. Validation additions in `agentic/config.py`: reuse
  `_validate_no_shell_metachars` on model strings; reject unknown provider names;
  **`providers.*.enabled: true` while `allow_cloud_providers: false` is a config
  error, not silently inert.**
- **Gate chain (6 conditions)** exactly as designed: `agentic.enabled` →
  `deepagent_github.enabled` → `allow_cloud_providers` → `providers.<n>.enabled` →
  key env set (`GROK_API_KEY` / `ANTHROPIC_API_KEY`, fail-closed) → per-run
  `--confirm-online`, audited as `agentic_deepagent_cloud_confirmed`.
- **Adapter**: extend `agentic/deepagent_github/model_adapter.py` with
  `build_chat_model(settings) -> BaseChatModel` per the doc's skeleton
  (`roadmap:752-781`). Do **not** reuse `llm/client.py`'s classes — they are
  single-shot `generate(prompt) -> str` wrappers with no tool-calling, and I6
  isolation cuts both ways. Transfer the *discipline* only: env-only keys,
  availability = key presence, type-only error messages.
- **`HandoffEnvelope` + `sanitize_handoff`** per `roadmap:875-901`, emitting
  `agentic_deepagent_cloud_handoff`. This is the artifact that makes egress
  auditable *as egress*.
- **Dependencies**: `langchain-xai` in a **separate** optional extra
  `agentic-deepagents-cloud`, exact-pinned in both `pyproject.toml` and
  `constraints.txt`; `langchain-anthropic` already arrives with `deepagents`.
- **Interrupt posture**: when a cloud provider is active, require `interrupt_on`
  coverage on **all** tools, not just the two write tools — a cloud-driven agent's
  tool call is the moment context leaves the operator's control.
- **Threat model**: amend `docs/THREAT_MODEL.md` egress scope — from "never, except
  the core graph's triple-gated fallback" to "also from the harness, behind the
  six-condition chain," naming `api.x.ai` and `api.anthropic.com`.
- The `xai-` redaction pattern is **already done** (`config.yaml:340`) — do not
  re-add.

**Verify:** config-gate matrix (any one gate false → local-only), fail-closed
key tests, fake-transport `ChatXAI`/`ChatAnthropic` construction, and a
shipped-config contract update pinning every new gate `false`.

### P4 — Live-fire the dormant deepagent path (closes G3 partially)

Now meaningful, because P3 supplies a model that can drive the loop.

- Add a `deepagent-plan` subcommand to `agentic/cli.py:200-239` (read-only: it
  fetches context via P1's scanned path, invokes the agent, prints a plan; no
  writes of any kind).
- Add `"deepagent-plan"` to `utils/ops_runner.py:55` `_AGENTIC_ACTIONS` and to
  `_AGENTIC_JSON_ACTIONS` (`:57`). Note `_TIMEOUT_SEC = 120` (`:51`) is too short
  for a model loop — add a per-action timeout the way `_sync_timeout_sec()`
  (`:136-161`) does for sync.
- Record the result: does `qwen2.5:7b` drive the loop at all, and does the cloud
  provider? This is the empirical answer to the open question at `roadmap:117-120`,
  and it is a documented finding, not a code deliverable.

### P5 — Real-repo read surface (closes G2, part 1)

- Add a `repo_clone` op to `agentic/gh_client.py`'s `_READ_OPS` (`:180`) and
  `build_read_argv` (`:210`), and to `config.yaml:474-480`'s `allowed_read_ops`.
  It inherits the existing binary resolution (`shutil.which` → absolute),
  version floor (`DEFAULT_MIN_GH`, `:38`), `_REPO_RE` argv-boundary revalidation
  (`:235-239`), and transient-only retry (`_is_transient_gh_error`, `:205`).
- New `RepoWorkspaceTools` (read-only) in `agentic/deepagent_github/tools.py`,
  jailing the clone with **`agentic/fsconnect/pathsafe.ScopedRoots` (`:126`)** —
  the repo's strongest containment primitive (POSIX `O_NOFOLLOW`/`dir_fd`
  descent, zero TOCTOU) — under the existing
  `agentic.deepagent_github.workspace_root` (`config.yaml:493`).
- Clone destination is a `TemporaryDirectory` + `_safe_child` (`github_coding_runner.py:33-56`)
  exactly as the fixture runner already does at `:202-204`.

### P6 — Verification executor (closes G1) — **the production-grade gate**

- New `agentic/executor/` package. Single entry:
  `run_verification(worktree: Path, checks: Sequence[Check]) -> VerificationReport`.
- Each check is an argv list run through the house pattern — model it on
  **`agentic/fsconnect/indexer.py::_run_reindex` (`:296-319`)**, which is already
  `[sys.executable, "-m", <module>]` + `capture_output` + explicit `timeout` +
  `check=False` + `audit_log` of the exit code:
  `[sys.executable, "-m", "pytest", "-q", "--tb=short"]`,
  `[sys.executable, "-m", "ruff", "check", "--select", "E,F,I,B,C4,UP,S", "."]`,
  `[sys.executable, ".claude/skills/invariant-guard/check_invariants.py"]`.
- **Containment (per the chosen posture):** `cwd` is the jailed worktree from P5;
  environment is a scrubbed allowlist with `no_proxy`/unset `HTTPS_PROXY` and
  `PIP_NO_INDEX=1` so a test cannot reach the network; hard wall-clock timeout per
  check; `shell=True` never; no inherited API keys.
- Exit codes follow the repo convention (`utils/ops_runner.py:68-91`):
  `0` ok · `2` failed · `3` env/config. `VerificationReport` feeds
  `core.py::decide_candidate` (`:183-223`) as a new hard-reject alongside the
  existing seven.
- **Threat-model amendment ships in this PR**, not after: `docs/THREAT_MODEL.md`
  §4 (the "deliberately *non-executing*" non-goal), §5 (the microVM rationale —
  it must now say *why* self-authored code against the operator's own repo, run
  network-isolated in a jailed worktree, is a different threat than untrusted
  third-party code), and §6 (ladder stage 5 moves from "conditional" to a named
  trigger condition).
- Add `agentic` is already a coverage source; if the executor lands as a top-level
  package instead, add `--cov=` in `ci.yml` **and** `[tool.coverage.run] source`
  (`pyproject.toml:105`) — the §4 trap.

### P7 — Real planner + loop driver (closes G3)

- Replace `draft_plan()` (`agentic/deepagent_github/runners.py:13-43`) — currently
  hardcoded `steps`/`proposed_tests` tuples that ignore `task.instruction` — with a
  model-driven plan → patch → verify → review loop.
- Export `GitHubCodingRunner` from `agentic/harness_optimizer/__init__.py`
  (`:40-68`) and wire the loop through `decide_candidate` so acceptance stays the
  deterministic gate.
- Iterate the planner/reviewer prompts through `harness_optimizer`'s existing
  holdout gates rather than tuning by eye — that machinery already exists and is
  currently unused by any caller (correction C2).

### P8 — Git write operations in the jailed clone (closes G2, part 2)

- `git checkout -b` / `git add` / `git commit` / `git diff` as argv-list
  subprocesses scoped to the P5 worktree. Still **no push, no PR**.
- Committer identity forced to the repo's convention
  (`user.email noreply@anthropic.com`, `user.name Claude`).
- Branch names validated against a strict regex and forced to the
  `claude/<topic>` prefix.

### P9 — Harness run-trigger + HITL surface (closes G6)

Now safe, because P2 landed auth.

- New authenticated routes on `harness/server.py`:
  `POST /api/agent/run` (start a coding run — throttled, joins the rate-limited
  set alongside `/api/chat`), `GET /api/agent/runs/{run_id}` (status + pending
  interrupt), `POST /api/agent/runs/{run_id}/decision` (approve/reject — **not**
  throttled; throttling an approval is hostile). All must reach `agentic/` via
  `utils.ops_runner` only — `tests/test_harness_isolation.py:74-85` is a hard
  blocker on a direct import.
- Progress transport: the console has **no** SSE, WebSocket, or polling today
  (verified — zero `EventSource`/`WebSocket`/`setInterval` in `harness.html`).
  Use the in-repo precedent rather than inventing one: `static/terminal.html:1984`
  self-schedules with `setTimeout` + backoff and uses `AbortController` deadlines
  (`:1103,:1201`). Poll `GET /api/agent/runs/{id}`.
- `static/harness.html` reuse points found during audit: `addMsg()` (`:205-216`)
  **returns the created div** and nothing uses that return value today — append
  approve/reject buttons into a live message node; `table()` (`:221-234`) renders
  the proposed diff via `createElement` + `textContent` (zero XSS surface — keep
  it that way, there is no `innerHTML` in the file and that is a stated contract at
  `:196-197`); the `.toggle` button (`:145-147`) is the existing stateful
  POST-round-trip idiom; `sendBtn.disabled` (`:478-491`) is the existing in-flight
  gate. Add `/run`, `/approve <id>`, `/reject <id>` as three cases in `runSlash()`
  (`:356`) plus three `COMMANDS` rows (`:322-335`).
- `resume_deepagent_interrupt` (`runners.py:82-138`) is the backend — note its
  timeout already collapses to reject (`:96`), so the UI surfaces two buttons and a
  countdown, not three.

**Tests that will break and must be extended:**
`tests/test_harness_console_contract.py` — `test_console_endpoint_exists_on_harness_app`
(`:163-170`) fails on any `api()` call without a matching route, and
`test_console_call_extraction_is_not_empty` (`:148-160`) has a hardcoded
`len(paths) >= 8` floor; `tests/test_harness.py:446-459`
`test_rate_limit_scoped_to_expensive_routes_only` encodes the throttling policy
and needs an explicit decision recorded for each new route.

### P10 — Push + draft-PR enablement (closes G5) — own phase-9 security review

Last, and gated on the checklist the repo already wrote for itself
(`docs/work/DEEP_AGENT_HARNESS_PHASES_6_9.md:67-82`).

- Implement `execute_write` in `agentic/writer.py:171-185` for the `pr_create`
  path only. The argv is already composed and correct
  (`_build_write_argv`, `:64-67` — `gh pr create --repo … --draft`); today it is
  returned as display text and never executed.
- Flip `EXECUTION_ENABLED` (`:31`) — and keep the surrounding comment honest by
  rewriting it: it currently says flipping the flag is insufficient *because* the
  executor is unimplemented, which stops being true.
- The four gates (`mode`, `writes_enabled`, `reason`, `confirm`) stay exactly as
  they are; `pr_comment`/`issue_comment` stay unimplemented in this PR.
- `git push -u origin <branch>` lands here too, scoped to `claude/*` branches.
- **Required before merge**, verbatim from `DEEP_AGENT_HARNESS_PHASES_6_9.md:69-77`:
  the two named pytest files, green `deepagents-harness` CI including
  approve/reject/timeout, OSV + pip-audit review of the optional dependency set,
  `GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q`, `git diff --check`,
  `ruff check --select E,F,I,B,C4,UP,S .`, and **a separate human security review**.
- `docs/THREAT_MODEL.md` §5's first bullet ("GitHub writes are hard-killed")
  becomes false and must be rewritten in this PR.

---

## Part 6 — Invariants and cross-cutting constraints

- **I6 is the hard constraint on every phase.** `harness/` and `agentic/` may never
  import each other or `gate`/`graph`/`mcp_hybrid_server`. Every harness→agentic
  call goes through `utils/ops_runner.py`'s `[sys.executable, "-m", "agentic.cli", …]`
  subprocess shim, which means **every new capability needs both an
  `agentic/cli.py` subcommand and an `_AGENTIC_ACTIONS` whitelist entry**
  (`utils/ops_runner.py:55`). The AST guards with negative self-tests
  (`tests/test_agentic_isolation.py:44-71`) will catch a shortcut.
- **I1–I5 are untouched by all of this** — none of these phases modifies `graph.py`,
  `gate.py`'s auth/sanitizer, or `soul.md` handling.
- **`utils/ops_runner.py:51`'s `_TIMEOUT_SEC = 120`** is wrong for every new action
  in this plan. Add per-action timeouts modeled on `_sync_timeout_sec()` (`:136-161`).
- **Free-text argv values use `--opt=value` single-argv form** (`ops_runner:272-286`)
  and large payloads go through `_write_body()`'s temp file (`:184-201`), never argv.
- **No new dependency without an exact pin in both `pyproject.toml` and
  `constraints.txt`** (only P3 adds one: `langchain-xai`).

---

## Part 7 — Verification

Per PR:

```bash
ruff check --select E,F,I,B,C4,UP,S .
GROK_API_KEY=dummy pytest tests/ -q --tb=short
python3 .claude/skills/invariant-guard/check_invariants.py      # must exit 0
python3 .claude/skills/config-guard/check_config.py --strict    # P3 (config change)
python3 .claude/skills/dep-guard/check_deps.py                  # P3 (new pin)
python3 .claude/skills/doc-sync/doc_sync.py                     # P0, P3, P6, P10
```

End-to-end, once P0–P9 have landed (P10 still dry-run):

1. `python -m agentic.cli --config config.yaml status` → agentic enabled, provider
   resolved, `gh` version above floor.
2. `python -m agentic.cli deepagent-plan --issue <n> --confirm-online` → a plan
   derived from a real issue, with `governance_findings` attached and an
   `agentic_deepagent_cloud_handoff` audit event recorded.
3. `cyclaw-harness` → `POST /api/agent/run` with the API key → run appears in
   `GET /api/agent/runs/{id}`; approve the interrupt from the console.
4. Confirm the executor ran: `pytest`/`ruff`/`invariant-guard` exit codes in the
   run artifact, and the diff exists in the jailed worktree.
5. `python -m metrics` → the new audit events aggregate cleanly.
6. Confirm containment: the committed tree is unmodified (hash before/after, the
   pattern `tests/test_agentic_fixture_repo.py` already uses), and the executor
   subprocess had no network.

Full-system: `/CyClaw-Sandbox` (its harness console REST checks at
`.claude/skills/CyClaw-Sandbox/harness_runtime_check.py` and
`harness_emulation.py` must be extended in P2 and P9 or they silently stop
covering the new routes).

---

## Part 8 — Risk register

| Risk | Where | Mitigation |
|---|---|---|
| Executor escapes the worktree | P6 | `pathsafe.ScopedRoots` jail + scrubbed env + hard timeout; threat-model amendment states the residual honestly |
| `qwen2.5:7b` cannot drive the loop | P4 | P3 lands first so a capable provider exists; P4's deliverable is the empirical answer, not a passing test |
| Cloud egress leaks context | P3 | 6-gate chain + `HandoffEnvelope` audited before bytes leave; `xai-` redaction already shipped |
| Unauthenticated approval surface | P9 | P2 lands auth + origin check first |
| Attacker-authored PR body drives the planner | P1 | `inspect_candidate_text` findings; `critical:` refuses the plan |
| Console contract test breaks silently | P2, P9 | `test_harness_console_contract.py` is parse-based and brittle by design — extend it in the same PR |
| Two open PRs touching `config.yaml` | P3, P5, P6 | Trial-merge locally before opening (the §4 shared-file trap) |
| Feature freeze drift | all | One concern per draft PR; a human merges each |

