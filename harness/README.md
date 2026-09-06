# `harness/` — CyClaw coding console

Out-of-band slash-command console and small FastAPI control plane. Separate
from the RAG gateway: `gate.py` keeps `127.0.0.1:8787`; this package binds
`127.0.0.1:8790` by default. Non-loopback hosts are refused.

I6: `gate.py`, `graph.py`, and `mcp_hybrid_server.py` never import this
package, and it never imports them. GitHub side-effects go through
`utils.ops_runner` → `python -m agentic.cli` (the same shim as
`POST /ops/agentic`). No host shell; no writes outside the harness home
(`%USERPROFILE%\.CyClaw` on Windows, `~/.CyClaw` elsewhere, or `CYCLAW_HOME`).

## Run

```bash
python -m harness.server          # after clone; no install needed
cyclaw-harness                    # console script after `pip install -e .`
```

Installers (home dir, venv, PATH shim) are the only OS-specific glue:

- macOS / Linux: `bash ./macos/install-cyclaw.sh`
- Windows: `powershell -ExecutionPolicy Bypass -File .\powershell\Install-CyClaw.ps1`

Full walkthroughs: [`docs/HARNESS_MACOS.md`](../docs/HARNESS_MACOS.md),
[`docs/HARNESS_POWERSHELL.md`](../docs/HARNESS_POWERSHELL.md).

## Package map

| Module | Role |
|---|---|
| `server.py` | FastAPI app + `static/harness.html` |
| `agent_routes.py` | The 7 `/api/agent/*` real-repo-run routes, registered onto `server.create_app`'s app |
| `auth_routes.py` | The `/api/auth/*` session/bootstrap routes, registered the same way |
| `config.py` | Home layout + read-only view of repo `config.yaml` |
| `sessions.py` | Per-session JSON + token tallies |
| `ollama.py` | Local OpenAI-compatible chat client |
| `prompts.py` | System prompt (repo skills + optional soul + optional session goal + optional `/web` extract + optional `/memory` operator notes) |
| `registry_view.py` | Read-only merge of skills / tools / connectors |
| `tools_view.py` | Wired-tool inventory + ASCII diagram (`/tools`) |
| `skills_view.py` | Wired-skill inventory + ASCII diagram (`/skills`) |
| `web_search.py` | Allowlist-only GET for `/web` (off by default; no search engine) |
| `memory_notes.py` | Operator `/memory` notes (off by default; not RAG `memory/`) |
| `agent_policy.py` | Check-profile names for real-repo runs |
| `env_keys.py` | Allowlisted dotenv secret store behind `POST /api/keys`. `MANAGED_KEYS` is what stops that route from being an arbitrary-environment-injection primitive: only allowlisted names can be written. Writes `$CYCLAW_HOME/.env` atomically (mode 600 on POSIX) and returns presence plus a masked tail, never a value. File-only — nothing reads `.env` at runtime, so a write needs a restart to reach `gate.py`, which is why the response reports `restart_required` |
| `schemas.py` | Request models |

## Skills vs the governed registry

`GET /api/registry` is assembled by `registry_view.py`:

- repo skills from `.claude/skills/*/SKILL.md`
- governed store `data/agentic/skills_registry.json` (**read-only** here)
- MCP tool names AST-parsed from `mcp_hybrid_server.py` (never imported)

Writes to the JSON store stay behind `python -m agentic.cli apply-skill`.
See [`agentic/README.md`](../agentic/README.md) and
[`docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md`](../docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md).

The shipped registry file is empty. That is correct.

## Console usage

Type these in `harness.html` (they are **not** `/query` RAG commands).
`/loop` and `/web` never start `/api/agent/*`. `/memory` never writes
`soul.md`, `docs/memories/`, or the RAG `memory/` store. `/web` is **off** until you
turn it on, and even then it can GET only hosts you allowlisted.

### Inventory

```
/help
/skills                  # prompt-injected + /agent-check skills (wired only)
/skills all              # include the repo / governed catalog
/skills ponytail         # one-skill box
/tools                   # harness routes that are actually registered
/tools all               # include MCP hybrid_search (catalog only)
/tools goal              # one-tool box
```

Also dispatched by the console (see `static/harness.html`): `/session`,
`/soul`, `/model`, `/connectors` (hidden alias: `/registry`), `/github`,
`/agent`, `/harness`, `/tokens`, `/status`, `/users`, `/clear` — each maps
onto the `/api/*` routes in the table above.

