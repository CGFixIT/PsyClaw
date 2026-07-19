# `.codex/`

This folder holds Codex-specific operating material for CyClaw. Repo-wide instructions belong in `AGENTS.md`; reusable task playbooks, checklists, and prompt templates can live here.

Start with `.codex/instructions.md` for the short Codex workflow overlay, then
use the narrower checklist, routine, or skill that fits the task.

## Purpose

Use `.codex/` to help future Codex agents start safely and consistently without copying large project docs. Keep material short, practical, and linked to canonical sources such as `AGENTS.md`, `CLAUDE.md`, `.github/copilot-instructions.md`, and the CI workflows.

Existing `.codex/skills/` content is project-specific skill material. Keep it
Codex-native: avoid hard-coding legacy agent tools, stale `.claude` execution
paths, or connector function names that may not exist in a given session.
Each skill includes `agents/openai.yaml` for Codex UI metadata and a default
prompt. Keep that metadata aligned with the skill name and trigger description.

When the active Codex surface exposes repo skills as slash commands, keep names
short and invocation-friendly, for example `/refactor` or
`/cyclaw-optimize`.

## Available Skills

- `skills/fable-protocol/` - reasoning-discipline layer ported from the Claude
  fable-protocol: epistemic calibration, speculation marking, premise testing,
  hostile self-review, security pass on every generated artifact,
  anti-sycophancy, and shipping-first prioritization.
- `skills/cyclaw-project-guidance/` - load current CyClaw operating context and
  canonical repo docs before substantial work.
- `skills/cyclaw-run-cyclaw/` - prepare, run, smoke-test, and interact with the
  local CyClaw FastAPI RAG server.
- `skills/cyclaw-sandbox-test/` - clone `origin/main`, mock Ollama, and
  smoke-test CyClaw gateway plus terminal/API surfaces before PRs.
- `skills/cyclaw-command-status/` - run a read-only environment, config, index,
  soul, telemetry, and live-health status check.
- `skills/cyclaw-command-run/` - run focused smoke checks against an existing
  checkout or server without performing setup.
- `skills/cyclaw-command-audit/` - summarize `logs/audit.jsonl` through
  `metrics.py` and flag audit anomalies or privacy risks.
- `skills/cyclaw-command-check-soul/` - verify soul file presence, hash,
  readability, and drift without mutating it.
- `skills/refactor/` - make focused, behavior-preserving architecture or
  measured-performance improvements.
- `skills/cyclaw-optimize/` - select one current, evidence-backed improvement
  and publish a focused draft PR when requested.

## Available Routines

- `routines/first-pass-repo-review.md` - orient in the repo before changing code.
- `routines/bugfix.md` - diagnose and fix a bug with targeted verification.
- `routines/feature.md` - implement a new feature without breaking CyClaw invariants.
- `routines/refactor.md` - improve structure while preserving behavior.
- `routines/test-and-verify.md` - choose and run verification commands.
- `routines/pr-review.md` - review a PR or diff with risk-first findings.
- `routines/security-review.md` - assess security-sensitive changes.

For quick selection, `AGENTS.md` contains a map of skills and routines with
their intended trigger conditions.

## Prompt Templates

- `prompts/issue-triage.md`
- `prompts/implementation-plan.md`
- `prompts/review-diff.md`
- `prompts/release-notes.md`

Copy these into a Codex prompt and fill in the placeholders. Each template references `AGENTS.md` so the agent starts from repo-specific guidance.

## Checklists

- `checklists/pre-commit.md`
- `checklists/pre-pr.md`
- `checklists/regression-risk.md`

Use checklists as lightweight reminders, not as a substitute for reading the relevant code and CI workflows.

## Adding New Routines

1. Put task-specific playbooks in `.codex/routines/`.
2. Keep repo-wide rules in `AGENTS.md` instead of duplicating them.
3. Link to canonical docs rather than copying long sections.
4. Include when to use it, inputs, workflow, verification, and final response expectations.
5. Keep routine names lower-case kebab-case.

Do not add compatibility copies of `.codex/instructions.md` or snapshots of
canonical repo guidance. They drift; link to the live source instead.

## Scratch Work

Generated scratch work, logs, local notes, and temporary outputs should not be committed unless the maintainer explicitly requests them. Use ignored runtime locations or local scratch space for experiments, then summarize results in the final response.
