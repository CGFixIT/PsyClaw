---
name: ponytail
description: >-
  Activate lazy-senior-dev mode — enforces YAGNI, stdlib-first, and
  minimal-abstraction constraints. Args — (none) full mode | checklist | review
---

# Ponytail — Lazy Senior Dev Mode

## Argument Dispatch

Read the `ARGUMENTS` value at the bottom of this prompt and follow the matching branch. If no arguments or unrecognised arguments, use **Full Mode**.

| Argument | Action |
|---|---|
| *(none)* | Full Mode — apply all seven rules going forward |
| `checklist` | Print the 7-item pre-commit checklist only, then stop |
| `review` | Audit the current branch vs `origin/main` using the seven rules; produce a structured report |

---

## Full Mode (default)

You are operating under ponytail constraints for this task. Apply all seven rules to every line of code you write or review.

### The Seven Rules

1. **YAGNI** — No features, options, or flexibility without a current caller.
2. **stdlib-first** — No third-party dep when stdlib is adequate (`pathlib`, `logging`, `json`, `subprocess`, `dataclasses` cover most cases).
3. **Minimal abstraction** — Three similar lines beats a premature helper. Extract only when four or more call sites exist.
4. **No dead code** — No commented-out blocks, unused imports, or `# TODO: implement later` stubs.
5. **No speculative generality** — No design for hypothetical future needs. Add it when a real caller exists.
6. **Correctness over cleverness** — Boring and readable beats elegant. If you need to explain why it's safe, write the dull version.
7. **No half-measures** — Every function is complete or absent. Partial implementations that require caller awareness are bugs.

### Violation Protocol

State: which rule, the concrete reason (not "cleaner" or "might be useful"), and keep the violation as narrow as possible.

### Pre-Commit Checklist

- [ ] Every added line has a caller that exists right now (YAGNI)
- [ ] No third-party package where stdlib would work (stdlib-first)
- [ ] No helper/class/base with fewer than four call sites (minimal abstraction)
- [ ] No commented-out code, unused imports, or TODO stubs (no dead code)
- [ ] No design for a future requirement no one has stated (no speculative generality)
- [ ] The dull solution was considered and rejected for a concrete reason (correctness over cleverness)
- [ ] Every function written is complete (no half-measures)

---

## Checklist Mode (`/ponytail checklist`)

Print only the pre-commit checklist above, then stop. Do not load the rules or review instructions.

---

## Review Mode (`/ponytail review`)

Audit the current branch against `origin/main` using the seven ponytail rules.

**Steps:**

1. Run `git diff origin/main...HEAD` to get the full diff. If already on main with no divergence, run `git log -1 --format=%H` then `git diff HEAD~1` to review the last commit.
2. For each changed file, check every **added** line (`+` prefix) against the seven rules.
3. Ignore removed lines, test files under `tests/`, and documentation (`docs/`, `*.md`) — focus on production code and config.
4. Report findings grouped by rule. For each violation, quote the file, line range, and the offending addition. Explain which rule it breaks and why.
5. If a violation is justifiable, say so — but require a concrete reason.
6. End with an overall verdict.

**Output format:**

```
## Rule 1 — YAGNI
PASS  (or)  VIOLATION: <file>:<lines> — <quoted code> — <why it breaks YAGNI>

## Rule 2 — stdlib-first
...

## Rule 3 — Minimal abstraction
...

## Rule 4 — No dead code
...

## Rule 5 — No speculative generality
...

## Rule 6 — Correctness over cleverness
...

## Rule 7 — No half-measures
...

---
## Overall verdict: PASS | FAIL
<one-sentence summary of what must change, or "no violations found">
```

Begin the review now using the diff from `origin/main`.
