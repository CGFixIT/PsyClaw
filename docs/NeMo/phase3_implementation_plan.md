---
title: "NeMo Guardrails — Phase 3 Plan (redirect to agentic/ and harness/)"
date: 2026-07-27
tags: [guardrails, nemo, agentic, harness, security, plan]
source: "planning session vs main @ d863494"
related:
  - docs/NeMo/later_development_guideline.md
  - docs/NeMo/phase2_implementation_plan.md
  - docs/agentic/AGENTIC_README.md
  - docs/HARNESS_POWERSHELL.md
  - guardrails/rails.py
  - agentic/registry.py
---

## Summary

This document redirects **Phase 3** of the NeMo Guardrails roadmap away from the
RAG query path and toward `agentic/` and `harness/`. It is a plan, not an
approved change.

The roadmap in `docs/NeMo/later_development_guideline.md` assumed Phase 3 meant
"add output rails to the graph." Auditing the shipped code against that
assumption on 2026-07-27 surfaced a mismatch worth fixing before writing any
code: **the guardrails layer is currently deployed where a prompt injection is
least dangerous, and is absent from the paths where one is most dangerous.**

Phase 3 as redirected has one organising goal: make `guardrails/` the shared home
for injection/abuse scanning that `agentic/` has already re-implemented three
times, and extend that coverage to the PowerShell harness, which has none of its
own.

---

## Finding 1 — the capability asymmetry

Prompt injection matters in proportion to what the model's output can *do*. The
four surfaces in this repo differ enormously on that axis, and the guardrails
coverage runs backwards to it:

| Surface | Input | What output can do | Injection guard today |
|---|---|---|---|
| `/query` (gate → graph) | Operator typing at a loopback terminal | Return text; append an audit line | `utils/sanitizer.py` (32 patterns, fail-closed) **plus** the Phase 2 guardrails input rail |
| `agentic/registry.py` | Skill definitions sourced from GitHub | Mutate the governed skills registry — decides which capabilities exist | Own scanner, enforcing |
| `agentic/fsconnect/` | File contents from local/SMB shares | **Write files**, trash/quota operations | Own scanner, explicitly labelled *advisory* (`agentic/fsconnect/client.py:50`) |
| `harness/` (PowerShell console, `127.0.0.1:8790`) | Operator slash-commands; model output stays in chat, never reaches `agentic/` | Today: `run_agentic_op("status")` only — a hardcoded action, zero caller-controlled arguments (`harness/server.py:264`) | N/A today (see Finding 3 — nothing to guard yet) |

The `/query` path is the only one that is double-guarded, and it is the only one
whose worst case is "the operator reads a sentence they did not want." The paths
that mutate a capability registry or write to a filesystem carry one guard each,
one of which is advisory.

Threat-model note: `docs/THREAT_MODEL.md` scopes CyClaw to a single operator on
loopback, which is what makes the `/query` path genuinely low-risk — the operator
attacking their own terminal is not a threat. That same reasoning does **not**
extend to `agentic/`, which ingests content the operator did not author: GitHub
skill definitions and files from shares.

---

## Finding 2 — the same scanner is implemented four times, and guardrails has the weakest copy

`OWASP_INJECTION_PATTERNS` (13 patterns) is defined once, in
`utils/personality.py:71`. Three `agentic/` modules import it and each rebuilds
its own compile-and-scan layer around the union with `config.yaml`'s
`policy.prompt_filter.banned_patterns` (32 patterns) — **37 patterns after
deduplication**:

- `agentic/registry.py:41` — imports it; `_build_injection_patterns()` /
  `_scan_injection()` at lines 174 and 215, with its own uncompilable-pattern
  logging
- `agentic/fsconnect/client.py:27` — imports it; `build_injection_patterns()` at
  line 50
- `agentic/harness_optimizer/governance.py:14` — imports it; own compile at
  line 92

`guardrails/rails.py` — the module whose entire stated purpose is this — imports
**none** of it. Its only imports are `re` and `collections.abc.Iterable`
(lines 20-21). Its injection scan is 7 hardcoded substrings
(`_INJECTION_MARKERS`, lines 63-71), matched by `str.__contains__`, with no
config input and no OWASP set.

So the scanner wired into the graph checks against 7 literal strings, while the
scanners guarding filesystem writes and registry mutation check against 37
regexes. The weakest implementation is the one carrying the "guardrails" name.

Consolidation is the obvious win, and it is not a new capability: it is moving
logic `agentic/` already runs into the module that should own it.

---

## Finding 3 — 3B resolved: the harness has no live attack surface to guard yet

This section corrects Finding 1's original table, which described `harness/` as
reaching `agentic.cli` via the `ops_runner` shim carrying "operator slash-commands
plus model output." That was true of the wiring documented in
`docs/HARNESS_POWERSHELL.md`, but not of what the code in `harness/server.py`
currently does with it — verified by reading the module, not inferred from the
docs describing it.

