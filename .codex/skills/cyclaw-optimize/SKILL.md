---
name: cyclaw-optimize
description: Find and implement one current, evidence-backed CyClaw improvement in reliability, security, performance, auditability, CI, packaging, or documentation. Use when working in CGFixIT/CyClaw and the user asks to optimize the repository or publish a focused optimization PR.
---

# Optimize CyClaw

Optimize the current repository state, not a remembered finding. The successful
outcome is one small, demonstrated improvement or a clear no-change conclusion;
do not manufacture architecture or speed work.

## Workflow

1. Read `AGENTS.md`, `CLAUDE.md`,
   `.codex/skills/cyclaw-project-guidance/SKILL.md`, and the affected source,
   tests, workflows, and docs.
2. Fetch `origin/main`. Preserve an unrelated or divergent checkout by using a
   clean isolated clone/worktree; never reset it to begin an optimization.
3. Do a time-boxed, read-only sweep for concrete defects, measurable waste,
   stale contracts, missing regression coverage, dependency/config drift, or
   security gaps. Trace relevant callers before retaining a candidate.
4. Check current open PRs and recent changes. Drop candidates already covered,
   inherited from a known-red base, or dependent on an unapproved product or
   security-policy decision.
5. Rank the remaining candidates by evidence, impact, effort, and regression
   risk. Select the smallest one whose benefit can be verified. If none clears
   that bar, report the evidence and stop.
6. Map `file -> planned change` before branching. Consolidate related shared
   file edits into one PR; only stack branches when a later change truly
   depends on an earlier one. Trial any multi-PR merge order in isolation.
7. Make one root-cause change. Reuse existing patterns and dependencies; avoid
   speculative abstraction, unrelated cleanup, and new tunables.
8. Add or update the smallest regression test, run relevant static/runtime
   checks, inspect the final diff, and document residual risk.
9. Commit, push, and open a draft PR only when the user authorized publishing.
   Monitor its CI to a terminal state and address branch-caused failures.

## High-signal areas

- drift among source, `config.yaml`, manifests, docs, tests, and workflows
- core-path optional-module isolation, auth, audit, retry/cancellation, and
  loopback/network boundaries
- dependency/install profile contracts through `$dep-guard` or `$verify-dep`
- sanitizer and adversarial input coverage through `$injection-redteam`
- invariant-sensitive behavior through `$invariant-guard`
- measurable hot paths with repeatable before/after evidence

## Guardrails

- Preserve RAG-first retrieval, topology-as-policy, triple-gated external
  fallback, audit convergence, soul governance, module isolation, auth, and
  loopback defaults.
- Do not add a dependency, configuration switch, or general abstraction without
  a demonstrated need.
- Do not use a green unit suite as proof of live integrations, hostile input,
  cancellation, or platform behavior that was not exercised.
- Keep local, pushed, draft-PR, CI, and merge state distinct. Never push to
  `main`, force-push, or merge without explicit authorization.
