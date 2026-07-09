---
name: cyclaw-optimize
description: >-
  Codex-native CyClaw optimization workflow. Use when working in CGFixIT/CyClaw and the user asks Codex to optimize CyClaw, find competitive or trade-bot advantages, harden CI, audit code/security/financial-risk assumptions, propose focused improvements, or open optimization PRs against main.
---

# CyClaw Optimize

Use this skill to scan the CyClaw `main` branch for concrete optimization,
security, reliability, financial-risk, auditability, and maintainability
opportunities, then turn the best findings into focused draft pull requests.

This is the Codex adapter for an earlier agent workflow. Treat the instructions
below as authoritative for Codex. Do not edit legacy agent source files unless
the user explicitly asks for that.

## Codex Operating Rules

- Run command steps only when the user's request clearly asks Codex to execute
  the workflow, make changes, or open PRs. For read-back, explanation, or review
  requests, inspect only.
- All shell, network, git, and GitHub actions remain governed by the active
  Codex sandbox, approval, and authentication rules. User intent to open PRs is
  not a bypass for required approvals.
- Use `.codex/skills/cyclaw-optimize/bootstrap.sh` as the bundled harness path.
- Use local `git` for branch creation, commits, and pushes.
- Prefer the GitHub app/plugin for PR and issue data when available. Use `gh`
  as the fallback for listing PRs, checking auth, and creating draft PRs.
- Do not rely on legacy agent tool names or hard-coded MCP function names. Use
  Codex tools, `rg`, shell file reads, GitHub plugin tools, or `gh`
  equivalents.
- Do the initial scan directly in Codex. If a Codex multi-agent tool is
  available and appropriate, it may be used for an independent read-only pass,
  but the workflow must still work without it.

## CyClaw Context

CyClaw includes a FastAPI RAG gateway (`gate.py`), LangGraph security topology
(`graph.py`), ChromaDB + BM25 hybrid retrieval, local LLM via LM Studio with a
triple-gated Grok/xAI fallback, an MCP hybrid server, the `agentic/` GitHub
layer, `agentic/fsconnect/` and `agentic/sqlconnect/` local-data connectors,
optional `guardrails/`, and the out-of-band `sync/` Dropbox pipeline.

Read code for leverage:

- performance
- security
- financial-risk and oversight assumptions
- auditability
- maintainability

Current project posture matters:

- portfolio and evidence packaging work outrank speculative feature work
- the business path is still hypothesis-stage; do not treat demand-side stats as
  PMF proof
- if a change does not clearly improve reliability, clarity, auditability,
  packaging, or demo quality, question whether it should exist

## Codex Bias For Unique Findings

Claude already has a broad optimization scan. Use Codex for the kinds of
findings it is better at:

- exact cross-file drift between `README.md`, `AGENTS.md`, `CLAUDE.md`,
  `.codex/`, tests, workflows, and the current code
- repo-wide command/path drift, especially stale `.claude` execution paths in
  Codex-facing docs or skills
- shared-file conflict risk across proposed PR chunks before implementation
- manifest, workflow, and Docker drift across `pyproject.toml`,
  `requirements.txt`, `constraints.txt`, `Dockerfile`, and CI
- entrypoint and optional-layer isolation violations involving `gate.py`,
  `graph.py`, `mcp_hybrid_server.py`, `agentic/`, `sync/`, and `guardrails/`
- Windows-vs-bash operational gaps for setup, smoke, and local verification
- doc claims that are no longer true in the current repo

Bias away from low-signal duplicate findings such as "big file, maybe refactor"
unless you can show the concrete defect and the smallest credible fix.

## Step 0 - Bootstrap

From the repo root, run the bundled harness when the user has asked to execute
the optimization workflow:

```bash
bash .codex/skills/cyclaw-optimize/bootstrap.sh codex/cyclaw-optimize-<topic>
```

On Windows without Bash, perform the equivalent steps directly in PowerShell:

```powershell
git fetch origin main --prune
git switch -c codex/cyclaw-optimize-<topic> origin/main
git status -sb
```

Omit the branch argument for a local inventory against the current branch.
With a branch argument, the script fetches `origin/main` and creates or checks
out the requested branch without force-resetting existing branch work.

The harness does not force a Claude git identity. It prints the current git
identity and only changes it when explicit `CODEX_GIT_USER_NAME` or
`CODEX_GIT_USER_EMAIL` environment variables are set.

## Step 1 - Read-Only Scan

Spend a short, time-boxed pass on concrete findings. Keep the scan read-only:
do not edit files while discovering candidates.

Sweep these areas:

- `.github/workflows/*.yml`: caching, action SHA pinning, concurrency,
  `cancel-in-progress`, matrix gaps, and license/secret-free hardening
- `tests/`: coverage gaps, brittle fixtures, logic errors, missing assertions
- `agentic/` and `sync/`: inefficient loops, redundant logic, weak
  error-handling, and financial or oversight-assumption risk
- `llm/client.py` and `graph.py`: Grok/xAI model names, endpoints, retry,
  timeout, and performance behavior
- `config.yaml`: risky defaults and configuration drift
- `requirements.txt`, `constraints.txt`, and `pyproject.toml`: loose or stale
  pins, missing dependencies, and test/tooling mismatch