`harness/server.py` has exactly **one** call into `agentic/`:
`run_agentic_op("status")` (line 264) — a hardcoded action string, zero
caller-controlled arguments. `/api/chat`, the only endpoint that accepts free
text, never calls `ops_runner` at all: it is LLM-in, LLM-out, recorded to a
session JSON file, with no write capability downstream. `/api/registry` and
`/api/harness/runs` are read-only (`harness/registry_view.py` parses
`SKILL.md` frontmatter and lists a directory; neither imports `ops_runner`).
`harness/schemas.py` already applies `extra="forbid"` and a length cap
(32KB) to the one free-text field that exists (`ChatRequest.message`).

**Conclusion: no harness-side scanner is needed today.** There is no code path
by which a chat message or model output reaches `propose-skill`, `apply-skill`,
fsconnect, or sqlconnect. A pre-flight scan added now would guard nothing, since
nothing reaches the guarded operations yet. If the harness console is ever wired
to expose those write operations directly (rather than only `status`), revisit
this finding — at that point `run_agentic_op`'s existing validation (`name`/
`desc` required, non-empty `reason` for `apply-skill`) and `agentic/registry.py`'s
fail-closed scanner (consolidated onto `guardrails.rails` in 3A) already sit
behind that boundary and would need no new gate, only a live caller.

**3B is therefore complete as a decision, with no code change**, which is the
outcome the original open question named as legitimate rather than assumed.

---

## Why this direction is cheaper than the query-path plan it replaces

The original Phase 3 (a `guardrail_output` node in `graph.py`) requires changing
the audit-convergence topology that invariant **I4** asserts statically — adding
a node changes the count of upstream paths reaching `audit_logger`, so
`.claude/skills/invariant-guard/check_invariants.py` and `test_graph`'s edge
assertions both have to be updated and argued. That is a deliberate change to the
shape of a security invariant.

The redirected Phase 3 requires **no invariant change at all**, because the import
direction it needs is already legal:

- `tests/test_guardrails_isolation.py:60-65` forbids `guardrails` → `{agentic, sync}`
- `tests/test_agentic_isolation.py:74-86` forbids `agentic` → `{gate, gate_ops, graph, mcp_hybrid_server}`

Neither test forbids **`agentic` → `guardrails`**. The dependency runs from the
higher-capability out-of-band layer into the shared safety module, which is the
direction that was always going to be correct, and the isolation suite already
permits it verbatim.

Verify before relying on it: re-run
`GROK_API_KEY=dummy pytest tests/test_agentic_isolation.py tests/test_guardrails_isolation.py -q`
after any consolidation commit, since a careless import added in the wrong
direction is exactly what those suites exist to catch.

---

## Proposed Phase 3 scope

**3A — consolidate the injection scanner into `guardrails/`.**
Give `guardrails/rails.py` a real scanner: the `OWASP ∪ banned_patterns` union
(37 patterns) that `agentic/` already trusts, replacing the 7 hardcoded markers.
Have `agentic/registry.py`, `agentic/fsconnect/client.py`, and
`agentic/harness_optimizer/governance.py` import it instead of each rebuilding
one. Behaviour-preserving for `agentic/` by construction (same pattern set, same
verdicts); a strict upgrade for `guardrails/`. This is the piece that makes
everything after it worth doing.

**3B — close the `harness/` gap. RESOLVED, no code needed (see Finding 3).**
Verified by reading `harness/server.py`, `harness/schemas.py`, and
`harness/registry_view.py`: the harness has no live call path into any
write-capable `agentic/` operation today, so there is nothing for a harness-side
scanner to guard. No PR for this piece. Revisit if the console is later wired to
expose `propose-skill` / `apply-skill` / fsconnect / sqlconnect directly.

**3C — promote the fsconnect scanner from advisory to enforcing (needs a decision).**
`agentic/fsconnect/client.py:50` labels its scanner *advisory*. Filesystem writes
are the highest-capability operation in the repo. Whether that stays advisory is
an operator risk decision, not a code cleanup, and it should be made explicitly
rather than inherited from a comment. Out of scope for the consolidation PR.

**Deferred from this plan: query-path output rails.** The `guardrail_output` node
work is not cancelled, only re-ranked below the above. One query-path item does
survive as an independent bug fix, described in the next section.

---

## The one query-path item that still stands on its own

Independent of the redirect: the Phase 2 input rail sits only on the
`route_by_score` → `local_llm` edge, so an injection or soul-mutation payload is
blocked when it retrieves well and answered when it retrieves poorly. Scoring
above or below `retrieval.min_score` (`0.028`, `config.yaml`) is orthogonal to
whether a query is hostile. `graph.py:850-855` already records this as a KNOWN
GAP.

