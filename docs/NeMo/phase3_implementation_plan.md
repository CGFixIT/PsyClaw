---
title: "NeMo Guardrails — Phase 3 Plan (output rails + full input-rail coverage)"
date: 2026-07-27
tags: [guardrails, nemo, graph, topology, security, plan]
source: "planning session vs main @ d863494"
related:
  - docs/NeMo/later_development_guideline.md
  - docs/NeMo/phase2_implementation_plan.md
  - guardrails/integration.py
  - utils/guardrail_bridge.py
  - graph.py
---

## Summary

This document is the planning contract for **Phase 3** of the NeMo Guardrails
roadmap: closing the gap between what `guardrails.enabled: true` *appears* to
switch on and what it actually enforces today. It is a plan, not an approved
change — the graph-edge work it describes sits in the High risk tier
(`CLAUDE.md` §7, "editing a graph edge") and needs explicit sign-off before any
code is written.

Phase 3 covers three separable pieces, deliberately ranked by how well each
clears the FEATURE FREEZE bar (`CLAUDE.md` §1):

| Piece | Nature | Freeze verdict |
|---|---|---|
| A. Close the `offline_best_effort` input-rail bypass | Real defect — identical query blocked or not depending on retrieval score | **Passes** (bug fix) |
| B. Reconcile stale guardrails documentation with shipped code | Documentation accuracy | **Passes** (docs) |
| C. Add a `guardrail_output` node (grounding / hallucination floor) | New capability on the live request path | **Needs explicit justification** |

Each piece is a separate reviewable PR. Piece C must not be bundled with A or B.

---

## Correction: what `guardrails.enabled: true` already does today

Verified against `main` @ `d863494` on 2026-07-27. Two claims that circulated in
earlier planning material (`PR #415`, opened 2026-07-03) are **no longer true**
and must not be carried into Phase 3 work:

- **The NeMo endpoint is not stale.** `guardrails/config/config.yml` lines 24 and
  28 name `qwen2.5:7b` at `http://127.0.0.1:11434/v1` (Ollama), not the retired
  LM Studio port 1234. Independently, `_apply_guardrails_config()`
  (`guardrails/integration.py:70-92`) overrides `engine` / `model` / `base_url`
  from `GuardrailsConfig` at engine-build time, so `config.yaml`'s `guardrails:`
  block is authoritative regardless of what the static NeMo directory names.
- **`nemoguardrails` is already declared.** `pyproject.toml:29-31` carries it as
  the optional extra `[project.optional-dependencies] guardrails` pinned to
  `nemoguardrails==0.18.2`. The roadmap's Phase 5 item "add the dependency" is
  therefore partially complete.

What `guardrails.enabled: true` switches on today is exactly one thing: the
**offline input rail** at the `guardrail_input` node in `graph.py`, built through
the `utils/guardrail_bridge.py` inversion shim and wired by Phase 2. It runs
`guardrails.integration.check_input()` — light injection markers plus the
soul-mutation regex, both model-free — and routes a blocked query straight to
`audit_logger` via `guardrail_router`. No LLM round-trip is added.

---

## Gap 1 — the input rail covers only one of four answer paths

`graph.py` reaches an answer through four terminal producers: `local_llm`,
`grok_fallback`, `claude_fallback`, and `offline_best_effort`. The Phase 2
`guardrail_input` node sits only on the `route_by_score` → `local_llm` edge, so
only the high-score path is railed. `graph.py:850-855` already records this as a
KNOWN GAP in a maintainer comment.

The observable defect: a query carrying an injection or soul-mutation payload is
**blocked when it retrieves well and answered when it retrieves poorly**. Scoring
above or below `retrieval.min_score` (`0.028`, `config.yaml`) is unrelated to
whether the query is hostile, so the rail's coverage is decided by an orthogonal
signal. An attacker who phrases a soul-mutation attempt in terms absent from the
corpus lands on the un-railed branch by construction.

Scope note: `gate.py`'s sanitizer (`utils/sanitizer.py`, 32 `banned_patterns`)
remains the fail-closed front door on *every* path and is unaffected — this gap
is in the defense-in-depth layer behind it, not the primary filter.

Phase 2 deliberately deferred this (`later_development_guideline.md`, resolved
open question: "`local_llm` branch only in Phase 2"), on the reasoning that the
low-score branch is sanitizer-screened and human-confirmed before any external
call. That reasoning holds for `grok_fallback` and `claude_fallback`, which
require `user_confirmed_online`. It does **not** hold for `offline_best_effort`,
which answers locally with no confirmation gate.

---

## Gap 2 — no output rail runs on the live path at any setting

`guardrails/integration.py` contains the grounding / hallucination check
(`grounding_score`, `is_possible_hallucination`, floor
`guardrails.hallucination_threshold: 0.18` from `config.yaml`) and the live NeMo
engine handoff (`get_cyclaw_guardrails`). Both are reachable only through
`safe_generate()`, and `safe_generate()` has **zero production callers** — it is
exercised by tests and the `guardrails.cli` only.

