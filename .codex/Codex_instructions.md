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

### Session And Branch Lifecycle

1. Before creating a branch, committing, pushing, rebasing, or updating a PR,
   fetch the remote base: `git fetch origin main --prune`.
2. Start from fresh `origin/main`, never by resetting an unknown or dirty
   checkout. Use an isolated clone or worktree when the current checkout is
   not clean.
3. Record the feature branch, its GitHub base, the intended merge predecessor,
   and the current `origin/main` SHA in the task/PR notes. Never commit or push
   directly to `main`; use short-lived, conventionally named feature branches
   and draft PRs by default.
4. Make the smallest coherent change, run the relevant checks, then commit.
5. Immediately before first push/draft, fetch again. If the branch no longer
   contains current `origin/main`, rebase it, inspect every conflict
   semantically, rerun affected checks, and commit any deliberate resolution.
   Never resolve with blind `ours`/`theirs`.
6. Trial the planned merge order in an isolated worktree before publishing
   related branches. After each trial merge, inspect that both intended hunks
   survive, no conflict markers remain, and changed YAML/JSON/shell/Python still
   validates.
7. Push only after that rebase and trial. The tracked pre-push hook checks
   naming and fresh-`origin/main` ancestry; install it once per clone with
   `bash scripts/install-githooks.sh`.
8. Create or update the draft PR using the repository template, monitor CI, and
   treat an inherited red `main` separately from a branch-caused failure.
9. When PR CI is green, fetch `origin/main` one final time. If it moved,
   rebase, rerun local checks, update the PR only with explicit
   force-with-lease approval, wait for fresh CI, then recommend merge. If it did
   not move, record that no-op freshness check before recommending merge.

Never force-push after a rebase without explicit user approval. Prefer local
`git` for branches, commits, rebases, and pushes; use GitHub tools for PR
metadata, comments, and checks, and use `gh` only after verifying it is the
real authenticated CLI.

### Multiple Related PRs (Claude CyClaw-Optimize Step 3.5)

Before branching, map every planned `file -> chunks` and flag files touched by
more than one PR, especially workflows, manifests, Docker/Compose, config, and
agent instructions.

- **Consolidate** when related changes share a file or cannot be reviewed or
  validated independently. One PR is safer than artificial parallelism.
- **Stack** only when the child genuinely depends on the parent: create the
  child from the parent branch, set its GitHub base to the parent PR, validate
  parent-first, merge parent first, then rebase the child onto fresh
  `origin/main`, change its GitHub base to `main`, and rerun its checks.
- **Trial chronological merges** from fresh `origin/main` in an isolated
  worktree: oldest/predecessor first, inspect, then the next candidate. A clean
  Git merge is not proof that both semantic changes remain.
- **Resolve surgically.** Read both sides and the surrounding code, preserve
  both compatible hunks, and add targeted validation for the combined result.
  Stop and ask when the two changes imply different behavior or security policy.

### PR Comment Apply-Fixes Boundary

`.github/workflows/codex-apply-fixes.yml` is an owner-gated write path for a
specific qualifying bot comment. The phrases `@codex apply fixes` and
`@openai-code-agent apply fixes` are not broad authorization: inspect the
resulting PR-head diff, rerun/review CI, and merge only through the normal human
decision. Advisory `@codex` requests remain read-only.

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
- Before opening a PR, read `.github/PULL_REQUEST_TEMPLATE.md` and write the
  body into its sections (Proposed changes / Invariant impact, Types of
  changes, Benefits / why, Risks to monitor, Checklist, Further comments) --
  not a separate ad hoc structure. Its own "Notes for contributors" section
  already allows a lighter pass for out-of-band/docs-only changes.
- At minimum, whatever the PR's shape, the body must cover what changed, why,
  verification run, and risks to monitor, and any skipped checks or
  environment limits -- the template's sections are the canonical place to
  say that, not a substitute for it.
- Before drafting a new PR, fetch `origin/main` again and confirm the branch is
  still current.
- At the end of implementation, rebase the feature branch onto the latest
  `origin/main` before push/draft and rerun affected checks. After draft-PR CI
  is green, fetch again; if `main` moved, rebase, rerun checks and CI, then
  recommend merge. Never force-with-lease without explicit approval.

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
