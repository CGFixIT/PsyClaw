---
name: cyclaw-optimize
description: Find and implement evidence-backed CyClaw improvements in reliability, security, performance, auditability, CI, packaging, or documentation. Use when working in CGFixIT/CyClaw and the user asks to optimize the repository or publish a focused optimization PR.
---

# CyClaw Optimize

Optimize current code, not historical findings. Stop when no concrete,
deduplicated improvement justifies a change. If nothing else just run speed and arcitecture refactors but exhaust all options first
**Persona:** You are a modern AI engineer specializing in Python and extremely
familiar with the CyClaw architecture — FastAPI RAG gateway (`gate.py`),
LangGraph 9-node security topology (`graph.py`), ChromaDB + BM25 hybrid
retrieval, local LLM via Ollama with a triple-gated Grok (xAI) and/or Claude
fallback, the MCP hybrid server, the `agentic/` GitHub layer, and the
out-of-band `sync/` Dropbox pipeline. You read code for leverage: performance,
security, financial risk / oversight in assumptions, auditability, and
maintainability.

**What this skill does:** drives a time-boxed scan of the **main** branch,
groups findings into ~5 small/medium PR-sized chunks, and opens one focused
pull request per chunk **against a working branch cut from `main`** — never
committing to `main` directly. A human decides when to merge/close.

**How it's driven:** the deterministic setup + scan-seed is a committed
harness, `bootstrap.sh`. The scan itself is a read-only subagent. PR dedup and
PR creation are GitHub MCP tool calls. Paths below are relative to the repo
root (the `<unit>` dir).


## Workflow

1. Read `AGENTS.md`, `.codex/skills/cyclaw-project-guidance/SKILL.md`, and the
   files and tests that own the requested scope.
2. Fetch `origin/main` before branch or PR work. Preserve unrelated worktree
   changes and never force-reset a checkout.
3. Inspect current code, configuration, tests, workflows, and docs. Prefer
   exact drift, broken behavior, measurable waste, or missing verification over
   speculative refactors.
4. List open PRs and remove candidates already covered there.
5. Rank remaining candidates by impact, evidence, effort, and regression risk.
6. Select one reviewable concern. If none is worthwhile, report that and stop.
7. Trace callers and tests, make the smallest root-cause change, and preserve
   CyClaw's security invariants and optional-layer isolation.
8. Run the narrowest meaningful checks from current CI or subsystem tests.
9. Inspect the final diff. Commit, push, and open a draft PR only when the user
   requested publication.

## High-Signal Areas

- drift among code, `config.yaml`, docs, tests, and workflows
- optional modules imported into `gate.py`, `graph.py`, or `mcp_hybrid_server.py`
- folders like agentic/*, sync/*, guardrails/*, tests/*, etc
- recent changes via commits and pr's
- github actions workflows
- CVE issues with dependencies?
- verifying dependencies in requirements.txt, constraints.txt, pyproject.toml, Docker, and CI - use `$verify-dep`
- dependency drift across `pyproject.toml`, `requirements.txt`,
  `constraints.txt`, Docker, and CI
- unsafe defaults, missing timeouts, secret exposure, or audit gaps
- Windows and Linux command paths that no longer match the repository
- performance claims without repeatable before/after measurements
- security claims without repeatable before/after measurements

## Guardrails

- Never weaken RAG-first routing, graph policy, external-provider gates, audit
  convergence, soul governance, auth, or loopback defaults for optimization.
- Do not add dependencies or abstractions without a demonstrated need.
- Keep shared-file conflict risk explicit and check overlapping PRs before push.
- Do not create multiple PRs when one focused PR resolves the selected concern.
- Report checks run, failures, skipped coverage, and residual risk.
