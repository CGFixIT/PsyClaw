# CyClaw + Ollama Setup Guide

**v1.9+ | Ollama Edition | 10-15 min First Run**

This guide covers installing CyClaw with Ollama as the local LLM backend. Ollama replaces LM Studio as the default -- it's lighter, simpler, and fully open-source.

---

## What's Changed (LM Studio -> Ollama)

| Aspect | Before (LM Studio) | After (Ollama) |
|--------|-------------------|----------------|
| Default port | `1234` | `11434` |
| Default model tag | `qwen2.5-7b-instruct` | `qwen2.5:7b` → now `qwen3.8:27b-mlx` |
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
ollama pull qwen3.8:27b-mlx

# Verify it works
ollama run qwen3.8:27b-mlx "Say hello"
# Should respond immediately
```

**Other models that work well with CyClaw:**

| Model | Command | Notes |
|-------|---------|-------|
| Qwen 3.8 27B MLX 4-bit (default) | `ollama pull qwen3.8:27b-mlx` | What `config.yaml` ships. Apple Silicon MLX 4-bit, ~18 GB. Third-party M5 Pro 48 GB reports ~29–34 tok/s decode — **measure on your machine** (`python3 scripts/measure_local_llm_throughput.py`). |
| Qwen 3.8 27B MLX NVFP4 | `ollama pull qwen3.8:27b-nvfp4` | Same ~18 GB. NVFP4 4-bit (higher quality than q4_K_M per Ollama's MLX blog). Candidate upgrade on 48 GB; switch **both** `models.local_llm.model` and `guardrails.model` (C11) only after measuring tok/s + answer quality. |
| Qwen 3.8 27B MLX MXFP8 | `ollama pull qwen3.8:27b-mxfp8` | ~32 GB 8-bit MLX. Fits 48 GB only with a tight KV budget — measure, watch Activity Monitor, do not assume. |
| Qwen 3.8 27B MLX BF16 | `ollama pull qwen3.8:27b-mlx-bf16` | ~56 GB. **Does not fit 48 GB unified.** |
| Qwen 3.8 27B (generic GGUF) | `ollama pull qwen3.8:27b` | Same weights without the MLX tag. Use on Intel/Windows/Linux, then set `models.local_llm.model` and `guardrails.model` to this tag |
| Qwen 2.5 7B | `ollama pull qwen2.5:7b` | Much lighter; the prior default. Best balance of quality + speed on modest hardware |
| Mistral 7B | `ollama pull mistral:7b` | Good alternative |
| Llama 3.1 8B | `ollama pull llama3.1:8b` | Meta's latest |
| Qwen 2.5 14B | `ollama pull qwen2.5:14b` | Higher quality, slower |

> **Note:** Model tags are case-sensitive in Ollama. Use the exact lowercase tag `ollama list` prints (e.g. `qwen3.8:27b-mlx`), not a display name like `Qwen3.8-27B-Instruct`.
>
> **Changing model = changing `config.yaml`.** `models.local_llm.model` AND `guardrails.model` must both match the tag you pulled — `config-guard`'s C11 check fails the build if they drift apart. Smaller model? Everything still works. Larger? Re-check `num_ctx` below.

---

## Step 3: Clone + Configure CyClaw

```bash
# Clone the repo
git clone https://github.com/CGFixIT/CyClaw.git
cd CyClaw

# Create virtual environment
python3.12 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install the pinned PyTorch CPU wheel first (Windows/Linux)
pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu

# Install all other dependencies
pip install -r requirements.txt -c constraints.txt
```

Do not download NLTK `punkt` data. CyClaw deliberately uses
`retrieval/stemmer.py`'s local regex tokenizer plus `PorterStemmer`; loading
punkt would add an unused data surface that the accepted-risk guardrails in
[`SECURITY.md`](../../SECURITY.md) explicitly prohibit.

On macOS, do not use the `+cpu` command above. Follow the canonical
[`setup-guide.md` macOS section](../../setup-guide.md#macos-apple-silicon),
which installs plain `torch==2.13.0` and strips the Linux/Windows Torch lines
from the manifests for that install.

---

## Step 4: Configure (Already Done!)

The shipped `config.yaml` is already set for Ollama. Verify these values:

```yaml
app:
  mode: "hybrid"  # shipped value; Grok/Claude still require provider enablement, a real key, and per-query confirmation

