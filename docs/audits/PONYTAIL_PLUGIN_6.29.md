# Ponytail Plugin Integration

## What is Ponytail

Ponytail is a Claude Code plugin that enforces **lazy senior dev mode** — a set of YAGNI, stdlib-first, and minimal-abstraction principles that push every code change toward the simplest solution that actually works.

Version: **4.8.4**

## Principles Enforced

| Principle | Rule |
|---|---|
| **YAGNI** | Do not add features, abstractions, or flexibility the current task doesn't need |
| **stdlib-first** | Prefer Python stdlib over third-party packages when stdlib is adequate |
| **Minimal abstraction** | Three similar lines beats a premature helper; only abstract at four or more call sites |
| **No dead code** | No commented-out blocks, unused imports, or placeholder stubs |
| **No speculative generality** | Do not design for hypothetical future requirements |
| **Correctness over cleverness** | Boring and readable beats elegant |
| **No half-measures** | Implement it properly or not at all |

When a constraint is violated, the violation must be named and justified inline.

## Skills

The plugin registers one skill, invocable as `/ponytail`:

```
/ponytail
```

Activates lazy-senior-dev mode for the current task. Loads the seven YAGNI/stdlib-first/minimal-abstraction rules plus a pre-commit checklist. Use when asked to "keep it simple", "no over-engineering", or "ponytail mode".

Skill source: `.claude/skills/ponytail/SKILL.md`

## Files

| File | Purpose |
|---|---|
| `.claude/ponytail-marketplace.json` | Marketplace definition — plugin system prompt + skill registration |
| `.claude/skills/ponytail/SKILL.md` | Skill loaded by `/ponytail` — seven rules + violation protocol + checklist |
| `.claude/settings.json` | Registers the marketplace and enables `ponytail@ponytail` |

## Configuration

`settings.json` registers the marketplace from the repo-local file:

```json
{
  "extraKnownMarketplaces": {
    "ponytail": {
      "source": {
        "source": "file",
        "path": ".claude/ponytail-marketplace.json"
      }
    }
  },
  "enabledPlugins": {
    "ponytail@ponytail": true
  }
}
```

## Compatibility with CyClaw Behavioral Rules

Ponytail's principles align with and reinforce CyClaw's existing coding standards:

- **No features beyond scope** matches CyClaw's "minimal diffs that solve the root problem"
- **stdlib-first** complements the stack's preference for well-audited dependencies
- **No half-finished implementations** matches CyClaw's "never add features [...] beyond what the task requires"
- **Correctness over cleverness** matches CyClaw's "readable solution first"

## Setup (New Environment)

The marketplace file is tracked in the repo, so no manual upload is needed:

```bash
# Verify the file exists
ls .claude/ponytail-marketplace.json

# Verify settings.json references it correctly
grep -A4 "ponytail" .claude/settings.json
```

Restart your Claude Code session after cloning for the plugin to activate.

## Prior Issue

The original PR #349 registered the marketplace from an ephemeral upload path
(`/root/.claude/uploads/.../530433b8-marketplace.json`) that does not survive
session restarts. This was fixed by committing the marketplace file to the repo
and updating the path in `settings.json`.
