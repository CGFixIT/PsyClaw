# cyclaw-advisor review — Windows live-API bomb and Darwin twin

**Date:** 2026-08-21
**Persona:** Legal (in-house privacy/compliance assistant)
**Scope:** `.claude/skills/CyClaw-Sandbox/windows-smoke.ps1` (existing 22-check live HTTP bomb) and the Darwin twin `macos-smoke.sh`
**Status:** Advisory only — not a substitute for licensed counsel. No outside-counsel escalation.

---

## 1. Direct assessment

`windows-smoke.ps1` is a **loopback-only operator/CI probe**. It does not introduce a new personal-data store, does not confirm online fallback (I3 stays closed), does not mutate `soul.md` (I5), and does not reach a git write. Residual risk is **operator-home session residue** if the bomb is pointed at a live harness whose `CYCLAW_HOME` is the real `~/.CyClaw` / `%USERPROFILE%\.CyClaw`. The Darwin twin copies those controls; it does not widen the processing.

Verdict: **acceptable for CI and operator smoke**, with the Medium residue finding below documented and mitigated in CI by isolated `CYCLAW_HOME`.

---

## 2. Relevant citations

| Control | Where | Why it matters here |
|---|---|---|
| GDPR Art. 5(1)(c) data minimisation | queries are synthetic fixture strings, not operator PII | Smoke is not a DSR corpus |
| GDPR Art. 5(1)(f) / Art. 32 | bind `127.0.0.1` only (`config.yaml` `api.host`, `harness.host`) | No LAN/WAN listener; DevSkim DS162092/DS137138 waived for that reason |
| GDPR Art. 5(2) accountability | `utils/logger.py` `hash_query` SHA-256; `audit_log` pops `query` before write | Audit stream stores hashes, never raw query text |
| GDPR Art. 25 data protection by design | I3 triple-gate; `user_confirmed_online` is never `true` in either smoke | No Grok/Claude sub-processor call from this path |
| Invariant I3 | `graph.py` `user_gate_router`; `config.yaml` `policy.fallback.send_local_context_to_grok`/`_claude`: `false` | Offline-best-effort / local only |
| Invariant I5 | `utils/personality.py`; harness `POST /api/soul` comment | Toggle is harness-local; `soul.md` untouched |
| Invariant I6 | `/api/agent/run` and `.../decision` are **auth-gate-only** | A real call clones a repo and can git-write; smoke must not |
| CCPA §1798.140(v) personal information | no consumer identifiers, no email/IP collection in this path | CCPA does not trigger on fixture HTTP |
| `policy.privacy` | `config.yaml` `redact_emails: true`, `redact_ips: true`, `redact_secrets_like` | Fail-path JSON printed by the scripts is still server-redacted on the audit side; scripts themselves must not echo `CYCLAW_API_KEY` |
| Auth Stage 3 gap | `docs/AUTHENTICATION_DESIGN.md`; `auth.enabled: false` | `/query` is still unauthenticated. Smoke posts queries without a user credential — that is current product posture, not a smoke regression. Note it; do not pretend accounts already gate `/query`. |

---

## 3. Data map of the 22 checks

Neither script starts servers. Both talk to whatever is already bound on loopback `:8787` / `:8790`.

| # | Surface | Data in | Persists? | Off-box? |
|---|---|---|---|---|
| 1 | `GET /health` | none | no | no |
| 2 | `POST /query` `"What is RRF fusion in CyClaw?"` | plaintext query in RAM | audit: `query_hash` only | no (no `user_confirmed_online`) |
| 3 | `POST /query` declined-online | same + explicit `user_confirmed_online: false` | hash only | no — I3 deny path |
| 4 | `POST /query` injection string | banned-pattern probe | hash + guardrail stream (`logs/guardrails.jsonl`, hashes) | no; expect HTTP 400 |
| 5 | `GET /soul` 401 then Bearer | `CYCLAW_API_KEY` in `Authorization` | soul.md not written | no |
| 6 | `GET /static/terminal.html` | none | no | no |
| 7 | `GET /` harness + CSRF extract | CSRF from `<meta name="csrf-token">` | token is per-process, not a secret-at-rest | no |
| 8–9 | `/api/status`, `/api/registry` | none | no | no |
| 10–12 | session create / get / rename | title `windows-smoke` / `macos-smoke` | **yes — harness session JSON under `CYCLAW_HOME`** | no |
| 13 | unknown session 404 | bogus id | no | no |
| 14 | `POST /api/soul` toggle + restore | boolean `enabled` | harness-local flag; **not** I5 `soul.md` | no |
| 15 | `POST /api/model` | model name `qwen3.8:27b-mlx` | harness config | no |
| 16 | `POST /api/chat` `"hello from …-smoke"` | chat text | session messages under `CYCLAW_HOME` | local LLM / mock_ollama only; 502 allowed if no backend |
| 17 | `GET /api/github/status` | none from script | read-only subprocess | may read local git remotes; must not push |
| 18 | `GET /api/harness/runs` | none | no | no |
| 19 | `GET /api/agent/checks` | Bearer | no | no |
| 20–21 | `POST /api/agent/run` and `.../decision` **unauthenticated** | empty `{}` | **must 401/403** — never a real run | no git write |
| 22 | `POST /ops/fsconnect` `action=status` | Bearer | config echo only | no filesystem walk |

Documented coverage gap (both scripts, same comment): `/ops/sync`, `/ops/agentic`, `/ops/sqlconnect` are **not** hit. That is a testing gap, not a privacy expansion. Do **not** add live `sync`/`sql`/`agentic` actions to the bomb without a separate privacy pass — those three can touch cloud, SQL rows, or writes.

