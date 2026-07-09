---
name: karpathy-guidelines
description: Behavioral guidelines to reduce common LLM coding mistakes. Use when writing, reviewing, or refactoring code to avoid overcomplication, make surgical changes, surface assumptions, and define verifiable success criteria.
license: MIT
---

# Karpathy Guidelines

Behavioral guidelines to reduce common LLM coding mistakes, derived from [Andrej Karpathy's observations](https://x.com/karpathy/status/2015883857489522876) on LLM coding pitfalls.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## Guardrails

This is a behavioral-discipline skill, not an executor — it never edits files or
runs commands on its own. It does not supersede CyClaw's binding rules:

- It cannot loosen or reinterpret any of the six invariants in `CLAUDE.md` §3
  (RAG-first, topology=policy, triple-gated external fallback, audit convergence,
  soul governance, module isolation). "Simplicity First" / "Surgical Changes" never
  justify touching a graph edge, `banned_patterns`, or `soul.md` without following
  the existing escalation rules in `CLAUDE.md` §7.
- "Goal-Driven Execution" pairs with, and does not replace, this repo's actual test
  suite (`GROK_API_KEY=dummy pytest tests/ -q --tb=short`) and
  `.claude/skills/invariant-guard/check_invariants.py`.
- Feature-freeze mode (`CLAUDE.md` §1) still governs: "Simplicity First" is about
  *how* to implement something already justified, not license to add scope.

## Gotchas

- These guidelines bias toward asking/pausing over guessing. In this repo's own
  escalation model (`CLAUDE.md` §7), only High-tier ambiguity warrants a stop — for
  Low/Medium tier, state the assumption and proceed on the smallest reversible
  interpretation. Prefer CyClaw's tiering when the two disagree.
  "Surgical Changes" mirrors an existing CyClaw rule (§4: "the diff touches only
  files named in the task").
- Sourced verbatim from
  [multica-ai/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills)
  (plugin `andrej-karpathy-skills`, marketplace `karpathy-skills`, MIT licensed);
  this repo does not use the Claude Code plugin-marketplace install path, so it is
  vendored here as a plain skill instead.
