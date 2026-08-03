---
name: OTel-Hardening
description: >-
  Re-verify that CyClaw's telemetry-kill switches still block dependency
  phone-home paths, using the authoritative `.claude/skills/OTel-Hardening`
  skill and its checker scripts as the source of truth.
---

# OTel-Hardening

Use this Codex wrapper skill when asked to audit, harden, or re-verify CyClaw
telemetry blocking.

Before acting:

- Read `.claude/skills/OTel-Hardening/SKILL.md`.
- Use `.claude/skills/OTel-Hardening/check_otel.py` for the deterministic
  checker.
- Use `.claude/skills/OTel-Hardening/verify.sh` when the repo asks for the
  verifier path.

Preserve CyClaw's telemetry-kill guarantees:

- keep telemetry-kill environment wiring ahead of heavy imports
- do not weaken or remove existing kill switches without explicit approval
- treat vendor drift as a verification problem first, not a rewrite prompt
