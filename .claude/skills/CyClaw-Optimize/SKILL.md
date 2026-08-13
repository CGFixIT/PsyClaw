---
name: CyClaw-Optimize
description: Find and implement current, evidence-backed CyClaw improvements, with macOS as the primary operator path and Windows as a close secondary. Use for reliability, security, performance, CI, packaging, documentation, RAG, harness, fsconnect, GitHub agent, Dropbox sync, or other optimization work against current main.
---

# CyClaw Optimize

Find the smallest current improvement that demonstrably earns a change. One
focused PR is better than several thin PRs, and a no-change conclusion is valid.
Do not optimize remembered findings, revive retired paths, or infer live-service
behavior from unit tests.

CyClaw is an offline-first Python 3.12 RAG gateway with a retrieval-only MCP
server. The core uses FastAPI, a 10-node LangGraph policy topology, ChromaDB +
BM25 + RRF retrieval, local Ollama generation, and separately gated Grok and
Claude fallbacks. Optional `agentic/`, `sync/`, harness, and Telegram surfaces
remain out of the core request path. Default-off guardrails enter through
`utils.guardrail_bridge` without direct core imports.

## Authority and current-state rule

Read these before retaining a candidate:

1. `AGENTS.md`, `CLAUDE.md`, `INVARIANTS.md`, and `docs/THREAT_MODEL.md`.
2. `.codex/skills/cyclaw-project-guidance/SKILL.md` and the relevant routine.
3. Current source, config, tests, workflow, and subsystem docs for the area.
4. Open PRs, recent commits, and current `origin/main`.

Current code and executable tests outrank stale prose. If they disagree, fix or
explicitly report the documentation drift. The tracked canonical Claude skill
is this file; a machine-side `.agents` copy is a port, not a second repo source.

Use this evidence ladder in findings and PR bodies:

1. **Static/configured** - code, config, or a parser check only.
2. **Simulated/mocked** - platform calls, subprocesses, network, or services are
   replaced by fixtures.
3. **Host-real** - the actual OS, filesystem, process, socket, or embedded
   component ran locally or in CI.
4. **Live external** - the real account, API, daemon, device, or managed service
   was exercised.

Never promote evidence to a higher level than the check actually reached.

## Platform and feature map

Use macOS as the primary operator path and Windows as a close secondary. Linux
still matters for CI and services, but it is not the focus of this skill.

| Surface | macOS primary | Windows secondary | Do not overclaim |
|---|---|---|---|
| Core RAG + MCP | Real embedded ChromaDB, BM25, and RRF run in blocking CI. Use plain `torch==2.13.0` on supported Apple Silicon/macOS. | The same real retrieval smoke is blocking. Install `torch==2.13.0+cpu` from the PyTorch CPU index first. | The RAG smoke stops before generation; mock Ollama is not a live Ollama daemon. MCP remains retrieval-only. |
| Harness | `macos/install-cyclaw.sh` installs `~/.CyClaw`; `invoke-cyclaw.sh` starts the gateway on `8787` and harness on `8790`, loopback only. | `powershell/Install-CyClaw.ps1` installs `%USERPROFILE%\.CyClaw`; blocking CI runs a live gateway + harness process smoke under `pwsh`. | macOS CI tests install/fsconnect but does not launch the full dual-process script. Windows smoke does not perform a real agent mutation. |
| Filesystem integration | Default install prepares `~/CyClaw-FS` and enables exactly list/stat/read there. Writes and indexing stay off. Darwin uses held-fd descent, APFS identity checks, typed permission errors, Apple metadata filtering, `/Volumes` opt-in, and Finder reveal. | Shipped config stays disabled with empty roots. There is no Windows setup helper. Native list/stat/read use checked Windows handles; writes are hard-refused before root creation. | Real APFS disk-image CI is not a TCC prompt, real iCloud-evicted file, physical removable/network disk, Time Machine backup, or loaded LaunchAgent. |
| GitHub coding agent | Optimize the governed `real-repo-run` clone -> plan -> patch -> verify -> human-decision path and harness console. Commit, push, and draft publish are separate gates. | The same Python path is portable and its smoke runs in the full suite. | CI uses local git plus a fake `gh`; it does not use a live GitHub account or model. DeepAgents is retained for compatibility/tests, not new feature work. |
| Dropbox sync | Real default-off rclone code uses an App-Folder-scoped Dropbox remote; scheduling uses cron. Pull is the safe default and soul is excluded. | The same subsystem schedules through Task Scheduler and a generated batch file. | Self-test and pytest mock the remote and do not prove rclone OAuth or live Dropbox transfer. |
| CI/install | `macos-latest` is a blocking gate with installer, adversarial path, fsconnect, RAG, and full-suite coverage. | `windows-latest` is blocking; Windows PowerShell 5.1 installer and native fsconnect lanes are also blocking. | A green matrix does not erase platform skips or prove external services. Use `-rs` or dedicated verbose lanes when skip visibility matters. |

