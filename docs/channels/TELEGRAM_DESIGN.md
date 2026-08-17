# CyClaw Telegram Channel — Design & Phase Plan
<hr>

> quick verification after token and bot created and config flipped

> python -m telegram.cli send --chat-id YOUR_CHAT_ID --text "CyClaw notify OK" --prompt-token

> .. then enter token - it stores in .env which is gitignored here
<hr>
**Status:** T0–T3 **code** is shipped and unit-tested (mocked Bot API / loopback
assumptions), including fail-closed T2 offset persistence and T3 one-shot state
consumption. T4 has a bounded staging path and remains **rollout-partial**. The
channel ships default **disabled**. Live operator validation of T1/T2 has **not**
been recorded in-repo.
**Date:** 2026-08-09 (pre-live persistence hardening)
**Invariant posture:** Out-of-band only (I6). Never imported by `gate.py` / `graph.py` / `mcp_hybrid_server.py`.  
**Companion:** Threat-model amendment in `docs/THREAT_MODEL.md` §5 (seventh amendment).

### Where we are (honest stage map)

| Phase | Code | Live operator validation | Recommended next action |
|---|---|---|---|
| **T0** Skeleton | Done | N/A (self-test only) | None |
| **T1** Notify | Done | **Pending** | First live step: bot + allowlist + one `send` |
| **T2** Chat long-poll | Done | **Pending** (blocked on T1 env) | After T1 works: `mode: chat` + `poll` |
| **T3** Hybrid confirm | Done (default-off) | **Pending** (needs T2 + operator opt-in) | Optional after T2 is boring |
| **T4** Media → fsconnect | **Partial** (POSIX only) | **Pending** | Optional / high risk; needs design answers first |
| **§6 Scheduler** | Not a `telegram/` task | — | Separate package; uses T1 as notify sink |

**Bottom line:** T1 → manual T2 validation can proceed without more feature
code. T3 remains optional and default-off until explicitly enabled after T2 is
boring. Answer the short decision list (§11) before more T4 or scheduler code.

Current `main` ships core `app.mode: hybrid` and both provider enable flags on.
That does not arm Telegram T3: `allow_hybrid_confirm` remains false, each
request still needs one-shot consent, and provider credentials are still
required.

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
│  gate.py → 12-node graph  │  I1–I5 unchanged
│  retrieve → … → audit     │
└───────────────────────────┘
```

### Package layout (current tree)

| Path | Role | Phase |
|---|---|---|
| `telegram/__init__.py` | Public exports + telemetry-kill | T0 |
| `telegram/config.py` | `TelegramConfig` + `load_telegram_config` | T0–T4 |
| `telegram/client.py` | Bot API + `/query` HTTP + chunking + bounded file download | T1–T4 |
| `telegram/runner.py` | `send_notify`, text/media handlers, `poll_*`, commands | T1–T4 |
| `telegram/state.py` | Persistent getUpdates offset + one-shot T3 consent state | T2–T3 |
| `telegram/media.py` | Explicit private-chat attachment staging via fsconnect CLI | T4 (partial) |
| `telegram/ratelimit.py` | Process-local sliding-window limiter | T0 (sqlite optional later) |
| `telegram/cli.py` | `status` / `test` / `send` / `poll` | T0–T2 |
| `telegram/selftest.py` | Operator pre-flight (no network required) | T0 |
| `macos/LaunchAgents/com.cgfixit.cyclaw.telegram-*.plist` | Health notify + poll KeepAlive (disabled templates) | T1–T2 |
| `tests/test_telegram_isolation.py` | I6 regression | T0 |
| `tests/test_telegram_config.py` | Config validation | T0 |
| `tests/test_telegram_client.py` | Bot API / chunking / query client | T1–T4 |
| `tests/test_telegram_runner.py` | Notify, chat, commands, poll, T3 consume | T1–T3 |
| `tests/test_telegram_state.py` | Offset + hybrid session state | T2–T3 |
| `tests/test_telegram_cli.py` | CLI wiring | T0–T2 |
| `tests/test_telegram_media.py` | T4 staging gates + fsconnect CLI path | T4 |

### Config block (`config.yaml`)

```yaml
telegram:
  enabled: false
  mode: "chat"                   # shipped YAML; "notify" is T1 outbound-only. Enable T1 first.
  bot_token_env: "TELEGRAM_BOT_TOKEN"
  allowed_chat_ids: []           # REQUIRED non-empty when enabled
  api_base: "https://api.telegram.org"
  poll_timeout_sec: 30
  max_message_chars: 4000
  allow_hybrid_confirm: false    # T3 master switch; explicit /online on <grok|claude> only
  hybrid_confirm_ttl_sec: 120    # T3 one-request TTL; hard-capped at 300 seconds
  rate_limit:
    max_ops: 20
    window_seconds: 60
  query:
    base_url: "http://127.0.0.1:8787"
    api_key_env: "CYCLAW_API_KEY"
    timeout_sec: 660
  media:
    enabled: false               # T4 default-off
    fsconnect_root: ""           # required when media is enabled; exact writable_roots entry
    max_download_bytes: 10485760 # also bounded by fsconnect.max_write_bytes and 20 MiB Bot API cap
