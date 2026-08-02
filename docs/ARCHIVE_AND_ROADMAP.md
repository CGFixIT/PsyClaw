# Archive and Roadmap

> **Looking for what is still open?** See
> [`/remaining_work.md`](../remaining_work.md) — the verified, actionable
> backlog. This file holds the *history and rationale* behind those items;
> that one holds the current state with file:line evidence.

This file is the single home for retired design history, superseded plans, and
scratch research that fed CyClaw's `agentic/` and `sync/` layers. It replaces
17 files that were classified, during a 2026-08-01 documentation audit, as
either explicitly retired/superseded, dated one-shot plans whose substance is
better tracked as a living checklist, or scratch research notes never meant to
be read standalone. None of the 17 source files were binding governance, a
live checklist with an open sign-off, or a dated historical record — those
categories stay as their own separate, uncombined files (see each section
below for the specific doc a given topic still lives in when it isn't here).

**Status: this file has been built; the 17 source files it condenses have
NOT yet been deleted.** They still exist on disk, side by side with this one,
pending an explicit follow-up decision to complete the cutover. Do not treat
the existence of both as drift — it's a deliberate staged rollout, not an
oversight.

**Required follow-up before the source files are deleted** (tracked here so it
isn't lost):

- ~~`AGENTS.md` cites `docs/SETUP.md` by name at least twice; those citations
  need to point at `/setup-guide.md` directly before `docs/SETUP.md` goes
  away.~~ **DONE 2026-08-02** — both citations (`AGENTS.md:11` and the
  Windows-PowerShell-setup line) now point at `setup-guide.md`. `docs/SETUP.md`
  itself is still present as a redirect stub, so nothing is dangling either
  way; deleting it remains part of the un-started cutover below.
- **Still owed:** after deletion, run
  `python3 .claude/skills/doc-sync/doc_sync.py` and separately grep
  `CLAUDE.md`/`README.md`/`AGENTS.md` for the other 16 paths being removed —
  this is exactly the class of drift that skill exists to catch, and it does
  not currently check for dangling doc-to-doc references, only the
  config-number and route-table drift it's built for.

**Files this document replaces** (verbatim list, so a future reader can
confirm nothing here was quietly dropped from the retirement announcement):
`docs/agentic/DEEP_AGENT_HARNESS_PHASES_6_9.md`,
`docs/agentic/CyClaw_Safe_Agentic_Enhancement_Plan.md`,
`docs/agentic/FSCONNECT_SQL_ROADMAP.md`,
`docs/agentic/cyclaw_codebase_notes.md`,
`docs/agentic/subagent_researcher_notes.md`,
`docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`,
`docs/LangChain_Deep_Agentic_Harness_latest_roadmap.md`, `docs/SETUP.md`,
`docs/SESSION_NOTES.md`, `docs/PSYCLAW_FEATURE_IDEAS.md`,
`docs/LOCAL_REMOTE_SYNC_GUARD.md`, `docs/DEPENDENCY_CURRENCY_PLAN.md`,
`docs/DROPBOX_SYNC_IMPLEMENTATION_PLAN.md`, `docs/zIdeas/PROPOSED_SKILLS.md`,
`docs/zIdeas/online-llm-grok-claude.md`,
`docs/security-philosophy/SECURITY_THREAT_model.md`,
`docs/NeMo/phase3_implementation_plan.md`.

**Files that stay separate and are NOT folded in here**, because they are
binding governance, a live checklist with a real (possibly still-open)
sign-off line, or a dated historical record: `docs/agentic/AGENTIC_README.md`,
`docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md`, `docs/THREAT_MODEL.md`,
`docs/agentic/FSCONNECT_SECURITY_REVIEW_CHECKLIST.md`,
`docs/agentic/FSCONNECT_WRITE_ENABLEMENT_PLAYBOOK.md`,
`docs/agentic/GITHUB_WRITE_ENABLEMENT.md`, `docs/!TERMINAL_UX_NEXT_STEPS.md`,
`docs/SECCOMP_EBPF_HARDENING.md`, `docs/PONYTAIL_PLUGIN.md`,
`docs/POSTGRES_BACKEND.md`, `docs/TESTCLIENT_HTTPX_DEPRECATION.md`,
`docs/HARNESS_POWERSHELL.md`, `docs/SYNC_README.md`, `docs/NeMo/README.md`,
`docs/NeMo/later_development_guideline.md`,
`docs/NeMo/phase2_implementation_plan.md`, `docs/online-llm/readme.md`,
`docs/future_langchain_plans.md`, `docs/codex-findings-7202026.md`,
`docs/zIdeas/API.md`, `docs/comparisons/INVARIANTS_COMPARISON.md`. If a
still-open idea from one of those files is what you're looking for, it is
tracked in that file, not duplicated here.

## Table of contents

1. Preamble (this section)
2. Retired Subsystem Records
   - 2a. Smaller items (DeepAgents graph harness Phases 6-9, and three
     one-line redirects)
   - 2b. GitHub Deep Agent Harness Optimizer (the master design plan)
   - 2c. LangChain Deep Agentic Harness Roadmap (status/review layer)
3. Agentic Layer Design History
4. Dependency Currency Tracking
5. Fsconnect/Sqlconnect Forward Roadmap
6. Session/Sync Hygiene Notes
7. Skill and Feature Proposals (Unbuilt)
8. NeMo Phase 3 Redirect Status
9. Still-Open Ideas — Index

## 2a. Retired Subsystem Records — smaller items

### DeepAgents graph harness, Phases 6-9

Source: `docs/agentic/DEEP_AGENT_HARNESS_PHASES_6_9.md`. Its own header (lines 6-11) states: **retired by owner decision on 2026-07-31, superseded by `agentic/real_repo_loop.py` as the live real-repo coding path.** The record itself is left "accurate and unmodified" — it documents a dead path, not a live one. All feature flags for this subsystem ship disabled in `config.yaml` by default (line 3-4), and none of what follows enables real GitHub writes, host shell execution, real-repository writes, remote memory, or an external approval decorator (line 17-18).

What phases 6-9 actually built, on top of the phase-5 scaffold in `GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`:

- **Phase 6 — subagent wiring.** `build_deepagent_github()` materializes real subagent dicts (`name`, `description`, `system_prompt`, `model`, callable tools). The only bound tools come from `ProposerWorkspaceTools`: manifest/read/RAG reads, plus writes scoped to `current/` and `proposal.md` only. The Deep Agents backend is `StateBackend` — deliberately not `FilesystemBackend` or `LocalShellBackend` — with built-in filesystem read/write denied, so the built-in execute tool has no host-command path (`StateBackend` has no `execute` method at all). Registry skills surface to the agent as virtual `/skills/*/SKILL.md` files; local memory reads only from `data/agentic/deepagent_github/AGENTS.md`, capped at 64 KB, never fetched remotely or agent-created. When scoped workspace writes are enabled, `interrupt_on` forces approve/reject on both write tools via an in-memory LangGraph checkpointer, and `resume_deepagent_interrupt()` treats a timeout as a reject — no approval queue, no event-loop lookup.
- **Phase 7 — fixture-based evaluator.** `agentic.harness_optimizer.runners.github_coding_runner` copies a committed fixture repo into a temp dir, overlays only declared candidate surfaces, and checks deterministic visible/holdout file expectations. It reuses the read-only `agentic.context` wrappers for task context, but tests inject fake responses — no network in the unit lane. The runner flags undeclared candidate files, undeclared changed surfaces, proposal injection patterns, and visible-case hardcoding, and emits the same `RunReport` shape so `decide_candidate()` stays the single deterministic acceptance gate. The `deepagents-harness` CI job installs the opt-in dependency set and runs approve/reject/timeout regression cases; the default CI matrix does not install Deep Agents at all.
- **Phase 8 — governed candidate records.** `patching.py` is a two-step local-only path: `propose_candidate_application()` accepts only an already-accepted runner result and re-checks candidate text for injection patterns (it does not write anything); `apply_candidate_artifact()` requires **all** of `agentic.enabled`, optimizer enabled, `mode: write`, `writes_enabled: true`, a non-empty human reason, `require_human_confirm_for_accept: true`, and explicit confirmation, before writing an atomic SHA-256-versioned JSON record. It independently rechecks the proposal hash and injection gate, so a caller cannot bypass `propose_candidate_application()` by hand-constructing a proposal. This is explicitly not a source-tree, registry, soul, or GitHub apply path — the existing registry/soul commands remain the only governors for those surfaces, and `agentic/writer.py` remains the disabled GitHub-write boundary.

**Phase 9 — the security gate, preserved as still-binding design rationale even though the path is dead:** before any future executor is considered, the doc requires (line 74-89) the phase-6/7/9 pytest files green, a green `deepagents-harness` CI run including approve/reject/timeout coverage, an OSV/pip-audit review of the optional dependency set, `GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q`, `git diff --check` + `ruff check --select E,F,I,B,C4,UP,S .`, and — the load-bearing clause — **a separate human security review for any request to add shell, host filesystem, GitHub mutation, or source-tree application.** The doc also records that "the separate approval-decorator concept remains absent" (line 86-89): there was no sensitive tool outside Deep Agents in this implementation that needed one, and adding one would itself have been a new security-sensitive feature rather than a prerequisite for the harness as built. Both the human-review requirement and the absent-approval-decorator note should carry forward as design rationale for whatever does eventually add executor-class capability to `agentic/`, even though this specific subsystem is retired.

### One-line redirects

- `docs/SETUP.md` — moved; the file is a one-paragraph stub stating it "has moved to keep a single canonical setup guide instead of two drifting copies" and points to `/setup-guide.md` at the repo root (Windows + Linux, Ollama; the stub dates that target as current as of 2026-07-29).
- `docs/security-philosophy/SECURITY_THREAT_model.md` — confirmed stub: its entire content is a single line, the GitHub URL `https://github.com/cgfixit/CyClaw/blob/main/docs/THREAT_MODEL.md`, i.e. it redirects to `docs/THREAT_MODEL.md`.
- `docs/DROPBOX_SYNC_IMPLEMENTATION_PLAN.md` — its status banner confirms the plan is superseded by the shipped `sync/` package (`sync/cli.py`, `sync/runner.py`, `sync/scheduler.py`, driven from the terminal's Sync Console via `POST /ops/sync`, covered by `tests/test_sync_*.py`), kept only for design rationale; the banner's own words name the successor doc as `Dropbox_Sync_Guide.md`. Note this is ambiguous against the rest of the same file: §5's file layout, §12's documentation deliverables, and §16.3 (Role C) all instead name `docs/SYNC_README.md` as the shipped operator guide. The banner text and the body of its own document disagree on the successor filename — flagging rather than guessing which is current.

## 2b. Retired Subsystem Record — GitHub Deep Agent Harness Optimizer

Source: `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md` (1145 lines).
**Retired by owner decision, 2026-07-31: no further development planned.**
`agentic/real_repo_loop.py` — a separate, simpler plan→patch→verify→commit
pipeline *not* built on `deepagents` — is now the one live real-repo coding
path. Authoritative current behavior lives in
`docs/agentic/DEEP_AGENT_HARNESS_PHASES_6_9.md`; the split between the two
subsystems is recorded in `docs/THREAT_MODEL.md`'s fifth amendment and
`docs/agentic/AGENTIC_README.md` §9. The plan doc's code (`builder.py`,
`subagents.py`, `tools.py`, `permissions.py`, and the rest of
`agentic/deepagent_github/` and `agentic/harness_optimizer/`) and its
tests/CI lane remain in the tree, unmodified and passing — nothing was
deleted, nothing beyond what already shipped disabled was disabled. Do not
propose completing its live-fire invocation.

### What the plan originally proposed

Two out-of-band `agentic/` features, both disabled by default, neither
importable from `gate.py`/`graph.py`/`mcp_hybrid_server.py`:

- **`agentic/harness_optimizer/`** — a governed better-harness-style
  meta-agent loop: `Experiment`/`Surface`/`Variant`/`RunReport`/
  `CandidateDecision` models, visible train cases plus hidden holdout cases,
  deterministic train+holdout scoring as the primary proof (a local-model
  judge only ever an optional second signal), keep/discard gated on score
  improvement + governance findings, rollback via versioned artifacts rather
  than silent mutation.
- **`agentic/deepagent_github/`** — an optional LangChain Deep Agents-backed
  local GitHub coding harness (LM Studio provider, scoped CyClaw tool
  wrappers) with eight defined subagents (`repo-context-reader`,
  `issue-planner`, `patch-proposer`, `test-selector`, `diff-reviewer`,
  `security-reviewer`, `pr-writer`, `harness-proposer`) that could read
  context, plan, propose diffs, and draft PR text — never apply diffs or
  write to GitHub by default.

Both were scoped with the same governance discipline as the rest of
`agentic/`: `propose → scan → human reason → explicit confirm → atomic apply
→ SHA-256 version record`, no autonomous apply, no hidden background loop.
The full proposed directory layout (two package trees under `agentic/`),
its dependency list (`deepagents`, `langchain`, `langchain-mcp-adapters`,
`fastmcp`, `quickjs`), and its CLI command surface
(`agentic.cli deepagent-github ...` / `harness-optimizer ...`) were never
built beyond scaffold — treat all three as dead specification, not as
partially-implemented surface to finish.

### What actually shipped (phases 0-9)

Phases 0-5 landed config plumbing, the harness-optimizer data models, a
mocked runner/scoring/governance scaffold, a fake-transport LM Studio
proposer path, and a `deepagent_github` skeleton — all inert, all
unimported by any live path (PRs #492/#493). Phases 6-9 then wired real
(but still disabled/gated) behavior on top of that scaffold:

- **Phase 6** — `builder.py` now materializes validated `SubAgent` dicts and
  real callable tools from `default_tool_specs()` instead of bare name
  strings + an empty tool list (the pre-phase-6 wiring reported
  `created=True` while being non-functional against the real `deepagents`
  package — fixed, not merely documented).
- **Phase 7** — `agentic/harness_optimizer/runners/github_coding_runner.py`
  implements the `HarnessRunner` protocol against local fixture repos
  (`tests/fixtures/github_coding_repo/`), no network, no shell.
- **Phase 8** — `agentic/harness_optimizer/patching.py` adds a governed
  propose/apply path (reason + confirm + write-mode + master-switch +
  optimizer-enable gates, atomic SHA-256-versioned artifacts). Deliberately
  does **not** add registry/soul/source-tree/GitHub apply adapters — those
  existing human-governed paths stay authoritative.
- **Phase 9** — security regression coverage + a `pip-audit` CI gate;
  execution stays disabled pending separate review.

A dated "Unwired Scaffold Inventory" (post-phase-5, 2026-07-10) tracked
items left on no import path (`draft_plan()`, `GovernanceFinding`,
`validate_write_policy()`, 9 of 11 `SurfaceType` members,
`HarnessOptimizerConfig`'s unenforced `require_human_confirm_for_accept`);
a follow-up "Current Resolution Ledger" records each as since wired to a
real (still gated) consumer — e.g. `draft_plan()` now returns a
deterministic no-write plan instead of raising, per PR #499's fix.

### Retirement rationale

The document itself frames the choice: `real_repo_loop.py` is "a separate,
simpler plan→patch→verify→commit pipeline, not built on `deepagents`" that
became the one live real-repo path. The plan's own §"Alternative considered"
(recorded 2026-07-11) makes the underlying tradeoff explicit — see below.
Nothing in the retirement note attributes the decision to a defect in the
Deep Agents scaffold; it reads as an architectural-fit call plus a
maintenance-surface call (two coding-harness designs is more than a
single-operator portfolio project needs going forward).

### Durable design rationale worth keeping

- **Rejected alternative, with reasoning:** a five-node LangGraph-native
  GitHub coding harness (`review → plan → gate_write → execute → audit`,
  using `Annotated[list, operator.add]` reducers) was compared directly
  against the Deep Agents approach. Its assessed advantages: zero new
  dependencies (`langgraph`/`langchain-core` already pinned) and every
  security gate as a single testable node function — a better match for
  CyClaw's topology-as-policy convention (Invariant I2) than a
  framework-managed agent loop. Recorded verdict: the LangGraph-native
  variant is the better architectural fit "unless many more agentic
  connectors are planned." Deep Agents was chosen anyway as the *optional*
  path (draft PR #515 kept it behind the `agentic-deepagents` extra), with
  the LangGraph-native design explicitly preserved as the fallback if that
  optional extra ever fails a dependency review. Given the 2026-07-31
  retirement and `real_repo_loop.py`'s simpler, non-`deepagents` design
  actually winning, this recorded fallback reasoning is the closest thing
  to a documented "why the simpler pipeline was right all along."
- **Default-deny filesystem permission ordering**, preserved from the same
  research pass, worth reusing for any future scoped write surface: allow
  rules first, then explicit denies, then a catch-all deny — e.g.
  `Allow(<allowed paths>)`, `Deny(".env")`, `Deny("secrets/**")`,
  `Deny("**")`.
- **Four HITL/audit implementation corrections** logged against early phase
  6-9 drafts, generalizable beyond this subsystem: use
  `utils.logger.audit_log()` (`utils/logger.py:210`) rather than a raw
  `open("audit.jsonl", "a")` append; anchor paths via the repo's `_BASE_DIR`
  pattern rather than a cwd-relative `os.getenv(...)`-derived path; score
  risky tool-argument substrings with word boundaries so benign names like
  `turkey.csv` don't false-positive on `key`; call
  `asyncio.get_running_loop()`, not `asyncio.get_event_loop()`, from
  coroutine-driven approval code.
- **HITL control-plane choice:** inside a Deep-Agents-style harness,
  `interrupt_on` plus a checkpointer is sufficient as the human-in-the-loop
  gate (approve/reject/timeout-as-rejection, all CI-covered); a separate
  approval-decorator layer was scoped only for a hypothetical tool outside
  Deep Agents and was never built since no such tool exists.

## 2c. Retired Subsystem Record — LangChain Deep Agentic Harness Roadmap

Tracks the now-retired `agentic/deepagent_github/` + `agentic/harness_optimizer/`
subsystem: a LangChain Deep Agents-backed local GitHub coding harness plus a
better-harness-style optimizer, designed and built across
`docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md` (canonical design
plan), `docs/agentic/DEEP_AGENT_HARNESS_PHASES_6_9.md` (implemented-controls
record), `docs/LangChain_Deep_Agentic_Harness_latest_roadmap.md` (status/review/
roadmap), and `docs/future_langchain_plans.md` (root pointer). **Retired by
owner decision, 2026-07-31: no further development planned.**
`agentic/real_repo_loop.py` — a separate, simpler plan→patch→verify→commit
pipeline not built on `deepagents` — is now the one live real-repo coding path
(`docs/THREAT_MODEL.md`'s fifth amendment; `docs/agentic/AGENTIC_README.md` §9).
The code (`builder.py`, `subagents.py`, `tools.py`, `permissions.py`,
`harness_optimizer/`), its tests, and its `deepagents-harness` CI lane remain in
the tree unmodified and still green — nothing was deleted beyond what already
shipped disabled (`agentic.enabled: false`, all nested `deepagent_github`/
`harness_optimizer` flags `false`).

### What shipped (phases 0-9)

All nine phases merged to `main`, always behind disabled-by-default gates:
config/docs (0-1), harness-optimizer data models and workspace builder (2),
mocked runner + acceptance gate (3), LM Studio proposer invocation with scoped
MCP-shaped tool wrappers (4), the `deepagent_github` skeleton (5), then via
PR #515 (`agent/deepagent-harness-phases-6-9`, merged 2026-07-13): real
subagent/tool/memory/skills wiring on a `StateBackend` with all built-in
filesystem tools denied (6), a fixture-repo eval runner with deterministic
scoring (7), governed propose/apply persisting **local versioned JSON
artifacts only** — never registry/soul/GitHub writes, which stay governed by
`agentic.cli apply-skill` and `utils/personality.py` (8), and a security-gate
CI job + doc (9). Companion cleanup PRs #516-520 merged; #497 (a competing
research doc) was closed unmerged, its three unique items folded into the
roadmap doc's "Folded from PR #497" section. Phase 9 was always a pre-executor
*review checklist*, never authorization for a real write executor — no phase
ever enabled shell, host filesystem, or GitHub-write execution.

### Explicit non-goals (recorded, not just deferred)

- **Bridging `mcp_hybrid_server.py` via `langchain-mcp-adapters`** — the
  roadmap doc notes the adapter package exists and works, but explicitly keeps
  it undeclared: the retrieval-only MCP server "never grows agentic tools,"
  per I6 module isolation. Not a gap; a boundary.
- `deepagents`' `LocalShellBackend` (real `subprocess.run(shell=True)`) and
  unrestricted `FilesystemBackend` over the real repo — categorically excluded
  in favor of `StateBackend` + deny-all `FilesystemPermission`.
- Grok/Claude provider parity for the harness — designed in full (six-gate
  chain mirroring I3, `agentic.deepagent_github.allow_cloud_providers`,
  per-provider `providers.<name>.enabled`) but **never implemented**; the
  entire "Provider parity design" section in the roadmap doc is a proposed
  skeleton only, now moot with the subsystem retired.
- Registry/soul persistence as phase-8 scope — deliberately narrowed away
  during implementation; those surfaces keep their existing single governors.

### Review checklist (R1-R10) disposition — historical, not actionable

R1 (filesystem-write posture / stale alias) — fixed before merge, 2026-07-13.
R2 (real Deep Agents graph constructed but only ever invoked via `FakeAgent`
in CI, never a live interrupt round-trip) — accepted as explicit scope, never
closed. R3 (`resume_deepagent_interrupt` had no try/except / unaudited
failure) — skeleton written, never landed. R4 (`apply_candidate_artifact`
never re-verified an accepted `CandidateDecision` produced the proposal —
`decision_fingerprint` binding proposed) — never landed. R5 (audit-event
count asymmetries between candidate/baseline lifecycle events) — never
landed. R7 (CI's `pip-audit` step re-declares pins via `printf`, drifting
silently from `constraints.txt`) — never landed. R8 (test gaps: unpinned
`finish_proposal` deny-by-default, dead `selected_commands` field) — never
landed. R9 (`DEEPAGENT_API_KEY` undocumented env credential) — never
documented. R10 (style nits) — never landed. All of R2-R10 are now dead
weight against a retired path; do not resurrect them as work items unless the
harness itself is un-retired.

### Durable ideas — real, independent of the retired path

Two items are genuinely worth carrying forward as their own work, not as
harness archaeology:

- **R6 — hardcoded tunable, still present.** `_MAX_MEMORY_BYTES = 64_000` is
  hardcoded at `agentic/deepagent_github/memory.py:11` (the roadmap doc's own
  fix proposal cited `memory.py`; if you've seen this attributed to
  `agentic/harness_optimizer/core.py`, that's wrong — verified against the
  current tree). This violates CLAUDE.md §2's "`config.yaml` is the single
  source of truth for every tunable" rule regardless of whether the harness
  ever ships live. The fix is cheap and was already fully designed and never
  applied: add `agentic.deepagent_github.memory_max_bytes: 64000` to
  `config.yaml`, validate it in `agentic/config.py` (`_validate_positive_int`),
  and thread `max_bytes=cfg.memory_max_bytes` into
  `load_local_memory_files()`. Since the module is dead code on no import path
  today, this is close to a pure docs/config-hygiene fix — worth doing
  precisely because it's low-risk, not despite the retirement.
- **Corpus-source allowlist for cloud egress.** Recorded in the roadmap's
  "Sanitized context handoff pipeline" section as the honest limitation on
  pattern-based redaction: redaction "cannot certify that free text contains
  no confidential meaning," and "minimizing *which documents* are eligible for
  cloud context (an allowlist of corpus sources for hybrid mode) is the
  stronger control." This is a real security control on the **existing,
  live** triple-gated Grok/Claude fallback (I3) in `graph.py`/`gate.py` — it
  has nothing to do with the retired Deep Agents harness, was never
  implemented for the core hybrid path either, and deserves explicit
  prioritization on its own merits rather than being dismissed as
  harness-adjacent scratch content.

Lower-priority research worth a pointer, not a task: the roadmap's three-tier
memory model (checkpoint/store/audit-log) and subagent-deployment policy
table for a *future* richer agent memory layer, and the recorded
LangGraph-native alternative (5-node `review→plan→gate_write→execute→audit`
`StateGraph`, zero new dependencies, assessed as the better architectural fit
"unless many more agentic connectors are planned") — both speculative design
inputs for any future real-repo agent work, not bound to the retired
`deepagents` package specifically, and both already superseded in practice by
`agentic/real_repo_loop.py`'s simpler pipeline.

## 3. Agentic Layer Design History

Three now-retired docs under `docs/agentic/` chained into one effort: a codebase
recon pass (`cyclaw_codebase_notes.md`), an external-tooling survey
(`subagent_researcher_notes.md`), and the master plan they both fed
(`CyClaw_Safe_Agentic_Enhancement_Plan.md`, v0.1). The plan cites the other two
directly ("See `cyclaw_codebase_notes.md`… See `subagent_researcher_notes.md`
for citations + confidence") — they are working notes for a single decision,
not independent documents, which is why they're consolidated together here.

### `cyclaw_codebase_notes.md` — the recon pass

Self-described as a "read-only research artifact; no code is proposed here,"
distilled from a full read of CyClaw v1.4.5 (branch
`claude/cyclaw-agentic-research-ouvzue`). It mapped four things any new
out-of-band feature would need to respect or reuse:

- **The request path to leave alone**: `POST /query` (`gate.py:220`) → rate
  limit → sanitizer → `GraphState` → `compiled_graph.invoke` → graph → response,
  entry hardcoded via `graph.set_entry_point("retrieve")`, routing deterministic
  (`score_router`, `user_gate_router`), Grok gated by `mode=="hybrid" AND
  grok.enabled AND user_confirmed_online`. (Line numbers as cited in the
  original notes; treat them as a snapshot of that HEAD, not a current-code
  guarantee.)
- **The `sync/` precedent as the template to copy**: never imported by
  `gate.py`/`graph.py`/`mcp_hybrid_server.py`; subprocess discipline via argv
  list + `shutil.which` + version floor (`sync/runner.py:54,467`); audit
  integration through `utils.logger.audit_log`; an exit-code contract; an
  additive, `enabled:false`-default config block; its own error subtree
  (`SyncError`). Every one of these became a requirement the shipped
  `agentic/` layer satisfies.
- **The soul-governance pattern as the template for any self-proposal
  surface**: `utils/personality.py`'s `propose_evolution` (never writes,
  advisory only) vs. `apply_evolution` (enforces the injection scan, requires a
  human `reason`, atomic write via tmp+`os.replace`) — later mirrored
  near-verbatim into `agentic/registry.py`'s `propose_skill`/`apply_skill`.
- **One explicitly flagged gap**: "Skills are filesystem-discovered
  `.claude/skills/<name>/SKILL.md`… there is no governed registry today — a
  clean gap to fill with the propose/apply pattern." This gap is closed: the
  same effort that produced these notes shipped `agentic/registry.py`'s
  `SkillRegistry` (propose/apply, injection scan at the write boundary,
  sha256 versioning, atomic write — a direct mirror of `personality.py`), per
  the master plan's "Implemented design" section. It has not regressed —
  `config.yaml`'s `agentic.registry_path` (`data/agentic/skills_registry.json`)
  is live in the shipped config today.

### `subagent_researcher_notes.md` — the external-tooling survey

An explicitly confidence-labeled survey (High/Medium/Low, sources listed and
dated June 2026, "treat specific version/metric claims as indicative, not
authoritative") of five external agentic tools, each mapped to a transferable
lesson and an explicit conflict with a CyClaw invariant. What the master plan
actually adopted from it:

- **Claude Code (High confidence)** — adopted the *pattern*, not the autonomy:
  deterministic hooks as enforcement ("cannot hallucinate") validated CyClaw's
  own "topology = policy" philosophy; subagent context isolation reaffirmed
  the out-of-band model already in use; skills-as-files confirmed
  `.claude/skills/` was right but incomplete (see the registry gap above).
  Rejected: autonomous multi-agent orchestration and tool auto-invocation.
- **GitHub Copilot SDK/CLI (High confidence)** — its
  permission-request/preToolUse-deny hook shape is cited as validating the
  writer triple-gate design, and its MCP allow-list shape confirmed keeping
  GitHub reads behind an explicit allow-list (`agentic/gh_client._READ_OPS`).
  Critically, this is also where the **`gh` CLI vs. PyGithub/SDK decision**
  gets made: the notes state outright, "prefer the `gh` CLI as a subprocess…
  over an SDK/library dependency — keeps `pip-audit`/`osv` surface unchanged."
  That reasoning is what the master plan's trade-off table encodes as Route A
  (chosen) vs. Route C (deferred) — see below.
- **Hermes Agent (Medium confidence)** — adopted "persist what worked" as a
  versioned, file-as-truth skill, explicitly *with the autonomy removed*: "CyClaw's
  registry is Hermes' idea with the autonomy removed." Conflict flagged as
  critical because Hermes self-modifies skills with no human gate — directly
  antithetical to Soul Governance.
- **OpenClaw/ClawHub (Medium confidence)** — adopted the registry *shape*
  (named, versioned, searchable) and read moderation hooks as the analogue of
  CyClaw's injection scan at the write boundary. Rejected the public networked
  marketplace / remote install as an unacceptable supply-chain surface.
- **Rust-rewritten harnesses (Low/unverified confidence)** — reaffirmed rather
  than introduced anything: CyClaw already shells out to hardened external
  binaries (`rclone`, `gh`, LM Studio/Ollama); no Rust rewrite was ever a live
  option.

### `CyClaw_Safe_Agentic_Enhancement_Plan.md` — the master plan

Proposed an out-of-band `agentic/` package, modeled on `sync/`, with two
off-by-default capabilities: (1) read-only GitHub context via the `gh` CLI
(argv-list, allow-listed ops, no shell, audited), and (2) a governed skills
registry reusing the soul propose/apply pattern. A write scaffold was
specified but deliberately inert in v0.1: `EXECUTION_ENABLED = False`, the
executor raising `NotImplementedError`, gated behind an out-of-band analogue of
the triple-gate (`mode==write AND writes_enabled AND reason AND confirm`).

**Route selection and the durable rejection of Route C.** The plan weighed four
routes for GitHub access:

| Route | Verdict |
|---|---|
| A. Out-of-band `agentic/` via `gh` CLI, read-first, writes stubbed | **Chosen** |
| B. Extend the MCP server with a GitHub tool | Rejected — weakens MCP's "retrieval-only, no sampling" property |
| C. PyGithub/SDK library in-process | **Deferred alternative** |
| D. In-graph agentic node | Rejected outright — breaks topology=policy / RAG-first |

Route C — an in-process PyGithub/SDK dependency instead of shelling out to
`gh` — was not rejected on capability grounds (the plan concedes it offers a
"richer API"); it was deferred specifically because it **adds a new runtime
dependency and therefore a new SCA surface**: a fresh entry in `pip-audit`/`osv`
scans that a subprocess call to an already-present external binary does not
create. `gh`, like `rclone` before it, costs zero new dependencies and leaves
the CI/pip-audit/osv bar exactly where `main` already clears it. The plan
records Route C as the fallback *only* "if `gh` cannot be required" — i.e., an
environment-availability escape hatch, not a preference. This is the specific
piece of design reasoning worth preserving verbatim: it is why `agentic/`
still shells out to `gh` today rather than importing a GitHub SDK, and the
underlying constraint (dependency surface vs. capability) will resurface any
time someone proposes replacing the `gh` subprocess call.

**Roadmap items — shipped, superseded, still open.** The plan's four-item
roadmap, checked against the current tree:

1. *(marked done in the plan itself)* Docs + read-only skeleton
   (`agentic/config.py`, `agentic/gh_client.py`, `agentic/context.py`) +
   governed registry (`agentic/registry.py`) + stubbed writer
   (`agentic/writer.py`) + tests (`tests/test_agentic_*.py`, cited at plan-time
   as 54 tests, subprocess-mocked, no live `gh`). **Shipped**, unchanged in
   shape since.
2. "Wire `context` output into a session-side helper (human reads PR context).
   No exec." — subsumed by later work rather than done as a standalone step;
   `agentic/context.py`'s structured PR/issue/repo bundles are now consumed by
   the harness (`harness/`) and the real-repo pipeline's planning stage instead
   of a separate helper.
3. "Optional: enable real writes — implement `execute_write` behind the same
   gate… only then consider flipping `EXECUTION_ENABLED`. Requires explicit
   human sign-off and a security review." **Shipped, but still disarmed.**
   This became a materially larger effort than the plan's stub: a plan → patch
   → verify → human-decides → commit loop (`agentic/real_repo_loop.py`)
   against a jailed real clone, backed by a sandboxed verification layer
   (`agentic/executor/`, argv-list checks, scrubbed env, per-check timeout —
   "soft sandbox, not a kernel boundary"), with GitHub push/PR reachable via
   `real-repo-run-decide --push/--publish` or standalone subcommands. Per
   `config.yaml:468-508`, it remains gated off by default —
   `agentic.enabled: false`, `deepagent_github.enabled: false`,
   `allow_github_writes: false`, and `EXECUTION_ENABLED` hardcoded `False` in
   code, not just config — consistent with the plan's own precondition that
   this step needed explicit sign-off before arming. See
   `docs/agentic/GITHUB_WRITE_ENABLEMENT.md` for the enablement path.
4. "Optional: surface registry skills to the operator tooling (still
   read-only at runtime)." Best read as **still open** as a discrete
   deliverable — no dedicated doc or module claims this was done as specified;
   the closest shipped analogue is the harness's own registry/session surface,
   which is adjacent but not a direct implementation of this line item.

**A related, later component not in these three docs but relevant to the
"superseded" framing**: `agentic/deepagent_github/` grew a second subsystem
after this plan — a DeepAgents subgraph (`builder.py`) explored as an
alternative to the real-repo loop. It was retired by owner decision on
2026-07-31 ("no further development is planned," per
`docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`), superseded by
`agentic/real_repo_loop.py`; its code/tests/CI are kept, not deleted, matching
this repo's general pattern of leaving superseded-but-tested code in place
rather than churning it out.

**Net effect on the six invariants**: nothing in this history changed the
invariant set. The master plan's own honesty checklist (I1–I6 predecessors,
five invariants at plan-time) claimed PASS across the board, and every
subsequent expansion (executor, real_repo_loop, deepagent_github) has stayed
inside the same out-of-band/triple-gate/disarmed-by-default shape the plan
established — the isolation boundary is still enforced and unit-tested
(`test_agentic_isolation.py`), not merely asserted in docs.

## 4. Dependency Currency Tracking

Source: `docs/DEPENDENCY_CURRENCY_PLAN.md`, deferred from PR #596 (merged
2026-07-21), which added the `verify-deps` skill and ran its PyPI currency +
CVE sweep across all 26 pinned packages. That doc was a one-shot snapshot;
this section reframes it as a living checklist so status can be updated in
place as bumps land, instead of leaving a dated planning doc that silently
goes stale.

**Re-derive "Target pin" before acting on any row** — PyPI moves continuously
and the values below are the 2026-07-21 sweep, spot-checked against the
current tree on 2026-08-01 (see Verification note). Get fresh targets with:

```bash
python3 .claude/skills/verify-deps/extract_pins.py   # current pins, all 4 install surfaces
# then, per package: WebFetch https://pypi.org/pypi/<package>/json -> info.version
```

Per `CLAUDE.md` §7 and `dep-guard`'s Guardrails, **bumping a pinned dependency
is Medium-High risk**. This table is a worklist for that review, not a
pre-approved change list — nothing here should be bumped without running the
affected test suite afterward and updating `pyproject.toml` +
`constraints.txt` together (`environment.yml`/`requirements.txt` too, where
the package appears there).

**Verification note:** all 11 "Current pin" values below were checked
directly against `pyproject.toml`, `constraints.txt`, and `environment.yml` on
2026-08-01 and match the 2026-07-21 plan's recorded pins exactly (e.g.
`ruff==0.15.20` at `pyproject.toml:52`, `constraints.txt:97`,
`environment.yml:70`; `fastapi==0.138.0` at `pyproject.toml:11`,
`constraints.txt:33`). No bump in this set has landed yet — every row's
Status is **not yet done**.

### Living checklist

| Tier | Package | Current pin | Target pin | Status |
|---|---|---|---|---|
| 1 | `ruff` | 0.15.20 | 0.15.22 | not yet done |
| 1 | `mypy` | 2.1.0 | 2.3.0 | not yet done |
| 2 | `langgraph` | 1.2.6 | 1.2.9 | not yet done |
| 2 | `langchain` | 1.3.11 | 1.3.14 | not yet done |
| 2 | `langchain-openai` | 1.3.3 | 1.3.5 | not yet done |
| 2 | `psycopg` (+ `psycopg-binary`, lock-step) | 3.2.13 | 3.3.4 | not yet done |
| 2 | `pgvector` | 0.4.2 | 0.5.0 | not yet done |
| 3 | `fastapi` | 0.138.0 | 0.139.2 | not yet done |
| 3 | `uvicorn` | 0.49.0 | 0.51.0 | not yet done |
| 3 | `langchain-core` | 1.4.8 | 1.5.0 | not yet done |
| 3 | `websockets` | 15.0.1 | 16.1.1 (**major**) | not yet done — blocked on langgraph-sdk compat check |

### Tier rationale (preserved from the source plan)

- **Tier 1** (`ruff`, `mypy`) — dev-tool-only, zero runtime blast radius:
  neither ships nor runs in production. Pinned in `pyproject.toml`'s
  `dev`/`full` optional-dependency groups, `constraints.txt`, and
  `environment.yml`. Still not risk-free — a `ruff`/`mypy` bump can silently
  change lint rules or type-check semantics mid-CI (`dep-guard`'s own
  Gotchas), so each needs a real `ruff check --select E,F,I,B,C4,UP,S .` run
  afterward; `mypy` is best-effort/not CI-enforced per `CLAUDE.md` §4, so its
  bump is lower-stakes but still worth a spot check.
- **Tier 2** (`langgraph`, `langchain`, `langchain-openai`, `psycopg`,
  `pgvector`) — real runtime dependencies, but the gap is patch/one-minor and
  nothing in the codebase does anything unusual with them. `langgraph` is
  pinned in all 4 install surfaces; `langchain`/`langchain-openai` only in the
  `agentic-deepagents` extra + `constraints.txt` (not the default install
  path — `agentic.deepagent_github` lazy-imports them); `psycopg`/`pgvector`
  only in the `postgres`/`pgvector` extras + `constraints.txt`. Bump
  `psycopg` and `psycopg-binary` together — constraints files can't carry the
  `[binary]` extra, so it's pinned as a separate line. Per-package
  verification: `langgraph` → `GROK_API_KEY=dummy pytest tests/test_graph.py
  tests/test_due_diligence_invariants.py -q` then
  `python3 .claude/skills/invariant-guard/check_invariants.py` (I1–I4 depend
  on `graph.py`'s actual LangGraph wiring); `langchain`/`langchain-openai` →
  `pytest tests/test_agentic_harness_phase679.py
  tests/test_agentic_deepagent_optional.py -q`; `psycopg`/`pgvector` →
  `pytest tests/test_personality_postgres.py tests/test_ratelimit_postgres.py
  tests/test_pgvector_store.py -q` against a live Postgres/pgvector service
  (the `postgres-backend` CI job's own target — not runnable standalone).
- **Tier 3** (`fastapi`, `uvicorn`, `langchain-core`, `websockets`) — needs an
  explicit compatibility check before bumping. `fastapi`/`uvicorn` are the
  HTTP stack `gate.py` runs on; check the FastAPI changelog for the
  0.138→0.139 range before bumping, then run `tests/test_gate.py`,
  `tests/test_gate_ops.py`, and `tests/test_terminal_contract.py` (the
  terminal-contract test pins exact route/behavior expectations a framework
  bump could shift). `langchain-core` is a base dependency, not an optional
  extra — it affects the default install (see the CVE comment near
  `chromadb`'s pin in `pyproject.toml`), so a bump needs the full test suite,
  not just the agentic-scoped subset. `websockets` is the outlier: its
  `constraints.txt:40-41` comment states it's pinned direct specifically
  because `langgraph-sdk imports websockets.asyncio at graph import time;
  keep this direct so legacy/no-deps paths cannot strand the app on
  websockets 12.x`. Going 15.x→16.x is a **major** bump — exactly what that
  comment warns about. Confirm whatever `langgraph` version is current (post
  Tier-2 bump or not) actually supports `websockets` 16.x before touching
  this pin; if in doubt, bump `langgraph` first, re-check, and treat
  `websockets` as its own follow-up rather than bundling it into the Tier 2/3
  batch.

### Explicitly excluded from this tracker

Not bump candidates — don't add rows for these without re-opening the
underlying decision:

- `numpy` (1.26.4) — held below 2.x on purpose (`dep-guard` D2); 2.x removes
  `np.float_` and breaks chromadb/onnxruntime.
- `chromadb` (1.5.9) — already latest; CVE-2026-45829 is risk-accepted
  (embedded `PersistentClient` only) with no fixed release available.
- `pydantic`/`pydantic-core` — `pydantic` already latest (2.13.4);
  `pydantic-core` has a newer release (2.47.0 as of the 2026-07-21 sweep) but
  no paired `pydantic` release yet per `constraints.txt`'s own comment.
  Bumping `pydantic-core` alone breaks the resolver (`dep-guard` D1) — not a
  candidate until a compatible `pydantic` release ships; re-check
  `dep-guard`'s `_PYDANTIC_LOCKSTEP` constant against PyPI when it does.
- `torch` — pinned to a `+cpu` local-version build off the PyTorch CPU index,
  not generic PyPI; the sweep couldn't enumerate that index the same way.
  Any bump is its own careful pass (Dockerfile, CI torch-wheel cache key, the
  documented CVE-2025-32434 minimum-safe-version rationale) — never folded in
  here.
- `pyyaml`, `httpx`, `sentence-transformers`, `rank-bm25`, `nltk`, `pytest`,
  `pytest-asyncio`, `pytest-cov`, `bandit`, `deepagents`, `pydantic-settings`
  — already at latest as of the 2026-07-21 sweep; re-run the sweep to confirm
  before assuming that still holds.

### Suggested execution shape (open, not started)

1. Tier 1 (`ruff`, `mypy`) — one small PR, `ruff check` clean, done.
2. Tier 2 (`langgraph`, `langchain`, `langchain-openai`, `psycopg`,
   `pgvector`) — one PR, run each package's listed verification command,
   confirm `dep-guard` and `invariant-guard` still pass clean after.
3. Tier 3 (`fastapi`, `uvicorn`, `langchain-core`) — one PR, full test suite,
   changelog-checked first.
4. `websockets` — only after confirming `langgraph`/`langgraph-sdk`
   compatibility with 16.x; likely folds into or follows the Tier 2 PR rather
   than standing alone.

For every PR in this sequence: update `pyproject.toml` AND `constraints.txt`
together (`dep-guard`'s D6 check catches a one-sided edit), update
`environment.yml` if the package is pinned there too, run
`python3 .claude/skills/dep-guard/check_deps.py` and
`python3 .claude/skills/verify-deps/extract_pins.py` before committing, and
state the specific version-to-version bump in the PR body per `CLAUDE.md`'s
Medium-High risk tier ("Proceed; expand tests; state the rollback path in the
PR body").

## 5. Fsconnect/Sqlconnect Forward Roadmap

Source: `docs/agentic/FSCONNECT_SQL_ROADMAP.md`. Every item below is still governed by
the design invariants that doc states up front — out-of-band isolation (new code only
under `agentic/<connector>/`, run via `python -m agentic.<connector>.cli`, guarded by
the recursive AST check in `tests/test_agentic_isolation.py`), topology-never-a-node,
`pathsafe` as the sole filesystem authority, the four-gate mutation pattern
(`enabled` + explicit flag + human `reason` + `confirm`), and audit-everything. Status
below was checked against the current tree (2026-08-01), not against the doc's own
claims — several items have moved since the doc was written.

### FS Phase 2 — production write-enablement (doc lists this as forward work; verified SHIPPED)
The doc frames Phase 2 as still-to-do, but the code has moved past it. `FsWriter.fs_delete`
(`agentic/fsconnect/writer.py:550`) implements the trash-vs-purge distinction behind the
destructive gate; `agentic/fsconnect/trash.py` and `agentic/fsconnect/quota.py` (ledger +
verified recompute, fail-closed on stale/corrupt ledger) both exist; write rate-limiting
reuses `utils/ratelimit.RateLimiter` per-root and globally (`writer.py:369-388`,
`fs_cfg.rate_limit_settings`). Both docs the playbook item calls for exist:
`docs/agentic/FSCONNECT_WRITE_ENABLEMENT_PLAYBOOK.md` and
`docs/agentic/FSCONNECT_SECURITY_REVIEW_CHECKLIST.md`. Treat this phase as closed, not
forward-looking.

### FS Phase 3 — incremental indexing & richer corpus (partially shipped, partially still open)
- **Watch-based incremental reindex (inotify / `ReadDirectoryChangesW`): NOT implemented.**
  Grepping `agentic/fsconnect/indexer.py` for `inotify`/`watchdog`/`ReadDirectoryChangesW`
  finds nothing. What *does* exist now (not present when the doc was written) is a
  size+mtime **skip-cache**, gated by `fsconnect.index_incremental` (`config.yaml:616`,
  default `false`): `FsIndexer._cache_probe`/`_load_cache`/`_save_cache`
  (`indexer.py:138-184`) let a re-run of `index --apply` skip re-reading/re-staging
  unchanged files and prune staged copies whose source disappeared
  (`_prune_staging`, ownership-bounded to files CyClaw itself staged). This closes the
  "instead of full restage" half of the ask but is still poll-driven (operator or cron
  re-invokes `cli.py:cmd_index`), not event-driven — no filesystem watcher process exists.
- **Content de-duplication by sha256: NOT implemented as described.** A per-file
  sha256 (`indexer.py:112`) is computed and stored, but only to detect a given file's
  own content changing across runs (staleness check for the skip-cache), not to
  detect duplicate content across *different* paths. Cross-file dedup remains open.
- **Local OCR for scanned PDFs/images: NOT implemented.** No `ocr`/`tesseract`
  reference anywhere in `agentic/fsconnect/`.
- **Quarantine of injection-flagged content: NOT implemented — confirmed advisory-only,
  matching the doc's own caveat.** `indexer.py:14` states this explicitly ("advisory-
  injection-scanned during staging (flag surfaced; roadmap: quarantine)").
  `FsIndexer._scan` (`indexer.py:55-59`) counts sanitizer-pattern hits per file and the
  count rides along as `injection_flag_count` in the manifest, the skip-cache, and
  (separately, on the write path) `agentic/fsconnect/client.py:136` /
  `agentic/fsconnect/writer.py:252,424,477,504` — but nothing anywhere reads that count
  to hold, reroute, or refuse a file. A file with a nonzero count is staged and
  reindexed exactly like a clean one. The quarantine mechanism itself remains to be built.

### FS Phase 4 — Windows hardening (more nuanced than the doc states; partially shipped)
- **`GetFinalPathNameByHandle` re-assertion: already wired for reads, per
  `agentic/fsconnect/pathsafe.py`'s own module docstring** ("File opens additionally
  re-assert containment via `GetFinalPathNameByHandle` on the open handle when
  available," `pathsafe.py:26-28`) — this is ahead of where the roadmap doc places it
  ("needs a Windows CI lane," implying not yet wired for reads).
- **Windows writes: NOT enabled at all — a stronger, undocumented-by-the-roadmap
  mitigation is in place instead.** `tests/test_fsconnect_writer.py:422-442`
  (`test_windows_writes_hard_refused`) confirms every write op is hard-refused on
  Windows unconditionally ("Until handle-based containment lands, writes are
  HARD-refused on Windows... even when every other gate passes"), because the
  name-validate-then-write-by-name fallback has a TOCTOU window a junction can exploit.
  This is a stricter stance than the doc anticipates — it isn't "hardening still to
  design," it's "writes disabled until the hardening lands."
- **Windows CI lane: partially present, not what the doc asks for.** `ci.yml`'s main
  `test` job already runs the full suite on `windows-latest` (`ci.yml:164`), so a
  Windows runner exists in CI. But the adversarial fixture matrix that would exercise
  the `# pragma: no cover` branches (20 of them in `pathsafe.py`, covering junction/UNC/
  8.3/ADS-style attacks) is `tests/test_fsconnect_pathsafe.py`, and that entire module
  is skipped when running on Windows itself (`pytestmark = pytest.mark.skipif(os.name
  == "nt", ...)`, line 17) — it validates the POSIX openat/`O_NOFOLLOW` authority path
  only. No Windows-specific junction/UNC/8.3/ADS fixture module exists elsewhere in
  `tests/`. The doc's ask ("a Windows CI runner so the `# pragma: no cover` branches
  are exercised") is still open even though a Windows CI leg now runs.
- **Per-root NTFS ACL inspection in `fs_stat`: NOT implemented.** No `ntfs`/`acl`
  reference in `agentic/fsconnect/`.

### Audit hardening (connector-wide) — one item shipped, one still open
- **Plain-language "rule applied" field: shipped, ahead of the doc.** Every audited
  fsconnect event carries `rule_applied` (`agentic/fsconnect/writer.py:28` states this
  as a design invariant; concrete emission sites at `writer.py:139,151,403,423,478,
  505,524,546,598,792`). The doc lists this as forward work; it is done.
- **Hash-chain / append-only tamper-evident audit: NOT implemented.** No
  chain/tamper/prev-hash construct in `utils/logger.py` or anywhere under
  `agentic/fsconnect/`. Still the RFP-disqualifier gap the doc names.

### SQL Phase 2 — usability behind the read-only guard (mostly still open)
- **Live-DB integration tests (Postgres + MSSQL) in a dedicated CI service container:
  still open for sqlconnect specifically**, though the *pattern* now exists elsewhere.
  `ci.yml` has a `postgres-backend` job (`ci.yml:424-487`) with a real
  `pgvector/pgvector:pg16` service container, but it runs only
  `test_personality_postgres.py`, `test_ratelimit_postgres.py`, and
  `test_pgvector_store.py` — no sqlconnect test file is wired into it, and there is no
  MSSQL service container anywhere in `ci.yml`. `agentic/sqlconnect/client.py:8-10`
  still marks `_execute` (`client.py:471`) and one helper class (`client.py:551`)
  `# pragma: no cover` for exactly this reason — the connect/execute paths need a live
  DB the CI doesn't provide for this connector.
- **Schema-aware NL→SQL helper: NOT implemented.** No `nl2sql`/text-to-SQL reference
  anywhere in `agentic/` or `docs/`.
- **More dialects (MySQL/MariaDB, Oracle, SQLite): NOT implemented.**
  `agentic/sqlconnect/config.py:17` hardcodes `VALID_DRIVERS = ("postgres", "mssql")` —
  still exactly the two dialects the doc describes as the v0.1 baseline.
- **Per-query cost caps: NOT implemented.** No cost-cap construct found.
- **EXPLAIN pre-checks: partially shipped, Postgres-only.** `SqlConnectClient.explain`
  (`client.py:521-539`) already exists and runs a plain (non-`ANALYZE`) `EXPLAIN` before
  execution — but it explicitly refuses the `mssql` driver
  (`client.py:531-535`, "explain is not supported for the mssql driver"), so this is
  half-done, not absent, and the doc doesn't mention it exists at all for Postgres.
- **Row-level PII redaction reusing `policy.privacy`: NOT implemented.** No reference
  to `policy.privacy` or PII redaction anywhere under `agentic/sqlconnect/`.

### OS-level agentic integration (Windows & Linux) — verified against the doc's own staging
1. **`agentic.fsconnect.cli reveal`: confirmed shipped**, matching the doc. `reveal()`
   lives in `agentic/fsconnect/osutil.py:59`, dispatches to `explorer`/`xdg-open`
   (`osutil.py:26,29`), and is wired as `cli.py`'s `reveal` subcommand
   (`cli.py:283-295,416-418`).
- **terminal.html "Open file share" button: confirmed still not built**, matching the
   doc's own "sketched here, not built" framing — no "file share"/`reveal` string
   appears in `static/terminal.html`, and no request-path endpoint for it exists in
   `gate.py`/`gate_ops.py` beyond the four already-documented `/ops/*` shims.
3. **Read-only system inventory (`agentic/osconnect/`): confirmed NOT started.**
   `agentic/osconnect/` does not exist anywhere in the tree — no directory, no file
   matching `*osconnect*` outside this roadmap doc itself. This item is exactly as
   undone as the doc states: a sketch, not a package.
4. **Governed OS actions (start/stop a service, allow-listed maintenance scripts):
   confirmed NOT started** — same basis (no `osconnect` package to hold them), and
   correctly the doc's own "later, high-risk" item.

### Other connectors worth building for this ICP (unstarted; unchanged from the doc)
- On-prem IMAP/Exchange (EWS), retrieval-only — no `agentic/imap*` or `agentic/ews*`
  package exists.
- On-prem SIEM (Splunk/Elastic), read-only — no corresponding package exists.
- Direct SMB/CIFS client (for shares not locally mountable) — no corresponding package
  exists; note `agentic/fsconnect/config.py` already has an `allow_unc_roots`/UNC-path
  concept for *mounted* UNC shares (referenced throughout `pathsafe.py`/`indexer.py`),
  which is a different thing from the direct-SMB-client idea sketched here — don't
  conflate the two when picking this up.

### When to extract a shared base (unchanged; still just a trigger condition, not a plan)
The doc's rule stands as stated and nothing currently contradicts it: once a third
connector (most likely IMAP or direct-SMB) joins `fsconnect`/`sqlconnect`, extract a
`connectors/base.py` ABC for the shared shape (disabled-default config, op allow-list,
audit, four-gate mutation, selftest, CLI exit-code contract) — as new-connector
scaffolding only, never retrofitted onto the existing `agentic/`/`sync/` modules. No
third connector exists yet, so this remains purely a trigger condition to watch for,
not an in-flight task.

## 6. Session/Sync Hygiene Notes

### `docs/SESSION_NOTES.md`

This file is confirmed to be an empty template scaffold, not a live log: it contains only the purpose statement, a `### Session: <YYYY-MM-DD>` markdown template block, and a placeholder line — `*None yet. Sessions from this template will be appended below.*` (`docs/SESSION_NOTES.md:49`) — with zero populated session entries. This matches `.claude/rules/PROJECT_RULES.md`'s own note that the active log lives under `.claude/session-notes/`, not here. Do not add session entries to this file; it stays as the blank template it already is. For session goals, decisions, blockers, discoveries, and coverage gaps, write to `.claude/session-notes/` instead — that is where CLAUDE.md §7 ("Blocked") and §10 ("End") both point.

### `docs/LOCAL_REMOTE_SYNC_GUARD.md`

Proposes a `SessionStart` hook, `.claude/hooks/session-start-sync-check.sh`, to close a recurring failure mode: local `main` drifting from `origin/main` (wrong-identity commits flagged "Unverified," ahead/behind divergence after remote merges, `git reset --hard` reached for as an ad-hoc — and destructive — fix). The hook is deliberately advisory only: on session start it (1) pins commit identity repo-locally to `noreply@anthropic.com`/`Claude`, (2) fetches the default branch read-only and reports ahead/behind counts, (3) prints reconciliation guidance (ff-only when safe, review `git log origin/main..HEAD` before discarding) without ever running reset/rebase/push/delete itself, and always exits 0 so it can never block a session. The doc also states the underlying operating conventions independent of the hook: treat local `main` as read-mostly, `git fetch && git merge --ff-only` after a remote merge rather than reset, treat `git reset --hard` as a last resort with a preservation branch first, and rely on pinned identity for verifiable commits.

**Current wiring status: the script sits on disk unregistered — it is not wired.** The file exists at `/home/user/CyClaw/.claude/hooks/session-start-sync-check.sh`, but `.claude/settings.json`'s `hooks.SessionStart` array contains exactly one entry — the `python-coding-agent` `SKILL.md` context loader (`.claude/settings.json:14-25`) — with no reference to `session-start-sync-check.sh` anywhere in the file, and no matching entries in `permissions.allow` beyond the git read-only commands already granted for other reasons (`git status`, `git diff`, `git log`, `git show`, `git branch`, `git fetch`). The doc's own "How to enable (opt-in)" section is accurate as written: shipping an active hook registration was left to a human decision, and that decision has not yet been made. Treat this proposal as still-open, not implemented — anyone acting on "sync guard is active" should be corrected.

## 7. Skill and Feature Proposals (Unbuilt)

### docs/zIdeas/PROPOSED_SKILLS.md — skill backlog

Five skills were proposed, ranked by leverage, with a self-reported status note (PROPOSED_SKILLS.md:7-16) claiming `invariant-guard`, `injection-redteam`, and `index-doctor` implemented, plus an unlisted fourth (`doc-sync`) added alongside them. Verified directly against `.claude/skills/` (not assumed from the doc):

| # | Skill | Doc's claimed status | Verified against `.claude/skills/` |
|---|---|---|---|
| 1 | `invariant-guard` | implemented | present — `.claude/skills/invariant-guard/` |
| 2 | `injection-redteam` | implemented | present — `.claude/skills/injection-redteam/` |
| 3 | `cve-triage` | open | **absent** — no `.claude/skills/cve-triage/` |
| 4 | `index-doctor` | implemented | present — `.claude/skills/index-doctor/` |
| 5 | `release-cut` | open | **absent** — no `.claude/skills/release-cut/` |
| — | `doc-sync` (not in original 5) | implemented | present — `.claude/skills/doc-sync/` |

Two proposals remain genuinely open, carried forward verbatim:

**`cve-triage`** (PROPOSED_SKILLS.md:60-76). Trigger: Dependabot/pip-audit opens a CVE PR, or "triage this CVE." Purpose: evaluate a new dependency CVE against CyClaw's existing risk-acceptance precedent (the chromadb CVE-2026-45829 call — embedded-mode-only, no HttpClient, telemetry killed, per CLAUDE.md's dependency notes) and produce a verdict — patch / pin / accept-with-rationale — drafting the `--ignore-vuln` entry plus justification comment in the established format when accepted. Rationale in the doc: the project already applies a rigorous, idiosyncratic CVE-acceptance style; a skill would make triage consistent and auditable instead of re-derived per incident.

**`release-cut`** (PROPOSED_SKILLS.md:94-104). Trigger: "cut a release" / "bump version." Purpose: bump `pyproject.toml`, update a changelog, sanity-check the four console entry points (`cyclaw-server`/`index`/`metrics`/`mcp`), run the reproducible-install gate, and tag. Doc's stop criteria: version bumped, entry points import-verified, install gate green.

Neither is a defect fix or hardening pass — both are net-new tooling. Under CLAUDE.md §1's FEATURE FREEZE (as of 2026-07-03), building either requires passing the explicit test first ("does this polish the portfolio signal or fix a real defect?"), not treating this backlog entry alone as authorization.

### docs/zIdeas/online-llm-grok-claude.md — web UI toggle for online fallback

The doc is largely a now-superseded how-to: its own verification summary (online-llm-grok-claude.md:76-93) describes wiring — hybrid-mode gating, `is_available()` checks, shared redaction across Grok/Claude clients — that matches current `gate.py`/`graph.py`/`llm/client.py` behavior and is no longer a proposal. One idea in it is still open and unbuilt:

**Web UI toggle for `grok.enabled`/`claude.enabled`** (online-llm-grok-claude.md:146-160). Proposal: expose the two `config.yaml` booleans (and possibly a per-query provider preference) as runtime toggles inside `static/terminal.html`, instead of requiring a config edit + server restart. On toggle, log the change to `audit.jsonl` with a `human_reason` field, explicitly described in the doc as "soul-like governance on settings."

This is not a quiet UI addition — it reaches directly into Invariant I3 (triple-gated external fallback, CLAUDE.md §3, PROJECT_RULES.md). `<provider>.enabled` is one of the three simultaneous gate conditions, and today it is fixed at process start from `config.yaml`, CyClaw's single source of truth for tunables (CLAUDE.md §1). Turning it into a live, UI-mutable value raises design questions the source doc only gestures at:
- `GET /`/`/static/*` are unauthenticated routes (CLAUDE.md's route table); a toggle reachable from that surface would need its own auth (reusing the existing API-key gate, per the `/soul*` endpoints, is the obvious precedent) rather than being open to any loopback client.
- it needs an audit trail with a human-supplied reason, analogous to I5's soul-governance requirement (`utils/personality.py` `apply_evolution`) — not just a silent config mutation.
- it must not create a second source of truth alongside `config.yaml`. The doc itself is unresolved on persistence (suggesting either an in-memory override, a new `user_prefs.json`, or soul-metadata storage) without settling which one is canonical or how it reconciles with `config.yaml` after a restart.

Per CLAUDE.md §7, this sits in the High tier (editing something adjacent to a security invariant) — it needs a design pass and explicit user sign-off before code, not a default "safe convenience layer" framing as the source doc suggests at online-llm-grok-claude.md:157.

**Stale reference flagged:** the doc pins `grok.model: "grok-4.3"` (online-llm-grok-claude.md:20,39). Current `config.yaml:87` ships `grok-4.5` as `models.grok.model` (`grok-4.3` is noted only as a still-resolvable prior default in that line's own comment, kept available if cost or the larger context window matters more than currency). The doc's `claude.model: "claude-sonnet-5"` (online-llm-grok-claude.md:26,45) does still match `config.yaml:98`. Per CLAUDE.md §1, code/config is the source of truth — treat the grok model string in this doc as stale.

### docs/PSYCLAW_FEATURE_IDEAS.md — vertical-market hypotheses

The doc's own framing governs everything in it: the named verticals (law, psychology/therapy, medical/dental, accounting) are "plausible, not validated... until buyer conversations prove urgency, budget, procurement path, and trust requirements" (PSYCLAW_FEATURE_IDEAS.md:14-16), and its business-status note states net-new features stay frozen absent a customer, paid pilot, or interview trigger (lines 27-29). Three items are catalogued; one is already built, two remain hypotheses:

1. **Audit/compliance summary endpoint** — status: repo-backed, already implemented (`GET /audit/summary`, API-key-gated, aggregate-only, per CLAUDE.md's route table). The doc's still-open piece is a *portable evidence export* — hash chains, PDF/CSV output, retention controls, external anchors — explicitly gated (lines 54-57) behind a separate design review and confirmed buyer/auditor demand, not something FEATURE FREEZE should wave through.
2. **Retention and right-to-erasure tooling** — status: hypothesis. A controlled purge for audit/interaction records past a retention window. Build trigger per the doc (lines 74-75): a paid pilot or discovery call naming retention as a purchase blocker. Security constraints stated (lines 70-72): any deletion path must be explicit, auditable, fail-closed, non-autonomous, and must not corrupt the append-only audit log or create a new plaintext persistence path.
3. **Matter/client tagging and conflict-wall checks** — status: hypothesis, law-firm-specific. Optional matter/client tags flowing into audit reporting as a tag dimension, plus a local conflict-wall check. Build trigger (lines 90-91): discovery proving matter-level reporting beats generic audit summaries and that buyers will pay for it. Flagged risk: tags are sensitive metadata; a safe design avoids plaintext client-name storage and keeps checks local.

None of the three should be built speculatively right now. CyClaw is in FEATURE FREEZE as of 2026-07-03 (CLAUDE.md §1), and the source doc's own closing principle says the same thing independently (lines 95-98): don't turn market pressure into product claims — commercial work starts with buyer discovery, attorney/IP review, and constrained paid pilots, not feature expansion ahead of demonstrated demand.

## 8. NeMo Phase 3 Redirect Status

Source: `docs/NeMo/phase3_implementation_plan.md` (dated 2026-07-27; redirects Phase 3 away from a `graph.py` output-rail node and toward consolidating the injection scanner already triplicated across `agentic/`). The doc split the redirected scope into three sub-items, 3A/3B/3C. Current status, verified against the tree rather than the doc's own claims:

| Item | What it proposed | Status |
|---|---|---|
| **3A** | Give `guardrails/rails.py` a real scanner (`OWASP_INJECTION_PATTERNS ∪ policy.prompt_filter.banned_patterns`, 37 patterns after dedup), replacing its 7 hardcoded substring markers; have `agentic/registry.py`, `agentic/fsconnect/client.py`, and `agentic/harness_optimizer/governance.py` import it instead of each rebuilding its own compile-and-scan layer. | **Shipped.** `guardrails/rails.py:29` imports `OWASP_INJECTION_PATTERNS` from `utils/personality.py` (the "import, don't move" branch of the doc's own I5 open question — resolved that way, not decided here). All three consumers now import from `guardrails.rails`: `agentic/registry.py:36-40`, `agentic/fsconnect/client.py:22-25` (`build_injection_patterns`/`scan_injection_patterns`), `agentic/harness_optimizer/governance.py:12` (`build_injection_pattern_sources`/`compile_injection_patterns`). No module rebuilds its own pattern union anymore. |
| **3B** | Decide whether the PowerShell harness needs its own pre-flight scanner. The doc resolved this 2026-07-27 as "no code needed" because `harness/server.py` at the time had exactly one call into `agentic/` (`run_agentic_op("status")`, hardcoded, zero caller-controlled args) and no free-text path reached any write-capable operation. | **The doc's own named trigger has fired — needs re-evaluation, not closed.** See below. |
| **3C** | Decide whether `agentic/fsconnect/client.py`'s injection scanner should be promoted from advisory to enforcing — flagged explicitly as an operator risk decision, out of scope for the 3A consolidation PR. | **Still open, unchanged.** `config.yaml:583` ships `scan_content: true` (flagging only) and `config.yaml:592` ships `block_on_injection_flags: false` — a flagged write still proceeds. No decision has been made since the doc was written. |

### 3B: the resolution's own revisit condition has since fired

The doc closed 3B on the explicit condition that there was "no code path by which a chat message or model output reaches `propose-skill`, `apply-skill`, fsconnect, or sqlconnect," and named its own falsification trigger: *"If the harness console is ever wired to expose those write operations directly (rather than only `status`), revisit this finding"* (`docs/NeMo/phase3_implementation_plan.md:113-114`).

That condition has fired. `harness/server.py` now ships `POST /api/agent/run` (`harness/server.py:617`, `dependencies=guarded`) plus `/api/agent/runs/{run_id}/decision`, `/push`, `/publish`, and `/discard` (`harness/server.py:667,685,706,725`) — all reaching `agentic.cli`'s `real-repo-run`/`real-repo-run-decide` family via `run_agentic_op`, exactly the write-capable operations 3B's premise said did not yet have a caller.

Verified directly rather than inferred from either doc:

- `harness/schemas.py:53-69` — `AgentRunRequest.instruction` is `Field(min_length=1, max_length=_MAX_INSTRUCTION_LEN)`. That is a length cap, not a content scan. No `pattern=`, no validator, nothing from `guardrails.rails`.
- `agentic/real_repo_loop.py:607-608` — the only check `run_real_repo_loop` applies to `instruction` is `not isinstance(instruction, str) or not instruction.strip()` (non-empty), raised as a bare `AgenticError`. No call to `inspect_candidate_text` or any `guardrails.rails` scanner touches the instruction text anywhere between the HTTP body and the planner prompt (`agentic/cli.py:602`, `agentic/real_repo_loop.py:632`).
- By contrast, GitHub-fetched context (PR/issue title, body, diff) genuinely is scanned: `agentic/cli.py:542` (`cmd_real_repo_run`) and `:253` (`cmd_deepagent_plan`) both call `_blocking_context_findings(bundle)` before that text reaches a model, gating on the finding *code* (not a severity string no producer sets — the doc's own comment at `agentic/cli.py:351-369` explains why the severity-based version silently never fired). A blocking finding refuses the run outright (`EXIT_FAIL`).

So the precise gap: **the operator-authored `instruction` field that reaches `/api/agent/run` has no injection/governance scan applied to it at all — only a length cap — while the GitHub-sourced context folded into the same prompt does get scanned.** This is an asymmetry in the opposite direction from Finding 1's original one (there, the low-risk `/query` path was double-guarded and the high-risk paths were under-guarded; here, the instruction field itself is the unscanned one and the third-party context alongside it is the guarded one).

This does not mean the `/api/agent/run` path is ungoverned end-to-end — downstream mitigations still apply and are real:
- Every proposed file the planner writes is scanned via `inspect_candidate_text` **before** any write lands (`agentic/real_repo_loop.py:668-670`), and a critical finding quarantines the whole iteration.
- A diff-scope gate rejects candidates that touch protected paths or exceed a write-size budget, computed before any write (`agentic/real_repo_loop.py:672-678`).
- Verification (caller-declared checks run in the jailed `agentic/executor` sandbox) must pass.
- Nothing commits without an explicit human approve/reject via `real-repo-run-decide` / `POST /api/agent/runs/{run_id}/decision` (`agentic/cli.py:831`, `harness/server.py:667`).

So an instruction carrying an injection payload is not scanned as input, but the planner's *output* — the only thing that can actually mutate the repo — is scanned, scope-gated, verified, and still requires a human decision. Whether that chain is sufficient, or whether the instruction field itself should also route through `guardrails.rails` (now trivial to wire, since 3A already put the shared scanner one import away), is a call for the prompt/harness owner to make explicitly — flagging it here as an open item this redirect surfaced, not as something already decided or fixed.

### Adjacent: the `graph.py` KNOWN GAP is dormant, not resolved

The doc's "one query-path item that still stands on its own" (deferred from the Phase 3 redirect, not cancelled) points at a comment in `graph.py` that is still present verbatim:

```
# user_gate → grok_fallback | offline_best_effort | audit_logger (conditional)
# KNOWN GAP: offline_best_effort bypasses guardrail_input — the offline
# input rail covers only the high-score route above. A low-score or
# declined-escalation injection-pattern query reaches the local LLM
# un-railed while a high-score twin is blocked. Closing this needs
# guardrail_router to learn the offline target — a maintainer design
# decision, flagged in the linked PR.
```
(`graph.py:849-855`, immediately above the `user_gate` conditional-edges block.)

This gap is currently **dormant, not active**: `config.yaml:652` ships `guardrails.enabled: false`, which makes the `guardrail_input` node a pure pass-through for every route (per `config.yaml:651-655`'s own comment) — so today neither the high-score nor the low-score path is actually railed, and the asymmetry the comment describes has no live effect. It becomes a real, exploitable asymmetry only if an operator flips `guardrails.enabled: true` without also closing this gap; it remains a graph-edge change (High tier, `CLAUDE.md` §7) and is explicitly out of scope for the 3A consolidation work.

## 9. Still-Open Ideas — Index

Every idea below came from one of the 17 archived source files and is still
NOT_IMPLEMENTED or only PARTIALLY_IMPLEMENTED as of the 2026-08-01 audit that
produced this document. This is a scannable index, not a duplicate of the
full reasoning — each row points at the section above with the complete
citation-backed detail. **This section exists specifically so a future
cleanup pass cannot silently drop one of these by not recognizing it as still
open; do not prune a row without re-verifying its status against the live
code first**, the same discipline every row here was produced with.

CyClaw is in FEATURE FREEZE as of 2026-07-03 (`CLAUDE.md` §1): polish,
hardening, and bugfixes pass the bar; new capabilities need explicit
justification before design work starts. The Freeze column below reflects
that test, not a judgment on the idea's merit.

| Idea | Status | Freeze | Detail |
|---|---|---|---|
| Future shell/host-FS/GitHub-mutation executor for the DeepAgents graph harness | NOT_IMPLEMENTED | retired path — do not build | §2a |
| Separate "approval-decorator" abstraction | NOT_IMPLEMENTED | no driving need — skip | §2a |
| `agentic/deepagent_github/memory.py:11`'s hardcoded `_MAX_MEMORY_BYTES = 64_000` → `config.yaml` | NOT_IMPLEMENTED | passes as config-hygiene, low risk even on a dead path | §2c |
| Corpus-source allowlist for cloud egress (I3 hybrid-mode hardening) | NOT_IMPLEMENTED | real security control on a **live** path, independent of the retired harness — worth explicit prioritization | §2c |
| R4 `decision_fingerprint` provenance binding on `HarnessApplicationProposal` | NOT_IMPLEMENTED | retired path — low priority unless revived | §2c |
| R2 live-runtime smoke test against a real constructed Deep Agents graph | NOT_IMPLEMENTED | retired path — low priority | §2c |
| Three-tier memory model / richer subagent memory | NOT_IMPLEMENTED | retired path, "research only" — skip | §2c |
| Persistent (SQLite/Postgres) checkpointer replacing `InMemorySaver` | NOT_IMPLEMENTED | retired path, doc says in-memory is fine — no action | §2c |
| Route C: in-process PyGithub/SDK instead of the `gh` CLI | NOT_IMPLEMENTED (deliberately deferred) | do not build — `gh` subprocess keeps the SCA surface flat | §3 |
| Surface registry skills to operator tooling (still read-only at runtime) | NOT_IMPLEMENTED as originally specified | open, low priority | §3 |
| Dependency currency bumps — Tier 1 (`ruff`, `mypy`) | NOT_IMPLEMENTED | passes cleanly — good small PR | §4 |
| Dependency currency bumps — Tier 2 (`langgraph`, `langchain`, `langchain-openai`, `psycopg`+`psycopg-binary`, `pgvector`) | NOT_IMPLEMENTED | passes — needs the per-package verification steps in §4 | §4 |
| Dependency currency bumps — Tier 3 (`fastapi`, `uvicorn`, `langchain-core`) | NOT_IMPLEMENTED | passes — changelog-check first | §4 |
| `websockets` 15.x→16.x (major) | NOT_IMPLEMENTED | blocked on a `langgraph-sdk` compatibility check | §4 |
| FS Phase 3: watch-based incremental reindex (inotify/`ReadDirectoryChangesW`) | NOT_IMPLEMENTED (skip-cache shipped instead) | real efficiency win — reasonable polish candidate | §5 |
| FS Phase 3: cross-file content dedup by sha256 | NOT_IMPLEMENTED | minor — low priority | §5 |
| FS Phase 3: local OCR for scanned PDFs/images | NOT_IMPLEMENTED | new capability, new dependency — needs explicit justification | §5 |
| FS Phase 3: quarantine of injection-flagged content (currently advisory-only) | NOT_IMPLEMENTED | meaningful security hardening — good candidate | §5 |
| FS Phase 4: adversarial Windows CI fixture matrix for `pathsafe.py`'s `# pragma: no cover` branches | NOT_IMPLEMENTED (a Windows CI leg runs, but skips this module) | worthwhile test-coverage hardening if Windows writes are ever pursued | §5 |
| FS Phase 4: per-root NTFS ACL inspection in `fs_stat` | NOT_IMPLEMENTED | new capability, Windows-specific, speculative — skip absent a driving need | §5 |
| Hash-chained / append-only tamper-evident audit log | NOT_IMPLEMENTED | real security hardening for a live write path — worth doing | §5 |
| SQL Phase 2: live-DB CI integration tests (a sqlconnect-specific job; Postgres pattern already exists elsewhere, MSSQL has none) | NOT_IMPLEMENTED | solid test-coverage hardening for an already-shipped connector | §5 |
| SQL Phase 2: schema-aware NL→SQL helper | NOT_IMPLEMENTED | clearly new capability — hold for explicit need | §5 |
| SQL Phase 2: more dialects (MySQL/MariaDB, Oracle, SQLite) | NOT_IMPLEMENTED | new capability — hold | §5 |
| SQL Phase 2: per-query cost caps | NOT_IMPLEMENTED | new capability — hold | §5 |
| SQL Phase 2: EXPLAIN pre-check for the `mssql` driver (Postgres already works) | PARTIALLY_IMPLEMENTED | reasonable small hardening follow-up | §5 |
| SQL Phase 2: row-level PII redaction reusing `policy.privacy` | NOT_IMPLEMENTED | security-relevant hardening of an existing feature — reasonable candidate | §5 |
| `static/terminal.html` "Open file share" button | NOT_IMPLEMENTED | new UI + new gate.py endpoint — needs justification | §5 |
| `agentic/osconnect/` (read-only OS inventory) | NOT_IMPLEMENTED | brand-new connector — squarely blocked by freeze | §5 |
| Governed OS actions (service start/stop, allow-listed maintenance scripts) | NOT_IMPLEMENTED | new capability, higher risk (system mutation) — ask first | §5 |
| IMAP/EWS, SIEM, direct-SMB connectors | NOT_IMPLEMENTED | new capability, large surface — hold | §5 |
| Shared `connectors/base.py` ABC | NOT_IMPLEMENTED | premature — trigger condition (a third connector) hasn't happened; skip per YAGNI | §5 |
| Wire `session-start-sync-check.sh` into `.claude/settings.json`'s `SessionStart` hooks | PARTIALLY_IMPLEMENTED (script exists, unregistered) | low-risk, advisory-only — good small session-hygiene PR | §6 |
| `cve-triage` skill | NOT_IMPLEMENTED | net-new tooling, but fits an existing pattern (`dep-guard`/`verify-deps`) — reasonable low-risk candidate | §7 |
| `release-cut` skill | NOT_IMPLEMENTED | net-new tooling — reasonable if releases are cut with any regularity | §7 |
| Web-UI toggle for `grok.enabled`/`claude.enabled` in `terminal.html` | NOT_IMPLEMENTED | touches Invariant I3 — needs a real design pass and explicit sign-off, not a quiet addition | §7 |
| Portable evidence export (hash chains, PDF/CSV, retention, external anchors) | NOT_IMPLEMENTED | hypothesis-stage, needs buyer-driven justification — do not build speculatively | §7 |
| Retention / right-to-erasure tooling for audit records | NOT_IMPLEMENTED | hypothesis-stage — same call | §7 |
| Matter/client tagging + conflict-wall checks | NOT_IMPLEMENTED | law-firm-specific hypothesis — hold | §7 |
| 3B: harness-side pre-flight scanner for the `instruction` field on `POST /api/agent/run` | NOT_IMPLEMENTED — **the doc's own revisit trigger has fired** | real, currently-unaddressed gap in a now-live path — flag for the prompt/harness owner, not a quiet fix | §8 |
| 3C: promote `agentic/fsconnect/client.py`'s injection scanner from advisory (`block_on_injection_flags: false`) to enforcing | NOT_IMPLEMENTED, still an open operator decision | deliberate decision needed, not a default flip | §8 |
| `graph.py` KNOWN GAP: `offline_best_effort` bypasses `guardrail_input` | NOT_IMPLEMENTED, dormant (`guardrails.enabled: false`) | real, code-acknowledged gap; needs a maintainer decision on `guardrail_router` before guardrails ever ship enabled — graph-edge change, High tier | §8 |