- `README.md`, `AGENTS.md`, `CLAUDE.md`, and `.codex/`: stale commands,
  outdated architecture maps, business-posture drift, and repo-doc mismatch
- readability and auditability issues anywhere in the repo

Return 6-10 distinct findings. Each finding must include:

- title
- file path and line number
- one-line description
- category
- effort: small or medium

End the scan with a suggested grouping into about five PR-sized chunks. Cite
real code only. Do not invent findings.

## Step 2 - Deduplicate Against Open PRs

Before choosing focus areas, list open PRs and drop candidate areas already
covered by an open PR. Also skip the known-stale "null allowed origins in
`config.yaml`" idea.

Use the best available GitHub path:

```bash
gh pr list --repo CGFixIT/CyClaw --state open --json number,title
```

Verify `gh` is the official GitHub CLI before using it; some Windows
environments may have a different executable named `gh`. If using a GitHub
connector, request only `number` and `title` when possible.
Do not dump large raw PR payloads into context.

## Step 3 - Select Focus Areas

Choose about five deduplicated chunks. Each chunk should be independently
reviewable:

- one or two major concepts, or
- three to five closely related minor tasks

For each chunk, state one line covering the files touched and why the change has
leverage: performance, security, financial-risk, auditability, or
maintainability.

If no clear opportunities remain after deduplication, say so and stop. Do not
manufacture low-value PRs.

## Step 3.5 - Plan Shared Files

Before creating implementation branches, build a file-to-chunks map and find
files touched by multiple chunks. Common shared files are
`.github/workflows/ci.yml`, `config.yaml`, `requirements.txt`,
`constraints.txt`, `pyproject.toml`, and `CLAUDE.md`.

For each shared file, choose one strategy:

- Consolidate all edits to that file in one chunk or a dedicated wiring PR.
- Stack later branches on earlier branches and set the child PR base to the
  parent branch.

If two branches edit the same shared file, trial-merge them locally before
opening PRs. Confirm both edits survive, no conflict markers exist, and any
structured file still parses.

Example checks:

```bash
git checkout -B _trial origin/main
git merge --no-ff origin/<branch-a>
git merge --no-ff origin/<branch-b>
grep -q '<a-marker>' <shared-file>
grep -q '<b-marker>' <shared-file>
grep -rc '<<<<<<<' <shared-file>
python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"
git checkout main
git branch -D _trial
```

## Step 4 - Implement One Draft PR Per Chunk

For each chunk:

1. Create a focused branch. Default branch names should look like
   `codex/cyclaw-optimize-<topic>`.
2. Make the smallest coherent change set for the chunk.
3. Verify the touched area.
4. Stage only intended files.
5. Commit with a clear message.
6. Push the branch.
7. Open a draft PR against `main`, unless Step 3.5 requires a stacked base.

Draft PR bodies should include:

- what changed
- why it helps
- validation performed
- risk to monitor

With `gh`, a typical fallback is:

```bash
gh pr create --repo CGFixIT/CyClaw --base main --head <branch> --draft \
  --title "<concise title>" --body-file <body-file>
```

On network failure during push, retry up to four times with exponential backoff:
2s, 4s, 8s, and 16s.

## Verification

Prefer the narrowest meaningful check for the touched area. The documented
CyClaw Python gate is:

```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short
```

Use a narrower test file when appropriate, for example:

```bash
GROK_API_KEY=dummy pytest tests/test_graph.py -q --tb=short
```

Fresh clones may not have Python dependencies installed. If dependencies are
missing, install them only when the task requires runtime validation and the
active approval rules allow it. For docs, skill, or workflow-only PRs, prefer
static validation such as markdown review, YAML parsing, or shell syntax checks.

## Guardrails

- Never commit directly to `main`.
- Open draft PRs; the human decides when to merge or close them.
- Keep each PR reviewable and restorable.
- Do not re-open work already covered by an open PR.
- Skip the stale null-allowed-origins `config.yaml` item.
- Respect the CyClaw invariants: RAG-first, topology-as-policy,
  triple-gated external Grok, audit convergence, and soul governance.
- Do not weaken graph-edge policy in the name of optimization.
- Workflow enhancements must not require a new license, secret, or key.
- Never mutate `data/personality/soul.md` without an explicit human reason.

## Gotchas

- Large PR-list payloads waste context; reduce to PR number and title.
- Largest file does not mean worst file. Confirm a real defect before proposing
  refactors of `sync/runner.py`, `graph.py`, `gate.py`, or
  `utils/personality.py`.
- Shared-file PRs can conflict even when each change is valid. Use the
  consolidate-or-stack plan before opening PRs.
- A broken `main` poisons child PR CI. Check whether failures reproduce on
  `main` before attributing them to a child PR.

## Example Finding Groups

This example shows the desired shape only. Re-scan current `main` instead of
reusing these findings blindly.

1. Test coverage wiring for gateway, retrieval, personality DB, and `agentic/`.
2. CI action pinning plus concurrency hardening.
3. LLM client retry and timeout resilience.
4. Audit hardening around path redaction, Grok model monitoring, and scheduler
   platform detection.
5. Documentation for security-sensitive CI and Grok model-name monitoring.