models:
  local_llm:
    provider: "ollama"
    base_url: "http://127.0.0.1:11434/v1"
    model: "qwen3.8:27b-mlx"  # must match your `ollama pull` tag exactly
    timeout_sec: 720     # what ships; sized for the dense ~27B MLX default on M5 Pro class
    max_tokens: 4096
```

**If you pulled a different model in Step 2,** update **both** `models.local_llm.model` and `guardrails.model` to match it exactly (e.g. `mistral:7b`, `llama3.1:8b`) — `config-guard`'s C11 check fails the build if the two drift apart.

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

Ollama makes swapping models cheap — no reindex, no reinstall. It does take a
small config edit (two keys, see below):

```bash
# Pull a new model
ollama pull mistral:7b

# Edit config.yaml -> BOTH keys must match the tag you pulled:
#   models.local_llm.model: "mistral:7b"
#   guardrails.model:       "mistral:7b"
# (config-guard's C11 check fails the build if they disagree)

# Restart CyClaw (no need to reindex — the index is model-independent;
# it is built from the embedding model, not the chat model)
```

---

## Thinking / Reasoning (disabled in the shipped config)

`qwen3.8:27b-mlx` is a thinking-capable model, and **Ollama turns thinking on by
itself for such a model whenever a request carries no reasoning control**. The
shipped `config.yaml` therefore sets it off explicitly:

```yaml
models:
  local_llm:
    reasoning_effort: "none"
