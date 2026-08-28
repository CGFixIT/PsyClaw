# CyClaw Timeout & Token-Budget Audit and Retune — 2026-08-28

Full-stack review of every timeout, token, and context-window value across the Mac
hardware → Ollama daemon → `qwen3.8:27b-mlx` → RAG pipeline → external providers →
harness/agentic loop → web consoles, followed by a researched retune applied on
branch `claude/clone-github-origin-main-6dstt1`. Companion to the sizing comments
in `config.yaml` (which stays the single source of truth for every tunable — this
report explains and evidences; it does not own numbers).

## What changed (the retune)

| Setting | Before | After | Why |
|---|---|---|---|
| `retrieval.max_context_tokens` (`config.yaml`) + `graph.py CHARS_PER_TOKEN` 4 → **3** | 4000 | **8000 (= 24,000 chars)** | The 16,384 Ollama window was only ~59% provisioned at the old floor (9,596). `num_ctx` — not this budget — bounds KV memory, so a bigger prompt budget costs prefill time only (~24 s at the [oMLX-measured ~170 tok/s M5 Pro prefill class](https://omlx.ai/benchmarks/performance/m47p196t), retrieved 2026-08-28 — not this checkout), zero new memory ceiling. New floor: 8000 + 4096 + ~1500 = **13,596** ≤ 16,384. |
| `graph.py _DEFAULT_MAX_CONTEXT_TOKENS` | 4000 | **8000** | Pinned equal to the config default by `tests/test_graph.py` (absent-key fallback must match documented default). |
| `models.grok.timeout_sec` / `models.claude.timeout_sec` | 30 | **90** | 30 s required ~68 tok/s sustained to finish a full `max_tokens` (2048) answer — legitimate long answers timed out. 90 s covers ~23 tok/s + TTFT; worst case with the 2-retry policy is ≈275 s, well inside the 780 s graph budget. Anthropic's own SDK default is 600 s. |
| `telegram.query.timeout_sec` / `opentweet.query.timeout_sec` (+ their `config.py` defaults) | 780 | **790** | Was exactly equal to `api.graph_timeout_sec`, so the client abort raced the server's 504. The client must lose that race to surface the diagnosable `GRAPH_TIMEOUT` (the pattern `static/terminal.js` already implements as `graph_timeout_sec + 10`). |
| `telegram/client.py` `/query` POST timeout shape | flat 780 | **`httpx.Timeout(790, connect=10)`** | A stalled TCP connect no longer burns the whole graph-length read budget (matches `opentweet/client.py` and `llm/client.py`). |
| `static/terminal.js` `callOps` | flat 60,000 ms | **130,000 ms default; `/ops/sync action=sync` synced from `/health`'s new `ops_sync_timeout_sec` (+60 s), 7,320,000 ms fallback** | The server holds `/ops/{agentic,fsconnect,sqlconnect}` subprocesses up to 120 s (`utils/ops_runner.py _TIMEOUT_SEC`) and the full sync up to 7,260 s (`_sync_timeout_sec()` with `post_sync_check`). The 60 s abort threw away the exit-code envelope while the CLI kept running. |
| `static/harness.html` `api()` | flat 15,000 ms on every non-chat route | **per-call `timeoutMs` (default 15,000)**: `/api/agent/run` → 3,630,000 ms; agent status/decision/push/discard/publish + `/api/github/status` → 130,000 ms | `POST /api/agent/run` blocks server-side up to the 3,600 s `ops_runner` cap; the CLI-backed routes up to 120 s. The browser aborted at 15 s (the UI even promised "up to 15 minutes"), leaving the subprocess burning with no listener. |
| Test pins moved with the values | — | — | `tests/test_reasoning_effort.py` (8000), `tests/test_graph.py` (8000 + comment), `tests/test_harness_console_contract.py` (new `api()` literal + the two new client deadline constants). |
| Docs re-derived | — | — | `config.yaml` comments (including the pre-headroom "8096" arithmetic slip), `macos/ollama-mlx.env` floor comment, `OLLAMA_SETUP.md` (formula + the stale "default is 4096" claim), `setup-guide.md`, `docs/m5-48gb-coding-expectations.md` knob table (+ reconciled its ~8k-chars agentic-prompt claim against OLLAMA_SETUP.md's ~39–40k worst case), `CLAUDE.md` load-bearing table, `.claude/skills/fable-protocol/SKILL.md` footgun floor, `.codex/skills/Cyclaw-Sandbox/NEW_SKILL.md` pins. |

Deliberately unchanged: `OLLAMA_CONTEXT_LENGTH=16384` (`macos/ollama-mlx.env`, test-pinned
— the whole point of the retune is to use the window already paid for),
`local_llm.max_tokens: 4096`, `local_llm.timeout_sec: 720`, `api.graph_timeout_sec: 780`,
`grok/claude.max_tokens: 2048` (raising cloud output caps is a spend decision — see
Findings), `soul_max_chars: 8000`, chunking 512/50, all rate limits.

## The context-window ledger (why 8000, not 9000)

All numbers below derive from code as shipped on this branch; the conversion
constant is `graph.py CHARS_PER_TOKEN = 3`.

- Total prompt-input budget: `max_context_tokens 8000 × CHARS_PER_TOKEN 3 = 24,000 chars`.
  **Amended 2026-08-28 after review:** the ratio was lowered from the conventional 4
  to a worst-case floor of 3. `indexing.chunk_size` counts *words*, so one chunk of
  symbol-dense corpus text (SHA-256 digests, CVE ids, base64, minified code) can be
  tens of thousands of characters *and* tokenize near 2 chars/token — at ratio 4 the
  derived 32,000-char budget could reach ~16k real prompt tokens and, with the 4,096
  reserved output, exceed the 16,384 window and stall the request. At 3 the budget
  holds inside the window even at 2 chars/token (24,000/2 + 4,096 = 16,096).
- Reserved out of that budget: soul preamble (≤ 8,000 chars + 7-char separator,
  `personality.soul_max_chars`) + query (≤ 4,000 chars, `policy.prompt_filter.max_input_chars`)
  + fixed framing (351 chars, `graph.py _LOCAL_FRAMING_CHARS`); retrieved context gets the
  remainder, floored at 800 chars (`_MIN_CONTEXT_CHARS`).
- Nominal worst case: 24,000 chars ≈ 8,000 tokens prompt + 4,096 reserved output
  = **12,096 of 16,384**, before headroom.
- Real-tokenizer range: the Qwen3 tokenizer is byte-level BPE running ≈3–4
  chars/token on English prose ([Qwen docs](https://qwen.readthedocs.io/en/v3.0/getting_started/concepts.html))
  and lower on symbol-dense text. Across that whole range the 24,000-char budget
  holds: at 4 chars/token it is 6,000 real tokens (10,096 with output), at 3.2 it
  is 7,500 (11,596), and at the pathological 2.0 it is 12,000 (16,096) — under the
  window in every case. The window utilisation the operator asked about therefore
  scales with content density rather than being fixed: ~10k of 16,384 on plain
  prose, ~14–16k on the dense corpus documents that motivated the raise, and never
  past it. 9000 was rejected for the same reason 8000-at-ratio-4 was: it puts the
  dense case over the window.
- The graph's post-assembly check (`graph.py` local_llm node) warns—without
  truncating—when the estimated prompt exceeds the budget; unchanged.

Memory cost of the raise: **zero new ceiling**. `num_ctx=16384` is what bounds KV
memory; whether the backend allocates that window up-front (llama.cpp) or grows a
prefix-trie cache lazily (the MLX runner) is backend-dependent and undocumented by
Ollama — either way this budget cannot push KV past the already-provisioned window.
Chunk physics cap the real block anyway: 5 chunks × `chunk_size: 512` (words) tops
out well under the 24,000-char budget in typical corpora; the pathological
single-chunk-of-digests case is what the ratio floor above exists for. Time cost: ~4,000 extra
prompt tokens ÷ ~170 tok/s measured-class M5 Pro prefill ≈ **+24 s** worst case,
inside the 720 s per-call budget with roughly 8× slack (see next section).

## Timeout ledger (every level, as now shipped)

```
Browser terminal.js /query ......... (graph_timeout_sec + 10)s = 790s, /health-synced
  └─ gate.py asyncio.wait_for ...... 780s  → 504 GRAPH_TIMEOUT      (api.graph_timeout_sec)
      └─ llm/client.py → Ollama .... 720s read / 10s connect        (local_llm.timeout_sec)
                                     retries: transport/5xx/429 only, 0.5s/1s backoff;
                                     read-timeout NOT retried (orphan-thread rationale,
                                     config.yaml models.local_llm comment)
telegram /query client ............. 790s (connect=10)   [was 780 flat — FIXED]
opentweet /query client ............ 790s (connect=10)   [was 780 — FIXED]
grok/claude fallback HTTP .......... 90s each, ≤3 attempts ≈ 275s   [was 30s — FIXED]
harness /api/chat → Ollama ......... 720s (models.local_llm.timeout_sec), no browser abort (Esc/stop only)
harness /api/agent/run ............. server min(formula, 3600)s; browser 3,630s [was 15s — FIXED]
harness CLI-backed agent routes .... server 120s; browser 130s      [was 15s — FIXED]
terminal /ops/{agentic,fs,sql} ..... server 120s; browser 130s      [was 60s — FIXED]
terminal /ops/sync (action=sync) ... server ≤7,260s; browser 7,320s [was 60s — FIXED]
health probes ...................... server-side 5s; terminal client 3s (deliberate, documented)
```

Boot-time guard: `utils/config_validation.py` enforces `graph_timeout ≥ llm_timeout + 30`;
shipped margin is 60 s. uvicorn's `timeout_keep_alive` (default 5 s) applies only to idle
keep-alive sockets *between* requests, not in-flight ones ([uvicorn server-behavior docs](https://www.uvicorn.org/server-behavior/)) —
no reverse proxy sits in front of the loopback bind, so no `proxy_read_timeout` applies.

## Is 720 s still right for the local decode? (measured-class evidence)

The `config.yaml` comment's third-party "~29–34 tok/s decode" exceeds the physical
bandwidth ceiling for dense decode on this SKU: 307 GB/s ÷ ~18 GB of 4-bit weights
≈ **17 tok/s** upper bound; an [oMLX benchmark of Qwen3.8-27B-MLX (4-bit) on M5 Pro
20c/48GB](https://omlx.ai/benchmarks/performance/m47p196t) reports **11.9 tok/s decode,
170.6 tok/s prefill** (29–34 is plausible only with multi-token-prediction /
speculative decode variants). Re-derived worst case at the *measured* class:

- Prefill ~14,100 tokens ÷ 170 tok/s ≈ 83 s
- Decode 4,096 tokens ÷ 11.9 tok/s ≈ 344 s
- Total ≈ **7.2 min against the 12-min budget** — 720 s remains correct with real
  headroom even at the slowest credible dense figure, and still covers the config
  comment's "high-single-digit tok/s" throttle case (4,096 ÷ 8 ≈ 512 s + prefill).

**Action for the operator (not automatable from a Linux container):** run
`python3 scripts/measure_local_llm_throughput.py` on the Mac and paste the decode
tok/s into the `config.yaml` comment, per the script's own closing instruction.
The 29–34 figure should be treated as unverified until then.

## Hardware/daemon level (48 GB M5 Pro)

- `macos/ollama-mlx.env` ships `OLLAMA_CONTEXT_LENGTH=16384`, `OLLAMA_KEEP_ALIVE=30m`,
  `OLLAMA_MAX_LOADED_MODELS=1`, `OLLAMA_NUM_PARALLEL=1`, `OLLAMA_FLASH_ATTENTION=1`,
  `OLLAMA_KV_CACHE_TYPE=q8_0` (all pinned by `tests/test_ollama_mlx_env.py`). No CyClaw
  client ever sends `num_ctx`/`options` — the env file is the only enforcement point.
- Current Ollama derives its *default* context from available VRAM
  ([docs.ollama.com/context-length](https://docs.ollama.com/context-length): 4k < 24 GiB,
  32k for 24–48 GiB, 256k ≥ 48 GiB), so the old doc claim "default is 4096" was stale in a
  direction that changed the failure mode: an unset var on a modern build over-provisions
  KV instead of stalling. The explicit 16384 stays correct for determinism either way
  (docs updated).
- KV-cache arithmetic: on a Qwen3-32B-shaped architecture (64 layers, 8 KV heads,
  head_dim 128 — [HF config](https://huggingface.co/Qwen/Qwen3-32B)), KV is 256 KiB/token
  at fp16 and ≈136 KiB/token at q8_0 → a 16k window costs ≈2.1 GiB (q8_0) to ≈4 GiB (fp16).
  `docs/m5-48gb-coding-expectations.md` states "~64 KiB/token / 16k ≈ 1 GB", which matches
  a q4 KV or a 4-KV-head architecture; `qwen3.8:27b`'s exact head count isn't publicly
  verifiable from here, so the doc's claim was left in place — **operator check:** `ollama ps`
  while loaded shows the real total; even the 4 GiB worst case fits trivially beside
  ~18 GB weights on 48 GB.
- Whether `OLLAMA_KV_CACHE_TYPE`/`OLLAMA_FLASH_ATTENTION` bind on the **MLX** backend
  (vs llama.cpp) is not documented by Ollama; the MLX runner uses its own prefix-trie
  cache. The env vars are harmless if ignored; the sizing above brackets both cases.
  (Speculating beyond that would violate the repo's own don't-invent rule.)

## Findings initially kept out of this change (1–4 landed same-day on this branch)

Findings 1–4 below were implemented later the same day as four focused commits on
this same branch after operator review of this report: the harness 422
`AGENTIC_BUDGET_EXCEEDED` guard (note: with the shipped 720s planner it also refuses
shapes above 3 iterations at the route — the uncapped CLI path is unchanged), the
gate-console 504 `OPS_TIMEOUT` mapping, the cloud-planner bounded retry (timeouts
deliberately not retried — one planner budget per iteration), and the
`numbat.max_bytes` single-generation rollover (ships 50 MiB). Findings 5–7 remain open.

1. **Harness run-request formula overflow** — `harness/schemas.py` accepts 10 iterations
   × 8 check profiles (formula ≈ 17,100 s) that `utils/ops_runner.py` silently truncates
   to its 3,600 s cap: the run is SIGKILLed mid-flight, leaking the clone and a
   permanently-`running` record (the module documents the kill path). A 422 at request
   time for combos that cannot finish is the clean fix.
2. **Cloud-planner single-shot** — `agentic/deepagent_github/chat_client.py` issues one
   `model.invoke()` with no retry; a transient 429/5xx fails the whole loop iteration
   (every other HTTP client in the repo carries `max_retries: 2`).
3. **`gate_ops.py` maps subprocess `TimeoutExpired` → 500 `OPS_ERROR`** while the harness
   maps the identical failure → 504 `AGENTIC_TIMEOUT`. Same failure, two codes.
4. **`utils/numbat_emitter.py` NDJSON stream has no rotation/size cap** (append-only,
   ships enabled).
5. **`cloud fallback output caps** — `grok/claude.max_tokens: 2048` can truncate answers
   the local path would give 4,096 for, and Sonnet-5-generation tokenizers emit more
   tokens for the same text; raising is a per-call spend increase, so it stays an
   explicit operator decision.
6. **Doc drift, cosmetic** — `config-guard` SKILL.md's rule table omits its own C13;
   `--strict` cannot pass on the deliberately-armed hybrid posture (C9) yet is offered
   as a merge gate. (A suspected "A–G vs A–F" smoke.sh drift did not survive a live
   run — the suite does emit section G.)
7. **`harness/ollama.py` 300 s fallback default** vs core 720 s applies only when
   `models.local_llm.timeout_sec` is absent from config (shipped config sets it, so the
   harness runs 720 s today); aligning the fallback constant is a one-liner when touched next.

## Validation evidence (this branch, Linux container, Tier-1 mock Ollama)

- `python3 .claude/skills/invariant-guard/check_invariants.py` — 47 passed, 0 failed.
- `config-guard` — 0 failures; the single warning is the known deliberately-armed hybrid
  posture (C9). C12 now advises `num_ctx ≥ 13,596` from the new values.
- `dep-guard` — clean. `doc-sync` — 0 drift items after the doc updates.
- `ruff check --select E,F,I,B,C4,UP,S .` (pinned 0.16.1) — clean.
- `mypy --strict` on the touched modules — no errors in touched files (pre-existing
  legacy-module errors documented in `CLAUDE.md` §4 unchanged).
- Full `pytest tests/` with the CI coverage flags: **TOTAL coverage 89%** (gate: 80).
  Exactly one failing test across the full suite:
  `tests/test_fsconnect_quota.py::test_quota_recompute_fail_closed_on_unreadable_root`,
  which fails **as root on unmodified main too** (an unreadable root is still readable
  by root; CI's non-root runner passes it). Not introduced by this change.
- `smoke.sh` (Ladder D, sections A–G against a live gate it managed itself):
  **all checks passed (10 passed, 3 skipped — Postgres, no DSN)**.
- `run_full_verification.py` (Ladder B, 11 phases, `CYCLAW_REPO` pointed at a scratch
  copy of this tree, realism Tier 2 — it detected and used the live mock daemon):
  venv run **222/225**; the 3 failures all carry the identical
  `cannot import name 'Settings' from 'chromadb.config'` error — the stub-collision
  artifact the skill itself documents for partially-real-deps venvs. A bare-interpreter
  cross-run failed only on genuinely absent third-party modules. Both runs: RAG
  pipeline (5 live queries), both triple gates, due-diligence invariants, REST surface,
  and both HTML contracts **PASS**; Security Invariants **24/24**.
- The one check green in neither mode (`anthropic_key_sanitized`) was proven directly
  against the real module, no stubs: with `GROK_API_KEY`/`ANTHROPIC_API_KEY` set to
  realistic values, `gate._sanitize_error` redacted both (`_SECRET_PATTERNS` also
  carries dedicated `sk-ant-` and `xai-` shapes).
- Console lifecycle emulations against the live stack (gate :8787 + harness :8790 +
  Tier-1 mock Ollama :11434): `terminal_emulation.py` **PASSED**, `harness_emulation.py`
  **PASSED** (all endpoint flows matched).
- Playwright browser lane (pre-installed Chromium, desktop 1440×1000 + mobile 390×844,
  pageerror/console/requestfailed listeners, full-page screenshots): both consoles
  rendered with **zero page errors**; the terminal completed a real `/query` round-trip
  through the actual DOM (type → send → rendered answer from the live graph);
  `page.evaluate` confirmed the live page carries `queryDeadlineMs = 790,000 =
  (graph_timeout_sec 780 + 10) × 1000` and the harness page carries
  `AGENT_RUN_TIMEOUT_MS = 3,630,000` / `AGENT_CLI_TIMEOUT_MS = 130,000` with the
  4-parameter `api()`. Console noise was exactly the documented-expected pair
  (auth-off 503 + favicon 404) plus two **pre-existing** findings already on file in
  `docs/audits/2026-08-27-auth-memory-console-harness-review.md`: F-01 (harness never
  serves `/static/auth_admin.js`) and F-02 (harness CSP) — both out of this change's scope.

## On-Mac runbook (the empirical half — cannot run from this container)

```bash
# 1. Throughput ground truth (paste decode tok/s into config.yaml's comment):
python3 scripts/measure_local_llm_throughput.py

# 2. Real KV + weights residency while loaded:
ollama ps          # SIZE column = weights + KV for the configured window

# 3. Confirm the daemon actually carries the env (an already-running app ignores it):
launchctl getenv OLLAMA_CONTEXT_LENGTH   # or: ps eww $(pgrep -f 'ollama serve') | tr ' ' '\n' | grep OLLAMA

# 4. End-to-end: a fat query through the console; watch for the absence of
#    "0% processing" and confirm /health's graph_timeout_sec drives the UI deadline.
```
