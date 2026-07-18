# CyClaw — GitHub Setup Guide (Windows + Linux)

**v1.3+ | Offline-First | LM Studio | 10–15 min**  
Tested & verified June 14 2026 — runs cleaner than 3.11 version

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Git | Any recent version |
| Python 3.12 (or 3.11) | 3.12 is primary supported runtime |
| LM Studio | Running on `http://127.0.0.1:1234/v1` with `qwen2.5-7b-instruct` loaded |
| Corpus `.md` files | Copy from local machine or `cgfixit.com/zSafeClaw/` |
| Windows: PowerShell as admin | Linux: bash |

---

## Windows (PowerShell — Recommended)

```powershell
# 1. Clone + venv
git clone https://github.com/CGFixIT/CyClaw.git
cd CyClaw
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. Torch CPU first (keeps install lean + offline-friendly)
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu

# 3. All other deps (pinned, verified Python 3.12 tree)
pip install -r requirements.txt -c constraints.txt

# 4. Env + NLTK one-time (while online)
$env:GROK_API_KEY = "offline-dummy-sk-123"
python -c "import nltk; nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True)"

# 5. Corpus + Index (MANDATORY — must do before first run)
mkdir -p data\corpus
# ← copy your .md files into data\corpus\ NOW, then:
python -m retrieval.indexer

# 6. Run
uvicorn gate:app --reload --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787` → Soul Console terminal loads automatically.

### Windows Smoke Test
```powershell
.\tests\apipsTest.ps1
```
All HTTP endpoint tests should pass green.

---

## Linux (Bash)

```bash
# 1. Clone + venv
git clone https://github.com/CGFixIT/CyClaw.git && cd CyClaw
python3.12 -m venv venv
source venv/bin/activate

# 2. Torch CPU (recommended even on Linux to avoid ~2.5 GB CUDA build)
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu

# 3. All other deps
pip install -r requirements.txt -c constraints.txt

# 4. Env + NLTK one-time
export GROK_API_KEY=offline-dummy-sk-123
python -c "import nltk; nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True)"

# 5. Corpus + Index (MANDATORY)
mkdir -p data/corpus
# copy your .md files into data/corpus/ then:
python -m retrieval.indexer

# 6. Run
uvicorn gate:app --reload --host 127.0.0.1 --port 8787
```

---

## Key Notes

### Why rebuild the index?
ChromaDB moved from 0.4.x → 1.5.x — the on-disk format changed. Any index built
on the web-deployed version is **incompatible** with this build. Additionally,
embeddings now use `normalize_embeddings=True` which changes the vector space.
You **must** rebuild. There is no migration path.

### GROK_API_KEY in offline mode
The dummy key value (`offline-dummy-sk-123`) is fine for `mode: offline` in
`config.yaml`. The key is only validated at Grok call time, which never happens
in offline mode. If you want full hygiene, set it to any non-empty string.

### NLTK offline after first run
`cyclaw_telemetry_kill.env` sets `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`.
The NLTK punkt tokenizer data is cached locally after the first `nltk.download()`
call — subsequent runs are fully offline.

### Test gate
The committed pytest suite is the install gate:

```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short
```

Failures are defects unless a test explicitly skips for a missing optional
service.

### constraints.txt
The `-c constraints.txt` flag pins the full transitive dependency tree for
reproducible builds. Restored to repo on June 14 2026 after accidental deletion.
If absent, `pip install -r requirements.txt` still works but without the full
transitive pin guarantee.

---

## config.yaml — Key Settings to Verify

```yaml
app:
  mode: "offline"                    # keep offline unless you have GROK_API_KEY

models:
  local_llm:
    base_url: "http://127.0.0.1:1234/v1"   # LM Studio default — do not change
    model: "qwen2.5-7b-instruct"            # must match model name in LM Studio exactly
    timeout_sec: 720
    max_tokens: 5000

retrieval:
  min_score: 0.028     # RRF fused-rank threshold (NOT cosine similarity — different scale)
  top_k_semantic: 5
  top_k_keyword: 5
  rrf_k: 60

personality:
  enabled: true
  soul_path: "data/personality/soul.md"
  interaction_ttl_days: 365
```

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

## Troubleshooting

| Symptom | Fix |
|---|---|
| `IndexNotFoundError` on startup | Run `python -m retrieval.indexer` — index not built yet |
| `Collection not found in ChromaDB` | Delete `index/` folder and reindex |
| LLM timeout on query | Increase `timeout_sec` in `config.yaml` — long-context inference is slow on CPU |
| `ModuleNotFoundError: nltk` | Run the NLTK download step (Step 4) |
| `FileNotFoundError: constraints.txt` | File restored to repo June 14 — `git pull` |
| Soul endpoint returns 404 | Set `personality.enabled: true` in `config.yaml` |
| `uvloop` install fails on Windows | Acceptable — uvloop is Linux-only, uvicorn falls back to asyncio automatically |

---

*Built by [Chris Grady](https://cgfixit.com) · Repo: [github.com/CGFixIT/CyClaw](https://github.com/CGFixIT/CyClaw)*  
*Guide generated June 14 2026 — v1.3+ baseline, Python 3.12 verified*
