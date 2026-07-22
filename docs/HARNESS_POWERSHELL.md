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
path; `CYCLAW_HARNESS_PORT` overrides the port.

## The console

Slash commands (type `/help` in the console):

| Command | Action |
|---|---|
| `/session new\|list\|use\|rename\|info` | chat session management |
| `/soul on\|off\|status` | include the governed soul in the system prompt (read-only; `soul.md` writes stay with `utils.personality`) |
| `/model [use <name>]` | show / select the local model |
| `/skills`, `/tools`, `/connectors` | merged registry views |
| `/github` | agentic GitHub status (read-only subprocess) |
| `/harness` | harness optimizer runs |
| `/tokens` | per-session token tally |
| `/status` | server status |
| `/clear` | clear the console |

Every chat reply shows the model name and the prompt/completion token counts
reported by Ollama; the header bar keeps a running tally across sessions.

## Agent system prompt

Chat calls compose the system prompt from the repo's own discipline skills —
`.claude/skills/ponytail/SKILL.md` (the seven lazy-senior-dev rules) and
`.claude/skills/karpathy-guidelines/SKILL.md` — with frontmatter stripped, so
the same contracts that govern human/agent work in this repo govern the
harness agent. When soul is enabled, the governed soul fragment is appended
read-only.

## Security posture

- Loopback-only bind (`127.0.0.1`); the server refuses any non-loopback host.
- The chat client refuses non-loopback model endpoints.
- Session IDs are server-generated hex; path traversal is rejected.
- No shell execution from the console; GitHub actions go through the
  whitelisted `utils.ops_runner` subprocess shim.
- The console renders all model output via `textContent` (no HTML injection).
