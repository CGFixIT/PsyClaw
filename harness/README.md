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
| `config.py` | Home layout + read-only view of repo `config.yaml` |
| `sessions.py` | Per-session JSON + token tallies |
| `ollama.py` | Local OpenAI-compatible chat client |
| `prompts.py` | System prompt (repo skills + optional soul + optional session goal + optional `/web` extract) |
| `registry_view.py` | Read-only merge of skills / tools / connectors |
| `tools_view.py` | Wired-tool inventory + ASCII diagram (`/tools`) |
| `skills_view.py` | Wired-skill inventory + ASCII diagram (`/skills`) |
| `web_search.py` | Allowlist-only GET for `/web` (off by default; no search engine) |
| `agent_policy.py` | Check-profile names for real-repo runs |
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
`/loop` and `/web` never start `/api/agent/*`. `/web` is **off** until you
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

### Goal + loop (local 27b)

```
/goal land the harness /web allowlist
/goal                    # show current
/loop 3                  # three chat turns toward the goal (default 3, cap 5)
/loop stop               # abort the in-flight Ollama socket
/goal clear
```

`/loop` is separately rate-limited (default 8 turns / 300s). Turns send
`{"loop": true}`, use a 1024-token output budget and a clipped history
window, and share a process-wide single-generation lock with ordinary chat
so Metal is never double-booked. A loop without a goal is `400 LOOP_REQUIRES_GOAL`.

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

Refused on purpose: `localhost`, `127.0.0.1`, RFC1918, link-local,
`169.254.169.254`, `user:pass@host`, `ftp://`, wildcards, redirects.

## Operator API (loopback)

Guarded routes require the same Bearer `CYCLAW_API_KEY` as other admin
surfaces (`utils.auth.require_api_key`).

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Console HTML |
| GET | `/api/status` | Health / config flags |
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
| GET/POST | `/api/sessions` | List / create |
| GET | `/api/sessions/{id}` | One session |
| POST | `/api/sessions/{id}/rename` | Rename |
| POST | `/api/sessions/{id}/goal` | Set or clear the session `/goal` (empty string clears) |
| GET | `/api/soul` | Harness-local soul-in-prompt flag (does not write `soul.md`) |
| POST | `/api/soul` | Toggle that flag |
| POST | `/api/model` | Select local model |
| POST | `/api/chat` | Chat turn (`loop: true` for `/loop`; 409 `CHAT_BUSY` if a generation is already running) |
| POST | `/api/chat/cancel` | Abort the in-flight Ollama POST (`/loop stop`) |
| GET | `/api/github/status` | `gh` / agentic status via ops runner |
| GET | `/api/agent/checks` | Named check profiles |
| POST | `/api/agent/run` | Start a real-repo run |
| GET | `/api/agent/runs/{id}` | Run status |
| POST | `/api/agent/runs/{id}/decision` | Human approve / reject |
| POST | `/api/agent/runs/{id}/push` | Push agent branch |
| POST | `/api/agent/runs/{id}/publish` | Draft PR |
| POST | `/api/agent/runs/{id}/discard` | Reclaim clone |
| GET | `/api/harness/runs` | Local run list |

## Verify `/goal` + `/loop` + `/web`

Do not run the full 14-phase swarm just to check these commands. Use
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