Other current optional surfaces deserve the same discipline:

- SQL connectors are default-off and hard read-only; ordinary CI is not a live
  MSSQL/Postgres connector test. The pgvector service job is Linux-only.
- Memory features ship off and use governed propose/apply boundaries. Account
  and session machinery exists, but do not assume every request path enforces it.
- Guardrails are default-off; offline heuristic rails and mocked optional NeMo
  coverage are not a live NeMo deployment.
- Telegram has mocked Bot API coverage, not recorded live operator validation;
  partial media work is not a cross-platform production contract.

## Workflow

### Step 0 - Fresh-main preflight

Preserve a dirty, divergent, or unrelated checkout. Use a clean isolated clone
or worktree instead of resetting it. Verify Git and GitHub authentication without
printing tokens, then fetch the exact remote-tracking ref:

```bash
git fetch origin +refs/heads/main:refs/remotes/origin/main
git rev-parse --verify origin/main
git status --short --branch
```

Stop if fetch/auth fails, the isolated tree is dirty, or the ref is unrelated.
Record the branch-point SHA. The optional Bash scan harness is:

```bash
bash .claude/skills/CyClaw-Optimize/bootstrap.sh <driver>/cyclaw-optimize-<topic>
```

Use the driver prefix required by `.github/PULL_REQUEST_TEMPLATE.md` (`codex/`,
`claude/`, and so on). On Windows without Bash, run the equivalent Git commands
directly; do not add a Bash dependency to the product.

### Step 1 - Read-only sweep

Time-box the first sweep, optionally delegating independent read-only audits.
Inspect only enough breadth to find grounded candidates:

- `gate.py`, `graph.py`, `retrieval/`, `llm/`, and `mcp_hybrid_server.py`
- `harness/`, `macos/`, `powershell/`, and platform integration tests
- `agentic/`, especially `fsconnect/`, `sqlconnect/`, and `real_repo_loop.py`
- `sync/`, `guardrails/`, memory/auth, Telegram, and their operator docs
- `config.yaml`, manifests, installers, workflows, and security controls
- tests for the exact behavior, including skips and mocked boundaries

Look for reproduced defects, measurable waste, stale contracts, missing
regressions, dependency/config drift, audit gaps, and unsafe defaults. File size,
disabled-by-default code, or novelty alone is not a defect. Under the feature
freeze, new behavior needs an explicit mechanism that improves the portfolio or
fixes a real operator problem.

### Step 2 - Verify and deduplicate candidates

Trace callers and reproduce or statically prove each candidate. Check recent
commits and list current open PRs using the available GitHub integration or the
official authenticated `gh` CLI. Reduce large responses to number, title, head,
and base. Drop work that is already covered, inherited from a known-red base,
retired, or blocked on an unapproved product/security decision.

Rank survivors by evidence, impact, effort, and regression risk. Select the
smallest root-cause change whose benefit can be verified. Stop honestly if none
clears that bar.

### Step 3 - Announce focused chunks

State each proposed chunk, owned files, benefit, verification, and residual
risk. There is no PR quota. Keep one or a small number of independently
reviewable chunks; do not split work to manufacture activity.

### Step 3.5 - Map shared files and branch topology

Before branching, build `file -> chunks`. For every file touched by more than
one chunk, choose one strategy:

- **Consolidate** related edits to the shared file in one chunk.
- **Stack** a truly dependent child on the parent branch and use the parent
  branch as the child's PR base.

Do not cut sibling branches from `main` and hope adjacent edits merge. Before
publishing related branches, trial-merge them in the planned order in a
throwaway worktree or clone. Confirm both changes survive, conflict-marker count
is zero, and affected YAML/TOML/JSON/shell still parses. Parent branches merge
before children; otherwise publish and merge in the planned human order.

### Step 4 - Implement the minimum change

