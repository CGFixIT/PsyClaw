---
name: verify-deps
description: Verify CyClaw's four install surfaces (pyproject.toml+uv, requirements.txt+pip, the Docker surface — Dockerfile + docker-compose.yml + .dockerignore + publish-ghcr.yml — and environment.yml) actually agree AND are current against upstream PyPI — and that the environment dependencies declared OUTSIDE the pin manifests (workflow-pinned tool versions, the Python version's four independent declarations, third-party imports declared in no manifest, the Docker fallback torch pin vs constraints.txt, compose/.dockerignore/publish-workflow coherence with the Dockerfile) have not drifted. dep-guard checks internal pin agreement (static, no network); this adds requirements.txt (which dep-guard never reads), the install-surface scope contract (which surface may carry extras — constraints.txt is a version ceiling, not an install list), the non-manifest drift surfaces, a real dry-run of each surface's install command, and a PyPI currency sweep with CVE awareness. Reports findings; never auto-bumps a runtime pin (Medium-High risk, CLAUDE.md §7) without explicit approval. Use when asked to verify/audit dependencies, check if deps are up to date, check whether a merge introduced dependency drift, or before a dependency-heavy release.
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

## The install surfaces are not four copies of one list

This is the distinction most reviews get wrong, and getting it wrong produces
*false* drift findings — "package X is in `constraints.txt` but not
`requirements.txt`, that's drift" is usually the checker being wrong, not the
tree. Verified against the repo, 2026-08-02:

| Surface | What it installs | Extras? |
|---|---|---|
| `pip install -r requirements.txt -c constraints.txt` | Base runtime + torch CPU. 18 requirement lines (test tools live in `requirements-test.txt`, kept out of the Docker image). Header declares itself a **legacy compatibility surface**, kept in sync with `pyproject.toml`/`constraints.txt` for the Dockerfile and legacy CI/tools | **None.** Zero extras, by design |
| `pip install -e ".[<extra>]" -c constraints.txt` | The 17 base deps, plus whichever of the 11 extras are named | **Yes — the only surface that can install one** |
| `Dockerfile` (+ `docker-compose.yml`, `.dockerignore`, `.github/workflows/publish-ghcr.yml`) | Runs `uv pip install --system -r requirements.txt -c constraints.txt`, with a pip fallback (`Dockerfile:40-43`). Compose runs the image (loopback publish, runtime-state mounts), `.dockerignore` shapes the build context, `publish-ghcr.yml` ships the image compose pulls — E5/E6 pin the four files to each other | **None** — it *is* surface #1, containerized |
| `conda env create -f environment.yml` | Base runtime + test/dev tools from conda-forge, plus a 3-package `pip:` tail | **None** |

Two consequences that drive every judgement in this skill:

**`constraints.txt` is not an install surface.** It is a version ceiling applied
*to* the other three. It legitimately pins packages that **no** surface installs
by default — every extras-only package (`deepagents`, `nemoguardrails`,
`psycopg`, `pgvector`, `langchain-openai`, `langchain-anthropic`,
`langchain-xai`) plus pinned transitives. A package present in `constraints.txt`
and absent from `requirements.txt` is the **designed** state, not drift. The
drift-shaped question is the reverse: a package installed by a surface but
*unpinned* in `constraints.txt`.

**`environment.yml` deliberately diverges from the pip pins, twice.** It carries
`fastapi=0.115.9` where the pip path is `0.139.2`, and
`opentelemetry-exporter-otlp-proto-grpc>=1.42` where the pip path has no such
line. Both are conda-forge *packaging* constraints (chromadb=1.5.9's conda build
hard-pins fastapi; its OTel floor is a 2022-era range that solves into a
protobuf-incompatible exporter), documented inline at the pin. Do **not**
"reconcile" either one toward the pip values — that reds the conda lane.
`dep-guard` already knows about these; re-flagging them is noise.

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

### Step 3 — Environment drift *outside* the pin manifests

```bash
python3 .claude/skills/verify-deps/check_env_drift.py     # add --strict to fail on warnings
```

Steps 1 and 2 both stop at the manifest boundary — they compare pin files to
other pin files. But CyClaw declares load-bearing environment dependencies in
places no manifest checker reads, and nothing cross-checks those. Six classes:

- **E1 — tool versions pinned inline in workflow YAML.** `flake8==7.3.0` and
  `wemake-python-styleguide==1.6.2` run in the (advisory) lint lane
  (`lint.yml`); `actionlint-py==1.7.12.24` and `zizmor==1.28.0` gate CI
  (`ci.yml`); `pip` is pinned at **14 separate sites** across 6 workflow
  files (the checker prints the live count). None appears in
  any manifest. The failure this catches is not a wrong version — it is the
  *same* tool pinned at two different versions in two jobs, which makes one
  lane's result silently unreproducible against the other's.
