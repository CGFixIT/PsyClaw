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
  `data/agentic/skills_registry.json` (read-only view).
- **Local models** — Ollama via the OpenAI-compatible `local_llm.base_url`
  from `config.yaml`; no keys, no login, offline.

## Install

```powershell
# From a CyClaw clone:
powershell -ExecutionPolicy Bypass -File .\powershell\Install-CyClaw.ps1

# Or let the installer clone origin main itself — just run the script.
# Options: -RepoPath C:\src\CyClaw  -SkipPythonDeps  -NoProfileEdit  -NoPathEdit
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

## Home layout (`%USERPROFILE%\.CyClaw`)

| Path | Contents |
|---|---|
| `config.json` | selected model, soul on/off, port |
| `sessions/` | one JSON per chat session: messages + token tally |
| `skills/` | user-visible copy of `.claude/skills` (seeded once) |
| `tools/` | connector/tool state |
| `memory/` | harness memory log (NOT the governed soul) |
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
| `/session new\|list\|use\|rename\|info` | chat session management |
| `/soul on\|off\|status` | include the governed soul in the system prompt (read-only; `soul.md` writes stay with `utils.personality`) |
| `/model [use <name>]` | show / select the local model |
| `/skills`, `/tools`, `/connectors` | merged registry views |
| `/github` | agentic GitHub status (read-only subprocess) |
| `/agent run\|confirm\|cancel` | stage, authorize, or discard a real-repo coding run |
| `/agent status\|approve\|reject <id>` | read a run record, or decide a pending one |
| `/agent checks` | list the selectable verification profiles |
| `/harness` | harness optimizer runs |
| `/tokens` | per-session token tally |
| `/status` | server status |
| `/clear` | clear the console |

Every chat reply shows the model name and the prompt/completion token counts
reported by Ollama; the header bar keeps a running tally across sessions.

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
2. `/agent confirm <reason>` authorizes it. This is the request that clones the
   repo, asks the local model for a patch, and runs the selected verification
   profile against the result. **It blocks for up to 15 minutes** — the run
   record is written only when the run ends, so there is no intermediate
   progress to poll for, and the run id first exists in that response.
3. On success the run stops *before committing* and reports
   `status: pending_decision`. `/agent approve <id>` is what actually commits;
   `/agent reject <id>` discards the clone. Neither pushes.

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
- The five state-changing routes (`POST /api/sessions`, `.../rename`,
  `/api/soul`, `/api/model`, `/api/chat`), `GET /api/github/status`, and all
  three `/api/agent/*` run routes require a
  Bearer `CYCLAW_API_KEY` — the same variable the gateway's `/soul` and `/ops/*`
  endpoints use. **Fail-closed:** an unset key means those routes return 401, not
  "no auth required". Paste the key into the console's `key` field, or export it
  before launching. The read-only routes stay open so the console can boot and
  report that a key is needed. The key is held in the browser page only — never
  `localStorage`, never a cookie.
- Those same routes reject browser cross-site requests via `Origin` /
  `Sec-Fetch-Site`. Requests carrying neither header (curl, PowerShell, the
  sandbox verifier) are allowed — a non-browser client is not a CSRF vector.
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
  before it can become a `--run-id=` argv element, and branch names must be in
  the `claude/` namespace.
- The console renders all model output via `textContent` (no HTML injection).
