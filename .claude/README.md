# `.claude/` — Project Skills & Workflows

Quick reference for Claude Code assistance patterns in CyClaw.

## Skills

The skills directory holds many more skills than the handful below (operational,
refactor-loop, memory, and agent skills). For the **authoritative, complete list**, see the
**"Available Skills (main branch)"** table in the root [`CLAUDE.md`](../CLAUDE.md) — kept in
sync there so a second list does not drift. A few common entry points:

```bash
/run-cyclaw              # Smoke-test the FastAPI server
/architecture-refactor   # Start architecture refactor loop
/tests-refactor          # Start test coverage loop
/logging-refactor        # Start logging audit loop
/speed-refactor          # Start speed optimization loop
/wrap-up                 # Run end-of-session checklist
/CyClaw-Optimize         # there are many more, verify folder each time
```

## Refactor Loop Pattern

All `*-refactor` skills follow the same seven-step cycle:

1. **Measure** — baseline the current state (tests, latency, log coverage)
2. **Assess** — identify the highest-leverage gap
3. **Execute** — make one focused change
4. **Test** — verify correctness via smoke test or pytest
5. **Commit** — commit with a clear message
6. **Track** — record progress in `/tmp/refactor-CyClaw.md`
7. **Loop** — repeat until all stopping criteria are met

## Folder Structure

```
.claude/
├── README.md              ← this file
├── settings.json          ← project permissions and hooks
├── skills/                ← project-specific skills (see CLAUDE.md for the full list)
│   ├── run-cyclaw/        ← SKILL.md + smoke.sh
│   ├── architecture-refactor/
│   ├── tests-refactor/
│   ├── …                  ← many more (memory, agent, sandbox, optimize, …)
│   └── wrap-up/
├── patterns/              ← reusable behavioral patterns (01–09)
├── utility-prompts/       ← coordinator / session-title / tool-summary / next-action
├── commands/              ← reference command docs
├── hooks/                 ← SessionStart / PreCompact / SessionEnd scripts
└── rules/                 ← project-specific rules (scoped by paths:)
```

## Key Conventions

- Skill folders: `kebab-case`, matching `name:` in SKILL.md frontmatter
- All SKILL.md files use YAML frontmatter: `name:`, `description:`
- Refactor progress is tracked in `/tmp/refactor-CyClaw.md`
- Git identity must be set before commits: `git config user.email noreply@anthropic.com`
