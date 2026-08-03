# CyClaw — GitHub Setup Guide (Windows · macOS · Linux)

**v1.9.0 | Offline-First | Ollama | ~15 min**
Verified 2026-07-29 against `main`; macOS path re-verified 2026-08-02.

This is the canonical setup guide (`docs/SETUP.md` now points here). For the
full architecture tour — agentic layer, filesystem/SQL connectors, NeMo
Guardrails, the coding harness, and the security model — see
[`README.md`](README.md). This guide covers what's needed to get the core RAG
gateway running, plus how to launch the harness console beside it and exercise
every REST endpoint from a terminal.

**On a Mac, go straight to [macOS (Apple Silicon)](#macos-apple-silicon)** —
the Linux block above it does not work here, for a reason spelled out in that
section. Then:
[running both servers](#running-both-servers-on-macos) ·
[testing every endpoint](#rest-api--testing-every-endpoint-from-the-terminal).

---

## Prerequisites

| Requirement | Notes |
|---|---|
| Git | Any recent version |
| Python 3.12 | Primary supported runtime (`requires-python >=3.12`) |
| [Ollama](https://ollama.com/) | Running on `http://127.0.0.1:11434`, with `qwen3.6:27b` pulled: `ollama pull qwen3.6:27b` |
| Corpus `.md` files (optional) | The repo ships a small sample corpus in `data/corpus/`, so the indexer has something to build against out of the box. Copy your own `.md` files in to replace/extend it — from your own notes, or an existing SafeClaw/PsyClaw-style corpus if you have one. |
| Windows | PowerShell, no admin/elevation needed. If script execution is blocked, run once: `Set-ExecutionPolicy RemoteSigned -Scope CurrentUser` |
| Linux | bash |
| macOS | **Apple Silicon (arm64) on macOS 14 Sonoma or newer**, bash or zsh. See [macOS](#macos-apple-silicon) — its torch step genuinely differs from Linux's, and an Intel Mac cannot satisfy the pinned torch build at all (why: [torch on macOS](#torch-on-macos-plain-build-no-cpu-suffix)) |

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

## Linux (Bash)

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

## macOS (Apple Silicon)

**Do not follow the Linux block above verbatim on macOS.** Its step 2 fails
here, and step 3 then fails a second time for a related reason. Both are
explained under [torch on macOS](#torch-on-macos-plain-build-no-cpu-suffix);
the short version is that the `+cpu` wheel Linux and Windows install does not
exist for macOS, and both `requirements.txt` and `constraints.txt` hardcode
that `+cpu` pin.

Two ways to do this. **Option A** is the fastest correct path if you also want
the coding-harness console; **Option B** is the by-hand core-RAG install.

### Option A — the installer script (handles the torch difference for you)

`macos/install-cyclaw.sh` already branches on `uname -s` = `Darwin` and does
the right thing (`macos/install-cyclaw.sh:124-137`):

```bash
git clone https://github.com/CGFixIT/CyClaw.git && cd CyClaw
bash ./macos/install-cyclaw.sh
```

It finds a Python ≥3.12, creates `~/.CyClaw/venv`, installs the correct torch
build, installs the rest from corrected manifests, writes a `cyclaw` shim, and
adds a PATH entry plus a `cyclaw()` function to your rc file (`~/.zshrc` on
zsh; on macOS bash it preserves the first existing login file among
`~/.bash_profile`, `~/.bash_login`, and `~/.profile`, creating
`~/.bash_profile` only when none exists). Useful flags:
`--repo-path ~/src/CyClaw` (use an existing clone), `--skip-python-deps`,
`--no-profile-edit`, `--no-path-edit`. Uninstall with
`bash ./macos/uninstall-cyclaw.sh` (`--remove-home` also deletes `~/.CyClaw`,
with a prompt).

**What it deliberately does NOT do** — verified against the script, which
contains zero references to any of these. Option A is not a superset of
Option B; it is a different target:

| Not handled | You still need to |
|---|---|
| Ollama install / `ollama serve` / model pull | Do [Ollama on macOS](#ollama-on-macos) yourself |
| The retrieval index | Run `python -m retrieval.indexer` — otherwise `/query` 503s |
| `CYCLAW_API_KEY` | Export it before launching, or the console's state-changing routes fail closed with 401. `macos/invoke-cyclaw.sh:66-68` warns about this at launch; the key is deliberately never written into the shim, since that would put a secret in a profile file on disk |
| `GROK_API_KEY` | Export it (any non-empty value offline) |
| Your own corpus | Copy `.md` files into `data/corpus/` |

It also installs its venv at `~/.CyClaw/venv`, **not** into your clone's
`.venv` — so after Option A you still have no environment in the clone itself.
This targets the **harness console** on `127.0.0.1:8790`
([`docs/HARNESS_MACOS.md`](docs/HARNESS_MACOS.md)), which is a different thing
from the core RAG gateway on `:8787`. **If you want the RAG gateway, use
Option B** — or run both, in which case do Option B as well.

### Option B — by hand, step by step

```bash
# 0. Prerequisites.
#    Python 3.12+ — either works:
#      brew install python@3.12
#    or the official installer: https://www.python.org/downloads/macos/
#    Ollama — see "Ollama on macOS" below; needed before step 6, not before 1.
#
#    Confirm you are on Apple Silicon (must print "arm64" — an Intel Mac
#    cannot install this repo's pinned torch at all; see "torch on macOS"):
uname -m
#    Confirm macOS 14 (Sonoma) or newer — the only macOS torch wheels published
#    at this pin are tagged macosx_14_0, which is a floor, not a preference:
sw_vers -productVersion

# 1. Clone + venv
git clone https://github.com/CGFixIT/CyClaw.git && cd CyClaw
python3.12 -m venv .venv
source .venv/bin/activate

# 2. Torch FIRST, and PLAIN — no "+cpu" suffix, no --index-url override.
#    Apple Silicon has no separate CPU/CUDA build to disambiguate, so PyPI
#    ships a single arm64 wheel. This is the step the Linux block gets wrong
#    for macOS.
pip install "torch==2.13.0"

# 3. Everything else — but from copies of both manifests with the torch and
#    PyTorch-index lines removed. Without this, pip tries to reconcile the
#    plain torch you just installed against the "+cpu" pin those files
#    hardcode, and the install fails. Identical to what CI's macos-latest leg
#    runs (.github/workflows/ci.yml:332-333).
grep -v -e '^torch==' -e '^--extra-index-url https://download.pytorch.org' \
    requirements.txt > /tmp/requirements-macos.txt
grep -v '^torch==' constraints.txt > /tmp/constraints-macos.txt
pip install -r /tmp/requirements-macos.txt -c /tmp/constraints-macos.txt \
    --ignore-installed PyYAML

# 4. Required env (any non-empty value works — see "GROK_API_KEY" below)
export GROK_API_KEY=dummy

# 4b. API key for the Soul + operator consoles. /query, /health and the
#     terminal UI need no key, but every /soul/* and /ops/* route fails CLOSED
#     with 401 without one — see "CYCLAW_API_KEY" below. Generate a real value
#     rather than typing a word: openssl ships with macOS, no install needed.
export CYCLAW_API_KEY="$(openssl rand -hex 20)"
echo "$CYCLAW_API_KEY"          # copy this — you paste it into the console UI

# 5. Build the retrieval index (see "Is the index really mandatory?" below)
python -m retrieval.indexer

# 6. Run
uvicorn gate:app --reload --host 127.0.0.1 --port 8787
```

Open `http://127.0.0.1:8787` → the terminal UI loads automatically.

`export` lasts only for that Terminal tab. To persist both keys, append them to
your shell rc file — zsh is the macOS default since Catalina, so that is
`~/.zshrc` unless you switched:

```bash
cat >> ~/.zshrc <<'EOF'
export GROK_API_KEY=dummy
export CYCLAW_API_KEY="paste-the-value-you-generated"
EOF
source ~/.zshrc
echo "$CYCLAW_API_KEY"          # confirm it survived
```

Check which shell you are actually in with `echo $SHELL` first. On bash, append
to the first existing login file in this order: `~/.bash_profile`,
`~/.bash_login`, `~/.profile`; create `~/.bash_profile` only when none exists.
macOS bash login shells do not read `~/.bashrc`.

### Running both servers on macOS

CyClaw ships **two** independent local web apps. They are separate processes on
separate ports; neither needs the other, and running one does not start the
other.

```bash
# 1) The RAG gateway — serves static/terminal.html at /, plus the whole REST API
source .venv/bin/activate
uvicorn gate:app --host 127.0.0.1 --port 8787
#    → http://127.0.0.1:8787

# 2) The coding-harness console — serves static/harness.html
#    In a SECOND Terminal tab (this one blocks too):
source .venv/bin/activate
python -m harness.server        # → http://127.0.0.1:8790
```

Three things about the harness command that differ from the gateway:

- **Use `python -m harness.server`, not `cyclaw-harness`.** `cyclaw-harness` is
  a `[project.scripts]` console script, and pip writes that shim into the venv
  only when the CyClaw *project itself* is installed (`pip install -e .`). The
  install above installs `requirements.txt`, which is a third-party pin list
  with no self-install line — so after following this guide exactly,
  `cyclaw-harness` is `command not found`. The `-m` form always works and is
  what both shipped launchers use (`macos/invoke-cyclaw.sh:87`,
  `powershell/Invoke-CyClaw.ps1:78`). If you want the short names, add
  `pip install -e . -c constraints.txt` after step 3. The same applies to
  `cyclaw-server`, `cyclaw-index`, `cyclaw-mcp`, `cyclaw-metrics`, and
  `cyclaw-clear-cache` — every `python -m …` form in this guide is chosen
  because it needs no self-install.
- **`uvicorn harness.server:app` also works, but the `-m` form is safer.** The
  module exposes `app` lazily — built on first attribute access, not at import,
  so importing `harness.server` never reads or creates `~/.CyClaw`. The catch
  is that the uvicorn form skips the bind-address guard in `main()`, so
  `--host 0.0.0.0` opens a public socket that `python -m harness.server` would
  have refused. `TrustedHostMiddleware` still rejects any non-loopback `Host`
  header in both forms, so a bound socket is not an open door — but the `-m`
  form keeps both layers.
- **The port is 8790, and you change it with an env var, not a flag:**

  ```bash
  CYCLAW_HARNESS_PORT=8795 python -m harness.server
  ```

  Values outside 1024–65535 are rejected at startup. `CYCLAW_HARNESS_HOST`
  exists too but only accepts loopback addresses — a non-loopback host exits
  immediately, by threat-model design.

Add `--reload` to the `uvicorn gate:app` line while editing code; leave it off
otherwise (it doubles the process count and re-imports the whole retrieval
stack on every save).

### Ollama on macOS

Same prerequisite as every platform, installed differently. Do this **before**
step 6 above — `uvicorn` will start without it, but `/query` needs a model
behind it.

Preferred: download the signed `.app` from
[ollama.com/download/mac](https://ollama.com/download/mac) and launch it. The
`curl -fsSL https://ollama.com/install.sh | sh` one-liner in
[`OLLAMA_SETUP.md`](OLLAMA_SETUP.md) also works, but it is a pipe-to-shell —
prefer the signed app on a machine you care about.

```bash
ollama --version            # sanity check
ollama serve                # only if you did NOT install the .app — see below
ollama pull qwen3.6:27b     # the model config.yaml expects by default (dense
                            # ~27B — multi-GB pull). Want a lighter one? See
                            # "Running a different local model" below.

# Ollama's own native API — confirms the daemon is up and lists pulled models:
curl -s http://127.0.0.1:11434/api/tags

# The OpenAI-compatible endpoint CyClaw actually talks to
# (config.yaml -> models.local_llm.base_url) is a DIFFERENT path on the same port:
curl -s http://127.0.0.1:11434/v1/models
```

The `.app` runs `ollama serve` for you, so a separate `ollama serve` will fail
with an address-already-in-use error — that is the app already doing its job,
not a problem to fix.

### macOS smoke test

There is no macOS-native equivalent of `windows-smoke.ps1`. Use the test suite
as the install gate, or a one-line readiness check against a running server:

```bash
GROK_API_KEY=dummy pytest tests/ -q --tb=short
curl -s http://127.0.0.1:8787/health
```

---

## REST API — testing every endpoint from the Terminal

Every route below is on the RAG gateway (`127.0.0.1:8787`). `curl` and
`python3` both ship with macOS; nothing extra to install. Pipe anything
through `python3 -m json.tool` to pretty-print it.

Export the key once per tab so the authenticated examples work as written:

```bash
export CYCLAW_API_KEY="the-value-you-generated"
AUTH="Authorization: Bearer $CYCLAW_API_KEY"
```

### Open routes (no key)

| Route | Method | What it does |
|---|---|---|
| `/` | GET | serves `static/terminal.html` — the browser console |
| `/static/*` | GET | static assets for that page |
| `/health` | GET | readiness: per-service status, `index_ready`, `graph_ready`, `mode` |
| `/query` | POST | the RAG request path — rate-limited (60/min per IP), sanitized |

```bash
# Readiness. "degraded" without Ollama running is NORMAL, not an error.
curl -s http://127.0.0.1:8787/health | python3 -m json.tool

# A normal query.
curl -s -X POST http://127.0.0.1:8787/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What is RRF fusion?"}' | python3 -m json.tool
```

If the corpus does not answer it, the response comes back with
`needs_confirm: true` and a `confirm_message` instead of an answer. Re-POST the
same query with your decision — this is the user gate, and it is the only way
an external provider is ever reached:

```bash
# Decline escalation → a local best-effort answer
curl -s -X POST http://127.0.0.1:8787/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"Who won the 2026 World Cup?","user_confirmed_online":false}'

# Confirm escalation, naming the provider. Still requires app.mode: "hybrid"
# AND that provider enabled in config.yaml AND its key env var set — all three,
# or it silently answers offline instead.
curl -s -X POST http://127.0.0.1:8787/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"Who won the 2026 World Cup?","user_confirmed_online":true,"online_provider":"grok"}'
```

`online_provider` accepts only `"grok"` or `"claude"`. The request model is
`extra='forbid'`, so a typo'd field name returns `422`, not a silent ignore.

### Authenticated routes (Bearer `CYCLAW_API_KEY`)

All of these return `401` when the key is missing **or** when `CYCLAW_API_KEY`
is unset on the server — fail-closed in both directions.

| Route | Method | What it does |
|---|---|---|
| `/soul` | GET | current soul text + version metadata |
| `/soul/propose` | POST | advisory injection scan of a proposed soul — never writes |
| `/soul/apply` | POST | enforced scan + atomic write; **requires a `reason`** |
| `/soul/reload` | POST | re-read `soul.md` from disk |
| `/soul/restore` | POST | restore from the `.bak` copy |
| `/audit/summary` | GET | aggregate audit stats only — never raw query text |
| `/ops/sync` | POST | Dropbox corpus sync shim |
| `/ops/agentic` | POST | agentic-layer shim |
| `/ops/fsconnect` | POST | filesystem-connector shim |
| `/ops/sqlconnect` | POST | SQL-connector shim |

```bash
curl -s -H "$AUTH" http://127.0.0.1:8787/soul | python3 -m json.tool
curl -s -H "$AUTH" http://127.0.0.1:8787/audit/summary | python3 -m json.tool

# Dry-run a soul change — scans and reports, writes nothing.
curl -s -X POST http://127.0.0.1:8787/soul/propose \
  -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"new_soul":"# Soul\n\nBe concise.","reason":"testing the scanner"}'

# Operator consoles. "status" is the read-only action on each; every one of the
# four takes an `action` field and nothing else is required.
curl -s -X POST http://127.0.0.1:8787/ops/sync \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{"action":"status"}'
curl -s -X POST http://127.0.0.1:8787/ops/agentic \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{"action":"status"}'
curl -s -X POST http://127.0.0.1:8787/ops/fsconnect \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{"action":"status"}'
curl -s -X POST http://127.0.0.1:8787/ops/sqlconnect \
  -H "$AUTH" -H 'Content-Type: application/json' -d '{"action":"status"}'
```

**Four of these mutate state. Do not treat any of them as a probe.**

| Route | What it changes |
|---|---|
| `POST /soul/apply` | rewrites `soul.md`, rotates the old copy to `.bak`, records a version row. Requires a non-empty `reason` — an architectural invariant, not a validation nicety, so there is no flag to skip it |
| `POST /soul/restore` | **also rewrites `soul.md`**, from the `.bak`. It reaches the same atomic write path with a hardcoded reason and `scan=False`, so it skips the injection scan `/soul/apply` enforces. Firing it "just to see" silently replaces your live soul with stale backup content |
| `POST /ops/sync` | with `action: "sync"` this is a **real rclone corpus transfer**. Only `"dry_run": true` makes it read-only; `action: "status"` is the safe probe |
| `POST /ops/agentic` | with `action: "apply-skill"` this writes a skill file. `action: "status"` is the safe probe |

Every other route above, and the `"status"` action on all four `/ops/*`
endpoints, is read-only.

### Reading the status codes

| Code | Means |
|---|---|
| `200` | success — including a `needs_confirm: true` gate response, which is not an error |
| `400` | your `Host` header is not in `config.yaml`'s `allowed_hosts`, or the injection filter rejected the query |
| `401` | missing/invalid `CYCLAW_API_KEY` (or it is unset server-side) |
| `404` | on a `/soul/*` route: `personality.enabled` is `false` in `config.yaml` |
| `422` | request body failed Pydantic validation — usually a misspelled field, since the models forbid extras |
| `429` | rate limit — 60 requests/min per IP |
| `503` | `INDEX_NOT_FOUND` — run `python -m retrieval.indexer` |
| `504` | the graph exceeded `api.graph_timeout_sec` (660s) |

### The harness console's API

The coding-harness console on `:8790` is a **separate app with its own route
set** (`/api/status`, `/api/chat`, `/api/sessions`, …), documented in
[`docs/HARNESS_MACOS.md`](docs/HARNESS_MACOS.md). None of the routes above
exist on `:8790`, and none of the harness routes exist on `:8787`.

---

## Key Notes

### torch on macOS: plain build, no `+cpu` suffix

The single most common way a macOS install fails here, and the reason macOS
gets its own section above rather than sharing Linux's.

`requirements.txt` and `constraints.txt` both hardcode `torch==2.13.0+cpu`
against `https://download.pytorch.org/whl/cpu`. That `+cpu` local-version
suffix exists on Linux and Windows specifically to avoid pip resolving the
default CUDA-bundled wheel. **Apple Silicon has no CUDA build to disambiguate
from, so no `+cpu`-suffixed macOS wheel is published at all** — that index
404s for macOS, confirmed on this repo's first `macos-latest` CI run
(`.github/workflows/ci.yml:168-173`).

Verified against PyPI, 2026-08-02: `torch==2.13.0` publishes exactly six macOS
wheels, and every one of them is `macosx_14_0_arm64`
(`torch-2.13.0-cp312-cp312-macosx_14_0_arm64.whl` is the Python 3.12 one).
Two consequences worth stating plainly:

- **macOS 14 (Sonoma) is the floor.** The `macosx_14_0` platform tag is a
  minimum, not a preference — macOS 13 and earlier cannot install this wheel.
- **Intel Macs are not supported at this pin.** There is no `x86_64` macOS
  wheel for torch 2.13.0 on PyPI, so an Intel Mac cannot satisfy the pinned
  build. Running CyClaw's core RAG gateway there needs a different torch pin
  than the one this repo ships, which is outside what this guide covers.

Three places in the repo already implement the correct macOS behavior and
agree with each other — the CI lane (`.github/workflows/ci.yml:316-334`), the
installer (`macos/install-cyclaw.sh:124-137`), and
[`docs/HARNESS_MACOS.md`](docs/HARNESS_MACOS.md). The by-hand steps in the
macOS section above are those same commands.

### Running a different local model

`config.yaml` ships `models.local_llm.model: "qwen3.6:27b"` — a dense ~27B
model — and the setup steps pull exactly that. It is the heaviest default this
project has shipped: expect a multi-gigabyte pull and meaningful RAM use. On
modest hardware a smaller tag (`qwen2.5:7b`, `mistral:7b`) is a perfectly
supported swap. Either direction, two things must move together, or CyClaw
hangs instead of failing loudly:

1. **The tag must match what you actually pulled, in _two_ config keys.**
   `models.local_llm.model` **and** `guardrails.model` are both passed through
   to Ollama, and `config-guard`'s C11 check fails the build if they drift
   apart. A tag mismatch against Ollama is an `Ollama HTTP 404`. Tags are
   case-sensitive — use exactly what `ollama list` prints.
2. **Ollama's context length must have headroom**, or generation stalls at
   `0% processing` rather than erroring. The formula (from `config.yaml`'s own
   comment) is:

   ```
   num_ctx  >=  retrieval.max_context_tokens + local_llm.max_tokens + ~1500
   ```

   At the shipped `4000 + 3000 + 1500`, that means **10,000–12,288**. Set it
   server-wide *before* starting Ollama — `num_ctx` is not a CLI flag:

   ```bash
   export OLLAMA_CONTEXT_LENGTH=12288
   ollama serve
   ```

This is the single most common "CyClaw hangs" cause, and it is *more* likely on
a large model, not less — a bigger model does not raise `num_ctx` for you.
Full detail, including the per-session `/set parameter num_ctx` alternative:
[`OLLAMA_SETUP.md`](OLLAMA_SETUP.md).

The shipped `local_llm.timeout_sec: 600` and `max_tokens: 3000` are sized for a
dense ~27B model (see `CLAUDE.md`'s load-bearing-numbers table), so they already
match the default above — no timeout tuning needed. `timeout_sec` must stay
below `api.graph_timeout_sec` (660) if you do raise it. Dropping to a smaller
model needs no config change beyond the two `model:` keys; the generous timeout
simply goes unused.

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
    model: "qwen3.6:27b"                     # must match a model tag actually pulled in Ollama
    timeout_sec: 600                        # must stay < api.graph_timeout_sec (660)
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
optional **NeMo Guardrails** content-safety layer, and a separate
**coding-harness** console on `127.0.0.1:8790` (Windows via `powershell/`,
macOS/Linux via `macos/` — same Python app, different install glue). See
README's own section for each — [Agentic Layer](README.md#agentic-layer-v160),
[Filesystem & SQL Connectors](README.md#filesystem--sql-connectors-v18),
[NeMo Guardrails](README.md#nemo-guardrails-v18),
[Coding Harness Console](README.md#coding-harness-console-v19). A
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
| **macOS:** `ERROR: No matching distribution found for torch==2.13.0+cpu` | You followed the Linux/Windows torch line. macOS has no `+cpu` wheel — use `pip install "torch==2.13.0"` and the stripped-manifest step ([macOS](#macos-apple-silicon)) |
| **macOS:** torch installs, then `requirements.txt` fails on a torch version conflict | Step 3's manifest stripping was skipped — both manifests hardcode the `+cpu` pin, so they must be filtered ([macOS](#macos-apple-silicon)) |
| **macOS:** `No matching distribution found for torch==2.13.0` (no `+cpu`) | Either an Intel Mac (no `x86_64` wheel exists at this pin) or macOS < 14 (the wheel is `macosx_14_0_arm64`) — see [torch on macOS](#torch-on-macos-plain-build-no-cpu-suffix) |
| **macOS:** `ollama serve` fails with address already in use | The Ollama `.app` is already serving `:11434` — nothing to fix |
| Soul endpoints return 404 | Set `personality.enabled: true` in `config.yaml` |
| `uvloop` install fails on Windows | Expected — uvloop is Linux-only; uvicorn falls back to asyncio automatically |

---

*Built by [Chris Grady](https://cgfixit.com) · Repo: [github.com/CGFixIT/CyClaw](https://github.com/CGFixIT/CyClaw)*
*v1.9.0, Python 3.12 verified — last accuracy pass 2026-07-29*
