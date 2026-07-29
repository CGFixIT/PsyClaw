# Governed Skills Registry — Design & Governance

> Implemented in `agentic/registry.py`. This doc explains *why* it is shaped like
> the soul layer and what guarantees it inherits.

## Motivation
Hermes/ClawHub show the value of a *named, versioned, reusable* skills catalog.
But their autonomy (self-writing skills) and their public marketplace (remote
install) are both off-limits for CyClaw. The transferable core is:

> persist a vetted procedure as file-as-truth, versioned, searchable.

CyClaw already has exactly this shape — for the **soul** — with a human gate. The
registry generalizes the soul governance pattern to a second surface.

## The pattern it reuses (1:1 with `utils/personality.py`)
| Soul (`personality.py`) | Registry (`agentic/registry.py`) |
|---|---|
| `propose_evolution(new_soul, reason)` — never writes; advisory flags | `propose_skill(spec, reason)` — never writes; advisory flags |
| `apply_evolution(..., scan=True)` — enforce scan at write boundary | `apply_skill(..., scan=True)` — enforce scan at write boundary |
| scanner = `banned_patterns ∪ OWASP` | **same union** (imports `OWASP_INJECTION_PATTERNS`) |
| atomic `tmp`+`os.replace` | atomic `tmp`+`os.replace` |
| sha256 version rows in `cyclaw_soul.db` | sha256 version + history in `skills_registry.json` |
| explicit human `reason` required | explicit non-empty `reason` required |

## Governance guarantees
1. **No autonomous modification.** There is no code path — graph node, request
   handler, or background loop — that calls `apply_skill`. It is reachable only via
   `python -m agentic.cli apply-skill --confirm`, operated by a human. Both
   `propose-skill` and `apply-skill` are also gated on the `agentic.enabled` master
   switch: while the layer is disabled they no-op (exit 0) and never construct a
   registry, so a write can never occur — even through the API-key-gated
   `POST /ops/agentic` console — while the operator believes the layer is off.
2. **Injection gate at the write boundary.** `apply_skill(scan=True)` scans the
   canonical `name\ndescription\nbody` against the **same** pattern set the query
   path uses; a match raises `PromptInjectionError` *before any write* and audits
   `agentic_skill_injection_blocked`. This closes the skill-poisoning vector (a
   skill body that says "ignore previous instructions" can never be persisted).
3. **Human reason required.** Empty/whitespace `reason` → `SkillRegistryError`.
4. **Atomic + versioned.** Writes go through `tmp` + `os.replace`; every apply
   bumps `version` and appends a sha256 history entry; `agentic_skill_applied`
   is audited.
5. **Local-only.** `registry_path` is validated to resolve **under the repo's
   `data/` tree** (`agentic/config.py`). No remote fetch, no third-party install.

## Data shape (`data/agentic/skills_registry.json`)
```json
{
  "version": 2,
  "updated": "2026-06-21T…Z",
  "skills": {
    "demo": {"name": "demo", "description": "...", "body": "...",
             "sha256": "…", "reason": "why", "updated": "…Z"}
  },
  "history": [{"version": 1, "name": "demo", "sha256": "…", "reason": "…", "timestamp": "…Z"}]
}
```

## What it deliberately does NOT do (v0.1)
- It does **not** execute skills, expose them to the graph, or load them into any
  LLM prompt. It is a *governed store*; runtime consumption is future work and
  would itself pass through review (a skill body is untrusted until used).
- It does **not** fetch or publish to any remote registry.

## Tests
`tests/test_agentic_registry.py` proves: propose never writes; apply writes &
versions; injection blocked (nothing persisted); reason required; bad names
rejected; reload sees persisted state.

`tests/test_agentic_registry_contract.py` pins the committed artifact itself:
the documented top-level shape, `version == len(history)` with rows numbered
1..N, per-skill sha256 recomputed from the canonical `name\ndescription\nbody`
text, and history rows that reference applied skills — so a hand-edit of the
store (a governance bypass) fails CI instead of passing unnoticed.
