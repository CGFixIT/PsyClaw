---
name: architecture-refactor
description: Run one evidence-backed, behavior-preserving CyClaw architecture cleanup at a time. Use when asked to simplify module boundaries, remove duplication, or untangle a measured structural problem; stop when the next step is speculative.
---

# Architecture Refactor

This is a loop, not permission for a repo-wide rewrite. Preserve CyClaw's six
invariants, public behavior, optional-layer isolation, and current project
freeze posture.

## Workflow

1. Start from a clean isolated branch based on current `origin/main`. Read
   `AGENTS.md`, `CLAUDE.md`, the relevant routine, and the affected modules.
2. Establish a baseline: targeted tests, `ruff`, and a small runtime or import
   probe. Record the measurable problem (cycle, duplicate path, slow hot path,
   or hard-to-test boundary) before changing code.
3. Pick exactly one smallest step. Prefer deletion or a narrow extraction over
   new frameworks, speculative interfaces, or broad renames. Map shared files
   before creating related branches.
4. Implement one coherent diff. Do not change routing policy, RAG-first order,
   external fallback gates, audit convergence, soul governance, or module
   isolation as a side effect.
5. Run the focused checks after each step; run invariant checks for core files.
   Review the diff as a hostile maintainer and compare the public API surface.
6. Stop when the stated defect is gone and the next improvement lacks evidence.
   A clean-looking architecture is not a success criterion by itself.

## Verification

```text
ruff check --select E,F,I,B,C4,UP,S <touched paths>
python -m py_compile <touched Python files>
pytest <targeted tests> -q --tb=short -p no:cacheprovider
python .claude/skills/invariant-guard/check_invariants.py
git diff --check
```

Run the full suite when the change crosses gateway, graph, retrieval, config,
or optional-layer boundaries. Report baseline failures separately from new
failures. Do not weaken tests to make a refactor pass.

## Git boundary

One concern per commit and draft PR. Never use `/code-review`, `/tmp` trackers,
or Claude-only commands as required steps; use the active Codex tools and a
workspace-local note when a tracker is genuinely useful. Never force-push.
