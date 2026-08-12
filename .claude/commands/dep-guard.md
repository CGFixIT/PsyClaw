---
description: >-
  Statically validate CyClaw's dependency-pin invariants across pyproject.toml
  and constraints.txt — pydantic/pydantic-core lock-step, numpy held < 2,
  torch pinned +cpu, uvicorn carrying no extra in the constraints file,
  exact-pin reproducibility, and cross-file version agreement. Use before
  merging any change to pyproject.toml, constraints.txt, or requirements.txt,
  when bumping a dependency, or when asked to "check deps" or "audit pins".
---

Invoke the `dep-guard` skill for the given task. $ARGUMENTS

See `.claude/skills/dep-guard/SKILL.md` for full detail.

## Notes

- Pure stdlib — runs in a fresh clone before pip install.
- `verify-deps` builds on this (adds the requirements.txt cross-check and the
  PyPI currency sweep); run dep-guard first, it is the cheaper gate.
