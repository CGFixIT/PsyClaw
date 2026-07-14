# Planning — PR #

 Bug Fixes

**Target branch:** `claude/bug-fixes-pr-main-67d7wr` → merge into `main`
**Verified against main HEAD:** `611118d` (PR #118 assessed against stale `453afcb`; line numbers below are re-verified against current code)
**Status:** Awaiting human approval before any commit.

---

## Independent Verification of the 8 Findings

### ✅ #1 — CRITICAL: `chunk_size=0` corrupts index — **VALID**
- **Location:** `retrieval/indexer.py:49` (`chunk_document`)
- **Confirmed:** With `chunk_size=0`, `end = min(start+0, len) = start`, so every iteration appends `" ".join(words[start:start]) == ""`. `step = max(1, 0-50) = 1`, so the loop runs `len(words)` times producing `len(words)` empty-string chunks → ChromaDB indexes garbage. No guard exists.
- **Fix:** Add fail-fast validation in `build_index()` (after reading config, before chunking):
  ```python
  if chunk_size < 1:
      raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
  ```

### ✅ #2 — HIGH: `overlap >= chunk_size` → chunk explosion / OOM — **VALID**
- **Location:** `retrieval/indexer.py:55`
- **Confirmed:** `step = max(1, chunk_size - overlap)`. With `overlap=512, chunk_size=512`, `step=1`, so a 100K-word corpus yields ~100K chunks instead of ~216. Clamp prevents the infinite loop but trades it for memory blow-up. No upstream reject.
- **Fix:** Extend the guard in `build_index()`:
  ```python
  if chunk_overlap >= chunk_size:
      raise ValueError(f"chunk_overlap ({chunk_overlap}) must be < chunk_size ({chunk_size})")
  ```
- **Note:** Validation goes in `build_index()` (config boundary), keeping `chunk_document`'s defensive `max(1,…)` clamp as defense-in-depth for direct callers.

### ❌ #3 — Undefined `RetrievedDoc` type hint — **DEBUNKED / NOT APPLICABLE**
- **The "Langgrinch" the user suspected.** `RetrievedDoc` IS defined as a `TypedDict` at `graph.py:55` and used correctly in `_format_context_chunks` (`graph.py:107`). No bug. **Dropped — no action.**

### ⚠️ #4 — MEDIUM: Redundant `fastapi` import — **VALID but cosmetic (line drifted to 91)**
- **Location:** `gate.py:55` (`FastAPI, HTTPException, Depends`) + `gate.py:91` (`Request`) — PR said line 97; actual is 91.
- **Confirmed:** Two separate `from fastapi import` statements. The second is intentionally placed beside the rate-limiter block with an explanatory comment. Not a bug; purely stylistic.
- **Fix (optional):** Add `Request` to the line-55 import; delete the standalone `from fastapi import Request` at line 91 (keep the rate-limiter comment + `RateLimiter` import).

### ✅ #5 — Silent clamping warning — **SUPERSEDED by #2**
- Once #2 raises `ValueError`, the misconfig can never reach the silent `max(1,…)` clamp. **No separate code — fold into #2. Do not add a WARN print.**

### ✅ #6 — `chunk_document` edge cases untested — **VALID**
- **Confirmed:** Zero test files reference `chunk_document`.
- **Fix:** New `tests/test_indexer.py`:
  - `test_chunk_document_empty_text()` → `chunk_document("") == []`
  - `test_chunk_document_normal()` → known input → expected chunk count
  - `test_build_index_rejects_zero_chunk_size()` → `ValueError` (validates #1)
  - `test_build_index_rejects_overlap_ge_chunk_size()` → `ValueError` (validates #2)

### ⚠️ #7 — PR refs in test comments — **PARTIALLY VALID (low priority)**
- **Confirmed:** `tests/test_audit.py:87` and `:141` cite `"PR #99 #10"`. PR's claimed lines (88, 101, 139) are off; line 101 has **no** PR reference.
- The "CLAUDE.md violation" justification is weak — the current `CLAUDE.md` contains no rule against PR refs in comments. Treat as optional hygiene, not a correctness fix.
- **Fix (optional):** Rewrite the two comments as timeless explanations (drop the `PR #99 #10` prefix).

### ✅ #8 — `grok_fallback_node` prompt structure untested — **VALID**
- **Confirmed:** No test references `grok_fallback_node`. It calls `_format_context_chunks(docs, limit=3, char_cap=200)` (`graph.py:260`), emitting `[Source: …, Score: …]` headers when `send_local_context_to_grok=True`.
- **Fix:** Add to `tests/test_graph.py`: mock `GrokClient`, call with `send_local_context_to_grok=True` + sample docs, assert the captured prompt contains `"[Source:"` and `"Score:"` and that an answer is returned.

---

## Proposed Scope (recommendation)

| Priority | Findings | Rationale |
|---|---|---|
| **P1 — Correctness (must)** | #1, #2 (#5 folded in) | Real bugs: index corruption + OOM |
| **P2 — Tests (should)** | #6, #8 | Lock in P1 + cover untested prompt path |
| **P3 — Hygiene (optional)** | #4, #7 | Cosmetic; no behavior change |
| **Dropped** | #3 | Debunked — code is correct |

## Files to Change
- `retrieval/indexer.py` — validation guards in `build_index()` (#1, #2/#5)
- `tests/test_indexer.py` — **new**, edge-case + validation tests (#6)
- `tests/test_graph.py` — grok prompt-structure test (#8)
- `gate.py` — consolidated import (#4, optional)
- `tests/test_audit.py` — comment cleanup (#7, optional)

## Validation
```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short
python -c "import gate"   # if #4 applied
```

## Workflow (per user instruction + CLAUDE.md)
1. **Hold for human approval** (this document) — no commits yet.
2. On approval: set git identity, implement on `claude/bug-fixes-pr-main-67d7wr`.
3. Run full test suite.
4. Push branch; open a **draft PR** into `main` describing fixes + dropped findings (#3, and #5 superseded).
5. Do **not** push directly to `main` via GitHub MCP (avoid add/add rebase conflicts).
