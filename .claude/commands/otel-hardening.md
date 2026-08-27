---
description: >-
  Re-verify that CyClaw's telemetry-kill switches (utils/telemetry_kill.py, the
  conditional HF Hub offline wiring in retrieval/embeddings.py) still actually
  block every telemetry path for the dependency vendors CyClaw ships —
  chromadb, langchain/langsmith/langgraph, huggingface_hub/transformers,
  sentence-transformers, onnxruntime, opentelemetry, nemoguardrails — via a
  static re-check plus a live web/forum search for vendor-side drift, then
  propose or apply additive kill-switch fixes. Use when asked to audit/harden
  telemetry, check for phone-home leaks, after bumping a vendor pin, or as a
  periodic re-verification sweep.
---

Invoke the `otel-hardening` skill for the given task. $ARGUMENTS

See `.claude/skills/otel-hardening/SKILL.md` for full detail.
