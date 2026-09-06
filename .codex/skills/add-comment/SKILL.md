---
name: add-comment
description: Add a small, comment-only readability pass to a bounded CyClaw module. Use when a newcomer needs the reason behind non-obvious code, ordering, thresholds, workarounds, or security gates; never change executable behavior.
---

# Add Comments

Use this for a bounded, reviewable comment-only change. Codex must not turn a
readability request into a refactor.

## Workflow

1. Choose one module or a short file list. If the user asks for the whole repo,
   split the work into independently reviewable chunks.
2. Read the surrounding code, existing comments, and the relevant section of
   `CLAUDE.md`. Explain **why**, not what the next line already says.
3. Do not touch `data/personality/soul.md`, generated/vendor/data/index files,
   or executable lines. Treat `gate.py`, `graph.py`, and sanitizer patterns as
   security-sensitive: only add a comment when the behavior is verified.
4. Add at most two precise sentences per confusing branch, boundary, magic
   value, workaround, or ordering dependency. Do not add TODO/FIXME comments.
5. Review the diff and prove it is comment-only before publishing.

## Verification

Run from the repository root:

```text
python -m ruff check --select E,F,I,B,C4,UP,S <touched Python files>
python -m py_compile <touched Python files>
git diff --check
```

Compare the before/after Python AST when proving a Python edit is comment-only.
Run the invariant checker when describing security-sensitive behavior. A proven
comment-only edit does not require an unrelated full runtime suite. Report
unavailable checks explicitly.

## Git boundary

Keep the branch focused. Commit/publish only within the user's requested scope;
use the repository template for authorized draft PRs. Never push `main` or
include unrelated cleanup or code movement.
