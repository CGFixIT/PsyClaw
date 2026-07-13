---
name: cyclaw-optimize
description: Find and implement evidence-backed CyClaw improvements in reliability, security, performance, auditability, CI, packaging, or documentation. Use when working in CGFixIT/CyClaw and the user asks to optimize the repository or publish a focused optimization PR.
---

# CyClaw Optimize

Optimize current code, not historical findings. Stop when no concrete,
deduplicated improvement justifies a change.

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
- dependency drift across `pyproject.toml`, `requirements.txt`,
  `constraints.txt`, Docker, and CI
- unsafe defaults, missing timeouts, secret exposure, or audit gaps
- Windows and Linux command paths that no longer match the repository
- performance claims without repeatable before/after measurements

## Guardrails

- Never weaken RAG-first routing, graph policy, external-provider gates, audit
  convergence, soul governance, auth, or loopback defaults for optimization.
- Do not add dependencies or abstractions without a demonstrated need.
- Keep shared-file conflict risk explicit and check overlapping PRs before push.
- Do not create multiple PRs when one focused PR resolves the selected concern.
- Report checks run, failures, skipped coverage, and residual risk.
