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
| `prompts.py` | System prompt (repo skills + optional soul) |
| `registry_view.py` | Read-only merge of skills / tools / connectors |
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

## Operator API (loopback)

Guarded routes require the same Bearer `CYCLAW_API_KEY` as other admin
surfaces (`utils.auth.require_api_key`).

| Method | Path | Notes |
|---|---|---|
| GET | `/` | Console HTML |
| GET | `/api/status` | Health / config flags |
| GET | `/api/registry` | Merged skills / tools / connectors |
| GET/POST | `/api/sessions` | List / create |
| GET | `/api/sessions/{id}` | One session |
| POST | `/api/sessions/{id}/rename` | Rename |
| GET | `/api/soul` | Harness-local soul-in-prompt flag (does not write `soul.md`) |
| POST | `/api/soul` | Toggle that flag |
| POST | `/api/model` | Select local model |
| POST | `/api/chat` | Chat turn |
| GET | `/api/github/status` | `gh` / agentic status via ops runner |
| GET | `/api/agent/checks` | Named check profiles |
| POST | `/api/agent/run` | Start a real-repo run |
| GET | `/api/agent/runs/{id}` | Run status |
| POST | `/api/agent/runs/{id}/decision` | Human approve / reject |
| POST | `/api/agent/runs/{id}/push` | Push agent branch |
| POST | `/api/agent/runs/{id}/publish` | Draft PR |
| POST | `/api/agent/runs/{id}/discard` | Reclaim clone |
| GET | `/api/harness/runs` | Local run list |