Reuse existing patterns and dependencies. Avoid speculative abstractions, new
knobs, broad rewrites, and unrelated cleanup. Add the smallest regression that
would fail without the fix. Keep documentation claims tied to the evidence
level actually exercised.

### Step 5 - Verify proportionately

Always inspect the final diff and run the narrowest relevant checks:

```bash
git diff --check
python .claude/skills/invariant-guard/check_invariants.py
```

For touched Python, run the configured Ruff selection and targeted pytest:

```bash
python -m ruff check --select E,F,I,B,C4,UP,S <touched-files>
python -m pytest <target-tests> -q --tb=short
```

Set the dummy test key once when a selected test imports the gateway:

```bash
# Bash
export GROK_API_KEY=dummy

# PowerShell
$env:GROK_API_KEY = "dummy"
```

Useful platform/feature routes include:

```bash
# macOS host-real filesystem/setup coverage
python -m pytest tests/test_fsconnect_macos_real.py tests/test_macos_fsconnect_setup.py tests/test_macos_scripts.py -vv -rs --tb=short

# Windows host-real handle authority (add -p no:cacheprovider locally if needed)
python -m pytest tests/test_fsconnect_pathsafe_windows.py -vv -rs --tb=short

# embedded retrieval on either supported desktop OS
python -m tests.ci_rag_smoke

# governed local-git/fake-gh coding loop; not live GitHub
python -m pytest tests/test_agentic_real_repo_run_smoke.py tests/test_agentic_real_repo_run_cli.py -q --tb=short

# Dropbox/rclone logic; still not a live Dropbox transfer
python -m pytest tests/test_sync_*.py -q --tb=short
```

PowerShell does not reliably expand pytest globs; enumerate
`tests/test_sync_*.py` and pass the resulting paths there.

For docs/skills, run `python .claude/skills/doc-sync/doc_sync.py`; run
dependency guards when install guidance changes and `bash -n` for touched shell.
Expand to `python -m pytest tests/ -q --tb=short` for cross-cutting or
release-risk changes. Explain every skip and unavailable physical/live check.

### Step 6 - Publish only when authorized

Fetch `origin/main` again before the first push and rebase independent work if
it moved. Re-run affected checks after any rebase. Stage intentionally, commit
with the repo's title convention, push only the feature branch, and open a
draft PR with the complete template. Record branch-point SHA, topology, commands,
results, skipped checks, and residual risk.

Monitor every required CI check to a terminal state. Fix branch-caused failures
with follow-up commits; distinguish inherited-red `main`. Never push to `main`,
force-push, merge, or request review unless separately authorized.

## Guardrails

- Preserve I1 RAG-first retrieval, I2 topology-as-policy, I3 triple-gated
  external fallback, I4 audit convergence, I5 soul governance, and I6 optional
  module isolation. Under I6, `gate.py`, `gate_ops.py`, `gate_auth.py`,
  `gate_memory.py`, `graph.py`, and `mcp_hybrid_server.py` must not import
  `agentic`, `sync`, `guardrails`, `harness`, or `telegram`; those out-of-band
  modules must not import the core request-path modules either.
- Preserve loopback defaults, fail-closed boundaries, redaction, human commit
  decisions, and default-off out-of-band integrations.
- Do not add dependencies, secrets, hosted services, licenses, or configuration
  switches without demonstrated need and complete install/CI alignment.
- Do not mutate `data/personality/soul.md` unless the task explicitly concerns
  governed soul content.
- Keep local, committed, pushed, draft, CI, review, and merged state distinct.

## Gotchas

- macOS uses plain Torch; Windows/Linux use the pinned CPU wheel and CPU index.
- The generic shipped fsconnect template is disabled and broader than the
  intentionally narrow macOS installed overlay. Compare the right state.
- Real APFS disk-image tests are stronger than monkeypatching but weaker than
  TCC, iCloud eviction, physical `/Volumes`, or Time Machine execution.
- A local git clone plus fake `gh` is not live GitHub. A sync self-test is not
  live Dropbox. A loopback mock server is not live Ollama.
- The dedicated real-repo job may be Linux-only while full-suite discovery runs
  the portable test on macOS and Windows. Read both workflow and test skips.
- `fail-fast: false` does not make an OS non-blocking; `continue-on-error` does.
- A largest-file list is a scan seed, not proof that a refactor is warranted.
- A broken `main` can make every child red. Compare failures before editing the
  branch, and do not smuggle unrelated base repairs into the PR.
