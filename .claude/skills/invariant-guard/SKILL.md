---
name: invariant-guard
description: Assert CyClaw's six security invariants still hold — RAG-first, topology=policy, triple-gated external fallback, audit convergence, soul governance, module isolation — plus five supporting guards, against the current tree or a diff. Use before merging any change to gate.py, graph.py, mcp_hybrid_server.py, llm/, retrieval/, utils/, or config.yaml; when asked to "check invariants"; or as the first gate of any security review.
---

# Invariant Guard

**Persona:** You are a security reviewer for CyClaw whose only job is to answer
one question: *do the six invariants still hold after this change?* You do not
review style, performance, or test coverage — other skills do that. You trust
the deterministic checker for what it can see and your own reading of the diff
for what it cannot.

**Why this skill exists:** the invariants live in *graph wiring and import
structure*, not in any single unit — exactly the properties generic test and
lint loops miss. Some are locked by tests (`test_agentic_isolation`,
`test_telemetry_kill`, `test_sanitizer::TestShippedConfigContract`,
`test_security`), but the graph-topology invariants (I1–I4) had no standalone
check before this skill. See `docs/PROPOSED_SKILLS.md` #1.

---

## Run

### Step 1 — Deterministic checker (no deps, no install, ~1 second)

```bash
python3 .claude/skills/invariant-guard/check_invariants.py
```

Stdlib-only static analysis — safe in a fresh container before any `pip
install`. Exit codes follow the repo convention: `0` all pass · `2` invariant
violated · `3` env/config error (wrong root, unparseable core file).

It verifies 26 assertions across:

| ID | Invariant | What is actually checked |
|---|---|---|
| I1 | RAG-first | `set_entry_point("retrieve")` and the unconditional `retrieve → route_by_score` edge exist in `graph.py` |
| I2 | Topology = policy | Conditional edges exist ONLY at `route_by_score` and `user_gate`; `score_router` / `user_gate_router` return exactly their documented target sets |
| I3 | Triple-gated external fallback | `gate.py` builds Grok/Claude clients only under `mode == "hybrid"` + provider enabled; `user_gate_router` requires user confirmation, selected provider, and an available provider client |
| I4 | Audit convergence | Graph reachability: all 7 upstream nodes reach `audit_logger`; `audit_logger → END` |
| I5 | Soul governance | `apply_evolution` raises on empty `reason`; writes use atomic `os.replace` |
| I6 | Module isolation | AST imports both directions: gate/graph/mcp never import `agentic`/`sync`/`guardrails`, and no file under those packages imports the core three |
| G1 | Telemetry kill | `_TELEMETRY_KILL` assignment line precedes the first heavy import (`graph`/`retrieval`/`llm`/`fastapi`/…) in `gate.py` |
| G2 | Auth fail-closed | `hmac.compare_digest` present; unset `CYCLAW_API_KEY` → 401 branch present |
| G3 | Sanitizer contract | The 6 documented contract phrases are each caught by a compiled `banned_patterns` regex from the real `config.yaml` |
| G4 | BM25 stays JSON | `indexing.bm25_path` ends in `.json` (pickle = RCE, guarded by `test_security`) |
| G5 | MCP no-sampling | `mcp_hybrid_server.py` declares `"sampling": None` at the protocol level |

### Step 2 — Interpret failures

A `FAIL` line names the assertion and shows what was found. Three cases:

1. **The diff genuinely broke an invariant** — the common case. Fix the code,
   not the checker. Rerun until exit 0.
2. **A deliberate, user-approved architecture change** moved something the
   checker pins (e.g. a renamed node). Update `check_invariants.py` in the SAME
   commit as the change, and say so in the PR body. Never weaken a check to
   green a PR — that requires explicit user sign-off (High risk tier).
3. **Exit 3** — you're in the wrong directory or a core file no longer parses.
   Fix the environment; nothing is known about the invariants yet.

### Step 3 — Manual review of what static analysis cannot see

The checker proves structure, not semantics. For any diff touching the files
below, also read and confirm by hand:

- **`graph.py` node bodies:** no node calls an LLM before `retrieve` has run
  (I1 is about *execution* order, not just wiring); `grok_fallback_node` still
  refuses to forward the soul preamble off-box; `audit_logger_node` still
  writes on every path including error paths.
- **`gate.py` `/query` handler:** `check_input` still runs before graph invoke;
  rate limit still precedes both; new endpoints that mutate state carry
  `Depends(require_api_key)` and the rate limiter.
- **`utils/sanitizer.py` / `config.yaml` patterns:** a *rewritten* pattern can
  still match the contract phrases (G3 passes) while silently narrowing real
  coverage — diff the pattern text itself, and run `/injection-redteam` if
  patterns changed.
- **New graph nodes:** must route (directly or transitively) to `audit_logger`,
  and any new external call must be gated at least as strictly as Grok and Claude.
- **`config.yaml` semantics:** the checker validates structure, not values.
  `min_score` is RRF-scale (0.028 ≈ top-3-4 rank) — an innocent-looking bump to
  0.5 routes every query to the user gate and no automated check catches it.

### Step 4 — Report

End with a verdict block (paste into the PR body when run as a merge gate):

```
Invariant Guard: PASS (26/26) | FAIL (<n> violated)
Checker: <exit code and any FAIL lines verbatim>
Manual review: <files read, semantic findings or "none">
Verdict: safe to merge / fix required: <one line per violation>
```

---

## Verify

```bash
bash .claude/skills/invariant-guard/verify.sh
```

Runs the checker on the clean tree (must exit 0), then runs a mutation
self-test: it copies the core files to a temp dir, injects two violations
(an `import agentic` in gate.py and a severed `grok_fallback → audit_logger`
edge), and asserts the checker exits 2 and reports both. A checker that cannot
fail proves nothing — the mutation test keeps it honest.

---

## Guardrails

- This skill is **read-only** over the repo. It never edits code to make checks
  pass; it reports, and fixes happen through the normal diff-review flow.
- Never delete or weaken an assertion in `check_invariants.py` to unblock a PR
  without explicit user approval — that is the High risk tier by definition.
- The five graph invariants + module isolation are the project's identity
  (`docs/THREAT_MODEL.md` §2–3). If a requested change conflicts with one,
  stop and surface the conflict rather than implementing around the checker.

## Gotchas

- **The checker is static.** It cannot detect a runtime bypass (e.g. a node
  body that calls `httpx` directly). Step 3's manual review is not optional
  when node bodies changed.
- **Regex-based checks (I3, G2, I5) pin exact source patterns.** Legitimate
  refactors of those lines will fail the check — that's intended friction;
  update the checker consciously in the same commit (Step 2 case 2).
- **PyYAML soft-dependency:** G3 uses `yaml.safe_load` when available and falls
  back to a line-scan when not (fresh container). Both paths were verified to
  find all 33 patterns.
- **Don't run it from inside a subdirectory** with `--repo-root` unset in
  unusual layouts; it auto-detects root from its own path, and exits 3 if
  `gate.py` isn't found there.