- **E2 — the Python version**, declared independently in `pyproject.toml`
  (`requires-python`), `Dockerfile` (`FROM python:`), `environment.yml`
  (`python=`), and every workflow's `python-version:`. Four places, no
  cross-check. Compares the concrete minor versions for equality and checks
  them against `requires-python`'s floor (a range that everything satisfies is
  correct, not drift).
- **E3 — a third-party module imported by source but declared in no manifest.**
  The class `dep-guard` cannot see by construction: it reads manifests and never
  reads imports. Walks first-party source with `ast`, skipping virtualenvs
  structurally (by their own `pyvenv.cfg`, not by guessing the directory name).
- **E4 — the install-surface scope contract** from the table above: asserts
  `requirements.txt` carries no extras-only package.
- **E5 — the Docker build's dependency-install contract**: asserts the
  Dockerfile copies the manifests, uses `requirements.txt` + `constraints.txt`
  on both the uv path and the pip fallback, never installs
  `requirements-test.txt` into the image, and pre-installs the **same**
  `torch==` the `constraints.txt` pin names — the Dockerfile's own comment
  records the miss (constraints moved `2.12.1 -> 2.13.0`, the fallback line
  did not, and the fallback installed the old wheel then failed the
  constrained resolve). A "some `torch==` is present" check passed that tree.
- **E6 — the rest of the Docker surface**: `docker-compose.yml`,
  `.dockerignore`, and `.github/workflows/publish-ghcr.yml` must agree with
  the Dockerfile and each other. `.dockerignore` keeps the three COPYed
  manifests in the build context and keeps every runtime-state directory
  (`logs/`, `checkpoints/`, `index/`, `data/`, `.emb_cache/`) out of image
  layers; compose mounts each of those back in, publishes only on
  `127.0.0.1`, and agrees with `EXPOSE`/`CMD --port` on the container port;
  compose's default `CYCLAW_IMAGE_TAG` equals `pyproject.toml`'s `version`;
  `publish-ghcr.yml` builds `./Dockerfile` and pushes the `IMAGE_NAME` compose
  pulls. Each file is info-skipped when absent (the Docker surface may be the
  Dockerfile alone), never silently passed when present and incoherent.

Pure stdlib, no network, no install — same constraints as `dep-guard` and
`extract_pins.py`, so it runs in a fresh clone before pip does. Exits 0 with
warnings, 2 on a failure; `--strict` promotes warnings to a failure.

**A clean tree currently reports zero E3 warnings.** `huggingface_hub` and
`starlette` were the first two findings this check ever surfaced
(`retrieval/embeddings.py` and `gate.py`/`harness/server.py` import them
directly; both survived only as hard transitives of `sentence-transformers`/
`fastapi`) and have since been promoted to explicit pins in
`pyproject.toml`/`constraints.txt`/`requirements.txt` — real resolved
versions from a fresh-venv install, not guessed. `huggingface_hub` is also
mirrored into `environment.yml`; `starlette` deliberately is not, because
`environment.yml`'s `fastapi` is pinned older (`0.115.9`, forced by
conda-forge's `chromadb` build — see the comment there) than the pip path's
`0.139.2`, and forcing the pip-resolved `starlette==1.3.1` alongside it could
easily demand a pairing `fastapi==0.115.9` was never built against. Any name
this check reports going forward is a new finding, not a known one.

`pyodbc` is a *third* undeclared import and is **intentional**, so it lives in
`_IMPORT_ALLOWLIST` with its reason rather than being reported:
`agentic/sqlconnect/client.py` imports it inside a function and raises a
friendly "pyodbc is not installed" if absent, so a disabled connector needs
nothing installed. Declaring it would put an MSSQL driver on every box for a
connector that ships off. Keep the allowlist an argued exception list — a new
entry needs its reason written next to it, or it becomes a silencer.

### Step 4 — Verify each install surface's primary command actually resolves

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
# 4. The compose half of the Docker surface renders (needs the docker CLI;
#    report "not verified" rather than skipping silently when it is absent):
docker compose config --quiet
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
  `mamba env create -f environment.yml --dry-run` in an
  environment where conda is installed — otherwise report it as "not
  dry-run-verified this pass," don't assert it works from reading the file.

### Step 5 — PyPI currency sweep

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

### Step 6 — Report

```
Verify Deps: <n> packages checked | <n> currency gaps | <n> flagged CVEs | <n> install-surface failures
dep-guard: <PASS/FAIL from Step 1>
requirements.txt drift: <none | list from Step 2>
Env drift (E1-E6): <n> failure(s), <n> warning(s) — <every E3 name reported, or "none">
Install surfaces dry-run: local-dev=<PASS/FAIL/unverified> legacy-CI=<...> Dockerfile=<...> conda=<not dry-run-verified, unless actually tested>
Currency: <table or summary — current / bump-candidate / needs-review / CVE-flagged>
Verdict: <fixes applied (list) | findings for review (list) | none>
```

