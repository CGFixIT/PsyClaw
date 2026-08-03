# CyClaw Telegram Channel — Design & Phase Plan

**Status:** Skeleton shipped (`telegram/` package, default **disabled**).  
**Date:** 2026-08-03  
**Invariant posture:** Out-of-band only (I6). Never imported by `gate.py` / `graph.py` / `mcp_hybrid_server.py`.  
**Companion:** Threat-model amendment in `docs/THREAT_MODEL.md` §5 (seventh amendment).

---

## 1. Purpose

Give a single trusted operator a **phone-reachable remote** for the existing
local RAG agent:

| Mode | Behaviour |
|---|---|
| `notify` (default) | Outbound only — scheduler / ops → Bot API `sendMessage` |
| `chat` | Two-way — inbound text → **HTTP `POST /query`** → reply |

Telegram is a **channel adapter**, not a second brain. It never:

- imports or invokes LangGraph directly
- sets `user_confirmed_online=true` automatically
- mutates soul / registry / real-repo without the existing human gates
- binds a public webhook listener next to the CyClaw loopback server (phase-1 uses long-poll)

---

## 2. Architecture

```
Phone (Telegram cloud)
        │
        │  long-poll getUpdates  /  sendMessage
        ▼
┌───────────────────────────┐
│  telegram/  (out-of-band) │  python -m telegram.cli …
│  config · client · runner │
└─────────────┬─────────────┘
              │  httpx → http://127.0.0.1:8787/query
              ▼
┌───────────────────────────┐
│  gate.py → 10-node graph  │  I1–I5 unchanged
│  retrieve → … → audit     │
└───────────────────────────┘
```

### Package layout (skeleton)

| Path | Role | Phase |
|---|---|---|
| `telegram/__init__.py` | Public exports + telemetry-kill | T0 |
| `telegram/config.py` | `TelegramConfig` + `load_telegram_config` | T0 |
| `telegram/client.py` | Bot API + `/query` HTTP | T1–T2 |
| `telegram/runner.py` | `send_notify`, `handle_inbound_text`, `poll_*` | T1–T2 |
| `telegram/cli.py` | `status` / `test` / `send` / `poll` | T0–T2 |
| `telegram/selftest.py` | Operator pre-flight (no network required) | T0 |
| `tests/test_telegram_isolation.py` | I6 regression | T0 |
| `tests/test_telegram_config.py` | Config validation | T0 |

### Config block (`config.yaml`)

```yaml
telegram:
  enabled: false
  mode: "notify"                 # notify | chat
  bot_token_env: "TELEGRAM_BOT_TOKEN"
  allowed_chat_ids: []           # REQUIRED non-empty when enabled
  api_base: "https://api.telegram.org"
  poll_timeout_sec: 30
  max_message_chars: 4000
  allow_hybrid_confirm: false    # T3 reserved — not wired
  rate_limit:
    max_ops: 20
    window_seconds: 60
  query:
    base_url: "http://127.0.0.1:8787"
    api_key_env: "CYCLAW_API_KEY"
    timeout_sec: 660
```

**Secrets:** bot token and optional CyClaw API key live **only** in environment
variables. Never commit tokens. Never put tokens in `config.yaml`.

---

## 3. Invariant mapping

| Invariant | How this design holds it |
|---|---|
| **I1 RAG-first** | Answers only via `POST /query` → graph entry `retrieve` |
| **I2 Topology=policy** | No Telegram-specific graph nodes or LLM routing |
| **I3 Triple-gate** | Payload always sends `user_confirmed_online: false`; hybrid stays core-gated |
| **I4 Audit** | `telegram_inbound` / `telegram_outbound` / `telegram_query` via `utils.logger.audit_log` |
| **I5 Soul** | No soul endpoints exposed through the bot in T0–T2 |
| **I6 Isolation** | Package never imported by core; pytest + invariant-guard list `telegram` |

---

## 4. Security gates (non-negotiable)

1. **Allowlist** — `allowed_chat_ids` empty while `enabled: true` is a **config load error**. Non-allowlisted inbound is refused and audited.
2. **Token in env** — `TELEGRAM_BOT_TOKEN` (or configured name). Audit stores only a 12-char SHA-256 fingerprint.
3. **Loopback `/query` only** — `telegram.query.base_url` host must be `127.0.0.1` / `localhost` / `::1`.
4. **No silent hybrid** — never auto-confirm online.
5. **Rate limit knobs** — present in config (enforcement loop is T2 hardening; see below).
6. **Threat model** — Telegram cloud sees message plaintext. Branding must say *local inference*, not *E2E private channel*.

---

