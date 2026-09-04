---
description: >-
  Verify CyClaw's four install surfaces (pyproject.toml+uv, requirements.txt+pip,
  the Docker surface — Dockerfile + docker-compose.yml + .dockerignore +
  publish-ghcr.yml — and environment.yml) actually agree AND are current against
  upstream PyPI. Delegates static pin agreement to dep-guard, adds the
  requirements.txt cross-check dep-guard skips, the install-surface scope
  contract (constraints.txt is a version ceiling, not an install list), the
  non-manifest drift checks E1–E6, a real dry-run of each surface's install
  command, and a PyPI currency + CVE sweep. Never auto-bumps a runtime pin.
---

Verify dependency pins are consistent across every install surface and current against upstream. $ARGUMENTS

See `.claude/skills/verify-deps/SKILL.md` for full detail.

## Notes

- Never bumps a runtime dependency's pin without explicit approval — that's
  Medium-High risk tier (CLAUDE.md §7); this reports findings for review.
- Run `.claude/skills/dep-guard/check_deps.py` first (Step 1) — this skill
  builds on dep-guard, it doesn't replace it.
