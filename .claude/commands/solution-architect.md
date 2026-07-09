---
description: Solution architect agent that studies a codebase in depth and produces a concrete, well-reasoned implementation plan before any code is written.
---

Study the codebase and produce a concrete implementation plan for the given task, before writing code. $ARGUMENTS

## Steps

1. Explore the existing codebase first: read `README`, `CLAUDE.md`, `CONTRIBUTING`, and any project-specific convention docs to ground the plan in established patterns.
2. Identify every file, module, and dependency the proposed change would touch, and map how they connect.
3. Present at least two distinct implementation options, each with explicit tradeoffs: complexity, breakage risk, performance, maintainability burden, and alignment with existing conventions.
4. Recommend one option with reasoning, but stop short of writing the code — this skill plans, it does not implement.

Follow `.claude/skills/solution-architect/SKILL.md` for the full approach.

## Notes

- Deliverable is a plan, not a diff — hand the plan to implementation only after the user picks a direction (or you state the smallest-reversible assumption and flag it, per `CLAUDE.md` §7).
- Any plan touching the six invariants must call that out explicitly.
