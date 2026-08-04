# `.codex/`

This folder holds Codex-specific operating material for CyClaw. Repo-wide instructions belong in `AGENTS.md`; reusable task playbooks, checklists, and prompt templates can live here.

Start with `.codex/Codex_instructions.md` for the short Codex workflow overlay, then
use the narrower checklist, routine, or skill that fits the task.

## Purpose

Use `.codex/` to help future Codex agents start safely and consistently without copying large project docs. Keep material short, practical, and linked to canonical sources such as `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, and the CI workflows.

Existing `.codex/skills/` content is project-specific skill material. Keep it
Codex-native: avoid hard-coding unavailable external agent tools or connector
function names. A maintained repo-local checker may be reused through its
documented path instead of being copied into a second implementation.
User-facing skills should include `agents/openai.yaml` for Codex UI metadata
and a default prompt. Keep that metadata aligned with the skill name and trigger
description; do not claim older wrappers have metadata until they do.

When the active Codex surface exposes repo skills as slash commands, keep names
short and invocation-friendly, for example `/refactor` or
`/cyclaw-optimize`.

## Available Skills And Routines

`AGENTS.md`'s "Codex Skills And Routines Map" is authoritative for trigger
conditions. This inventory is deliberately duplicated for discovery; update both
in the same change.

### Skills

| Skill | Use it for |
| --- | --- |
| `fable-protocol` | Evidence-first reasoning and hostile self-review on substantive work. |
| `doc-sync` | Reconcile code-derived facts and CyClaw documentation after structural changes. |
| `cyclaw-project-guidance` | Load CyClaw invariants, architecture, and canonical references before substantial work. |
| `verify-dep` | Reconcile dependency/install profiles, Docker, platform installers, and supply-chain checks. |
| `cyclaw-run-cyclaw` | Prepare, index, start, and verify the local RAG gateway. |
| `cyclaw-sandbox-test` | Fresh-main sandbox and mocked API/terminal smoke coverage. |
| `cyclaw-command-status` | Read-only environment and readiness status. |
| `cyclaw-command-run` | Focused endpoint and local-runtime smoke checks. |
| `cyclaw-command-audit` | Privacy-safe audit-log analysis. |
| `cyclaw-command-check-soul` | Read-only soul presence, hash, readability, and drift checks. |
| `OTel-Hardening` | Re-verify telemetry-kill wiring and dependency phone-home controls. |
| `refactor` | Behavior-preserving structural or measured performance work. |
| `cyclaw-optimize` | Evidence-backed, focused optimization PRs. |

### Routines

| Routine | Use it for |
| --- | --- |
| `first-pass-repo-review.md` | Orient in a subsystem or verify setup. |
| `bugfix.md` | Reproduce, diagnose, fix, and verify a defect. |
| `feature.md` | Add behavior while preserving invariants and optional-layer isolation. |
| `refactor.md` | Keep behavior stable while simplifying a narrow area. |
| `test-and-verify.md` | Select and report targeted, CI-parity, or static checks. |
| `pr-review.md` | Review a PR or diff with findings first. |
| `security-review.md` | Review trust boundaries, secrets, routing, dependencies, and optional layers. |

## Prompt Templates

- `prompts/issue-triage.md`
- `prompts/implementation-plan.md`
- `prompts/review-diff.md`
- `prompts/release-notes.md`
- `prompts/pr-agent.md`
- `prompts/pr-review.md`
- `prompts/pr-apply-fixes.md`

Copy these into a Codex prompt and fill in the placeholders. Each template references `AGENTS.md` so the agent starts from repo-specific guidance.

## Checklists

- `checklists/pre-commit.md`
- `checklists/pre-pr.md`
- `checklists/regression-risk.md`

Use checklists as lightweight reminders, not as a substitute for reading the relevant code and CI workflows.

## PR Comment Automation And Git Hooks

- Advisory PR comments route through `.github/workflows/codex.yml`; they are
  read-only review assistance, not merge approval.
- `.github/workflows/codex-apply-fixes.yml` is the owner-gated write path.
  Only an owner reply containing `@codex apply fixes` or
  `@openai-code-agent apply fixes` to a qualifying bot comment may update an
  eligible same-repository PR head. Inspect the generated diff and its CI before
  merging.
- Install the tracked per-clone hooks with `bash scripts/install-githooks.sh`.
  The pre-push hook enforces branch naming and fresh-`origin/main` ancestry for
  feature branches. It does not replace the multi-PR mapping, isolated trial
  merge, or post-green-CI rebase gates in `Codex_instructions.md`.

## Adding New Routines

1. Put task-specific playbooks in `.codex/routines/`.
2. Keep repo-wide rules in `AGENTS.md` instead of duplicating them.
3. Link to canonical docs rather than copying long sections.
4. Include when to use it, inputs, workflow, verification, and final response expectations.
5. Keep routine names lower-case kebab-case.

Do not add compatibility copies of `.codex/Codex_instructions.md` or snapshots of
canonical repo guidance. They drift; link to the live source instead.

## Scratch Work

Generated scratch work, logs, local notes, and temporary outputs should not be committed unless the maintainer explicitly requests them. Use ignored runtime locations or local scratch space for experiments, then summarize results in the final response.