`guardrail_safety_node` (`guardrails/integration.py:326`) is documented as
"PROVIDED FOR FUTURE WIRING ONLY" and is not imported by `graph.py`.

Consequence: with `guardrails.enabled: true`, `nemoguardrails==0.18.2` installed,
and Ollama reachable, the live NeMo engine is still **never built** on the
request path, and no answer is ever checked for grounding. The Colang output
flows in `guardrails/config/rails.co` (`check grounding` at line 110,
`check soul leak` at 118, `self check facts` at 125) never execute in production.

---

## The blocking design constraint: `safe_generate` generates

The single most important constraint on Phase 3, and the reason
`guardrail_safety_node` was left unwired rather than simply plugged in:

`safe_generate()` is the guardrailed analogue of a raw LLM call — it *produces*
an answer via `rails.generate_async()`. By the time any output rail would run in
`graph.py`, `local_llm_node` has **already produced the answer**. Wiring
`safe_generate` (or the `guardrail_safety_node` that wraps it) as an output node
would generate the answer a second time: double latency, double token spend, and
a returned answer that is not the one the graph actually computed or audited.

Phase 3 therefore **must not reuse `safe_generate`** for the output node. It
needs a new, non-generating, synchronous entry point in
`guardrails/integration.py` mirroring the shape `check_input()` already
established:

```python
def check_output(
    answer: str, context: str, *, cfg=None, metrics=None
) -> dict[str, Any]:
    # returns {"blocked": bool, "message": str, "rails": list[str],
    #          "grounding_score": float}
```

Offline-only, no generation, no LLM round-trip — the same discipline that let
`check_input()` be wired safely in Phase 2. The model-assisted Colang rails
(`self_check_input`, `self_check_facts`) stay out of scope for Phase 3 precisely
because they *do* require an extra LLM round-trip per query; they are a separate
later decision with a measured latency budget attached.

---

## Invariant analysis for the proposed `guardrail_output` node

Each of the six invariants (`CLAUDE.md` §3), evaluated against a design that adds
one `guardrail_output` node plus one `guardrail_output_router` between the answer
producers and `audit_logger`:

| # | Invariant | Verdict | Reasoning |
|---|---|---|---|
| I1 | RAG-first | **Preserved** | `retrieve` remains the unconditional entry point; an output rail attaches strictly after an answer exists, so nothing precedes retrieval. |
| I2 | Topology = policy | **Preserved, with care** | The rail must be a visible node plus a conditional edge, never middleware inside `local_llm_node`. Routing stays a graph edge decided by a plain Python router, not an LLM. |
| I3 | Triple-gated external fallback | **Untouched** | An output rail neither adds nor removes a condition on reaching `grok_fallback` / `claude_fallback`. Placing the rail after those nodes does not weaken the three gates in front of them. |
| I4 | Audit convergence | **Preserved, and load-bearing** | A blocked answer must route to `audit_logger`, never to `END`. The count of upstream paths reaching `audit_logger` changes, so `.claude/skills/invariant-guard/check_invariants.py` and `test_graph`'s edge assertions both need updating in the same PR — argued explicitly in the PR body, not silently. |
| I5 | Soul governance | **Preserved** | Rails in `guardrails/rails.py` only read; a rail may refuse a soul-mutation attempt but never writes `data/personality/soul.md`. Soul evolution stays the reason-bearing `gate.py` endpoint. |
| I6 | Module isolation | **Preserved via the existing pattern** | Extend `utils/guardrail_bridge.py` with a `build_output_guard()` factory injected at `build_graph()` time, exactly as `build_input_guard()` already is. `gate.py` and `graph.py` continue never naming `guardrails`. |

The invariant that costs real work is **I4**: adding a node changes the audit
convergence topology that `invariant-guard` asserts statically. That is a
deliberate, reviewable change to a security invariant's *shape* (not its
guarantee) and is the single strongest reason Phase 3 piece C needs sign-off
before implementation rather than after.

---

## Fail-open versus fail-closed for an output rail

Phase 2 chose **fail-open** for the input rail: `guardrail_input_node` catches
every exception from the injected guard and answers normally
(`graph.py:340-345`), on the reasoning that a raising defense-in-depth layer must
never take down `/query` when the fail-closed sanitizer already ran.

An output rail inherits that reasoning for *exceptions* — a crashing rail should
not swallow an answer the graph already computed. It does **not** automatically
inherit it for *verdicts*: a low grounding score is the rail working correctly,
not failing, and suppressing the answer is the intended behavior.

