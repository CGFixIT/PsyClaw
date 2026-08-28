---
name: cyclaw-swarm-verification
description: >
  CyClaw Swarm Verification -- comprehensive test system for the CyClaw
  offline-first RAG project (github.com/CGFixIT/CyClaw). Verifies the
  12-node LangGraph pipeline (incl. pre-action hooks) across 5 queries and
  three local-LLM realism tiers, triple-gated online API fallback (Grok +
  Claude, connection-only, no API cost), the pre-action hook gate, API key
  redaction, all 14 due-diligence invariant classes, spend/Numbat/
  sequence-detection forensics, the memory/Telegram/OpenTweet/unslop
  subsystems, the auth subsystem (bootstrap/session+CSRF/RBAC/TLS), the MCP
  manifest drift pin, and OS scheduler glue. Covers BOTH consoles fully:
  the terminal console's REST surface (/soul, /ops/sync, /ops/agentic,
  /ops/fsconnect, /ops/sqlconnect, /index/*, /memory/*, /auth/*) and all
  four of its slash commands, and the harness console's full guarded +
  unguarded route set (status/registry/tools/skills/web/memory/sessions/
  goal/loop/soul/model/keys/chat/github/harness-runs/all seven agent
  routes/auth), its ToolBroker capability gate on /loop and agent runs, and
  its full slash-command palette including the hidden `registry` alias.
  Use when asked to verify, smoke-test, validate, or test CyClaw; mentions
  CyClaw swarm, terminal consoles, the harness console, triple-gate API,
  Grok/Claude fallback, key redaction, due-diligence invariants, guardrails,
  memory, telegram, opentweet, netconnect, or running the test suite. Not
  the Claude Code session-memory skill (memory-orchestrator /
  docs/memories/).
---

# CyClaw Swarm Verification

**The contract, stated precisely.** A green run of this skill's full audit
means: the 12-node graph routes all 5 sample queries correctly at whichever
local-LLM realism tier was actually live; the Grok/Claude triple gate holds
end to end with zero real API spend; every terminal- and harness-console
REST endpoint and slash command this document lists is registered and
behaves per its documented contract; the 14 due-diligence invariant classes
and the 26-row Guardrails table below all hold; and the out-of-band
subsystems (spend ledger, Numbat, sequence detection, memory, Telegram,
OpenTweet, unslop, MCP manifest pin, OS scheduler glue) behave per their own
documented defaults. It does **not** mean live 27b-quality generation was
exercised, that a real Ollama daemon answered, that any browser JS ran, or
that a real GitHub-writing agentic run completed -- those are explicitly
out of scope everywhere they matter, called out inline.

Numbers below are marked one of two ways: a bare number next to a named
test or checker means that number is enforced somewhere and won't silently
drift (cite the enforcer, don't just trust this document); `(derive)` means
read it from the running code/tree at verification time, never copy it from
here. **Code, `config.yaml`, and the tests currently on disk always win over
this document.** Surface inventory below was last reconciled against main
@ `572227e` / `a4ca399` (2026-08-28) -- if it disagrees with what you see on
a fresh checkout, trust the checkout and treat the disagreement as this
document's own next drift-fix.

## Operator map -- which ladder proves what

Six ladders. They are complementary, not substitutable -- a green run of
one is **not** evidence the others would also pass. Always invoke with
`python3.12` (never bare `python3` -- see Gotchas).

**Claude Code `/memory` is not this skill.** In a Claude Code session,
`/memory` still means `memory-orchestrator` -> `docs/memories/`. The
harness console slash command `/memory` (below) is a separate, fail-closed
operator-note toggle under `~/.CyClaw/memory`. It does not extract session
memory, does not write `docs/memories/`, and does not replace the
PreCompact / SessionEnd hooks.

| Ladder | Command | Proves | Does **not** prove |
|---|---|---|---|
| **A. `/goal` + `/loop` only** | subsection below | session goal CRUD, goal in system prompt, `LOOP_REQUIRES_GOAL`, loop limiter ≠ chat limiter, cancel idempotence, HTML slash wiring, I6 (no `agentic` import from `harness/`) | RAG/graph, live model quality, browser `/loop auto` + `GOAL_DONE`, `/api/agent/run` |
| **B. In-process swarm** | `python3.12 .claude/skills/CyClaw-Sandbox/run_full_verification.py` | 11 phases (config invariants, telemetry maps, mock RAG index + 5 queries, triple-gate, redaction, due-diligence, terminal REST + slash commands, harness `TestClient` incl. `/api/keys` + agent auth-gates, harness HTML contract) -- prints its own totals, never hand-count them | Live HTTP servers, browser JS, Windows installer, real chromadb (stub mode) |
| **C. CI lifecycle** | `bash .claude/skills/CyClaw-Sandbox/verify.sh` | 3.12 venv, full pytest, RAG smoke, live `gate.py` + harness + `mock_ollama`, both emulations | Browser `/loop auto`, real 27b-class model, Auth beyond the shipped default, live NeMo rail |
| **D. Surface smoke** | `bash .claude/skills/CyClaw-Sandbox/smoke.sh` (a.k.a. `/run`, Quick Mode) | Sections A-G against a live server it starts itself -- prints its own PASS/FAIL totals to `.claude/sandbox-test.txt`, never a fixed count | Due-diligence classes, harness `/goal`/`/loop`, agent-run routes, the full Ladder F sweep below |
| **E. Live API bomb** | `windows-smoke.ps1` / `macos-smoke.sh` | Matching live-HTTP checks (gate + harness) against already-running servers -- see the scripts' own numbered comments for the current count | Broader fsconnect/sqlconnect/guardrails/Postgres (that's ladder D); `/ops/sync`, `/ops/agentic`, `/ops/sqlconnect` (documented gap in both scripts' headers) |
| **F. Full sandbox clone audit** | Steps section below | Everything in B-D plus spend/Numbat/sequence-detection, the full auth lifecycle (bootstrap through RBAC and TLS), memory/Telegram/OpenTweet/unslop, MCP manifest pin, OS scheduler glue, and the groundedness evaluator -- the "verify everything" ladder | Nothing it doesn't say it skips (opt-in stages are marked SKIP, not silently passed) |

### A. `/goal` + `/loop` only (harness console contract)

From repo root, test extras installed, `CYCLAW_HOME` isolated if you launch
a server:

```bash
python3.12 -m pytest tests/test_harness.py tests/test_harness_console_contract.py \
  tests/test_harness_auth.py tests/test_harness_isolation.py -q --tb=short

python3.12 .claude/skills/CyClaw-Sandbox/harness_runtime_check.py

# Live HTTP -- verify.sh Stage 9 does this with mock_ollama on :11434:
CYCLAW_HOME=$(mktemp -d) CYCLAW_API_KEY=verify-soul-key-ci \
  python3.12 -m harness.server &
python3.12 .claude/skills/CyClaw-Sandbox/harness_emulation.py http://127.0.0.1:8790
```

Rows that must pass before claiming `/goal` / `/loop` work:

| Check | Where it is asserted |
|---|---|
| Goal set / trim / persist / clear / unknown-id 404; listing omits `goal` | pytest + swarm Phase 11 + emulation step 14 |
| Goal lands in the chat system prompt; blank goal omitted | `tests/test_harness.py` |
| `loop: true` with no goal -> 400 `LOOP_REQUIRES_GOAL` | pytest + swarm + emulation step 15 |
| `loop: true` with a goal is chat-only (200, or documented 502 with no backend) | pytest + swarm + emulation step 15 |
| `POST /api/chat/cancel` is idempotent (`/loop stop`) | pytest + swarm + emulation step 16 |
| Dedicated loop limiter ≠ chat limiter; `CHAT_BUSY` generation gate | pytest only (`test_loop_rate_limit_*`, `test_chat_busy_*`) |
| `/goal` and `/loop` in `COMMANDS` / dispatch switch; no `innerHTML` | HTML contract + `test_harness_console_contract.py` |
| `/loop` never calls `/api/agent/*` | `test_loop_command_never_starts_a_real_repo_run` |
| The named-capability ToolBroker gate (issue #1134) allows a `/loop` turn and denies per its allowlist | `test_guardrails_tool_broker.py`, `test_tool_broker_adversarial.py` -- forcing a live denial needs monkeypatching the allowlist; the runtime check only confirms the gate is wired |

### `verify.sh` stage numbers (historical -- do not renumber)

Labels are **not** sequential in source order. CI logs and comments cite
them; do not "fix" the numbers.

| Label | Source order | What runs |
|---|---|---|
| Stage 1 | 1st | Python 3.12 venv + deps |
| Stage 2 | 2nd | `pytest tests/` |
| Stage 3 | 3rd | emulated RAG (`tests/ci_rag_smoke.py`) |
| Stage 5 | 4th | `gate_runtime_check.py` |
| Stage 8 | 5th | `harness_runtime_check.py` |
| Stage 4 | 6th | live `gate.py` API smoke |
| Stage 7 | 7th | `terminal_emulation.py` |
| Stage 9 | 8th | live harness + `harness_emulation.py` |
| Stage 6 | last | write `/tmp/cyclaw-verify-report.md` |

Stage 1's hard failure on a missing/wrong `python3.12` is deliberate, not a
gap against this repo's usual "skip cleanly without deps" skill convention
(CLAUDE.md §6): the Python-3.12 runtime gate is this script's own
advertised feature (CLAUDE.md §4 routes "suspected config drift" reports
here specifically because bare `python3`/`pytest` silently run the wrong
interpreter and fail ~142 tests in a way that looks like a red `main`).
Every other stage degrades gracefully; this one is supposed to stop you.

## Where the surfaces live

One row per surface. "Pinned by" names a test or checker that would catch
regression on its own, independent of this skill.

| Surface | File(s) | What to verify | Pinned by |
|---|---|---|---|
| Gate core routes | `gate.py` | `/`, `/health`, `/query`, `/soul*`, `/audit/summary`, `/index/build`, `/index/status` | `gate_runtime_check.py` |
| Ops routes | `gate_ops.py` | `POST /ops/{sync,agentic,fsconnect,sqlconnect}`; every `action` field is a closed `Literal` (`schemas/api.py`) -- an unrecognized value is a **422**, not a handler-level 400 | `test_terminal_consoles.py` |
| Auth routes | `gate_auth.py` | 13 paths (`/auth/setup-status`, `/login`, `/logout`, `/whoami`, `/users` GET+POST, `/password`, `/users/{u}/password`, `/users/{u}/role`, `/users/{u}/disable`, `/users/{u}/enable`, `/users/{u}` DELETE, `/audit/summary`); every route exists and answers **503** (not 404) when `auth.enabled` is false (the shipped default); identity attaches to `POST /query` only when an `AuthManager` exists; `audit` role is forbidden from `/query`; account lockout answers **423** | `test_auth_admin_contract.py`, `tests/test_due_diligence_invariants.py` |
| Memory routes | `gate_memory.py` | `/memory/status` is always 200 (probeable when the subsystem is off); `/memory/{facts,episodes,proposals,propose,apply,reject}` and `/query/export/html` are **404** when their toggle is off (all ship false) | `test_memory_isolation.py`-style isolation + live probe |
| Terminal console | `static/terminal.html` + `static/terminal.js` (CSP forces `script-src 'self'`, so the console's JS logic lives in the sibling file -- read both together) | 5 toolbar panels (Soul/Sync/Agentic/FS/SQL); the confirm dialog's generic `handleConfirm(confirmed, entryId, onlineProvider)`; the four slash commands `/users /admin /audit /help` (everything else is a toolbar button or a RAG query, not a command) | `tests/test_terminal_contract.py` (reads `terminal.html + terminal.js` combined; pins the 5 POST-only paths) |
| Harness console | `harness/server.py` + `static/harness.html` | Guard order rate-limit -> same-origin -> API key -> CSRF (`guarded` dependency list); ~29 guarded + ~11 unguarded routes (derive the exact split from `app.routes` -- don't hardcode it, this skill has been burned by a stale count here before); the `COMMANDS` array (derive the full palette from the array itself, currently ~19 distinct commands incl. two rows both dispatching to `/agent`) plus the hidden `registry` alias of `/connectors` (`case 'connectors': case 'registry':`) | `test_harness_contract.py`, `test_harness_console_contract.py`, `test_harness_tools_contract.py` |
| Harness ToolBroker gate | `utils/tool_broker.py`, wired into `harness/server.py` | Named-capability gate (issue #1134) in front of `/loop` turns and `POST /api/agent/run`; `assert_allowed(...)` raises `ToolDenied` -> 403. No route was added by this gate -- it's a control layered on two existing ones | `test_guardrails_tool_broker.py`, `test_tool_broker_adversarial.py` |
| MCP | `mcp_hybrid_server.py` | Single tool `hybrid_search`; `sampling: None`; `check_input` runs before retrieval; `mcp_manifest.json` SHA-256 drift pin | `tests/test_mcp_server.py`, `tests/test_mcp_manifest.py` |
| Graph | `graph.py` | 12 nodes (`retrieve`, `route_by_score`, `guardrail_input`, `guardrail_output`, `local_llm`, `user_gate`, `pre_action_hook_grok`, `pre_action_hook_claude`, `grok_fallback`, `claude_fallback`, `offline_best_effort`, `audit_logger`); 4 routers (`score_router`, `guardrail_router`, `pre_action_hook_router`, `user_gate_router`) | `tests/test_operator_docs_node_count.py` (`>=12`), `tests/test_graph.py` |
| CLIs | `sync/cli.py`, `agentic/cli.py`, `agentic/fsconnect/cli.py`, `agentic/sqlconnect/cli.py`, `agentic/netconnect/cli.py`, `telegram/cli.py`, `opentweet/cli.py`, `guardrails/cli.py`, `utils/authn_cli.py`, `utils/gen_cert.py`, `retrieval/indexer.py`, `retrieval/clear_cache.py`, `metrics.py`, `utils/telemetry_kill.py --export` | Name + exit-code contract only (`--help` for the current subcommand set -- never hardcode a subcommand count in this document, it drifts every time a CLI grows one) | `tests/test_*_cli.py` per connector |
| Telemetry contract | `utils/telemetry_kill.py`, `utils/onnx_telemetry.py` | Owned by the `otel-hardening` skill, not this one -- run `python3 .claude/skills/otel-hardening/check_otel.py --strict --as-of $(date +%F)` for the full contract. This skill only spot-checks that the canonical maps (`TELEMETRY_KILL`/`UPDATE_CHECK_OPT_OUT`/`SCRUBBED_ENV_KEYS`) are wired at import time | `otel-hardening/check_otel.py` |
| Invocable checker skills | `.claude/skills/{invariant-guard,config-guard,dep-guard,verify-deps,otel-hardening,doc-sync,index-doctor,injection-redteam}/` | Each has its own `check_*.py`/`verify.sh` -- run the ones relevant to what changed, not all of them on every pass | see each skill's own `SKILL.md` |

## Steps -- the full audit (Ladder F)

The single procedure absorbing the in-process swarm's 11 phases and every
live/out-of-band surface into one ordered run. Record PASS / FAIL / SKIP
per numbered item; an opt-in item you didn't exercise is SKIP, never a
silent pass.

### 1. Clone & inspect

```bash
git clone https://github.com/CGFixIT/CyClaw.git
cd CyClaw && git checkout main && git pull
git log -1 --format='%H %cs'          # record sha/date for the sign-off
python3.12 --version                  # must be 3.12.x
grep '^version' pyproject.toml        # (derive) record it
```

Read `config.yaml` and confirm the shipped contract (all `(derive)` --
compare against the file, don't trust a number written here): `app.mode`,
`api.host`/`api.port`, `models.grok.enabled`/`models.claude.enabled`,
`retrieval.min_score`, `policy.prompt_filter.banned_patterns` length
(floor, not exact -- injection-redteam legitimately grows this list),
`fsconnect`/`sqlconnect`/`sync`/`agentic`/`auth`/`numbat`/`unslop`/
`netconnect`/`telegram`/`opentweet`/`guardrails` blocks present with their
shipped `enabled` values, `policy.fallback.require_user_confirm` present
but unwired (hardcoded in `user_gate_router`), `policy.fallback.
{grok,claude}_max_prompt_chars`, `policy.privacy.redact_secrets_like`
includes an `sk-ant-*` pattern, `policy.fallback.pre_action_hook.enabled`
(shipped false), `security.api_key_optional` (shipped false).

Verify module isolation (I6): `agentic/`, `sync/`, `guardrails/`,
`harness/`, `telegram/`, `opentweet/`, `netconnect/` are never imported by
`gate.py`, `graph.py`, or `mcp_hybrid_server.py`, and vice versa; the
`agentic.*.cli` modules run only via subprocess from `utils/ops_runner.py`.

Verify code structure: `graph.py` has `_external_fallback_node(state,
client, cfg, *, provider, label)`; `grok_fallback_node`/`claude_fallback_node`
are thin wrappers over it (shared prompt assembly, cost-guard truncation,
audit-log truncation events, and -- since the `served_model` audit field
landed -- the vendor-echoed model id forwarded onto the audit record).

### 2. Environment

Requirements: Python 3.12 exactly, `rank-bm25`, PyYAML, numpy, httpx, and
(full mode) chromadb/sentence-transformers/langgraph/fastapi/uvicorn.

**In a Claude Code cloud sandbox** (CLAUDE.md §4's documented realities,
not a generic sandbox checklist): the default image ships Python
3.10/3.11/3.12/3.13 side by side, but `update-alternatives` and the
pre-installed dependencies both point at 3.11 -- bare `python3`/`pytest`
silently run the wrong interpreter and fail ~142 `test_agentic_*` tests on
a 3.12-only stdlib parameter, which looks like a red `main`, not a version
mismatch. Build an explicit venv instead:
```bash
python3.12 -m venv /root/.venv-cyclaw-312
/root/.venv-cyclaw-312/bin/pip install torch==2.13.0+cpu \
  --index-url https://download.pytorch.org/whl/cpu
/root/.venv-cyclaw-312/bin/pip install -r requirements.txt -r requirements-test.txt \
  -c constraints.txt --ignore-installed PyYAML
```
If the PyTorch CPU index is blocked by an outbound proxy, install plain
`torch` and feed pip **scratchpad copies** of `requirements.txt`/
`constraints.txt` with the `torch==`/`--extra-index-url` lines stripped --
never edit the repo's own manifests (macOS needs this same plain-torch path
for a different reason: no `+cpu` wheel exists on the arm64 index). The
venv does not survive session end; treat this as a per-session setup step,
always invoked as `/root/.venv-cyclaw-312/bin/python`, never bare `python3`.

`GROK_API_KEY=dummy` (any non-empty value) is sufficient everywhere --
`security.require_env` is decorative and read by no code.

**Full dependency install** (preferred when network access allows):
`pip install -e ".[test,full]"`.

**Sandbox/stub fallback**: `run_full_verification.py` builds its own
in-memory stubs for chromadb/sentence-transformers/langgraph/langsmith --
see that file for the exact stub set. Note: those stubs assume a genuinely
bare interpreter with nothing installed; running the script in a venv that
already has real chromadb installed can produce one spurious import-order
failure unrelated to CyClaw itself (see Gotchas).

Generate a session key rather than a hand-picked one for any live-gate
work in this Steps section: `python3 -c 'import secrets;
print(secrets.token_urlsafe(32))'`. Never log the full value -- truncate to
the first 6 characters in notes.

### 3. Static gates (no server)

```bash
GROK_API_KEY=dummy python3.12 -m pytest tests/ -q --tb=short   # (derive) record N passed / N total
python3 .claude/skills/invariant-guard/check_invariants.py     # (derive) record N/N
GROK_API_KEY=dummy python3.12 -m pytest tests/test_due_diligence_invariants.py -q  # 14 classes
python3 .claude/skills/doc-sync/doc_sync.py
python3 .claude/skills/config-guard/check_config.py
```

### 4. In-process swarm

```bash
GROK_API_KEY=dummy python3.12 .claude/skills/CyClaw-Sandbox/run_full_verification.py
```
Expect `total_passed == total_checks` in `verification_report.json`;
pointing `CYCLAW_REPO` at a working tree (rather than a scratch clone)
makes this write mock corpus/index/report files into it -- the script
warns loudly when you do this on purpose.

### 5. Live gate (:8787)

```bash
CYCLAW_API_KEY=$CYCLAW_API_KEY GROK_API_KEY=dummy nohup python3.12 -m uvicorn gate:app \
  --host 127.0.0.1 --port 8787 >/tmp/gate.log 2>&1 &
for i in $(seq 1 40); do curl -sf 127.0.0.1:8787/health >/dev/null && break; sleep 0.5; done
python3.12 .claude/skills/CyClaw-Sandbox/gate_runtime_check.py
python3.12 .claude/skills/CyClaw-Sandbox/terminal_emulation.py http://127.0.0.1:8787
CYCLAW_API_KEY=$CYCLAW_API_KEY python3.12 .claude/skills/CyClaw-Sandbox/test_terminal_consoles.py
```
Verify: `/health` 200 unauthenticated; `/` serves `terminal.html`; auto-docs
off (`/docs`/`/openapi.json` 404); every `/soul/*` and `/ops/*` route 401s
without the key, 429 under rate-limit exhaustion, and carries the security
headers (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`,
`Permissions-Policy`, `Content-Security-Policy`); `/auth/*` returns 503 (not
404) while `auth.enabled` is false; all four terminal slash commands
respond; `/index/build`/`/index/status` are loopback+same-origin gated, NOT
key-gated (deliberate -- an unset key must not brick a first-run index
build). Enabling auth in a scratch config additionally exercises: bootstrap
from loopback only, session cookie + CSRF on `/query`, RBAC (`audit` role
403s on `/query`), and TLS via `cyclaw-gen-cert` (session cookie gains
`secure` once `api.tls.enabled: true`).

### 6. Live harness (:8790)

```bash
CYCLAW_HOME=$(mktemp -d) CYCLAW_API_KEY=$CYCLAW_API_KEY nohup python3.12 -m harness.server \
  >/tmp/harness.log 2>&1 &
# pair with mock_ollama.py on :11434 for a deterministic /api/chat 200:
python3.12 .claude/skills/CyClaw-Sandbox/mock_ollama.py --port 11434 --model qwen3.8:27b-mlx &
python3.12 .claude/skills/CyClaw-Sandbox/harness_runtime_check.py
python3.12 .claude/skills/CyClaw-Sandbox/harness_emulation.py http://127.0.0.1:8790
```
Verify: every guarded route enforces the full chain (missing key -> 401,
missing/bad CSRF -> 403, cross-origin -> refused); `/api/keys` GET returns
presence + masked tail only (never a value -- assert the caller's own
Bearer key never round-trips in the response) and a non-`MANAGED_KEYS` name
is refused on POST; `/api/auth/setup-status` is 503 `AUTH_DISABLED` with
the shipped default (200 + `{enabled, needs_password, username}` once an
operator config turns auth on); the seven `/api/agent/*` routes
(`checks`, `run`, `runs/{id}`, `runs/{id}/decision`, `runs/{id}/push`,
`runs/{id}/publish`, `runs/{id}/discard`) are auth-gate-only here -- a bad
bearer 401s on every write route, and none of `run`/`decision`/`push`/
`publish`/`discard` is ever actually invoked (a real run clones a repo,
calls a model, can block ~900s, and push/publish/discard reach a git
write); the full slash-command palette (derive from `COMMANDS`, see the
surfaces table) responds, including the hidden `registry` alias; no
`innerHTML` anywhere in `harness.html` (`textContent`/`createElement`
only -- chat replies, skill descriptions, and session titles are all
untrusted-origin strings).

### 7. Out-of-band subsystems

- **Spend** (`utils/spend.py`, `logs/spend.jsonl`): append-only; each real
  external generate appends one record; `cyclaw-metrics` splits spend by
  source (`query` vs `agentic`) and prints a vendor-cost comparison; a
  price staleness warning surfaces (non-fatal) once the pricing table's own
  `PRICED_AS_OF` date is old enough -- read the actual threshold from
  `utils/spend.py`, don't hardcode it here.
- **Numbat** (`utils/numbat_emitter.py`, ships `enabled: true`): activity
  appends NDJSON lines to `logs/numbat-events.ndjsonl`; fail-soft (make the
  emitter raise -- e.g. read-only logs dir in a scratch copy -- and confirm
  the request still succeeds); the documented skip-set event types never
  double-emit through the mainline projection.
- **Sequence detection** (`utils/sequence_detect.py`, forensic-only): CLI
  or `cyclaw-metrics`-joined surface lists its rule set; craft synthetic
  audit+spend fixtures and confirm a suspicious pattern is flagged and a
  clean log is not; `grep -rn sequence_detect gate.py graph.py mcp*.py`
  returns nothing (I6-style isolation, even though it isn't in the formal
  out-of-band package set).
- **Memory** (`gate_memory.py` + package `memory/`, ships every switch
  false): `/memory/status` 200 with flags off; `/memory/{facts,episodes,
  proposals}` and `/memory/{propose,apply,reject}` 404 while their toggles
  are off; enabling in a scratch config exercises propose -> apply ->
  queryable facts, and an injection payload in a proposal triggers the
  documented block event.
- **Telegram** (`telegram/`, ships `enabled: false`): CLI disabled-state
  report; a mocked-Bot-API integration (stub `getMe`/`sendMessage`/
  `getUpdates`) drives `poll_once`/`send_notify`; the `/online on <grok|
  claude>` hybrid-confirm command works only in an allowlisted private
  chat with the exact syntax.
- **OpenTweet** (`opentweet/`, ships `enabled: false`): CLI disabled-state
  exit; enabled in a scratch config, the runner's generation call is
  loopback `/query` with `user_confirmed_online: false` and a draft is the
  default outcome -- it never escalates online or posts by itself.
- **Unslop** (`agentic/unslop_bridge.py`, ships `enabled: false`): zero
  effect when off; enabled, forcing the vendored bridge to raise must not
  block the agentic run it's attached to (log-only, non-blocking).
- **netconnect** (`agentic/netconnect/`, ships `enabled: false`): passive
  LAN inventory only -- no ping/probe/sweep; every returned address must
  fall inside an explicitly configured RFC1918/loopback CIDR; an empty or
  overly broad scope fails closed rather than defaulting open.

### 8. MCP + OS glue

- `mcp_manifest.json`'s SHA-256 drift pin: flip a byte in a scratch copy
  and confirm drift is reported and fails closed. `hybrid_search` works
  with no LLM booted; `check_input` runs before retrieval.
- Platform generators refuse cross-platform on the wrong host (a Darwin
  generator run on Linux, or vice versa, errors before its own governance
  gate even evaluates `--confirm`/`--reason`); secrets never appear in
  argv for either platform's credential wrapper.
- `sync/scheduler.py::get_scheduler`: the configured backend selects the
  matching implementation; an unsupported backend/platform pairing (e.g.
  `launchd` requested on non-Darwin) is a clean `SchedulerError`, never a
  silent fallback to a different backend; cron-line ownership stays scoped
  to CyClaw-managed lines only.

### 9. Groundedness evaluator (opt-in)

`CYCLAW_EVAL_LIVE=1 ANTHROPIC_API_KEY=... python3.12 tests/judge_eval.py`
against a loopback LLM: exit 0 pass / 1 fail / 2 infra. Without live keys,
verify the gate refuses cleanly (exit 2) rather than silently skipping.

### 10. Report

End with a sign-off naming: the repo sha/date and Python/pyproject
versions actually used; which local-LLM realism tier was live (0 = pytest
stub, 1 = `mock_ollama.py`, 2 = real Ollama daemon -- **both**
`run_full_verification.py` and `verify.sh` auto-detect and report this,
don't hand-guess it); per-item PASS/FAIL/SKIP for every numbered item
above; the due-diligence and Guardrails pass counts (report the actual
`N/14` and `N/26` from the run, never copy last time's numbers); and any
Known Residual (below) that changed since it was last confirmed.

## Quick Mode (`/run`)

`bash .claude/skills/CyClaw-Sandbox/smoke.sh` -- a fast, live-server pass
covering sections A-G (core API, fsconnect, sqlconnect, NeMo soft-import,
Postgres-backend skip-cleanly, and the full pytest suite as its final
section). It builds its own index if one is missing, starts and stops its
own `gate.py`, and writes `.claude/sandbox-test.txt`. It does **not** build
the mock RAG corpus, walk the due-diligence invariant classes, exercise the
harness console at all, or touch any agent-run route -- a green Quick Mode
is not evidence the full Ladder F audit would also pass, and the reverse
holds too.

## Bundled resources

All scripts below live flat in this skill directory
(`.claude/skills/CyClaw-Sandbox/`), not under a `scripts/`/`references/`
subdirectory -- invoke them by that path.

- **`run_full_verification.py`** -- the in-process swarm (Ladder B, 11
  phases). Env: `CYCLAW_REPO=/path` to use an existing checkout instead of
  cloning fresh (warns before writing mock corpus/index/report files into
  it); `FULL_DEPS=1` to attempt a full dependency install first. Both this
  script and `verify.sh` auto-detect the live Ollama realism tier.
- **`gate_runtime_check.py`** / **`harness_runtime_check.py`** --
  independent, import-time-only checks: app builds, telemetry-kill maps
  are active, the expected route subset registers, auto-docs stay
  disabled, entry points are callable. `harness_runtime_check.py` also
  confirms the ToolBroker gate's symbols import cleanly.
- **`terminal_emulation.py`** / **`harness_emulation.py`** -- exercise the
  exact HTTP fetch lifecycle each console's own JS performs, against an
  already-running server. Wired into `verify.sh` (stages 7 and 9); also
  runnable standalone. Pair `harness_emulation.py` with `mock_ollama.py`
  on `127.0.0.1:11434` for a deterministic `/api/chat` 200 instead of the
  documented 502 no-backend fallback.
- **`test_terminal_consoles.py`** -- stdlib-`urllib` integration test
  against a running `gate.py` with `CYCLAW_API_KEY` set. Asserts every
  `/soul/*`/`/ops/*` route's auth gate, an unknown `action` 422s at the
  schema boundary (closed `Literal` fields, not a handler-level 400), and
  the security-header/DROP-rejection contracts.
- **`mock_ollama.py`** -- stdlib-only mock Ollama/OpenAI-compatible server
  (`/api/tags`, `/api/chat`, `/api/generate`, `/v1/models`,
  `/v1/chat/completions`) for deterministic offline chat testing. This is
  realism **Tier 1** of three: Tier 0 is the in-process pytest stub
  (`MockLocalLLM` in `tests/conftest.py`), Tier 2 is a real Ollama daemon.
- **`verify.sh`** -- the CI-wired, full Linux lifecycle: Python 3.12
  provisioning, the pytest suite, an emulated RAG query, both independent
  runtime checks, then both consoles launched live and emulated end to
  end. Non-sequential stage labels, see the table above -- never renumber.
- **`smoke.sh`** -- Quick Mode, see above.
- **`windows-smoke.ps1`** / **`macos-smoke.sh`** -- the platform live-API
  bombs against already-running servers (they start nothing themselves).
  `windows-latest` CI runs the PowerShell script; `macos-latest` CI runs
  the bash one. Neither is discovered by the `verify-skills` matrix (that
  job only globs `verify.sh`/`smoke.sh`); both run as dedicated, blocking
  CI steps instead. `macos-smoke.sh` is Darwin-first (bash 3.2, no jq) and
  also the POSIX twin a Linux operator can run by hand.
- **`test-specifications.md`** -- detailed test-case inventory (query
  prompts, per-provider triple-gate cases, redaction cases, the
  due-diligence classes, console endpoint tests, macOS realism coverage
  table). Read when implementing new tests or debugging a failure.

## Known residuals

Track as KNOWN -- confirm they still exist at each full audit; never
report one of these as a new finding.

1. `check_jailbreak` / `check_soul_leak` are listed in guardrails config
   but not enforced by the offline heuristic floor (model-assisted only);
   `guardrail_output` is grounding-only.
2. Telegram's T4 media handling is partial and POSIX-only.
3. `memory/consolidation.py` is a deliberate stub; consolidation stays
   disabled in v1.
4. `terminal.html` has no memory console and no full auth-management UI --
   both are REST-only surfaces today (a minimal `.toolbar-auth` affordance
   exists for `/users`/`/audit`).
5. `embeddings_local`'s health-check entry is static by design (it does
   not depend on whether the model has actually loaded) -- not a finding.
6. `security.require_env` is decorative; no code enforces it at boot.
7. Hand-run `uvicorn gate:app` (bypassing the shipped launcher or Docker
   CMD) still gets the canonical telemetry/update-check env only at module
   import, not before the interpreter starts -- documented in
   `docs/THREAT_MODEL.md`, accepted.

## Guardrails

Restates the security invariants this skill verifies (see `CLAUDE.md` §3
for the six canonical ones plus supporting guards this table extends).

| # | Invariant | Check |
|---|---|---|
| 1 | RAG-First | `retrieve` is always the unconditional graph entry point |
| 2 | Topology = Policy | Routing via the named routers, never a prompt or ad-hoc branch |
| 3 | Triple-Gated External | `app.mode=="hybrid"` AND `<provider>.enabled` AND per-request `user_confirmed_online`, all three, for whichever provider is selected |
| 4 | Audit Convergence | Every path -- including `hook-denied`, `external-unavailable`, `guardrail-blocked` -- reaches `audit_logger` exactly once before END |
| 5 | Soul Governance | Evolution requires a non-empty human `reason` string; write is atomic |
| 6 | Telemetry Contract | Owned by `otel-hardening`, not this skill: canonical maps in `utils/telemetry_kill.py` applied at import (G1 anchor); `python3 .claude/skills/otel-hardening/check_otel.py --strict` is the authority, not a count copied into this document |
| 7 | Loopback Only | `api.host == "127.0.0.1"` |
| 8 | FsConnect Read-Only Default | `fsconnect.writes_enabled=false`, `follow_symlinks=false` |
| 9 | FsConnect Pathsafe | All paths resolve through `ScopedRoots` with `O_NOFOLLOW` |
| 10 | FsConnect Op Whitelist | Capability list is closed (derive the current set from `config.yaml`'s `fsconnect.allowed_fs_ops` -- it grows over time, e.g. `fs_largest`) |
| 11 | SQL Read-Only Default | `sqlconnect.read_only=true`, `allow_write=false` |
| 12 | SQL Query Guard | Only SELECT/WITH; comments and `;` rejected |
| 13 | Module Isolation (I6) | `agentic/sync/guardrails/harness/telegram/opentweet/netconnect` never imported by `gate.py`/`graph.py`/`mcp_hybrid_server.py`, and vice versa |
| 14 | Soul Privacy | Soul preamble never forwarded to Grok/Claude (off-box) |
| 15 | API Key Gate | All mutating gate.py routes require `CYCLAW_API_KEY`, fail-closed on an unset key |
| 16 | Rate Limit | Every route shares the per-IP `RateLimiter` |
| 17 | Key Redaction Parity | `ANTHROPIC_API_KEY` redacted the same as `GROK_API_KEY`; `sk-ant-*` pattern present in both `gate.py` and `config.yaml` |
| 18 | Harness Loopback Only | `harness/server.py` binds only `127.0.0.1`/`localhost`/`::1` |
| 19 | Harness Module Isolation | `harness/` never imported by the core-six modules, and vice versa (I6) |
| 20 | Harness Chat Rate Limit | `/api/chat` shares the same `RateLimiter` + `config.yaml` rate-limit block as `gate.py`'s `/query` |
| 21 | Harness Console XSS Safety | All model/registry output rendered via `textContent`/`createElement`; no `innerHTML` |
| 22 | Auth Disabled By Default | `auth.enabled=false` ships default, matching every other opt-in subsystem |
| 23 | Auth Route Presence Doesn't Disclose State | Every `/auth/*` route always returns 503, never 404, while `auth.enabled` is false |
| 24 | Auth Stage 3 Enforced | The identity dependency (`require_session_or_token`) attaches to `POST /query` whenever an `AuthManager` exists; the `audit` role is forbidden from `/query`; the 503-when-disabled contract is unchanged when auth stays off |
| 25 | Pre-Action Hook Fail-Closed | A non-zero/non-2 exit or a timeout on `policy.fallback.pre_action_hook`'s command denies (`answer_model="hook-denied"`); the hook's stdin payload carries no query text or soul content |
| 26 | API-Key Bypass Hygiene | `security.api_key_optional`'s bypass (when turned on) requires a loopback socket peer AND zero reverse-proxy forwarding headers; the bind guard separately refuses a non-loopback `api.host` while that flag is set |

## Gotchas

- **`python3` may not be 3.12.** See Step 2 above -- point explicitly at
  `python3.12` (or the session venv's `bin/python`) rather than trusting
  the default.
- **Quick Mode and the full audit test different things.** See the
  Operator map's Ladder D row -- treat them as complementary, never
  substitutable in either direction.
- **Three local-LLM realism tiers, not one.** Tier 0 = in-process pytest
  stub, Tier 1 = `mock_ollama.py`, Tier 2 = a real daemon. Both
  `run_full_verification.py` and `verify.sh` auto-detect which one is
  live and report it -- still state the tier honestly in your own sign-off
  rather than assuming Tier 2 realism from a Tier 0/1 run.
- **The agent-run routes are auth-gate-only in this skill, on purpose.**
  Only `/api/agent/checks` is ever actually invoked; `run`, `decision`,
  `push`, `publish`, and `discard` are only probed for a 401 on a bad key,
  because a real call clones a repo, calls a model, can block ~900s, and
  the last three reach a git write. A green audit does not mean the agent
  loop itself was verified end to end -- that is `agentic/real_repo_loop.py`
  territory, out of scope here.
- **`run_full_verification.py` writes into whatever `CYCLAW_REPO` points
  at** from Phase 3 onward (mock corpus, BM25 index, two JSON report
  files). Point it at a scratch clone, not a working tree, unless those
  writes are what you want -- the script warns loudly either way.
- **A venv with real chromadb installed can produce one spurious failure**
  in `run_full_verification.py`'s Phase 6 (`anthropic_key_sanitized`):
  `_install_stubs()` assumes a bare interpreter and unconditionally
  replaces `sys.modules["chromadb"]`/`chromadb.config` with empty stubs;
  if real chromadb is already installed in the venv, a later
  `from chromadb.config import Settings` inside `gate.py`'s import chain
  can hit the stub instead of the real module, depending on import order.
  This is an environment artifact of a partially-real-deps venv, not a
  CyClaw regression -- confirm by reproducing it with only `_install_stubs()`
  plus a bare `from gate import _sanitize_error`, independent of anything
  else in this skill, before treating it as a finding.
- **`pkill -f` can match your own invoking command line.** Use a distinct
  marker or kill by PID rather than a broad process-name pattern,
  especially when a prior command in the same session already started a
  server under a similar name.

## Mock Embedding Implementation

`MockSentenceTransformer` creates sparse keyword-based 384-dim vectors:
- Each word hashes to 3 dimension slots via MD5
- Slot values accumulate per word occurrence
- Final vector L2-normalized

`MockChromaClient` / `MockCollection` implement:
- `add(embeddings, documents, metadatas, ids)` -- append documents
- `query(query_embeddings, n_results)` -- cosine similarity search
- `get_or_create_collection(name)` -- singleton collection registry
