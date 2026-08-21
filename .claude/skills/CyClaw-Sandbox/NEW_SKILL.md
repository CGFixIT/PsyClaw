## CLAUDE: next onvoke of this skill please revise this to customized for you instead of kimi:

---
name: cyclaw-swarm-ver-v2
description: CyClaw Swarm Verification v2 — comprehensive sandbox test system for the CyClaw offline-first RAG project (github.com/CGFixIT/CyClaw), updated to main @ 68595dfa / v1.9.0 (2026-08-21). Verifies the LangGraph pipeline (12 nodes incl. pre-action hooks, 5 queries), the Ollama local-LLM path (qwen3.8:27b-mlx, 127.0.0.1:11434) at three realism tiers, LM Studio fallback, triple-gated online API fallback (Grok + Claude) with connection-only tests, pre-action hook gate (#963), API key redaction, due-diligence invariants (14 classes), Phase 2+4 guardrails, spend tracking ledger (logs/spend.jsonl, PRICED_AS_OF staleness), Numbat event emission (logs/numbat-events.ndjsonl), sequence detection forensics, the memory subsystem, the auth subsystem (Stages 1-4: bootstrap, login/session+CSRF, RBAC admin/operator/audit, TLS), the Telegram channel, the OpenTweet channel, unslop bridge, the harness coding console (127.0.0.1:8790 incl. /api/keys, /api/web, /api/memory, /api/tools, /api/skills), MCP manifest drift pin, macOS launchd/Keychain + ollama-mlx glue, Windows Task Scheduler glue, sync schedulers, and ALL terminal.html REST endpoints: /soul, /ops/sync, /ops/agentic, /ops/fsconnect, /ops/sqlconnect — with a full sandbox clone-verification ladder that generates its own CYCLAW_API_KEY and exercises every gated surface. Use when asked to verify, smoke-test, validate, or test CyClaw; mentions CyClaw swarm, terminal consoles, Ollama, triple-gate API, Grok/Claude fallback, key redaction, due diligence invariants, guardrails, memory, telegram, opentweet, harness, macOS launchd, spend tracking, numbat, or running the test suite.
---

# CyClaw Swarm Verification v2

Baseline of this document: **main @ 68595dfa, version 1.9.0 (2026-08-21)**.
Supersedes the 2026-08-15 baseline (main @ 1e14d9c). When the repo has moved,
re-derive facts from `config.yaml`, `graph.py`, `gate.py`, `pyproject.toml`,
`tests/README.md`, and `INVARIANTS.md` — those files win over this document.

## 0. What changed since the 2026-08-15 baseline (read this first)

1. **Graph is now 12 nodes** (was 10): `retrieve -> route_by_score ->
   guardrail_input -> {local_llm | user_gate | offline_best_effort |
   guardrail_output}` and `user_gate -> {pre_action_hook_grok ->
   grok_fallback | pre_action_hook_claude -> claude_fallback |
   offline_best_effort}`; everything converges on `audit_logger`. Bare
   `graph.compile()` (no checkpointer). `build_graph()` is keyword-only.
2. **Pre-action hook gate (issue #963)**: `utils/external_pre_hook.py` —
   `run_pre_action_hook(provider, model, query_hash, cfg)`; JSON stdin payload
   `{action, provider, model, query_hash}`; exit 0 allow / 2 deny / any other
   = fail-closed deny; timeout clamped [1,30] s, default 5. Config
   `policy.fallback.pre_action_hook` (enabled false, command [],
   timeout_sec 5). Deny yields answer_model `"hook-denied"`; unavailable
   provider yields `"external-unavailable"` — both joined to spend/audit.
3. **Model bump**: `qwen3.8:27b-mlx` (was qwen3.6:27b), max_tokens 4096,
   timeout_sec 720, `api.graph_timeout_sec 780`. Recommended Ollama
   `num_ctx 16384`; `retrieval.max_context_tokens 4000`; `min_score 0.028`.
4. **Armed shipped posture** (since 2026-08-07): `app.mode: "hybrid"`,
   `models.grok.enabled: true`, `models.claude.enabled: true`,
   `agentic.mode: "write"` + `writes_enabled: true` — but the master
   `agentic.enabled` is still false, so nothing mutates until an operator
   flips it. `security.api_key_optional: false`.
5. **Spend tracking**: `utils/spend.py` append-only ledger
   `logs/spend.jsonl`; `record_external_usage(*, provider, model, usage,
   spend_file, source, query_hash, route_path)`; source ∈ {query, agentic,
   eval}; TICKS_PER_USD = 10_000_000_000; xAI `cost_in_usd_ticks` capture with
   `ticks_mismatch` (5% rel / 0.01 abs cap); `_RATES` for grok-4.5 ($2/$6 per
   1M tok, cached 0.30, long ≥200k ctx $4/$12) and claude-sonnet-5 ($2/$10,
   cache-write 2.50 / cache-read 0.20 / TTL split); PRICED_AS_OF "2026-08-19",
   STALE_AFTER_DAYS 30 (warns when stale). `cyclaw-metrics` splits spend by
   source and prints a vendor-cost comparison (`_add_spend_compare`,
   comparable_* pair). `tests/spend_live_probe.py` is an opt-in live probe.
6. **Numbat emission**: `utils/numbat_emitter.py`; config `numbat.enabled:
   true`; output `logs/numbat-events.ndjsonl` (schema 0.3.0; CI pins Numbat
   0.2.0 CLI). Mainline projection via `project_audit_record()` inside
   `audit_log` — fail-soft, lazy import. `_AUDIT_ACTION_PLANE_EVENTS` skip set
   (fsconnect_read, sqlconnect_read, agentic_real_repo_change_decided/
   approved, agentic_executor_check_result) prevents double-writes.
7. **Sequence detection (forensic-only)**: `utils/sequence_detect.py` CLI
   joined into cyclaw-metrics output; rules: repeat_hash,
   injection_then_online_rag, injection_then_external_spend,
   hook_denied_then_spend, window_injection_to_escalation (15-min),
   unjoinable_query_spend. NEVER imported by gate/graph/mcp — keep it that way.
8. **OpenTweet channel**: `opentweet/` (cli, config, client, runner,
   selftest); config block default disabled; drafts by default; loopback
   `/query` with `user_confirmed_online: false`; CLI exit codes 0 ok /
   2 config-or-disabled / 3 runtime. Design doc docs/channels/OPENTWEET_DESIGN.md.
9. **Unslop bridge**: `agentic/unslop_bridge.py` + vendored
   `agentic/vendor/unslop/`; config `unslop.enabled: false`; log-only,
   non-blocking; metrics at logs/unslop.jsonl. Excluded from coverage.
10. **Auth Stages 3 AND 4 are wired** (the repo's own
    `.claude/skills/CyClaw-Sandbox/SKILL.md` is stale on this — trust
    gate.py): `require_session_or_token` attached to POST /query when an auth
    manager exists (`attach_identity_to_query` rewrites route.dependencies and
    the dependant tree). RBAC roles admin/operator/audit — the audit role is
    forbidden from /query. Session cookie `cyclaw_session` (httponly,
    samesite=strict, secure=tls_enabled); CSRF header `x-cyclaw-csrf` hashed
    before compare; session_id/csrf_token stored hashed; scrypt N=2**17
    (OWASP floor); race-safe bootstrap insert + first-password claim. TLS
    Stage 4: `gate._serve` passes ssl_certfile/keyfile when
    `api.tls.enabled` is literal true; `cyclaw-gen-cert` script generates a
    self-signed pair. The 503-when-disabled contract still holds.
11. **API-key bypass hardening**: `security.api_key_optional: false` ships
    off. When on, bypass requires a loopback socket peer AND no reverse-proxy
    forwarding headers (X-Forwarded-*, X-Real-IP, Forwarded) —
    `_api_key_bypass_allowed` predicate; the bind guard refuses non-loopback
    hosts while the flag is on; inert under Docker; config-guard C13 keys on
    api.host.
12. **Harness grew**: routes /api/keys (env_keys.py — `$CYCLAW_HOME/.env`
    mode 600 atomic writes, MANAGED_KEYS allowlist, masked tail, names-only
    audit), /api/tools, /api/skills, /api/web + allow/deny/fetch/search/
    inject/forget (web_search.py), /api/memory + add/forget/clear
    (memory_notes.py), /api/chat/cancel, /api/sessions/{id}/goal + /rename.
    The `guarded` chain is rate-limit + same-origin + API key + CSRF;
    auto-docs disabled. Slash commands: /session /soul /model /skills /tools
    /web /memory /github /harness /tokens /status /goal /loop.
13. **MCP hardening**: `mcp_manifest.json` SHA-256 drift pin
    (utils/mcp_manifest.py, #987); `check_input` guardrail runs on MCP
    hybrid_search before retrieval (#982).
14. **Groundedness evaluator**: `tests/judge_eval.py`, opt-in
    `CYCLAW_EVAL_LIVE=1`, needs ANTHROPIC_API_KEY + a loopback LLM; exit 0
    pass / 1 fail / 2 infra; fixtures under tests/fixtures/groundedness/.
15. **macOS additions**: setup-from-clone.sh, setup-cyclaw-keys.sh,
    macos/ollama-mlx.env (16384 ctx, keep_alive 30m, flash-attn, KV q8_0),
    scripts/measure_local_llm_throughput.py, macos-smoke.sh (22-check Darwin
    twin of windows-smoke.ps1).
16. **Test suite**: 181 `test_*.py` files; `GROK_API_KEY=dummy pytest tests/
    -q --tb=short`; python3.12 REQUIRED (3.11 gives ~142 misleading errors);
    conftest mocks everything; ci_rag_smoke.py deliberately not test_*;
    judge_eval.py gated. invariant-guard I1-I6 + G1-G5 — report the actual
    count from the run (was 35).
17. **pyproject**: version 1.9.0; websockets==15.0.1, huggingface-hub==1.26.0,
    starlette==1.3.1; scripts: cyclaw-server, cyclaw-mcp, cyclaw-index,
    cyclaw-metrics, cyclaw-clear-cache, cyclaw-harness, cyclaw-user,
    cyclaw-gen-cert; wheel packages now include telegram, opentweet, memory;
    coverage omits agentic/vendor/*; marker `uses_shipped_execution_flag`.

## 1. Sandbox environment rules (unchanged, still binding)

- `/tmp` wipes between turns — clone fresh, or clone into a working dir you
  re-create each turn. Never assume a prior clone survives.
- Python 3.12 required. Verify with `python3 --version` before anything else.
- PyPI is throttled: use the Aliyun mirror wheelhouse recipe
  (`pip install --index-url https://mirrors.aliyun.com/pypi/simple/ ...`),
  or expect multi-minute stalls.
- HuggingFace: `HF_ENDPOINT=https://hf-mirror.com`, `HF_HUB_OFFLINE=1` once
  models are cached.
- `pkill -f` self-match hazard: your own shell command line contains the
  pattern — use `pkill -f 'pattern' || true` with a distinct marker, or kill
  by PID file.
- Long-running servers: `nohup ... &` then poll the health endpoint; never
  block on a foreground server.
- Kimi sandbox GitHub MCP constraints: CANNOT push `.github/workflows/**`;
  never retype push content from memory (re-read the file, push immediately,
  SHA-verify the blob); squash-merge is default; PR bodies say "CI is the
  verification of record".
- `/karpathy` and `/ponytail` slash skills are unavailable in this sandbox —
  say so if asked; fable-discipline self-review substitutes.

## 2. Full sandbox clone verification ladder (THE new core procedure)

This ladder clones origin/main, generates a real `CYCLAW_API_KEY`, and
exercises every gated surface with it. Run stages in order; record
PASS/FAIL/ANOMALY per stage.

### Stage 0 — Clone & install

```bash
cd /tmp && rm -rf cyclaw-verify
git clone --depth 50 https://github.com/CGFixIT/CyClaw cyclaw-verify
cd cyclaw-verify
git log -1 --format='%H %cs'   # record the sha/date for the sign-off
python3 --version              # must be 3.12.x
grep '^version' pyproject.toml # expect 1.9.0 or newer
pip install --index-url https://mirrors.aliyun.com/pypi/simple/ -e '.[dev]'
export GROK_API_KEY=dummy      # local gates require the var to exist
export CYCLAW_API_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
echo "CYCLAW_API_KEY generated (32-byte urlsafe) — used for ALL gated probes"
```

Key rule: the generated key is passed as `X-API-Key: $CYCLAW_API_KEY` (gate)
and `x-api-key` (harness). Never log the full key — use
`${CYCLAW_API_KEY:0:6}…` in notes.

### Stage 1 — Static gates (no server)

1. `GROK_API_KEY=dummy python3 -m pytest tests/ -q --tb=short` — expect all
   pass; record the count (181 test files; suite total printed at end).
2. `python3 tests/invariant_guard.py` (or the repo's current guard entry) —
   record N/N (I1-I6 + G1-G5).
3. Due-diligence: `GROK_API_KEY=dummy python3 -m pytest tests/test_due_diligence* -q`
   — 14 classes incl. TestShippedCoreConfigContract (armed posture:
   mode hybrid, grok/claude enabled, agentic write+writes_enabled,
   api_key_optional false).
4. Config contract spot-check vs `config.yaml`: model qwen3.8:27b-mlx,
   timeout_sec 720 < graph_timeout_sec 780, max_tokens 4096,
   retrieval.max_context_tokens 4000, min_score 0.028,
   numbat.enabled true, unslop.enabled false,
   policy.fallback.pre_action_hook.enabled false, opentweet disabled,
   memory consolidation stubbed, api.tls.enabled false (default),
   logging third_party_level INFO.

### Stage 2 — Boot the gate with the generated key

```bash
cd /tmp/cyclaw-verify
CYCLAW_API_KEY=$CYCLAW_API_KEY nohup python3 -m uvicorn gate:app \
  --host 127.0.0.1 --port 8787 >/tmp/gate.log 2>&1 &
echo $! > /tmp/gate.pid
for i in $(seq 1 30); do curl -sf 127.0.0.1:8787/health && break; sleep 1; done
```

Verify:
- `/health` 200 unauthenticated; `embeddings_local` entry present and static
  by design (weak signal — do NOT file as a finding).
- `curl 127.0.0.1:8787/` (index/terminal.html) 200.
- Auto-docs OFF: `/docs` and `/openapi.json` must 404 (or be unmounted).
- API-key required: `curl -X POST 127.0.0.1:8787/soul/reload -d '{}'
  -H 'Content-Type: application/json'` without key -> 401; with
  `-H "X-API-Key: $CYCLAW_API_KEY"` -> passes the key gate.
- Bypass posture: with shipped `api_key_optional: false` the key is always
  required; if a scratch config turns it on, verify bypass only works from a
  loopback peer with no forwarding headers, and that sending
  `X-Forwarded-For: 1.2.3.4` from loopback still 401s.

### Stage 3 — RAG pipeline, 5 queries (three realism tiers)

Tier 0 = stubs (conftest), Tier 1 = `.claude/skills/CyClaw-Sandbox/mock_ollama.py`
over HTTP (`--model qwen3.8:27b-mlx`), Tier 2 = a real Ollama daemon (usually
unavailable in sandbox — state the tier honestly).

Against the live gate (Tier 1 recommended: point `models.local.base_url` at
the mock on 127.0.0.1:11434 in a scratch config via `CYCLAW_CONFIG`):

1. **Vault hit, high score**: answer_model `local`, audit shows retrieve ->
   route_by_score -> guardrail_input -> local_llm -> guardrail_output ->
   audit_logger; audit record carries `llm` + `llm_model` fields.
2. **Vault hit, second phrasing**: same path; retrieval respected
   max_context_tokens 4000.
3. **Offline best-effort**: low-score query with no online confirmation ->
   offline_best_effort node; answer_model `offline-best-effort`.
4. **Grok connection-only**: with `GROK_API_KEY=dummy` the availability gate
   fails closed — answer_model `external-unavailable` (NOT a billed call).
   Then, if the operator supplies a real key, confirm-only flow:
   user_gate pauses (confirmed is None), `POST /query` with
   `user_confirmed_online: true` routes user_gate -> pre_action_hook_grok.
   With hook disabled (shipped default) -> grok_fallback. Connection-only:
   assert a request was attempted and audit/spend written; do NOT burn tokens
   beyond a minimal prompt.
5. **Claude connection-only**: same shape via pre_action_hook_claude ->
   claude_fallback; needs ANTHROPIC_API_KEY for the real variant.

Also verify on Tier 1:
- answer_model vocabulary ∈ {local, grok, claude, offline-best-effort,
  guardrail-blocked, hook-denied, external-unavailable}.
- Untrusted context wrapped in `ctx-<nonce>` tags; query/soul-aware char
  budgeting (`_context_char_budget`).
- Guardrail-blocked input still reaches audit_logger (convergence invariant).
- Every path — including hook-denied and external-unavailable — reaches
  audit_logger exactly once.

### Stage 4 — Pre-action hook gate (#963)

In a scratch config enable `policy.fallback.pre_action_hook` with:
- command = a stub that exits 0 -> grok_fallback proceeds.
- command = a stub that exits 2 -> answer_model `hook-denied`; audit records
  the denial with the hook outcome; NO external call is attempted.
- command = a stub that exits 1 / times out -> fail-closed deny (same
  hook-denied surface); timeout clamped to [1,30].
- Inspect the JSON stdin payload the stub receives: keys exactly
  {action, provider, model, query_hash} — no query text, no soul.

### Stage 5 — Spend tracking

1. `logs/spend.jsonl` is append-only; after Stage 3 query 4/5 (real-key run)
   each external generate appends one record with provider, model, usage,
   source (`query`), query_hash, route_path hops.
2. `estimate_usd` matches the _RATES table for grok-4.5 / claude-sonnet-5;
   long-context band (≥200k) priced at $4/$12 for grok-4.5.
3. xAI `cost_in_usd_ticks`: when the vendor returns ticks, they are captured;
   `ticks_mismatch` flags >5% rel (0.01 abs cap) drift vs estimate.
4. `cyclaw-metrics` output splits spend by source (query vs agentic) and
   prints the vendor-comparison pair (comparable_* rows via
   `_add_spend_compare`).
5. PRICED_AS_OF staleness: with today > 30 days past "2026-08-19", a stale
   warning surfaces — verify the warning fires and is non-fatal.
6. Agentic-source spend: an agentic executor run that calls an external
   provider records source `agentic` (opt-in; requires agentic.enabled).

### Stage 6 — Numbat emission

1. With `numbat.enabled: true` (shipped), gate activity appends NDJSON lines
   to `logs/numbat-events.ndjsonl` — schema 0.3.0 envelope.
2. Fail-soft: make the emitter raise (e.g. read-only logs dir in a scratch
   copy) — the request still succeeds; no exception escapes audit_log.
3. Skip set: fsconnect_read, sqlconnect_read,
   agentic_real_repo_change_decided/approved, agentic_executor_check_result
   produce NO numbat mainline events (they have their own plane).
4. If the Numbat 0.2.0 CLI is available (CI pins it), validate the ndjsonl
   file against the CLI.

### Stage 7 — Sequence detection (forensic CLI)

1. `python3 -m utils.sequence_detect --help` (or the cyclaw-metrics joined
   surface) lists the six rules.
2. Synthetic fixtures: craft audit+spend logs containing
   injection_then_external_spend and hook_denied_then_spend patterns in /tmp;
   assert the CLI flags them; assert a clean log flags nothing.
3. `unjoinable_query_spend`: a spend record whose query_hash has no audit
   join is flagged.
4. Import isolation: `grep -rn "sequence_detect" gate.py graph.py mcp*.py`
   returns nothing.

### Stage 8 — Auth subsystem (Stages 1-4, all now wired)

Against the live gate with the generated key:

1. **503-when-disabled** (default config): every /auth/* route returns 503
   with the disabled-state body; process exits 0 on the self-check path.
2. Enable auth in a scratch config, then:
   - `GET /auth/setup-status` -> reports unbootstrapped.
   - `POST /auth/bootstrap-password` from loopback -> sets the first admin
     password (race-safe insert; second call refused). Non-loopback -> refused.
   - `POST /auth/login` -> sets `cyclaw_session` cookie (httponly,
     samesite=strict; secure only when tls.enabled). Response includes the
     CSRF token surface; session_id and csrf_token stored hashed on disk.
   - `GET /auth/whoami` with cookie -> identity; without -> 401.
   - **Stage 3 enforcement**: `POST /query` with ONLY the session cookie and
     no CSRF header -> 403; cookie + `x-cyclaw-csrf` -> passes identity.
     Bearer admin token path also passes (`_require_write_actor`:
     cookie+CSRF OR admin bearer).
   - RBAC: create users via `POST /auth/users` (admin only); an
     `audit`-role session calling POST /query -> 403; `operator` allowed.
   - Password rotation: `POST /auth/password` (self),
     `POST /auth/users/{u}/password` (admin), `/auth/users/{u}/role`,
     `DELETE /auth/users/{u}`, `/auth/disable`, `/auth/enable`.
   - `GET /auth/audit/summary` returns hashed/pseudonymized entries only —
     no plaintext passwords, no raw session ids.
   - Same-origin enforcement: cross-site `Sec-Fetch-Site` / mismatched Origin
     host/port/scheme -> refused on mutating routes.
   - Rate-limited 401: repeated bad logins throttle and audit.
3. **Stage 4 TLS**: `python3 -c "from utils import ..." ` not needed — use
   the `cyclaw-gen-cert` script into a scratch dir, set `api.tls.enabled:
   true` with cert/key paths, boot, and confirm https serves and the session
   cookie now carries `secure`. Boot failure without cert files must be a
   clear error, not a silent http fallback.
4. Honesty check: no claims of features beyond Stages 1-4 anywhere in docs.

### Stage 9 — Guardrails (input + output)

1. Input: prompt-injection fixture -> guardrail_input blocks -> answer_model
   `guardrail-blocked` -> audit_logger still reached.
2. Output: `guardrail_output` is grounding-only — verify it does not attempt
   semantic filtering; `guardrail_degraded` event fires on output-guard
   exception (fail-open).
3. Known residual: `check_jailbreak` and `check_soul_leak` are listed in
   config but NOT enforced by the offline heuristic floor (model-assisted
   only; config comments say so). Not a finding — a residual.
4. MCP path: hybrid_search runs check_input BEFORE retrieval (#982).

### Stage 10 — Memory subsystem

1. Disabled default: `/memory/status` returns 200 with flags off;
   `/memory/facts`, `/memory/episodes`, `/memory/proposals` 404-gated by
   toggles; `/memory/propose|apply|reject` 404 when toggles off.
2. Enabled (scratch config): propose -> apply -> facts queryable;
   episodes limit floor 0 / cap 500; reject emits memory_reject +
   memory_proposal_rejected events; injection in a proposal payload triggers
   memory_apply_injection_blocked.
3. `/query/export/html` gated by memory.export_html.
4. Fusion never raises; `memory/consolidation.py` remains a deliberate stub
   (must stay disabled in v1).

### Stage 11 — Telegram channel (mocked Bot API tier)

1. CLI disabled-state report exits 0; codes 0 ok / 2 config-or-disabled /
   3 runtime.
2. Proxy hygiene (#917): telegram/client.py ignores HTTP(S)_PROXY for Bot
   API calls (pinned by tests).
3. T3 hybrid-confirm: `_online_command` accepts ONLY exact `/online on grok`
   / `/online on claude` in an allowlisted PRIVATE chat; one-shot grants,
   `hybrid_confirm_ttl_sec: 120`; grants and refusals audited.
4. SlidingWindowLimiter reserve/release — failed send releases its slot.
5. Long-poll offset persistence fail-closed.
6. T4 media: partial, POSIX-only, staged through fsconnect write path,
   `max_download_bytes: 10485760`.
7. Mocked-Bot-API integration: stub getMe/sendMessage/getUpdates/getFile,
   point telegram.api_base at it, drive poll_once/send_notify; assert
   chunking at max_message_chars 4000 and retry_after cooldown.

### Stage 12 — OpenTweet channel (new)

1. Default disabled: CLI reports disabled and exits 2 (config-or-disabled).
2. Enabled in scratch config: runner calls loopback `/query` with
   `user_confirmed_online: false` — never escalates online by itself.
3. Drafts-by-default: no post is sent without explicit confirm.
4. Selftest path exits 0; exit codes 0/2/3 honored.
5. Out-of-band set: opentweet is part of the I6 isolation set — it must
   never be imported by core (gate/graph).

### Stage 13 — Unslop bridge (new)

1. `unslop.enabled: false` shipped — zero effect when off.
2. Enabled in scratch config: log-only, non-blocking — force the vendored
   unslop to raise; the agentic run still completes; entry lands in
   logs/unslop.jsonl.

### Stage 14 — Harness console (:8790)

```bash
CYCLAW_HOME=/tmp/cyclaw-home CYCLAW_API_KEY=$CYCLAW_API_KEY \
  nohup python3 -m harness.server --port 8790 >/tmp/harness.log 2>&1 &
```

1. Runtime check: `python3 .claude/skills/CyClaw-Sandbox/harness_runtime_check.py`
   — create_app() without a live LLM; telemetry-kill env active; auto-docs
   disabled; `_LOOPBACK_HOSTS` guard defined; new routes registered:
   /api/tools, /api/skills, /api/web{,/allow,/deny,/fetch,/search,/inject,
   /forget}, /api/memory{,/add,/forget,/clear}, /api/chat/cancel,
   /api/sessions/{id}/goal, /api/sessions/{id}/rename, /api/keys, plus the
   agent routes.
2. `GET /api/status` -> version, model qwen3.8:27b-mlx, provider ollama,
   base_url 11434/v1, home, repo_root, sessions, layout.
3. Loopback-only: non-loopback bind refused.
4. Guarded chain on every mutating route: rate-limit + same-origin + API key
   + CSRF — probe each guard independently (missing key 401, missing CSRF
   403, cross-origin refused).
5. **/api/keys**: PUT a managed key -> lands in `$CYCLAW_HOME/.env` mode 600
   (atomic tmp+rename); GET returns masked tail only; a key NOT in
   MANAGED_KEYS is refused; audit carries key NAMES only, never values.
6. **/api/web**: allow/deny list lifecycle; fetch/search/inject honor the
   allowlist; forget purges. Disabled/empty policy fails closed.
7. **/api/memory**: add/forget/clear round-trip against $CYCLAW_HOME notes.
8. **/api/chat/cancel**: cancels an in-flight run; unknown id -> clean 404.
9. Soul consumer: harness/prompts.py reads soul.md read-only from disk —
   INVARIANTS Rule 5 third consumer, deliberately unscanned. Not a finding.
10. Agent loop: /api/agent/checks, /api/agent/run, /api/agent/runs/{id} +
    decision/push/publish/discard — the human decision gate MUST precede any
    publish/discard transition.
11. harness.html: no-innerHTML contract holds; #apiKey field present; slash
    commands /session /soul /model /skills /tools /web /memory /github
    /harness /tokens /status /goal /loop respond.

### Stage 15 — Terminal consoles (all 5 REST surfaces)

With the gate up and the generated key:

1. `/soul` GET/POST, `/soul/propose`, `/soul/apply`, `/soul/reload` — empty
   reason refused (soul reason gate); write path scanned for injection;
   reload re-reads from disk.
2. `/ops/sync` — status + trigger; scheduler backend from config.
3. `/ops/agentic` — status reflects agentic.enabled false (master switch)
   even though mode/write flags are armed; executor check results audited.
4. `/ops/fsconnect` — read/list under configured roots only; path escape
   refused; writes only through the governed path; macOS volume roots off by
   default.
5. `/ops/sqlconnect` — read-only queries; mutation statements refused;
   fsconnect_read/sqlconnect_read skip numbat mainline (Stage 6.3).
6. terminal.html has NO memory console and NO full auth management UI
   (REST-only surfaces; only a minimal `.toolbar-auth` affordance exists) —
   known residual, not a finding.

### Stage 16 — MCP surface

1. `mcp_manifest.json` SHA-256 drift pin: `python3 -m utils.mcp_manifest`
   (or its CLI) verifies; flip a byte in a scratch copy -> drift reported,
   fail-closed.
2. No-LLM MCP path (invariant 11): hybrid_search works with no LLM booted.
3. check_input runs before retrieval (#982).

### Stage 17 — OS glue & schedulers (platform simulation on Linux)

1. `python3 macos/generate_service_plist.py` (no args) -> usage error;
   `--service gate` on Linux -> "this generator is Darwin-only" (platform
   gate fires before the governance gate, even with --confirm --reason).
2. `python3 windows/generate_service_task.py --service gate --confirm
   --reason x` on Linux -> "this generator is Windows-only".
3. Keychain wrappers: bare `-w` (secret from TTY, never argv); env wrapper
   fails closed.
4. macos-smoke.sh (22 checks) is the Darwin twin of windows-smoke.ps1 — on
   Linux, verify structure only.
5. `sync/scheduler.py::get_scheduler`: `scheduler_backend: "cron"` ->
   CronScheduler; `"launchd"` on non-Darwin -> SchedulerError (NO silent
   fallback — intentional); Windows -> WindowsTaskScheduler. CronScheduler
   matches only CyClaw-owned cron lines (#907).

### Stage 18 — Groundedness evaluator (opt-in)

`CYCLAW_EVAL_LIVE=1 ANTHROPIC_API_KEY=... python3 tests/judge_eval.py`
against a loopback LLM: exit 0 pass / 1 fail / 2 infra; fixtures under
tests/fixtures/groundedness/. In sandbox without keys: verify the gate
refuses cleanly (exit 2) when disabled or missing env.

## 3. Security invariants (26 checks — 24 carried + 2 new)

| # | Invariant | Verification |
|---|-----------|--------------|
| 1 | RAG-first: retrieve is unconditional entry | TestRagFirstEntry |
| 2 | Score gate: route_by_score enforces min_score 0.028 | Low-score query -> user_gate |
| 3 | User gate pauses on confirmed is None | user_gate_router |
| 4 | Availability gate: client.is_available() checked before fallback | Stage 3 q4/q5 (dummy key -> external-unavailable) |
| 5 | Audit convergence: EVERY path reaches audit_logger exactly once — incl. hook-denied, external-unavailable, guardrail-blocked | TestAuditConvergence + live Stage 3/4 |
| 6 | Module isolation: agentic/sync/guardrails/harness/telegram/opentweet never in core | TestCoreModuleIsolation (AST) + invariant-guard |
| 7 | Soul reason gate: empty reason refused | TestSoulReasonGate |
| 8 | Soul injection scan: write-path-only boundary | TestSoulInjectionScanBoundary |
| 9 | Audit query privacy: SHA-256 hashes, no plaintext | TestAuditQueryPrivacy |
| 10 | Sanitizer CWD independence | TestSanitizerCwdIndependence |
| 11 | MCP no-LLM path | TestMcpNoLlmPath |
| 12 | Health embeddings signal is static | TestHealthEmbeddingsSignalIsStatic |
| 13 | Fallback require_user_confirm is unwired | TestFallbackRequireUserConfirmIsUnwired |
| 14 | Soul never forwarded off-box | _external_fallback_node signature (no personality param); harness/prompts.py read-only local consumer |
| 15 | Grok+Claude share _external_fallback_node | graph.py structure |
| 16 | Prompt truncation audit events for both providers | Truncation test |
| 17 | API key redaction parity (Grok + Anthropic + telegram token) | Redaction test |
| 18 | Timeout sanity: 720 < 780 (llm < graph) | Config contract |
| 19 | Guardrails fail open when disabled; guardrail_degraded on output exception | Stage 9 |
| 20 | Guardrail-blocked queries still reach audit_logger | TestGuardrailInputAuditConvergence |
| 21 | Shipped config matches the armed core contract (mode hybrid, providers enabled, agentic write armed, api_key_optional false) | TestShippedCoreConfigContract |
| 22 | Memory fusion never raises; consolidation stays stubbed | Stage 10 |
| 23 | Auth 503-when-disabled; no claims beyond Stages 1-4 | Stage 8 honesty check |
| 24 | OS generators refuse cross-platform; secrets never in argv (keychain -w / CredMan) | Stage 17 |
| 25 | **Pre-action hook fail-closed**: non-zero/non-2 exit or timeout denies; payload carries no query text or soul | Stage 4 |
| 26 | **API-key bypass hygiene**: bypass only from loopback peer with zero forwarding headers; bind guard refuses non-loopback when enabled | Stage 2 |

## 4. Due-diligence invariants (14 classes)

Unchanged set, including TestShippedCoreConfigContract — run
`tests/test_due_diligence*` and report per-class PASS/FAIL with line numbers
for any failure.

## 5. Known residuals on main @ 68595dfa (2026-08-21)

Track as KNOWN — verify they still exist, never report as new findings:

1. `check_jailbreak` / `check_soul_leak` listed in config but NOT enforced by
   the offline heuristic floor (model-assisted only). `guardrail_output` is
   grounding-only.
2. Telegram T0-T3 unit-tested; live operator validation against the real Bot
   API pending per TELEGRAM_DESIGN.md; T4 media partial, POSIX-only.
3. `memory/consolidation.py` deliberate stub; consolidation disabled in v1.
4. terminal.html has NO memory console and NO full auth management UI
   (REST-only surfaces; minimal `.toolbar-auth` affordance only).
5. `.claude/skills/CyClaw-Sandbox/test_terminal_consoles.py` helper drift:
   expects 400 for unknown ops actions, main returns 422 at the schema
   boundary. Helper-side, not product-side.
6. `embeddings_local` health entry static by design; `security.require_env`
   decorative (no boot enforcement); 40 banned_patterns best-effort
   defense-in-depth.
7. PR #415 is CLOSED (was parked) — skip entirely; do not re-propose.
8. Open work (from remaining_work.md, 2026-08-21): httpx2/TestClient
   migration, websockets 15->16, check_soul_leak offline enforcement,
   agentic GitHub-clone-only mode; open issues #962 (judge close-out),
   #964, #1013.
9. The repo's own `.claude/skills/CyClaw-Sandbox/SKILL.md` understates auth
   (claims Stage 3 not landed) — code wins; Stage 3 IS wired.

## 6. Verification ladders (choose per ask)

- **Ladder A — fast local**: pytest subset + /goal+/loop harness commands.
- **Ladder B — in-process**: `.claude/skills/CyClaw-Sandbox/run_full_verification.py`.
- **Ladder C — full CI lifecycle**: `.claude/skills/CyClaw-Sandbox/verify.sh`
  (stage order 1,2,3,5,8,4,7,9,6 — do NOT renumber).
- **Ladder D — surface smoke**: smoke.sh (29 checks).
- **Ladder E — live bombs**: windows-smoke.ps1 / macos-smoke.sh (22 checks),
  platform-matched only.
- **Ladder F — sandbox full clone verification (this document, §2)**: the
  only ladder that generates its own CYCLAW_API_KEY and exercises every
  gated REST surface end-to-end. Default to F for "verify everything".

## 7. Final sign-off format

```
CyClaw Swarm Verification Complete (v2).
Repo state: main @ <sha>, version <pyproject version>
Python: <version>   Ollama realism tier: <0=stubs / 1=mock-ollama-over-HTTP / 2=real Ollama daemon>
CYCLAW_API_KEY: generated in-sandbox (32-byte urlsafe) — used on all gated probes: <YES/NO>
Test suite: <N tests, pass/fail>   invariant-guard: <N/N>
Full functionality status: <PASS/FAIL>
RAG pipeline (5 queries): <PASS/FAIL>
  - Query 1 (vault hit): <PASS/FAIL>
  - Query 2 (vault hit): <PASS/FAIL>
  - Query 3 (offline best-effort / Ollama): <PASS/FAIL>
  - Query 4 (Grok API connection-only): <PASS/FAIL>
  - Query 5 (Claude API connection-only): <PASS/FAIL>
Local Ollama client + LM Studio fallback: <PASS/FAIL>
Pre-action hook gate (#963, allow/deny/fail-closed/timeout): <PASS/FAIL>
Spend tracking (ledger + rates + ticks + metrics split + staleness): <PASS/FAIL/SKIP>
Numbat emission (ndjsonl + fail-soft + skip set): <PASS/FAIL>
Sequence detection (6 rules + import isolation): <PASS/FAIL>
REST API surface (terminal consoles, all 5): <PASS/FAIL>
Auth subsystem (Stages 1-4 + RBAC + CSRF + same-origin): <PASS/FAIL/SKIP>
API-key bypass hardening (_api_key_bypass_allowed): <PASS/FAIL>
Memory subsystem: <PASS/FAIL/SKIP>
Telegram channel (tier noted): <PASS/FAIL/SKIP>
OpenTweet channel: <PASS/FAIL/SKIP>
Unslop bridge (log-only, non-blocking): <PASS/FAIL/SKIP>
Harness console (incl. /api/keys, /api/web, /api/memory, cancel): <PASS/FAIL/SKIP>
MCP manifest drift pin + no-LLM path: <PASS/FAIL>
Groundedness evaluator (judge_eval, tier noted): <PASS/FAIL/SKIP>
macOS glue (platform noted): <PASS/FAIL/SKIP>
Windows glue (platform noted): <PASS/FAIL/SKIP>
Sync schedulers: <PASS/FAIL>
Due-Diligence Invariants: <N/14> passed
Security Invariants: <N/26> passed
Guardrails (input + output): <PASS/FAIL>
Known residuals re-confirmed: <N/9> (list any that changed)
Recommendations: <list or "none">
```
