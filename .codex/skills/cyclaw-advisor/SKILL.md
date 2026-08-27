---
name: cyclaw-advisor
description: Read-only CyClaw architecture and operations advisor for origin/main 7564f508 (2026-08-27). High-signal since last Codex snapshot — 12-node graph (pre-action hooks + input/output rails), NeMo 0.24, endpoint-trust, NVIDIA check() wrap, provenance registry, Numbat CEL, Ollama reasoning_effort, pygments test pin. Use when asked how gateway, graph, retrieval, auth, soul, memory, harness, or optional layers connect, where to change something, whether a design or PR is safe, or when the user runs /cyclaw-advisor.
metadata:
  short-description: Current-main CyClaw architecture and operations advisor
  recorded-head: 7564f508ba37708c313d88831593f8ea5d4b15bf
  recorded-date: "2026-08-27"
---

# CyClaw Advisor

Advisory and study-oriented. Do not change code, configuration, soul content, branches, or pull requests unless the user explicitly changes the request to an implementation task. Re-verify mutable claims against `origin/main` and live code; this file is a routing aid, not an authority snapshot.

Recorded HEAD for this refresh — `7564f508ba37708c313d88831593f8ea5d4b15bf` (2026-08-27). If live `origin/main` moves and no high-signal merge landed, say the skill is already current.

## Activation

1. Fetch and state the current `origin/main` SHA.
2. Read `AGENTS.md`, `CLAUDE.md`, `INVARIANTS.md`, `config.yaml`, and affected code before answering.
3. Re-list open PRs before advising a change or merge order.
4. Use `README.md`, `docs/THREAT_MODEL.md`, and subsystem docs only for context; code and configuration win when they disagree.

## Current architecture to verify

The core request path is `gate.py` -> `graph.py` -> retrieval/LLM services. Live `graph.py` `add_node` count on this HEAD is **12**, not 10. Count live `add_node` calls rather than trusting a copied list if main changes. Current nodes — `retrieve`, `route_by_score`, `guardrail_input`, `guardrail_output`, `local_llm`, `user_gate`, `pre_action_hook_grok`, `pre_action_hook_claude`, `grok_fallback`, `claude_fallback`, `offline_best_effort`, `audit_logger`. Policy routers and conditional edges must remain in the graph, and all paths must converge on `audit_logger`.

The six invariants are the decision frame — RAG-first, topology-as-policy, triple-gated external fallback, audit convergence, soul governance, and module isolation. Never weaken those or the I6 / out-of-band contract. The optional `agentic`, `sync`, `guardrails`, `harness`, and `telegram` layers remain out of the core request path (`gate.py` / `graph.py` / `mcp_hybrid_server.py` must not import them).

`graph.py` now calls `utils.endpoint_trust.assert_loopback` before the local LLM and `assert_online_destination` before Grok/Claude. That is a destination allowlist, not a new graph node. `generate_guard` (NVIDIA `check()` via `utils/guardrail_bridge.py`) may wrap `client.generate`; `None` is the unwrapped path.

Current main also includes Stage 2 auth in `gate_auth.py`, optional username on graph state when auth is enabled, memory routes and stores behind their master switch, and the loopback harness console. Verify each `enabled` value in `config.yaml`; route presence does not mean a feature ships enabled.

`agentic/writer.py` still ships `EXECUTION_ENABLED = True` (armed 2026-08-07) with `mode: write` and `writes_enabled: true`, but `agentic.enabled` still ships `false`. The six-gate write chain is unchanged. Do not describe the write path as live just because the code flag is armed.

## Latest merges into origin/main (high-signal only)

Most recent first. Ignore docs polish, screenshot uploads, dependabot noise, and dead-code housekeeping unless they touch the list in the update procedure.

- `2bfa5f40` — Ollama-only `models.local_llm.reasoning_effort` (default `none`) plus LocalProposerClient payload + OLLAMA_SETUP docs. Not an invariant change.
- #1147 — `HandoffEnvelope.had_redactions` bool (was misleading `redactions_applied` int). Cloud-handoff audit shape only.
- #1146 — `pygments==2.21.0` pin on `pyproject.toml` test extra (closes unconstrained `.[test]` resolve).
- #1144 — docs reconcile; guarded-route comments now say 18 key-gated gate routes and 29 guarded harness routes (comment/doc only).
- #1143 — optional CEL monitor backend for Numbat structured-field rules.
- #1142 — untrusted retrieval provenance ids + optional Qwen tag manifest.
- #1141 — allowlist Grok/Claude destinations; require loopback for local LLM (`utils/endpoint_trust.py`).
- #1140 — wrap existing generate with NVIDIA `check()` via the guardrail bridge.
- #1139 — optional `nemoguardrails` pin 0.23.0 -> 0.24.0.
- #1138 — first-party Numbat emission for pre-action hook verdicts.
- #1137 — NeMo self-check model smash stopped; engine keyed by policy fingerprint.
- #1136 — guardrail boundary types, profile matrix, typed metrics, real-NeMo CI.
- #4e252cb — CI `--cov=utils.endpoint_trust` on `ci.yml` and `python-package-conda.yml`.

## Recent activity summary

27 Aug 2026 was a security-surface day on optional rails and destination trust, not a core-topology rewrite. The 12-node graph and I1-I6 edges are intact. New advisor-relevant facts are endpoint-trust checks inside existing fallback/local nodes, optional NVIDIA `check()` around generate, NeMo 0.24 as the optional engine pin, provenance/Qwen registry, and Numbat hook+CEL emission. Writer six-gate posture is unchanged (armed code flag, master switch off). Ollama `reasoning_effort` is local-provider config only.

## Load-bearing checks

Before citing values, inspect `config.yaml` and relevant code for the loopback host/port, retrieval RRF settings and threshold, model IDs, auth/memory/agentic/harness/Telegram/sync switches, telemetry-kill ordering, and API-key enforcement. Do not infer cosine similarity from an RRF threshold, claim `/query` is always authenticated when auth is disabled, or claim memory is on when its master switch is false. Do not treat `EXECUTION_ENABLED = True` as "writes are on" while `agentic.enabled` is false.

## Response shape

Give the direct answer first, then cite paths and symbols, identify the relevant invariant or risk, distinguish confirmed facts from inference, and state what was not verified. For PR advice, report base/head topology, overlap, CI state, and whether the recommendation is safe, needs design, or violates an invariant. Never expose private corpus contents, raw audit records, secrets, or credentials.
