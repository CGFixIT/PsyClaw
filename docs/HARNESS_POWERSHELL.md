# CyClaw PowerShell Coding Harness

A grok-build / kimi-code style local coding harness for Windows 10, Windows 11,
and Windows Server 2019–2022. After setup, running `cyclaw` in any PowerShell
window starts the harness control plane (loopback only) and opens the
slash-command-driven console at `http://127.0.0.1:8790`.

The harness is a strictly out-of-band package (`harness/`): like `agentic/`,
`sync/`, and `guardrails/`, it is never imported by `gate.py`, `graph.py`, or
`mcp_hybrid_server.py` and never imports them (invariant I6). It reuses the
existing subsystems rather than duplicating them:

- **GitHub coding agent** — `agentic/` via `python -m agentic.cli`, driven
  through the same `utils.ops_runner` subprocess shim the `/ops/agentic`
  endpoint uses. Read mode by default; writes stay behind the governed
  `propose-skill` / `apply-skill` human-reason gate.
- **Harness optimizer** — `agentic/harness_optimizer/` run artifacts under
  `data/agentic/harness_optimizer/runs/` surface in the console via `/harness`.
- **Skills registry** — `.claude/skills/*/SKILL.md` plus the governed
  `data/agentic/skills_registry.json` (read-only view). `/skills` in the
  console is a **wiring diagram** of what this process actually injects or
  runs as `/agent checks`, not a dump of every SKILL.md.
- **Allowlist-only web** — `/web` (off by default). GET of operator-allowlisted
  http(s) hosts only; no search engine, no browser, no private/loopback IPs.
- **Local models** — Ollama via the OpenAI-compatible `local_llm.base_url`
  from `config.yaml`; no keys, no login, offline.

## Install

```powershell
# From a CyClaw clone:
powershell -ExecutionPolicy Bypass -File .\powershell\Install-CyClaw.ps1

# Or let the installer clone origin main itself — just run the script.
# Options: -RepoPath C:\src\CyClaw  -SkipPythonDeps  -NoProfileEdit  -NoPathEdit  -ReplaceRepo
```

The installer: creates `%USERPROFILE%\.CyClaw`, clones or links the repo,
creates a venv and installs dependencies (CPU torch first, then
`requirements.txt -c constraints.txt`, matching the documented trap-avoidance
order), writes the `cyclaw.cmd` shim, adds the shim directory to the user
PATH, and registers a `cyclaw` function in the PowerShell profile. Works on
Windows PowerShell 5.1 (the default on Windows 10/11 and Server 2019/2022)
and PowerShell 7+.

Uninstall (keeps data by default):

```powershell
.\powershell\Uninstall-CyClaw.ps1            # remove PATH/profile hooks only
.\powershell\Uninstall-CyClaw.ps1 -RemoveHome # also delete ~/.CyClaw (prompts)
```

