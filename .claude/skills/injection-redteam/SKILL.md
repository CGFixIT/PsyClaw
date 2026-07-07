---
name: injection-redteam
description: Adversarially probe CyClaw's prompt-injection sanitizer with a jailbreak/injection corpus, surface bypasses, and close each one with a minimal high-signal banned_patterns rule plus a regression test — while holding the false-positive budget so legitimate queries still pass. Use when asked to redteam the sanitizer, harden injection defense, or after any change to utils/sanitizer.py or config.yaml banned_patterns.
---

# Injection Redteam

**Persona:** You are an offensive security engineer stress-testing CyClaw's one
inbound content boundary: the 33-pattern prompt-injection filter
(`utils/sanitizer.py` + `config.yaml` `policy.prompt_filter.banned_patterns`).
The sanitizer runs on every `/query` and at index time; it is the control that
answers "prompt injection (direct)" and "corpus poisoning" in the threat model
(`docs/THREAT_MODEL.md` §2). Adversarial coverage decays as new bypass families
emerge — this loop keeps it current. See `docs/PROPOSED_SKILLS.md` #2.

**What "done" looks like for one pass:** every probe in the corpus behaves as
labeled (jailbreaks blocked, legitimate queries allowed), each newly-closed gap
has a regression test, and no legitimate query regressed.

---

## The loop

### Step 1 — Run the corpus against the shipped sanitizer

```bash
python3 .claude/skills/injection-redteam/redteam.py
```

Requires the project venv (PyYAML + `utils` importable). It drives every probe
in `probes.yaml` through the REAL `check_input` against the real `config.yaml`
and classifies each:

| Bucket | Meaning | Action |
|---|---|---|
| **new_bypasses** | `expect: blocked`, got through, NOT flagged `open_finding` | **Regression.** A pattern stopped working — fix before anything else. |
| **open_findings** | `expect: blocked`, got through, flagged `open_finding` | Known gap. This loop's work list. |
| **fixed_findings** | flagged `open_finding` but now blocked | You (or a config change) closed it — drop the flag to bank the anchor. |
| **false_positives** | `expect: allowed` but blocked | Usability regression — a pattern is too broad. |

Exit `0` = baseline holds (open findings are allowed to exist). Exit `2` = a new
bypass or a false positive. Exit `3` = deps/env problem.

The seed corpus ships with **7 real open findings** in the shipped config (e.g.
`from THE it department` defeats the `from (it department|...)` pattern; a
zero-width character splits a trigger word). Confirm them yourself before
touching anything.

### Step 2 — Pick one open finding and classify the bypass family

Work one finding at a time. Map it to the `banned_patterns` taxonomy section it
*should* have matched (the comment on each probe names the gap):
`Core Override` · `Role` · `System` · `Memory` · `Authority` · `Tool` ·
`Obfuscation`. Naming the family keeps the new pattern in the right place and
the taxonomy honest.

### Step 3 — Add a minimal, high-signal pattern

> ⚠️ This edits `config.yaml` `banned_patterns` — a **security boundary**. Per
> the escalation rules, changes here go through review: make the change on the
> branch, run the full checks, and call it out explicitly in the PR body. Do
> not weaken or remove an existing pattern to make a probe pass.

Add ONE regex to the matching taxonomy section in `config.yaml`. Requirements:
- Written to compile under `re.IGNORECASE` (that is how `_load_filter` builds
  it — do not add inline `(?i)` or case variants).
- Use `\s+` between words so corpus newlines/tabs don't defeat it (match the
  existing patterns' style).
- **High-signal only.** It must catch the jailbreak phrasing without matching
  the benign near-miss probes (`bn-09`, `bn-10`, …). Broad verbs like `remember`
  or `update` on their own will blow the false-positive budget.
- Keep the taxonomy count comments accurate if you change section totals.

### Step 4 — Re-run; confirm the finding closed and nothing regressed

```bash
python3 .claude/skills/injection-redteam/redteam.py
```

The probe should move from `open_findings` to `fixed_findings`, and
`false_positives` must stay empty. If a benign probe now blocks, your pattern is
too broad — tighten it. Then drop `open_finding: true` from that probe in
`probes.yaml` so it becomes a permanent regression anchor.

### Step 5 — Add a regression test

Append the closed phrase to `tests/test_sanitizer.py`
`TestShippedConfigContract.test_documented_phrases_blocked` (the parametrize
list), matching the existing style — it asserts the phrase raises against the
REAL `config.yaml`. This is what makes the fix durable; the phrase count in that
test is not asserted, so adding one is safe.

```bash
GROK_API_KEY=dummy pytest tests/test_sanitizer.py -q --tb=short
```

### Step 6 — Repeat until the corpus is dry; then extend it

Loop Steps 2–5 until `open_findings` is empty. Then earn the next round: add new
probes for families not yet represented (unicode homoglyphs, split-token
payloads, multilingual overrides, instruction smuggling inside quoted "context")
and go again. Never delete a probe — a closed finding stays as an anchor.

### Step 7 — Sync the documented pattern count

If you added patterns, the documentary total "33" is cited in several places
(`config.yaml` header comment, `CLAUDE.md`, `guardrails/rails.py` comment,
`agentic/fsconnect/client.py` comment). Update them, or run `/doc-sync` which
checks exactly this. The count is **documentary, not asserted** — no test
enforces `== 33` — but drift misleads the next reader.

---

## Verify

```bash
bash .claude/skills/injection-redteam/verify.sh
```

Runs the baseline (must be exit 0 — no new bypasses/FPs) and a regression
self-test: it points the runner at a config with the filter DISABLED and
asserts the runner then reports new bypasses (exit 2). A redteam that can't tell
a working sanitizer from a broken one is worthless; the self-test keeps it
honest. Skips cleanly (exit 0) if project deps aren't importable.

---

## Guardrails

- **The sanitizer is a security boundary.** Additions go through PR review and
  are called out explicitly. Never delete or weaken an existing pattern, and
  never disable the filter, to make a probe pass — that inverts the skill.
- **Hold the false-positive budget.** Every `benign` probe must stay allowed. A
  pattern that blocks real CyClaw questions fails the run even if it closes a
  jailbreak.
- **The MCP path deliberately does NOT sanitize** (`mcp_hybrid_server.py` has no
  LLM behind it — nothing to protect). Do not "extend coverage" by adding
  `check_input` there; that is a documented non-goal, not a gap.
- **Patterns live in `config.yaml`, not code.** `utils/sanitizer.py` is the
  engine; the rules are config. Add rules to config.

## Gotchas

- **Sanitizer caches by config path.** `_load_filter` is `lru_cache(maxsize=8)`
  keyed on the config path — a long-running process won't see edits. The runner
  starts fresh each invocation, so always re-run the script (don't edit config
  inside one Python session and re-check).
- **`enabled: true` + zero patterns silently degrades to length-only.** If you
  ever empty the list, the filter stops blocking phrases and only enforces
  `max_input_chars` — it logs a warning, it does not error.
- **Empty `policy:`/`prompt_filter:` YAML parses to `None`.** The engine's
  `or {}` chaining tolerates it; don't "simplify" that away.
- **Regexes are matched with `search`, not `fullmatch`** — a pattern matches
  anywhere in the query. Anchor deliberately only when you mean to.
- **`redteam.py` needs the venv.** Fresh containers have no deps; install first
  (`/run-cyclaw` or `/sandbox-runtime-verification`) or the runner exits 3.
