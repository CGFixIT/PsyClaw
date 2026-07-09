# Enabling Grok and Claude Online Fallback Queries in CyClaw

**Status**: Verified against current main branch HEAD `ce1e07a1add713f881beb34eb132b4c4d82c0944` (Jul 9, 2026) and recent Claude parity work (#445–#449, #435 retrieval error surfacing, redaction standardization, shared fallback helper).

CyClaw supports **optional external LLM fallbacks** (Grok via xAI and Claude via Anthropic) **only in hybrid mode** when the RAG score is low (vault miss) **and** the user explicitly confirms. This is intentionally gated for safety, cost control, and to preserve the RAG-first + offline-first invariants.

Both providers are **disabled by default** (`enabled: false`) for security and to avoid accidental API spend.

---

## 1. Changes Required in `config.yaml`

Only two boolean flips are needed. No other structural changes to the file.

```yaml
models:
  grok:
    enabled: true          # <-- change from false
    base_url: "https://api.x.ai/v1"
    model: "grok-4.3"
    # ... rest of retry/timeout/max_tokens unchanged

  claude:
    enabled: true          # <-- change from false
    base_url: "https://api.anthropic.com/v1"
    model: "claude-sonnet-5"
    anthropic_version: "2023-06-01"
    # ... rest unchanged
```

**Full minimal diff** (recommended):

```diff
 models:
   grok:
-    enabled: false
+    enabled: true
     base_url: "https://api.x.ai/v1"
     model: "grok-4.3"
     ...
   claude:
-    enabled: false
+    enabled: true
     base_url: "https://api.anthropic.com/v1"
     model: "claude-sonnet-5"
     anthropic_version: "2023-06-01"
     ...
```

After editing, **restart the server** (`uvicorn gate:app` or via docker-compose). Config is loaded once at startup.

---

## 2. Environment Variables (Required)

**Never put keys in `config.yaml` or source code.**

Create or update your `.env` (already gitignored) or export before starting the server:

```bash
# Grok (xAI)
export GROK_API_KEY="xai-..."          # or put in .env

# Claude (Anthropic)
export ANTHROPIC_API_KEY="sk-ant-..."  # preferred name
# or
export CLAUDE_API_KEY="sk-ant-..."     # also accepted by client
```

- Keys are read **only** via `os.environ.get(...)` in `llm/client.py`.
- Empty / missing key → `is_available() == False` → provider is skipped gracefully.
- Both clients apply identical redaction logic for the respective key patterns in errors, logs, and audit entries (standardized in recent Jul 9 commits).

---

## 3. No Core Code Changes Required (Verification Summary)

After thorough static analysis of `gate.py`, `llm/client.py`, `graph.py` (via raw pulls), `config.yaml`, and recent commit history:

- **Wiring is already complete and correct**.
- `gate.py`:
  - Loads `config.yaml` once.
  - Instantiates `GrokClient` / `ClaudeClient` **only** when `mode == "hybrid"` **and** the respective `enabled: true`.
  - Respects `is_available()` before offering the provider in confirm options.
  - All paths converge on audit logging + PII/key redaction.
- `llm/client.py`:
  - `ClaudeClient` and `GrokClient` are symmetric (shared retry/timeout/_post_with_retry logic + recent shared fallback-node helper).
  - Missing key raises typed `ClaudeServiceError` / `GrokServiceError` with `details={"required_env": "..."}` — never crashes the request path.
  - Recent commits added full redaction parity and test coverage for Claude.
- `graph.py` + routing: Triple-gate (hybrid + enabled + `user_confirmed_online`) + three-mode distinction (`answer_model`) already implemented. Low RAG score → confirm gate → online option only if provider is available and enabled.
- July 8 `#435` fix ensures retrieval failures are **not masked** as vault misses (explicit error surfacing in `confirm_message` and `QueryResponse.error`).

**Result**: Enabling the two flags + setting the two env vars is sufficient. The system degrades safely to `offline_best_effort` when keys are absent or providers disabled.

---

## 4. How Online Fallbacks Are Triggered (After Enabling)

1. Query runs in **hybrid** mode.
2. RAG retrieval (ChromaDB 1.5.9 + BM25 + RRF, `min_score=0.028`).
3. If `top_score < min_score` (genuine vault miss) **or** explicit offline trigger:
   - User sees confirmation prompt (web UI or API `needs_confirm`).
   - If user confirms (`user_confirmed_online: true`):
     - Available enabled providers (Grok and/or Claude) are offered.
     - Selected provider → `generate()` call with full redaction + retry.
4. Response includes `answer_model: "grok"` or `"claude"` + audit event.
5. If key missing or provider disabled → silently falls back to `offline_best_effort` (no user-facing error unless debugging).

This preserves **RAG-first**, **triple-gate external**, and **audit convergence** invariants.

---

## 5. Verification Steps (After Changes)

```bash
# 1. Validate config
python -c "
import yaml
cfg = yaml.safe_load(open('config.yaml'))
print('grok.enabled:', cfg['models']['grok']['enabled'])
print('claude.enabled:', cfg['models']['claude']['enabled'])
"

# 2. Check keys are loaded (do not print the actual key)
python -c "
import os
print('GROK_API_KEY set:', bool(os.getenv('GROK_API_KEY')))
print('ANTHROPIC_API_KEY set:', bool(os.getenv('ANTHROPIC_API_KEY')))
"

# 3. Start server (with dummy keys for smoke testing if desired)
GROK_API_KEY=dummy GROK_API_KEY=dummy ANTHROPIC_API_KEY=dummy \
uvicorn gate:app --host 127.0.0.1 --port 9876

# 4. Health check
curl http://127.0.0.1:9876/health | jq

# 5. Run a low-score / vault-miss query (or use the browser console)
# Expect needs_confirm + grok/claude options when both enabled + keys present
```

Also run the project's smoke harness or relevant pytest tests (`-k "claude or grok or fallback or retrieval"`).

---

## 6. Future Enhancement Note: Web Interface Toggle

**Current state (Jul 2026)**: Requires manual edit of `config.yaml` + server restart. This is deliberate for auditability and to keep the single source of truth in version-controlled config.

**Recommended future change**:
- Expose `grok.enabled` and `claude.enabled` (plus perhaps per-query model preference) as **runtime toggles in the browser console** (`static/terminal.html` — already has FS/SQL panels and ops surfaces).
- Add a small settings section or modal in the terminal UI.
- On toggle change:
  - Update the in-memory config (or a lightweight user/session prefs layer).
  - Log the change to `audit.jsonl` with `human_reason` (for soul-like governance on settings).
  - Optionally persist to a small `user_prefs.json` or the soul metadata (with atomic write + drift detection).
- The underlying triple-gate logic, `is_available()` checks, and `enabled` flags in the loaded config **must still be honored** — the UI toggle is only a convenience layer on top of the existing secure defaults.
- This keeps the YAML as the authoritative default while giving power users / operators a safe, auditable way to flip online fallbacks without editing files or restarting.

This would be a natural extension of the existing `/ops/*` + `utils.ops_runner` pattern and the PR#239 console work.

---

**Summary**: Two boolean changes in `config.yaml` + two environment variables = full Grok + Claude online fallback capability. Everything else is already wired correctly and hardened (including graceful missing-key handling and retrieval error distinction). The web UI toggle is noted as the logical next UX improvement while preserving all invariants.

File generated for easy inclusion in `docs/` or root of the CyClaw repo.