`powershell\Invoke-CyClaw.ps1` (the `cyclaw` shim's launcher) falls back to
a system `python` on `PATH` when `%USERPROFILE%\.CyClaw\venv\Scripts\python.exe`
is absent (e.g. after `Install-CyClaw.ps1 -SkipPythonDeps`) — it only throws
if neither the venv nor a system Python is found. If the harness behaves
unexpectedly (wrong dependency versions, missing packages), check which
interpreter actually ran: a system Python without CyClaw's pinned deps will
silently answer `python -m harness.server` in place of the venv's.

## Home layout (`%USERPROFILE%\.CyClaw`)

| Path | Contents |
|---|---|
| `config.json` | selected model, soul on/off, **web on/off**, port |
| `sessions/` | one JSON per chat session: messages + token tally + optional `/goal` |
| `skills/` | user-visible copy of `.claude/skills` (seeded once) |
| `tools/` | `/web` allowlist + last extract (`web_allowlist.json`, `web_last.json`, `web_context.txt`) |
| `memory/` | harness-local log (NOT the governed `memory/` package or `soul.md`) |
| `repo/` | the CyClaw checkout (when the installer cloned) |
| `venv/` | the Python environment |
| `bin/` | `cyclaw.cmd` + `Invoke-CyClaw.ps1` |

`CYCLAW_HOME` overrides the home location; `CYCLAW_REPO` overrides the repo
path; `CYCLAW_HARNESS_PORT` overrides the port. `CYCLAW_API_KEY` authenticates
the state-changing routes — passed through from the caller's environment, never
generated or written to disk by the launcher.

## The console

Slash commands (type `/help` in the console):

| Command | Action |
|---|---|
| `/help` | list the available commands |
| `/session new\|list\|use\|rename\|info` | chat session management |
| `/soul on\|off\|status` | include the governed soul in the system prompt (read-only; `soul.md` writes stay with `utils.personality`) |
| `/memory [on\|off\|add\|forget\|clear]` | operator notes in the system prompt (**off by default**; not RAG `memory/`, not `soul.md`) |
| `/api [set <KEY> <value>]` | view or store allowlisted CyClaw API keys in `$CYCLAW_HOME/.env` (mode 600 on POSIX); file-only, so a write reports `restart_required` — see `harness/README.md` § `/api` |
| `/goal [text]\|clear` | session-scoped intent, injected into the chat prompt (not a write authorization) |
| `/loop [n]\|stop\|auto` | human-gated chat turns toward `/goal` (never starts `/api/agent/*`) |
| `/model [use <name>]` | show / select the local model |
| `/skills [all\|<name>]` | wiring diagram of skills this console actually injects or runs as `/agent checks` |
| `/tools [all\|<name>]` | wiring diagram of harness routes that are registered; MCP `hybrid_search` is catalog-only |
| `/web [on\|off\|allow\|deny\|fetch\|search\|inject\|forget]` | allowlist-only GET; **off by default**; no search engine, no browser |
| `/connectors` | connector catalog |
| `/github` | agentic GitHub status (read-only subprocess) |
| `/agent run\|plan\|read\|confirm\|cancel` | stage, refine, authorize, or discard a real-repo coding run |
| `/agent status\|approve\|reject <id>` | read a run record, or decide a pending one |
| `/agent plan [clear]` | load or clear a reviewed local `.md` / `.txt` plan for the staged run |
| `/agent read <repo-relative-path>` | declare an existing cloned-repo file for coder context (`clear` removes all) |
| `/agent checks [profile ...]` | list or choose allow-listed verification profiles for the staged run |
| `/harness` | harness optimizer runs |
| `/tokens` | per-session token tally |
| `/status` | server status |
| `/users` | manage CyClaw accounts (same user store as the gate's `/auth/*`; separate harness-local session login at `/api/auth/*`) |
| `/clear` | clear the console |

Every chat reply shows the model name and the prompt/completion token counts
reported by Ollama; the header bar keeps a running tally across sessions.

Copy-paste examples for `/goal`, `/loop`, `/skills`, `/tools`, and `/web`
(including the fail-closed `/web` refusals) live in
[`harness/README.md`](../harness/README.md) § Console usage. `/loop` is
separately rate-limited for a local 27b; `/web` can GET only hosts you
allowlisted, and only after `/web on`.

### Agentic coding runs

`/agent` drives `agentic/real_repo_loop.py`'s two-step gate from the console.
Two `config.yaml` flags govern it, and they refuse at **different points** —
worth knowing before you treat either as an off switch:

- `agentic.enabled: false` (the shipped default) short-circuits immediately.
  Nothing is fetched, nothing is cloned; the console reports the layer is
  disabled.
- `deepagent_github.allow_git_write_tools: false` (also the shipped default)
  refuses **after** the `gh` context fetch and the full `git clone` have already
  happened — the gate lives inside the loop, not ahead of it. The run reports
  `write_refused`, and the clone is discarded, but a network round-trip and a
  working copy were spent getting there. Leave `agentic.enabled` false if you
  want the hard off switch.

1. `/agent run claude/<topic> <what the agent should do>` stages a proposal and
   prints it. Nothing is sent.
2. Optionally, `/agent plan` opens the browser's native picker for a reviewed
   local plan. The console retains only the selected text, never a server-side
   path; reselect the file after editing it. `/agent read <repo-relative-path>`
   declares existing clone content the coder may see, and `/agent checks pytest ruff`
   replaces the default profile list. These values stay staged until confirmation.
3. `/agent confirm <reason>` authorizes it. This is the request that clones the
   repo, asks the local model for a patch, and runs the selected verification
   profile against the result. **It blocks for up to 15 minutes** — the run
   record is written only when the run ends, so there is no intermediate
   progress to poll for, and the run id first exists in that response.
4. On success the run stops *before committing* and reports
   `status: pending_decision`. `/agent approve <id>` is what actually commits;
   `/agent reject <id>` discards the clone. Neither pushes.
5. Escalating past the local commit is two further, separate decisions —
   deliberately not folded into approve, and each its own route:
   `/agent push <id>` puts the branch on origin, and
   `/agent publish <id> <why>` opens a draft PR. **Both refuse on a shipped
   checkout, but not for the same reason:** push needs
   `deepagent_github.allow_git_write_tools` (ships `false`), while publish's
   code-level gate `EXECUTION_ENABLED` ships `True` since the operator
   enablement of 2026-08-07 — publish is held by `agentic.enabled` (ships
   `false`) plus its per-call reason/confirm. See the filed checklist in
   `docs/agentic/GITHUB_WRITE_ENABLEMENT.md`, including the
   `CYCLAW_AGENTIC_WRITE_DISABLE` rollback.
6. `/agent discard <id>` reclaims the clone. It is the only step that frees
   disk: an approved run keeps its clone on purpose (push and publish still
   need it) and nothing reclaims it automatically, so a console session that
   only ever approves accumulates one full repository clone per run.

`checks` names a profile from `/agent checks`, never a command. The console
cannot send an argv to execute: profile names are resolved against the
allow-list in `harness/agent_policy.py`, because the executor runs each check
as a real subprocess with the parent `PATH`.

## Agent system prompt

Chat calls compose the system prompt from the repo's own discipline skills —
`.claude/skills/ponytail/SKILL.md` (the seven lazy-senior-dev rules) and
`.claude/skills/karpathy-guidelines/SKILL.md` — with frontmatter stripped, so
the same contracts that govern human/agent work in this repo govern the
harness agent. When soul is enabled, the governed soul fragment is appended
read-only.

## Security posture

- Loopback-only bind (`127.0.0.1`); the server refuses any non-loopback host.
- Every state-changing route under `/api/*` — session create/rename/goal,
  `/api/soul`, `/api/model`, `POST /api/memory` and its sub-routes, `POST /api/web`
  and its six sub-routes (`GET /api/web` is deliberately open so the console can
  boot), `/api/chat` +
  `/api/chat/cancel`, `POST /api/keys` (writes credentials into
  `$CYCLAW_HOME/.env`), and all six `/api/agent/*` run routes (`run`,
  `runs/{id}`, `runs/{id}/decision`, `runs/{id}/push`, `runs/{id}/publish`,
  `runs/{id}/discard`) — plus the reads that return more than a summary,
  `GET /api/sessions/{session_id}` (full message content, unlike the
  title-only list at `GET /api/sessions`), `GET /api/memory`, `GET /api/keys` (which
  credentials are set, plus a masked tail — never a value), and `GET /api/github/status`,
  require a Bearer `CYCLAW_API_KEY` — the same variable the gateway's
  `/soul` and `/ops/*` endpoints use. **Fail-closed:** an unset key means
  those routes return 401, not "no auth required" — with one deliberate
  exception, `config.yaml`'s `security.api_key_optional` (default `false`),
  which drops the key requirement from every route above, `/api/keys`
  included, but **only for a request that both has a loopback socket peer and
  carries no reverse-proxy forwarding header** (`X-Forwarded-For`/`-Host`/
  `-Proto`, `X-Real-IP`, `Forwarded`) — a proxy running on this host
  terminates the remote connection and opens its own from loopback, so the
  peer alone would hand the bypass to anyone who can reach the proxy. A LAN
  client still gets 401, and so does a request arriving through a forwarding
  proxy. See `config.yaml`'s comment on that flag for the full treatment,
  residual gap included. Paste the key into the
  console's `key` field, or export it before launching. The read-only
  routes stay open so the console can boot and report that a key is
  needed. The key is held in the browser page only — never `localStorage`,
  never a cookie. The separate `/api/auth/*` block (`/users` command) uses
  its own `cyclaw_harness_session` cookie + CSRF token instead of this key
  — see `docs/AUTHENTICATION_DESIGN.md`.
- Those same routes reject browser cross-site requests via `Origin` /
  `Sec-Fetch-Site`. Requests carrying neither header (curl, PowerShell, the
  sandbox verifier) are allowed — a non-browser client is not a CSRF vector.
- A per-process CSRF token, minted fresh at server start and embedded only in
  the page `GET /` serves, is required on every one of those same routes —
  unlike the `Origin`/`Sec-Fetch-Site` check above, there is **no** carve-out
  for a header-less request. This closes the residual gap that check leaves:
  a local process holding `CYCLAW_API_KEY` but sending neither browser header
  still needs to have fetched the console page to have the token. Scripted
  operator tools (`.claude/skills/CyClaw-Sandbox/harness_emulation.py`, etc.)
  fetch `GET /` once and parse the token out of the page the same way the
  console's own `api()` helper does.
- The chat client refuses non-loopback model endpoints.
- Session IDs are server-generated hex; path traversal is rejected.
- No shell execution from the console; GitHub actions go through the
  whitelisted `utils.ops_runner` subprocess shim.
- A coding run's verification commands are **never** taken from the request.
  The console sends a profile name; `harness/agent_policy.py` maps it to a
  fixed argv. `agentic/executor` runs each check as a real subprocess inheriting
  the parent `PATH`, and nothing downstream inspects `argv[0]`, so accepting a
  caller-supplied command would make an authenticated route a remote shell.
- `run_id` is validated as anchored 32-char lowercase hex at the HTTP boundary,
  before it can become a `--run-id=` argv element, and branch names must use one of
  the accepted vendor prefixes — `claude/`, `codex/`, `grok/`, `kimi/`, `CyClaw/`,
  `cyclaw/`, `agent/`, plus any `CYCLAW_AGENT_BRANCH_PREFIX` override — validated
  against `utils/agent_identity.BRANCH_NAME_RE`.
- A named-capability **ToolBroker** gate (`utils.tool_broker.assert_allowed`) sits
  inside `/api/chat` when `loop: true`, inside the agent-run route, and inside
  `/api/web/fetch` and `/api/web/search`. It is independent of the API key and
  the CSRF check — passing those does not pass this. The error contract differs
  by route: loop-chat and the agent-run route surface a denial as `403
  TOOL_DENIED`, while `harness/web_search.py`'s `WebTool._gate_tool` wraps the
  same `ToolDenied` into `WebToolError(code="WEB_TOOL_DENIED")`, which
  `harness/server.py`'s `_web_err` maps to `400` (its default status) — not
  `403`. Every denial is audited regardless of status code.
- The console renders all model output via `textContent` (no HTML injection).
