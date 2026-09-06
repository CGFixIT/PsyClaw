---
name: cyclaw-optimize
description: Find and implement evidence-backed CyClaw improvements, deduplicate against current PRs, and publish focused drafts only when requested or already authorized.
---

# Optimize CyClaw

Follow `AGENTS.md`, `$cyclaw-project-guidance`, and
`.codex/Codex_instructions.md`. Distinguish an audit request from an
implementation or publication request; the skill name alone grants neither
remote write access nor permission to expand scope.

## Inspect and select

1. Inspect local changes, fetch `origin/main`, and record the baseline. Use an
   isolated checkout when necessary. Codex development branches are `codex/*`;
   commit identity comes from `utils/agent_identity.py`, not a vendor persona.
2. Read the affected code and actual callers/tests. Start with observed defects,
   measured cost, or a concrete verification gap. File size or a newer package
   release alone is not a finding. Avoid fixed finding/PR quotas.
3. List current open PRs with the available GitHub tools or verified authenticated
   CLI. Inspect relevant diffs and reviews before declaring duplication or a fix.
   Use per-PR review endpoints rather than downloading every historical comment.
4. Record each surviving finding's trigger, evidence, impact, smallest fix, and
   verification. Recheck old accepted-risk/deferral notes against current code
   and threat model; do not carry a permanent skip list from a prior session.
5. Announce the selected changes before editing. Use a bounded independent
   read-only subagent only when delegation is available and authorized; the
   same scan can run locally without an Explore/MCP-specific tool requirement.

## Current areas and traps

- Local destination trust covers both local answer paths; container host models
  need explicit `trusted_hosts`. Keep malformed URLs on the typed error path.
- Mac dotenv security includes source return status, preserved shell export
  state, and absolute BSD stat selection, not just file permission checks.
- Invalid UTF-8 cache/run-record recovery is already implemented in fsconnect
  and the run store. Reproduce any remaining issue before proposing it again.
- CI lints every workflow, including the environment.yml no-op; the actual
  Conda profile is the root `environment.yml`. Defender Bandit includes MCP.
- Embeddings deliberately use CPU for consistent retrieval. Shipped hybrid and
  enabled providers still require consent; armed writer flags do not bypass the
  default-off agentic master switch.

These are source-backed starting points, not a substitute for checking the
current head. Use config, code, and active workflows for all counts and pins.

## Implement and validate

Map `file -> proposed PRs` before branching. Keep disjoint fixes independent,
consolidate related overlapping work, or stack real dependencies with the child
based on its parent branch. Trial-merge shared paths and inspect that all
intended behavior survives. Parent-before-child is required; PR number alone
is not a dependency or a universal merge-order rule.

Apply one coherent fix per review boundary. Use focused regressions and touched
Python lint, then broader CI-equivalent tests for shared runtime/security code.
Docs/workflow-only work needs local syntax and contract checks before publication;
waiting for remote CI is not local validation. Follow `$verify-dep` for install
changes and `$invariant-guard` for core trust boundaries.

If the request is only an audit, return validated findings. If implementation
is authorized, complete it without asking again. If publication is authorized,
use the existing PR branch or create a focused draft with the repository's full
PR template, current base, findings/fixes, risks, validation, and merge order.
Never merge or push main without explicit approval.

Before push, refresh main, rebase if necessary, and validate the resulting head.
Rewriting an existing remote branch requires authorization and an exact expected
SHA lease. Check the actual remote result after an uncertain push before retrying.
Update PR title/body to describe the final behavior and watch current-head CI;
report inherited failures and unavailable platform evidence separately.