```

Accepted values are `none`, `low`, `medium`, `high`, `max`. Only `none` disables
thinking; an unrecognized value fails at CyClaw startup with a `ConfigError`
rather than reaching Ollama. **Omitting the key leaves thinking ON** — that is
the backward-compatible path for a custom config, not a way to turn it off.

### The two endpoints do not share a vocabulary

| Endpoint | Control | Used by |
|---|---|---|
| `/v1/chat/completions` (OpenAI-compatible) | `"reasoning_effort": "none"` | `/query`, the harness console and `/loop`, the agentic local proposer, NeMo guardrails |
| `/api/chat`, `/api/generate` (native) | `"think": false` | `scripts/measure_local_llm_throughput.py` |

Sending `think` to the OpenAI-compatible endpoint does nothing, and
`reasoning_effort` is not accepted on the native ones. There is no `"none"`
spelling for native `think` — the disable form there is the boolean `false`.

### What does NOT disable thinking

- **Lowering `max_tokens`.** That shrinks the reply budget; the model still
  reasons, and now has less room left for the answer.
- **Changing the context length (`num_ctx`).**
- **Changing `temperature`.**
- **Hiding a `<think>` block in the response.** The tokens were still generated
  and still cost time. Disabled and hidden are not the same thing.

### Preflight: confirm your Ollama build accepts it

Ollama validates this field server-side and returns an error for a value it does
not know. Older builds accepted only `high`/`medium`/`low`/`true`/`false` — no
`none`. Ollama publishes no minimum version for this, so **test it rather than
trusting a version number**, on the machine that actually serves the model:

```bash
curl -sS http://127.0.0.1:11434/v1/chat/completions -H 'Content-Type: application/json' -d '{"model":"qwen3.8:27b-mlx","messages":[{"role":"user","content":"2+2"}],"reasoning_effort":"none","max_tokens":16,"stream":false}'
```

A normal completion means you are fine. A `400` mentioning `invalid think value`
means that build predates `none`: upgrade Ollama, or remove the key from
`config.yaml` (which leaves thinking on) until you do.

### Applying a change

Restart CyClaw after editing `config.yaml`. **Ollama itself does not need a
restart** — the control travels on each request, unlike `num_ctx` below.

The field is sent only when the *active resolved* local backend is Ollama. If
`models.local_llm.fallback` is enabled and CyClaw fails over to LM Studio, the
Ollama-only field is dropped from that request automatically.

---

## Ollama Context Size (Advanced)

If you hit the "0% processing" stall on large context queries, increase Ollama's context window:

Set the environment variable before `ollama serve` (recommended, persists for all models):

```bash
export OLLAMA_CONTEXT_LENGTH=16384
ollama serve
```

Or per-session inside an interactive `ollama run` shell (there is no `--num_ctx` CLI flag):

```
ollama run qwen3.8:27b-mlx
>>> /set parameter num_ctx 16384
```

> Note: setting this explicitly is **not optional** with the default config. Older Ollama builds default the context window to 4096 tokens — below the ~13,600-token floor CyClaw's no-stall formula requires, producing the silent stall. Newer builds instead derive the default from available VRAM (per [docs.ollama.com/context-length](https://docs.ollama.com/context-length): 4k below 24 GiB, 32k from 24–48 GiB, 256k above), which on a 48 GB Mac over-provisions KV memory instead. An explicit `16384` is deterministic across Ollama versions and matches what the KV budget in `macos/ollama-mlx.env` was sized for.

The config.yaml formula: `Ollama num_ctx >= max_context_tokens + max_tokens + ~1500 headroom`
With defaults: `8000 + 4096 + 1500 = 13596`, so **16384** remains the recommended value (~2.8k tokens spare — deliberate, because the ~4-chars/token estimate under-counts Qwen3 BPE on technical text; see the `retrieval.max_context_tokens` comment in `config.yaml`).

### The agentic real-repo coding loop needs more headroom than that

The formula above is derived only from the `/query` RAG path's budget
(`retrieval.max_context_tokens` + `models.local_llm.max_tokens`). It is **not**
enough by itself if you also drive `agentic/real_repo_loop.py` (the
`real-repo-run` / `real-repo-run-plan` CLI subcommands, or the harness
console's `/api/agent/run`) against the same Ollama instance — that pathway's
per-iteration prompt can legitimately be several times larger, and the
"0% processing" stall applies to it identically.

Summing the loop's own documented per-component caps (`agentic/real_repo_loop.py`):
a declared plan folded into the prompt (`_MAX_PLAN_CHARS`, 6,000 chars), existing
files read for edit-in-place context (`_MAX_TOTAL_READ_CHARS`, 12,000 chars),
prior-iteration verification feedback (`_MAX_FEEDBACK_TOTAL_CHARS`, at least
4,000 chars once check output is included), quoted GitHub PR/issue context
capped in `agentic/cli.py` (8,000 chars), the fixed system prompt (~900 chars),
and an instruction up to 8,192 chars via the harness route (`harness/schemas.py`)
— the worst case is roughly **39,000–40,000 characters of INPUT alone for one
iteration**, before reserving any output budget. At this project's own
~4-chars/token convention (see the formula above), that is approximately
**9,750–10,000 input tokens** — which by itself can already approach or exceed
the 16384 window recommended above, a number sized only for the
smaller RAG-path formula.

This is arithmetic over the loop's own stated caps, not a number CyClaw states
anywhere as a recommendation — treat it as a floor to reason from, not a
guarantee. Real invocations are usually much smaller (a short instruction, no
`read_paths`, no plan file); the worst case only bites when you actually use
several of these inputs together (e.g. a declared plan **and** several
`read_paths` **and** a PR/issue's context on the same run).

If you use `real-repo-run`/`real-repo-run-plan` with `read_paths`, a declared
plan, or GitHub context, don't just clear the RAG-path minimum — size for the
larger pathway instead:

```bash
export OLLAMA_CONTEXT_LENGTH=24576   # or higher; measure for your actual usage
ollama serve
```

Neither proposer client (`agentic/harness_optimizer/model_adapter.py`'s
`LocalProposerClient`, used by default, or
`agentic/deepagent_github/chat_client.py`'s `ChatModelProposerClient`, used
with `--provider`) sends `num_ctx` in its own request — exactly like the RAG
path, this is 100% an out-of-band, operator-set Ollama setting, and neither
client can request a bigger window for a single large call on your behalf.

**On Apple Silicon specifically** (e.g. a Mac with 48GB of unified memory): a
larger `num_ctx` grows Ollama's KV-cache, and unlike a discrete-GPU box, that
cache shares the *same* memory pool as the model weights, CyClaw's own Python
process (ChromaDB + embeddings + FastAPI), the OS, and anything else you have
open — there is no separate VRAM budget to fall back on. This repo does not
ship a measured GB-per-context-length figure for `qwen3.8:27b-mlx` to cite here,
so don't guess at a number: watch actual usage (Activity Monitor, or
`ollama ps` for the running model's reported size) after raising `num_ctx`,
rather than maximizing it up front on the assumption that more is free.

---

## Measure tok/s (do this on the Mac, not from a remote agent)

A remote checkout cannot see `127.0.0.1:11434` on your laptop. The numbers
in `config.yaml` comments are **timeout budgets**, not claimed throughput.
To replace third-party figures with *your* quant + prompt mix:

```bash
# Ollama must already be up, with macos/ollama-mlx.env sourced into *that*
# process (quit the .app fully if it was started from Spotlight).
python3 scripts/measure_local_llm_throughput.py
python3 scripts/measure_local_llm_throughput.py --model qwen3.8:27b-nvfp4 --json
```

The script calls Ollama's native `/api/generate` and prints prefill tok/s,
decode tok/s, load ms. Warmup is on by default so the first 27B cold-load
does not poison the decode sample. Exit 2 = Ollama down; exit 3 = generate
failed (wrong tag, stall, timeout).

## MLX quant tunings (48 GB M5 Pro)

Weight quant is the **Ollama tag**, not an environment variable. Official
library tags as of 2026-08:

| Tag | Disk | Fits 48 GB? | Role |
|---|---|---|---|
| `qwen3.8:27b-mlx` | ~18 GB | yes | **shipped default**, 4-bit MLX |
| `qwen3.8:27b-nvfp4` | ~18 GB | yes | same RAM, NVFP4 4-bit (Ollama MLX blog: better perplexity vs q4_K_M). Measure before switching C11 tags. |
| `qwen3.8:27b-mxfp8` | ~32 GB | maybe | 8-bit. KV + CyClaw Python + OS share the remaining ~16 GB. Measure; expect swap if `num_ctx` is high. |
| `qwen3.8:27b-mlx-bf16` | ~56 GB | **no** | do not pull on 48 GB |

Runtime knobs (source before `ollama serve`; documented in Ollama's FAQ):

```bash
set -a
. macos/ollama-mlx.env
set +a
ollama serve
```

That file sets `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_KEEP_ALIVE=30m`,
`OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`,
`OLLAMA_FLASH_ATTENTION=1`, `OLLAMA_KV_CACHE_TYPE=q8_0`. Flash-attn and KV
q8_0 are llama.cpp-runner knobs that Ollama applies when the backend
supports them; they may be no-ops on a pure MLX tag and still help if you
fall back to `qwen3.8:27b` GGUF. Do **not** raise `OLLAMA_NUM_PARALLEL`
on this class of machine — it multiplies KV RAM and stalls both `/query`
and harness `/loop`.

`macos/setup-from-clone.sh` sources the same file when *it* launches
`ollama serve`. An already-running Ollama.app ignores it until quit.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `Ollama timeout` error | Check `ollama serve` is running; increase `timeout_sec` in config.yaml |
| `Ollama HTTP 404` | Model not pulled, or the tag does not match `config.yaml`: run `ollama pull qwen3.8:27b-mlx` (or set `model:` to what `ollama list` shows) |
| `0% processing` stall | Ollama context too small: increase `num_ctx` (see above) |
| `IndexNotFoundError` on startup | Run `python -m retrieval.indexer` first |
| Empty answers | Check corpus files exist in `data/corpus/` |
| Port 11434 in use | Another Ollama instance running: `killall ollama` and retry |

---

## Ollama Commands Quick Reference

Substitute a real tag for `<model>` — this is a reference table, not a block to
paste as-is.

```text
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
  |  { "model": "qwen3.8:27b-mlx", "messages": [...], ... }
  v
Ollama (OpenAI-compatible API)
  |
  v
llama.cpp (inference engine, bundled inside Ollama)
```

---

*Built by [Chris Grady](https://cgfixit.com) . Repo: [github.com/CGFixIT/CyClaw](https://github.com/CGFixIT/CyClaw)*
*Ollama migration completed July 2026*
