# Proposed CyClaw-Tailored Skills & Loops

Suggestions for `.claude/skills/` additions specific to CyClaw's three
invariants — **RAG-first retrieval**, **LangGraph topology as security policy**,
**offline-first** — rather than generic refactor loops. Ranked by leverage.

> **Status (2026-07):** #1 `invariant-guard`, #2 `injection-redteam`, and #4
> `index-doctor` are now **implemented** under `.claude/skills/`, each with a
> deterministic checker/runner and a self-testing `verify.sh`. A fourth new
> skill, **`doc-sync`** (code↔docs drift detector — not originally in this
> list), was added alongside them. Still open: #3 `cve-triage`, #5
> `release-cut`.

> Existing skills today: `architecture-refactor`, `logging-refactor`,
> `speed-refactor`, `tests-refactor` (loops); `run-cyclaw`, `wrap-up`,
> `invariant-guard`, `injection-redteam`, `index-doctor`, `doc-sync` (task/check).

---

## 1. `invariant-guard` 🔴 highest leverage — ✅ IMPLEMENTED

**Trigger:** before merging any change to `gate.py`, `graph.py`, `llm/`,
`retrieval/`, or `sync/`; or on demand ("check invariants").

**Purpose:** assert CyClaw's three invariants still hold after a diff. Concretely:
- **Topology-as-policy:** every graph path terminates through the audit node; no
  edge bypasses the user-confirmation gate before an online (Grok) call; the
  sanitizer runs on every inbound query path (HTTP *and* MCP).
- **Offline-first:** no new unconditional outbound network call; LM Studio /
  Grok remain optional and gated.
- **RAG-first:** no path answers from the LLM without first attempting retrieval.

**Why a skill:** these are exactly the properties that generic test/lint loops
miss because they live in the *graph wiring*, not in any single unit. This is the
single highest-value addition given "topology as security policy."

**Stop criteria:** all invariant assertions pass against the current diff;
findings reported with the offending edge/file.

---

## 2. `injection-redteam` 🟡 (loop) — ✅ IMPLEMENTED

**Trigger:** "redteam the sanitizer", or on changes to `utils/sanitizer.py` /
`config.yaml` injection patterns.

**Purpose:** iteratively probe `PromptInjectionError` coverage with known
jailbreak/injection families (instruction-override, role-swap, encoded payloads,
"add to your knowledge base" memory-poisoning), add any bypass found as a new
blocklist pattern + regression test, repeat until a probe corpus is fully caught.

**Why a skill:** the sanitizer is a security boundary; adversarial coverage decays
as new bypass techniques emerge. A loop keeps it current. Pairs with the existing
`tests-refactor` loop but is threat-model-driven rather than coverage-driven.

**Stop criteria:** 100% of the probe corpus blocked; each new pattern has a test.

---

## 3. `cve-triage` 🟡

**Trigger:** Dependabot/pip-audit opens a CVE PR, or "triage this CVE".

**Purpose:** evaluate a new dependency CVE against CyClaw's documented
risk-acceptance model (the chromadb `CVE-2026-45829` precedent in
`pip-audit.yml`: embedded-mode-only, no HttpClient, no network surface, telemetry
killed). Produce a verdict: patch / pin / accept-with-rationale, and if accepted,
draft the `--ignore-vuln` entry + justification comment in the established format.

**Why a skill:** the project already has a rigorous, idiosyncratic CVE-acceptance
style; encoding it as a skill makes triage consistent and auditable instead of
re-derived each time.

**Stop criteria:** every open CVE has a documented verdict consistent with the
invariants.

---

## 4. `index-doctor` 🟢 — ✅ IMPLEMENTED

**Trigger:** "rebuild/validate the index", retrieval-quality complaints, or
corpus changes under `data/corpus/`.

**Purpose:** rebuild ChromaDB + BM25, validate chunk counts, surface
near-duplicate or empty chunks, and run a fixed query set through RRF to confirm
`min_score` gating and fusion still behave. Wraps the work `run-cyclaw` does
manually into a focused retrieval-health check.

**Stop criteria:** index builds clean; fixed-query smoke produces expected
score/route behavior.

---

## 5. `release-cut` 🟢

**Trigger:** "cut a release" / "bump version".

**Purpose:** the project ships console scripts and is versioned (v1.4.0). A skill
to bump `pyproject.toml`, update a CHANGELOG, sanity-check the four console
entry points (`cyclaw-server/index/metrics/mcp` — the subject of PR #87), run the
reproducible-install gate, and tag.

**Stop criteria:** version bumped, entry points import-verified, install gate green.

---

## Recommendation

Build **#1 `invariant-guard` first** — it protects the property the whole project
is organized around and that nothing currently checks automatically. **#2
`injection-redteam`** is the natural second (security boundary upkeep). #3–#5 are
convenience/consistency wins to add as the need arises.