## 5. Phase plan (detailed instructions for finishing the skeleton)

### T0 — Skeleton (THIS PR) — DONE when merged

**Done means:**

- [x] `telegram/` package, default `enabled: false`
- [x] Config loader with hard validation
- [x] CLI: `status`, `test`, `send`, `poll`
- [x] Isolation tests + config tests
- [x] Design doc + threat-model amendment
- [x] `telegram` listed in invariant-guard `OUT_OF_BAND_PKGS` and `pyproject` package lists

**Do not** enable in production `config.yaml` until T1 operator checklist is green.

---

### T1 — Notify-only productionization

**Goal:** Scheduler / health scripts can push a message to your phone.

**Operator checklist**

1. Create a bot with BotFather; put token in `TELEGRAM_BOT_TOKEN`.
2. Message the bot once; resolve your chat id (`@userinfobot` or `getUpdates` once).
3. Set in `config.yaml`:
   ```yaml
   telegram:
     enabled: true
     mode: notify
     allowed_chat_ids: ["YOUR_CHAT_ID"]
   ```
4. Ensure CyClaw is **not** required for pure notify (send does not call `/query`).
5. Run:
   ```bash
   python -m telegram.cli test
   python -m telegram.cli send --chat-id YOUR_CHAT_ID --text "CyClaw notify OK"

   ```

**Code still to harden (T1 follow-ups)**

| Item | File(s) | Instructions |
|---|---|---|
| In-process rate limiter | `telegram/runner.py` or new `telegram/ratelimit.py` | Reuse `utils.ratelimit.RateLimiter` with a dedicated sqlite path under `data/` (do **not** share the gateway limiter DB). Key by `tg:outbound` and `tg:inbound:{chat_id}`. |
| Launchd template | `macos/LaunchAgents/com.cgfixit.cyclaw.telegram-health.plist` | Example job: curl `/health` → on fail `python -m telegram.cli send …`. Disabled by default; document in this file. |
| Message chunking | `telegram/client.py` | Telegram hard limit 4096; already truncate via `max_message_chars`. Prefer split-on-paragraph for long answers in T2. |
| `send` dry-run | `telegram/cli.py` | Add `--dry-run` that validates allowlist + prints payload without HTTP. |

**Tests to add**

- Mock `httpx` for `send_message` success/fail.
- Allowlist refusal unit test (already partially covered via config).

**Exit criteria:** nightly or on-demand notify works on M5 Mac without `mode: chat`.

---

### T2 — Two-way chat (long-poll)

**Goal:** Text CyClaw from Telegram; get a local RAG answer back.

**Operator checklist**

1. CyClaw server up on loopback (`python -m gate` / install scripts).
2. Ollama serving `qwen3.6:27b` (or configured local model).
3. Config:
   ```yaml
   telegram:
     enabled: true
     mode: chat
     allowed_chat_ids: ["YOUR_CHAT_ID"]
   query:
     # inherits defaults — keep base_url loopback
   ```
4. `export CYCLAW_API_KEY=…` if soul/ops require it (and if `/query` is keyed in your deploy).
5. Run in a dedicated terminal or launchd:
   ```bash
   python -m telegram.cli poll
   ```

**Code still to build / harden**

| Item | File(s) | Instructions |
|---|---|---|
| Answer field normalization | `telegram/runner.py::_extract_answer` | Align with live `schemas/api.py` response model fields; add a unit test with a fixture of a real `/query` JSON body from `tests/` or a captured sample. |
| Streaming / typing indicator | `telegram/client.py` | Optional `sendChatAction` typing while `/query` runs (long 27B calls). Do not change graph timeouts. |
| Concurrent updates | `telegram/runner.py` | Process updates **sequentially** in v1 (one in-flight `/query`). Document that parallelism is out of scope until a worker queue exists **outside** gate. |
| Offset persistence | new `telegram/state.py` | Persist `getUpdates` offset under `data/telegram/offset.json` (atomic write) so restarts do not re-handle messages. |
| Command namespace | `telegram/runner.py` | Reserve `/help`, `/status` (local health only via loopback), `/id` (echo chat id). Do **not** implement `/online` until T3. |
| Injection double-check | optional | `/query` already runs the 40-pattern sanitizer. Do not reimplement; do not strip content in ways that desync from gate. |
| launchd plist | `macos/…` | Keep-alive poller with `KeepAlive` + `ThrottleInterval`; log to `~/Library/Logs/CyClaw/telegram-poll.log`. |

**Tests to add**

- `handle_inbound_text` with httpx mock for `/query` + `sendMessage`.
- Poll loop with `max_iterations=1` and mocked `get_updates`.
- Isolation test remains green.

