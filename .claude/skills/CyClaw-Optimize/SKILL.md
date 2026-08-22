---
name: CyClaw-Optimize
description: Methodically scan the CyClaw main branch for code, CI, security, financial-risk, and maintainability optimization opportunities, then open a small set of focused, reviewable pull requests only when each chunk earns its keep (not a fixed PR quota). Use when asked to optimize CyClaw, find competitive/trade-bot advantages, harden CI, audit for risk, propose improvements, or open optimization PRs against main.
---

# CyClaw-Optimize

**Persona:** You are a modern AI engineer specializing in Python and extremely
familiar with the CyClaw architecture — FastAPI RAG gateway (`gate.py`),
LangGraph 10-node security topology (`graph.py`), ChromaDB + BM25 hybrid
retrieval, local LLM via Ollama with a triple-gated Grok (xAI) and/or Claude
fallback, the MCP hybrid server, the `agentic/` GitHub layer (including
`deepagent_github/`, the GitHub coding agent, and `fsconnect/`, the filesystem
bridge), the `harness/` coding-agent server, and the out-of-band `sync/`
Dropbox pipeline — all of which ship platform launchers for **macOS** (primary
target) and **Windows** (close secondary) under `macos/` and `powershell/`.
You read code for leverage: performance, security, financial risk / oversight
in assumptions, auditability, and maintainability.

**What this skill does:** drives a time-boxed scan of the **main** branch,
groups findings into a **small set of reviewable PR-sized chunks** (about 1–4
when several independent fixes earn their keep — **a guideline, not a quota**;
do not invent low-value PRs to hit a number), and opens one focused pull request
per kept chunk on a branch cut from the **topology base** Step 3.5 assigns —
never committing to `main` directly. A human decides when to merge/close.
Prefer merging this session's drafts **lowest PR number → highest**, parent
before child when stacked.

**How it's driven:** the deterministic setup + scan-seed is a committed
harness, `bootstrap.sh`. The scan itself is a read-only subagent. PR dedup and
PR creation are GitHub MCP tool calls. Paths below are relative to the repo
root (the `<unit>` dir).

---

## Run (agent path)

### Step 0 — Bootstrap (harness)

Run the harness. It pins the git identity the stop hook requires, fetches
`origin/main`, positions you on a fresh working branch cut from `origin/main`
(creating it if you pass a name), and prints a repo inventory that seeds the
scan:

```bash
bash .claude/skills/CyClaw-Optimize/bootstrap.sh "agent/cyclaw-optimize-<topic>"
```

Omit the branch argument to run read-only against the current branch. The
script never force-resets an existing branch that already has commits.

> Verified this session: the harness ran clean, reported the branch /
> merge-base / commits-ahead, and printed file counts, the largest-Python-file
> list (refactor candidates), the CI workflow list, dependency manifests, Grok
> touchpoints, and recent `main` commits.

### Step 1 — Time-boxed scan (subagent, ~4 minutes)

Dispatch **one read-only `Explore` subagent** to scan for ~4 minutes. Do not
let it edit anything. Give it the persona and the concrete areas to sweep.
This is the prompt that worked this session (adapt the topic, keep the
structure):

> You are a modern AI engineer specializing in Python and deeply familiar with
> the CyClaw architecture. Repo root is the CWD. Spend ~4 minutes on a
> READ-ONLY scan for concrete optimization / fix opportunities suitable for
> small-to-medium focused PRs. Sweep: `.github/workflows/*.yml` (caching,
> action SHA pinning, `cancel-in-progress`, matrix gaps — **nothing needing a
> license/secret/key**); `tests/` (coverage gaps, logic errors, brittle
> fixtures, missing assertions); `agentic/` and `sync/` (bugs, inefficient
> loops, redundant logic, error-handling and financial/oversight-assumption
> risk); `agentic/fsconnect/`, `harness/`, and `agentic/deepagent_github/`,
> with **macOS as the primary target** (Darwin-specific path handling in
> `pathsafe.py`, `macos/*.sh` installer/launcher scripts) and **Windows as a
> close secondary** (`powershell/*.ps1` scripts, `pathsafe.py`'s `_win_*`
> helpers) — but do not re-flag the Windows `pathsafe` test-coverage gap as a
> new finding, it's a tracked, deliberate deferral (see
> `docs/work/FSCONNECT_SQL_ROADMAP.md`'s "FS Phase 4 — Windows hardening");
> `llm/client.py` + `graph.py` (outdated Grok/xAI model names &
> endpoints, retry/timeout, performance); `config.yaml` (risky defaults);
> `requirements.txt` / `constraints.txt` / `pyproject.toml` (loose/outdated
> pins, missing imports); readability/auditability anywhere. Return 6–10
> DISTINCT findings, each with: title, file path(s) + line numbers, one-line
> description, category, effort (small/medium). End with a suggested grouping
> into a small set of PR-sized chunks (only as many as earn a PR — not a fixed five). Cite real code — do not invent.

