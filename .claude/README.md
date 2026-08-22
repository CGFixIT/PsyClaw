# `.claude/` — Project Skills & Workflows

Quick reference for Claude Code assistance patterns in CyClaw.

## Skills

The skills directory holds many more skills than the handful below (operational,
refactor-loop, memory, and agent skills). For the **authoritative, complete list**, see the
**"Available Skills (main branch)"** table in the root [`CLAUDE.md`](../CLAUDE.md) — kept in
sync there so a second list does not drift. A few common entry points:

These are Claude Code slash commands typed into the session, not shell
commands — the fence below is `text` on purpose, because pasting these into a
terminal only produces "No such file or directory".

```text
/invariant-guard         # Static-assert the six security invariants (stdlib)
/config-guard            # Static-validate config.yaml's relational/value/threat-model contract
/dep-guard               # Static-validate dependency-pin invariants (pyproject + constraints)
/run                     # Smoke-test the FastAPI server (29 smoke checks)
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
├── settings.json          ← project permissions, hooks, and plugin marketplace
├── ponytail-marketplace.json ← local plugin marketplace (see settings.json extraKnownMarketplaces)
├── skills/                ← project-specific skills (see CLAUDE.md for the full list)
│   ├── invariant-guard/   ← SKILL.md + check_invariants.py + verify.sh
│   ├── architecture-refactor/
│   ├── tests-refactor/
│   ├── …                  ← many more (memory, agent, sandbox, optimize, …)
│   └── wrap-up/
├── patterns/              ← reusable behavioral patterns (01–09)
├── utility-prompts/       ← coordinator / session-title / tool-summary / next-action
├── commands/              ← reference command docs
├── tools/                 ← tool-usage reference docs
├── hooks/                 ← session-start-sync-check.sh only; on disk but NOT
│                            registered in settings.json. The live SessionStart /
│                            PreCompact / SessionEnd / UserPromptSubmit hooks are
│                            inline commands in settings.json pointing into .claude/skills/*
├── memory/                ← legacy memory location (live memory: docs/memories/)
└── rules/                 ← project-specific rules (PROJECT_RULES.md; plain
                              Markdown, no frontmatter, applies repo-wide)
```

## Skill Caching Policy

Claude Code resolves skills from three scopes: **project** (`<repo>/.claude/skills/`,
version-controlled, shared with every collaborator on this repo), **user**
(`~/.claude/skills/`, personal, follows the operator across every repo), and
**plugin/built-in** (document/artifact tooling, review helpers, and config
utilities shipped by Claude Code itself or by installed marketplaces).

This directory intentionally vendors only the **project** scope — every skill
in `CLAUDE.md` §9's tables already lives under `.claude/skills/` here, one
folder per skill, named for the `name:` in its `SKILL.md` frontmatter — with
one exception: `CyClaw-Sandbox/` declares `name: cyclaw-swarm-verification`. User-scope and built-in
skills are **not** copied in, for three concrete reasons:

1. **YAGNI / no current caller.** No CyClaw code path or documented workflow
   invokes them — they serve the operator across unrelated repos, not this
   project.
2. **Drift risk.** Built-in skills are maintained upstream by Claude Code
   itself; a vendored copy would silently diverge from the version other
   sessions actually run, defeating the point of "official" tooling.
3. **Scope leakage.** User-scope skills are tied to an operator's own identity
   and working style, not to CyClaw. Checking them into a shared repo would
   expose that context to everyone who clones it — out of scope for a project
   `.claude/` tree.

If a personal or built-in skill genuinely becomes load-bearing for CyClaw
(a documented workflow starts depending on it), add it to `.claude/skills/`
at that point and update `CLAUDE.md` §9 — not before.

### Slash-command wrappers (`.claude/commands/`)

Every project skill in `.claude/skills/` also gets a thin
`.claude/commands/<name>.md` wrapper (frontmatter `description:` + an
"Invoke the `<name>` skill" body) so the skill is reliably reachable as
`/<name>` in any Claude Code client, including ones that surface commands
and skills separately. Wrappers are deliberately thin — the `SKILL.md` stays
the single source of truth and the wrapper only points at it, so the two
cannot drift apart in substance. Five commands are standalone by design and
have no skill folder (`/audit`, `/check-soul`, `/conversation-summary`,
`/run`, `/status`) — they are short inline procedures, not skills.
2026-08-11: added the two missing wrappers (`config-guard`, `dep-guard`) so
the wrapper set now covers all skills; nothing else was missing.

## Environment Doctor — settings.json audit (2026-08-11)

