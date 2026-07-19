---
name: dep-guard
description: Statically validate CyClaw's dependency-pin invariants across pyproject.toml and constraints.txt — pydantic/pydantic-core lock-step, numpy held < 2, torch pinned +cpu, uvicorn carrying no extra in the constraints file, exact-pin reproducibility, and cross-file version agreement. Use before merging any change to pyproject.toml, constraints.txt, or requirements.txt, when bumping a dependency, or when asked to "check deps" or "audit pins". Pure stdlib — runs in a fresh clone before pip install.
---

# Dep Guard

**Persona:** You are a supply-chain reviewer for CyClaw with one question: *do
the dependency pins still honor the invariants the install depends on?* You do
not audit for CVEs (pip-audit/osv-scanner do that) or resolve versions — you
assert that the handful of load-bearing pins still hold and that the two pin
files agree.

**Why this skill exists:** CyClaw's install rules (CLAUDE.md §4 "Dependencies")
are real traps that live only in prose and comments — a bumped `pydantic-core`
that strands the resolver, a `numpy` float to 2.x that removes `np.float_` and
breaks chromadb, a torch pin that loses `+cpu` and drags in CUDA, a `[standard]`
extra sneaking into `constraints.txt` where pip ≥26.1.2 rejects it. Nothing
enforces them. This does — with zero third-party imports (`tomllib` is stdlib on
3.12), so it runs the moment the repo is cloned, before any `pip install`.

---

## Run

### Step 1 — Deterministic checker (stdlib only, ~1 second)

```bash
python3 .claude/skills/dep-guard/check_deps.py
```

It parses `pyproject.toml` (via `tomllib`) and `constraints.txt` and imports
nothing third-party. Exit codes follow the repo convention: `0` pins hold · `2`
a FAIL check tripped · `3` env/config error (a pin file missing or unparseable).

Add `--strict` to escalate every `WARN` to a failure (use it as a merge gate
when you want the documented pins locked, not just the resolver-breaking ones):

```bash
python3 .claude/skills/dep-guard/check_deps.py --strict
```

It checks (severity in brackets):