You may keep reading code after the 4 minutes; the time-box only governs the
initial sweep.

### Step 2 — Dedup against open PRs (MCP) — do this BEFORE picking focus areas

List open PRs and drop any candidate area already covered by an open PR. Also
explicitly skip the known-stale "null allowed origins in `config.yaml`" idea —
it is out of scope.

```text
mcp__github__list_pull_requests(owner="CGFixIT", repo="CyClaw", state="open")
```

> Gotcha (verified): this call returns a very large payload. Parse it down to
> `number` + `title` only — e.g. read the saved tool-result file with a small
> `python3 -c "import json; ..."` over `number`/`title` — rather than dumping
> it into context.

### Step 3 — Select focus areas and announce them

From the deduped findings, choose only the PR-sized chunks that clearly earn
their keep. **Count is a guideline, not a target:** about 1–4 focused PRs is
typical when several independent fixes are real; **one solid PR is better than
four thin ones**, and zero is correct when nothing remains after dedup. Never
split or invent work just to fill a slot. Each chunk = **1–2 major concepts OR
3–5 minor tasks**, cross-file/cross-concept where it adds value. State, in one
line each, which section of code each PR will touch and why (the leverage:
performance / security / financial-risk / auditability / maintainability).
Prefer chunks that are independently reviewable and easily restorable through
GitHub — subject to Step 3.5 when they share files.

### Step 3.5 — Plan branch topology for shared files (prevents merge-loss / conflicts)

Always START from `origin/main` (Step 0 / the bootstrap cuts the first branch
there — that is correct). The strategy below only changes **where you cut the
2nd-and-later branch when it edits a file an earlier chunk also edits.**

Before creating any branches, build a **file → chunks** map and find every file
touched by more than one chunk. The usual shared files: `.github/workflows/ci.yml`
(everyone appends to the pytest list / `--cov` list), `config.yaml`,
`requirements.txt` / `constraints.txt` / `pyproject.toml`, `CLAUDE.md`. For each
shared file pick ONE strategy:

- **(A) Consolidate** — put *all* edits to that shared file in a single chunk/PR
  (or a dedicated "CI wiring" PR). Best when the edits are small and related
  (e.g. each chunk only appends one line to the ci.yml test list). The other
  chunks then touch only their own new files and carry no ci.yml edit.
- **(B) Stack** — cut the later branch from the *earlier branch* instead of
  `origin/main`, so the later PR already contains the earlier's edit to the
  shared file. Set the later PR's **base to the earlier branch** on GitHub (not
  `main`). Stacked PRs must merge parent-first; rebase the child after the
  parent merges. Best when chunks are large and otherwise independent.

**Why it matters:** two branches cut from the same base that edit the **same or
adjacent lines** of a shared file produce a merge **conflict** — GitHub blocks
the merge button until a human resolves it, and a careless resolution can drop
one side's edit (the "lost changes" failure mode). Non-adjacent edits to the same
file 3-way-merge cleanly, but verify rather than trust luck.

**Verify before opening PRs** — for every pair of branches that share a file, do
a throwaway 3-way merge locally and confirm both edits survive with no conflict:

```bash
git checkout -B _trial origin/main
git merge --no-ff "origin/<branch-A>" && git merge --no-ff "origin/<branch-B>"
grep -q '<A-marker>' "<shared-file>" && grep -q '<B-marker>' "<shared-file>" && echo "both present"
grep -rc '<<<<<<<' "<shared-file>"           # must be 0
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"  # still valid
git checkout main && git branch -D _trial
```

> Verified this session: two PRs both appended to `ci.yml` (one to the pytest
> list, one to the `--cov` list) in **non-adjacent** regions. Trial merges in
> both orders combined cleanly — both edits present, valid YAML, zero conflict
> markers — so no consolidation/stacking was needed. The check is cheap; run it
> whenever ≥2 chunks touch one file rather than assuming the regions are disjoint.

**Session merge order:** plan chunks so humans can merge this run's drafts from
**lowest PR number → highest**, with stack **parents always before children**.
That order is the universal default for the rest of this skill (Step 4+), whether
a given PR's GitHub `base` is `main` or a parent branch.

### Step 4 — One focused PR per chunk

Work chunks in **planned merge order** (see Step 3.5): parent / lower-order
first, then children. For each chunk, use its **topology base** — not a blanket
assumption that every branch starts at `main`:

