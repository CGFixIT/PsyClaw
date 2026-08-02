# Remaining Work

Open engineering work on CyClaw `main`, as of **2026-08-02** (`9282359`).

**How this list was produced, and why that matters.** Every item below was
found by scanning the tree, then put through an *adversarial refutation* pass —
a separate reviewer whose only job was to prove the item was already done or
had been explicitly descoped. Twelve candidates went in; **eleven survived, one
was refuted** (see [Refuted](#refuted-already-shipped)). So this is not a wish
list scraped from stale plan docs: each entry below is something a reviewer
actively tried and failed to disprove.

**Relationship to other docs.** [`docs/ARCHIVE_AND_ROADMAP.md`](docs/ARCHIVE_AND_ROADMAP.md)
holds the *history and rationale* — retired designs, superseded plans, why
things were decided. This file is the *actionable checklist*: what is still
open, with the file:line that proves it. When they disagree, trust the
file:line evidence here and re-verify, since code moves and prose does not.

Nothing here is a regression or a surprise. Several are deliberate staged
rollouts whose second half was never scheduled.

---

## Security / correctness

These four are the ones with a security or behavioral consequence. They are
listed first on purpose.

### 1. `offline_best_effort` bypasses the input guardrail — **DONE 2026-08-02**

The offline input rail used to cover only the high-score retrieval route, so a
query that fell through to `offline_best_effort` reached the local model without
passing the `guardrail_input` node. The rail's coverage was keyed on a retrieval
score, which is not a security property.

Closed by routing `user_gate`'s `"offline_best_effort"` return **through
`guardrail_input`** in the conditional-edge `path_map`, and giving
`guardrail_router` a third target discriminated on `needs_user_confirm` (set
explicitly on both branches by `route_by_score_node`, so it is a reliable
"which inbound edge did this arrive on" signal).

- **I2** holds: the decision is still a graph edge, not a runtime `if` outside a
  router. **I3** is untouched — `user_gate_router`'s provider gating is
  unchanged and the two external legs stay deliberately un-railed (their policy
  gate is the triple gate, not this rail). **I4** holds on both new legs.
- `invariant-guard`'s expected-target sets were updated in the same change;
  `test_low_score_path_never_invokes_the_guard` (which pinned the old behavior)
  was superseded by `test_low_score_offline_path_now_invokes_the_guard`, plus
  `test_external_fallback_leg_is_not_railed` to pin the deliberate exclusion.

### 2. NeMo Phase 3 — query-path output rails (`guardrail_output`)

A repo-wide search for `guardrail_output` returns **zero hits in any `.py`
file**. There is no output rail on the query path.

> **Corrected 2026-08-02.** This entry previously claimed "the output-rail logic
> is implemented and tested; only the `graph.py` wiring is missing," and called
> it the best value-per-risk item here. **Both claims were wrong** — they came
> from a summary that was not checked against the code. What is actually there:

- The grounding/hallucination check lives **inside `safe_generate()`**
  (`guardrails/integration.py:308`), which is `async` and **generates its own
  answer** — it is "the guardrailed analogue of a raw LLM call," not a checker.
- `guardrail_safety_node()` exists but is marked **"PROVIDED FOR FUTURE WIRING
  ONLY"** (`integration.py:320`), and `check_input`'s own docstring names the
  reason it stays unwired: wiring it would **double-generate** — the graph has
  already produced an answer by then.
- `utils/guardrail_bridge.py` exposes only `build_input_guard`. There is no
  output counterpart, and I6 forbids `graph.py` importing `guardrails` directly.

So the real work is a new **sync, non-generating** output check, a
`build_output_guard` bridge function, a node, a router, and edges preserving I4
— not an edge.

**It is also not an approved design.** `docs/NeMo/phase3_implementation_plan.md`
carries it as an *open question*, not a plan: *"Do the query-path output rails
ever get built? Deferred by this redirect, not cancelled. Revisit once 3A has
landed... **(Priority: low)**."* 3A has since landed
(`build_injection_pattern_sources`/`compile_injection_patterns` are in
`guardrails/rails.py`), so the gate is lifted — but the question is still
unanswered.

**Design trap to solve before building.** The output rail is a *grounding check
against retrieved context*. The `grok_fallback` / `claude_fallback` /
`offline_best_effort` paths are reached **precisely because retrieval scored
below `min_score`**, so context is weak or absent — and `grounding_score`
returns 0.0 when there is content but no supporting context. Applying the rail
to those paths would block nearly every fallback answer. Any design must say
which paths it covers and why.

- **Risk tier:** High (`graph.py` edges), and larger than #1 or #3.
- **Source:** `docs/NeMo/phase3_implementation_plan.md:176`, `:261`.
- **Design (2026-08-02, not yet approved):** `docs/NeMo/phase4_implementation_plan.md`
  resolves the design trap above — grounding-only, `local_llm` path only in its
  first cut, with the `check_soul_leak` half of `output_rails` deliberately left
  unbuilt pending a false-positive sweep against real model output. No code
  written; still needs owner sign-off before implementation.

### 3. NeMo Phase 2 — input rails do not cover the user-gate branch

`graph.py`'s `user_gate_router` maps to `grok_fallback` / `claude_fallback` /
`offline_best_effort` / `audit_logger` with no `guardrail_input` on any of
those paths. Same shape as #1, different branch.

- **Risk tier:** High (`graph.py` edges).
- **Source:** `docs/NeMo/phase2_implementation_plan.md:180`.

### 4. No injection scan on `instruction` at `POST /api/agent/run`

`harness/server.py:675`'s handler passes `instruction=req.instruction` through
to the agentic run with no pre-flight scan. Operator-supplied text goes
straight into a planning loop.

- **Mitigating context, so this is not overstated:** this route is already
  behind Bearer `CYCLAW_API_KEY` + a CSRF token + an `Origin`/`Sec-Fetch-Site`
  cross-site guard, and it is loopback-only. The realistic threat is not a
  remote attacker but a *confused-deputy* path — text that reached the operator
  from somewhere else and got pasted in.
- **Risk tier:** Medium. It adds a scan; it does not change routing.
- **Source:** `docs/ARCHIVE_AND_ROADMAP.md:961` (item "3B").

---

## Dependency currency

Five tiers, none started. All pins verified unchanged at `constraints.txt` on
2026-08-02. These are **staleness, not known vulnerabilities** — no CVE is
being carried knowingly. Bumping a runtime pin is Medium–High tier per
`CLAUDE.md` §7 and needs explicit approval.

| # | Tier | Pins (`constraints.txt`) | Notes |
|---|---|---|---|
| 5 | Tier 1 — dev tooling | `ruff==0.15.20` (:114), `mypy==2.1.0` (:115) | Lowest blast radius, but a `ruff`/`mypy` bump silently changes what lint/type-check accept — re-run both gates in the same PR |
| 6 | Tier 2 — agentic/db | `langgraph==1.2.6` (:43), `langchain==1.3.11` (:81), `langchain-openai==1.3.3` (:82), `psycopg`/`psycopg-binary==3.2.13`, `pgvector==0.4.2` | |
| 7 | Tier 3 — web/core | `fastapi==0.138.0` (:33), `uvicorn==0.49.0` (:37), `langchain-core==1.4.8` (:44) | Touches the `gate.py` request path |
| 8 | `websockets` 15 → 16 (major) | `websockets==15.0.1` (:42) | **Genuinely blocked**, not merely unscheduled: `langgraph-sdk` imports `websockets.asyncio` at graph-import time, which is why this pin is direct rather than transitive. An earlier attempt was abandoned |
| 9 | `httpx2` migration | `httpx==0.28.1` (:39) | Starlette's `TestClient` steers onto `httpx2`; `pyproject.toml` currently filters the deprecation warning by exact message. No runtime impact today — owed before a future Starlette major hard-cuts over. Source: `docs/TESTCLIENT_HTTPX_DEPRECATION.md` |

Each bump must move **all four install surfaces together** (`pyproject.toml`,
`constraints.txt`, `requirements.txt`, `environment.yml`) — that is exactly the
drift class `.claude/skills/verify-deps/` exists to catch.

---

## Documentation consolidation

### 10. The `ARCHIVE_AND_ROADMAP` cutover is half-done

`docs/ARCHIVE_AND_ROADMAP.md` was written to replace 17 retired/superseded
docs. **All 17 are still on disk**, side by side with the file that condenses
them. The doc says so itself at `:13-17` and calls it a deliberate staged
rollout — but stage 2 was never scheduled, so the current state is duplication
without the consolidation benefit.

Remaining steps, in order:

1. ~~Repoint `AGENTS.md`'s two `docs/SETUP.md` citations at `setup-guide.md`~~
   — **done 2026-08-02** (this was the doc's own stated prerequisite).
2. Delete the 17 source files.
3. Run `python3 .claude/skills/doc-sync/doc_sync.py`.
4. Separately grep `CLAUDE.md` / `README.md` / `AGENTS.md` for the other 16
   paths — **`doc-sync` does not check dangling doc-to-doc references**, only
   config-number and route-table drift, so step 4 is not optional.

- **Risk tier:** Low, but irreversible-ish (deletions) — worth one explicit
  confirmation before step 2.

### 11. `docs/SETUP.md` is still a redirect stub

Kept deliberately so old links resolve. Nothing dangles either way today; its
deletion is part of item 10's step 2, not separate work.

---

## Additional finding (2026-08-02, outside the original eleven)

Surfaced while answering a question about offline operation, verified the same
way, and recorded here so it is not lost:

### The agentic coding loop has no local-directory mode

`RepoWorkspaceTools` exposes exactly two entry points —
`clone()` (`repo_workspace.py:292`), which shells `gh repo clone`, and
`attach()` (`:332`), which re-opens a directory a prior `clone()` produced and
validates it is a `clone()` output under `workspace_root`. `agentic.repo` is
validated against `_REPO_RE` (`gh_client.py:52`) as `owner/name`.

Consequence: **the agentic loop always starts from a GitHub clone.** It cannot
be pointed at a local working directory or an arbitrary git remote. A
local-only operator can chat with the local model offline, but cannot drive the
plan → patch → verify loop against their own local project.

- **Not a defect** — it is what was built, and the GitHub coupling is what the
  governance/audit story is written around. Recorded because it is a real
  capability boundary that no doc currently states plainly.
- **Risk tier if changed:** Medium–High. A local-path mode would need its own
  containment story; `pathsafe.ScopedRoots` already exists and would be the
  right primitive, but the write-gate reasoning assumes a throwaway clone.

---

## Refuted (already shipped)

Listed so nobody re-opens it: **promote `agentic/fsconnect`'s injection scanner
from advisory to enforcing.** Already done — `agentic/fsconnect/writer.py:240-254`
implements `_check_injection()`, raising `FsWriteRefused` with
`failed_gate="injection_scan"`. It landed as Phase 2 write-enablement item 8,
independently of and prior to the Phase 3 item that still lists it as open.
The stale plan entry is the only thing suggesting otherwise.

---

## Suggested order

Re-ranked 2026-08-02, after #2's entry was corrected (see the note there — it
was previously listed first on a false premise).

1. **#1** (`offline_best_effort` bypasses `guardrail_input`) — the only
   graph-edge item the phase-3 plan explicitly blesses: *"a real inconsistency
   and a legitimate bug fix under FEATURE FREEZE"* that *"can proceed on its own
   schedule."* It closes a live gap — a hostile query is blocked when it
   retrieves well and answered when it retrieves poorly — and needs no new
   `guardrails/` primitive, because the input guard it routes through already
   exists and is already wired.
2. **#5** (Tier 1 dep bumps) — dev-tooling only, contained.
3. **#4** (instruction scan on `POST /api/agent/run`) — the other live gap
   rather than staleness or staging.

**#3** is the same shape as #1 (extend the existing input rail to another
branch) and is the natural companion to do in the same sitting, while the graph
topology is loaded in someone's head.

**#2 is deliberately not in this list.** It is the largest of the graph items,
it needs a new `guardrails/` primitive and a new bridge function rather than an
edge, and the plan still carries it as an unanswered question at priority: low.
It wants a written design and a sign-off before code, not a slot in a queue.

The remaining dependency tiers are mechanical once #5 establishes the
four-surface pattern.