A clean tree reports zero E3 warnings, so every E3 name the run prints is a
new finding — list each one; never summarize them away as "known".

---

## Verify

```bash
bash .claude/skills/verify-deps/verify.sh
```

Eighteen checks, pure stdlib, no install needed:

1. `extract_pins.py` on the clean tree — exit 0, no `requirements.txt` drift
2. Mutation: drift `httpx` in a copy of `requirements.txt`, assert the `DRIFT` line
   (and 2b: the same for a `requirements-test.txt` pin — `pytest` drifted away
   from `constraints.txt`)
3. Missing pin files — must fail closed (exit 3)
4. `check_env_drift.py` on the clean tree — exit 0. Asserts the **exit code, not
   a warning count**: a clean tree reports zero E3 warnings today, and a future
   undeclared transitive should surface as a warning to read, not fail this test
5. Mutation E1: the same tool pinned at two versions in two workflow files —
   exit 2. This is the drift class nothing else in the repo can see
6. Mutation E4: an extras-only package leaking into `requirements.txt` — exit 2,
   *preceded* by a negative test that the same package named in a **comment**
   does not trip it (`requirements.txt` discusses extras in prose)
7. Strict mutation: a `requirements.txt` pin omitted from `constraints.txt` —
   `--strict` must reject it (exit 2)
8. The clean tree must also satisfy the strict import/environment checks
   (`check_env_drift.py --strict` exit 0)
9. Mutation E5: a Dockerfile that loses the constrained install contract —
   exit 2 (the E5 self-test)
10. Negative control: the shipped Docker surface (`Dockerfile`,
    `docker-compose.yml`, `.dockerignore`, `publish-ghcr.yml`) copied whole
    beside the pin manifests — exit 0 with the E6 ok lines
11. Mutation E5: the fallback `torch==` pre-install lags `constraints.txt` —
    exit 2 (the lock-step the Dockerfile comment asks for)
12. Mutation E6: `.dockerignore` swallows a COPYed manifest — exit 2
13. Mutation E6: `.dockerignore` stops excluding `index/` — exit 2 (private
    vectors would bake into a published image)
14. Mutation E6: the compose publish leaves `127.0.0.1` — exit 2
15. Mutation E6: compose drops the `./index` mount — exit 2 (503
    `INDEX_NOT_FOUND` with a green healthcheck)
16. Mutation E6: compose's default `CYCLAW_IMAGE_TAG` no longer matches
    `pyproject.toml`'s `version` — exit 2
17. Mutation E6: `publish-ghcr.yml`'s `IMAGE_NAME` is not the image compose
    pulls — exit 2
18. Mutation E6: `.dockerignore` replaces `data/` with nested `data/personality/`
    — exit 2 (`startswith("data/")` on a subpath must not green the `data` tree)

Both scripts take `--repo-root`, which is what lets the mutations run against a
`mktemp -d` tree instead of the real repo. Does not re-test `dep-guard`'s own
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
- **E3's import→distribution mapping is PEP 503 normalization, not a lookup
  table** — `_DIST_ALIAS` in `check_env_drift.py` holds only the four names
  that differ by more than punctuation (`yaml`→`pyyaml`,
  `dateutil`→`python-dateutil`, `dotenv`→`python-dotenv`,
  `celpy`→`cel-python`). Every
  underscore/hyphen case (`langchain_xai`, `rank_bm25`, `huggingface_hub`, …)
  is handled by treating `_` and `-` as equivalent, per PEP 503. Adding those
  to the table instead would work today and silently rot on the next
  `langchain_*` import — resist it.
- **E3 skips virtualenvs structurally, by their own `pyvenv.cfg`** — not by
  name. The venv in this tree was `.venv312` when the check was written, so a
  hardcoded `.venv/` skip missed it entirely and the first run reported ~200
  site-packages modules as undeclared. Any name-based skip list has the same
  bug waiting in it.
- **E6 reads YAML with regexes on purpose.** `docker-compose.yml` and
  `publish-ghcr.yml` are scanned for the specific lines the contract names
  (`image:`, port publishes, `:/app/<dir>` mounts, `IMAGE_NAME:`, `file:`)
  rather than parsed, so the checker stays stdlib-only and pre-install like
  the rest of this skill. A restructured compose file that moves those lines
  into anchors/extension fields would need the regexes revisited — the
  failure mode is a false FAIL, never a silent pass.
- **`extract_pins.py` imports `dep-guard/check_deps.py` directly** (sibling
  skill, `sys.path` insert) rather than re-parsing pin files — if
  `dep-guard`'s parsing helpers change their names/signature, this script
  breaks with an `ImportError`, which is the intended failure mode (loud,
  not a silent drift between two parallel parsers).
