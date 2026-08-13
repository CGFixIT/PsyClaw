---
description: Run the current macOS-first, Windows-aware CyClaw optimization workflow and publish only evidence-backed changes that the user authorized.
---

Run the canonical CyClaw optimization workflow in
`.claude/skills/CyClaw-Optimize/SKILL.md`. Apply any scope in `$ARGUMENTS`.

Start from a freshly fetched `origin/main`, preserve dirty/divergent checkouts,
deduplicate against current open PRs, and use the Step 3.5 shared-file topology
rule before branching. Prefer one minimal demonstrated improvement or an honest
no-change result. Treat macOS as the primary operator path and Windows as a
blocking close secondary. Keep mocked, host-real, and live-external evidence
distinct.

Do not infer publication authority from this command alone. Never push to
`main`, force-push, merge, or request review without explicit authorization.