```

**Secrets:** CyClaw never persists the bot token. Manual `send` and `poll` may
read it from the no-echo `--prompt-token` terminal prompt; unattended jobs read
it from the configured environment variable. Never commit tokens or put them in
`config.yaml`.

---

## 3. Invariant mapping

| Invariant | How this design holds it |
|---|---|
| **I1 RAG-first** | Answers only via `POST /query` → graph entry `retrieve` |
| **I2 Topology=policy** | No Telegram-specific graph nodes or LLM routing |
| **I3 Triple-gate** | Normal payload sends `user_confirmed_online: false`; T3 sends true only after an explicit, one-shot, provider-specific consent claim while core gates remain unchanged. Failed state deletion refuses the query and leaves the update unacknowledged for retry. |
| **I4 Audit** | `telegram_inbound` / `telegram_outbound` / `telegram_query` via `utils.logger.audit_log` |
| **I5 Soul** | No soul endpoints exposed through the bot in T0–T4 |
| **I6 Isolation** | Package never imported by core; pytest + invariant-guard list `telegram` |

---

## 4. Security gates (non-negotiable)

1. **Allowlist** — `allowed_chat_ids` empty while `enabled: true` is a **config load error**. Non-allowlisted inbound is refused and audited.
2. **Token never persisted by CyClaw** — unattended jobs use `TELEGRAM_BOT_TOKEN` (or the configured env name); manual `send` and `poll` may use the explicit `--prompt-token` no-echo terminal prompt. Audit stores only a 12-char HMAC-SHA256 pseudonym.
3. **Loopback `/query` only** — `telegram.query.base_url` host must be `127.0.0.1` / `localhost` / `::1`.
4. **No silent hybrid** — only exact `/online on <grok|claude>` can arm consent; a bare `/online on` is refused rather than selecting a provider. Failed session deletion is audited and refuses the query.
5. **Rate limit** — process-local sliding window on outbound + inbound
   (`telegram/ratelimit.py`). Multi-process sqlite limiter remains deferred
   (YAGNI) until more than one poller is real.
6. **Threat model** — Telegram cloud sees message plaintext. Branding must say *local inference*, not *E2E private channel*.
7. **T4 media gates** — only an allowlisted private chat with `/save --confirm <reason>` can stage an attachment. The bridge checks configured fsconnect write, strict-root, scan, injection-block, and persistent write-rate-limit gates before it contacts Telegram's file service; its root must be absolute, explicit, outside the repo/corpus, and not overlap a read root. The unresolved exception is host capability: fsconnect's Windows refusal occurs later, so T4 must remain off there until Telegram rejects the request before download. It uses the fsconnect CLI, never an in-process import, and does not auto-index.

---

## 5. Phase plan

### T0 — Skeleton — DONE

**Done means:**

- [x] `telegram/` package, default `enabled: false`
- [x] Config loader with hard validation
- [x] CLI: `status`, `test`, `send` (+ `--dry-run`), `poll`
- [x] Process-local rate limiter (`telegram/ratelimit.py`) on outbound + inbound
- [x] Isolation tests + config/client/runner/cli tests
- [x] Design doc + threat-model amendment
- [x] `telegram` listed in invariant-guard `OUT_OF_BAND_PKGS` and `pyproject` package lists
- [x] CI `--cov=telegram` on both coverage lanes (dep-guard D10)

**Do not** enable in production `config.yaml` until T1 operator checklist is green.

---

### T1 — Notify-only productionization

**Goal:** Scheduler / health scripts can push a message to your phone.

**Implementation status:** Code and mocked verification are complete. The
operator checklist below is intentionally unverified in this repository session:
it needs an enabled bot, a real allowlisted chat id, and an operator-supplied
token.

**Operator checklist**

1. Create a bot with BotFather. For a manual smoke, pass `--prompt-token`; the
   value stays only in that CLI process. For unattended launchd, store it in
   the macOS Keychain (`macos/cyclaw-keychain-set.sh
   com.cgfixit.cyclaw.telegram-bot-token`) — `python -m telegram.cli
   health-plist` / `poll-plist` generate a plist whose `ProgramArguments`
   run it through `macos/cyclaw-keychain-env.sh` first, which resolves the
   token from the Keychain and exports `TELEGRAM_BOT_TOKEN` at process-start
   time. No token value is ever written into the plist. An operator-only
   `0600` secret file remains a valid alternative if you prefer not to use
   the Keychain.
2. Send `/help` to the bot once; resolve your chat id from this bot's own
   `getUpdates` response. Do not disclose it to a third-party bot.
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
   # Manual prompt path:
   python -m telegram.cli send --chat-id YOUR_CHAT_ID --text "CyClaw notify OK" --prompt-token

   # After unattended env/secret-store injection is configured:
   python -m telegram.cli test
   ```

