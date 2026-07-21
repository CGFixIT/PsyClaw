---
description: Detect and reconcile drift between CyClaw's code/config (the source of truth) and its documentation — CLAUDE.md, AGENTS.md, README, config comments, command docs, and skill tables.
---

Detect and reconcile doc drift against the current code/config state. $ARGUMENTS

**Persona:** You are the technical editor who keeps CyClaw's docs honest. The
rule is absolute: **code is the source of truth; docs are derived.** When a doc
and the code disagree, the doc is wrong — you fix the doc. You NEVER change code
behavior to match a stale doc; if a doc describes behavior the code should have
but doesn't, that is a code decision for the user, not a doc edit. Drift is not
cosmetic here: this repo found a command doc (`check-soul.md`) that falsely
claimed the server won't boot without `soul.md` (it self-heals) and pointed at a
`soul_hash` constant that doesn't exist — a weaker agent following it would
report a healthy server as broken.

**Why it matters:** the repo carries two agent manuals (`CLAUDE.md`,
`AGENTS.md`), a README, config comments, command docs, and a skills table — six
places one fact can rot. Nothing checks them against the code.

## Step 1 — Deterministic checker (no heavy deps)

```bash
python3 .claude/skills/doc-sync/doc_sync.py
```

Needs only PyYAML. It extracts facts from code/config and flags docs that cite
them wrongly. Exit `0` no drift · `2` drift found · `3` env error.

| ID | Fact (source of truth) | Checks |
|---|---|---|
| D1 | `.claude/skills/*/SKILL.md` | Every skill on disk appears in the CLAUDE.md skills table |
| D2 | `pyproject [project.scripts]` | Every console entry point is named in CLAUDE.md |
| D3 | `config.yaml` | `port`/`min_score`/`rrf_k`/`graph_timeout_sec`/`soul_max_chars` cited in CLAUDE.md match the real values |
| D4 | `banned_patterns` length | The "`<n>` patterns" claim is consistent across CLAUDE.md, config.yaml, guardrails, fsconnect |
| D5 | `gate.py @app` routes | Every API route is named in CLAUDE.md |
| D6 | `.claude/settings.json` hooks | A "stop hook" claim is either backed by a wired Stop hook or accurately attributed to the session runtime |

## Step 2 — Reconcile each mechanical drift item

For every `DRIFT` line, edit the DERIVED doc to match the source of truth. One
commit per doc family (all CLAUDE.md fixes together, all README fixes together)
keeps the history reviewable. Examples of the fix direction:
- D1 → add the missing skill row to the CLAUDE.md skills table.
- D3 → replace the stale number in CLAUDE.md with the config value (never edit
  config to match the doc).
- D5 → add the missing route to the CLAUDE.md request-flow/route section.
- D6 → reword the doc to say the enforcement is applied by the session runtime
  (not wired in repo `settings.json`), OR wire the hook if that is the intent —
  the latter is a settings change, so confirm with the user first.

## Step 3 — Manual pass for prose claims the checker can't parse

The checker covers structured facts. Read these source→doc pairs by hand and
correct any claim the code contradicts:

- **Command docs (`.claude/commands/*.md`) vs actual behavior** — the highest-
  risk drift class (the `check-soul.md` example). For each command doc, confirm
  every behavioral claim ("the server will…", "look for constant X") against the
  code path it describes.
- **Boot/failure semantics** in CLAUDE.md "Environment Quirks" — verify against
  `gate.py`/`utils/personality.py`: soul.md self-heals; a missing index is a
  503 fail-soft, not a crash; `require_env` is decorative.
- **AGENTS.md ↔ CLAUDE.md** — the two manuals must not contradict each other on
  invariants, install steps, or the current project mode.
- **Two memory systems / two session-note locations** — `.claude/memory/` is
  legacy; `docs/memories/` is live. Docs should point at the live one.
- **THREAT_MODEL.md control table** vs the code that implements each control
  (sanitizer at `/query`, TrustedHostMiddleware, triple-gate) — controls should
  not be described that the code no longer has, or vice versa.

## Step 4 — Report

Produce a table: `drift item → source of truth → doc fixed (or "flagged to
user" for behavior questions)`. Anything that would require a CODE change to
resolve goes to the user as a question, never as a silent doc edit that hides
the mismatch.

## Verify

```bash
bash .claude/skills/doc-sync/verify.sh
```

Confirms the checker runs on the live tree (drift there is expected and does not
fail the check) and passes a detection self-test: it builds a stub CLAUDE.md
missing real skills/routes and asserts the checker flags drift (exit 2). A
drift-checker that can't detect planted drift is useless; the self-test keeps it
honest. Skips cleanly if PyYAML is absent.

## Known drift baseline (this session, 2026-07)

Seed list so the first run has context — reconcile these:

1. **`ponytail` skill** on disk, absent from the CLAUDE.md skills table (D1).
2. **`/audit/summary`, the four `/ops/*`, and `/soul/*` sub-routes** exist in
   `gate.py` but were missing from the CLAUDE.md route map (D5).
3. **"stop hook" claims** in CLAUDE.md/PROJECT_RULES reference a hook not wired
   in `.claude/settings.json`; the committer-email + force-push enforcement is
   applied by the session runtime — say so, or wire it (D6).
4. **`check-soul.md`** (fixed this session) claimed soul.md is required to boot
   and referenced a nonexistent `soul_hash` constant.
5. **`session-start-sync-check.sh`** exists but is not wired in `settings.json`;
   docs implying it runs are stale.
6. **`docs/SESSION_NOTES.md`** is an empty scaffold while real notes live under
   `.claude/session-notes/` — align the pointer.

## Guardrails

- **Code wins, always.** Fix docs, not behavior. A doc that describes desired-
  but-absent behavior is a user decision — flag it, don't "make it true" by
  editing config or code under the guise of a doc sync.
- **Never edit `config.yaml` values, graph edges, or code to silence a drift
  line.** D3/D4/D5 drift is fixed in the doc, not the source.
- Wiring a hook (to resolve D6 by making the claim true) is a `settings.json`
  change — confirm with the user first.

## Gotchas

- **The checker is fact-scoped.** It cannot read prose intent — Step 3's manual
  pass is where command-doc and semantics drift is caught.
- **D3 only flags a WRONG cited number, not an undocumented one.** Not every
  tunable must be in CLAUDE.md; the check fires only when the doc discusses a key
  but shows a value that no longer matches config.
- **D4 ignores small numbers** (<6) to avoid matching unrelated "3 patterns"
  phrasing; it targets the documented set size.
- **Run it after, not before, a rename.** If you rename a skill or route, the
  checker will (correctly) flag the old name in docs until you update them.

## Notes

- Never change code behavior just to make a stale doc "true."
- Run after any architecture/config/skill change, before a release, or at end-of-session per `CLAUDE.md` §10.
- Every `##` doc section must stay self-contained — the corpus is chunked and searched section-by-section.