A doctor-style audit of `.claude/settings.json` was run against the live tree
(don't re-create what passes; fix only what's broken). Verdict and findings:

**settings.json: one dangling hook reference as of 2026-08-22.** Valid JSON
against the declared schema, and every `check_*.py` / `verify.sh` checker path
resolves. But the registered `UserPromptSubmit` hook points at
`.claude/skills/fable-protocol/context_gate.py`, which no longer exists — that
file was deleted on `main` and the skill directory now holds only `SKILL.md`.
The hook command ends in `2>/dev/null || true`, so it silently no-ops instead
of erroring, which is exactly why the drift went unnoticed. Fixing it means
editing `settings.json` (unwiring the hook or restoring the script), which is a
High-tier change under `CLAUDE.md` §7 — confirm intent with the operator rather
than doing it as part of a doc pass. Note also that the four registered hook
*entries* resolve to three distinct scripts:
`memory-orchestrator/orchestrate.py` is referenced twice (`PreCompact` and
`SessionEnd`). Otherwise:
no personal data (no usernames, absolute machine paths, or emails — keep it
that way, this file is shared with every collaborator); hooks anchor to
repo-relative paths so they survive any checkout location. Observation, not
changed: the `PreCompact`/`SessionEnd` memory hooks have no `|| true` guard
while `SessionStart`/`UserPromptSubmit` do — if `python3` is ever absent on
an operator machine those two will surface hook errors; left as-is because
hook edits are High tier (`CLAUDE.md` §7) and there is no recorded failure.

**Remote-environment env-var misconfiguration (root cause of the stray
`C:\Users\...` directory).** The Claude Code remote execution environment for
this repo injects Windows-local values into Linux containers:

- `CLAUDE_CONFIG_DIR=C:\Users\<user>\.claude\` — on Linux this is a
  *relative* path, so Claude Code materializes a literal `C:\Users\...`
  directory at the repo root and uses it as live config storage. Observed
  breakage in-session: session-start hook output, task tracking (lock file
  `ENOENT`), and skill sync all landed in the stray directory.
- `CLAUDE_CODE_DEBUG_LOGS_DIR=C:\Users\<you>\.claude\debug` — an
  unsubstituted template; invalid on every OS (`<`/`>` are illegal in
  Windows paths too).

Repo-side mitigation (this commit): a root `.gitignore` guard (`/C:*`) so the
stray directory — which contains live session state — can never be staged by
a broad `git add`. Operator-side fix (cannot be done from the repo): edit the
environment's variables at claude.ai → Code → environment settings and
remove both entries. Neither belongs in a shared remote environment —
`C:\Users\<user>\.claude\` is already Claude Code's *default* config
location on Windows, so the variable adds nothing there either; per-machine
overrides belong in that machine's own shell profile. `settings.json` cannot
fix this: `CLAUDE_CONFIG_DIR` is read at CLI startup before project settings
load, and pinning it in the repo would break every other machine.

**Verification follow-up (2026-08-11, fresh remote session).** The operator-side
fix above landed half-way, verified live from a new cloud container for this
repo:

- `CLAUDE_CONFIG_DIR` — **removed**. Session state (hook output, task
  tracking, skill sync) lands in the default config location again, no stray
  `C:\...` directory materializes anywhere on the container filesystem, and
  `git status` stays clean. The `/C:*` guard stays as defense-in-depth.
- `CLAUDE_CODE_DEBUG_LOGS_DIR` — **still injected**. The variable was edited
  (the `<you>` template now carries a real Windows username) instead of
  removed, so every Linux container still receives a Windows-local path — and
  a personal username — in its environment. No stray directory is currently
  produced by this one, but the removal instruction above still applies:
  claude.ai → Code → environment settings, delete the entry.

## Key Conventions

- Skill folders match the `name:` in SKILL.md frontmatter. Most are
  `kebab-case`; three ship mixed-case by convention (`CyClaw-Optimize`,
  `CyClaw-Sandbox`, `OTel-Hardening`), and `CyClaw-Sandbox/`'s frontmatter
  declares `name: cyclaw-swarm-verification`
- All SKILL.md files use YAML frontmatter: `name:`, `description:`
- Refactor progress is tracked in `/tmp/refactor-CyClaw.md`
- Git identity must be set before commits (driver-agnostic defaults from
  `utils/agent_identity.py`; see `CLAUDE.md` §10):
  `git config user.email cyclaw-agent@users.noreply.github.com` and
  `git config user.name "CyClaw Agent"`
