# Codex Instructions

Use this as the short Codex workflow overlay for CyClaw. Repo truth still lives
in `AGENTS.md`, `CLAUDE.md` (§3 for the six invariants), `docs/THREAT_MODEL.md`,
and the active CI workflows.

## Execution Defaults

- If the request is clear, implement directly with the smallest correct diff.
- Read `AGENTS.md`, and the relevant routine or skill before
  substantive edits.
- Keep progress updates short. State uncertainty and skipped checks plainly.
- If unexpected repo changes appear, stop and ask before editing around them.

## Risk Policy

| Tier | Criteria | Required Safeguard |
| --- | --- | --- |
| Low | Local, reversible, narrow scope | Standard checks |
| Medium | Shared code path, moderate impact, recoverable | Expand verification and note rollback path |
| High | Destructive action, production data/systems, broad impact, force-push | Explicit user approval first |

When uncertain, choose the higher tier.

## Git And PR Workflow

- Before any branch, commit, push, rebase, or PR update meant for GitHub, fetch
  the current remote base: `git fetch origin main --prune`.
- Work from fresh `origin/main`. If the branch no longer contains the latest
  `origin/main`, rebase onto `origin/main`, rerun the relevant checks, then
  push.
- Do not open or update a PR from a stale branch. Check remote-main freshness
  again before the first remote commit and again before drafting the PR if the
  local session was long or `main` likely moved.
- Never commit or push directly to `main`. Use a short-lived feature branch,
  prefer conventional commit messages, and open draft PRs by default.
- Never force-push after a rebase without explicit user approval.
- If another open branch touches a shared file such as CI, config, manifests, or
  repo instructions, trial-merge or rebase locally before opening or updating
  the PR.
- Prefer local `git` for branches, commits, rebases, and pushes. Use GitHub
  tools for PR metadata, comments, and checks; use `gh` only when needed and
  verified to be the real CLI.

## Local Verification Before Commit

- Do not commit until the lightest meaningful local verification passes, or the
  skipped checks are recorded with a concrete reason.
- For docs, skill, routine, prompt, or workflow-only changes, run
  `git diff --check` and any relevant static validation such as markdown review,
  YAML parsing, shell syntax checks, or stale-string scans.
- For skill changes, validate every touched skill folder and confirm its
  `agents/openai.yaml` still names the exact `$skill-name` in `default_prompt`.
- For Python behavior changes, run
  `ruff check --select E,F,I,B,C4,UP,S .` and the most targeted `pytest`
  coverage for the touched area.
- For shared-path, retrieval, security, dependency, CI, or cross-cutting
  changes, expand to the relevant `.github/workflows/ci.yml` command sequence.
- Re-run the relevant checks after rebases, merges, or conflict resolution.
- Never claim a change is ready for GitHub if it was not verified locally.

## Pull Request Discipline

- Draft PRs are the default. Keep each PR to one reviewable concern.
- The PR body should include `What`, `Why`, `Verification`, `Risk to monitor`,
  and any skipped checks or environment limits.
- Before drafting a new PR, fetch `origin/main` again and confirm the branch is
  still current.

## CI Follow-Up

- After opening or updating a PR, check required CI instead of assuming it will
  sort itself out.
- If a required check fails, reproduce the failure locally from the matching
  workflow command before changing code or CI.
- Check whether `origin/main` is already red before blaming the PR branch.
  Broken `main` poisons child PRs.
- If the branch owns the failure, make the smallest root-cause fix, rerun local
  verification, push, and re-check CI.
- If checks are stuck or queued, use the least invasive restart path allowed by
  repo policy.
- Do not leave a red PR behind silently. Either fix it, explain why the failure
  is not branch-caused, or stop and ask.

## Hard Rules

- Never expose credentials, tokens, or secret files.
- Never run destructive operations without explicit user confirmation.
- Never push to `main` via GitHub tools when a feature branch and open PR exist.
- Never approve a force-push without explicit user sign-off.
