# CyClaw × Numbat × Always-On Roadmap

Status: living plan
Related PR: feat/numbat-audit-ndjson-v1 (mainline audit-trail projection)

> Reality check (2026-08-21, baseline `main`): parts of the original Step 1–2
> scope already shipped — the out-of-band action-plane emitter
> (`utils/numbat_emitter.py`, #959/#973), the rules-test fixture CI job
> (#961/#981), and the pre-external hook runner
> (`utils/external_pre_hook.py`, `policy.fallback.pre_action_hook`).
> This document tracks what is DONE upstream vs what remains.

## North star

- Numbat = external monitor/enforce plane (never imported as a Python dep;
  it is an external static Go binary scanning emitted NDJSON).
- Grok Build patterns = templated prompts + reversibility taxonomy (later).
- Always-on = three planes: Control/daemon, Channels (thin → POST /query),
  Jobs (out-of-band under `sync/` and `agentic/`).
- Never bolt OpenClaw/Hermes-style subsystems into the query graph.

## Invariants lock

I1–I6 in `INVARIANTS.md` (and `tests/test_due_diligence_invariants.py`).
Any step that needs an invariant exception is rejected:

| ID | Rule |
|----|------|
| I1 | `retrieve` remains sole graph entry — no pre-retrieval nodes |
| I2 | Routing stays topology/edges, not free-form LLM policy |
| I3 | External providers (Grok/Claude) remain triple-gated |
| I4 | Every path converges on `audit_logger` → END; audit writes fail-soft |
| I5 | Soul evolution stays human-gated (`reason` required) |
| I6 | `agentic/`, `sync/` jobs stay out-of-band — never imported from `gate.py` / `graph.py` |

## Numbat integration

### Step 1 — Audit NDJSON dual-write

**DONE (action plane):** #973 merged `utils/numbat_emitter.py` —
executor checks, `ops_runner` subprocesses, `real_repo_loop` decisions,
fsconnect/sqlconnect operations project to `logs/numbat-events.ndjsonl`
(schema 0.3.0, Numbat CLI 0.2.0 wire contract, `source_agent: "unknown"`,
`source_type: "hook"`, `tags: ["cyclaw", ...]`). #981 pins the CLI in CI
with emitter-shaped fixtures.

**THIS PR (mainline request path):** `utils/logger.audit_log()` now also
projects every redacted legacy audit record (`rag_query`, `user_gate_pause`,
`prompt_injection_blocked`, `rate_limit_exceeded`, soul governance events,
`mcp_rag_*`, `retrieval_degraded`, `*_prompt_truncated`, …) into the same
Numbat stream via `project_audit_record()`:

- Explicit event-name mapping table; unknown events degrade to
  `tool.call` at `confidence: "low"` — an audit line is never dropped.
- Numbat's `additionalProperties: false` means CyClaw forensics
  (`query_hash`, `top_score`, `guardrail_*`, `sources`, …) cannot be
  top-level keys; they ride in a capped (2000-char) `content_preview`
  JSON string built from the ALREADY redacted/hashed record.
- `artifact_type: "cyclaw_audit_jsonl"` distinguishes the projection from
  action-plane records in the same file.
- Lazy import inside `audit_log()` — no new import-time surface for
  `gate.py` / `graph.py` (I6 hygiene; `numbat_emitter` imports `_anchor` /
  `_get_config` from `utils/logger`, so a top-level import would be circular).
- `logs/audit.jsonl` stays authoritative and always written; the projection
  is fail-soft and independently disabled by `numbat.enabled: false`.

**Validation:** `tests/test_numbat_audit_projection.py` (mapping table,
schema allowlist discipline, no raw query text in either stream, disabled
switch, fail-soft on disk error) plus existing
`tests/test_numbat_emitter.py`, `tests/test_audit.py`,
`tests/test_logger.py`, `tests/test_due_diligence_invariants.py`.

**Exit criteria:** tests green; sample lines documented in the PR body.

### Step 2 — Pre-external hook contract (exit code 2 = deny)

**DONE (runner):** `utils/external_pre_hook.py` implements the contract —
JSON on stdin (provider, model, query_hash; no secrets), exit 0 allow /
exit 2 deny / anything else fail-closed deny + audit, hard timeout clamped
to [1, 30]s (default 5). Wired on the external fallback path in `graph.py`;
configured via `policy.fallback.pre_action_hook`. Disabled by default.

**Remaining:** monitor/enforce mode split (`hooks.fail_mode`), Numbat-shaped
`permission.denied` / `network.indicator` emission from the hook verdict
itself (today the external hook command owns its own emission).

### Step 3 — CEL sanitizer backend (monitor-first)

- Optional cel-python rules evaluating structured fields, not raw prompts alone.
- Ship monitor-only rules first; enforce later.
- Keep the regex banned-list as fail-closed baseline until CEL is proven.

### Step 4 — Sequence rules

- Multi-event patterns (injection → exfil tool chain).
- Requires stable `session_id` plumbing end to end (prerequisite task;
  see open issue #966 for cross-request state on the /query path).

### Step 5 — Templated soul / prompt assembly (Grok Build-inspired)

- Versioned templates under soul governance.
- Still human-gated apply; no AGENTS.md auto-ingest from untrusted trees.
- Action-reversibility taxonomy as edges/tool allowlists, not prose.

## Always-on architecture (OpenClaw/Hermes shape without their trust model)

### Phase 0 — Daemonize local stack

- launchd/systemd user units: Ollama/LM Studio + cyclaw gate :8787
  (+ harness :8790 if used). Windows: see `windows/` service scripts.
- Health checks; crash restart; log rotation. No new authority.

### Phase 1 — Channels check-in only

- Telegram (allowlisted chat_id) thin client exists under `telegram/`;
  Slack equivalent would follow the same shape.
- Channel adapters only call the existing HTTP API (`POST /query`,
  `/health`, `/soul` GET). No direct graph imports; no tool execution in
  the channel process.
- Authn: allowlist + shared secret header; bind stays loopback or tailnet.

### Phase 2 — Jobs runner (out-of-band)

- Extend `sync/runner.py` / agentic patterns already out-of-band.
- Job types: github pr poll, news digest, x/twitter trends (read-only first).
- Results land in corpus or notification sink — not silent soul edits.
- I6: `gate.py` must not import job modules.

### Phase 3 — Read-only collectors

- Explicit allowlisted endpoints; egress policy.
- All collector actions audited AND Numbat-projected (the projection added
  in Step 1 makes this free for anything routed through `audit_log`).
- User confirmation still required for any online escalation path (I3).

### Phase 4 — Numbat live hooks + harden

- Wire Step 2 hooks in enforce mode for external/tools.
- Case bundles / findings review workflow.
- Threat-model update (`docs/THREAT_MODEL.md`).

## OpenClaw / Hermes — what to steal vs refuse

Steal:

- Heartbeat ("anything need attention?" / HEARTBEAT_OK) vs cron (real work).
- Gateway as presence plane.
- Channel adapters as thin clients.

Refuse:

- Skill self-write into identity.
- Unbounded tool surface from chat.
- Trusting retrieved/channel text as authority.
- Merging jobs into the query graph.

## source_agent upstream

Track an issue/PR to `perplexityai/numbat` adding `cyclaw` to the
`source_agent` enums in the event/finding/enforcement/indicator schemas.
Until merged, records stay `source_agent: "unknown"` and identify CyClaw
via `entrypoint: "cyclaw"`, the `cyclaw` tag, and
`evidence.artifact_type`. Related: open issue #965 (upstream Numbat
artifact parser for CyClaw audit logs) is deferred in favor of this
first-party NDJSON projection.

## Implementation order & estimated effort

1. ~~Step 1 action-plane emitter~~ — DONE (#973)
2. Step 1 mainline audit projection (this PR) — ~1 day
3. `session_id` plumbing — 0.5 day (prerequisite for Step 4; cf. #966)
4. Step 2 hook runner — DONE; monitor/enforce split remains — 0.5 day
5. Phase 0 daemon — 0.5–1 day
6. Phase 1 Telegram allowlist hardening — 1 day
7. Steps 3–4 CEL/sequence — 2–3 days
8. Phase 2–3 jobs/collectors — 2–4 days
9. Step 5 templates + Phase 4 enforce — 2+ days

## Exit criteria per step

Each step: targeted pytest green, invariant tests
(`tests/test_due_diligence_invariants.py`) green, docs updated, and a
dual-run observation period before any monitor→enforce flip.