### Goal + loop (local 27b)

```
/goal land the harness /web allowlist
/goal                    # show current
/loop 3                  # three chat turns toward the goal (default 3, cap 5)
/loop stop               # abort the in-flight Ollama socket
/goal clear
```

`/loop` is separately rate-limited (default 8 turns / 300s). Turns send
`{"loop": true}` without a client `max_tokens` value. The server controls the
per-turn generation budget: 2048 tokens by default, or the operator's existing
`api.harness_loop_rate_limit.max_tokens` override. Loop turns use a clipped
history window and share a process-wide single-generation lock with ordinary
chat so Metal is never double-booked. A loop without a goal is `400 LOOP_REQUIRES_GOAL`.

The browser separately caps each loop at 5 turns and stops further turns once
reported **aggregate completion usage** reaches 12,000 tokens. This check runs
after a reply, so the final turn can take the total above that threshold. It is
not a per-generation allowance and remains useful when the server's per-turn
budget is configured above its default.

### Operator memory (off by default)

Pinned notes live under the harness home (`memory/notes.json`). They enter
the system prompt **only** after `/memory on`. They are not soul.md, not
RAG facts, and not `docs/memories/` snapshots.

```
/memory                           # status: on/off + notes + read-only RAG flags
/memory add prefer ruff over flake8
/memory on                        # inject notes into the next chat turn
/memory forget <id>
/memory clear
/memory off
```

`/memory add` refuses empty text, a 20-note cap, and the same critical
injection patterns as a soul write. The RAG `memory:` block in
`config.yaml` is echoed as `rag.*` and `writable_from_harness` is always
`false`.

### Allowlist-only web (offline-safe)

There is no search engine and no browser. “Search” greps pages you already
allowlisted. Private / loopback / metadata IPs are refused at allow and
at fetch (DNS is checked at fetch time).

```
/web                              # status: enabled + allowlist (no page text)
/web allow https://docs.python.org/3/
/web allow docs.python.org        # host form; https assumed
/web on                           # persist enable; still fail-closed if allowlist empty
/web fetch https://docs.python.org/3/library/os.html
/web search pathlib               # scan allowlisted pages only
/web inject                       # last extract → next chat system prompt (untrusted)
# then type a normal question; the 27b sees the extract as read-only context
/web forget                       # drop the injected extract
/web deny docs.python.org
/web off
```

Allowing `https://docs.python.org/3/` does **not** allow
`https://docs.python.org/` or any other host. `/web fetch` against a
non-allowlisted URL is `WEB_HOST_DENIED`. `/web` off is `409 WEB_DISABLED`.
An allowlist URL preserves its scheme, host, optional non-default port, and
path; text responses are streamed and capped at 256 KiB.

Refused on purpose: `localhost`, `127.0.0.1`, RFC1918, link-local,
`169.254.169.254`, `user:pass@host`, `ftp://`, wildcards, redirects.

## `/api` — credentials

```
/api                          # masked status of every managed key
/api set GROK_API_KEY sk-...   # store one
```

Writes `$CYCLAW_HOME/.env` (default `~/.CyClaw/.env`, mode 600) — the same
file `macos/setup-cyclaw-keys.sh` manages, in the same `export KEY='value'`
form, so the two are interchangeable and unrelated lines survive a web write.

**It does not load the value into a running process.** Nothing in CyClaw reads
`.env` at runtime (there is no `python-dotenv` dependency); the operator's
shell sources it. So a save persists the key and `restart_required` comes back
true — restart `gate.py` for it to take effect. The status table labels each
key `env` (the process has it) or `file` (written, awaiting a restart).

`CYCLAW_API_KEY` is settable here too, and is deliberately *not* applied live:
`require_api_key` reads the environment per request, so writing it into the
running process would invalidate the credential the caller is currently using.
The response flags it under `self_auth_written`.

Values are never echoed back, never logged (the audit line records key NAMES
only), and the key name must be one of `harness.env_keys.MANAGED_KEYS` — an
arbitrary name would make this an environment-injection primitive.

## GitHub appendix — staged agent runs

Agentic runs remain disabled by default and separate from ordinary chat and
`/loop`. Stage optional run fields before the existing confirmation:

