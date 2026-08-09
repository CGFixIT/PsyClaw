# Refactor Routine

## When To Use

Use this for structure, readability, duplication, or maintainability improvements where behavior should stay the same.

## Inputs To Establish

- Target files/subsystem.
- Desired outcome: simplify, isolate, type, speed, logging, tests, or docs.
- Risk tolerance and verification budget.

Infer these from the request and repository first; ask only when a material
irreversible choice remains.

## Workflow

1. Read `AGENTS.md`, `$fable-protocol`, and existing tests around the area.
2. Establish a baseline with the smallest relevant test/smoke command.
3. Keep the refactor behavior-preserving and narrow.
4. Avoid mixing dependency, CI, formatting, and behavior changes.
5. Preserve graph security invariants and optional-layer isolation.
6. Run the same baseline checks after the edit.
7. If behavior changes are discovered, stop and reclassify as a feature/bugfix.
8. Keep mechanical rewrites separate from semantic cleanup when either diff
   would be hard to review.

## Verification Checklist

- Baseline understood or explicitly unavailable.
- Public behavior unchanged.
- Tests before/after are comparable.
- No unrelated reformatting.
- No new dependencies unless approved.

## Expected Final Response

- Refactor goal.
- Files changed.
- Behavior-preservation evidence.
- Checks run and any unverified areas.
- Any intentional non-goals left for a separate PR.
