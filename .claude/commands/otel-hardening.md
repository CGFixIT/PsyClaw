---
description: >-
  Re-verify that CyClaw's telemetry-kill contract still holds end to end — the
  canonical env maps in utils/telemetry_kill.py (telemetry vs. update-check,
  visibly separate), the scrubbed credential/declarative-config names, the real
  ONNX Runtime suppression (ORT_DISABLE_TELEMETRY before import + the
  disable_telemetry_events() API at the load seams), and the process-boundary
  delivery surfaces (Docker ENV/compose, macOS/PowerShell launchers, generated
  launchd plists / Windows tasks / cron lines, agentic verifier children, gh
  children) — via a static checker with an INDEPENDENT name→value oracle and a
  category-1-to-5 egress classification of every dependency, provider,
  executable, connector, scheduled job, and launcher. Then a live vendor-doc
  sweep for drift since each control's last review date. Use when asked to
  audit/harden/re-verify telemetry, check for phone-home leaks, after bumping
  any telemetry-capable vendor pin, when adding a dependency or process
  launcher (strict mode fails on an unclassified one), or as a standing sweep.
---

Invoke the `otel-hardening` skill for the given task. $ARGUMENTS

See `.claude/skills/otel-hardening/SKILL.md` for full detail.
