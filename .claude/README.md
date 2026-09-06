# `.claude/` — Project Skills & Workflows

Quick reference for Claude Code assistance patterns in CyClaw.

## Skills

The skills directory holds many more skills than the handful below (operational,
refactor-loop, memory, and agent skills). For the **authoritative, complete list**, see the
**§9 "Skills"** section of the root [`CLAUDE.md`](../CLAUDE.md) — kept in
sync there so a second list does not drift. A few common entry points:

These are Claude Code slash commands typed into the session, not shell
commands — the fence below is `text` on purpose, because pasting these into a
terminal only produces "No such file or directory".

```text
/invariant-guard         # Static-assert the six security invariants (stdlib)
/config-guard            # Static-validate config.yaml's relational/value/threat-model contract
/dep-guard               # Static-validate dependency-pin invariants (pyproject + constraints)
/run                     # Smoke-test the FastAPI server (smoke suite, sections A-G)
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
├── hooks/                 ← session-start-sync-check.sh (second SessionStart hook
│                            since 2026-09-04) and fable-protocol-loader.sh (third,
│                            since 2026-09-06; model-gated). The other live hooks
│                            (SessionStart persona loader, PreCompact, SessionEnd)
│                            are inline commands in settings.json pointing into
│                            .claude/skills/*
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
2026-08-11: added the two missing wrappers (`config-guard`, `dep-guard`).
2026-09-04: added `fable-5.1-cc`, the last skill that shipped without one.
2026-09-06: `fable-5.1-cc` was consolidated into `fable-protocol` (its content
is now that skill's §8 onward) and deleted, wrapper and all — see
`fable-protocol/SKILL.md` §11. The wrapper set still covers every skill —
verify with
`comm -23 <(ls .claude/skills | sort) <(ls .claude/commands | sed 's/\.md$//' | sort)`,
which must print nothing.

## Environment Doctor — settings.json audit (2026-08-11)

A doctor-style audit of `.claude/settings.json` was run against the live tree
(don't re-create what passes; fix only what's broken). Verdict and findings:

**settings.json: the dangling hook is gone (resolved 2026-09-04).** Valid JSON
against the declared schema, and every hook path now resolves. The history is
worth keeping: from some point before 2026-08-22 until 2026-09-04 the registered
`UserPromptSubmit` hook pointed at
`.claude/skills/fable-protocol/context_gate.py`, a file deleted on `main`. Because
the command ended in `2>/dev/null || true` it silently no-opped on every prompt
instead of erroring — which is precisely why it went unnoticed for weeks. The
operator's call was to unwire it rather than write a replacement: the skill stays
reachable as `/fable-protocol` and through its `description` trigger, and injecting
an 18KB skill into every prompt is cost the skill's own §7 warns about. **Lesson
encoded:** the sync-check hook added the same day carries no `|| true`, because the
script already always exits 0 and that tail is the thing that hid the last failure.
The five registered hook *entries* now resolve to four distinct scripts:
`memory-orchestrator/orchestrate.py` is referenced twice (`PreCompact` and
`SessionEnd`). Otherwise:
no personal data (no usernames, absolute machine paths, or emails — keep it
that way, this file is shared with every collaborator); hooks anchor to
repo-relative paths so they survive any checkout location. Observation, not
changed: the `PreCompact`/`SessionEnd` memory hooks have no `|| true` guard
while the `SessionStart` persona loader does — if `python3` is ever absent on
an operator machine those two will surface hook errors; left as-is because
hook edits are High tier (`CLAUDE.md` §7) and there is no recorded failure.
The `SessionStart` sync-check added 2026-09-04 likewise has no `|| true` — that
is deliberate, not an oversight (see the dangling-hook note above).

**`fable-protocol-loader.sh` (third `SessionStart` hook, added 2026-09-06).**
This is the replacement the 2026-09-04 note said was not being written, on a
different trigger and with a gate. It injects `fable-protocol/SKILL.md` as
`additionalContext` once per SessionStart (startup/resume/clear/compact), the
same moment and mechanism as the persona loader, NOT per prompt — so the
per-prompt cost that got the old `UserPromptSubmit` hook unwired does not
return. It skips when the session model id contains `fable` or `mythos`: the
protocol exists so a smaller model applies what Fable applies by default, and
Fable gets it on demand via `/fable-protocol`. Why SessionStart and why a gate
that can miss: verified against Claude Code 2.1.261's hook schema, SessionStart
is the only event whose stdin JSON carries `model` (optional), and no event
fires on a mid-session `/model` switch — so a switch to Sonnet/Opus after start
is not re-gated; `CLAUDE.md` §10 tells the operator to run `/fable-protocol` by
hand in that case. Absent/unknown model strings inject (fail-open on a cheap
control). The script exits 0 unconditionally and keeps stdout JSON-only, with
diagnostics on stderr; tested 2026-09-06 with sonnet/opus/fable/mythos/absent/
malformed stdin.

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
  `kebab-case`; two ship mixed-case by convention (`CyClaw-Optimize`,
  `CyClaw-Sandbox`), and `CyClaw-Sandbox/`'s frontmatter
  declares `name: cyclaw-swarm-verification`. (`OTel-Hardening` was the
  third until issue #1135 renamed it `otel-hardening` to satisfy the Agent
  Skills spec's lowercase name-matches-directory rule.)
- All SKILL.md files use YAML frontmatter: `name:`, `description:`
- Refactor progress is tracked in `/tmp/refactor-CyClaw.md`
- Git identity must be set before commits (driver-agnostic defaults from
  `utils/agent_identity.py`; see `CLAUDE.md` §10):
  `git config user.email cyclaw-agent@users.noreply.github.com` and
  `git config user.name "CyClaw Agent"`
