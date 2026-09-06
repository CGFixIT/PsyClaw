#!/usr/bin/env bash
# CyClaw SessionStart hook -- model-gated fable-protocol loader.
#
# Injects .claude/skills/fable-protocol/SKILL.md as additionalContext at every
# SessionStart (startup / resume / clear / compact) UNLESS the session model is
# Fable-tier. Rationale: the protocol exists to make a smaller model apply the
# disciplines a stronger one applies by default; Fable is the model it was
# written by and for, so it gets the skill on demand (/fable-protocol), not
# injected.
#
# Why SessionStart and not per prompt: SessionStart is the ONLY hook event whose
# stdin JSON carries the model (`model`, optional -- verified against the Claude
# Code 2.1.261 hook schema: base {session_id, transcript_path, cwd,
# permission_mode?} + SessionStart {source, agent_type?, model?}; UserPromptSubmit
# carries only {prompt}). A mid-session `/model` switch fires no hook at all, so
# this cannot re-gate on it -- see .claude/README.md. Per-prompt injection of
# this file was also deliberately unwired on 2026-09-04 (cost + attack surface).
#
# Exit code is always 0: this hook advises, it must never block a session.
# Diagnostics go to stderr; stdout carries only the hook JSON (or nothing).
set -uo pipefail

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 0
skill="$repo_root/.claude/skills/fable-protocol/SKILL.md"
[ -f "$skill" ] || { echo "[fable-loader] $skill missing; nothing injected" >&2; exit 0; }
command -v jq >/dev/null 2>&1 || { echo "[fable-loader] jq not on PATH; nothing injected" >&2; exit 0; }

input=$(cat 2>/dev/null || true)
model=$(printf '%s' "$input" | jq -r '.model // empty' 2>/dev/null || true)
# tr, not ${var,,}: macOS ships bash 3.2, which lacks case-conversion expansion.
model_lc=$(printf '%s' "$model" | tr '[:upper:]' '[:lower:]')

case "$model_lc" in
  *fable*|*mythos*)
    echo "[fable-loader] model '$model' is Fable-tier; fable-protocol not injected (available on demand via /fable-protocol)" >&2
    exit 0 ;;
esac

# Absent/unknown model falls through to inject: a cheap discipline layer applied
# once too often beats one silently skipped on a model string we did not expect.
echo "[fable-loader] model '${model:-unknown}': injecting fable-protocol (SessionStart only; a mid-session /model switch does not re-run this hook)" >&2
jq -cn --rawfile ctx "$skill" '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$ctx}}'
exit 0
