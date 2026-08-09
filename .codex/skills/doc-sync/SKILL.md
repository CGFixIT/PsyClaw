---
name: doc-sync
description: Reconcile CyClaw documentation with current code, configuration, commands, skills, and workflows. Use after architecture, configuration, skill, routine, prompt, checklist, command, or release-path changes; code is authoritative and this workflow updates only derived guidance.
---

# Sync CyClaw Documentation

Use the maintained checker under `.claude/skills/doc-sync/` as the single
mechanical implementation. This Codex skill defines how to use its evidence
without copying a second parser or treating prose as executable truth.

## Workflow

1. Read the relevant source, target diff, `AGENTS.md`, `CLAUDE.md`, and the
   documentation surface being claimed. Do not begin from a historical drift
   list; re-check current `origin/main`.
2. Run the checker from the repository root:

   ```text
   python .claude/skills/doc-sync/doc_sync.py
   ```

   It validates skill inventory, console entry points, documented config
   values, sanitizer-pattern counts, route coverage, and hook claims. Exit 0
   means no mechanical drift; it does not prove every prose assertion.
3. Perform a bounded manual pass where the change reaches: command docs versus
   implementation, install docs versus manifests/workflows, `AGENTS.md` versus
   `CLAUDE.md`, routines/prompts/checklists versus active Codex behavior, and
   security claims versus the threat model and source.
4. Update the derived document, never code/config/graph behavior merely to
   match stale prose. Treat a desired-but-absent behavior as a decision for the
   user, not a documentation repair.
5. When skills change, update both `AGENTS.md` and `.codex/README.md`; verify
   every Codex skill has `SKILL.md`, `agents/openai.yaml`, and a default prompt
   containing its exact `$skill-name`.
6. Re-run the checker and `git diff --check`. Use the shell self-test below
   when the environment can run it.

## Verification

```text
bash .claude/skills/doc-sync/verify.sh
git diff --check
```

On Windows without Bash, run the Python checker directly and explicitly report
that its mutation self-test was not run. Do not claim a clean checker result
validates newly invented behavior, live integrations, or an untested command.

## Publication boundary

Keep documentation truth changes separate from runtime changes where possible.
Use a focused draft PR, state the source of truth and checks run, and never
push directly to `main`.