| Topology (Step 3.5) | Cut branch from | GitHub PR `base` |
|---|---|---|
| Independent (no shared-file dependency on another chunk in this run) | `origin/main` | `main` |
| Stacked on an earlier chunk | that earlier chunk's branch | that earlier branch (not `main`) until the parent merges |
| Consolidated | whatever branch owns the shared-file edits | same as that branch's topology |

1. Make the focused change(s). Keep the diff minimal and on-topic; avoid
   touching unrelated files (CLAUDE.md operating contract).
2. Verify (see **Verify** below).
3. Commit with a clear message, then push with upstream tracking:

   ```bash
   git add -p
   git commit -m "<type>: <what changed and why>"
   git push -u origin "<branch-name>"
   ```

   On network failure only, retry up to 4× with exponential backoff
   (2s, 4s, 8s, 16s).
4. Open a **draft** PR via MCP with a clear title and a body covering: the
   change, its benefit, risk to monitor, and **merge topology** (independent /
   stacked-on `<parent-branch or #N>` / consolidated; planned merge order).

   Independent chunk (only when Step 3.5 says it has no stack parent):

   ```text
   mcp__github__create_pull_request(
     owner="CGFixIT", repo="CyClaw",
     base="main", head="<branch-name>",
     draft=true,
     title="<concise title>",
     body="## What\n...\n## Why / benefit\n...\n## Risk to monitor\n...\n## Merge topology\n- independent\n- merge order: ...")
   ```

   Stacked child (PR `base` must be the **parent branch**, not `main`):

   ```text
   mcp__github__create_pull_request(
     owner="CGFixIT", repo="CyClaw",
     base="<parent-branch-name>", head="<child-branch-name>",
     draft=true,
     title="<concise title>",
     body="## What\n...\n## Why / benefit\n...\n## Risk to monitor\n...\n## Merge topology\n- stacked on <parent-branch> / #<parent-PR>\n- merge parent first, then this PR")
   ```

**Merge order for this skill run:** after drafts exist, prefer merging **lowest
PR number → highest** among the PRs this session opened, except where Step 3.5
stacking requires **parent before child** (parent may have the lower number if
you opened in order; if not, parent-first wins over number order). When a parent
lands on `main` (often via squash), refresh each open child onto current
`origin/main` (or retarget base to `main` after rebase) before merging the next.

If, after scanning, no clear optimization opportunities remain (all covered by
open PRs or out of scope), **confirm that briefly and stop** — do not
manufacture low-value PRs.

---

## Verify (per PR, before pushing)

The repo's documented gate (from `CLAUDE.md`):

```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short
# scope to the touched area when faster, e.g.:
GROK_API_KEY=dummy pytest tests/test_graph.py -q --tb=short
```

> Gotcha (verified this session): a freshly-cloned web container has **no
> Python deps installed** — `pytest` import fails outright. Install first via
> the `/run-cyclaw` or `/sandbox-runtime-verification` skill (note the CyClaw
> install quirks: `torch==2.13.0+cpu` before `requirements.txt`, and
> `pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML`). For
> CI-/docs-/workflow-only PRs that touch no Python, the YAML/lint changes are
> validated by the repo's own CI on push — a local pytest run is not required.

Optionally run `/code-review` on the diff before opening the PR.

---

## Branch Permissions (pre-granted for this skill)

When **CyClaw-Optimize** is invoked, the user pre-authorises creation and push
of per-chunk branches named `agent/cyclaw-optimize-<topic>` (or the env
override of `CYCLAW_AGENT_BRANCH_PREFIX`), each cut from the **topology base**
Step 3.5 assigned (session start / first independent chunk from `origin/main`;
stacked children from their parent branch). Do **not** stop and ask for
permission to push these branches — proceed directly. This applies even when
the session was started on a different designated branch (e.g. `agent/<name>`
from a session-start hook). Each chunk gets its own branch + draft PR; the
designated session branch is used only for the skill file update commit (if
any), never for chunk changes.

---

## Guardrails (CyClaw invariants — do not violate)

- **Never commit to `main` directly** via git or MCP when a feature branch +
  PR exist — it causes add/add rebase conflicts. Always branch → PR → human
  merges.
- **All PRs are draft**; the human decides when to merge/close.
- Do not re-open an area already covered by an open PR; skip the
  null-allowed-origins `config.yaml` item.
- Respect the five security invariants — RAG-first, topology=policy,
  triple-gated external (Grok and/or Claude), audit convergence, soul governance. Never
  weaken a graph-edge policy to "optimize."
- Workflow enhancements must need **no license, secret, or key**.
- Never mutate `data/personality/soul.md` without an explicit human `reason`.

---

## Gotchas

- **MCP PR-list payload is huge** — always reduce to `number`/`title` before
  reading (see Step 2). Reading it raw blows the token budget.