---

## 4. Red flags

### Critical

None.

### High

None. The bomb never sets `user_confirmed_online: true`, never forwards local context, never prints `CYCLAW_API_KEY` / CSRF, never calls `/api/agent/run` authed.

### Medium

1. **Harness session residue on a live operator console.** Checks 10–12 and 16 write a session titled `windows-smoke` / `macos-smoke` plus a chat turn into whatever `CYCLAW_HOME` the already-running harness uses. CI sets `CYCLAW_HOME` to `${{ runner.temp }}/cyclaw-runtime-home`. An operator who fires the bomb against their daily console leaves that session on disk until they delete it. Not personal data of a data subject, but it is operator workspace clutter and a log of the probe. **Required:** keep CI isolation (done); document residue in the script header (done in `macos-smoke.sh`; Windows header should say the same).

2. **`/query` still ungated by per-user auth (Stage 3 not landed).** The smoke posts plaintext queries with no user credential. That matches shipped `auth.enabled: false`. If an operator later enables the auth store but not Stage 3, the smoke will keep posting unauthenticated queries while `data/auth/cyclaw_auth.db` exists populated — a data-mapping trap, not a smoke bug. Flag for any DPA that assumes "accounts = query access."

### Low

3. **Fail-path body echo.** `Fail "… $($h | ConvertTo-Json)"` / `fail "… $HTTP_BODY"` can print soul payload or chat reply on a failed assertion. Soul text is operator-authored; chat is the local model. Neither script echoes the Bearer value. Acceptable for a loopback probe; do not relax this into verbose `curl -v`.

4. **Windows catch `$_`.** PowerShell exception text can include the request URI. It should not include the `Authorization` header under `Invoke-RestMethod` defaults. Do not add `-Verbose` / `-Debug` to CI.

5. **Shared coverage gap.** Future editors adding `/ops/sqlconnect` `query` or `/ops/sync` `sync` to "close the gap" would create a High finding. The comments in both scripts exist so that does not happen silently.

---

## 5. Darwin twin — privacy delta vs Windows

`macos-smoke.sh` is a POSIX/bash 3.2 reimplementation of the same 22 checks.

| Item | Windows | Darwin twin | Privacy delta |
|---|---|---|---|
| Bind | `http://127.0.0.1:$Port` | `http://127.0.0.1:${PORT}` | none |
| JSON | `ConvertFrom-Json` | `python3` (`jget`); **no jq** | none — jq is a CI-image luxury, not a privacy control, but avoiding it keeps stock macOS from pulling Homebrew |
| Secrets | `$ApiKey` in headers only | `Authorization: Bearer ${API_KEY}` never `echo`d | none; tests pin the echo ban |
| CSRF | regex on harness HTML | `sed` extract of `csrf-token` | same source as `harness.html` JS |
| Agent writes | auth-gate-only, 32-zero run id | same | none |
| Session title | `windows-smoke` | `macos-smoke` | same residue class |
| Chat text | `hello from windows-smoke` | `hello from macos-smoke` | fixture, not PII |
| Starts servers? | no | no (CI boots them, then invokes the script) | none |

CI change: `macos-latest` previously inlined **5** jq checks (health, soul 401/authed, `/api/status`, `GET /`). That was a smaller surface and **did** depend on jq (present on GitHub's image, absent on a stock Mac). Wiring `macos-smoke.sh` is parity with `windows-smoke.ps1`, not a new processing activity.

---

## 6. Recommendations (required vs nice-to-have)

**Required (this PR):**

- Ship `macos-smoke.sh` as the Darwin/POSIX twin; do not start servers inside it.
- Point `macos-latest` live-smoke at that file; keep mock-ollama + gateway + harness boot and `CYCLAW_HOME` isolation in the workflow.
- Static-pin the twin (`tests/test_macos_smoke.py`): loopback, no jq/Homebrew, no secret echo, endpoint lock-step, auth-gate-only on agent writes, CI invokes the script.
- Header comment on the Darwin script stating residue + cyclaw-advisor posture (done).

**Nice-to-have (not blocking):**

- Mirror the residue sentence in the Windows script header so the twins stay comment-lock-step.
- Optional session delete after rename if harness grows a delete route (it does not today; do not invent a write).
- Do not add `/ops/sync|agentic|sqlconnect` live actions without a new Legal pass.

---

## 7. Escalation

**No escalation.** No personal data of a natural person is collected by these fixtures. No breach-notification clock (GDPR Art. 33 72-hour; CCPA/CPRA thresholds) starts. No DPA Schedule change: the smoke does not add a sub-processor.

If a later change (a) sets `user_confirmed_online: true`, (b) auths `/api/agent/run`, (c) prints `CYCLAW_API_KEY`, or (d) binds `0.0.0.0`, **stop and re-review** — any of those four is an automatic High/Critical.

---

## 8. Invariant matrix (this change)

| Invariant | Before | After | Evidence |
|---|---|---|---|
| I1 RAG-first | intact | intact | `/query` still hits `retrieve`; smoke is a client |
| I2 topology=policy | intact | intact | no graph edit |
| I3 triple-gate | intact | intact | no `user_confirmed_online: true` |
| I4 audit convergence | intact | intact | `hash_query` still pops `query` |
| I5 soul reason | intact | intact | no `/soul/apply`; harness toggle restores |
| I6 isolation | intact | intact | agent routes unauthenticated only |

Advisory only. Not licensed legal advice.