The trap to avoid: `guardrails.hallucination_threshold` is documented in
`later_development_guideline.md` as an untuned placeholder ("tune against the
real corpus; current value is a placeholder"). Shipping a blocking output rail on
an untuned floor risks suppressing correct answers — a false-positive rate nobody
has measured. Phase 3 should therefore land the output rail in **observe-only
mode first** (record the grounding score and the would-block verdict to
`logs/guardrails.jsonl`, return the answer unchanged), gather real distribution
data, and only then flip to enforcing behind a separate config key. That ordering
also keeps the first PR's blast radius to "adds a node that cannot change any
answer."

---

## Proposed sequencing

Three PRs, in this order, each independently revertible:

**PR 1 — documentation reconciliation (piece B, no code).**
`docs/NeMo/later_development_guideline.md` carries drift that would mislead any
future implementer: line 31-32 states the skeleton "is *not* wired into the
request path yet" (Phase 2 wired it), and line 44 describes
`guardrails/config/config.yml` as pointing at "loopback LM Studio" (it points at
Ollama 11434). Correcting the guideline's current-state section, marking Phase 2
DONE, and recording the verified state from this document's correction section is
a pure docs change that clears the freeze bar and de-risks everything after it.

**PR 2 — close the `offline_best_effort` bypass (piece A, bug fix).**
Route `offline_best_effort` through the same offline input rail the high-score
path already gets, so the rail's coverage stops depending on `retrieval.min_score`
(`0.028`). This is an edge change and still High tier, but it *narrows* a
documented inconsistency rather than adding a capability, and it reuses the
already-shipped `check_input()` with no new guardrails surface area. The
`grok_fallback` / `claude_fallback` paths stay out of scope: their
`user_confirmed_online` gate is a real compensating control that
`offline_best_effort` lacks.

**PR 3 — `guardrail_output` node, observe-only (piece C, new capability).**
Requires: explicit user approval against the FEATURE FREEZE test, a new
non-generating `check_output()` in `guardrails/integration.py`, a
`build_output_guard()` factory in `utils/guardrail_bridge.py`, the
`invariant-guard` and `test_graph` topology updates for I4, and the observe-only
posture described in this document's fail-open section. Not to be started before
PR 1 and PR 2 have merged.

---

## Verification required for any Phase 3 code PR

Commands are the canonical ones from `CLAUDE.md` §8 — no invented invocations:

```bash
# Static invariants — run FIRST (before the change, to capture the baseline)
# and LAST (after, to argue any intentional topology delta explicitly).
python3 .claude/skills/invariant-guard/check_invariants.py

# Config contract (any config.yaml key added for the observe-only toggle)
python3 .claude/skills/config-guard/check_config.py --strict

# Dependency pins (relevant if the optional guardrails extra is touched)
python3 .claude/skills/dep-guard/check_deps.py

# Lint (CI-enforced) and the full suite
ruff check --select E,F,I,B,C4,UP,S .
GROK_API_KEY=dummy pytest tests/ -q --tb=short

# Guardrails-specific suites (8 files today; no heavy deps, no live services)
GROK_API_KEY=dummy pytest tests/test_guardrails_*.py tests/test_guardrail_bridge.py -q
```

Runtime probe, both settings, because the shipped default must stay inert:

1. `guardrails.enabled: false` (the shipped default in `config.yaml`) —
   `/query` behavior byte-identical to `main`, and `guardrails` never imported.
2. `guardrails.enabled: true` — a probe that clears the `gate.py` sanitizer but
   trips the rail under test; expect HTTP 200, one event in
   `logs/guardrails.jsonl` (SHA-256 hashes only, never raw text), and a converged
   event in `logs/audit.jsonl`.

---

## Open questions to resolve before Phase 3 code

- [ ] **Does `guardrail_output` block, or only observe, in its first shipped
      form?** This document recommends observe-only until
      `guardrails.hallucination_threshold` (`0.18`, `config.yaml`) has been tuned
      against the real corpus. Needs an operator decision. (Priority: high)
- [ ] **Is `nemoguardrails==0.18.2` required in `constraints.txt` as well as
      `pyproject.toml`?** `CLAUDE.md` §6 requires an exact pin in both for a new
      dependency; it is currently declared only in `pyproject.toml:30` as an
      optional extra. Confirm whether `dep-guard` treats optional extras as
      in-scope for the cross-file agreement rule before adding or dismissing it.
      (Priority: medium)
- [ ] **Do the model-assisted Colang rails (`self_check_input`,
      `self_check_facts`) ever go live?** Each adds an LLM round-trip per query.
      Requires a measured latency budget and a false-positive rate against a
      soul-attack corpus first. (Priority: low — explicitly out of Phase 3 scope)
- [ ] **Second guardrail model in Ollama, or reuse `main`?** Carried forward
      unresolved from `later_development_guideline.md`; only becomes blocking if
      the model-assisted rails are approved. (Priority: low)

---

## References

- `docs/NeMo/later_development_guideline.md` — the roadmap and the invariant
  contract for the guardrails layer (carries known current-state drift; see this
  document's proposed PR 1)
- `docs/NeMo/phase2_implementation_plan.md` — the input-rail node contract this
  document extends, including the inversion-shim decision
- `CLAUDE.md` §3 (the six invariants), §7 (risk tiers and escalation)
- `docs/THREAT_MODEL.md` — single-operator, loopback-bound security scope