**Follow-up disposition**

| Item | File(s) | Status / instructions |
|---|---|---|
| Persistent rate limiter | `telegram/ratelimit.py` | **Deferred (YAGNI).** Process-local sliding window is enough for a single poller. Upgrade to dedicated sqlite under `data/` only if multi-process pollers are needed. Keys: `tg:outbound`, `tg:inbound:{chat_id}`. |
| Launchd generator | `python -m telegram.cli health-plist` | **Shipped.** Generates the same curl-`/health`-then-notify-on-fail plist from real resolved paths (no placeholders), token injected via the Keychain wrapper. Never calls `launchctl load` itself. The static `macos/LaunchAgents/com.cgfixit.cyclaw.telegram-health.plist` template remains as a hand-editable reference/fallback. |
| Message chunking | `telegram/client.py` | **Shipped.** `chunk_text` splits on paragraph/line before hard cut; `send_message` sends sequential chunks. |
| `send` dry-run | `telegram/cli.py` | **Shipped in T0** (`--dry-run` validates allowlist + prints preview, no HTTP). |

**Tests**

- Mock `httpx` for `send_message` success/fail — **shipped**.
- Allowlist refusal unit test — **shipped**.

**Exit criteria:** nightly or on-demand notify works on M5 Mac without `mode: chat`.

---

### T2 — Two-way chat (long-poll)

**Goal:** Text CyClaw from Telegram; get a local RAG answer back.

**Implementation status:** Code and mocked verification are complete. Live
operator validation remains pending for the same token/chat/server prerequisites
as T1.

**Operator checklist**

1. CyClaw server up on loopback (`python -m gate` / install scripts).
2. Ollama serving `qwen3.8:27b-mlx` (or configured local model).
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
6. Use a private 1:1 bot chat for the first rollout. The current allowlist is
   chat-scoped, so allowlisting a group grants query access to every member.
