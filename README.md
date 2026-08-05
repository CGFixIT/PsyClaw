# CyClaw: Local AI you can Trust.

> Offline-first, RAG-enforced, $ecure Local AI chatbot / agentic coding harness w/ Dropbox Sync for RAG data/corpus/*.md/*.txt

[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.139-blue.svg)](https://fastapi.tiangolo.com/)
[![LangGraph](https://img.shields.io/badge/LangGraph-1.2.9-blue.svg)](https://github.com/langchain-ai/langgraph)
[![PGvector](https://img.shields.io/badge/PGvector-0.4.2-blue.svg)](https://github.com/pgvector/pgvector/)
[![CodeQL Advanced](https://github.com/CGFixIT/CyClaw/actions/workflows/codeql.yml/badge.svg)](https://github.com/CGFixIT/CyClaw/actions/workflows/codeql.yml)
[![CyClaw CI/CD testing](https://github.com/cgfixit/CyClaw/actions/workflows/ci.yml/badge.svg)](https://github.com/cgfixit/CyClaw/actions/workflows/ci.yml)
[![CyClaw Conda CI](https://github.com/cgfixit/CyClaw/actions/workflows/python-package-conda.yml/badge.svg)](https://github.com/cgfixit/CyClaw/actions/workflows/python-package-conda.yml)
[![Gitleaks Secret Scan](https://github.com/CGFixIT/CyClaw/actions/workflows/gitleaks.yml/badge.svg)](https://github.com/CGFixIT/CyClaw/actions/workflows/gitleaks.yml)
[![OSV-Scanner](https://github.com/CGFixIT/CyClaw/actions/workflows/osv-scanner.yml/badge.svg)](https://github.com/CGFixIT/CyClaw/actions/workflows/osv-scanner.yml)

[![Screenshots: local AI](https://raw.githubusercontent.com/cgfixit/CyClaw/refs/heads/main/docs/screenshots/IMG_3630.jpeg)](https://github.com/CGFixIT/CyClaw/tree/main/docs/screenshots)

[![Screenshots: local AI](https://i.imgur.com/N7hPPez.png)](https://github.com/CGFixIT/CyClaw/tree/main/docs/screenshots)


## Table of Contents

- [What It Does](#what-it-does)
- [Architecture](#architecture)
- [API Key Setup (Soul Mutations)](#api-key-setup-soul-mutations)
- [Quick Start](#quick-start)
- [Full Setup Guide](setup-guide.md)
- [Project Structure](#project-structure)
- [Dropbox Corpus Sync](#dropbox-corpus-sync)
- [NeMo Guardrails](#nemo-guardrails-v18)
- [GitHub Agentic Coding Harness](#github-agentic-coding-harness-v19)
- [Security Model](https://github.com/cgfixit/CyClaw/tree/main/docs/security-philosophy)
- [Remaining Work](remaining_work.md) 
- [Archive & Roadmap](docs/ARCHIVE_AND_ROADMAP.md) 

---

## What It Does

CyClaw is a personal RAG (Retrieval-Augmented Generation) backend that:

1. **Answers questions exclusively from your local Markdown corpus** — no internet by default
2. **Enforces every safety invariant via LangGraph topology** — not prompts, not config flags, not discipline
3. **Maintains a persistent soul/personality layer** (`soul.md`) with SHA-256 drift detection, atomic evolution writes, and user-gated modification
4. **Falls back to an external LLM only with explicit user confirmation** in hybrid mode — Grok (xAI) or Claude (Anthropic), selected per-query, each independently triple-gated at config, env, and per-query level
5. **Exposes both a FastAPI HTTP gateway and an MCP server** for Claude Desktop / Copilot Studio integration
6. **Ships optional, out-of-band operator layers** for Dropbox corpus sync (`sync/`) and agentic GitHub context / governed local workflows (`agentic/`, `.claude/`) — never imported into the request path, now also drivable from the browser terminal via governed **Sync** and **Agentic** consoles
7. **Extends the agentic layer to local data** (v1.8) with an opt-in **filesystem connector** (`agentic/fsconnect/` — scoped reads + gated writes over local/SMB shares, TOCTOU-safe) and a read-only **SQL connector** (`agentic/sqlconnect/` — SELECT-only Postgres/MSSQL scaffold) — both disabled by default and out-of-band
8. **Adds an optional NeMo Guardrails content-safety layer** (v1.8, `guardrails/`) that soft-imports `nemoguardrails` and degrades to offline heuristic rails — defense-in-depth only, never a routing authority (graph topology stays the sole policy)
9. **Scaffolds an optional LangChain Deep Agents / governed harness-optimizer layer** (v1.9, `agentic/deepagent_github/` + `agentic/harness_optimizer/`) — opt-in, disabled by default, and out-of-band like every other agentic feature above; phases 0-9 are implemented and tested — phases 0-5 (config, workspace tools, mock scoring/acceptance gate) plus phases 6-9 (real subagent wiring, fixture-based GitHub coding evaluator, governed propose/apply), which landed in PR #515 (2026-07-13). **Superseded by item 11 below:** P10 has since landed a real draft-PR write path and a sandboxed verification executor — both still shipped disarmed
10. **Ships a local PowerShell coding-harness console** (v1.9, `harness/` + `powershell/`, merged 2026-07-22) — a grok-build-style slash-command console on `127.0.0.1:8790` chatting with the local model over the OpenAI-compatible endpoint, with per-session token tallies, a seeded skills catalog under `%USERPROFILE%\.CyClaw`, and the same I6 isolation as every other out-of-band layer
11. **Adds a real-repo GitHub agentic coding harness** (v1.9, `agentic/real_repo_loop.py` + `agentic/executor/`) — clone → plan → patch → verify → **human decides** → commit, with pushing a `claude/*` branch and opening a *draft* PR as two further separate decisions; a diff-scope gate refuses candidates that rewrite the tests judging them, verification runs as sandboxed argv-list subprocesses, and every gate ships closed (the draft-PR step behind a hardcoded `EXECUTION_ENABLED = False` that no config file can flip)

---

## Architecture

```
User Query (HTTP POST /query or MCP tool call)
         │
         ▼
    ┌─────────────────────────────────────────────────────┐
    │  gate.py  (FastAPI, 127.0.0.1:8787)                 │
    │  • Rate limit (60 req/min per IP — RUNS FIRST)      │
    │  • Injection filter (sanitizer.py, config-driven)   │
    │  • Soul init (PersonalityManager closure)           │
    │  • Telemetry kill block (before any SDK import;     │
    │    shared — MCP + indexer apply the same block)     │
    └──────────────────┬──────────────────────────────────┘
                       │
                       ▼
    ┌─────────────────────────────────────────────────────┐
    │  graph.py  (LangGraph 10-node State Machine)        │
    │                                                     │
    │  [ENTRY]                                            │
    │     ↓                                               │
    │  1. retrieve  (Chroma + BM25 + RRF fusion)          │
    │     ↓                                               │
    │  2. route_by_score  (top_score >= 0.028 RRF?)       │
    │     ├─ YES ──→ 3. guardrail_input (offline rail;    │
    │     │           opt-in, pass-through when disabled) │
    │     │           blocked ──→ 9. audit_logger          │
    │     │           passed  ──→ 4. local_llm             │
    │     │                        (Ollama :11434)        │
    │     └─ NO  ──→ 5. user_gate (needs_confirm=true)    │
    │                    ├─ not yet answered ──→          │
    │                    │      9. audit_logger           │
    │                    ├─ confirmed + hybrid ──→        │
    │                    │      6. grok_fallback OR       │
    │                    │         claude_fallback        │
    │                    └─ declined / offline ──→        │
    │                       3. guardrail_input (again)    │
    │                           blocked ──→ 9. audit_logger│
    │                           passed  ──→                │
    │                           7. offline_best_effort    │
    │     ↓ (all four answer nodes converge)              │
    │  8. guardrail_output (offline output rail; opt-in;  │
    │     grounding check applies to local_llm answer only)│
    │     ↓                                               │
    │  9. audit_logger (SHA-256 + PII redact → jsonl)     │
    │     ↓                                               │
    │  [END]                                              │
    └─────────────────────────────────────────────────────┘
                       │
                       ▼
    ┌─────────────────────────────────────────────────────┐
    │  HybridRetriever  (retrieval/hybrid_search.py)      │
    │  • ChromaDB  (semantic, all-MiniLM-L6-v2, 384d)    │
    │  • BM25Okapi (keyword, Porter stemming)             │
    │  • RRF fusion (k=60, equal 1.0/1.0 weighting)      │
    │  • Per-chunk provenance metadata in every result    │
    └─────────────────────────────────────────────────────┘
```

### LangGraph Topology (rendered)

```mermaid
flowchart TD
    A(["🌐 Client\nHTTP POST /query\nor MCP tool call"])
    A --> B

    subgraph GATEWAY ["gate.py — FastAPI 127.0.0.1:8787"]
        B["TrustedHostMiddleware\nHost header allowlist"]
        B --> C["Rate Limiter\n60 req/min per IP"]
        C --> D["Prompt Injection Filter\n40 patterns · config-driven · lru_cache"]
        D --> E["Build GraphState\nquery + user_confirmed_online"]
    end

    E --> F

    subgraph GRAPH ["graph.py — LangGraph 10-node State Machine"]
        F(["① retrieve\nChroma + BM25 + RRF"])
        F --> G["② route_by_score\ntop_score ≥ 0.028?"]
        G -->|"YES — local context"| X["③ guardrail_input\noffline rail · opt-in\npass-through when disabled"]
        X -->|"blocked"| L
        X -->|"passed · high score"| H["④ local_llm\nOllama :11434\nqwen3.6:27b"]
        G -->|"NO — vault miss"| I["⑤ user_gate\nneeds_confirm = true"]
        I -->|"confirmed=true + hybrid\n+ grok.enabled + provider=grok"| J["⑥ grok_fallback\nxAI grok-4.5\ntriple-gated · not railed"]
        I -->|"confirmed=true + hybrid\n+ claude.enabled + provider=claude"| W["⑦ claude_fallback\nAnthropic claude-sonnet-5\ntriple-gated · not railed"]
        I -->|"confirmed=false\nor offline mode"| X
        X -->|"passed · vault miss"| K["⑧ offline_best_effort\nlocal LLM · no RAG gate"]
        I -->|"confirmed=None — PAUSE\nreturn needs_confirm to the client"| L
        H --> Y["⑨ guardrail_output\noffline rail · opt-in\ngrounding check: local_llm only"]
        J --> Y
        W --> Y
        K --> Y
        Y --> L
        L(["⑩ audit_logger\nSHA-256 hash · PII redact\n→ logs/audit.jsonl"])
    end

    L --> M(["📤 QueryResponse\nanswer · sources · model_used\nretrieval_mode · needs_confirm"])

    subgraph RETRIEVAL ["retrieval/hybrid_search.py"]
        N["ChromaDB\nsemantic · 384-dim cosine"]
        O["BM25Okapi\nkeyword · Porter stemming"]
        P["RRF fusion\nk=60 · equal weighting"]
        N --> P
        O --> P
    end

    F <-->|"hybrid search"| P

    subgraph SOUL ["utils/personality.py"]
        Q["soul.md\nSHA-256 drift detection"]
        R["SQLite / Postgres\nversion history · TTL prune"]
        Q <--> R
    end

    H <-->|"soul preamble\n≤ 8000 chars"| Q
    K <-->|"soul preamble"| Q

    subgraph OOB ["Out-of-band — never imported by gate/graph/MCP"]
        S["agentic/cli.py\nGitHub read ops"]
        T["agentic/fsconnect/\nscoped FS read/write"]
        U["sync/cli.py\nDropbox corpus pull"]
        V["guardrails/\nNeMo rails skeleton"]
    end

    style GATEWAY fill:#1a3a5c,color:#ffffff,stroke:#4a90d9
    style GRAPH fill:#1a3a2a,color:#ffffff,stroke:#4a9d5a
    style RETRIEVAL fill:#3a2a1a,color:#ffffff,stroke:#d9904a
    style SOUL fill:#3a1a3a,color:#ffffff,stroke:#d94ad9
    style OOB fill:#2a2a2a,color:#aaaaaa,stroke:#666666,stroke-dasharray:5 5
    style J fill:#5c1a1a,color:#ffffff
    style W fill:#5c1a1a,color:#ffffff
    style L fill:#1a1a3a,color:#ffffff
```

---

## API Key Setup (Soul Mutations)

CyClaw's soul mutation endpoints (`/soul/propose`, `/soul/apply`, `/soul/reload`, `/soul/restore`) require a **Bearer API key**. Without it they return `HTTP 401` immediately — intentional fail-closed behavior.

> **All `/soul/*` endpoints — including `GET /soul` — require a valid `Authorization: Bearer <key>` token.** Only `/health`, `/query`, and the console pages (`GET /`, `/static/*`) are unauthenticated.

### macOS — zsh (the default shell) or bash

Set for the current Terminal tab. Generate a real value instead of typing one —
`openssl` ships with macOS:

```bash
export CYCLAW_API_KEY="$(openssl rand -hex 20)"
echo "$CYCLAW_API_KEY"        # copy it; you paste this into the console UI
uvicorn gate:app --host 127.0.0.1 --port 8787
```

Persist it. macOS has defaulted to **zsh** since Catalina, so that means
`~/.zshrc` unless you switched — check with `echo $SHELL` first:

```bash
echo 'export CYCLAW_API_KEY="your-strong-local-secret"' >> ~/.zshrc
source ~/.zshrc
echo "$CYCLAW_API_KEY"        # confirm it survived
uvicorn gate:app --host 127.0.0.1 --port 8787
```

On bash, append it to the first existing login file in this order:
`~/.bash_profile`, `~/.bash_login`, `~/.profile`. Create `~/.bash_profile` only
when none exists; macOS bash login shells do not read `~/.bashrc`.

Full macOS walkthrough — including launching the harness console beside the
gateway and exercising every REST endpoint with `curl` — is in
[`setup-guide.md`](setup-guide.md#macos-apple-silicon).

### Linux — bash / zsh

Set for the current session:

```bash
export CYCLAW_API_KEY="your-strong-local-secret"
uvicorn gate:app --host 127.0.0.1 --port 8787
```

Persist in your shell profile (`~/.bashrc`, `~/.zshrc`, or `~/.profile`):

```bash
echo 'export CYCLAW_API_KEY="your-strong-local-secret"' >> ~/.bashrc
source ~/.bashrc
uvicorn gate:app --host 127.0.0.1 --port 8787
```

### Windows — PowerShell

*(Windows is the fallback path; CyClaw is developed and verified on macOS
first. Everything below still works and is CI-covered on `windows-latest`.)*

Set for the current session only (cleared on terminal close):

```powershell
$env:CYCLAW_API_KEY = "your-strong-local-secret"
uvicorn gate:app --host 127.0.0.1 --port 8787
```

Persist across sessions (writes to the current user's environment permanently):

```powershell
[System.Environment]::SetEnvironmentVariable(
    "CYCLAW_API_KEY",
    "your-strong-local-secret",
    [System.EnvironmentVariableTarget]::User
)
# Restart your terminal, then launch normally:
uvicorn gate:app --host 127.0.0.1 --port 8787
```

Verify it is set before launching:

```powershell
echo $env:CYCLAW_API_KEY
```

### Windows — Command Prompt (cmd.exe)

```cmd
set CYCLAW_API_KEY=your-strong-local-secret
uvicorn gate:app --host 127.0.0.1 --port 8787
```

Persist permanently (takes effect in new sessions):

```cmd
setx CYCLAW_API_KEY "your-strong-local-secret"
```

### Windows Server 2022 — System-wide (all users, requires admin)

```powershell
[System.Environment]::SetEnvironmentVariable(
    "CYCLAW_API_KEY",
    "your-strong-local-secret",
    [System.EnvironmentVariableTarget]::Machine
)
```

Or via GUI: **System Properties → Advanced → Environment Variables → System variables → New**.

### All platforms — `.env` file (already in `.gitignore`)

Create `.env` in the repo root:

```
# Keys live here, never in config.yaml — config.yaml only names which
# provider is enabled; the key itself is read from the environment.
CYCLAW_API_KEY=your-strong-local-secret
GROK_API_KEY=your-xai-key-or-dummy-when-offline
ANTHROPIC_API_KEY=your-anthropic-key
```

The Claude variable is **`ANTHROPIC_API_KEY`**, not `CLAUDE_API_KEY` —
`llm/client.py` and `agentic/config.py` both read the former, and nothing in
the codebase reads the latter. Setting the wrong name is silent: Claude simply
reports unavailable and the query falls back to a local answer.

Load it before launching:

```bash
# Bash / Zsh
export $(grep -v '^#' .env | xargs)
uvicorn gate:app --host 127.0.0.1 --port 8787
```

```powershell
# PowerShell
Get-Content .env | ForEach-Object {
    if ($_ -match '^([^#=][^=]*)=(.*)$') {
        [System.Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim())
    }
}
uvicorn gate:app --host 127.0.0.1 --port 8787
```

### Choosing an API key value

CyClaw is loopback-only (`127.0.0.1:8787`) — the key never crosses a network. Still:

- Use at least **20 random characters**: `openssl rand -hex 20` (Linux/macOS) or `[System.Web.Security.Membership]::GeneratePassword(24,4)` (PowerShell)
- Do **not** reuse a password from elsewhere
- Do **not** commit the key to Git (`.env` is already in `.gitignore`)
- Don't forget to set the api key via terminal on Mac or env var in Windows or the web app will not recognize it.

## Quick Start

### Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.12 | Primary supported runtime |
| [Ollama](https://ollama.com/) | Any | Must be running on `localhost:11434` |
| Model pulled in Ollama | — | `qwen3.6:27b` (default), `mistral:7b`, or any chat model |
| **macOS** (primary) | 14 Sonoma+ | **Apple Silicon only.** An Intel Mac cannot install this repo's pinned torch at all — no `x86_64` wheel is published at that pin |
| Windows / Linux (fallback) | — | Both fully supported and CI-covered; they share the `+cpu` torch path below |

### Install — macOS (Apple Silicon)

macOS is the primary supported platform, and it needs a **different torch step**
than Windows/Linux: the `+cpu` local-version wheel does not exist for macOS, and
both manifests hardcode that pin, so the generic block fails twice on a Mac.

```bash
git clone https://github.com/CGFixIT/CyClaw
cd CyClaw
python3.12 -m venv .venv
source .venv/bin/activate

# 1) torch FIRST, and PLAIN — no +cpu suffix, no --index-url override.
#    Apple Silicon has one arm64 wheel; there is no CPU/CUDA build to pick between.
pip install "torch==2.13.0"

# 2) Everything else, from copies of both manifests with the torch and
#    PyTorch-index lines stripped out. Same thing CI's macos-latest leg runs.
grep -v -e '^torch==' -e '^--extra-index-url https://download.pytorch.org' \
    requirements.txt > /tmp/requirements-macos.txt
grep -v '^torch==' constraints.txt > /tmp/constraints-macos.txt
pip install -r /tmp/requirements-macos.txt -c /tmp/constraints-macos.txt \
    --ignore-installed PyYAML
```

Prefer a script? `bash ./macos/install-cyclaw.sh` branches on `uname -s` and
handles the torch difference for you — but it targets the **harness console**,
not the RAG gateway, and skips Ollama, the index, and the API keys. The
tradeoffs are tabulated in
[`setup-guide.md`](setup-guide.md#option-a--the-installer-script-handles-the-torch-difference-for-you).

### Install — Windows / Linux (fallback)

```bash
git clone https://github.com/CGFixIT/CyClaw
cd CyClaw
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 1) Install CPU-only torch first (CVE-2025-32434 fixed in 2.6.0; 2.13.0 is within the patched range)
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu

# 2) Install the rest, pinned to the verified transitive tree.
pip install -r requirements.txt -c constraints.txt
```

### Every optional feature in one environment (any platform)

For a from-scratch dev box or a full manual smoke test — Postgres/pgvector, NeMo
Guardrails, dev/test tools, and both cloud providers — substitute step 2 with:

```bash
pip install -e ".[all]" -c constraints.txt
```

### Required local prep

```bash
mkdir -p index logs   # optional — gate.py/the retriever/logger self-create these on first run
export GROK_API_KEY=dummy
```

`data/personality/soul.md` ships committed to git with CyClaw's real
personality already in place — do not recreate it from a placeholder on a
fresh clone. If it's ever deleted, `PersonalityManager` self-heals with a
generic default, but that's a recovery path, not the normal first-run state.

### Run

CyClaw ships **two** independent local web apps. Neither starts the other; run
whichever you need, or both in separate terminal tabs.

```bash
# The RAG gateway — serves static/terminal.html at / plus the whole REST API
python -m retrieval.indexer                          # once, before the first /query
uvicorn gate:app --host 127.0.0.1 --port 8787        # → http://127.0.0.1:8787

# The coding-harness console — serves static/harness.html
python -m harness.server                             # → http://127.0.0.1:8790
```

**The `cyclaw-*` short names need a self-install.** `cyclaw-server`,
`cyclaw-harness`, `cyclaw-index`, `cyclaw-mcp`, `cyclaw-metrics`, and
`cyclaw-clear-cache` are `[project.scripts]` console scripts, and pip writes
those shims only when the CyClaw *project itself* is installed. The install
steps above install `requirements.txt` — a third-party pin list with no
self-install line — so those names are `command not found` after them. Add
`pip install -e . -c constraints.txt` if you want them; otherwise use the
`python -m …` forms, which always work and are what both shipped launchers use.

Override the harness port with `CYCLAW_HARNESS_PORT=8795 python -m
harness.server`, not a CLI flag; it refuses to bind a non-loopback address.

`uvicorn harness.server:app` also works, via a lazy module-level `app`
(`harness/server.py` builds it on first attribute access rather than at import,
so merely importing the module never touches `~/.CyClaw`). Prefer the `-m` form
anyway: the uvicorn form bypasses the bind-address guard, so `--host 0.0.0.0`
opens a public socket that `python -m harness.server` would have refused.
`TrustedHostMiddleware` still rejects any non-loopback `Host` header either
way, so the containment holds — but one layer fewer.

Open `/` for the terminal UI and `/health` for readiness. The terminal exposes five operator consoles — **Soul**, **Sync**, **Agentic**, **Filesystem**, and **SQL** — the latter four calling `POST /ops/sync`, `/ops/agentic`, `/ops/fsconnect`, and `/ops/sqlconnect` (API-key gated, rate-limited, audited).

Every gateway route with a copy-pasteable `curl` invocation, plus what each
status code means, is in
[`setup-guide.md`](setup-guide.md#rest-api--testing-every-endpoint-from-the-terminal).

---

## Project Structure

```text
CyClaw/
├── gate.py
├── graph.py
├── config.yaml                 # single source of truth
├── README.md
├── Dropbox_Sync_Guide.md
├── mcp_hybrid_server.py        # retrieval-only MCP server
├── agentic/                    # out-of-band GitHub context + governed registry
│   ├── cli.py
│   ├── context.py
│   ├── gh_client.py
│   ├── registry.py
│   ├── writer.py               # gh pr create --draft, implemented but shipped disarmed
│   ├── fsconnect/              # (v1.8) local/SMB filesystem connector
│   │   ├── cli.py
│   │   ├── client.py           # scoped reads (fs_list/stat/read/grep)
│   │   ├── pathsafe.py         # TOCTOU-safe openat/O_NOFOLLOW security core
│   │   ├── writer.py           # gated, atomic writes (default-disabled)
│   │   └── indexer.py          # toggleable RAG-corpus indexing of the share
│   ├── sqlconnect/             # (v1.8) read-only SQL scaffold (Postgres/MSSQL)
│   │   ├── cli.py
│   │   └── client.py           # SELECT-only query guard, env-only DSN
│   ├── harness_optimizer/      # (v1.9) governed better-harness-style optimizer scaffold
│   │   ├── core.py             # Experiment/Surface/RunReport/CandidateDecision models
│   │   ├── proposer.py         # scoped train/holdout workspace builder
│   │   ├── mcp/tools.py        # audited, symlink-hardened proposer workspace tools
│   │   └── governance.py       # visible-case-hardcoding + governance-finding gates
│   └── deepagent_github/       # (v1.9) optional LangChain Deep Agents GitHub harness
│       ├── builder.py          # lazy create_deep_agent() seam, never imported unless enabled
│       ├── permissions.py      # phase-5 no-write policy refusal
│       └── subagents.py        # validated SubAgent specs, no bare-string tools
├── guardrails/                 # (v1.8) optional NeMo Guardrails layer (out-of-band)
│   ├── cli.py
│   ├── config.py
│   ├── integration.py          # soft-imports nemoguardrails; degrades gracefully
│   ├── rails.py                # offline heuristic rails (injection/soul/grounding)
│   ├── metrics.py              # separate logs/guardrails.jsonl stream (hashes only)
│   └── config/                 # NeMo config.yml + rails.co (Colang flows)
├── harness/                    # (v1.9) PowerShell coding-harness console (out-of-band)
│   ├── server.py               # FastAPI control plane, 127.0.0.1:8790 (cyclaw-harness)
│   ├── sessions.py             # JSON session store with per-session token tallies
│   ├── ollama.py               # loopback-only OpenAI-compatible /v1 chat client
│   ├── config.py               # %USERPROFILE%\.CyClaw home layout + config.json
│   ├── prompts.py              # system prompt from ponytail + karpathy skills (+ soul, read-only)
│   ├── registry_view.py        # merged skills/tools/connectors view (AST-parses MCP tools)
│   └── schemas.py              # request models
├── telegram/                   # (v1.9) optional Telegram channel (out-of-band), shipped enabled: false
│   ├── cli.py
│   ├── client.py               # Bot API client — outbound notify + long-poll inbound chat
│   ├── config.py               # loads config.yaml's `telegram:` block
│   ├── runner.py                # long-poll loop; answers via loopback POST /query only
│   ├── state.py                 # T3 hybrid-confirm consent state (default off)
│   ├── media.py                 # T4 attachment staging via agentic/fsconnect (default off)
│   └── ratelimit.py
├── powershell/                 # Windows installer/launcher for the harness
│   ├── Install-CyClaw.ps1      # home + venv + PATH shim + profile function
│   ├── Invoke-CyClaw.ps1
│   └── Uninstall-CyClaw.ps1
├── .claude/                    # local operator workflows and prompts
│   ├── commands/
│   ├── hooks/
│   ├── memory/
│   ├── patterns/
│   ├── rules/
│   ├── skills/
│   ├── tools/
│   └── utility-prompts/
├── retrieval/
│   ├── indexer.py
│   ├── hybrid_search.py
│   ├── embeddings.py
│   └── stemmer.py
├── llm/
│   └── client.py
├── sync/                       # optional Dropbox corpus sync
│   ├── cli.py
│   ├── runner.py
│   └── scheduler.py
├── utils/
│   ├── sanitizer.py
│   ├── logger.py
│   ├── personality.py
│   ├── health.py
│   ├── ratelimit.py
│   └── telemetry_kill.py       # shared kill block — applied by gate.py, mcp_hybrid_server.py, retrieval/vector_store.py
├── tests/
├── docs/
├── static/
├── data/
│   ├── corpus/
│   └── personality/
└── .github/workflows/
```

---

## Dropbox Corpus Sync

CyClaw includes an **optional, out-of-band** Dropbox sync layer that mirrors a Dropbox corpus into `data/corpus/` without touching `gate.py`, `graph.py`, or the MCP request path.

**Key capabilities**
- `rclone`-backed pull sync with safety fuses (`max_delete`, `max_transfer`)
- crash-safe single-instance locking — an OS-backed lock (`fcntl.flock` / `msvcrt.locking`) prevents a scheduled run and a manual run from racing, and releases automatically even if the process dies
- audit logging for changed corpus files
- optional scheduler integration for Linux and Windows
- optional reindex trigger when corpus changes

**Core commands**

```bash
python -m sync.cli test
python -m sync.cli sync --dry-run
python -m sync.cli sync
python -m sync.cli status
python -m sync.cli schedule
python -m sync.cli unschedule
```

The same actions are available from the **Sync Console** panel in the terminal UI via `POST /ops/sync` (loopback-only, API-key gated, audited).

See `Dropbox_Sync_Guide.md` for full setup and scheduling details, and [`docs/SYNC_README.md`](docs/SYNC_README.md) for module internals (lock lifecycle, exit codes, error taxonomy).

---

## Agentic Layer (v1.6.0)

CyClaw now includes a **concise, governed agentic layer** for local operator workflows. It is **opt-in, disabled by default, and fully out-of-band**: it is never imported by `gate.py`, `graph.py`, or `mcp_hybrid_server.py`.

### What it adds

- **Read-only GitHub context** through the `gh` CLI
- **Governed local skills registry** with explicit human gating
- **Project workflows and operator helpers** under `.claude/`
- **Reusable local patterns** for memory, commands, tools, hooks, and utility prompts

### Security posture

- reads only in normal operation
- no GitHub token is stored or forwarded by CyClaw
- `gh` is invoked as an argv list, not via shell execution
- the GitHub write path (`gh pr create --draft`) is IMPLEMENTED but shipped
  DISARMED: `EXECUTION_ENABLED` is a hardcoded `False` in `agentic/writer.py`
  that no config file can flip, plus four config/per-call gates. Arming it is a
  filed-checklist operator procedure (`docs/agentic/GITHUB_WRITE_ENABLEMENT.md`)
- all agentic reads, refusals, and registry changes are audit logged

### Enable it

```yaml
agentic:
  enabled: true
  repo: "CGFixIT/CyClaw"
  mode: "read"
  writes_enabled: false
  gh_min_version: "2.40.0"
  registry_path: "data/agentic/skills_registry.json"
```

### Main agentic commands

```bash
python -m agentic.cli status
python -m agentic.cli context --repo
python -m agentic.cli context --pr 123
python -m agentic.cli context --issue 45
python -m agentic.cli test
python -m agentic.cli propose-skill --name deploy --desc "..." --body-file s.md --reason "draft"
python -m agentic.cli apply-skill --name deploy --desc "..." --body-file s.md --reason "add deploy runbook" --confirm
```

The **Agentic Console** panel drives these from the terminal UI via `POST /ops/agentic`; skill-Apply stays disabled behind a 4-gate checklist (`mode=write` + `writes_enabled` + reason + `--confirm`) — dry-run only under shipped defaults.

**Key areas in agentic folders**
- `skills/` — reusable project skills / workflows
- `commands/` — shortcut command entry points
- `patterns/` — repeatable operating patterns
- `tools/` — tool wrappers and helper definitions
- `utility-prompts/` — reusable operator prompts
- `memory/` — memory-oriented helpers / artifacts
- `hooks/` and `rules/` — local guardrails and automation boundaries

**Examples from the current repo**
- run / smoke-test workflows for CyClaw
- architecture, tests, logging, and speed refactor loops
- wrap-up / session-end workflows
- memory orchestration support patterns

---

## Filesystem & SQL Connectors (v1.8)

v1.8 extends the agentic layer beyond GitHub to **local data**, for the regulated or security conscious use case where AI use is compliance heavy. Both connectors are **opt-in, disabled by default, and fully out-of-band** — never imported by `gate.py`, `graph.py`, or `mcp_hybrid_server.py`, so the six security invariants hold by construction. While disabled, their CLIs are a pure no-op (exit 0).

### `agentic/fsconnect/` — local / SMB filesystem connector

Scoped **reads** and separately-gated **writes** over a local or SMB file share, sharing one TOCTOU-safe security core.

- **`pathsafe.py` security core** — POSIX `openat` / `O_NOFOLLOW` handle-descent from a held root directory fd (so the root cannot be swapped under the process). Denies UNC, NTFS alternate data streams (`file::$DATA`), `\\?\` / `\\.\` device paths, `..` traversal, and any symlink / reparse point. Segment-aware containment closes **CVE-2025-53110** (sibling-prefix) and `realpath` + `O_NOFOLLOW` close **CVE-2025-53109** (symlink/junction escape).
- **Reads** (`fs_list` / `fs_stat` / `fs_read` / `fs_grep`) confined to `allowed_roots`, audited, with a 5 MiB read cap and advisory OWASP∪`banned_patterns` content scanning.
- **Writes** (`fs_write` / `fs_append` / `fs_mkdir` / `fs_move`) — fully built but **`writes_enabled: false` by default**; confined to a **separate** `writable_roots` list; gated by a human `reason` + `--confirm` (for destructive ops); atomic (`tmp` + `os.replace`); **content-agnostic** (never calls the LLM — an operator pipes local-LLM/QWEN output in). A code-level `FS_WRITE_HARD_DISABLE` kill switch forces dry-run regardless of config.
- **Toggleable RAG-corpus indexing** of the share (`index_enabled`, dry-run default) stages eligible files into the corpus and triggers a reindex **subprocess** — enabling a generate → write → index loop without importing the retrieval layer.

```bash
python -m agentic.fsconnect.cli status
python -m agentic.fsconnect.cli read  <path>            # scoped read
python -m agentic.fsconnect.cli grep  <path> <pattern>
python -m agentic.fsconnect.cli write <path> --reason "..."   # dry-run unless writes_enabled
python -m agentic.fsconnect.cli index --apply           # stage share → corpus
python -m agentic.fsconnect.cli test                    # pre-flight self-test
```

Enable in `config.yaml`:

```yaml
fsconnect:
  enabled: true
  allowed_roots: ["/srv/share"]   # REQUIRED when enabled; existing dirs
  max_file_bytes: 5242880         # 5 MiB read cap
  writes_enabled: false           # master write switch (dry-run plans while false)
  writable_roots: [null]          # null => OS default (/var/lib/cyclaw-fs | C:\CyClaw-FS)
  max_write_bytes: 10485760       # 10 MiB write cap
  index_enabled: false            # toggle RAG-corpus indexing of the share
```

### `agentic/sqlconnect/` — read-only SQL connector (v0.1 scaffold)

A disabled-by-default scaffold for read-only on-prem SQL (Postgres / MSSQL). Read-only is enforced three ways: a **SELECT/WITH-only query guard** (rejects DDL/DML, stacked statements, and comment-hidden keywords by scanning a quote-stripped copy), a **session-level read-only** transaction, and a hard `allow_write: false`. The DSN is read from an **environment variable only** (`CYCLAW_SQL_DSN`), never hardcoded; drivers (`psycopg` / `pyodbc`) are imported lazily. The quote-stripping scan (layer 1) is a single left-to-right pass that gives `'...'`, `"..."`, `[...]`, and Postgres `$tag$...$tag$` quoting the same precedence the database itself gives them — an earlier regex-alternation version could be fooled by a quote character nested inside a different quoting form (e.g. `$$'$$`) into treating a stacked `DROP` as part of one `SELECT`; layers 2 and 3 were never affected.

```bash
python -m agentic.sqlconnect.cli status
python -m agentic.sqlconnect.cli schema                 # list table schemas (read-only)
python -m agentic.sqlconnect.cli query --table public.users   # bounded preview
python -m agentic.sqlconnect.cli test
```

```yaml
sqlconnect:
  enabled: false
  driver: "postgres"             # "postgres" | "mssql"
  dsn_env: "CYCLAW_SQL_DSN"      # DSN from this env var only
  statement_timeout_ms: 5000
  max_rows: 1000
  allow_write: false             # reserved; v0.1 cannot write regardless
```

---

## NeMo Guardrails (v1.8)

An **opt-in** content-safety layer in `guardrails/`. Absence of the `guardrails:` block, or `enabled: false` (the shipped default), is a pure no-op. Since Phase 2, when enabled, `utils/guardrail_bridge.py` wires its offline input rail into one visible `graph.py` node (`guardrail_input`, between `route_by_score` and `local_llm`) — still **defense-in-depth only, never a routing authority**: the graph's own `guardrail_router` edge (topology, not guardrails code) decides where a blocked query goes. `guardrails` itself is still never imported directly by `gate.py` or `graph.py` — `utils/guardrail_bridge.py` is the only seam, preserving module isolation (invariant I6). `mcp_hybrid_server.py` never touches it at all.

- **`nemoguardrails` is an optional dependency.** The layer **soft-imports** it and, when it is absent, degrades to **offline heuristic rails** that need no second LLM call:
  - **input** — light prompt-injection marker scan + soul/identity-mutation intent detection (the content-layer arm of the Soul-Governance invariant);
  - **output** — token-overlap **grounding** check against the retrieved context, flagging likely-ungrounded (hallucinated) answers below `hallucination_threshold`.
- When `nemoguardrails` **is** installed, the same Python checks back the live NeMo actions (via the Colang flows in `guardrails/config/rails.co`), so the offline heuristics and live rails never drift.
- Decisions are recorded to a **separate** metrics stream (`logs/guardrails.jsonl`) that stores **only SHA-256 hashes** — never raw text — mirroring the audit log's privacy posture.

```bash
python -m guardrails.cli status                         # config + nemoguardrails availability
python -m guardrails.cli check "your query here"        # run offline rails (no LLM/NeMo needed)
python -m guardrails.cli metrics                         # summarize the guardrail stream
python -m guardrails.cli test                            # pre-flight self-test
```

```yaml
guardrails:
  enabled: false                 # opt-in; also gates the graph.py guardrail_input node
  engine: "openai"               # Ollama OpenAI-compatible endpoint
  model: "qwen3.6:27b"            # keep in sync with models.local_llm.model
  hallucination_threshold: 0.18  # token-overlap floor for the grounding rail
  metrics_path: "logs/guardrails.jsonl"   # separate from logs/audit.jsonl (hashes only)
```

> Full design / wiring plan: `docs/NeMo/later_development_guideline.md`. Phase 2
> implementation contract: `docs/NeMo/phase2_implementation_plan.md`.

---

## Agentic Harness Scaffold (v1.9)

A governed, **opt-in, disabled-by-default, and out-of-band** scaffold for two related
future capabilities — never imported by `gate.py`, `graph.py`, or `mcp_hybrid_server.py`,
same isolation guarantee as every other agentic feature above:

- **`agentic/harness_optimizer/`** — a better-harness-style optimizer that would evaluate
  candidate improvements to allowed harness surfaces against visible train cases and
  hidden holdout cases, deterministic scoring, and a hard acceptance gate (no score
  regression, no unallowed surface changed, no visible-case hardcoding, no critical
  governance finding).
- **`agentic/deepagent_github/`** — an optional LangChain Deep Agents-backed local GitHub
  coding harness using Ollama and scoped CyClaw tool wrappers, lazily importing
  `deepagents` only when explicitly enabled.

**Status:** phases 0-9 are implemented. Phases 0-5 (config validation, the
proposer workspace + its audited, symlink-hardened tool boundary, mock
scoring/acceptance gate, the lazy `deepagent_github` builder seam) are covered by
`tests/test_agentic_harness_optimizer.py` and `tests/test_agentic_harness_phase345.py`.
Phases 6-9 (real Deep Agents subagent wiring, a fixture-based GitHub coding
evaluator, and governed propose/apply with human-confirmed acceptance) landed in
PR #515 (merged 2026-07-13), are covered by `tests/test_agentic_harness_phase679.py`,
and are documented in `docs/work/DEEP_AGENT_HARNESS_PHASES_6_9.md` — phase 9 is
a security gate, not authorization to add an executor. Full plan and phase ledger:
`docs/work/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`.

> **Superseded 2026-08-01.** Phase 9's security gate was subsequently satisfied and
> P10 landed, so "not authorization to add an executor" no longer describes the
> current tree: a sandboxed verification executor (`agentic/executor/`) and a
> draft-PR write path (`agentic/writer.py::execute_write`) both exist, and the live
> real-repo coding pipeline is `agentic/real_repo_loop.py` — **not** the
> `deepagents`-backed graph this section describes. Both new capabilities still ship
> disarmed. See [GitHub Agentic Coding Harness](#github-agentic-coding-harness-v19)
> below for what is actually wired today.

Every gate below the master `agentic.enabled` switch defaults to `false`; while
disabled, nothing under either package is reachable from `agentic.cli`, and no
`deepagents`/`langchain` optional dependency is imported.

```yaml
agentic:
  deepagent_github:
    enabled: false
    allow_deepagents_dependency: false    # extras must be installed explicitly
    allow_filesystem_write_tools: false
    allow_shell_execution: false
    allow_github_writes: false            # writer.py remains the write-policy boundary
  harness_optimizer:
    enabled: false
    require_human_confirm_for_accept: true
    allow_local_model_judge: false
```

---

## Coding Harness Console (v1.9)

A grok-build-style local coding console, shipped as the strictly out-of-band
`harness/` package (Windows merged 2026-07-22; macOS/Linux port merged
2026-08-01). Like `agentic/`, `sync/`, and `guardrails/`, it is never imported
by `gate.py`, `graph.py`, or `mcp_hybrid_server.py` and never imports them
(invariant I6).

`harness/` itself is pure Python with **no OS branch in its request-handling
path** — same routes, same slash commands, same security posture everywhere.
Only the install/launch glue is platform-coupled, which is why there are two
sibling script trees (`powershell/`, `macos/`) rather than one abstraction.

- **Launch:** `python -m harness.server` serves a slash-command console at
  `http://127.0.0.1:8790` (`static/harness.html`); loopback-only bind,
  non-loopback hosts refused. `gate.py` keeps `:8787`. (`cyclaw-harness` is the
  same entry point, but only exists after `pip install -e .` — see
  [Quick Start](#quick-start).)
- **Install (Windows):** `powershell -ExecutionPolicy Bypass -File .\powershell\Install-CyClaw.ps1`
  sets up `%USERPROFILE%\.CyClaw` (config, sessions, seeded skills catalog),
  a venv, a `cyclaw.cmd` PATH shim, and a `cyclaw` profile function.
- **Install (macOS / Linux):** `bash ./macos/install-cyclaw.sh` sets up the
  same `~/.CyClaw` layout, a venv, a `cyclaw` shim, and a PATH entry plus a
  `cyclaw()` function in your rc file (`~/.zshrc` on zsh; macOS bash preserves
  the first existing login file among `.bash_profile`, `.bash_login`, and
  `.profile`, creating `.bash_profile` only when none exists; Linux retains its
  `.bash_profile`/`.bashrc` selection). Targets bash
  (including macOS's stock 3.2) and zsh; BSD userland assumed, no Homebrew
  dependency, no GNU-only flags. It branches on `uname -s` to install the
  correct **plain** torch build on macOS — the one step a hand-install most
  often gets wrong. Uninstall: `bash ./macos/uninstall-cyclaw.sh`
  (add `--remove-home` to delete `~/.CyClaw` too).
- **Chat:** talks to the local model through the OpenAI-compatible `/v1`
  endpoint from `config.yaml`'s `models.local_llm.base_url` (Ollama by
  default) — no keys, no login, offline. Every reply shows prompt/completion
  token counts; sessions persist as human-inspectable JSON with atomic writes.
- **Reuse, not duplication:** GitHub actions go through the same
  `utils.ops_runner` subprocess shim as `/ops/agentic` (read mode by default);
  the skills/tools/connectors panes are read-only registry views — including
  the governed `data/agentic/skills_registry.json` catalog alongside the
  repo's own filesystem skills — composed by `harness/registry_view.py`; the
  system prompt is composed from the repo's own `ponytail` +
  `karpathy-guidelines` skills, with the governed soul appended read-only when
  enabled.

Full setup, slash-command reference, home layout, and security posture:
[`docs/HARNESS_POWERSHELL.md`](docs/HARNESS_POWERSHELL.md) (Windows) and
[`docs/HARNESS_MACOS.md`](docs/HARNESS_MACOS.md) (macOS/Linux). The macOS doc
covers only what genuinely differs — install glue, the torch build, git
credential helpers (`git-credential-osxkeychain` rather than Windows
Credential Manager), and the note that `pathsafe.ScopedRoots`' POSIX
`openat`/`O_NOFOLLOW` containment is the *stronger* branch, so macOS is not on
a weaker path than Windows here.

---

## GitHub Agentic Coding Harness (v1.9)

The real-repo coding pipeline: **clone → plan → patch → verify → human decides →
commit**, with pushing and opening a draft PR as two further, separate decisions.
Driven by `agentic/real_repo_loop.py`, which fuses three previously-independent
pieces — the planner's model call, a jailed real clone
(`agentic/deepagent_github/repo_workspace.py`), and the sandboxed verification
executor (`agentic/executor/`). Out-of-band like every other agentic feature:
never imported by `gate.py`, `graph.py`, or `mcp_hybrid_server.py` (invariant I6).

**It ships fully disarmed.** Every gate below defaults to closed, and the draft-PR
step is gated by a constant in code that no config file can flip.

### How a run works

0. **`real-repo-run-plan`** (optional, two-stage) asks a model for an implementation
   plan and prints it. It clones nothing, writes nothing, and commits nothing. You
   read the plan, edit it, and feed it back with `--plan-file` — so one model plans,
   a **human approves**, and another model codes against the approved text. The plan
   is injection-scanned on load, truncated at 6,000 chars, and its SHA-256 is
   recorded on the run so the record says which plan was in force.
1. **`real-repo-run`** clones the configured repo into a jailed workspace, asks the
   planner for whole-file replacements, writes them, and runs the selected
   verification checks. It **stops before committing** and reports
   `status: pending_decision`. A run that never passes reports `exhausted`.
2. **`real-repo-run-decide --decision approve`** is what actually commits (locally).
   `reject` discards. Neither pushes.
3. **`real-repo-run-push`** puts the `claude/*` branch on origin.
4. **`real-repo-run-publish`** opens a **draft** PR (`gh pr create --draft`).
5. **`real-repo-run-discard`** reclaims the clone — the only step that frees disk.
   An approved run keeps its clone on purpose, since push and publish still need it.

Each escalation is its own command and its own decision, deliberately not folded
into `approve`.

### Security posture

- **Diff-scope gate.** A candidate that writes into `tests/`, `conftest.py`,
  `.github/`, `.git/`, `pyproject.toml`, `setup.cfg`, `pytest.ini`, or
  `.claude/skills/` is refused outright — those are the files that judge the
  candidate's own acceptance, and rewriting them is the classic reward-hacking
  failure mode of a make-the-checks-pass loop. Also budget-capped
  (`max_write_budget_bytes`, 100000 bytes per iteration).
- **Two scanners, two questions, on the same bytes.** Proposed file content is run
  through an injection scan (*is this trying to talk to a model?*) **and** a
  code-shape scan (*is this code trying to exfiltrate a key?* —
  `inspect_code_shape`, `agentic.deepagent_github.scan_code_shape`, ships `true`).
  The second exists because a working SSH-key exfiltration payload contains no
  injection phrase at all: it matches on *combinations* — a secret path plus a
  network egress call, a decode plus a dynamic exec, a socket plus an fd-dup or a
  shell path, a pipe-to-shell. Every hit is CRITICAL and refuses the candidate.
- **Verification runs as argv-list subprocesses**, never a shell, with `cwd`
  pinned to the clone, a scrubbed environment allowlist
  (`PATH`, `HOME`, `LANG`, `LC_ALL`, `PYTHONPATH`, `VIRTUAL_ENV`,
  `PYTHONIOENCODING`) plus forced `NO_PROXY=*` / `PIP_NO_INDEX=1`, and a 120s
  per-check timeout. **Containment is best-effort software, not a hard boundary** —
  the env scrub cannot stop a determined test file from opening a raw socket. See
  `agentic/executor/runner.py`'s own statement of its limits.
- **The console sends check-profile *names*, never argv.** `harness/agent_policy.py`
  resolves them against a fixed allow-list (`pytest`, `ruff`); a request body that
  could carry an argv would make an authenticated route a remote shell.
- **`push_branch` passes no credential.** Its four-name env allowlist deliberately
  excludes `GH_TOKEN`/`GITHUB_TOKEN`, because that environment is shared with the
  executor that runs model-proposed check commands. It authenticates only via a
  HOME-resident credential helper (`gh auth setup-git`).
- Branch names are forced into the `claude/` namespace; `run_id` is validated as
  32-char lowercase hex before it can become an argv element.

### Enable it

All five ship `false`; the run path needs the first three, and nothing here arms
the draft-PR step:

```yaml
agentic:
  enabled: false                        # master switch
  deepagent_github:
    enabled: false
    allow_git_write_tools: false        # gates every write/commit/push in the clone
    model: ""                           # must be set for the local planner
    workspace_root: "data/agentic/workspaces"
    max_write_budget_bytes: 100000
    max_handoff_chars: 200000           # outbound-prompt cap for cloud egress
    allow_cloud_providers: false        # gate 3 of the cloud chain
    providers:
      grok:   { enabled: false, model: "grok-4.5" }
      claude: { enabled: false, model: "claude-sonnet-5" }
```

Opening a PR additionally requires `agentic.mode: "write"`, `writes_enabled: true`,
**and** flipping `EXECUTION_ENABLED` in `agentic/writer.py` — a hardcoded `False`
that is deliberately not config-reachable. Arming it is a filed-checklist
procedure with a sign-off line, not a toggle:
[`docs/agentic/GITHUB_WRITE_ENABLEMENT.md`](docs/agentic/GITHUB_WRITE_ENABLEMENT.md).

### Commands

```bash
# Optional stage 0: plan, review by hand, then hand the approved text to the coder.
python -m agentic.cli real-repo-run-plan \
  --pr 123 --instruction "fix the off-by-one in the parser" --out plan.md

python -m agentic.cli real-repo-run \
  --pr 123 --instruction "fix the off-by-one in the parser" \
  --read-file src/parser.py --checks-file checks.json \
  --plan-file plan.md \
  --branch claude/parser-fix --commit-message "fix: off-by-one" \
  --reason "triage issue 123" --confirm

python -m agentic.cli real-repo-run-status  --run-id <32-hex>
python -m agentic.cli real-repo-run-decide  --run-id <32-hex> --decision approve
python -m agentic.cli real-repo-run-push    --run-id <32-hex>
python -m agentic.cli real-repo-run-publish --run-id <32-hex> --reason "..." --confirm
python -m agentic.cli real-repo-run-discard --run-id <32-hex>
```

Exit codes are an API: `0` ok · `2` failed · `3` env/config · `4` write refused.
`real-repo-run` exits `0` whether or not a candidate was accepted — the record's
`status` field carries that.

### From the harness console

Seven routes on `127.0.0.1:8790`. `GET /api/agent/checks` is open (it lists a
hardcoded allow-list and spawns nothing); the other six require a Bearer
`CYCLAW_API_KEY` plus an `Origin`/`Sec-Fetch-Site` cross-site check:
`POST /api/agent/run`, `GET /api/agent/runs/{id}`, and
`POST /api/agent/runs/{id}/{decision,push,publish,discard}`.
`POST /api/agent/run` is deliberately synchronous — the run record is written only
when the run ends, and the `run_id` first exists in that response. Its wall-clock
budget is **derived from what the request asked for**, not a flat constant:
`iterations × planner_timeout + iterations × checks × 120s + 300s` overhead, capped
at 3600s. A flat budget was a real bug — `subprocess.run(timeout=)` sends an
uncatchable SIGKILL, so a request whose own planner budget exceeded it left a
leaked clone and a permanently `running` record that no later status call could
resolve. Console equivalents are `/agent run|confirm|status|approve|reject|push|publish|discard`.

### Optional cloud planner (Grok / Claude)

The loop is local-only by default, and **the local path (no `--provider` flag)
needs nothing beyond the base install** — `LocalProposerClient` is a plain `httpx`
call, and nothing on that code path (`real_repo_loop.py`, `repo_workspace.py`,
`executor/runner.py`) imports `deepagents` or `langchain`. If you just want to try
the harness against your own Ollama model, `pip install -e .` is enough; skip the
rest of this section.

`--provider grok|claude --confirm-online` drives the loop with a cloud model
instead, behind a **six-condition chain**: `agentic.enabled` →
`deepagent_github.enabled` → `allow_cloud_providers` → `providers.<name>.enabled`
→ the provider's API key env var (`GROK_API_KEY` / `ANTHROPIC_API_KEY`, key presence
only, never a network probe) → per-run `--confirm-online`. Every outbound prompt is
injection-scanned, redacted, hashed, and audited as egress before it leaves the
process.

Cloud SDKs are **opt-in extras, deliberately absent from the default install,
`requirements.txt`, and the Docker image** — installing them is a separate,
explicit step, matched to which provider(s) you actually want:

```bash
# Claude only
pip install -e ".[agentic-deepagents]"                        -c constraints.txt

# Grok only — lighter: just langchain-xai, no deepagents/langchain pulled in
pip install -e ".[agentic-deepagents-cloud]"                   -c constraints.txt

# Both providers, one command
pip install -e ".[agentic-deepagents,agentic-deepagents-cloud]" -c constraints.txt
```

(Want Postgres/pgvector and NeMo Guardrails too, not just the cloud providers?
`pip install -e ".[all]"` in [Quick Start](#quick-start) installs every optional
extra in one command.)

Neither cloud extra is part of `full` (what CI and `full`-installed dev boxes get)
— that split is deliberate, so a machine that never touches cloud providers never
carries their SDKs. The published Docker image installs `requirements.txt` only
(base deps, no extras at all), so running this feature — local *or* cloud — in a
container means installing on top: add `pip install -e .` for local mode, or one
of the three commands above for cloud, after the image's own install step.


---

## Security Model

| Layer | Mechanism |
|---|---|
| Network | Binds `127.0.0.1:8787` — no external exposure by design |
| Input | Config-driven injection filter (`policy.prompt_filter`) |
| Rate limit | 60 req/min per IP |
| Telemetry | Shared kill block (`utils/telemetry_kill.py`) runs before any SDK import in every entry point — gateway, MCP server, and indexer CLI; HF Hub network calls are also cut off once the embedding model is confirmed cached (`retrieval/embeddings.py`) |
| Audit | All paths log SHA-256 query hash + PII-redacted metadata |
| Grok gating | Triple gate: `mode=hybrid` AND `grok.enabled=true` AND `user_confirmed_online=true` |
| Claude gating | Same triple gate, independently: `mode=hybrid` AND `claude.enabled=true` AND `user_confirmed_online=true` |
| Soul writes | Explicit human reason string + enforced write-boundary scan + atomic write |
| Agentic writes | `pr_create` implemented, shipped disarmed behind six gates (one of them a source constant); `pr_comment`/`issue_comment` remain plan-only |
| Filesystem connector | Reads scoped to `allowed_roots` (5 MiB cap); writes default-OFF, confined to a separate `writable_roots`, gated by human `reason` + `--confirm`, atomic; TOCTOU-safe `pathsafe` core denies UNC/ADS/device-path/`..`/symlink escapes |
| SQL connector | Read-only: SELECT/WITH-only query guard + session read-only + hard `allow_write: false`; DSN from env var only; disabled scaffold by default |
| Guardrails | Out-of-band, opt-in defense-in-depth; degrades to offline heuristic rails without `nemoguardrails`; never a routing authority; separate hash-only metrics stream |
| `/ops/*` routes | Loopback-only, `require_api_key` gated, rate-limited (60/min), every call audited (`ops_sync_executed` / `ops_agentic_executed` / `ops_fsconnect_executed` / `ops_sqlconnect_executed`); shells out via `subprocess.run([...])` — never imports `sync/` or `agentic/` |
| Container | Non-root, `no-new-privileges`, `cap_drop: ALL`, read-only rootfs, seccomp, resource limits; optional eBPF/Falco detection (`deploy/falco/`, off by default) |

> **Scope:** CyClaw is a single-operator, loopback-bound local server. The full threat model — what the sandbox does and does **not** cover (no microVM by design) and why — is documented in [`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md).
---

*Envisioned and initially created then vibe coded further (via AI) by [Chris Grady](https://cgfixit.com) · [cgfixit.com/linkedin](https://cgfixit.com/linkedin)*
