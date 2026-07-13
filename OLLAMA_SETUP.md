# CyClaw + Ollama Setup Guide

**v1.9+ | Ollama Edition | 10-15 min First Run**

This guide covers installing CyClaw with Ollama as the local LLM backend. Ollama replaces LM Studio as the default -- it's lighter, simpler, and fully open-source.

---

## What's Changed (LM Studio -> Ollama)

| Aspect | Before (LM Studio) | After (Ollama) |
|--------|-------------------|----------------|
| Default port | `1234` | `11434` |
| Default model | `qwen2.5-7b-instruct` | `qwen2.5:7b` |
| Provider name | `lmstudio` | `ollama` |
| Install method | GUI download + model search | `curl \| sh` + `ollama pull` |
| Model format | Multiple (GGUF, etc.) | Ollama Registry (built on GGUF) |
| Auth | Optional API key in UI | Optional via Ollama config |

---

## Prerequisites

| Requirement | Version | Notes |
|-------------|---------|-------|
| Python | 3.12 | Primary supported runtime |
| Git | Any | For cloning |
| Ollama | Latest | See install steps below |

---

## Step 1: Install Ollama

### macOS

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Or download from [ollama.com/download](https://ollama.com/download/mac)

### Linux

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

### Windows (PowerShell as Admin)

```powershell
# Download installer from https://ollama.com/download/windows
# Or use WSL2 and run the Linux install above (recommended)
```

### Verify Ollama

```bash
ollama --version
# Should print version number

# Start the server (keep running in a separate terminal)
ollama serve

# In another terminal, verify the API is up
curl http://127.0.0.1:11434/api/tags
# Should return JSON list of pulled models (empty on first run)
```

---

## Step 2: Pull Your Model

```bash
# Pull the default CyClaw model (recommended)
ollama pull qwen2.5:7b

# Verify it works
ollama run qwen2.5:7b "Say hello"
# Should respond immediately
```

**Other models that work well with CyClaw:**

| Model | Command | Notes |
|-------|---------|-------|
| Qwen 2.5 7B (default) | `ollama pull qwen2.5:7b` | Best balance of quality + speed |
| Mistral 7B | `ollama pull mistral:7b` | Good alternative |
| Llama 3.1 8B | `ollama pull llama3.1:8b` | Meta's latest |
| Qwen 2.5 14B | `ollama pull qwen2.5:14b` | Higher quality, slower |

> **Note:** Model tags are case-sensitive in Ollama. Use `qwen2.5:7b` (lowercase), not `Qwen2.5-7B-Instruct`.

---

## Step 3: Clone + Configure CyClaw

```bash
# Clone the repo
git clone https://github.com/CGFixIT/CyClaw.git
cd CyClaw

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install PyTorch CPU (keeps install lean)
pip install torch==2.4.1+cpu --index-url https://download.pytorch.org/whl/cpu

# Install all other dependencies
pip install -r requirements.txt -c constraints.txt
```

### One-Time NLTK Setup

```bash
python -c "import nltk; nltk.download('punkt', quiet=True); nltk.download('punkt_tab', quiet=True)"
```

---

## Step 4: Configure (Already Done!)

The shipped `config.yaml` is already set for Ollama. Verify these values:

```yaml
app:
  mode: "offline"  # keep offline unless you have GROK_API_KEY

models:
  local_llm:
    provider: "ollama"
    base_url: "http://127.0.0.1:11434/v1"
    model: "qwen2.5:7b"  # must match your `ollama pull` tag exactly
    timeout_sec: 300
    max_tokens: 3000
```

**If you pulled a different model in Step 2,** update the `model` field to match exactly (e.g., `mistral:7b`, `llama3.1:8b`).

---

## Step 5: Build the Index

```bash
# Create corpus directory
mkdir -p data/corpus

# Copy your .md knowledge files into data/corpus/
# Then build the search index:
python -m retrieval.indexer
```

> **This is mandatory.** The indexer creates ChromaDB + BM25 indexes from your corpus. Without it, CyClaw will fail to start.

---

## Step 6: Run

```bash
# Ensure Ollama is running first (in another terminal):
# ollama serve

# Start CyClaw
uvicorn gate:app --reload --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787` in your browser. The Soul Console terminal loads automatically.

---

## Smoke Test

```bash
# Quick curl test
curl -X POST http://127.0.0.1:8787/query \
  -H "Content-Type: application/json" \
  -d '{"query": "What is CyClaw?", "user_confirmed_online": false}'
```

You should get a JSON response with an `answer` field and `model_used: "local"`.

---

## Switching Models

Ollama makes it trivial to swap models without changing CyClaw config:

```bash
# Pull a new model
ollama pull mistral:7b

# Edit config.yaml -> model: "mistral:7b"
# Restart CyClaw (no need to reindex)
```

---

## Ollama Context Size (Advanced)

If you hit the "0% processing" stall on large context queries, increase Ollama's context window:

Set the environment variable before `ollama serve` (recommended, persists for all models):

```bash
export OLLAMA_CONTEXT_LENGTH=12288
ollama serve
```

Or per-session inside an interactive `ollama run` shell (there is no `--num_ctx` CLI flag):

```
ollama run qwen2.5:7b
>>> /set parameter num_ctx 12288
```

> Note: Ollama's default context window is 4096 tokens — below the 8,500-token floor CyClaw's no-stall formula requires. Setting this is **not optional** with the default config.

The config.yaml formula: `Ollama num_ctx >= max_context_tokens + max_tokens + ~1500 headroom`
With defaults: `4000 + 3000 + 1500 = 8500`, so `10000-12288` is the safe range.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Ollama timeout` error | Check `ollama serve` is running; increase `timeout_sec` in config.yaml |
| `Ollama HTTP 404` | Model not pulled: run `ollama pull qwen2.5:7b` |
| `0% processing` stall | Ollama context too small: increase `num_ctx` (see above) |
| `IndexNotFoundError` on startup | Run `python -m retrieval.indexer` first |
| Empty answers | Check corpus files exist in `data/corpus/` |
| Port 11434 in use | Another Ollama instance running: `killall ollama` and retry |

---

## Ollama Commands Quick Reference

```bash
ollama serve              # Start the API server
ollama pull <model>       # Download a model
ollama list               # Show installed models
ollama rm <model>         # Remove a model
ollama ps                 # Show running models
ollama run <model>        # Interactive chat with a model
ollama --help             # Full help
```

---

## Architecture Note

CyClaw's `LocalLLMClient` (in `llm/client.py`) speaks raw HTTP to any OpenAI-compatible `/chat/completions` endpoint. Ollama exposes this at `POST /v1/chat/completions`. Zero adapter code is needed -- just configuration.

```
CyClaw (LocalLLMClient)
  |
  |  POST http://127.0.0.1:11434/v1/chat/completions
  |  { "model": "qwen2.5:7b", "messages": [...], ... }
  v
Ollama (OpenAI-compatible API)
  |
  v
llama.cpp (inference engine, bundled inside Ollama)
```

---

*Built by [Chris Grady](https://cgfixit.com) . Repo: [github.com/CGFixIT/CyClaw](https://github.com/CGFixIT/CyClaw)*
*Ollama migration completed July 2026*
