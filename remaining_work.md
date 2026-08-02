# Remaining Work

Open engineering work on CyClaw `main`, as of **2026-08-02** (`9282359`).

**How this list was produced, and why that matters.** Every item below was
found by scanning the tree, then put through an *adversarial refutation* pass —
a separate reviewer whose only job was to prove the item was already done or
had been explicitly descoped. Twelve candidates went in; **eleven survived, one
was refuted** (see [Refuted](#refuted-already-shipped)). So this is not a wish
list scraped from stale plan docs: each entry below is something a reviewer
actively tried and failed to disprove.

**Update, later on 2026-08-02:** a second pass acted on most of what was still
open here. #1 was already done before this pass started. #2 went from "design
only" to "designed and implemented" (two PRs). #3 turned out to be stale on
inspection — refuted, not implemented, moved below. #4 turned out to be
mechanically already closed by a PR that landed between the original audit and
this pass; only its test coverage was missing, now added. #5 (Tier 1 dep bump)
was independently found already done, by a different track, before this pass's
own dep-bump agent could touch it. #10/#11 were resolved by relocation instead
of deletion, per an explicit owner decision that changed the plan mid-stream.
Each item below is annotated in place rather than silently rewritten, so the
history stays legible.

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

### 2. NeMo Phase 3 — query-path output rails (`guardrail_output`) — **DONE 2026-08-02**

> **Update 2026-08-02.** Designed in `docs/NeMo/phase4_implementation_plan.md`
> (PR #750) and implemented per that design (PR #751) — both **merged**. The
> section below is left as the original finding for context — everything
> after this note describes the state that landed.

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
- **Design (PR #750, merged):** `docs/NeMo/phase4_implementation_plan.md` resolved
  the design trap above — grounding-only, `local_llm` path only, with the
  `check_soul_leak` half of `output_rails` deliberately left unbuilt pending a
  false-positive sweep against real model output.
- **Implementation (PR #751, merged):** built exactly to that design and
  independently re-verified in a second pass (fresh checkout, own commands,
  `CONFIRMED_SAFE`, zero defects) before the PR was opened. Added
  `guardrail_output` as a 10th graph node between all four answer-producing
  nodes and `audit_logger`, with no new router (one unconditional outbound
  edge) and no new `GraphState` field (input- vs. output-blocked already
  distinguishable via the existing `answer_model` sentinel).
  `CLAUDE.md`/`README.md`/`INVARIANTS.md`/`PROJECT_RULES.md`'s topology counts
  were updated in the same PR.

### 3. NeMo Phase 2 — input rails do not cover the user-gate branch — **REFUTED 2026-08-02, see below**

> **Correction 2026-08-02.** This entry's text (below, left unmodified for
> context) is stale — it was written to describe the same gap #1 closed, and
> was never updated once #1 shipped. Re-checked before treating it as
> actionable work: it is not. See the "Refuted" section for why.

`graph.py`'s `user_gate_router` maps to `grok_fallback` / `claude_fallback` /
`offline_best_effort` / `audit_logger` with no `guardrail_input` on any of
those paths. Same shape as #1, different branch.

- **Risk tier:** High (`graph.py` edges).
- **Source:** `docs/NeMo/phase2_implementation_plan.md:180`.

### 4. No injection scan on `instruction` at `POST /api/agent/run` — **CLOSED 2026-08-02**

> **Correction 2026-08-02.** This entry's framing (below, left unmodified for
> context) is stale. PR #748 (merged, landed between the original audit and
> this correction) added `_refuse_if_injected_instruction` to
> `agentic/cli.py`'s `cmd_real_repo_run`, called before any context fetch or
> clone. `harness/server.py`'s `POST /api/agent/run` reaches exactly that code
> path via `utils.ops_runner.run_agentic_op("real-repo-run", instruction=...)`,
> which spawns `python -m agentic.cli ... real-repo-run --instruction=...` —
> so the scan already covers this route today. The only real gap was test
> coverage of that fact through the harness's *specific* call shape (as
> opposed to the bare CLI, which PR #748's own tests already covered) — PR
> #752 closes that.

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

Four tiers remain (Tier 1 is done). All pins re-verified at `constraints.txt`
on 2026-08-02. These are **staleness, not known vulnerabilities** — no CVE is
being carried knowingly. Bumping a runtime pin is Medium–High tier per
`CLAUDE.md` §7 and needs explicit approval.

| # | Tier | Pins (`constraints.txt`) | Notes |
|---|---|---|---|
| 5 | Tier 1 — dev tooling — **DONE 2026-08-02** | `ruff==0.16.1`, `mypy==2.3.0` | Bumped from `0.15.20`/`2.1.0` in commit `3d540cd` across all four install surfaces. Re-verified same day, independently, against live PyPI (`pypi.org/pypi/{ruff,mypy}/json`) — both are still the current latest stable release, nothing newer to bump to. A `mypy` 2.1.0-vs-2.3.0 spot-check on two representative files showed byte-identical output — no new findings from the bump |
| 6 | Tier 2 — agentic/db | `langgraph==1.2.6` (:43), `langchain==1.3.11` (:81), `langchain-openai==1.3.3` (:82), `psycopg`/`psycopg-binary==3.2.13`, `pgvector==0.4.2` | |
| 7 | Tier 3 — web/core | `fastapi==0.138.0` (:33), `uvicorn==0.49.0` (:37), `langchain-core==1.4.8` (:44) | Touches the `gate.py` request path |
| 8 | `websockets` 15 → 16 (major) | `websockets==15.0.1` (:42) | **Genuinely blocked**, not merely unscheduled: `langgraph-sdk` imports `websockets.asyncio` at graph-import time, which is why this pin is direct rather than transitive. An earlier attempt was abandoned |
| 9 | `httpx2` migration | `httpx==0.28.1` (:39) | Starlette's `TestClient` steers onto `httpx2`; `pyproject.toml` currently filters the deprecation warning by exact message. No runtime impact today — owed before a future Starlette major hard-cuts over. Source: `docs/TESTCLIENT_HTTPX_DEPRECATION.md` |

Each bump must move **all four install surfaces together** (`pyproject.toml`,
`constraints.txt`, `requirements.txt`, `environment.yml`) — that is exactly the
drift class `.claude/skills/verify-deps/` exists to catch.

---

## Documentation consolidation

### 10. The `ARCHIVE_AND_ROADMAP` cutover is half-done — **RESOLVED 2026-08-02, by relocation not deletion**

> **Update 2026-08-02.** The plan below (delete the 17 source files) was
> superseded by an explicit owner decision: relocate and lightly refresh
> instead of delete. 16 of the 17 were `git mv`'d into a new `docs/work/`
> folder (history preserved), each got a surgical staleness pass, and every
> citing file was repointed — see PR #753. The 17th,
> `docs/NeMo/phase3_implementation_plan.md`, was deliberately left in place
> because `docs/NeMo/phase4_implementation_plan.md` (PR #750, now merged and
> permanent on `main`) cites it by exact file:line — moving it would break
> that reference. This is no longer a temporary in-flight concern; revisit
> only if `phase3_implementation_plan.md` itself is ever retired or folded
> into `phase4_implementation_plan.md` directly. `docs/ARCHIVE_AND_ROADMAP.md`'s
> own preamble now documents the old→new mapping directly; this entry is left
> below for historical context, not as an open task.

`docs/ARCHIVE_AND_ROADMAP.md` was written to replace 17 retired/superseded
docs. **All 17 are still on disk**, side by side with the file that condenses
them. The doc says so itself at `:13-17` and calls it a deliberate staged
rollout — but stage 2 was never scheduled, so the current state is duplication
without the consolidation benefit.

Original planned steps, in order (superseded by the relocation above):

1. ~~Repoint `AGENTS.md`'s two `docs/SETUP.md` citations at `setup-guide.md`~~
   — **done 2026-08-02** (this was the doc's own stated prerequisite).
2. ~~Delete the 17 source files.~~ — **superseded**: relocated instead, see above.
3. Run `python3 .claude/skills/doc-sync/doc_sync.py` — done as part of PR #753.
4. Separately grep `CLAUDE.md` / `README.md` / `AGENTS.md` for the other 16
   paths — **`doc-sync` does not check dangling doc-to-doc references**, only
   config-number and route-table drift — done as part of PR #753. A handful of
   citations outside that scope (`.claude/rules/PROJECT_RULES.md` and a few
   others — see PR #753's body for the full list) were deliberately left for a
   future pass that next touches those specific files.

### 11. `docs/SETUP.md` is still a redirect stub — **RESOLVED 2026-08-02**

Kept deliberately so old links resolve. Nothing dangles either way today; it
was relocated to `docs/work/SETUP.md` alongside the other 16 in PR #753
(not deleted — see item 10's update above).

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

Listed so nobody re-opens either of these:

- **Promote `agentic/fsconnect`'s injection scanner from advisory to
  enforcing.** Already done — `agentic/fsconnect/writer.py:240-254`
  implements `_check_injection()`, raising `FsWriteRefused` with
  `failed_gate="injection_scan"`. It landed as Phase 2 write-enablement item 8,
  independently of and prior to the Phase 3 item that still lists it as open.
  The stale plan entry is the only thing suggesting otherwise.
- **Item #3, NeMo Phase 2 input rails on the user-gate branch (2026-08-02).**
  Item #3's text describes `grok_fallback`/`claude_fallback`/
  `offline_best_effort` as all lacking `guardrail_input` coverage — but that's
  leftover from before item #1 shipped. Item #1's fix already routed
  `offline_best_effort` through `guardrail_input`, and its own resolution
  **permanently and deliberately excludes** the two external-provider legs
  from the rail ("their policy gate is the triple gate, not this rail"),
  pinned by `tests/test_graph.py::test_external_fallback_leg_is_not_railed`.
  Implementing #3 as literally written would mean reversing that already-
  shipped, explicitly-tested design decision. There is no coherent remaining
  work under this description — the file's own text just never got updated
  once #1 landed.

---

## Suggested order

**Superseded 2026-08-02 — most of this list is done; kept for history.** #1,
#2 (design + implementation, PRs #750/#751, both **merged**), #4 (PR #752,
**merged**), #5, and #10/#11 (PR #753, still open) are all closed or in-review.
#3 turned out to be stale and is refuted, not implemented. What's left:

1. **Get PR #753 reviewed and merged** — the only one of this round's five
   PRs still open; nothing else in this file is blocked on more
   investigation, it's blocked on review.
2. **Tier 2/3/4 dependency bumps** (`langgraph`/`langchain`/`psycopg`/
   `pgvector`; `fastapi`/`uvicorn`/`langchain-core`; the blocked `websockets`
   major and the `httpx2` migration) — the four-surface pattern Tier 1
   established is mechanical to repeat, but each of these touches more of the
   request path and is correspondingly higher risk; take them one at a time,
   not as a batch.
3. **The `check_soul_leak` question PR #751's design deliberately deferred**
   (see `docs/NeMo/phase4_implementation_plan.md` Decision 2) — needs a
   dedicated false-positive sweep against real model output before it's
   buildable, not a code change on its own.

No other item in the original ranking remains open.
