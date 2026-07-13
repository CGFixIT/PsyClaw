# CyClaw LangChain Deep Agentic Harness — Status and Roadmap

Date: 2026-07-11. Status: living status/roadmap record for the out-of-band
LangChain Deep Agents harness (`agentic/deepagent_github/` +
`agentic/harness_optimizer/`).

## Purpose and provenance

This document records where the CyClaw agentic harness stands as of
2026-07-11, the owner decisions that shape phases 6-9 and beyond, a review
checklist against draft PR #515 (the phases 6-9 implementation of record), a
source-verified reference for the real `deepagents` package API, the design
for Grok/Claude provider parity inside the harness, the sanitized
context-handoff pipeline, and a compliance mapping for regulated customers
(HIPAA, legal privilege, ITAR/CUI, SOC 2 / ISO 27001).

Authority relationships (deliberate, to avoid drift):

- `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md` remains the
  canonical **design plan** for the two harness features. This document does
  not restate its design authority; it records **status, decisions, review
  findings, and roadmap** on top of it.
- `docs/agentic/DEEP_AGENT_HARNESS_PHASES_6_9.md` (added by draft PR #515)
  becomes the authoritative **implemented-controls** description once #515
  merges.
- `config.yaml` owns every tunable number cited here. Where a number appears
  below, it is sourced from `config.yaml`, `pyproject.toml`, or the named PR
  diff — never invented.
- Produced from: a full read of the phases 0-5 code on `main` @ `8c78f3f`, a
  file-by-file review of draft PR #515 (branch
  `agent/deepagent-harness-phases-6-9` @ `c362d39`), a consolidation
  inventory of the three root LangChain planning docs, a source-level
  verification of `deepagents` 0.6.12 (PyPI + langchain-ai/deepagents +
  langchain-ai/docs repos), and four owner decisions recorded 2026-07-11.

## Owner decisions recorded 2026-07-11

These four decisions were made explicitly by the project owner on 2026-07-11
and govern the roadmap below. They supersede any earlier "not planned"
statements in this repo's planning docs.

1. **Grok/Claude scope: full provider parity.** Grok and Claude become
   selectable coding-loop providers for the Deep Agents harness, behind the
   same gate discipline as the core graph's triple-gated fallback (I3). This
   is the largest threat-model shift in the roadmap and carries a dedicated
   security-review section (see "Provider parity design" below). Owner
   context, verbatim intent: the cloud apps for both AIs already exist in
   their own clouds; CyClaw needs them (a) to verify they actually work end
   to end, and (b) to understand and sanitize prompt context handoff well
   enough to legitimately secure it and explain it to highly regulated
   potential customers (legal, medical, manufacturing).
2. **Compliance framing: all four frameworks.** The sanitized-handoff and
   compliance section addresses HIPAA (medical), attorney-client privilege /
   ABA Model Rule 1.6 (legal), ITAR/CUI/trade secrets (manufacturing), and
   SOC 2 / ISO 27001 (general attestation) — not a subset.
3. **PR #515 is the authoritative in-flight implementation of phases 6-9.**
   Future work reviews and iterates on #515 rather than re-implementing. The
   review checklist below is the concrete artifact for that.
4. **Docs consolidation executes directly on `main`, absorbing PR #501.**
   The root LangChain planning docs are consolidated into the canonical plan
   doc; #501's reconciliation content is folded in and #501 closed as
   superseded. Canonical-doc edits are placed to avoid #515's diff regions,
   with one disclosed trivial exception (see "Docs consolidation record").

---

The five sections that follow are the 2026-07-11 baseline analysis, preserved
as written (it was produced against `main` @ `8c78f3f` before the PR #515
deep review), then elaborated. Where the deep review or an owner decision
changed a conclusion, a dated **[Status 2026-07-11]** annotation follows the
original text rather than silently rewriting it.

## Part 1: Is Your Mental Model Correct?

**Yes, completely.** Your framing of "GitHub connection via LM Studio + MCP +
Qwen cached model" is architecturally sound and matches the repo's intent:

```
┌─────────────────────────────────────────────────────────────┐
│ User Query to CyClaw (gate.py / graph.py)                   │
│ → RAG retrieval (ChromaDB + BM25)                           │
│ → Local LM Studio (Qwen 2.5 7B)                             │
│ → Optional external fallback (Grok/Claude, triple-gated)    │
└─────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────┐
│ Autonomous Agentic GitHub Task (NEW, out-of-band)           │
│ deepagent_github + harness_optimizer                        │
│ → Local LM Studio (same Qwen model)                         │
│ → MCP tools (scoped proposer workspace only)                │
│ → No writes to real repo by default                         │
│ → Zero coupling to core gate/graph path                     │
└─────────────────────────────────────────────────────────────┘
```

The architecture **does not** route through your existing gate/graph/mcp/rag
infrastructure — that's intentional. `agentic/` is out-of-band via
`python -m agentic.cli ...`. This preserves the six security invariants
(particularly I6: module isolation) and allows the agentic layer to be
entirely disabled without touching the core.

**[Status 2026-07-11] Elaboration.** Three precision points on the diagram:

- "MCP tools" in the second box is today a *boundary contract*, not a running
  MCP server: `agentic/harness_optimizer/mcp/tools.py` implements
  `ProposerWorkspaceTools` as plain audited Python callables that enforce the
  same allowlist a future MCP server must expose (no shell, no GitHub writes,
  no holdout reads, writes only under `current/` or via `finish_proposal`).
  The retrieval-only `mcp_hybrid_server.py` remains a separate surface and
  never grows agentic tools.
- The "same Qwen model" line is literal: PR #515's builder constructs
  `ChatOpenAI(model=settings.model, base_url=settings.base_url, ...)` against
  the LM Studio endpoint from `agentic.deepagent_github.base_url`
  (`http://localhost:1234/v1` in `config.yaml`), fully independent of
  `llm/client.py`'s `LocalLLMClient` used by the core graph. Two planes, one
  model server.
- One honest capability caveat, source-verified: `deepagents` 0.6.12's own
  suggested-model guidance starts at frontier/open-weight models well above
  7B. Whether `qwen2.5-7b-instruct` reliably drives the deep-agent loop
  (todo/task/filesystem tool-calling) is **unverified** — treat the first
  live phase-6 runs as an experiment, and expect that harness quality may be
  the first real argument for the larger open-weight models discussed in the
  model-swap notes (Qwen3.7-Max et al.).

## Part 2: Is LangChain Deep Agents Redundant vs. Your Existing Stack?

**No. They are complementary, not competitive.** Here's the distinction:

| Concern | gate/graph/mcp/rag | deepagent_github | Overlap? |
|---|---|---|---|
| **Primary use** | Answer user queries with retrieval + optional external LLM | Autonomous task completion (plan, propose diffs, review) | ✗ No |
| **Routing** | Deterministic graph topology (edges = policy) | LangChain Deep Agents (agent autonomy within guardrails) | ✗ No |
| **Tools** | Single tool: `hybrid_search` (retrieval-only) | Multiple (repo-context, file-write to proposer workspace, etc.) | ✗ No |
| **External LLM** | Triple-gated fallback (Grok/Claude on user confirmation) | Local-only by default (LM Studio Qwen) | ~ Separate configs |
| **Writes** | None | Proposals to local workspace only (no real-repo writes by default) | ✗ No |
| **Audit** | Central JSONL + redaction | Mirrored audit pattern (same `utils.logger.audit_log`) | ✓ Yes (intentional reuse) |
| **When disabled** | Core RAG/LLM path continues unchanged | Full agentic layer is a no-op | ✓ Yes (isolation) |

**Key insight:** The existing gate/graph/mcp/rag is **synchronous
query→answer**. The agentic layer is **asynchronous task→completion** (plan a
GitHub issue, propose a diff, select tests, draft a PR — all in a loop, human
confirming steps). They're solving orthogonal problems.

**[Status 2026-07-11] Elaboration.** Two additions to the table now that
PR #515 exists:

- The "External LLM" row changes under owner decision 1: the harness gains
  optional Grok/Claude providers behind an I3-equivalent gate chain (see
  "Provider parity design"). The *defaults* stay identical — local-only,
  everything false.