```text
/agent run codex/docs-fix update the documentation
/agent iterations 2
/agent pr 42
/agent confirm reviewed the requested documentation change
```

`/agent iterations <n>` accepts integers 1..10. `/agent pr <n>` and
`/agent issue <n>` accept positive integers that the browser can represent
exactly; setting either clears the other. Each command accepts `clear` to omit
that field from the request, restoring server defaults. Staging sends nothing;
the summary shows the selected values. The server still validates all fields
and may refuse a run that exceeds its budget even within the iteration range.

`/agent confirm <reason>` sends the staged values on `POST /api/agent/run` with
`confirm: true`. A loaded `/agent plan` remains plan **text** in that same body;
there is no separate plan endpoint.

`/agent approve <id>` fetches the current run before deciding. If its pending
diff has not been displayed in this console, or has changed since display, the
console shows it and asks you to repeat the approval command after review.
`/agent status <id>` also displays the diff. `/clear` and page reload forget
displayed diffs. A missing diff, an unavailable/truncated-diff notice, or a
non-pending run cannot be approved through the console. This is a console
review guard; server authorization still applies.

Approval commits locally. `/agent push <id>` is a separate action, followed by
`/agent publish <id> <reason>` with its own `confirm: true`. Nothing combines
approval, push, or publication. `/agent discard <id>` explicitly reclaims the
clone; refusals do not discard the staged proposal or trigger a retry.

## Operator API (loopback)

For `403 CROSS_ORIGIN_BLOCKED` or `403 CROSS_SITE_BLOCKED`, bookmark and fetch
the same host, scheme, and port. `localhost` and `127.0.0.1` are different browser
origins. Open `http://127.0.0.1:8790/` (or the configured harness port), not a
`file://` copy of the console. The console reports the refusal and does not
automatically switch hosts.

Guarded routes require the same Bearer `CYCLAW_API_KEY` as other admin
surfaces (`utils.auth.require_api_key`). The separate `/api/auth/*` block
below is different: it is the harness's own per-user login (Stage 6 of
`docs/AUTHENTICATION_DESIGN.md`), gated by a `cyclaw_harness_session` cookie
+ CSRF token rather than the API key, and returns `503 AUTH_DISABLED` unless
the shared `auth.enabled` config flag is `true`. The console's `/users`
slash command drives it.

