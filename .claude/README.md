# `.claude/` — Project Skills & Workflows

Quick reference for Claude Code assistance patterns in CyClaw.

## Skills

The skills directory holds many more skills than the handful below (operational,
refactor-loop, memory, and agent skills). For the **authoritative, complete list**, see the
**"Available Skills (main branch)"** table in the root [`CLAUDE.md`](../CLAUDE.md) — kept in
sync there so a second list does not drift. A few common entry points:

```bash
/invariant-guard         # Static-assert the six security invariants (stdlib)
/config-guard            # Static-validate config.yaml's relational/value/threat-model contract
/dep-guard               # Static-validate dependency-pin invariants (pyproject + constraints)
/run-cyclaw              # Smoke-test the FastAPI server
/architecture-refactor   # Start architecture refactor loop
/tests-refactor          # Start test coverage loop
/logging-refactor        # Start logging audit loop
/speed-refactor          # Start speed optimization loop
/wrap-up                 # Run end-of-session checklist
/CyClaw-Optimize         # there are many more, verify folder each time
```

The three static guards — `/invariant-guard` (topology & imports), `/config-guard`
(config.yaml numbers & relations), `/dep-guard` (dependency pins) — are the
pre-merge/pre-install checks; each ships a `check_*.py` plus a `verify.sh` that
CI runs automatically. See the authoritative table in [`CLAUDE.md`](../CLAUDE.md) §9.

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

## Skill Caching Policy

Claude Code resolves skills from three scopes: **project** (`<repo>/.claude/skills/`,
version-controlled, shared with every collaborator on this repo), **user**
(`~/.claude/skills/`, personal, follows the operator across every repo), and
**plugin/built-in** (shipped by Claude Code itself or installed marketplaces —
`xlsx`, `pdf`, `pptx`, `docx`, `dataviz`, `canvas-design`, `mcp-builder`,
`skill-creator`, `theme-factory`, `web-artifacts-builder`, `artifact-*`,
`keybindings-help`, `update-config`, `code-review`, `simplify`,
`fewer-permission-prompts`, `loop`, `claude-api`, `init`, `security-review`,
and similar). Personal skills (`cg-coach`, `cg-career-advisor`, `learn`,
`unfiltered-expert-style`, the user-level copy of `fable-protocol`) are
likewise operator-identity-scoped, not project-scoped.

This directory intentionally vendors only the **project** scope — every skill
in `CLAUDE.md` §9's tables already lives under `.claude/skills/` here, one
folder per `name:` in its `SKILL.md` frontmatter. Personal and built-in
skills are **not** copied in, for three concrete reasons:

1. **YAGNI / no current caller.** No CyClaw code path or documented workflow
   invokes `xlsx`, `pdf`, `cg-coach`, etc. — they exist for the operator across
   unrelated repos, not for this project.
2. **Drift risk.** Built-in skills are maintained upstream by Claude Code
   itself; a vendored copy would silently diverge from the version other
   sessions actually run, defeating the point of "official" tooling.
3. **Personal-scope leakage.** Coaching/career/writing-style skills carry the
   operator's identity, not CyClaw's. Checking them into a shared repo would
   expose personal context to every collaborator who clones it — out of scope
   for a project `.claude/` tree.

If a personal or built-in skill genuinely becomes load-bearing for CyClaw
(a documented workflow starts depending on it), add it to `.claude/skills/`
at that point and update `CLAUDE.md` §9 — not before.

## Key Conventions

- Skill folders: `kebab-case`, matching `name:` in SKILL.md frontmatter
- All SKILL.md files use YAML frontmatter: `name:`, `description:`
- Refactor progress is tracked in `/tmp/refactor-CyClaw.md`
- Git identity must be set before commits: `git config user.email noreply@anthropic.com`