7. Run exactly one poller per bot token. Stop a manual `poll` process before
   loading the KeepAlive LaunchAgent; offset and rate-limit state do not
   coordinate concurrent pollers.
8. On the first run with no saved offset, expect queued updates to be handled.
   Use a fresh/controlled DM and send only the documented `/help` command;
   `/start` is not a CyClaw command.

**Follow-up disposition**

| Item | File(s) | Status / instructions |
|---|---|---|
| Answer field normalization | `telegram/runner.py::_extract_answer` | **Shipped.** Prefers `QueryResponse.answer` / `model_used`; unit test with full response-shaped fixture. |
| Streaming / typing indicator | `telegram/client.py` | **Deferred (optional).** `sendChatAction` typing while `/query` runs. Do not change graph timeouts. |
| Concurrent updates | `telegram/runner.py` | **Shipped sequential.** One in-flight `/query` per poll batch; parallelism out of scope until a worker queue exists **outside** gate. |
| Offset persistence | `telegram/state.py`, `telegram/runner.py` | **Shipped, fail-closed.** Atomic `data/telegram/offset.json`; `poll_forever` loads/saves and respects no-ack-on-`TelegramRuntimeError`. A save failure is audited and retried in-process while polling stays paused, so launchd cannot repeatedly replay an already-answered update. A process crash during that retry can still resume from the old durable offset under Telegram's at-least-once delivery model. |
| Command namespace | `telegram/runner.py` | **Shipped.** `/help`, `/status` (loopback `/health` only), `/id`, and T3 `/online`; `/save` only explains the T4 confirmation form when sent without an attachment. |
| Injection double-check | optional | `/query` already runs the sanitizer. Do not reimplement. |
| launchd generator | `python -m telegram.cli poll-plist` | **Shipped.** Generates the KeepAlive + ThrottleInterval plist from real resolved paths, token injected via the Keychain wrapper (optionally chains a second wrapper layer for `CYCLAW_API_KEY` via `--api-key-service`). The static `macos/LaunchAgents/com.cgfixit.cyclaw.telegram-poll.plist` template remains as a hand-editable reference/fallback. |

**Tests**

- `handle_inbound_text` with mocks for `/query` + `sendMessage` — **shipped** (+ commands).
- Poll loop with `max_iterations=1` and mocked `get_updates` — **shipped** (+ offset file).
- Isolation test remains green.

**Exit criteria:** allowlisted phone chat rounds trip offline RAG answers; non-allowlisted chat is silent/refused; audit.jsonl shows inbound+query+outbound events.

---

### T3 — Hybrid confirm UX (optional) — IMPLEMENTED, default-off

**Goal:** From Telegram, explicitly allow a single Grok/Claude escalation without editing config.

**Design constraints**

- Must not weaken I3: still need `mode=hybrid` AND `provider.enabled` AND user confirm.
- Telegram consent is the **user_confirmed_online** signal for **one** request
  only (default TTL 120s; hard cap 300s).
- `allow_hybrid_confirm: true` is a master switch and defaults false.
- Current `main` already permits core hybrid routing and enables both provider
  blocks. Telegram T3 still needs the master switch, one-shot command, provider
  credentials, and all core runtime gates.

**Implemented behavior**

1. Only exact `/online on <grok|claude>` can grant consent. `/online on` and
   any ambiguous form return usage instead of silently choosing a provider.
