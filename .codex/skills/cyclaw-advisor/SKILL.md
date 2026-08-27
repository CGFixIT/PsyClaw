---
name: cyclaw-advisor
description: Read-only CyClaw architecture and operations advisor for origin/main d9b0f8cd (2026-08-27). High-signal since 7564f508 — ToolBroker name-gate in utils (WebTool, harness /loop, POST /api/agent/run), hard sandboxes (Win Job Object, Darwin Seatbelt, Linux netns), acceptance-manifest + disposable-copy before real-repo finalize, soul-leak on check_output, fail-closed generation-call inventory, telemetry-kill process-boundary delivery, 27B plan-burst prompt. Graph still 12 nodes. Use when asked how gateway, graph, retrieval, auth, soul, memory, harness, or optional layers connect, where to change something, whether a design or PR is safe, or when the user runs /cyclaw-advisor.
metadata:
  short-description: Current-main CyClaw architecture and operations advisor
  recorded-head: d9b0f8cdb7b59923371da692ab0be18ed116e9df
  recorded-date: "2026-08-27"
---

# CyClaw Advisor

Advisory and study-oriented. Do not change code, configuration, soul content, branches, or pull requests unless the user explicitly changes the request to an implementation task. Re-verify mutable claims against `origin/main` and live code; this file is a routing aid, not an authority snapshot.

Recorded HEAD for this refresh — `d9b0f8cdb7b59923371da692ab0be18ed116e9df` (2026-08-27). If live `origin/main` moves and no high-signal merge landed, say the skill is already current.

Hardware / install / harness-path facts that affect environment references live in the sibling `CyClaw-environment.md`. Code and `config.yaml` still win.

## Activation

1. Fetch and state the current `origin/main` SHA.
2. Read `AGENTS.md`, `CLAUDE.md`, `INVARIANTS.md`, `config.yaml`, and affected code before answering.
3. Re-list open PRs before advising a change or merge order.
4. Use `README.md`, `docs/THREAT_MODEL.md`, and subsystem docs only for context; code and configuration win when they disagree.

## Current architecture to verify

The core request path is `gate.py` -> `graph.py` -> retrieval/LLM services. Live `graph.py` `add_node` count on this HEAD is **12**, not 10. Count live `add_node` calls rather than trusting a copied list if main changes. Current nodes — `retrieve`, `route_by_score`, `guardrail_input`, `guardrail_output`, `local_llm`, `user_gate`, `pre_action_hook_grok`, `pre_action_hook_claude`, `grok_fallback`, `claude_fallback`, `offline_best_effort`, `audit_logger`. Policy routers and conditional edges must remain in the graph, and all paths must converge on `audit_logger`. A 13th node is forbidden.

The six invariants are the decision frame — RAG-first, topology-as-policy, triple-gated external fallback, audit convergence, soul governance, and module isolation. Never weaken those or the I6 / out-of-band contract. The optional `agentic`, `sync`, `guardrails`, `harness`, and `telegram` layers remain out of the core request path (`gate.py` / `graph.py` / `mcp_hybrid_server.py` must not import them).

`graph.py` now calls `utils.endpoint_trust.assert_loopback` before the local LLM and `assert_online_destination` before Grok/Claude. That is a destination allowlist, not a new graph node. `generate_guard` (NVIDIA `check()` via `utils/guardrail_bridge.py`) may wrap `client.generate`; `None` is the unwrapped path. Do not wire `generate_async` onto `/query`.

Current main also includes Stage 2 auth in `gate_auth.py`, optional username on graph state when auth is enabled, memory routes and stores behind their master switch, and the loopback harness console. Verify each `enabled` value in `config.yaml`; route presence does not mean a feature ships enabled. `guardrails.enabled` still ships boolean `false`.

`agentic/writer.py` still ships `EXECUTION_ENABLED = True` (armed 2026-08-07) with `mode: write` and `writes_enabled: true`, but `agentic.enabled` still ships `false`. The six-gate write chain is unchanged. Do not describe the write path as live just because the code flag is armed.

### ToolBroker (utils, not guardrails)

Canonical name-gate is `utils/tool_broker.py` so harness can call it without importing `guardrails` (I6). Empty allowlist is deny. NeMo must never grant a tool. Audit stores tool name + argv digest only — never raw argv, URLs, instruction, or reason.

Live callers on this HEAD:

- WebTool (`harness/web_search.py`) — `assert_allowed` before fetch.
- Harness `POST /api/chat` with `loop=true` — `assert_allowed("harness_loop", (session_id,), ...)` after `_guard_loop_turn`, before the generation lock. Prompts stay out of argv. Empty allowlist is `TOOL_DENIED` and does not call the model.
- `POST /api/agent/run` — after check-profile resolve, `assert_allowed("agent_run", ("real-repo-run",), allowlist=frozenset({"agent_run"}))` before `ops_runner`. Empty allowlist is 403 `TOOL_DENIED` and does not spawn. Instruction/reason stay out of argv. Confirm/reason gates still apply; the broker cannot grant a run when confirm is missing.

