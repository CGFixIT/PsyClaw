---
name: otel-hardening
description: Re-verify that CyClaw telemetry-kill controls block dependency phone-home paths. Use before changing telemetry, instrumentation, dependency imports, startup ordering, or network egress policy; use the maintained checker instead of duplicating telemetry logic.
---

# otel-hardening

Use this for a focused telemetry-boundary review. The maintained checker under
`.claude/skills/otel-hardening/` is the single static implementation; this
skill provides the Codex workflow around it.

## Workflow

1. Read the target diff, `utils/telemetry_kill.py`, affected imports, startup
   path, `docs/THREAT_MODEL.md`, and the maintained checker guide.
2. Run the deterministic baseline:

   ```text
   python .claude/skills/otel-hardening/check_otel.py
   ```

3. Trace all new or changed SDK imports and environment reads. Telemetry
   controls must occur before the import that can initialize the SDK.
4. Validate the affected runtime path without credentials or external egress
   when the environment supports it. Do not turn on telemetry to test a kill
   switch.
5. Run `bash .claude/skills/otel-hardening/verify.sh` when Bash is available;
   otherwise report the mutation self-test as skipped rather than inferred.

Preserve CyClaw's telemetry-kill guarantees:

- keep telemetry-kill environment wiring ahead of heavy imports
- do not weaken or remove existing kill switches without explicit approval
- treat vendor drift as a verification problem first, not a rewrite prompt

Keep any remediation narrow, run the invariant guard for core imports, and use
a focused draft PR. Never expose telemetry credentials, endpoints, or user data
in tests or reports.
