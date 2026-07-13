---
name: config-guard
description: Statically validate CyClaw's config.yaml contract — the relational, value-safety, and threat-model invariants that boot-time validation and invariant-guard do not cover (graph_timeout > llm_timeout, chunk_overlap < chunk_size, the soul/context budget, loopback-only host, RRF-scale min_score, safe shipped posture). Use before merging any change to config.yaml, when asked to "check config" or "validate config", and as a cheap pre-boot gate in CI or a fresh clone.
---

# Config Guard

**Persona:** You are a configuration reviewer for CyClaw with one question:
*does config.yaml still honor the contract the running system assumes?* You do
not review code, topology, or docs — other skills do that. `config.yaml` is the
single source of truth (CLAUDE.md §1), and its load-bearing numbers are bound by
relations, not free knobs.

**Why this skill exists:** the dangerous config mistakes are *relational* and
*value-scale* — they parse fine, boot fine, and break behavior silently.
`utils/config_validation.py` runs at boot but only range-checks a handful of
values in two blocks. `invariant-guard` proves graph/import *structure*, and its
own SKILL.md flags the one this skill closes head-on: *"`min_score` is RRF-scale
(0.028 ≈ top-3-4 rank) — an innocent-looking bump to 0.5 routes every query to
the user gate and no automated check catches it."* This is that automated check,
plus the other relations nothing else pins.

---

## Run

### Step 1 — Deterministic checker (needs PyYAML, ~1 second)

```bash
python3 .claude/skills/config-guard/check_config.py
```

Static — it parses `config.yaml` and never imports the app (no torch/chromadb),
so it runs in a fresh clone the moment PyYAML is present. Exit codes follow the
repo convention: `0` contract holds · `2` a FAIL check tripped · `3` env/config
error (missing/unparseable `config.yaml`, or PyYAML absent).

Add `--strict` to escalate every `WARN` to a failure (use it as a merge gate
when you want the shipped defaults locked, not just the hard relations):

```bash
python3 .claude/skills/config-guard/check_config.py --strict
```

It checks (severity in brackets):

| ID | Severity | What is checked |
|---|---|---|
| C1 | FAIL | `retrieval.min_score` is a number in `[0, 1]` (a valid RRF score) |
| C2 | FAIL | `api.graph_timeout_sec` **>** `models.local_llm.timeout_sec`, with the documented ≥30s margin (else the graph is cut mid-LLM-call → orphaned invocation) |
| C3 | FAIL | `indexing.chunk_overlap` **<** `indexing.chunk_size` (else chunking never advances) |
| C4 | FAIL | `api.host` is loopback (`127.0.0.1`/`localhost`/`::1`) — the threat-model boundary |
| C5 | FAIL | `personality.soul_max_chars` is positive and **<** `retrieval.max_context_tokens*4` (soul cannot crowd out retrieved context) |
| C6 | FAIL | `api.rate_limit.max_requests` / `window_seconds` are positive integers |
| C7 | WARN | `retrieval.min_score` stays on the RRF scale (≤ 0.1) — **the acknowledged trap**; a stricter gate belongs in 0.05–0.08, never the cosine-scale 0.5 |
| C8 | WARN | `retrieval.rrf_k == 60` (the documented authoritative fusion constant) |
| C9 | WARN | shipped posture is safe: `app.mode == offline`, `grok`/`claude` disabled |
| C10 | WARN | `policy.fallback.send_local_context_to_{grok,claude}` stays `false` (no off-box context leak by default) |
| C11 | WARN | `guardrails.model`/`base_url` track the local LLM (config.yaml says keep in sync) |
| C12 | INFO | restates the no-stall floor: local `num_ctx` ≥ `max_context_tokens + max_tokens + 1500` |

### Step 2 — Interpret failures

A `FAIL` line names the check and shows the offending values. Three cases:

1. **The change genuinely broke the contract** — the common case. Fix
   `config.yaml`, not the checker. Rerun until exit 0.