| ID | Severity | What is checked |
|---|---|---|
| D1 | FAIL/WARN | `pydantic` and `pydantic-core` are BOTH exact-pinned in `constraints.txt` (FAIL if not); a drift from the documented lock-step pair is a WARN (bump both together) |
| D2 | FAIL | `numpy` is held below 2.x (exact `<2` pin) — numpy 2 removes `np.float_` and breaks chromadb/onnxruntime |
| D3 | FAIL | every `torch` pin carries the `+cpu` local build tag (else the default index pulls a CUDA wheel) |
| D4 | FAIL/WARN | `constraints.txt` `uvicorn` carries **no** extra (pip ≥26.1.2 rejects extras in a `-c` file); WARN if `pyproject` `uvicorn` is missing `[standard]` |
| D5 | WARN | every `constraints.txt` entry is an exact `==` pin (its reproducibility purpose) |
| D6 | FAIL | every package pinned in BOTH files agrees on version (`constraints.txt`'s own header says they MUST match) |
| D7 | INFO | `chromadb` pin is CVE-2026-45829 risk-accepted, embedded `PersistentClient` only (SECURITY.md) — do not "fix" it |
| D8 | FAIL/WARN | every CI workflow and install script that hardcodes a torch version agrees with the manifest pin (FAIL); stale doc / `.osv-scanner.toml` references are WARN |
| D9 | FAIL | `.github/workflows/environment.yml` (conda CI lane) pins agree with the pip manifests — `fastapi` exempt (conda-forge's chromadb build pins it; documented in the file itself) |

### Step 2 — Interpret failures

A `FAIL` line names the check and the offending pins. Three cases:

1. **A bump broke an invariant** — the common case. A lone `pydantic-core` bump,
   a numpy-2 float, a lost `+cpu`. Fix the pins, not the checker. Rerun until
   exit 0.
2. **A deliberate, coordinated bump.** Moving the pydantic pair together, or a
   vetted numpy/torch change, is legitimate — update BOTH pin files, this
   checker's documented constants (e.g. `_PYDANTIC_LOCKSTEP`), and CLAUDE.md §4
   in the SAME commit, and say so in the PR body. A `WARN` is exactly this:
   allowed, but flagged so it is conscious.
3. **Exit 3** — `pyproject.toml` or `constraints.txt` is missing/unparseable.
   Fix the environment first.

### Step 3 — What the static check cannot see

The checker proves the pins and their agreement, not that the resolve actually
succeeds or is CVE-clean. For a real bump, also:

- Run the documented install and confirm it resolves:
  `pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML`
  (torch CPU wheel FIRST — CLAUDE.md §8).
- Let CI's `pip-audit` / `osv-scanner` jobs judge advisories — dep-guard does
  not. A pin can satisfy every invariant here and still carry a CVE.
- If you added a NEW source module alongside a dep, remember its `--cov=` flag in
  `ci.yml` and `[tool.coverage.run] source` (CLAUDE.md §4 Testing) — dep-guard
  does not track coverage wiring.

### Step 4 — Report

End with a verdict block (paste into the PR body when run as a merge gate):

```
Dep Guard: PASS (0 fail / <n> warn) | FAIL (<n> fail)
Checker: <exit code and any FAIL/WARN lines verbatim>
Bump review: <pins changed + both files + docs updated, or "none">
Verdict: safe to merge / fix required: <one line per FAIL>
```

---

## Verify

```bash
bash .claude/skills/dep-guard/verify.sh
```

Runs the checker on the clean tree (must exit 0), then six mutation
self-tests: numpy floated to 2.x asserts a D2 FAIL (exit 2); a `[standard]`
extra added to the constraints `uvicorn` asserts a D4 FAIL (exit 2); a
`pydantic-core` drift asserts a D1 WARN (exit 0) that becomes a failure under
`--strict` (exit 2); a CI workflow hardcoding a stale torch version asserts a
D8 FAIL (exit 2); a comment-only `.osv-scanner.toml` torch drift asserts a D8
WARN (exit 0); and an `environment.yml` pin the manifests moved past asserts a
D9 FAIL (exit 2). Pure stdlib — no install needed.

---

## Guardrails

- This skill is **read-only** over the repo. It reports; it never edits a pin
  file to make a check pass.
- Adding a new runtime dependency, or bumping a pinned one, is the Medium–High
  risk tier (CLAUDE.md §7). dep-guard tells you *whether* an invariant broke; it
  does not authorize the bump. A new runtime dependency still needs its exact pin
  in BOTH `pyproject.toml` and `constraints.txt` (CLAUDE.md §6 Code-change bar).
- Weakening a FAIL to a WARN, or deleting a check, needs explicit user approval.
  Adding a check is safe; removing coverage is not.
- The chromadb CVE is risk-accepted by the threat model. Do not "fix" it by
  switching to the HTTP client or filing a fix PR (CLAUDE.md §4).

## Gotchas

- **The checker is static.** Passing dep-guard does NOT mean the resolve
  succeeds or is advisory-clean — Step 3's real install + the CI security jobs
  are not optional when you bump a pin.
- **D1 pins a snapshot pair on purpose.** `_PYDANTIC_LOCKSTEP` hard-codes the
  current pydantic/pydantic-core pair, so a legitimate coordinated bump WARNs
  until you update the constant — intended friction (Step 2 case 2), the same
  discipline `invariant-guard` uses for its regex pins.
- **Version compare is string-exact.** D6 compares the pinned version text; it
  does not evaluate ranges. A `pyproject` range (`>=`) against a `constraints`
  exact pin is skipped, not judged — dep-guard checks exact-vs-exact agreement,
  which is CyClaw's actual convention (both files pin `==`).
- **numpy/torch pins may be optional-extra.** The checker scans
  `[project.dependencies]` AND every `[project.optional-dependencies]` group, so
  the `torch-cpu` extra's `torch==...+cpu` is seen even though it is not a
  default install.
- **Names are PEP 503-normalized.** `pydantic-core`, `pydantic_core`, and
  `Pydantic.Core` compare equal — do not rely on exact casing/punctuation.
