---
description: >
  Clone origin/main to a clean local sandbox, install all dependencies, then
  run a comprehensive audit — LangGraph routing, the Ollama local-LLM path
  at three realism tiers, triple-gated Grok/Claude fallback, API key
  redaction, Phase 2 guardrails, due-diligence invariants, the full terminal
  console REST surface, and a Python-3.12 runtime gate. Produces a dated
  report and opens a draft PR.
---

Invoke the `CyClaw-Sandbox` skill and run the full sandbox audit: clone,
install, mock Ollama, audit every subsystem, report. $ARGUMENTS

See `.claude/skills/CyClaw-Sandbox/SKILL.md` for the full 21-phase procedure
(realism ladder, config validation, gate/graph standalone checks, the
5-query/4-path swarm test, triple-gate + key redaction, due-diligence
invariants, Phase 2 guardrails, the full `/soul/*` + `/ops/*` REST surface,
terminal.html contract, the 3.12 runtime gate, 20 security invariants, and
the report/PR machinery).

For a fast check against your current checkout with no clone/report/PR —
use the skill's **Quick Mode** section instead (absorbs the former separate
`/run-cyclaw` and `/run` commands):

```bash
bash .claude/skills/CyClaw-Sandbox/smoke.sh
```

## Notes

- A **destructive-safe, clone-first** audit — never modifies the real
  `data/personality/soul.md`, fully reproducible.
- Run the highest Ollama realism tier the environment supports (Tier 0
  stubs / Tier 1 mock-over-HTTP / Tier 2 real daemon) and record which tier
  was used — never claim Tier 2 realism from a Tier 0/1 run.
- Draft PR only; a human decides when to merge.
- `status: degraded` without live Ollama is expected and normal.