2. **A deliberate, user-approved re-tune** moved a documented number (e.g. a
   stricter `min_score` in 0.05–0.08, a new `rrf_k`). Update the number AND the
   `config.yaml` comment AND CLAUDE.md §2 "Load-bearing numbers" in the SAME
   commit, and say so in the PR body. A `WARN` is exactly this: allowed, but
   flagged so it is conscious.
3. **Exit 3** — `config.yaml` is missing/unparseable or PyYAML is not installed.
   Fix the environment first; nothing is known about the contract yet.

### Step 3 — What the static check cannot see

The checker proves the numbers and their relations, not runtime behavior. When a
config change is semantically load-bearing, also:

- Run `/index-doctor` if you touched `chunk_size`/`chunk_overlap`/`top_k_*`/
  `rrf_k`/`min_score` — the retrieval quality probe is the real test that a
  re-tune helped rather than just parsed.
- Run `/injection-redteam` if you touched `policy.prompt_filter` (`banned_patterns`,
  `max_input_chars`) — C-checks do not judge pattern coverage.
- Run `/invariant-guard` for anything security-adjacent — it owns the structural
  guards (BM25 stays JSON, telemetry-kill order, MCP sampling) this skill
  deliberately does not re-check.

### Step 4 — Report

End with a verdict block (paste into the PR body when run as a merge gate):

```
Config Guard: PASS (0 fail / <n> warn) | FAIL (<n> fail)
Checker: <exit code and any FAIL/WARN lines verbatim>
Re-tune review: <numbers changed + docs updated, or "none">
Verdict: safe to merge / fix required: <one line per FAIL>
```

---

## Verify

```bash
bash .claude/skills/config-guard/verify.sh
```

Runs the checker on the clean tree (must exit 0), then a mutation self-test:
mutation A drops `graph_timeout_sec` below `timeout_sec` and asserts a C2 FAIL
(exit 2); mutation B raises `min_score` to 0.5 and asserts it is a C7 WARN
(exit 0) by default but a failure under `--strict` (exit 2). The test SKIPs
cleanly (exit 0) when PyYAML is absent so a fresh pre-install container does not
fail CI.

---

## Guardrails

- This skill is **read-only** over the repo. It reports; it never edits
  `config.yaml` to make a check pass.
- `config.yaml` is the single source of truth (CLAUDE.md §1). Do not change a
  behavior elsewhere to make a stale config "correct" — fix the config or
  re-tune deliberately (Step 2 case 2).
- C4 (loopback-only) is the threat-model boundary (`docs/THREAT_MODEL.md`:
  single-operator, loopback-bound). Never relax it to green a check without
  explicit user sign-off — that is the High risk tier by definition.
- Lowering a FAIL to a WARN in `check_config.py`, or deleting a check, needs
  explicit user approval. Adding a check is safe; removing coverage is not.

## Gotchas

- **The checker is static.** It confirms the numbers are internally consistent,
  not that a re-tune *improved* retrieval — Step 3's `/index-doctor` pass is not
  optional when you move a retrieval knob.
- **WARN is intentional friction, not a bug.** `min_score` in 0.05–0.08, a
  changed `rrf_k`, or an operator running `mode: hybrid` are legitimate — the
  WARN exists so the change is conscious and documented, not silent. Use
  `--strict` only where you want the shipped defaults locked.
- **C5 uses ~4 chars/token.** `soul_max_chars < max_context_tokens*4` is the
  documented budget rule; it is a coarse guard, not an exact tokenizer count.
- **Needs PyYAML.** Nested config needs a real parser; unlike `invariant-guard`
  there is no stdlib fallback. In a fresh container, `pip install ... PyYAML`
  first (CLAUDE.md §8) — `verify.sh` skips cleanly until then.
- **No overlap with boot validation.** `utils/config_validation.py` still runs
  at boot and is the last line of defense; this skill is the earlier, richer,
  no-import gate. Both should agree — if they ever disagree, the code (boot
  validator) wins and this checker is stale.
