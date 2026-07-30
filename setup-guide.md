# CyClaw — GitHub Setup Guide (Windows + Linux)

**v1.9.0 | Offline-First | Ollama | ~15 min**
Verified 2026-07-29 against `main`.

This is the canonical setup guide (`docs/SETUP.md` now points here). For the
full architecture tour — agentic layer, filesystem/SQL connectors, NeMo
Guardrails, the PowerShell harness, and the security model — see
[`README.md`](README.md). This guide covers only what's needed to get the
core RAG gateway running.

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Git | Any recent version |
| Python 3.12 | Primary supported runtime (`requires-python >=3.12`) |
| [Ollama](https://ollama.com/) | Running on `http://127.0.0.1:11434`, with `qwen2.5:7b` pulled: `ollama pull qwen2.5:7b` |
| Corpus `.md` files (optional) | The repo ships a small sample corpus in `data/corpus/`, so the indexer has something to build against out of the box. Copy your own `.md` files in to replace/extend it — from your own notes, or an existing SafeClaw/PsyClaw-style corpus if you have one. |
| Windows | PowerShell, no admin/elevation needed. If script execution is blocked, run once: `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| Linux / macOS | bash |

---

## Windows (PowerShell)

```powershell
# 1. Clone + venv
git clone https://github.com/CGFixIT/CyClaw.git
cd CyClaw
python -m venv .venv
.\.venv\Scripts\Activate.ps1

# 2. Torch CPU first — order matters (keeps the CPU wheel, avoids a multi-GB
#    CUDA build, and stays on the patched side of CVE-2025-32434)
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu

# 3. Everything else, pinned to the verified transitive tree
pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML

# 4. Required env (any non-empty value works — see "GROK_API_KEY" below)
$env:GROK_API_KEY = "dummy"

# 5. Build the retrieval index (safe to skip for now — see "Is the index
#    really mandatory?" below — but /query 503s until you do this)
python -m retrieval.indexer

# 6. Run
uvicorn gate:app --reload --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787` → the terminal UI loads automatically.

### Windows smoke test

```powershell
.\.claude\skills\CyClaw-Sandbox\windows-smoke.ps1
```

Runs 6 real checks (`/health`, an on-topic query, an offline-confirmation
gate check, an injection-blocked query, `/soul`, the terminal page) with
explicit pass/fail output and a non-zero exit on any failure. For a single
quick manual check instead, `tests\apipsTest.ps1` fires one `POST /query` and
prints the raw response — useful for eyeballing a response shape, not a
pass/fail test.

---

## Linux / macOS (Bash)

```bash
# 1. Clone + venv
git clone https://github.com/CGFixIT/CyClaw.git && cd CyClaw
python3.12 -m venv .venv
source .venv/bin/activate

# 2. Torch CPU first — order matters (see the Windows step 2 note above)
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu

# 3. Everything else, pinned to the verified transitive tree
pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML

# 4. Required env (any non-empty value works — see "GROK_API_KEY" below)
export GROK_API_KEY=dummy

# 5. Build the retrieval index (see "Is the index really mandatory?" below)
python -m retrieval.indexer

# 6. Run
uvicorn gate:app --reload --host 127.0.0.1 --port 8787
```

### Linux smoke test

There isn't yet a Linux-native equivalent of `windows-smoke.ps1` in this repo.
Run the full test suite (below) as the install gate, or hit `curl -s
http://127.0.0.1:8787/health` for a one-line readiness check against a
running server.

---

## Key Notes

### Is the index really mandatory?

`gate.py` is fail-soft: it boots and serves `/health` even with no index
built. Only `POST /query` returns `503 {"code": "INDEX_NOT_FOUND"}` until you
run `python -m retrieval.indexer` at least once. Run it before you actually
try to query CyClaw, not necessarily before every server start — and note
`index/` is never checked into git, so this step is needed in every fresh
clone and every fresh environment, not just the first one you ever set up.

### GROK_API_KEY in offline mode

The dummy value is fine for `app.mode: "offline"` in `config.yaml` — the key
is only read lazily, at an actual Grok call site (`llm/client.py`), which
never fires in offline mode. `security.require_env` in `config.yaml` is
decorative; no code enforces it. Tests specifically require
`GROK_API_KEY=dummy` (or any non-empty value) to be set.

### CYCLAW_API_KEY — required for the Soul console, not for `/query`

`/query`, `/health`, and the terminal UI itself need no key. But
`/soul`, `/soul/propose`, `/soul/apply`, `/soul/reload`, and `/soul/restore`
— along with the `/ops/sync`, `/ops/agentic`, `/ops/fsconnect`, and
`/ops/sqlconnect` operator consoles — all require a Bearer `CYCLAW_API_KEY`
and fail **closed** (401) if it's unset. This is easy to miss because the
terminal UI itself loads with no key at all; you'll only hit it when you try
to use one of those consoles. See README's
[API Key Setup](README.md#api-key-setup-soul-mutations) section for the full
per-platform (PowerShell / cmd / bash / systemd / `.env`) instructions.

### No NLTK data download needed

`nltk` is a pinned dependency, but only for its Porter stemmer (pure code, no
downloaded corpus). Tokenization uses a plain word-regex —
`retrieval/stemmer.py` deliberately never calls `nltk.data.load()` /
`word_tokenize()`, so the punkt tokenizer's own path-traversal CVE is never
reachable. There is no `nltk.download()` step to run, and running one adds
network egress this project otherwise avoids for no benefit.

### Telemetry

Every CyClaw entry point (`gate.py`, `mcp_hybrid_server.py`,
`retrieval/vector_store.py`) applies a shared telemetry-kill block
(`utils/telemetry_kill.py`) before any SDK import — LangChain/LangSmith
tracing, ChromaDB's OpenTelemetry and PostHog paths, NeMo Guardrails' usage
stats, HuggingFace Hub's telemetry ping, and the generic OpenTelemetry SDK are
all disabled unconditionally, with no manual step needed. `HF_HUB_OFFLINE`/
`TRANSFORMERS_OFFLINE` are the one deliberate exception: CyClaw sets those two
itself, but only once it has confirmed the embedding model is already cached
on disk, so a brand-new install can still complete its one-time model
download. See `docs/security-philosophy/cyclaw_telemetry_kill.env` for the
full reference list if you want to source it by hand for a locked-down
deployment.

### Test gate

```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short
```

is the install-correctness gate. A bare `pytest` run like this does **not**
measure coverage (`pyproject.toml`'s `addopts` has no `--cov=`); the 80%
coverage gate only applies under CI's explicit `--cov=` invocation. Any
failure here is a real defect against your environment, not an expected
placeholder — there are no known-failing tests on a clean install.

### constraints.txt

The `-c constraints.txt` flag pins the full transitive dependency tree for a
reproducible install; it's a normal, permanent, actively-maintained part of
the repo (not a recovery step). If it's ever missing, `git pull` restores it;
`pip install -r requirements.txt` alone still works without it, just without
the transitive-pin guarantee.

---

## config.yaml — Key Settings to Verify

```yaml
app:
  mode: "offline"                          # keep offline unless you have GROK_API_KEY

models:
  local_llm:
    provider: "ollama"
    base_url: "http://127.0.0.1:11434/v1"  # Ollama's OpenAI-compatible endpoint — do not change
    model: "qwen2.5:7b"                     # must match a model tag actually pulled in Ollama
    timeout_sec: 300                        # must stay < api.graph_timeout_sec (330)
    max_tokens: 3000                        # reserved output budget — see config.yaml's own comment on num_ctx headroom

retrieval:
  min_score: 0.028     # RRF fused-rank threshold (NOT cosine similarity — a different scale)
  top_k_semantic: 5
  top_k_keyword: 5
  rrf_k: 60

personality:
  enabled: true
  soul_path: "data/personality/soul.md"     # ships pre-populated in git — do not overwrite it
  interaction_ttl_days: 365
```

`data/personality/soul.md` is committed to git with CyClaw's real personality
already in place — a fresh clone does not need (and should not have) this
file recreated from a placeholder. If you ever delete it, `PersonalityManager`
self-heals with a generic default and a fresh version row, but that's a
recovery path, not the normal first-run state.

---

## MCP Server (Optional — Claude Desktop / Copilot Studio)

```json
{
  "mcpServers": {
    "cyclaw": {
      "command": "python",
      "args": ["/full/path/to/CyClaw/mcp_hybrid_server.py"]
    }
  }
}
```

The MCP server exposes retrieval-only (`hybrid_search` tool). It has no LLM
sampling capability by design.

---

## Beyond the core RAG gateway

This guide only covers `gate.py` + the retrieval pipeline. CyClaw also ships
several opt-in, disabled-by-default, out-of-band layers — none of them
required to get the server above running, and none of them ever imported into
the request path: a GitHub-context/governed-skills **agentic layer**, a
local/SMB **filesystem connector** and read-only **SQL connector**, an
optional **NeMo Guardrails** content-safety layer, and a separate **PowerShell
coding-harness** console on `127.0.0.1:8790`. See README's own section for
each — [Agentic Layer](README.md#agentic-layer-v160),
[Filesystem & SQL Connectors](README.md#filesystem--sql-connectors-v18),
[NeMo Guardrails](README.md#nemo-guardrails-v18),
[PowerShell Coding Harness](README.md#powershell-coding-harness-v19). A
Docker/`docker-compose` deployment path also exists (`Dockerfile`,
`docker-compose.yml`) with a hardened container posture — see
[`docs/THREAT_MODEL.md`](docs/THREAT_MODEL.md) for what it does and doesn't
cover.

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `POST /query` returns `503 INDEX_NOT_FOUND` | Run `python -m retrieval.indexer` — index not built yet (see "Is the index really mandatory?" above) |
| `Collection not found in ChromaDB` | Delete the `index/` folder and re-run the indexer |
| LLM timeout on query | Long-context inference is slow on CPU — raise `models.local_llm.timeout_sec` in `config.yaml` (stay under `api.graph_timeout_sec`), or switch to a smaller Ollama model |
| `401` on any `/soul/*` or `/ops/*` endpoint | `CYCLAW_API_KEY` isn't set — see "CYCLAW_API_KEY" above |
| `400` on every request, working server otherwise | Your `Host` header isn't in `config.yaml`'s `allowed_hosts` allow-list — add the hostname/IP you're reaching CyClaw by |
| `ModuleNotFoundError: nltk` (or any other pinned package) | The dependency install (step 3) didn't finish — re-run it; this is not fixed by `nltk.download()` |
| `FileNotFoundError: constraints.txt` | `git pull` — it's a normal tracked file, not a one-time recovery |
| Soul endpoints return 404 | Set `personality.enabled: true` in `config.yaml` |
| `uvloop` install fails on Windows | Expected — uvloop is Linux-only; uvicorn falls back to asyncio automatically |

---

*Built by [Chris Grady](https://cgfixit.com) · Repo: [github.com/CGFixIT/CyClaw](https://github.com/CGFixIT/CyClaw)*
*v1.9.0, Python 3.12 verified — last accuracy pass 2026-07-29*