- A historical "alternative considered" is worth preserving: the root
  `LangChainFix.md` research recorded a **LangGraph-native** variant (a
  5-node review→plan→gate_write→execute→audit `StateGraph` under `agentic/`,
  zero new dependencies, every gate a single testable node, using
  `Annotated[list, operator.add]` reducers) and assessed it the better
  architectural fit *unless* many more agentic connectors were planned. The
  repo chose Deep Agents as the optional path; PR #515 validates that choice
  by keeping the dependency optional (`agentic-deepagents` extra) and the
  wiring policy-scoped. If the extra ever fails a dependency review, the
  LangGraph-native design remains the documented fallback. (Folded into the
  canonical plan doc as a dated section by this consolidation.)

## Part 3: Current Phase State + What Exists

Based on the code audit of phases 0-5 (now hardened in this session's PRs
#517–#519):

### Phases 0–5: IMPLEMENTED AND TESTED ✓

| Phase | Status | Key Artifacts |
|---|---|---|
| **Phase 0** | Planning doc (the 1055-line blueprint you read) | `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md` ✓ |
| **Phase 1** | Config + docs validated + tests green | `agentic/config.py` + `DeepAgentGitHubConfig` + `HarnessOptimizerConfig` ✓ |
| **Phase 2** | Harness optimizer data models + workspace builder | `Experiment`, `Surface`, `Variant`, `RunReport`, `CandidateDecision`, `ProposerWorkspace` ✓ |
| **Phase 3** | Mocked runner + acceptance gate (`decide_candidate()`) | `MockHarnessRunner`, `Scorecard`, scoring/governance modules ✓ |
| **Phase 4** | Local LM Studio proposer invocation + scoped tools | `ProposerWorkspaceTools`, MCP tool specs, `invoke_workspace_proposer()` ✓ |
| **Phase 5** | Optional deepagent_github skeleton + no writes | `builder.py`, `SubagentSpec` (8 subagents defined), `DeepAgentPermissionPolicy` ✓ |

### Phases 6–9: NOT STARTED (Planned, Not Yet Implemented)

| Phase | Planned | Current Blocker |
|---|---|---|
| **Phase 6** | Real subagent wiring; skills implementation; memory; interrupt_on config; prompts | `draft_plan()` raises `NotImplementedError` ✓ (defensive placeholder) |
| **Phase 7** | Real GitHub coding eval runner (`github_coding_runner.py`) | Module doesn't exist; fixtures not created |
| **Phase 8** | Governed propose/apply for improvements; registry/soul persistence | `patching.py` doesn't exist; governance gates not wired into apply |
| **Phase 9** | Security review before any real write execution | Documentation + regression test suite only; no code enabled |

### No phases beyond 9.

Phase 9 is the terminal gate: security review, then decides if real writes
ever get enabled (likely: never in the default config, always requiring
explicit separate review).

**[Correction 2026-07-11].** The "NOT STARTED" heading above is preserved as
written but was already stale when written: draft **PR #515**
(`agent/deepagent-harness-phases-6-9`, +1539/−147 across 25 files, all CI
green including a new `deepagents-harness` job) implements phases 6-9
end-to-end **on a branch**. The accurate statement is: *phases 6-9 are
implemented in draft PR #515 and not yet merged to `main`.* Per owner
decision 3, #515 is the implementation of record; the per-phase blockers
column above becomes the review checklist in the "PR #515 review" section of
this document. The "no phases beyond 9" conclusion stands — #515's own
phases doc frames phase 9 as "a security gate, not authorization to add an
executor," and the post-#515 roadmap (provider parity, live-runtime HITL
round-trip, checklist fixes) is tracked in this document rather than as new
numbered phases.
## Part 4: "Unwired" Scaffold (Phase 5 Status)

The phase-5 code includes structural placeholders that are **intentionally
NOT imported yet**:

