---
name: cyclaw-sandbox-test
description: Clone CyClaw origin/main into a clean local sandbox, emulate LM Studio with a bundled OpenAI-compatible mock, and smoke-test RAG query flows plus terminal.html API surfaces. Use when asked for Cyclaw-Sandbox-Test, CyClaw sandbox API smoke, mock LM Studio verification, terminal console endpoint coverage, or a fresh-main local audit before a PR.
---

# Cyclaw-Sandbox-Test

Use this skill for a fresh, local CyClaw runtime smoke. It is intentionally narrower than a full release audit: it proves the gateway starts against a mock LM Studio and exercises the browser/API surfaces without adding dependencies or mutating soul content.

## Workflow

1. Work from a fresh `origin/main` clone unless the user explicitly asks for the current checkout.
2. Run the bundled runner from the repo root:

```powershell
python .codex\skills\cyclaw-sandbox-test\scripts\run_sandbox_test.py --repo-url https://github.com/CGFixIT/CyClaw.git
```

For an already-prepared checkout, skip the heavy setup:

```powershell
python .codex\skills\cyclaw-sandbox-test\scripts\run_sandbox_test.py --in-place --skip-install --skip-index
```

3. Read the generated Markdown report path printed at the end. Treat any `FAIL` as a blocker before pushing runtime changes.

## What It Exercises

- Mock LM Studio: `GET /v1/models` and `POST /v1/chat/completions` on `127.0.0.1:1234`.
- Runtime prep: `data/personality`, `index`, and `logs` directories; `GROK_API_KEY=dummy`; `CYCLAW_API_KEY` set to a dummy local key.
- RAG/API smoke: `/health`, `/query` vault-hit, alternate RAG query, offline-declined query, broad miss-style query, and prompt-injection rejection.
- Terminal console surfaces: `/`, `/static/terminal.html`, `/soul`, `/soul/reload`, unauthenticated fail-closed checks for `/soul/propose`, `/soul/apply`, `/soul/restore`, `/audit/summary`, `/ops/sync`, `/ops/agentic`, `/ops/fsconnect`, and `/ops/sqlconnect`.

## Safety Rules

- Do not run authenticated `/soul/propose`, `/soul/apply`, or `/soul/restore` during smoke. The runner checks those mutation routes without auth and expects `401`.
- Do not bind outside `127.0.0.1`.
- If port `1234` already serves an OpenAI-compatible `/v1/models`, reuse it only if it returns the expected model id; otherwise stop and report the conflict.
- Use `--skip-install` only when dependencies are already installed. Use `--skip-index` only when the index already exists.

## Scripts

- `scripts/mock_lmstudio.py`: deterministic loopback LM Studio emulator.
- `scripts/run_sandbox_test.py`: clone/setup/start/smoke/report runner.
