---
name: verify-deps
description: Verify CyClaw's four install surfaces (pyproject.toml+uv, requirements.txt+pip, Dockerfile, environment.yml) actually agree AND are current against upstream PyPI — dep-guard checks internal pin agreement (static, no network); this adds requirements.txt (which dep-guard never reads), a real dry-run of each surface's install command, and a PyPI currency sweep with CVE awareness. Reports findings; never auto-bumps a runtime pin (Medium-High risk, CLAUDE.md §7) without explicit approval. Use when asked to verify/audit dependencies, check if deps are up to date, or before a dependency-heavy release.
---

# Verify Deps

**Persona:** You are a supply-chain reviewer for CyClaw answering a broader
question than `dep-guard`: not just *do the pins agree with each other*, but
*do the pins agree with each other **and** still reflect a defensible choice
against what's actually available upstream, across every real way someone
installs this project*. You report; you do not unilaterally bump a runtime
dependency.

**Why this skill exists, and how it differs from `dep-guard`:** `dep-guard`
is a fast, pure-stdlib, no-network static checker — it is the correct tool
for "did this PR break a pin invariant," and it should stay that way (no
network dependency in a merge gate). But it has two blind spots by design:
it never reads `requirements.txt` (grep `check_deps.py` — zero references),
and it cannot tell you whether `numpy==1.26.4` is still a reasonable choice
in July 2026 versus May 2024, because that requires a live PyPI lookup.
This skill closes both gaps. It was built after discovering, by hand, that
the "preferred" `uv pip install -r pyproject.toml --constraint
constraints.txt` recipe documented in three files (and actually *executed*
as the Dockerfile's primary install path) had been silently failing and
falling through to its pip fallback on every single build — a bug no
existing check would have caught, because it's a install-command-shaped
bug, not a pin-agreement-shaped one.

---

## Run

### Step 1 — Static pin agreement (delegates to dep-guard)

```bash
python3 .claude/skills/dep-guard/check_deps.py
```

Run this first — it's the fast, authoritative check for pyproject.toml
`<->` constraints.txt `<->` environment.yml agreement (D1-D10) and the
load-bearing pin invariants (pydantic lock-step, numpy `<2`, torch `+cpu`,
uvicorn no-extras). Don't re-implement any of this; if it fails, fix that
first — the rest of this skill assumes clean pins to start from.

### Step 2 — requirements.txt cross-check + normalized pin table

```bash
python3 .claude/skills/verify-deps/extract_pins.py
```

Prints a `package × {pyproject.toml, constraints.txt, requirements.txt,
environment.yml}` table and flags any `requirements.txt` `<->`
`constraints.txt` disagreement — the one pair `dep-guard` never compares.
Add `--json` for a machine-readable version if you're about to hand the
package list to Step 3.

This is reporting-only (always exits 0 on a parseable tree, 3 if
`pyproject.toml`/`constraints.txt` are missing) — a drift line here is a
finding to act on, not a gate failure to unblock.

### Step 3 — Verify each install surface's primary command actually resolves

Static agreement doesn't prove the *command* works — the Dockerfile bug this
skill was born from had perfectly agreeing pins and a still-broken install
line. For each surface, dry-run the documented/executed primary command
against a real Python 3.12 venv:

```bash
python3.12 -m venv /tmp/verify-deps-venv
source /tmp/verify-deps-venv/bin/activate
# 1. Local dev (AGENTS.md / README):
uv pip install --dry-run -e . -c constraints.txt --extra-index-url https://download.pytorch.org/whl/cpu
# 2. Legacy/CI (CLAUDE.md §8):
uv pip install --dry-run -r requirements.txt -c constraints.txt
# 3. Dockerfile's primary line — same command as #2, run with --system to
#    match the container's real invocation:
uv pip install --dry-run --system -r requirements.txt -c constraints.txt
deactivate && rm -rf /tmp/verify-deps-venv
```

Read the failure class if any command errors:
- `no version of torch==...+cpu` (or similar unresolvable pin) → the CPU
  wheel index isn't being reached; check for a missing `--extra-index-url`
  or a stripped `[tool.uv.sources]` route (see Gotchas).
- `no virtual environment found` → you forgot `--system` or an active venv;
  not a real finding, fix the test invocation.
- A network/proxy error reaching `download.pytorch.org` specifically →
  environment-local (sandboxes/CI runners sometimes restrict egress to
  approved hosts); note it as unverified rather than asserting pass or fail.
- Environment.yml (conda): there's no equivalent dry-run flag for `conda`/
  `mamba env create`; if you need to verify it, the honest option is a real
  `mamba env create -f .github/workflows/environment.yml --dry-run` in an
  environment where conda is installed — otherwise report it as "not
  dry-run-verified this pass," don't assert it works from reading the file.

### Step 4 — PyPI currency sweep

For every package `extract_pins.py` reported (or a targeted subset if asked
about one), check the latest stable release and any published advisory
affecting versions at-or-above the current pin:

```
WebFetch https://pypi.org/pypi/<package>/json  →  read "info.version"
```

If `WebFetch` chokes on a large payload (`pydantic-core` and a few others
have big JSON bodies), fall back to `curl -s https://pypi.org/pypi/<package>/json | python3 -c "import json,sys; print(json.load(sys.stdin)['info']['version'])"`.
For CVE/advisory awareness, `WebSearch` for `"<package>" CVE <year>` and
sanity-check the affected-version range against the pin — a headline CVE
that was fixed in a version below your pin is not a finding.

This is naturally parallelizable — for a full sweep (~25+ packages),
batch 4-5 packages per subagent call rather than looking each one up
serially.

**Classify every gap, don't just list it:**

| Gap | Action |
|---|---|
| Matches latest, or pin is the newest release on an intentionally-held line (e.g. numpy `1.26.x`) | Report "current," no action |
| Behind latest, **no CVE found**, dev-tool-only (`ruff`, `mypy`, `bandit`, `pytest*`) | Report as a bump *candidate* — low blast radius, but `dep-guard`'s own Gotchas note that a `ruff`/`mypy` bump can silently change lint/type-check behavior mid-CI, so don't bump without re-running lint/tests |
| Behind latest, **no CVE found**, runtime dependency (`fastapi`, `uvicorn`, `langgraph`, ...) | Report as a bump candidate for explicit review — **do not bump without asking**. This is CLAUDE.md §7 Medium-High risk tier, the same tier `dep-guard`'s Guardrails already name for "bumping a pinned [dependency]" |
| Behind latest, **CVE found affecting the pin's version** | Escalate clearly — this is the one case worth flagging even without being asked, since it's a live security gap, not a staleness preference |
| Pin is a documented, deliberate exception (chromadb CVE-2026-45829 risk-accepted, numpy `<2`, pydantic/pydantic-core lock-step, `websockets` pinned direct for `langgraph-sdk` import-time compatibility) | Do not recommend bumping — report the gap for awareness only, cite the documented reason |

### Step 5 — Report

```
Verify Deps: <n> packages checked | <n> currency gaps | <n> flagged CVEs | <n> install-surface failures
dep-guard: <PASS/FAIL from Step 1>
requirements.txt drift: <none | list from Step 2>
Install surfaces dry-run: local-dev=<PASS/FAIL/unverified> legacy-CI=<...> Dockerfile=<...> conda=<not dry-run-verified, unless actually tested>
Currency: <table or summary — current / bump-candidate / needs-review / CVE-flagged>
Verdict: <fixes applied (list) | findings for review (list) | none>
```

---

## Verify

```bash
bash .claude/skills/verify-deps/verify.sh
```

Runs `extract_pins.py` on the clean tree (must exit 0, no `requirements.txt`
drift), then a mutation test (drift `httpx` in a copy of `requirements.txt`,
assert the `DRIFT` line appears), then a missing-pin-files test (must exit
3). Pure stdlib — no install needed. Does not re-test `dep-guard`'s own
mutations (`.claude/skills/dep-guard/verify.sh` already does, and this skill
delegates Step 1 to it rather than duplicating it).

---

## Guardrails

- **Never bump a runtime dependency's pin without explicit user approval.**
  This is CLAUDE.md §7 Medium-High risk tier by `dep-guard`'s own
  Guardrails ("Adding a new runtime dependency, or bumping a pinned one, is
  the Medium–High risk tier"). This skill's job is to make an *informed*
  bump decision possible, not to make the decision.
- **A bump touches at minimum two files** (`pyproject.toml` AND
  `constraints.txt`, per CLAUDE.md §6's code-change bar), often three or
  four (`requirements.txt`, `environment.yml` if the package is conda-side
  too) — never bump one pin file and leave the others stale; that's the
  exact class of bug this skill exists to catch.
- **The pydantic pair, numpy `<2`, torch `+cpu`, and the chromadb CVE
  pin are load-bearing exceptions `dep-guard` already enforces** — this
  skill inherits those constraints rather than re-deciding them. If a real
  reason emerges to revisit one (e.g. a pydantic release finally pairs with
  a newer `pydantic-core`), update `dep-guard`'s `_PYDANTIC_LOCKSTEP`
  constant and CLAUDE.md §4 in the same commit as the bump, not this
  skill's own logic.
- **Currency findings do not need a fix to be a complete run.** "0 CVEs
  found, N packages a minor version behind, none recommended for
  unattended bump" is a valid, complete report — don't manufacture urgency
  to justify a change.

## Gotchas

- **`uv pip install` (uv's pip-compatible interface) does not honor
  `pyproject.toml`'s `[tool.uv.sources]`/`[[tool.uv.index]]`** — only uv's
  project commands (`uv sync`, `uv add`, `uv lock`) do. Any "preferred uv
  recipe" that omits `--extra-index-url https://download.pytorch.org/whl/cpu`
  (or doesn't pre-install torch before running) will fail to resolve
  `torch==...+cpu`, because that wheel only exists on the CPU index, never
  on PyPI. Verified by dry-run against this repo's real pins,
  2026-07 — this is not a hypothetical.
- **A build stage that copies only manifest files (`pyproject.toml`,
  `constraints.txt`, `requirements.txt`) before installing** (the
  Dockerfile's layer-caching pattern) cannot use `-e .` or `-r
  pyproject.toml` to install the local `cyclaw` package itself — hatchling
  has no `gate.py`/`graph.py`/etc. to build a wheel from yet at that point.
  Point that specific install line at `requirements.txt` (a concrete
  external-package list, no local build needed) instead.
- **`--dry-run` does not prove a real install succeeds.** It validates
  resolution planning, not the final build/link step — an `-e .` dry-run
  can pass even when source files a real (non-dry-run) install would need
  aren't present. Don't over-claim "verified" from dry-run alone; say what
  was actually checked.
- **PyPI's JSON classifier metadata (`Development Status :: N - ...`) is
  not a reliability signal** — several CyClaw-pinned packages (`fastapi`,
  `pydantic-core`) carry pre-1.0-era classifiers as a long-standing quirk,
  unrelated to whether the current release is stable. Judge by the version
  string (no `rc`/`a`/`b`/`dev` suffix) and release-not-yanked status, not
  the classifier.
- **A CVE headline is not automatically a finding** — always check the
  affected-version range against the actual pin. Several 2026 CVE waves
  (LangChain/LangGraph in particular) fix in a version already below what
  CyClaw pins; reporting those as open findings would be noise.
- **`extract_pins.py` imports `dep-guard/check_deps.py` directly** (sibling
  skill, `sys.path` insert) rather than re-parsing pin files — if
  `dep-guard`'s parsing helpers change their names/signature, this script
  breaks with an `ImportError`, which is the intended failure mode (loud,
  not a silent drift between two parallel parsers).