`docs/NeMo/phase5_agent_run_broker.md` still says "contract only". Live `harness/server.py` already implements the wrap (#1163). Code wins.

### Real-repo approve / executor

Approve is bound to an acceptance-manifest digest (`agentic/executor/manifest.py`) — `run_id + base HEAD + path→sha256`. Digest mismatch or HEAD drift refuses approve. That is a TOCTOU close, not a signature.

Before finalize, `prove_disposable_copy` (`agentic/executor/apply.py`) copies the candidate tree, scrubs user/system git config, pins `core.hooksPath` to an empty dir, re-runs `verify_manifest`, and destroys the copy.

Production verification must not call unconstrained `subprocess.run`. `production_sandbox()` (`agentic/executor/hard_sandbox.py`) selects Windows Job Object, Darwin `sandbox-exec` Seatbelt, or Linux `unshare --net`. Missing binary or failed capability probe raises `HardSandboxUnavailable` — fail closed, no software fallback to `ArgvListSandbox` (tests only). POSIX timeout kills the process group, not just the wrapper.

## Latest merges into origin/main (high-signal only)

Most recent first. Ignore docs polish, screenshot uploads, dependabot noise, and dead-code housekeeping unless they touch the list in the update procedure.

- #1162 — NeMo `check()` exercised under a temp `guardrails.enabled: true` overlay in CI; shipped `config.yaml` stays boolean `false`.
- #1161 — disposable-copy match before real-repo finalize (`agentic/executor/apply.py`).
- #1160 — Darwin Seatbelt + Linux netns hard-sandbox backends; POSIX tree-kill on timeout; Seatbelt `TMPDIR`.
- #1163 — gate `POST /api/agent/run` through ToolBroker (`agent_run` / argv `real-repo-run`).
- #1158 — gate harness `/loop` through ToolBroker (`harness_loop`); import from `utils`, not `guardrails`.
- #1159 — ToolBroker adversarial eval pack; `decide()` does not interpret argv or spawn.
- #1157 — ToolBroker name-gate in `utils/tool_broker.py`; WebTool wraps before fetch. Harness must not import `guardrails` (I6).
- #1156 — fail-closed generation-call inventory on registered adapters.
- #1155 — enforce `detect_soul_leak` on `check_output` without `scan_injection`.
- #1154 — bind real-repo approve to an acceptance-manifest digest.
- #1153 — fail-closed Windows Job Object sandbox for the agentic executor.
- #1152 — tailor `real-repo-run-plan` prompt for 27B burst handoff; real-repo-run stays CLI not MCP.
- #1149 — telemetry-kill contract: canonical safe env, real ONNX suppression, process-boundary delivery, independent checker. Not a general network kill switch.
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

Late 27 Aug 2026 was #1134 Phase 4/5 on the out-of-band harness and agentic executor, plus the #1135 telemetry-kill delivery fix. The 12-node graph and I1-I6 edges are intact. New advisor-relevant facts are ToolBroker as a utils name-gate in front of WebTool, `/loop`, and `/api/agent/run`; hard sandboxes that fail closed instead of falling back to raw subprocess; acceptance-manifest + disposable-copy before real-repo approve/finalize; soul-leak on output rails; fail-closed generation-call inventory; and telemetry-kill applied at process boundaries (not a network kill switch). Writer six-gate posture is unchanged (armed code flag, master switch off). Do not treat the stale Phase 5 contract note as unimplemented.

## Load-bearing checks

Before citing values, inspect `config.yaml` and relevant code for the loopback host/port, retrieval RRF settings and threshold, model IDs, auth/memory/agentic/harness/Telegram/sync switches, telemetry-kill ordering, and API-key enforcement. Do not infer cosine similarity from an RRF threshold, claim `/query` is always authenticated when auth is disabled, or claim memory is on when its master switch is false. Do not treat `EXECUTION_ENABLED = True` as "writes are on" while `agentic.enabled` is false. Do not treat ToolBroker allow as permission to skip confirm/reason. Do not claim Darwin/Linux verification can run unconstrained if `sandbox-exec` / `unshare --net` is missing — that path is `HARD_SANDBOX_UNAVAILABLE`.

## Response shape

Give the direct answer first, then cite paths and symbols, identify the relevant invariant or risk, distinguish confirmed facts from inference, and state what was not verified. For PR advice, report base/head topology, overlap, CI state, and whether the recommendation is safe, needs design, or violates an invariant. Never expose private corpus contents, raw audit records, secrets, or credentials.