2. **Private chat only** (issue #792). Groups/supergroups share one `chat_id`
   across senders; T3 grant and claim both refuse non-`private` types so one
   member cannot arm external fallback for another. Multi-user ACLs remain a
   non-goal — use a 1:1 DM with the bot.
3. The grant stores only `confirm_until` and provider in
   `data/telegram/session_{chat_id}.json`; chat ids are canonical signed 64-bit
   integers and per-session file locking plus atomic replacement protects the
   one-shot claim across poller processes.
4. On the ordinary path, the next non-command **private** text claims and
   deletes that state **before** the `/query` call. It can set
   `user_confirmed_online: true` and the selected `online_provider` only when
   unexpired and the master switch remains enabled. Once deletion succeeds, a
   failed query or Telegram reply does not restore the claim; there is no
   sticky “always online”.
5. Grant, refuse, and consume are audited without message text or secrets.
6. CyClaw's existing `mode=hybrid` and enabled-provider checks remain the final
   authority. The Telegram bridge cannot make an offline configuration use an
   external provider.

**Pre-live state blocker closed (2026-08-09):** `claim_hybrid_confirm` now
propagates deletion failure as a retryable `TelegramRuntimeError` without
returning a provider or issuing `/query`. The runner audits the state error and
leaves the Telegram update unacknowledged so deletion can be retried.

**Delivery/replay semantics:** Telegram long-polling is at-least-once. A
retryable `/query` or reply failure leaves an update eligible for redelivery,
and a host restart after an offset-save failure replays from the last durable
offset. T2 may therefore repeat a query. A successfully deleted T3 claim stays
consumed and a replay cannot repeat the online escalation.

**Verification:** state, runner, client, and isolation tests cover expiry,
single-use consumption, deletion failure, disabled/ambiguous command refusals,
exact query payloads, and audit redaction. Live validation is deferred until
T1/T2 has a configured bot.

---

### T4 — Media → fsconnect (optional, high risk) — PARTIAL

**Goal:** Stage a Telegram photo/document as an opaque artifact for operator
review. T4 does not directly create an indexable corpus file.

**Platform boundary:** T4 writes are supported only on POSIX/macOS/Linux because
fsconnect hard-refuses Windows writes. The current Telegram preflight discovers
that refusal only after Bot API file resolution/download; a pre-download
platform gate is required before T4 can be called complete.

**Safe subset implemented**

- Media is disabled by default. It accepts a document or the largest Telegram
  photo only from an allowlisted **private** chat with caption
  `/save --confirm <reason>`.
- Before resolving a Telegram file, the bridge requires `fsconnect.enabled`,
  `writes_enabled`, `strict_roots`, `scan_content`, and
  `block_on_injection_flags` all to be exactly true, plus
  `fsconnect.write_rate_limit.enabled: true`. Its configured root must be an
  absolute, explicit `fsconnect.writable_roots` entry outside the repository
  and corpus and may not overlap an fsconnect read root.
- It bounds the declared size and streamed bytes to the minimum of
  `telegram.media.max_download_bytes`, `fsconnect.max_write_bytes`, and the
  Telegram cloud Bot API 20 MiB download limit. Bot API `getFile` is followed
  by a token-bearing HTTPS download URL that is never logged.
- The bridge does not trust or retain the original filename, MIME type, caption,
  or Bot API file path. It derives an opaque deterministic target and invokes
  `python -m agentic.fsconnect.cli ... write --confirm` using fixed argv and
  stdin. There is no direct `agentic.fsconnect` import in `telegram/`.
- It stores documents as `.bin` and photos as `.jpg`. Neither extension is in
  fsconnect's default index allowlist. It does **not** reindex, write
  `data/corpus`, or expose a voice/STT path.
- Any later corpus promotion must verify the real type, safely extract and scan
  content, copy/rename it to an approved extension, and invoke the existing
  index workflow explicitly. Photos would require separately approved OCR;
  no OCR or automatic ingestion is planned.

**Remaining work before T4 may be called complete**

- Reject unsupported hosts before `getFile` or download, with a regression test.
- Live, operator-controlled Bot API and fsconnect CLI validation on macOS/Linux
  with a disposable writable root.
- A separately reviewed, explicit reindex/ingest workflow if staged attachments
  should become RAG corpus content, including binary extraction/rescan.
  Automatic corpus ingestion remains out of scope.
- Replay-aware staging policy covering both acknowledgement failure after an
  applied write and host restart after offset-persistence failure. Never use a
  blind overwrite as replay recovery.

**Verification:** Bot API calls are mocked in unit tests; one test executes the
real local fsconnect CLI against a temporary dedicated root and confirms that
the confirmation text is absent from the audit file. No live Telegram token or
operator filesystem is needed for this coverage.

**Planner boundary:** This planner currently ends at T4. No T5 or T6 task is
defined in the repository, so no speculative cross-channel UI or harness
routing is introduced here.

**Out of scope for v1:** voice notes → STT, automatic soul updates, group chats with multiple untrusted members.

---

## 6. Background scheduler (related, separate package)

**Deferred under the current feature freeze.** Reuse the shipped LaunchAgent
templates; do not create `scheduler/` without an explicit operator need.

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
- Mirroring terminal or harness UI activity into Telegram. Telegram text uses
  the ordinary `/query` response path and forwards its answer text (chunked
  only when the Bot API limit requires it); terminal and harness activity stay
  in their respective web interfaces.
- Autonomous agent loops driven by Telegram messages
- Storing Telegram message history inside the RAG corpus by default

---

## 8. Acceptance checklist

### Code / CI (current coverage plus pre-live gaps)

- [x] `python -m telegram.cli test` passes with default config (disabled)
- [x] Full telegram unit suite green under `GROK_API_KEY=dummy` (isolation,
      config, state, media, runner, client, cli — one skip is fine if env-gated)
- [x] `telegram` in invariant-guard `OUT_OF_BAND_PKGS`; no core imports of `telegram`
- [x] Threat-model seventh amendment present
- [x] Default `enabled: false`, empty allowlist legal only while disabled
- [x] T3: session-file deletion failure refuses the request and cannot reuse consent
- [x] T2 unattended: offset-save failure is explicit/audited; no-repoll retry and restart recovery are tested

### Live operator (not done in-repo — this is the real gate)

- [ ] BotFather bot + fresh token supplied by `--prompt-token` for manual smoke or env secret store for unattended use (never in git/plist)
- [ ] Own chat id discovered and in `allowed_chat_ids`
- [ ] T1: `python -m telegram.cli send …` delivers to phone
- [ ] T2: with server + Ollama up, `mode: chat` + `poll` answers offline RAG
- [ ] T2 uses one private DM and exactly one poller; manual poll is stopped before launchd
- [ ] Non-allowlisted chat is refused; audit shows hashed/inbound/outbound events
- [ ] T3 (optional): only after T2, provider credentials, and `allow_hybrid_confirm`
- [ ] T4 (optional): only on macOS/Linux after §11 answers and a disposable fsconnect root

---

## 9. Rollout on MacBook Pro M5 (operator notes)

1. `bash ./macos/install-cyclaw.sh` if not already.
2. Ollama: `qwen3.8:27b-mlx`, `num_ctx` ≥ budget in `config.yaml` comments.
3. Keep embeddings on CPU (`EMBED_DEVICE`) for deterministic retrieval.
4. Run CyClaw loopback server.
5. Enable Telegram **T1 first**; live with notify for a few days before `mode: chat`.
6. Poll process: separate from uvicorn so a stuck `/query` does not kill the web UI (or vice versa), but run exactly one poller per bot token.
7. Prefer `python -m telegram.cli health-plist` / `poll-plist` (or the static
   templates under `macos/LaunchAgents/`) only after manual T1/T2 works and
   the offset path is writable. The generators inject the token via the
   Keychain wrapper, never as a plaintext plist value; if hand-editing a
   template instead, keep the token out of it the same way.

---

## 10. Changelog for this design

| Date | Change |
|---|---|
| 2026-08-03 | Initial design + skeleton package (T0) |
| 2026-08-03 | T1/T2 residual: offset file, commands, chunking, launchd templates |
| 2026-08-04 | T3 explicit one-shot provider consent; T4 default-off, private-chat fsconnect CLI staging; T4 intentionally remains partial (no auto-index or live operator validation). |
| 2026-08-04 | Stage review: code is at T0–T3 + partial T4; next work is operator T1→T2 live validation and §11 decisions — not more T2/T3 skeleton. |
| 2026-08-05 | PRs #799–#801 made T3 private-chat-only, converged terminal T4 refusal audits, and recorded rollout-partial stage truth/operator decisions. |
| 2026-08-07 | Fresh-main parity review: corrected current hybrid posture and T2 config example; added T3 fail-closed deletion blocker, T2 single-poller/replay limits, and T4 POSIX/opaque-staging constraints. |
| 2026-08-09 | Closed the T2 offset-save and T3 session-delete blockers with fail-closed retry handling, a typed T3 error, audit events, and restart/retry regressions. Live Bot API validation remains pending. |

---

## 11. Open operator decisions (answer before more code)

These are the questions that block useful next engineering. Prefer short
answers; leave blank only if the feature stays off forever.

### A. Rollout priority (pick one primary path)

1. **T1 live notify only** — bot token, chat id, one successful `send`, optional
   health-failure notify. No chat mode yet.
2. **T1 then T2 chat** — after notify works, enable `mode: chat` + long-poll and
   verify offline RAG from the phone.
3. **Skip live Telegram for now** — leave channel disabled; work elsewhere
   (scheduler design, portfolio docs, other CyClaw work).

### B. T1 prerequisites (fill when choosing A.1 or A.2)

| Item | Your answer |
|---|---|
| Bot exists via BotFather? | yes / no |
| How will the bot token be supplied? (never commit or inline in plist) | `--prompt-token` for manual smoke / Keychain runtime wrapper / operator-only `0600` secret file |
| Chat id of the **only** trusted phone? | (numeric; bootstrap from this bot's own `getUpdates`; `/id` only confirms after allowlisting) |
| Host for first enablement? | Windows box / MacBook M5 / both |
| Notify failure sink desired? | manual only / health launchd / nightly sync wrapper later |

### C. T2 chat posture

| Item | Your answer |
|---|---|
| Local Ollama model ready for long answers? | yes / no / which model |
| CyClaw loopback server always-on while polling? | yes / only when I start it |
| First chat scope? | private DM (recommended) / allowlisted group (trust every member) |
| Typing indicator (`sendChatAction`)? | defer (default) / want it soon |
| Multi-process pollers? | no (required by current design) |
| Unattended launchd before manual T2 is stable? | no (recommended) / yes after validation |

### D. T3 hybrid from Telegram (optional)

| Item | Your answer |
|---|---|
| Do you want phone-side one-shot Grok/Claude escalate? | no for now / yes later / yes soon |
| Current main ships `app.mode: hybrid` and provider enables on; keep that posture for testing? | yes with credentials / change before test |
| OK that downstream failure still **consumes** a successfully deleted one-shot consent? | yes (current) / revisit design |

### E. T4 media (high risk — default stay off)

| Item | Your answer |
|---|---|
| Need photo/document → disk at all? | no / yes staging only / yes then into corpus |
| Supported host and disposable absolute fsconnect writable root? | macOS/Linux only; path outside repo + corpus |
| Explicit type validation/extraction before corpus promotion? | required / leave staging-only |
| Auto-reindex after stage? | **never** (current) / later explicit CLI only |
| Replay policy after applied write or lost offset? | durable receipt + re-ack (recommended) / design later / leave T4 off |

### F. Scheduler (§6) coupling

| Item | Your answer |
|---|---|
| Want T1 wired as failure notify for sync/health before any `scheduler/` package? | yes shell/launchd only / wait for scheduler package / not needed |

### Recommended default if you only answer one thing

Ship **A.1 → A.2** in a private DM on the host you use daily: enable notify,
prove one message, then run one manual chat poller. Load the KeepAlive poller
only after the manual path is stable and the offset path is writable. Leave
T3/T4/media master switches false until explicitly needed; T4 is macOS/Linux only.