1. **`draft_plan()` in `agentic/deepagent_github/runners.py`**
   Raises `NotImplementedError` (PR #499, this session) instead of returning
   hardcoded data.
   Caller: Phase 6/7 will wire real planning logic.

2. **Builder seam gap: bare subagent names + empty tools list**
   ```python
   agent = creator(
       model=settings.model,
       tools=[],  # ← empty
       subagents=[subagent.name for subagent in subagents],  # ← bare strings
   )
   ```
   Real Deep Agents API requires `tools` to be callables and `subagents` to
   be dicts with `name`/`description`/`system_prompt`/`model`/`tools`. This
   works with test mocks but would fail against the real `deepagents`
   package.
   **Phase 6 fix:** Construct validated `SubAgent` spec dicts and pass real
   tool callables; only set `created=True` after validation.

3. **`skills.py`, `memory.py`, `governance.py` (deepagent_github)**
   Stubs returning placeholder values. Phase 6 wires real implementations.

4. **No `prompts/` directories**
   Phase 6 adds: `system.md`, `planner.md`, `reviewer.md`,
   `test_selector.md`, `pr_writer.md`.

5. **No examples/ directories**
   Would hold fixture configs in TOML (phase 7+).

**[Status 2026-07-11].** Every numbered item above is resolved or deliberately
redirected by draft PR #515:

1. `draft_plan()` now returns a deterministic, task-specific **no-write**
   plan (validates `task_id`/`repo`/`instruction`, derives its source line
   from `pr_number`/`issue_number`, fixed 4-step plan, `proposed_tests`
   pointing at the two phase-6-9 test files, and a Summary/Validation/
   Security `pr_body` containing "no-write").
2. The builder seam gap is closed exactly as prescribed: materialized
   subagent dicts (`name`/`description`/`system_prompt`/`model`/`tools`),
   wired callable tools validated by set-equality against
   `default_tool_specs(policy)` names, `created=True` only after validation,
   per-subagent `interrupt_on` subsets, and a real-package test
   (`tests/test_agentic_deepagent_optional.py`) that constructs the actual
   Deep Agents graph.
3. `skills.py` gains `governed_skill_files(registry)` (applied registry
   entries → virtual `/skills/<name>/SKILL.md`, `json.dumps`-quoted
   frontmatter); `memory.py` gains `load_local_memory_files(repo_root)`
   (only `data/agentic/deepagent_github/AGENTS.md`, containment-checked,
   64,000-byte cap, never created by the agent). `deepagent_github/
   governance.py` remains a zero-caller shim — and now carries a semantic
   drift risk documented in the review checklist below.
4. **Redirected, not added:** phase 6 as implemented derives each subagent's
   `system_prompt` from `SubagentSpec` fields (a new `system_prompt`
   property) instead of adding a `prompts/` directory of Markdown files. The
   planned `prompts/*.md` layout from the canonical doc §6 is therefore
   superseded for subagents; a prompts directory remains a live option for
   the harness-optimizer's outer/judge prompts if phase-8+ tuning wants
   file-backed surfaces (they are already declared as `SurfaceType` members).
5. Still absent by design; the committed fixture repo
   (`tests/fixtures/github_coding_repo/`) covers phase 7's need without TOML
   examples.

## Part 5: Grok API Integration Feasibility

**Feasible, but requires separate review and isn't planned.** Here's why:

The plan doc **explicitly states:** "no Grok fallback reuse unless separately
reviewed" (section 8, "Model provider").

Current design:
- `deepagent_github` uses LM Studio only (config: `provider: "lmstudio"` or
  `"openai_compatible"`)
- `model_adapter.py` has a `DeepAgentModelSettings` dataclass that could be
  extended
- The three external LLM clients (`llm/client.py`: `LocalLLMClient`,
  `GrokClient`, `ClaudeClient`) are separate from agentic

**To add Grok to Deep Agents harness, you'd need:**

1. Add `grok` provider option to `DeepAgentGitHubConfig`
2. Wire `GrokClient` (from `llm/client.py`) into `DeepAgentModelSettings`
3. Add config flags for `agentic.deepagent_github.grok.enabled` +
   `agentic.deepagent_github.grok.api_key`
4. **Separate security review:** Does Grok have different risk profile than
   LM Studio? (Yes: network, rate limits, billing; LM Studio is local.)
   Would need explicit threat-model update.
5. Update tests to mock Grok responses
6. Update docs/plan

**Why it's NOT planned:** The optimizer and Deep Agents harness are designed
to work **offline-first** with cached local models (Qwen). Grok integration
would shift the threat model from "single operator, local inference" to
"cloud API calls" — that's a different security story. If you wanted it,
you'd be asking "should agentic tasks ever hit the network?" That requires a
separate design decision.

**TL;DR:** Technically feasible (~200 lines of config + adapter wiring), but
philosophically out-of-scope unless you decide agentic harness _should_ have
cloud fallbacks (which isn't the current design posture).

**[Decision 2026-07-11].** That separate design decision has now been made:
**owner decision 1 selects full provider parity** — Grok and Claude become
selectable coding-loop providers behind an I3-equivalent gate chain, with the
dedicated security review this section demanded. Three corrections to the
sketch above, from the phase 6-9 code as implemented in #515:

- Step 2 changes: the harness should **not** wire `llm/client.py`'s
  `GrokClient`/`ClaudeClient` (plain `generate(prompt) → str` chat clients)
  into Deep Agents — the harness needs LangChain `BaseChatModel` instances
  with tool-calling (`ChatXAI` / `ChatAnthropic`). What transfers from
  `llm/client.py` is the **discipline**, not the classes: env-var-only keys
  (`GROK_API_KEY`, `ANTHROPIC_API_KEY`), `is_available()` = key presence,
  type-only error messages (never echo bodies/URLs that can carry secrets),
  and bounded retry with no retry on 4xx.
- Step 3 changes: **no `api_key` field in `config.yaml`, ever** (repo rule:
  no secrets in config; keys are env-only). The per-provider config carries
  `enabled` and model naming only.
- The estimate holds: the provider-parity diff is small (~200 lines with
  tests). The security review — redaction parity, egress documentation,
  per-run confirmation — is the real work. Full design in "Provider parity
  design" below.

## Summary: Roadmap to Shipping

To ship a GitHub-connected coding agent that works via LM Studio + Qwen, you
need:

✓ **Done:** Phases 0–5 scaffold (config, workspace tools, mock runners,
permission gates)
⏳ **Next:** Phases 6–7 (subagent wiring, real planner/reviewer prompts,
GitHub eval runner with fixture repos)
⏳ **Then:** Phase 8 (governance persist: registry/soul integration for
accepted improvements)
⏳ **Finally:** Phase 9 (security regression suite, then decide: do real
writes ever get enabled?)

**Realistic shipping date for "can propose diffs + review them":**
Post-phase-7 (requires real GitHub runner + fixture repos, estimated ~2–3
weeks of careful phase 6/7 implementation).

**Critical path blockers:**
1. Phase 6: Real `draft_plan()` implementation (LangChain prompts +
   structured output)
2. Phase 7: Real `github_coding_runner.py` with deterministic scoring (not
   just local-model judge)
3. Phase 8: Proven governance gates (propose ≠ apply; human confirm required)

The three completed PRs (#517–#519, this session) closed small gaps in phases
0–5 (config contract test, holdout symlink security, README refresh). The
heavy lifting is phases 6–7.

**[Correction 2026-07-11].** The "~2–3 weeks" estimate is overtaken by
events: draft PR #515 implements all three critical-path blockers. The
revised critical path to a *usable* local coding harness is:

1. Review and merge #515 against the checklist below (days, not weeks).
2. `pip install ".[agentic-deepagents]" -c constraints.txt`, enable the
   nested flags, and run the first live LM Studio session — the first time
   the real graph is **invoked** (CI only constructs it; see checklist item
   R2).
3. Provider parity (owner decision 1) as a follow-up PR using the design in
   this document.

Note also one phase-8 scope narrowing #515 made deliberately, which the
original summary above did not anticipate: accepted candidates persist as
**versioned local JSON artifacts** under
`data/agentic/harness_optimizer/runs/accepted/` — *not* as registry/soul
writes. The existing `agentic.cli apply-skill` and `utils/personality.py`
paths remain the only governors for registry/soul mutation. "Registry/soul
persistence" as phase-8 scope is therefore **retired**, on purpose.

## PR #515 review: phases 6-9 implementation of record

Draft PR #515, branch `agent/deepagent-harness-phases-6-9`
(base `main` @ `8c78f3f`, head `c362d39`): 25 files, +1539/−147. All CI green
on the head sha, including the new `deepagents-harness` job and ruff. No
changes to `gate.py`, `graph.py`, `mcp_hybrid_server.py`, `requirements.txt`,
`CLAUDE.md`, or `sync/` — invariants I1-I6 structurally unaffected.

What it implements, per phase:

- **Phase 6 (Deep Agents wiring).** `build_deepagent_github()` gains gates in
  order: enabled → `refuse_unsupported_write_policy` (shell/GitHub-write
  still hard-refused, audited) → `allow_deepagents_dependency` → new
  `model_not_configured` and `workspace_required` early returns. Real path
  lazily imports `deepagents` + `langchain_openai`, builds
  `ChatOpenAI(model=…, base_url=…, api_key=os.getenv("DEEPAGENT_API_KEY",
  "not-needed"))`, denies ALL built-in filesystem tools via
  `FilesystemPermission(operations=["read","write"], paths=["/**"],
  mode="deny")` on a `StateBackend` (virtual, per-thread, no host FS, no
  `execute`), and wires exactly five audited CyClaw callables
  (`repo_context_read`, `local_repo_read`, `rag_search_readonly`, and — only
  when `allow_filesystem_write_tools: true` —
  `proposal_workspace_write_current`, `finish_proposal`). Subagents are
  materialized dicts with per-subagent tool subsets and `interrupt_on`
  restricted to their own tools; a subagent with zero wired tools is refused.
  Local memory: `data/agentic/deepagent_github/AGENTS.md` (64,000-byte cap)
  → virtual `/memory/AGENTS.md`. Skills: applied registry entries → virtual
  `/skills/<name>/SKILL.md`.
- **Phase 7 (fixture eval runner).**
  `agentic/harness_optimizer/runners/github_coding_runner.py` (260 lines):
  copies the committed fixture repo (`tests/fixtures/github_coding_repo/`) to
  a temp dir, overlays only declared candidate surfaces (undeclared file →
  `critical: unallowed_candidate_file`), checks proposal injection +
  visible-case hardcoding, scores per-case by deterministic
  `expected_text in file_text`, and returns the existing `RunReport` so
  `decide_candidate()` stays the sole acceptance gate. GitHub context comes
  only from the existing read-only `agentic.context` wrappers, caller-
  supplied; unit tests inject fakes, no network.
- **Phase 8 (governed candidate records).** New
  `agentic/harness_optimizer/patching.py`:
  `propose_candidate_application()` (accepted-decision + slug + injection
  gates, SHA-256, writes nothing) and `apply_candidate_artifact()` with the
  fail-closed chain: slug → non-empty text → `hmac.compare_digest` hash match
  → injection re-scan (OWASP ∪ `banned_patterns`) → `agentic.enabled` →
  `harness_optimizer.enabled` → `mode: write` → `writes_enabled: true` →
  **`require_human_confirm_for_accept` must remain `true`** (setting it false
  *blocks* apply — fail-closed) → non-empty human `reason` → explicit
  `confirm=True`. Output: atomic, monotonically versioned JSON artifact under
  the configured `data/` dirs. Deliberately NOT a source-tree/registry/soul/
  GitHub apply.
- **Phase 9 (security gate).** New `deepagents-harness` CI job (installs the
  optional pins, runs the two phase-6-9 test files, `pip-audit`s the three
  pins), the real-package builder test asserting `StateBackend` has no
  `execute` and is not a sandbox backend, approve/reject/timeout interrupt
  regression tests (timeout → reject), and
  `docs/agentic/DEEP_AGENT_HARNESS_PHASES_6_9.md` documenting all of it.
  Dependency pins in both `pyproject.toml` (new `agentic-deepagents` extra)
  and `constraints.txt`: `deepagents==0.6.12`, `langchain==1.3.11`,
  `langchain-openai==1.3.3`. Zero `config.yaml` key changes (comment-only:
  the `require_human_confirm_for_accept` "NOT YET ENFORCED" tripwire comment
  is removed because enforcement now exists, and the paired tripwire test was
  deliberately renamed/updated in the same diff — exactly what the tripwire
  demanded).

### Review checklist (merge-blocking → nice-to-have)

The one **real security delta to sign off, R1**, then runtime-reality items,
then hygiene. Everything here was verified against the actual diff.

- **R1 — filesystem-write posture change (the PR's one loosening). FIXED
  2026-07-13.** `refuse_phase5_write_policy` → `refuse_unsupported_write_policy`
  no longer refuses `allow_filesystem_write_tools: true`; scoped workspace
  writes are the intended phase-6 capability. The four mitigations hold as a
  set: writes only via `ProposerWorkspaceTools` containment (symlink-hardened
  by PR #518, merged before this fix landed), `interrupt_on` approve/reject on
  both write tools, StateBackend deny-all built-ins, double audit per call.
  **Alias drift fixed:** the backward-compat alias `refuse_phase5_write_policy`
  was deleted from `agentic/deepagent_github/permissions.py`, and
  `deepagent_github/governance.py`'s zero-caller helper was renamed from
  `validate_phase5_policy()` to `validate_write_policy()` and now calls
  `refuse_unsupported_write_policy()` directly — no more loosened-semantics-
  under-an-old-name hazard. Verified: repo-wide grep for both old names
  returns zero hits outside this changelog note.
- **R2 — the real graph is constructed but never invoked in CI.**
  `invoke(payload, config=…, version="v2")` and
  `Command(resume={"decisions": [{"type": …}]})` are exercised only via
  FakeAgent; the docs phrase "CI covers approve, reject, and timeout" means
  the fake-based tests run in the `deepagents-harness` job, not a real
  interrupt round-trip. Accept that scope explicitly, or add one optional
  live-runtime smoke (constructed graph + a stub OpenAI-compatible server).
- **R3 — `resume_deepagent_interrupt` has no try/except:** a runtime failure
  during resume raises raw and unaudited, unlike `invoke_deepagent` which
  audits `agentic_deepagent_invocation_failed`. Wrap it identically (skeleton
  in the follow-up-work section below).
- **R4 — phase-8 provenance gap:** `apply_candidate_artifact` re-verifies
  hash + injection + all config/human gates but never re-verifies that an
  accepted `CandidateDecision` produced the proposal — a caller can
  hand-construct a `HarnessApplicationProposal` for arbitrary clean text and
  record it. Blast radius is a local JSON record under `data/` (low), but
  the phases doc's "cannot bypass" phrasing overstates; either bind the
  decision into the proposal (skeleton below) or soften the doc claim.
- **R5 — audit-coverage asymmetries:** `_validate_wired_tools` mismatch,
  `model_not_configured`/`workspace_required` early returns, and
  memory/skills load errors raise without dedicated audit events; candidates
  emit `agentic_harness_candidate_evaluated` twice (start + finish) while
  baselines get distinct `_started`/`_finished` — makes event-count metrics
  ambiguous. Cheap fixes, none load-bearing.
- **R6 — hardcoded tunable:** `_MAX_MEMORY_BYTES = 64_000` in `memory.py`
  belongs in `config.yaml` per repo rule (and docs say "64 KB" while code is
  64,000 bytes — pedantic drift). Add
  `agentic.deepagent_github.memory_max_bytes: 64000`.
- **R7 — CI drift risk:** the `pip-audit` step re-declares the three pins via
  `printf` — bumping `constraints.txt` without touching `ci.yml` silently
  audits stale versions; `pip-audit` itself installs unpinned. Generate the
  audit list from `constraints.txt` (grep the three package names) or accept
  and comment the duplication.
- **R8 — small test gaps:** `finish_proposal` denied-by-default is not pinned
  by the specs-minimal test (only `proposal_workspace_write_current` is);
  no tests for the `model_not_configured`/`workspace_required` early-return
  statuses; `GitHubCodingEvaluation.selected_commands` is a dead
  never-populated field; `fetch_github_task_context` is exported but uncalled
  by the runner (caller-supplied context — fine, but it's new unwired
  surface the plan doc's inventory discipline would normally list).
- **R9 — `DEEPAGENT_API_KEY` is a new, undocumented env credential** (only a
  builder comment mentions it). Document it (README env table + phases doc),
  and note the redaction interaction: `policy.privacy.redact_secrets_like`
  is pattern-based, so an operator pointing the harness at an authenticated
  OpenAI-compatible endpoint should confirm their key shape matches an
  existing pattern (see the `xai-` gap in the provider-parity section).
- **R10 — style nits:** stray no-op triple-quoted string after `__all__` in
  `runners/__init__.py` (fold into a comment); disabled/refused build
  results now return `tool_names=()` where phase 5 populated them (fine, but
  an external consumer of `DeepAgentBuildResult.tool_names` on non-created
  results sees a behavior change).

Merge recommendation: **R1's alias fix is the only thing worth blocking on**;
R2's scope just needs an explicit yes/no; everything else can land as a
follow-up hardening PR against the checklist. (This is consistent with owner
decision 3: iterate on #515, don't re-implement.)

## Verified deepagents API reference (v0.6.12, 2026-07-11)

Source-verified from the `langchain-ai/deepagents` repo (main ==
`0.6.12`), the `langchain-ai/docs` repo (builds docs.langchain.com), and
PyPI JSON — not from memory. Confidence labels retained where sources were
indirect.

**Package facts.** `deepagents==0.6.12` (2026-06-25), MIT,
`requires-python >=3.11,<4.0` (CyClaw's 3.12 floor is fine). Direct deps:
`langchain>=1.3.12,<2`, `langchain-core>=1.4.9,<2`,
`langchain-anthropic>=1.4.8,<2`, `langchain-google-genai>=4.2.7,<5`,
`langsmith>=0.9.3`, `wcmatch>=10.1`. **`langgraph` is transitive** (via
langchain), and **`langchain-anthropic` + `langchain-google-genai` +
`langsmith` install unconditionally** even for local-only use — a real
dependency-weight consideration for `constraints.txt` review (pydantic
floors interact with the repo's lock-step pins). `langchain-openai` (needed
for LM Studio) and `langchain-xai` (needed for Grok) are **extra installs**.
Note: #515 pins `langchain==1.3.11`, below deepagents 0.6.12's declared
`langchain>=1.3.12` floor — verify pip resolves this cleanly in the
`deepagents-harness` job or bump the pin (the job is green, but check which
langchain actually installed).

**`create_deep_agent` signature (quoted from `deepagents/graph.py`):**

```python
def create_deep_agent(
    model: str | BaseChatModel | None = None,
    tools: Sequence[BaseTool | Callable | dict[str, Any]] | None = None,
    *,
    system_prompt: str | SystemMessage | SystemPromptConfig | None = None,
    middleware: Sequence[AgentMiddleware[StateT_co, ContextT]] = (),
    subagents: Sequence[SubAgent | CompiledSubAgent | AsyncSubAgent] | None = None,
    skills: list[str] | None = None,
    memory: list[str] | None = None,
    permissions: list[FilesystemPermission] | None = None,
    backend: BackendProtocol | BackendFactory | None = None,
    interrupt_on: dict[str, bool | InterruptOnConfig] | None = None,
    response_format: ... = None,
    state_schema: type[DeepAgentState] | None = None,
    context_schema: type[ContextT] | None = None,
    checkpointer: Checkpointer | None = None,
    store: BaseStore | None = None,
    debug: bool = False,
    name: str | None = None,
    cache: BaseCache | None = None,
) -> CompiledStateGraph[...]:
```

Gotchas verified from source/docs:

- The HITL parameter is **`interrupt_on`** (no `interrupt_config` exists);
  `InterruptOnConfig` lives in `langchain.agents.middleware`, not deepagents.
- `model=None` (default `claude-sonnet-4-6`) is **deprecated since 0.5.3**,
  removed in 1.0 — always pass a model.
- A plain-string `system_prompt` is a **prefix** before the built-in base
  prompt; use `SystemPromptConfig(base=None, ...)` to drop the base prompt
  entirely — relevant if CyClaw wants full prompt control as a governed
  surface (`SurfaceType.DEEPAGENT_SYSTEM_PROMPT`).
- **`SubAgent` TypedDict:** required `name`, `description`, `system_prompt`
  (the key is `system_prompt`, not `prompt`); `NotRequired`: `tools`,
  `model` (per-subagent override), `middleware`, `interrupt_on` ("Requires a
  checkpointer."), `skills`, `permissions`, `response_format`.
  `CompiledSubAgent` (`name`/`description`/`runnable`) supports prebuilt
  graphs and does **not** inherit top-level `interrupt_on`. If no
  `general-purpose` subagent is supplied, deepagents adds one automatically —
  suppress via `GeneralPurposeSubagentProfile(enabled=False)` (which also
  removes the `task` tool when no sync subagents exist). **Follow-up for
  CyClaw:** decide whether the auto-added general-purpose subagent is
  acceptable inside the governed harness or should be suppressed; #515 does
  not currently address it.
- **HITL:** `interrupt_on={tool_name: True | False | InterruptOnConfig}`;
  decisions are approve/edit/reject/respond (use `reject` to deny, not
  `respond`); a **checkpointer is required**; resume via
  `agent.invoke(Command(resume={"decisions": [...]}), config=config,
  version="v2")` with one decision per action request, in order; multiple
  pending tool calls batch into a single interrupt. `permissions=` rules with
  `mode="interrupt"` auto-install the HITL middleware and merge with
  `interrupt_on` (user entries win per tool name). #515 uses
  approve/reject only — intentional minimalism matching the plan ledger.
- **Backends:** `StateBackend` (default; virtual, thread-scoped, persisted
  only via checkpointer), `FilesystemBackend` (real FS — docs warning:
  "**Always** use `virtual_mode=True` with `root_dir` … the default provides
  no security even with `root_dir` set"; inappropriate for web servers),
  `StoreBackend`, `CompositeBackend` (route `/workspace/` → scoped FS),
  `LocalShellBackend` (real `subprocess.run(shell=True)` — "No isolation";
  categorically out for CyClaw), `ContextHubBackend`, `LangSmithSandbox`.
  #515's choice of `StateBackend` + deny-all `FilesystemPermission` is the
  strictest available posture.
- **Built-in tools (0.6.12):** `write_todos`; `ls`, `read_file`,
  `write_file`, `edit_file`, `glob`, `grep`; `execute` (errors unless the
  backend implements `SandboxBackendProtocol`); `task` (subagent calls).
  `tools=` is **additive only** — dropping a built-in requires a
  `HarnessProfile` with `excluded_tools` or per-subagent
  `FilesystemMiddleware(tools=[...])` allowlists; `FilesystemMiddleware` and
  `SubAgentMiddleware` are protected (excluding them raises `ValueError`).
  A `delete` tool exists only in `0.7.a1+` pre-releases.
- **MCP:** `langchain-mcp-adapters==0.3.0` exposes MCP servers via
  `MultiServerMCPClient({...}).get_tools()` (async), and deepagents consumes
  the result directly as `tools=`. The official example runs the agent with
  `ainvoke` — if CyClaw ever bridges its retrieval-only MCP server into the
  harness, the loop goes async. Not declared in the repo today; keep it out
  until separately reviewed (canonical doc §17 already says so).

**Model wiring per provider (verified):**

```python
# LM Studio / any OpenAI-compatible local endpoint — instance form, NOT an
# "openai:" string (string specs flip deepagents' provider profile to the
# OpenAI Responses API by default; the instance form bypasses that).
from langchain_openai import ChatOpenAI          # extra install
model = ChatOpenAI(
    model="qwen2.5-7b-instruct",                 # must match GET /v1/models
    base_url="http://localhost:1234/v1",
    api_key="lm-studio",                          # ignored by LM Studio; non-empty
)

# xAI Grok — first-class integration
from langchain_xai import ChatXAI                # extra install (pulls langchain-openai)
model = ChatXAI(model="grok-4.3")                # key from env; base https://api.x.ai/v1/
# ("xai:grok-4.3" string form also works once langchain-xai is installed.)

# Anthropic Claude — langchain-anthropic is ALREADY a mandatory deepagents dep
from langchain_anthropic import ChatAnthropic
model = ChatAnthropic(model="claude-sonnet-5")   # key from env ANTHROPIC_API_KEY
```

`AnthropicPromptCachingMiddleware` sits in deepagents' default stack
unconditionally and no-ops for non-Anthropic models — harmless for LM
Studio/Grok runs.

**Model-swap claim checks (correcting the canonical doc's "unverified"
ledger note):**

- "Qwen 3.7 Max supports the Anthropic Messages protocol" — **verified in
  substance; naming corrected to `Qwen3.7-Max`** (successor line to
  Qwen3-Max). Alibaba Cloud Model Studio exposes Anthropic-protocol
  endpoints (a dedicated "Claude Code" page; Beijing
  `dashscope.aliyuncs.com/apps/anthropic`, Singapore workspace-scoped
  equivalents, configured via `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN`).
  Pricing **partially verified** (secondary sources): list $2.50/$7.50 per
  1M tokens in/out, ~1M context, promo pricing observed at half that.
- "Kimi K2.5 at ~$0.50/task on CursorBench vs Opus-tier ~$7/task" —
  **misattribution, now corrected**: those numbers belong to **Cursor
  Composer 2.5** (verified built on Moonshot's open-source Kimi K2.5
  checkpoint, per Cursor's own blog after the disclosure controversy):
  Composer 2.5 ≈63% at ≈$0.50/task vs Claude Opus 4.7 (xhigh) ≈62% at
  ≈$7/task on Cursor's self-reported benchmark; an independent benchmark
  (Artificial Analysis Coding Agent Index) shows the same direction with
  different absolutes. Kimi K2.5's own verified API pricing: $0.60/M input,
  $3.00/M output, $0.10/M cached input, 262,144-token context.
- Practical takeaway for CyClaw: protocol-compatible larger open-weight
  models exist if 7B-Qwen proves too weak for the harness (see Part 1
  caveat) — but any swap decision should re-verify pricing at decision time;
  promo rates and model lineups move monthly.
## Provider parity design: Grok and Claude inside the harness (owner decision 2026-07-11)

Design goal: make Grok and Claude selectable coding-loop providers for the
Deep Agents harness with the **same gate discipline as invariant I3** (the
core graph's triple-gated external fallback), the same key hygiene as
`llm/client.py`, and an auditable, explainable egress story. Everything below
is a **proposed skeleton for a future PR** — nothing in this section is
implemented as of 2026-07-11.

### Gate chain (I3 pattern, extended for the agentic layer)

A cloud provider drives the harness only when ALL of the following hold —
the first five are config/construction gates, the last is per-run human
action:

1. `agentic.enabled: true` (master switch)
2. `agentic.deepagent_github.enabled: true`
3. `agentic.deepagent_github.allow_cloud_providers: true` (new; default
   `false` — the agentic analog of `app.mode: "hybrid"`)
4. `agentic.deepagent_github.providers.<name>.enabled: true` (new; per
   provider, default `false`)
5. The provider's API key env var is set (`GROK_API_KEY` /
   `ANTHROPIC_API_KEY` — same env vars as `llm/client.py`; **fail closed**
   when unset, mirroring `GrokServiceError("GROK_API_KEY not set")`)
6. The invoking command passes an explicit per-run confirmation flag (e.g.
   `--confirm-online`), audited as `agentic_deepagent_cloud_confirmed` —
   the agentic analog of `user_confirmed_online`

This preserves the I3 shape (mode gate + provider-enable gate + per-use human
confirmation) with the agentic layer's two master switches in front.

### Config skeleton (all defaults off; no secrets in config, ever)

```yaml
agentic:
  deepagent_github:
    enabled: false
    provider: "lmstudio"            # default provider stays local
    base_url: "http://localhost:1234/v1"
    model: ""
    allow_cloud_providers: false    # NEW gate 3; false = providers block inert
    providers:                      # NEW; per-provider enable + naming only
      grok:
        enabled: false
        model: "grok-4.3"           # cite models.grok.model; re-verify at enable time
      claude:
        enabled: false
        model: "claude-sonnet-5"    # cite models.claude.model; same drift warning
    # existing keys unchanged: allow_deepagents_dependency,
    # allow_filesystem_write_tools, allow_shell_execution,
    # allow_github_writes, workspace_root
```

Validation additions in `agentic/config.py`: booleans real, model strings
shell-metachar-free (reuse `_validate_no_shell_metachars`), unknown provider
names rejected, and `providers.*.enabled: true` while
`allow_cloud_providers: false` is a **config error** (fail loud, not
silently inert).

### Adapter skeleton (`agentic/deepagent_github/model_adapter.py` extension)

What transfers from `llm/client.py` is the discipline — env-only keys,
availability = key presence, type-only errors — not the client classes
(the harness needs tool-calling `BaseChatModel`s, not `generate(prompt)`
chat wrappers):

```python
_CLOUD_KEY_ENVS = {"grok": "GROK_API_KEY", "claude": "ANTHROPIC_API_KEY"}


def build_chat_model(settings: DeepAgentModelSettings) -> object:
    """Return a BaseChatModel for the configured provider.

    Local providers never require a key. Cloud providers fail closed on a
    missing key and never place the key anywhere it can be logged.
    """
    if settings.provider in ("lmstudio", "openai_compatible"):
        from langchain_openai import ChatOpenAI  # extra: agentic-deepagents
        return ChatOpenAI(
            model=settings.model,
            base_url=settings.base_url,
            api_key=os.getenv("DEEPAGENT_API_KEY", "not-needed"),
        )
    if settings.provider == "grok":
        from langchain_xai import ChatXAI  # extra: agentic-deepagents-cloud
        key = (os.environ.get("GROK_API_KEY") or "").strip()
        if not key:
            raise AgenticError("GROK_API_KEY not set", details={"required_env": "GROK_API_KEY"})
        return ChatXAI(model=settings.model, api_key=key)
    if settings.provider == "claude":
        from langchain_anthropic import ChatAnthropic  # already a deepagents dep
        key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
        if not key:
            raise AgenticError("ANTHROPIC_API_KEY not set", details={"required_env": "ANTHROPIC_API_KEY"})
        return ChatAnthropic(model=settings.model, api_key=key)
    raise AgenticError("unknown deepagent provider", details={"provider": settings.provider})
```

Dependency plan: `langchain-xai` goes in a **separate** optional extra (e.g.
`agentic-deepagents-cloud`) so the local-only install never pulls a cloud
SDK it doesn't need; `langchain-anthropic` arrives with deepagents anyway
(mandatory upstream dep — a fact to state plainly in the threat model).
Both pinned in `pyproject.toml` AND `constraints.txt` per repo rule.

### Security review requirements (the actual work)

- **Threat-model delta (docs/THREAT_MODEL.md):** egress changes from "never,
  except the core graph's triple-gated fallback" to "also from the harness,
  behind the six-condition chain above." Document exactly what payloads can
  leave (see handoff pipeline below), to which hosts
  (`api.x.ai`, `api.anthropic.com`), and that provider-side retention is
  governed by provider terms, not CyClaw.
- **Redaction parity — close the `xai-` gap.**
  `policy.privacy.redact_secrets_like` today covers `Bearer …`,
  `api_key=` forms, `AKIA…`, `xox…`, `ghp_…`, `sk-[a-zA-Z0-9]{32,}`, and
  `sk-ant-…` — there is **no pattern for xAI's `xai-` key format**. A bare
  `xai-…` key in any audited string is caught only if it happens to follow
  `Bearer ` or an `api_key=` assignment. `llm/client.py`'s type-only error
  messages mean keys don't normally reach audit — this is a
  defense-in-depth gap, not an active leak — but provider parity makes it
  load-bearing. Add `- "xai-[a-zA-Z0-9]{20,}"` to `redact_secrets_like`
  (and, per checklist R9, document `DEEPAGENT_API_KEY` and confirm the
  operator's endpoint key shape matches a pattern). Extend the existing
  redaction-parity tests (the sandbox validator already checks GROK/
  ANTHROPIC parity) to cover it.
- **Interrupt posture for cloud runs:** consider requiring `interrupt_on`
  coverage on **all** tools (not just the two write tools) when a cloud
  provider is active — a cloud-driven agent's tool calls are the moment
  context leaves the operator's control. At minimum, audit each tool call
  with the active provider name so `metrics.py` can answer "what did the
  cloud model touch?"
- **No mixing planes:** the harness's cloud path must not reuse
  `graph.py`'s `user_gate` or `llm/client.py`'s clients — I6 isolation cuts
  both ways. The only shared artifacts are env var names, config
  conventions, and `utils.logger.audit_log`.
- **Test plan:** config-gate matrix tests (any one gate false → local-only),
  fail-closed key tests, fake-transport ChatXAI/ChatAnthropic construction
  tests, redaction tests for `xai-`, and a shipped-config contract update
  (PR #517's `TestShippedAgenticConfigContract`) pinning the new gates
  `false`.

### Why this satisfies the owner's actual need

The owner's stated need is (a) verify Grok/Claude genuinely work end-to-end,
and (b) understand + sanitize the context handoff well enough to explain it
to regulated customers. Full parity delivers (a) directly. For (b), the
value is that the harness becomes a **demonstration vehicle**: the same
sanitize→redact→audit pipeline runs identically whether the model is local
Qwen or cloud Grok/Claude, and the audit log proves it. The compliance
mapping below is written to be shown to exactly those customers.

## Sanitized context handoff pipeline

What leaves the machine, when, and in what form — for both planes. This
section is the "explain it to a regulated customer" artifact.

**When nothing leaves (the default):** every cloud gate above is `false` out
of the box, `app.mode` is `"offline"`, and both `models.grok.enabled` and
`models.claude.enabled` are `false`. In this state CyClaw makes zero
outbound model calls: retrieval is local (ChromaDB embedded +
BM25 JSON), embeddings are local CPU, the LLM is LM Studio on loopback, and
the server itself binds only `127.0.0.1:8787`.

**What can leave when the gates pass (core plane, today):** exactly one
payload shape per provider — the fused prompt (user query + retrieved local
context) POSTed to `{base_url}/chat/completions` (Grok) or
`{base_url}/messages` (Claude) with the key in a header. Never the corpus
wholesale, never the audit log, never the soul file.

**Sanitization stages (all shipped code, cited):**

1. **Inbound injection filter** — `utils/sanitizer.py::check_input`:
   length cap + `banned_patterns` (compiled `re.IGNORECASE`) reject
   prompt-injection shapes before any model, local or cloud, sees the text.
2. **At-rest minimization** — `utils/logger.py::audit_log`: raw query text
   is never persisted; `query` becomes `query_hash` (SHA-256), and every
   other value passes recursive redaction (`redact_sensitive`) — emails,
   IPv4s, and `redact_secrets_like` patterns become
   `[REDACTED_EMAIL]`/`[REDACTED_IP]`/`[REDACTED_SECRET]`.
3. **Error hygiene** — `llm/client.py` maps every provider failure to a
   type-only message (`f"Grok error: {type(e).__name__}"`) because
   stringified exceptions can carry URLs, body fragments, or secrets.
4. **Key handling** — keys exist only in env vars, are checked with
   fail-closed availability, and are sent only as request headers; the
   soul-endpoint auth check uses `hmac.compare_digest` (no timing oracle).

**Proposed harness addition — an explicit handoff envelope** so the agentic
plane's cloud egress is audited *as egress*, not merely as a model call:

```python
@dataclass(frozen=True)
class HandoffEnvelope:
    """What is about to leave the machine, recorded before it leaves."""

    provider: str                 # "grok" | "claude"
    prompt_sha256: str            # hash of the exact outbound prompt
    prompt_chars: int
    context_doc_ids: tuple[str, ...]  # which local chunks were included
    redactions_applied: int       # count from redact_sensitive pre-pass


def sanitize_handoff(prompt: str, *, provider: str, doc_ids: tuple[str, ...],
                     config_path: str = "config.yaml", cfg: dict | None = None) -> tuple[str, HandoffEnvelope]:
    checked = check_input(prompt, config_path)          # raises PromptInjectionError
    redacted = redact_sensitive(checked, cfg)           # strip emails/IPs/secret shapes
    envelope = HandoffEnvelope(
        provider=provider,
        prompt_sha256=hash_query(redacted),
        prompt_chars=len(redacted),
        context_doc_ids=doc_ids,
        redactions_applied=int(redacted != checked),
    )
    audit_log({"event": "agentic_deepagent_cloud_handoff", **asdict(envelope)},
              config_path=config_path, cfg=cfg)
    return redacted, envelope
```

Two honest limitations to state to any customer (and to keep in the threat
model): (1) redaction is pattern-based — it removes *recognizable* secret
and PII shapes, and cannot certify that free text contains no confidential
meaning; minimizing *which documents* are eligible for cloud context (an
allowlist of corpus sources for hybrid mode) is the stronger control.
(2) Once bytes reach a provider, retention/processing is governed by the
provider agreement, not by CyClaw — which is why the per-run confirmation
gate exists.

## Compliance mapping: HIPAA, legal privilege, ITAR/CUI, SOC 2 / ISO 27001

Scope honesty first: CyClaw's threat model
(`docs/THREAT_MODEL.md`) is **single-operator, loopback-bound,
single-tenant**. The mapping below shows how CyClaw's mechanisms *align
with* each framework's concerns for that deployment shape. It is not a
certification, not legal advice, and a multi-user or hosted deployment would
require controls (tenant isolation, IAM, encryption-at-rest management,
BC/DR) that are out of CyClaw's current scope. Said plainly: this is the
architecture to *demonstrate and explain* the controls story, and offline
mode is the strongest control in it.

| CyClaw mechanism (shipped) | HIPAA (medical) | Legal privilege (ABA 1.6) | ITAR/CUI/trade secrets | SOC 2 / ISO 27001 |
|---|---|---|---|---|
| Offline-by-default; all cloud gates false; loopback-only bind `127.0.0.1:8787` | Minimizes PHI disclosure surface to zero by default | No third-party disclosure → no waiver-risk event | Export-controlled data never transits a cloud endpoint | Least functionality; network security (CC6.x-style) |
| Triple/six-condition human gates before any egress | Supports minimum-necessary discipline per disclosure | Deliberate, logged decision before any confidential handoff | Explicit human authorization precedes any egress | Change/authorization control on data flows |
| Audit JSONL: SHA-256 query hashing, recursive PII/secret redaction, thread-locked appends | Audit-trail expectation; log itself avoids becoming a PHI store | Records access without recording privileged content | Access accountability without content spillage | Logging & monitoring evidence, tamper-resistant append |
| Injection filter (`banned_patterns`) + soul/skills injection scans at write boundaries | Guards integrity of clinical-adjacent outputs | Guards against adversarial content exfiltrating context | Guards governed surfaces against tampering | Input validation / integrity controls |
| Fail-closed auth (`hmac.compare_digest`; unset key = 401), env-only secrets | Access control expectation | Confidentiality duty: no shared-secret sprawl | Need-to-know enforcement | Access control + secrets management |
| Atomic writes + SHA-256 versioning + human `reason` for soul/skills/artifacts (I5, phase-8 apply chain) | Documented change history | Defensible record of who changed what and why | Configuration-change traceability | Change management with attribution |
| Module isolation I6 + disabled-by-default agentic layer | Contains blast radius of optional features | Optional features cannot silently widen disclosure | Out-of-band tools cannot reach the data plane | Segmentation / least privilege by construction |
| Documented risk acceptances (e.g. embedded-only ChromaDB CVE) | — | — | — | Risk-register discipline auditors ask for |

Per-framework notes for customer conversations:

- **HIPAA.** Before any PHI-adjacent use of a cloud provider, execute the
  appropriate agreements with that provider (e.g., a BAA) and verify current
  provider terms — CyClaw cannot substitute for that, and this document
  deliberately makes no claim about any provider's BAA availability. The
  architecture's honest pitch: PHI can be processed entirely on-premises
  (offline mode), and if hybrid mode is ever used, the handoff envelope +
  redaction + per-run confirmation give you the minimum-necessary and
  audit-trail story.
- **Legal privilege.** Voluntary disclosure to a third party is the classic
  waiver risk; the offline default means privileged material never leaves
  the machine, and the six-condition chain makes any exception a deliberate,
  attributable act. ABA Model Rule 1.6(c)'s "reasonable efforts" standard is
  exactly what the redaction + gating + audit stack demonstrates.
- **ITAR/CUI/manufacturing.** Treat cloud providers as out of scope for
  export-controlled technical data and CUI unless the provider environment
  is specifically authorized for it — the correct posture is offline mode,
  full stop; trade-secret work gets the same treatment by policy. This is
  the segment where CyClaw's offline-first design is not a compromise but
  the selling point.
- **SOC 2 / ISO 27001.** The mechanisms table maps to auditor questions
  (logging, access control, change management, segmentation, risk
  acceptance). What a real attestation would still need: formal policies,
  personnel controls, BC/DR, and vendor management — organizational scope,
  not code scope.

## Follow-up code skeletons (post-#515 hardening, from the review checklist)

Concrete starting points for the next PR loops. Each is small, one-concern,
and mapped to a checklist item.

**R3 — audited resume (`agentic/deepagent_github/runners.py`):**

```python
def resume_deepagent_interrupt(agent, *, task_id, decision, config_path="config.yaml", cfg=None):
    resolved = "reject" if decision == "timeout" else decision
    message = _RESUME_MESSAGES[resolved]
    audit_log({"event": "agentic_deepagent_interrupt_resumed",
               "decision": decision, "resolved_decision": resolved},
              config_path=config_path, cfg=cfg)
    from langgraph.types import Command
    try:
        return agent.invoke(
            Command(resume={"decisions": [{"type": resolved, "message": message}]}),
            config={"configurable": {"thread_id": task_id}}, version="v2",
        )
    except (HTTPError, OSError, RuntimeError, TypeError, ValueError) as exc:
        audit_log({"event": "agentic_deepagent_resume_failed",
                   "error_type": type(exc).__name__},          # type-only, never str(exc)
                  config_path=config_path, cfg=cfg)
        raise AgenticError("Deep Agents interrupt resume failed") from exc
```

**R4 — provenance binding (`agentic/harness_optimizer/patching.py`):** make
the proposal carry proof it came from an accepted decision, and re-verify at
apply time:

```python
@dataclass(frozen=True)
class HarnessApplicationProposal:
    variant_id: str
    changed_surfaces: tuple[str, ...]
    proposal_text: str
    proposal_sha256: str
    decision_fingerprint: str   # NEW: sha256 of the accepted CandidateDecision's
                                # canonical serialization (variant ids + scores + gates)

# in propose_candidate_application(): fingerprint = _decision_fingerprint(decision, variant)
# in apply_candidate_artifact(): recompute from a caller-supplied decision+variant pair and
# hmac.compare_digest against proposal.decision_fingerprint before the existing gates.
```

**R6 — memory cap into config (`agentic/config.py` + `memory.py`):**

```yaml
  deepagent_github:
    memory_max_bytes: 64000   # cap on data/agentic/deepagent_github/AGENTS.md
```

with `_validate_positive_int` in `DeepAgentGitHubConfig.__post_init__` and
`load_local_memory_files(repo_root, max_bytes=cfg.memory_max_bytes)`.

**R1 — alias retirement (`agentic/deepagent_github/permissions.py` +
`governance.py`):** delete `refuse_phase5_write_policy` (the alias whose
semantics silently changed) and rewrite `validate_phase5_policy` as
`validate_write_policy(policy) -> bool` calling
`refuse_unsupported_write_policy` — with the audit wrapping the canonical
doc's unwired-inventory note already requires for that path.

**Provider parity** — see the dedicated section above (config, adapter, gate
matrix, redaction pattern, tests).

## Docs consolidation record (2026-07-11)

Executed directly on `main` per owner decision 4, absorbing draft PR #501
(closed as superseded after this landed). Disposition per document:

- **`LangchainIntegrationPlan.md` (root, 507 lines)** → replaced with #501's
  9-line superseded-pointer. Its two unique items were folded into the
  canonical doc first: the Windows-workspace-path-safety open question and
  the verified "current `llm/client.py` is plain chat completions (no
  tool-calling)" clause (both → canonical §19), plus the run-artifact
  directory layout (→ canonical §7 addendum, marked proposed vs. shipped).
- **`LangChainFix.md` (root, 361 lines)** → reduced to a pointer stub
  retaining #501's "Reconciliation with shipped code (2026-07-10)" header,
  extended with two corrections #501 missed (the "5 subsystems via
  subprocess" claim is wrong for NeMo/guardrails, which load in-process via
  `utils/guardrail_bridge.py`; the hardcoded `anthropic:claude-sonnet-4-6`
  model contradicted the local-default rule), plus research provenance
  links. Its unique load-bearing content — the LangGraph-native alternative
  verdict, the `Annotated[..., operator.add]` reducer note, and the
  ordered Allow/Deny default-deny permissions pattern — moved into the
  canonical doc as a dated "Alternative considered" section.
- **`future_Langchain_plans.md` (root)** → kept as the single root pointer
  (canonical Phase 0 names it); edited to add the omitted I6 (module
  isolation) to its invariant list, point phases 6-9 at draft PR #515 and
  the canonical ledgers, and drop its duplicate allowed/not-allowed lists.
- **`docs/memories/zOld/LangchainIntegrationPlan.md`** → untouched on
  purpose: byte-identical archive of the pre-implementation draft
  (sha256 `67f62aa3…`), and after the root gut it is the only readable
  full snapshot outside git history.
- **Canonical plan doc** → received #501's §3 strikethrough hunk verbatim
  plus the folds above. Placement was chosen against PR #515's ±148-line
  rewrite of the same file: the §3/§7/§19 edits sit in regions #515 does not
  touch; the appended "Alternative considered" section will produce **one
  trivial merge conflict** with #515's final hunk (its one-line model-swap
  rewording at the old end-of-file). Resolution when rebasing #515: take
  both — #515's reworded line and the appended section. Recorded here so
  the rebase is a 30-second decision, not an investigation.

## Working agreements for the next PR loops

How to consume this document in future sessions (human or agent):

1. **One concern per PR**, cut from `main`, draft, What/Why/Risk body — per
   `CLAUDE.md` §5/§6. The checklist items R1-R10 and the provider-parity
   design are pre-scoped to that size on purpose.
2. **Order of operations:** ~~merge #515 first (after R1's alias fix)~~ —
   done 2026-07-13, R1 fixed and merged (see below). Next: hardening PRs
   (R3-R8), then provider parity, then the live LM Studio smoke. Do not
   start provider parity on a branch cut before this point — it edits the
   same `model_adapter.py`/`config.py` region #515 touched.
3. **Every PR that touches `agentic/`** re-runs
   `python3 .claude/skills/invariant-guard/check_invariants.py` and
   `GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q` locally; anything
   touching docs re-runs `python3 .claude/skills/doc-sync/doc_sync.py`.
4. **Numbers live in `config.yaml`/`pyproject.toml`.** If this document and
   the code disagree, the code wins and this document gets a dated
   correction — same rule as everywhere else in the repo.
5. **Model claims expire.** The Qwen3.7-Max/Kimi/Composer numbers above were
   verified 2026-07-11 from secondary sources; re-verify before any
   purchasing or model-swap decision.

## PR resolution status (2026-07-13)

All six PRs tracked by this document's original review cycle are resolved.
Recorded here as the terminal status of that batch; the per-PR review detail
above (checklist, conflict matrix, R1-R10) is kept as the historical record
of *why* each call was made, not rewritten to erase the "in-flight" framing
it was written under.

- **#515** (phases 6-9 implementation) — merged. Both pre-merge conditions
  from the "PR #515 review" section were satisfied first: the R1 alias fix
  (deleted `refuse_phase5_write_policy`, renamed
  `deepagent_github/governance.py`'s helper to `validate_write_policy()`)
  and the one-hunk doc conflict in
  `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md` (resolved by
  taking main's verified model-swap text). Verified before push: 93/93
  relevant agentic tests, `test_agentic_deepagent_optional` skips cleanly
  without the optional dependency, invariant-guard 27/27, ruff clean.
- **#516, #517, #518, #519** — merged (self-contained, zero cross-overlap
  fixes; #518 landed before #515 per the load-bearing containment
  dependency the R1 sign-off required).
- **#520** — merged (the archived-doc identity-block redaction from the
  `ac1a195` privacy sweep).
- **#497** — closed, not merged. Its branch (`origin/clonebackup-792026`)
  added a single root file, `LangchainDeepAgent.md`, containing research
  notes on Deep Agents memory/persistence design and some naming asides.
  Privacy check: clean — none of the personal-identity markers `ac1a195`
  ordered removed appear in it. Redundancy check: most of its content
  (the `builder.py` toothless-agent bug, the unused `SurfaceType`/
  `GovernanceFinding` findings, the Qwen/Kimi pricing research) was already
  absorbed into `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`
  and this document during the 2026-07-11 consolidation. Three items were
  genuinely unique and are folded in below; the source file was not merged.

## Folded from PR #497: Deep Agents memory, persistence, and taxonomy notes

Three items from the closed #497 branch's `LangchainDeepAgent.md` that
weren't already captured elsewhere in this document or the canonical plan
doc. Recorded verbatim from the source research (light formatting only) so
the content survives the file's removal; provenance and citation numbering
below are as they appeared in that file, not re-verified against current
sources — treat citation markers as historical pointers, not fresh
verification.

### Harness primitive taxonomy: no single "folder" name

There is a semi-standard taxonomy for the "skills/hooks/tools folders"
question, per LangChain and the broader agentic-engineering community — five
primitives, each its own first-class concept with different loading
semantics, no single umbrella term beyond "harness primitives" or
"scaffolding":

- **AGENTS.md** — lightweight, always-loaded repo instructions.
- **Skills** — named, on-demand directories with a `SKILL.md`, portable
  across harnesses (`skills.sh` is the emerging registry).
- **MCP** — authenticated connections to external services (Linear, Slack,
  Sentry-style); downside is context bloat from tool injection.
- **Subagents** — bounded/parallel task delegation for context isolation —
  this is what `agentic/deepagent_github/builder.py`'s `SubAgent` wiring
  targets.
- **Hooks** — deterministic pre/post logic (lint, format, approval-gate)
  that shouldn't depend on the model remembering anything.

Related aside on naming: a "harness" is the full scaffolding (model + tools
+ skills + hooks + MCP + subagents working together); a single clever prompt
pattern or notification trigger is closer to a hook or a UX affordance (a
"doorbell") than a harness in its own right.

### Proposed memory model for a future Deep Agents harness

Not implemented by phases 6-9 as merged (which use only the bounded local
`AGENTS.md` file — see "Verified deepagents API reference" above for the
shipped `StateBackend`/memory design). This is unincorporated design
research for a *future* richer memory layer, kept here rather than silently
lost:

- **Three-tier model:** `checkpoint` for active run/thread continuity,
  `store` for promoted durable facts, and file-backed audit/event logs for
  forensics and replay — matching LangGraph's short-term-vs-long-term
  persistence split, and fitting CyClaw's offline, small-surface design
  better than one shared memory blob.
- **No default cross-boundary writes.** Subagents should not write directly
  into shared long-term memory by default; parent/subgraph state should stay
  namespace-isolated, with the shared store reserved for data intentionally
  crossing boundaries.
- **Persistence rule:** an in-memory-only checkpointer (LangGraph's
  `InMemorySaver`, which phases 6-9 use today) is fine for the current
  interrupt-approve/reject/timeout scope, but is a dev-only choice for
  anything beyond that — production-shaped use needs persistent
  checkpointing with retention/pruning. For CyClaw specifically, SQLite is
  the sane local default; Postgres only if genuine concurrent multi-session
  orchestration is needed — matching the offline-first, loopback-only
  posture already established elsewhere in this repo.
- **Subagent memory policy:** give each subagent a private working set plus
  a read-only slice of parent context; promotion to shared memory should
  require an explicit `promote_memory()` step with schema and trust level,
  preventing context rot. Proposed promotion classes: `user_fact`,
  `project_convention`, `retrieval_hint`, `security_finding`,
  `tool_capability`, `rejected_hypothesis` — only the first four persisting
  by default; rejected hypotheses expire fast.
- **Dynamic subagent deployment policy:** only spawn dynamic subagents for
  naturally fan-out, conditional, or multi-phase work — repo-wide file
  review, per-file GitHub analysis, multi-document synthesis, verifier
  passes on security findings, exhaustive sweeps. Not for ordinary
  single-query RAG or simple tool calls, which just burns tokens and
  complicates state.
- **Proposed pipeline shape:** `controller -> policy gate -> planner ->
  subagent dispatcher -> result verifier -> memory promoter` — preserving
  CyClaw's established principle of enforcement in topology/policy, not
  hopeful prompt wording. Recommended hard gates on any dynamic-subagent
  deployment: max fan-out, allowed subagent types, allowed tools,
  retrieval-first requirement, per-run budget, and approval required for
  mutating tools.

  A starter policy table for when to deploy:

  | Trigger | Deploy? | Memory access | Notes |
  |---|---|---|---|
  | Single local RAG question | No | Thread checkpoint only | Keep cheap and deterministic |
  | Repo-wide file review | Yes | Read-only parent context, per-subagent scratchpad | Fan-out and synthesize |
  | Security finding verification | Yes | No shared writes until verifier consensus | Adversarial verification pattern |
  | Tool mutation or external action | Maybe, gated | No autonomous promotion | Require approval |
  | Learning stable project conventions | No immediate fan-out | Promote to store after repeated confirmation | Prevent garbage memory |

  Overall recommendation if this is ever built: keep subagent state
  disposable, shared memory sparse, and deployment logic rule-based first,
  model-suggested second — the same discipline that keeps the rest of
  CyClaw's agentic layer safe to leave disabled by default.
