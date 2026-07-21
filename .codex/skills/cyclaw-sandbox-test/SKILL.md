---
name: cyclaw-sandbox-test
description: Clone CyClaw origin/main into a clean local sandbox, emulate Ollama plus dummy-key Grok/Claude provider APIs, and smoke-test RAG query flows plus terminal.html API surfaces. Use when asked for Cyclaw-Sandbox-Test, CyClaw sandbox API smoke, mock Ollama verification, terminal console endpoint coverage, or a fresh-main local audit before a PR.
---

# Cyclaw-Sandbox-Test

Use this skill for a fresh, local CyClaw runtime smoke. It is intentionally narrower than a full release audit: it proves the gateway starts against a mock Ollama, exercises dummy-key Grok/Claude API checks against the same loopback mock, and verifies browser/API surfaces without mutating soul content.

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

To skip the targeted API/RAG tests during a fast local rerun:

```powershell
python .codex\skills\cyclaw-sandbox-test\scripts\run_sandbox_test.py --in-place --skip-install --skip-index --skip-tests
```

3. Read the generated Markdown report path printed at the end. Reports are
   written to a temporary directory outside the checkout. Treat any `FAIL` as
   a blocker before pushing runtime changes.

## What It Exercises

- Mock Ollama: `GET /v1/models` and `POST /v1/chat/completions` on `127.0.0.1:11434`.
- Mock external providers: dummy-key Grok and Claude clients pointed at the loopback mock; `/health` must report `ollama`, `grok_api`, `claude_api`, and `embeddings_local` healthy in hybrid mode.
- Runtime prep: `data/personality`, `index`, and `logs` directories; `GROK_API_KEY=dummy`; `ANTHROPIC_API_KEY=dummy`; `CYCLAW_API_KEY` set to a dummy local key.
- RAG/API smoke: `/health`, `/query` vault-hit, alternate RAG query, offline-declined query, broad miss-style query, and prompt-injection rejection.
- Terminal console surfaces: `/`, `/static/terminal.html`, `/soul`, `/soul/reload`, unauthenticated fail-closed checks for `/soul/propose`, `/soul/apply`, `/soul/restore`, `/audit/summary`, `/ops/sync`, `/ops/agentic`, `/ops/fsconnect`, and `/ops/sqlconnect`.
- Targeted tests: `tests.ci_rag_smoke`, `tests/test_client.py`, `tests/test_health.py`, `tests/test_graph.py`, `tests/test_rag_integration.py`, `tests/test_terminal_contract.py`, and `tests/test_cyclaw_sandbox_skill.py`.

## Safety Rules

- Do not run authenticated `/soul/propose`, `/soul/apply`, or `/soul/restore` during smoke. The runner checks those mutation routes without auth and expects `401`.
- Do not bind outside `127.0.0.1`.
- If port `11434` already serves an OpenAI-compatible `/v1/models`, reuse it only if it returns the expected model id; otherwise stop and report the conflict.
- The runner parses sandbox `config.yaml`, temporarily enables hybrid
  Grok/Claude against the loopback mock, then restores the exact original text
  before writing the report.
- Use `--skip-install` only when dependencies are already installed. Use `--skip-index` only when the index already exists.

## Scripts

- `scripts/mock_ollama.py`: deterministic loopback Ollama, Grok, and Claude emulator.
- `scripts/run_sandbox_test.py`: clone/setup/start/smoke/report runner.