`config.yaml`'s `security.api_key_optional` (default `false`, shared with
`gate.py`) can remove the `CYCLAW_API_KEY` requirement from every guarded
route below — including the agent run/push/publish routes — but **only for a
request that both has a loopback socket peer and carries no reverse-proxy
forwarding header** (`X-Forwarded-For`/`-Host`/`-Proto`, `X-Real-IP`,
`Forwarded`). Both halves are required: a proxy running on this host
terminates the remote connection and opens its own from loopback, so the peer
alone would hand the bypass to anyone who can reach the proxy. Header presence
is the signal; the value is attacker-controlled and is never parsed. A LAN
client gets 401 without a valid key regardless of what
`security.allowed_hosts` contains: that list validates the `Host` header on
requests that already arrived and opens no listening socket, and a `Host`
header is attacker-supplied anyway. The peer check is also why this holds
under `uvicorn harness.server:app --host 0.0.0.0`, which never runs `main()`'s
loopback guard. It never touches the `/api/auth/*` session system above.
`config.yaml`'s comment on the flag states the residual gap — a proxy that
strips those headers is indistinguishable from none — so read it, and the main
README's "API Key Setup" section, before enabling it.

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Console HTML — templated per request with a CSP nonce (`Content-Security-Policy`, `X-Frame-Options`, `Cache-Control: no-store`) and the per-process CSRF token |
| GET | `/static/*` | Shared static assets (`auth_admin.js` Users panel) |
| GET | `/api/status` | Health / config flags |
| GET | `/api/keys` | Allowlisted credentials: presence + masked tail only, never a value |
| POST | `/api/keys` | Store credentials in `$CYCLAW_HOME/.env` (mode 600). File-only: reports `restart_required` |
| GET | `/api/registry` | Merged skills / tools / connectors |
| GET | `/api/tools` | Wiring inventory + ASCII diagram for `/tools` |
| GET | `/api/skills` | Wiring inventory + ASCII diagram for `/skills` |
| GET | `/api/web` | `/web` status: enable flag + allowlist hosts (no page text) |
| POST | `/api/web` | Enable / disable allowlist-only fetch (`enabled`) |
| POST | `/api/web/allow` | Add a host or URL-prefix |
| POST | `/api/web/deny` | Remove one allowlist entry |
| POST | `/api/web/fetch` | GET one allowlisted URL (SSRF-checked, text only) |
| POST | `/api/web/search` | Scan allowlisted pages for a query (no search engine) |
| POST | `/api/web/inject` | Put last extract into the next chat system prompt |
| POST | `/api/web/forget` | Clear injected extract |
| GET | `/api/memory` | `/memory` status: enable flag + notes + read-only RAG flags |
| POST | `/api/memory` | Enable / disable prompt injection (`enabled`; default off) |
| POST | `/api/memory/add` | Pin one injection-scanned operator note |
| POST | `/api/memory/forget` | Drop one note by id |
| POST | `/api/memory/clear` | Drop every note |
| GET/POST | `/api/sessions` | List / create |
| GET | `/api/sessions/{id}` | One session |
| POST | `/api/sessions/{id}/rename` | Rename |
| POST | `/api/sessions/{id}/goal` | Set or clear the session `/goal` (empty string clears) |
| GET | `/api/soul` | Harness-local soul-in-prompt flag (does not write `soul.md`) |
| POST | `/api/soul` | Toggle that flag |
| POST | `/api/model` | Select local model |
| POST | `/api/chat` | Chat turn (`loop: true` for `/loop`; 409 `CHAT_BUSY` if a generation is already running) |
| POST | `/api/chat/cancel` | Abort the in-flight Ollama POST and release the generation gate (`/loop stop`; console also sends this on reload) |
| GET | `/api/github/status` | `gh` / agentic status via ops runner |
| GET | `/api/agent/checks` | Named check profiles |
| POST | `/api/agent/run` | Start a real-repo run; `409 AGENT_RUN_BUSY` if another run is already in progress, and again if it shares the local-model backend with an in-flight `/api/chat` turn |
| GET | `/api/agent/runs/{id}` | Run status |
| POST | `/api/agent/runs/{id}/decision` | Human approve / reject |
| POST | `/api/agent/runs/{id}/push` | Push agent branch |
| POST | `/api/agent/runs/{id}/publish` | Draft PR |
| POST | `/api/agent/runs/{id}/discard` | Reclaim clone |
| GET | `/api/harness/runs` | Local run list |
| GET | `/api/auth/setup-status` | First-run bootstrap check: whether the admin account still needs a password. Rate-limited; same-origin (curl with no Origin still allowed); no credential |
| POST | `/api/auth/bootstrap-password` | Sets the first admin password on a fresh install; open only until that password exists |
| POST | `/api/auth/login` | Session login (sets `cyclaw_harness_session` cookie); `503` when harness auth is off |
| POST | `/api/auth/logout` | Session logout |
| GET | `/api/auth/whoami` | Current session's username + role |
| GET | `/api/auth/users` | List accounts (admin/operator) |
| POST | `/api/auth/users` | Create an account (admin/operator; operator cannot create an admin) |
| POST | `/api/auth/users/{u}/password` | Reset a user's password |
| POST | `/api/auth/users/{u}/role` | Set a user's role (admin only) |
| DELETE | `/api/auth/users/{u}` | Delete an account (admin only) |

## Verify `/goal` + `/loop` + `/web`

Do not run the full swarm audit just to check these commands. Use
ladder **A** in [`.claude/skills/CyClaw-Sandbox/SKILL.md`](../.claude/skills/CyClaw-Sandbox/SKILL.md)
(operator map):

```bash
python3.12 -m pytest tests/test_harness.py tests/test_harness_web.py \
  tests/test_harness_console_contract.py tests/test_harness_auth.py \
  tests/test_harness_isolation.py -q --tb=short
python3.12 .claude/skills/CyClaw-Sandbox/harness_runtime_check.py
```

That is the contract: goal CRUD + prompt injection, `LOOP_REQUIRES_GOAL`,
dedicated loop limiter, `/loop stop` cancel, `/web` default-off + allowlist
+ SSRF refusals, HTML slash wiring, and I6 (harness never imports
`agentic/`). It does **not** prove live 27b quality, browser `/loop auto` +
`GOAL_DONE`, or a real fetch of an allowlisted host (tests use MockTransport).