**Exit criteria:** allowlisted phone chat rounds trip offline RAG answers; non-allowlisted chat is silent/refused; audit.jsonl shows inbound+query+outbound events.

---

### T3 — Hybrid confirm UX (optional)

**Goal:** From Telegram, explicitly allow a single Grok/Claude escalation without editing config.

**Design constraints**

- Must not weaken I3: still need `mode=hybrid` AND `provider.enabled` AND user confirm.
- Telegram confirm is the **user_confirmed_online** signal for **one** request only (TTL, e.g. 120s).
- `allow_hybrid_confirm: true` is a master switch; default false forever until implemented.

**Implementation sketch**

1. Inbound `/online on` or inline button → store `confirm_until` timestamp in `data/telegram/session_{chat_id}.json`.
2. Next `post_query` may set `user_confirmed_online: true` only if `now < confirm_until` AND config allows.
3. Audit every confirm grant/consume.
4. Never persist “always online”.

**Do not start T3 until T2 is boring.**

---

### T4 — Media → fsconnect (optional, high risk)

**Goal:** Photo/document from Telegram staged into an fsconnect writable root, optional reindex.

**Rules**

- Downloads only to fsconnect `writable_roots` with existing four-gate write path.
- Never auto-write into `data/corpus` without operator confirm.
- Scan content with existing injection scanner; prefer `block_on_injection_flags: true` before enabling.
- Size caps align with `fsconnect.max_write_bytes`.

**Out of scope for v1:** voice notes → STT, automatic soul updates, group chats with multiple untrusted members.

---

## 6. Background scheduler (related, separate package)

Telegram T1 is the **notify sink** for jobs. The scheduler itself must remain out-of-band (same shape as `sync.scheduler` / launchd), **not** asyncio tasks inside `gate.py`.

### Recommended job table

| Job | Invokes | Telegram |
|---|---|---|
| Nightly corpus pull | `python -m sync.cli sync` | On failure → `telegram.cli send` |
| Health probe | `curl -sf http://127.0.0.1:8787/health` | On failure → send |
| Index doctor | future / skill | Optional summary notify |

### Future `scheduler/` package (not in this PR)

```
scheduler/
  config.py      # jobs: from config.yaml scheduler: block
  cli.py         # run-once --job <name>
  runner.py      # argv-list only to other CLIs
  jobs/
    health.py
    sync_pull.py
```

**Rules for any scheduler PR**

1. No import of `graph` / `gate`.
2. Jobs are argv subprocesses or httpx to loopback.
3. Never auto-run `real-repo-run-publish` or arm `EXECUTION_ENABLED`.
4. Prefer macOS launchd plists under `macos/LaunchAgents/` over in-process APScheduler for lid-sleep honesty.

Wire each job’s `on_failure_notify: true` to `python -m telegram.cli send` once T1 is live.

---

## 7. Explicit non-goals

- Public multi-user bot / multi-tenant isolation
- Webhook server bound on `0.0.0.0` as the default (if webhook is ever added: reverse-proxy + secret path + still allowlist)
- Replacing `terminal.html` or harness console
- Autonomous agent loops driven by Telegram messages
- Storing Telegram message history inside the RAG corpus by default

---

## 8. Acceptance checklist for reviewers

- [ ] `python -m telegram.cli test` passes with default config (disabled)
- [ ] `pytest tests/test_telegram_isolation.py tests/test_telegram_config.py` green
- [ ] `python .claude/skills/invariant-guard/check_invariants.py` still green (I6 includes `telegram`)
- [ ] No new imports of `telegram` from `gate.py` / `graph.py` / `mcp_hybrid_server.py` / `gate_ops.py`
- [ ] Threat-model amendment present
- [ ] Default `enabled: false` and empty allowlist safe when disabled
- [ ] Draft PR — do not merge until human has read §5 T1/T2 and threat model

---

## 9. Rollout on MacBook Pro M5 (operator notes)

1. `bash ./macos/install-cyclaw.sh` if not already.
2. Ollama: `qwen3.6:27b`, `num_ctx` ≥ budget in `config.yaml` comments.
3. Keep embeddings on CPU (`EMBED_DEVICE`) for deterministic retrieval.
4. Run CyClaw loopback server.
5. Enable Telegram T1 first; live with notify for a few days before `mode: chat`.
6. Poll process: separate from uvicorn so a stuck `/query` does not kill the web UI (or vice versa).

---

## 10. Changelog for this design

| Date | Change |
|---|---|
| 2026-08-03 | Initial design + skeleton package (T0) |