This is a real inconsistency and a legitimate bug fix under FEATURE FREEZE
(`CLAUDE.md` §1), but it is a graph-edge change and therefore still High tier
(`CLAUDE.md` §7). It can proceed on its own schedule, before or after Phase 3,
and should not be bundled with the consolidation work.

---

## Invariant analysis for the redirected scope

Evaluated against Phase 3A (consolidating the scanner into `guardrails/` and
importing it from `agentic/`):

| # | Invariant | Verdict | Reasoning |
|---|---|---|---|
| I1 | RAG-first | **Untouched** | No change to `graph.py`; `retrieve` remains the unconditional entry point. |
| I2 | Topology = policy | **Untouched** | No node added, no edge changed, no router introduced. |
| I3 | Triple-gated external fallback | **Untouched** | No change to the conditions guarding `grok_fallback` / `claude_fallback`. |
| I4 | Audit convergence | **Untouched** | The `audit_logger` convergence topology is not modified — this is the invariant the deferred query-path plan would have had to change. |
| I5 | Soul governance | **Preserved, with care** | `OWASP_INJECTION_PATTERNS` currently lives in `utils/personality.py`, which owns soul governance. Consolidation must not weaken the soul write-path scan that `PersonalityManager` performs; if the constant moves rather than being re-exported, `utils/personality.py` must keep scanning against the identical set. `test_personality` and `test_due_diligence_invariants` both pin this. |
| I6 | Module isolation | **Preserved** | `agentic` → `guardrails` is permitted by both isolation suites (see the import-direction section of this document). `gate.py` / `graph.py` / `mcp_hybrid_server.py` continue to name neither. |

The invariant that needs attention here is **I5**, not I4 — because the shared
pattern set currently lives inside the soul-governance module. The safest shape
is for `guardrails/` to import from `utils/personality.py` rather than to move
the constant, leaving the soul write-path scan byte-identical.

---

## Verification required for any Phase 3 code PR

Commands are the canonical ones from `CLAUDE.md` §8 — no invented invocations:

```bash
# Both isolation suites — the load-bearing check for this redirect
GROK_API_KEY=dummy pytest tests/test_agentic_isolation.py tests/test_guardrails_isolation.py -q

# Static invariants, with attention to I5 (soul write-path scan)
python3 .claude/skills/invariant-guard/check_invariants.py

# Adversarial probe of the consolidated scanner against the sanitizer corpus
python3 .claude/skills/injection-redteam/redteam.py

# Lint (CI-enforced) and the full suite
ruff check --select E,F,I,B,C4,UP,S .
GROK_API_KEY=dummy pytest tests/ -q --tb=short
```

A consolidation PR must additionally demonstrate **behaviour preservation for
`agentic/`**: the three call sites currently scan against the same 37-pattern
union, so the consolidated scanner must produce identical verdicts. A test that
asserts the pre- and post-consolidation pattern sets are equal is the cheapest
proof.

---

## Open questions to resolve before Phase 3 code

- [ ] **Does `guardrails/rails.py` import `OWASP_INJECTION_PATTERNS` from
      `utils/personality.py`, or does the constant move to a neutral home?**
      Importing is safer for invariant I5 (the soul write-path scan stays
      byte-identical); moving is cleaner but touches soul governance. Recommend
      importing. (Priority: high — blocks 3A)
- [x] **Does the PowerShell harness need its own pre-flight scan, or is the
      `utils.ops_runner` subprocess boundary the right and sufficient place?**
      Resolved 2026-07-27 (Finding 3): neither is needed yet — the harness has
      no live call path into any write-capable `agentic/` operation to guard.
      Revisit only if the console is later wired to expose one directly.
- [ ] **Should `agentic/fsconnect/`'s scanner stay advisory?** Filesystem writes
      are the highest-capability operation in the repo. An operator risk
      decision, not a cleanup. (Priority: medium)
- [ ] **Do the query-path output rails ever get built?** Deferred by this
      redirect, not cancelled. Revisit once 3A has landed and the consolidated
      scanner exists to back them. (Priority: low)

---

## References

- `docs/NeMo/later_development_guideline.md` — the original roadmap this document
  redirects (carries known current-state drift: it still describes the skeleton
  as unwired, which Phase 2 changed)
- `docs/NeMo/phase2_implementation_plan.md` — the input-rail node contract and
  the inversion-shim decision
- `docs/agentic/AGENTIC_README.md`, `docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md` —
  binding governance for the layer this redirect targets
- `docs/HARNESS_POWERSHELL.md` — the PowerShell harness contract
- `docs/THREAT_MODEL.md` — single-operator, loopback-bound scope; the basis for
  the capability-asymmetry argument
- `CLAUDE.md` §3 (the six invariants), §7 (risk tiers and escalation)
