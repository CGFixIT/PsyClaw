# NeMo Phase 4 — query-path output rail (`guardrail_output`) design

**Status: DESIGN ONLY. Not approved, not scheduled, no code written against it.**
This document answers the open question `docs/NeMo/phase3_implementation_plan.md`
left standing at `:261-263` ("Do the query-path output rails ever get built?
Priority: low") and the parallel entry in `remaining_work.md` item #2. It exists
so an owner can approve, amend, or reject a concrete design in one sitting,
instead of the decision being re-litigated from scratch next time someone reads
that open question. **Touches zero files.** `graph.py`, `guardrails/`, and
`utils/guardrail_bridge.py` are all High risk tier (`CLAUDE.md` §7 — graph-edge
change) and require separate, explicit owner sign-off before any of the code
below is written, exactly as the Phase 2 precedent (`phase2_implementation_plan.md`)
was reviewed before its code PR.

Written as of `main` @ `8b19822` (2026-08-02), after Phase 2 (input rail wired,
`remaining_work.md` item #1) and Phase 3A (scanner consolidation) had both
already landed.

---

## Summary

`remaining_work.md` item #2 already corrected two prior misreadings: the
grounding/hallucination logic is not a standalone checker but lives inside
`safe_generate()` (`guardrails/integration.py:192`), which is `async` and
**generates its own answer** — wiring it as a graph node would double-generate.
`guardrail_safety_node()` (`:326`) is explicitly marked "PROVIDED FOR FUTURE
WIRING ONLY" for exactly that reason. So the real work is a new **sync,
non-generating** check, a bridge function mirroring `build_input_guard`, a new
node, and new edges — not a one-line wire-up.

This document's contribution beyond that framing:

1. **No new primitive needs to be written in `guardrails/rails.py`.** The two
   functions an output rail needs — `grounding_score`/`is_possible_hallucination`
   (`:202-225`) and `scan_injection` (`:108-131`, already reused on the model's
   own answer by the live Colang `check soul leak` flow via
   `check_injection(text=$bot_message)`) — are both pure, sync, already
   unit-tested, and already used elsewhere. The only new code is a thin
   integration wrapper.
2. **The design trap in `remaining_work.md` is real, verified against the actual
   node prompts, and has exactly one clean resolution: scope grounding to the
   `local_llm` path only.** See Decision 2.
3. **A second, previously unflagged false-positive risk applies to the
   `check_soul_leak` half of `config.yaml`'s `guardrails.output_rails` default**
   (`["check_grounding", "check_soul_leak"]`, `guardrails/config.py:59`) — see
   Decision 2's second half. This document recommends **not** building it in
   the same change as grounding.
4. **The topology fits with zero new router.** Every path through the new node
   reaches `audit_logger` regardless of verdict — the same reasoning
   `remaining_work.md` item #2 already anticipated ("not an edge"). Decision 5
   shows why this holds under I2 by citing an existing precedent in the same
   file, not just by assertion.
5. **Stage disambiguation (was this answer blocked at input or at output?) needs
   zero new `GraphState` fields.** See Decision 4.

---

## Recap: the design trap, verified against the live node bodies

`grounding_score(answer, context)` (`guardrails/rails.py:202`) returns `1.0` for
an empty answer and **`0.0` when there is content but no supporting context** —
by design, not a bug (its own docstring says so). Read against the actual prompt
each answer-producing node builds (`graph.py`, current `main`):

| Node | When reached | Context behavior | Grounding-check verdict if applied uniformly |
|---|---|---|---|
| `local_llm_node` (`:359`) | retrieval scored ≥ `min_score` | Full retrieved docs, always | Meaningful — prompt itself demands "Answer based STRICTLY on the retrieved context" (`:397`) |
| `offline_best_effort_node` (`:576`) | retrieval scored low, or user declined/offline | Docs if present, but the prompt **explicitly invites ungrounded content**: "Provide the best answer you can. Clearly note where you lack sufficient context" (`:619`), or when `docs` is empty, "Provide the best general answer you can" (`:625`) | **Would false-positive-block almost every honest best-effort answer** — the node's own contract is "answer even when ungrounded, and say so," which is precisely what trips `is_possible_hallucination` |
| `grok_fallback_node` / `claude_fallback_node` (`_external_fallback_node`, `:444`) | user confirmed + hybrid + provider usable | `docs = state.get("retrieved_docs", []) if send_ctx else []`, and `send_local_context_to_grok`/`_claude` both **default `false`** (`config.yaml`) | **Would false-positive-block nearly every default-config external answer** — zero context is the norm, not the exception, for this path |

So a single uniform grounding check across all four answer-producing edges is
not a smaller version of the right design — it is a wrong design that happens to
share a node name with the right one. `remaining_work.md` already said "any
design must say which paths it covers and why"; this table is that answer,
checked against the code rather than asserted.

---

## Decision 1 — No new check primitive; a thin non-generating wrapper only

`guardrails/integration.py` already has the exact shape this needs, twice:

- `check_input(query, *, cfg, metrics)` (`:154`) — the Phase 2 precedent. Sync,
  never generates, calls only offline heuristics, returns
  `{"blocked": bool, "message": str, "rails": list[str]}`. `utils/guardrail_bridge.py`'s
  `build_input_guard` is the only production caller and the only place that
  ever imports it.
- `safe_generate()`'s own internal grounding step (`:302-311`) already computes
  `grounding_score(response, context)` and `is_possible_hallucination(...)` —
  proof the exact same call, made against a **already-produced** answer instead
  of one this function itself generated, is all a Phase 4 check needs to do.

Proposed new function, same file, same shape as `check_input`:

```python
def check_output(
    answer: str,
    context: str,
    *,
    query: str = "",
    cfg: GuardrailsConfig | None = None,
    metrics: GuardrailMetrics | None = None,
) -> dict[str, Any]:
    """Phase 4 output rail -- sync, offline-only, NEVER generates.

    Mirrors check_input's non-generating guarantee in reverse: check_input
    runs before generation and can skip an LLM call; this runs after
    generation and can only replace an answer that already exists. Grounding-
    only in this cut (Decision 2) -- callers decide WHICH answer paths reach
    this function at all; it does not re-derive that policy itself.
    """
    if cfg is None:
        cfg = load_guardrails_config()
    if metrics is None:
        metrics = GuardrailMetrics(cfg.metrics_path)

    score = grounding_score(answer, context)
    if not is_possible_hallucination(answer, context, cfg.hallucination_threshold):
        metrics.record_allowed(stage="output", score=score, query=query)
        return {"blocked": False, "message": "", "rails": []}

    metrics.record_hallucination(score=score, threshold=cfg.hallucination_threshold, query=query)
    metrics.record_blocked(stage="output", rail="check_grounding", reason="low grounding", query=query)
    return {"blocked": True, "message": cfg.block_message, "rails": ["check_grounding"]}
```

No change to `grounding_score`, `is_possible_hallucination`, `GuardrailsConfig`,
or `GuardrailMetrics` — all four are consumed as-is. `query` is accepted only for
metrics correlation (so an output-block event can be joined with the same
hashed-query used elsewhere in that request's audit trail); the answer text
itself is never hashed or persisted anywhere new, matching the existing
metrics-are-hashes-only contract (`docs/NeMo/README.md` TL;DR).

---

## Decision 2 — Scope: `local_llm` only for grounding; defer soul-leak entirely

**Grounding.** Per the table above, the only path where a "does this answer
match the retrieved context" question is even well-posed is `local_llm` — it is
the only node whose own prompt makes a strict-grounding promise to begin with.
Recommendation: **Phase 4a checks `local_llm` only.** `offline_best_effort` and
the two external fallbacks are excluded by design, not by omission — their
prompts already tell the model it's fine to answer without full grounding, and
applying this check there would punish the node for doing exactly what it was
asked to do.

**`check_soul_leak` (the other half of `config.yaml`'s
`guardrails.output_rails` default, `["check_grounding", "check_soul_leak"]`,
`guardrails/config.py:59`) should NOT be built in the same change, and arguably
not built at all without a dedicated measurement first.** The live Colang
`check soul leak` flow reuses `scan_injection` (`rails.py:108`) against the
model's own answer text. Reading that function's own docstring: it is
"deliberately the SMALL scan" (7 fixed substrings —
`_INJECTION_MARKERS`, `rails.py:71-79`), designed and validated as an **input**
check that sits *behind* `utils/sanitizer.py`'s fail-closed 40-pattern filter,
where a legitimate user typing e.g. "you are now" as part of their own query is
comparatively rare. Applied to **output** — every answer the local model or an
external provider produces — the same marker set has a materially different
false-positive profile: `"you are now"` alone is ordinary technical prose
("...you are now connected to...", "...you are now in the main menu...").
Longer, prose-heavy, explanatory answers are exactly the content most likely to
contain an incidental match purely because they have more surface area than a
typical query. This is a genuine, previously unflagged risk — distinct from
(and in addition to) the scanner's documented input-side rationale — and it has
not been measured against any real model output, only reasoned about here.

Recommendation: leave `check_soul_leak` **unbuilt** in Phase 4a. If it is ever
wanted, it needs its own adversarial false-positive sweep against a
representative sample of real model answers — the same methodology
`.claude/skills/injection-redteam/redteam.py` already applies to the input
side — as a prerequisite, not as a follow-up nice-to-have. That sweep, and the
decision whether to proceed, is out of scope for this document and should be
its own future design note if picked up.

Practical effect: `check_output` above computes grounding only. No
`scan_injection`/soul-leak call exists in the Phase 4a code at all — there is
nothing to gate behind a config flag because there is nothing built yet to gate.

---

## Decision 3 — Bridge: `build_output_guard`, mirroring `build_input_guard` exactly

`utils/guardrail_bridge.py:19` is the entire precedent to copy:

```python
def build_output_guard(cfg: dict[str, Any]) -> Callable[[str, str, str], dict[str, Any]] | None:
    """Build the Phase 4 guardrail_output callable, or None when disabled.

    Same guardrails.enabled gate as build_input_guard -- no separate toggle.
    Returns None before importing guardrails at all when disabled.
    """
    if not (cfg.get("guardrails") or {}).get("enabled", False):
        return None

    from guardrails.config import load_guardrails_config
    from guardrails.integration import check_output
    from guardrails.metrics import GuardrailMetrics

    gcfg = load_guardrails_config()
    metrics = GuardrailMetrics(gcfg.metrics_path)

    def _output_guard(query: str, answer: str, context: str) -> dict[str, Any]:
        return check_output(answer, context, query=query, cfg=gcfg, metrics=metrics)

    return _output_guard
```

Same top-level `guardrails.enabled` flag as the input rail — no new config key.
Same I6 preservation: `gate.py`/`graph.py` never name `guardrails`; the import
happens only inside this function, only when enabled.

---

## Decision 4 — Stage disambiguation needs no new `GraphState` field

An operator reading `logs/audit.jsonl` needs to tell "blocked at input" (no LLM
ran) apart from "blocked at output" (an LLM ran; its answer was replaced). This
is already fully derivable from fields `audit_logger_node` already emits, with
zero additions:

- Input-blocked: `guardrail_input_node` sets `answer_model: "guardrail-blocked"`
  (`graph.py:353`) — a sentinel distinct from every real model name.
- Output-blocked (proposed): the new node leaves `answer_model` **untouched** —
  it still reads `"local"` (or whichever path produced the now-replaced answer)
  — and sets the same `guardrail_blocked` / `guardrail_rails` fields the input
  rail already contributes to the audit event (`graph.py:662-663`).

So: `guardrail_blocked=True` + `answer_model=="guardrail-blocked"` → input-side
block (no generation happened). `guardrail_blocked=True` + `answer_model` a real
model name → output-side block (generation happened, answer replaced). The two
cases are mutually exclusive by construction, not by convention: an
input-blocked query's graph path (`guardrail_router` → `audit_logger` directly)
never visits any answer node or the new output node at all (see Decision 5's
edge list), so nothing can ever set both sentinels on the same request. No new
field, no new naming scheme, no risk of the two stages' events colliding.

---

## Decision 5 — Graph wiring: one node, zero new routers

Node, mirroring `guardrail_input_node`'s None-passthrough and fail-open shape
(`graph.py:327-357`):

```python
def guardrail_output_node(
    state: GraphState, *, output_guard: Callable[[str, str, str], dict[str, Any]] | None
) -> dict:
    """Node 7.5: offline output rail, local_llm path only in this cut (Decision 2).

    Runs AFTER generation, unlike guardrail_input_node -- the answer already
    exists, so a block here REPLACES it rather than skipping a call. Every
    inbound edge still reaches audit_logger next regardless of verdict: there
    is no conditional edge here because the verdict changes what the next
    node LOGS, never WHICH node runs next.
    """
    if output_guard is None or state.get("answer_model") != "local":
        return {}

    docs = state.get("retrieved_docs", [])
    context = "\n\n".join(d.get("text", "") for d in docs)  # matches guardrail_safety_node's own precedent, integration.py:337-338

    try:
        result = output_guard(state.get("query", ""), state.get("answer", ""), context)
    except Exception:
        logger.warning("output_guard raised; failing open (answer returned as generated)", exc_info=True)
        return {}

    if not result.get("blocked"):
        return {}

    return {
        "answer": result.get("message", ""),
        "answer_sources": [],
        "guardrail_blocked": True,
        "guardrail_rails": result.get("rails", []),
    }
```

Edges — replace the four existing `<node> -> audit_logger` unconditional edges
with:

```python
graph.add_node("guardrail_output", partial(guardrail_output_node, output_guard=output_guard))

graph.add_edge("local_llm", "guardrail_output")
graph.add_edge("grok_fallback", "guardrail_output")
graph.add_edge("claude_fallback", "guardrail_output")
graph.add_edge("offline_best_effort", "guardrail_output")
graph.add_edge("guardrail_output", "audit_logger")
```

All four answer paths funnel through the one node (rather than inserting it
only on `local_llm`'s edge) even though Phase 4a's check only fires for
`local_llm` — so that Decision 2's *scope* can widen later (if a Phase 4b ever
adds a check applicable to another path) without a second graph-wiring PR; only
`guardrail_output_node`'s body would need to change. It also nets fewer total
edges than the alternative (one node + 5 edges vs. three untouched edges + a
2-edge detour on only one).

**Why this does not violate I2 (topology = policy).** I2's rule is about
*routing* — which node the graph traverses next — never being decided by a
runtime `if` outside a router function. The `if ... != "local"` check inside
`guardrail_output_node` decides **what data to hand a check function**, not
which node runs next: every request reaches `audit_logger` immediately
afterward regardless of the branch taken or the check's verdict. This is not a
novel argument invented for this design — the exact same shape already exists,
unremarked, in this same file: `_external_fallback_node` (`graph.py:444`)
computes `send_ctx = fallback_cfg.get(...)` then
`docs = state.get("retrieved_docs", []) if send_ctx else []` — an internal `if`
that decides prompt content, with zero effect on which edge is traversed. If
that is not an I2 violation (and `invariant-guard`/`test_graph` have never
flagged it as one), neither is this.

No change to `COND_SOURCE_ROUTERS` — zero new router functions. `guardrail_router`,
`score_router`, and `user_gate_router` are untouched.

---

## Invariant analysis

| # | Invariant | Verdict | Reasoning |
|---|---|---|---|
| I1 | RAG-first | **Untouched** | `retrieve` remains the unconditional entry point; nothing here runs before it. |
| I2 | Topology = policy | **Untouched, argued explicitly** | See Decision 5 — no conditional edge, no router; the branch is internal node logic identical in kind to existing code in the same file. |
| I3 | Triple-gated external fallback | **Untouched** | No change to any condition guarding `grok_fallback`/`claude_fallback`; the new node runs strictly after those gates have already been evaluated and only ever no-ops on those two paths in this cut. |
| I4 | Audit convergence | **Holds, mechanically** | `check_invariants.py`'s `reaches_audit()` DFS (`:402-410`) is recursive over the edge adjacency map it builds from `EXPECTED_UNCONDITIONAL_EDGES` + `COND_SOURCE_ROUTERS` targets — it does not hardcode hop count. The 8-node upstream root set at `:399-400` needs no edit: `local_llm`/`grok_fallback`/`claude_fallback`/`offline_best_effort` still reach `audit_logger`, now via one extra hop through `guardrail_output`, which the existing recursion already handles. |
| I5 | Soul governance | **Untouched** | No `soul.md` path touched; `check_output` never imports `utils/personality.py`. |
| I6 | Module isolation | **Preserved by construction** | `graph.py` still never imports `guardrails` — `output_guard` is an injected `Callable`, built by `utils/guardrail_bridge.py` exactly as `input_guard` already is. `tests/test_guardrails_isolation.py` covers both directions today and needs no new exemption. |

---

## `invariant-guard` and test deltas (would ride in the same PR as the code)

- `.claude/skills/invariant-guard/check_invariants.py`:
  - `EXPECTED_NODES` (`:41-44`): add `"guardrail_output"`.
  - `EXPECTED_UNCONDITIONAL_EDGES` (`:45-52`): drop the four
    `(<node>, "audit_logger")` tuples for `local_llm`/`grok_fallback`/
    `claude_fallback`/`offline_best_effort`; add
    `("local_llm", "guardrail_output")`, `("grok_fallback", "guardrail_output")`,
    `("claude_fallback", "guardrail_output")`,
    `("offline_best_effort", "guardrail_output")`,
    `("guardrail_output", "audit_logger")`. Net: 6 → 7 entries.
  - `COND_SOURCE_ROUTERS` (`:58-62`): **no change** — `guardrail_output` has no
    conditional edges.
  - The I4 root node set at `:399-400`: no change needed (see Invariant table
    above) — but re-run the script and read its output rather than trusting
    this document, per its own standing instruction.
- `tests/test_graph.py`: any test asserting the compiled graph's raw edge count
  or invoking `build_graph()` end-to-end with `guardrails.enabled: true` and a
  mocked `LocalLLMClient` whose canned answer/context don't share tokens would
  need updating — the new rail (when enabled) would now legitimately flag such
  a fixture as ungrounded. Fixtures under the shipped `enabled: false` default
  are unaffected (`output_guard` is `None`, pure pass-through — same protection
  Phase 2 already relies on).
- `tests/test_guardrails_integration.py`: new tests for `check_output`, mirroring
  the existing `check_input` test shape in the same file.
- `tests/test_guardrail_bridge.py`: new tests for `build_output_guard`, mirroring
  the existing `build_input_guard` tests in the same file.
- `tests/test_guardrails_isolation.py`: no new exemption needed; re-run to
  confirm.
- `docs/NeMo/README.md`'s "Status" table (`:33-37`) already understates current
  state (still shows Phase 2 as "Next," though it shipped) — a pre-existing
  drift this document does not fix, flagged here only so a future PR doesn't
  compound it further.

---

## PR file manifest (if approved)

| File | Change |
|---|---|
| `guardrails/integration.py` | add `check_output()` |
| `utils/guardrail_bridge.py` | add `build_output_guard()` |
| `graph.py` | add `guardrail_output_node`, register the node, replace 4 edges with 5 |
| `gate.py` | add `output_guard = build_output_guard(cfg)`, thread into `build_graph(..., output_guard=output_guard)` — mirrors `input_guard`'s existing two-line wiring at `gate.py:504,510` |
| `.claude/skills/invariant-guard/check_invariants.py` | `EXPECTED_NODES` + `EXPECTED_UNCONDITIONAL_EDGES` deltas above |
| `tests/test_guardrails_integration.py` | `check_output` tests |
| `tests/test_guardrail_bridge.py` | `build_output_guard` tests |
| `tests/test_graph.py` | new node/edge coverage + any fixture updates the enabled-path needs |

No `config.yaml` change — reuses the existing `guardrails.enabled` flag. No new
dependency.

---

## Verification (for the future code PR, not run against this document)

```bash
# Both isolation suites
GROK_API_KEY=dummy pytest tests/test_agentic_isolation.py tests/test_guardrails_isolation.py -q

# Static invariants -- MUST show the updated node/edge counts, not just exit 0
python3 .claude/skills/invariant-guard/check_invariants.py

# The graph + guardrails suites
GROK_API_KEY=dummy pytest tests/test_graph.py tests/test_guardrails_integration.py tests/test_guardrail_bridge.py -q

# Full suite + lint
ruff check --select E,F,I,B,C4,UP,S .
GROK_API_KEY=dummy pytest tests/ -q --tb=short
```

Runtime probe (requires a built index + Ollama reachable):
1. `enabled: false` (shipped default) — behavior byte-identical to `main` today;
   `output_guard is None` on every request.
2. `enabled: true` + a `local_llm` query whose mocked/real answer shares no
   tokens with its retrieved context — expect HTTP 200, `answer_model` still
   `"local"`, `answer` replaced with `block_message`, `guardrail_blocked: true`,
   `guardrail_rails: ["check_grounding"]`, one `check_grounding`-rail blocked
   event in `logs/guardrails.jsonl` (hash only), and a converged audit event in
   `logs/audit.jsonl`.
3. Same config, an `offline_best_effort` or external-fallback query with weak/no
   context — expect the answer to pass through **unchecked** (proving the scope
   exclusion in Decision 2 actually holds at runtime, not just on paper).

---

## Open questions for the owner

- [ ] **Approve Phase 4a as scoped** (grounding-only, `local_llm` path only), or
      request a different scope before any code is written?
- [ ] **Confirm `check_soul_leak` stays deferred**, pending a dedicated
      false-positive sweep against real model output (Decision 2) — or is there
      an appetite to fund that sweep as a prerequisite now?
- [ ] Should the four-answer-path wiring (all funnel through `guardrail_output`,
      Decision 5) be preferred over wiring the node onto `local_llm`'s edge
      only? This document recommends the former for forward-compatibility and
      fewer total edges, but it is a judgment call, not a forced conclusion.

---

## References

- `remaining_work.md` item #2 — the finding this document resolves.
- `docs/NeMo/phase3_implementation_plan.md:176-263` — the redirect that deferred
  this work and the open question this document answers.
- `docs/NeMo/phase2_implementation_plan.md` — the input-rail precedent this
  design mirrors throughout (inversion shim, sync-only offline check, graph
  wiring shape, invariant-table format).
- `guardrails/integration.py`, `guardrails/rails.py`, `utils/guardrail_bridge.py`,
  `graph.py` — cited by line above; re-read before implementing, this document
  is a snapshot of `main` @ `8b19822`.