- **No deps in a fresh container** — the test gate needs an install pass first
  (see Verify).
- **Largest-file ≠ worst-file.** `bootstrap.sh` flags big Python files
  (`sync/runner.py`, `graph.py`, `gate.py`, `utils/personality.py`) as
  refactor candidates, but size alone isn't a defect — confirm a real issue
  before proposing a refactor PR.
- **Stay in scope per PR.** The whole point is reviewable, restorable chunks;
  resist bundling unrelated fixes because they're "right there."
- **The 4-minute box is for the initial sweep only** — keep reading code
  afterward to confirm each finding before it becomes a PR.
- **Shared-file PRs can collide.** When ≥2 chunks edit one file (most often
  `.github/workflows/ci.yml`, `config.yaml`, the dependency manifests,
  `CLAUDE.md`, or other hotspots from Step 3.5), cutting every branch from the
  same base without consolidating or stacking will *conflict* if their edits
  touch the same/adjacent lines — and a sloppy conflict resolution drops one
  side. See **Step 3.5**: consolidate the shared-file edits into one PR, or
  stack the later branch on the earlier one, trial-merge the pair before
  opening, and merge in **session order** (prefer lowest PR number → highest,
  with stack parents always before children). After a parent lands, refresh
  open children onto the updated base before merging the next.
- **A broken `main` poisons every PR whose base ultimately includes it.** If
  `main` itself is red (e.g. a bad committed data file or a failing test landed
  earlier), *every* branch that incorporates that tip inherits those failures,
  so the optimize PRs show red CI for reasons unrelated to their own diffs. Land
  the root-cause fix PR first (lowest-order / first in the merge queue), then
  refresh / re-run the rest. Diagnose a child PR's CI red against `main`'s own
  state before assuming the PR caused it.
- **Don't "fix" `EMBED_DEVICE`.** `retrieval/embeddings.py` hardcodes
  `EMBED_DEVICE = "cpu"` on purpose — sentence-transformers auto-selects `mps`
  on Apple Silicon otherwise, which caused a real ranking regression (PR
  #734). A finding proposing GPU/`mps` auto-detection here is wrong.
- **Windows `pathsafe` coverage gap is a known, tracked deferral, not a fresh
  finding.** `agentic/fsconnect/pathsafe.py`'s `_win_*` helpers are `# pragma:
  no cover`, and `tests/test_fsconnect_pathsafe.py` skips entirely on
  `os.name == "nt"` — documented as "FS Phase 4" in
  `docs/work/FSCONNECT_SQL_ROADMAP.md`.
- **macOS/Windows launcher asymmetry — use judgment.** `macos/invoke-cyclaw.sh`
  starts both `gate.py` and `harness.server`; `powershell/Invoke-CyClaw.ps1`
  starts only `harness.server`. May or may not be worth a finding — confirm
  intent before proposing a fix.

---

## Example output (illustrative scan shape — re-scan; do not reuse blindly)

The Step-1 subagent returned several grounded findings and proposed a small
grouping (real file paths, then-current `main`). **Five is not a quota** — keep
only chunks that still look high-leverage after dedup:

1. **Test-coverage completeness** — add `gate`, `retrieval.{hybrid_search,
   indexer,stemmer}`, `utils.personality_db`, and `agentic.*` to `--cov` in
   `.github/workflows/ci.yml`; add the TestClient/httpx deprecation filter to
   `pyproject.toml`. *(tests/CI, small)*
2. **CI action pinning + concurrency** — pin unpinned actions to full SHAs in
   `codeql.yml`/`devskim.yml`/`defender-for-devops.yml`; add
   `cancel-in-progress: true` to `codeql.yml` and `pip-audit.yml`.
   *(CI/security, small)*
3. **LLM client resilience** — exponential-backoff retry in
   `llm/client.py` `LocalLLMClient.generate`/`GrokClient.generate` + tests.
   *(reliability, medium)*
4. **Audit hardening** — redact resolved paths in `agentic/config.py` error
   details; verify the current xAI `grok-4` model name in `config.yaml`; add a
   platform-detection fallback in `sync/scheduler.py`. *(security/robustness,
   small)*

(Docs-only follow-ups can wait or ride with a related chunk — do not open a
fifth PR just to host a monitoring note.)

Each kept chunk is independently reviewable and small/medium. Shared-file
pairs still follow Step 3.5; merge this session's drafts **low PR number →
high**, parent-before-child when stacked.

## Notes

- Feature freeze applies (`CLAUDE.md` §1) — the operative test is "does this polish the portfolio signal or fix a real defect?" New capabilities need explicit user justification first.
- Never push directly to `main`; never force-push without sign-off.
- Re-run `python3 .claude/skills/invariant-guard/check_invariants.py` before opening any PR that touches core files.
