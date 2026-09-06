---
name: cyclaw-sandbox-test
description: Clone CyClaw origin/main into a clean local sandbox, emulate Ollama plus dummy-key Grok/Claude provider APIs, and smoke-test RAG query flows plus terminal.html API surfaces. Use when asked for Cyclaw-Sandbox-Test, CyClaw sandbox API smoke, mock Ollama verification, terminal console endpoint coverage, or a fresh-main local audit before a PR.
---

# Cyclaw-Sandbox-Test

Use this skill for a fresh, local CyClaw runtime smoke. It proves the gateway
starts against a mock Ollama, exercises dummy-key Grok/Claude API checks against
the same loopback mock, verifies browser/API surfaces, and runs independent
gate and harness runtime contracts without mutating the operator's checkout or
soul. It may scaffold a sandbox-only default soul when the isolated clone lacks
one.

## Workflow

1. Use fresh `origin/main` for a baseline audit. To verify a PR, use an isolated
   checkout of its exact head (`--branch` selects it), record its base, and do
   not replace the candidate with main. `--in-place` is intended for disposable
   prepared checkouts; the runner temporarily rewrites config and can build data.
2. Use `py -3.12` on Windows or `python3.12` on macOS/Linux for every command
   below. Run the static guards before spending time on a clean install:

```text
<python-3.12> .claude/skills/config-guard/check_config.py
<python-3.12> .claude/skills/invariant-guard/check_invariants.py
<python-3.12> .claude/skills/dep-guard/check_deps.py --strict
<python-3.12> .claude/skills/verify-deps/extract_pins.py --strict
<python-3.12> .claude/skills/verify-deps/check_env_drift.py --strict
```

Current `main` emits config guard C9 as a hybrid-posture warning.
Record it; do not silently suppress a real config failure or an unexpected new
warning. The other listed commands are strict gates.

3. Run the bundled runner from the repo root:

```text
# Windows
py -3.12 .codex\skills\cyclaw-sandbox-test\scripts\run_sandbox_test.py --repo-url https://github.com/CGFixIT/CyClaw.git

# macOS/Linux
python3.12 .codex/skills/cyclaw-sandbox-test/scripts/run_sandbox_test.py --repo-url https://github.com/CGFixIT/CyClaw.git
```

For an already-prepared checkout, skip the heavy setup:

```text
<python-3.12> .codex/skills/cyclaw-sandbox-test/scripts/run_sandbox_test.py --in-place --skip-install --skip-index
```

To skip the targeted API/RAG tests during a fast local rerun:

```text
<python-3.12> .codex/skills/cyclaw-sandbox-test/scripts/run_sandbox_test.py --in-place --skip-install --skip-index --skip-tests
```

4. Read the generated Markdown report path printed at the end. Reports are
   written to a temporary directory outside the checkout. Treat any `FAIL` as
   a blocker before pushing runtime changes.

## Platform Rules

- Windows and Linux install `torch==2.13.0+cpu` from the PyTorch CPU index.
- Native macOS support means Apple Silicon on macOS 14+ with Python 3.12+.
  macOS installs plain `torch==2.13.0`, then uses temporary filtered copies of
  the legacy manifests. It never changes tracked files or tries the unavailable
  `+cpu` wheel. Run this path on a physical Apple-Silicon Mac when validating a
  macOS release; hosted `macos-latest` is useful CI evidence, not that proof.
- For a safe macOS installer-layout smoke, isolate the home directory before
  invoking the installer:

```bash
SANDBOX_HOME="$(mktemp -d)"
HOME="$SANDBOX_HOME" bash macos/install-cyclaw.sh --repo-path "$PWD" --skip-python-deps < /dev/null
test -x "$SANDBOX_HOME/.CyClaw/bin/cyclaw"
HOME="$SANDBOX_HOME" bash macos/uninstall-cyclaw.sh < /dev/null
```

## Escalation Coverage

The bundled runner is the required fresh-clone gate. For changes to the
harness, terminal UI, agentic controls, or platform installers, also run the
relevant established verifier after dependencies are installed:

```bash
CYCLAW_HOME="$(mktemp -d)" CYCLAW_API_KEY=sandbox-key \
  python .claude/skills/CyClaw-Sandbox/run_full_verification.py
```

On Linux, `bash .claude/skills/CyClaw-Sandbox/verify.sh` additionally exercises
the live gateway and harness servers. Do not substitute it for the macOS path:
its dependency bootstrap is Linux-oriented.

## What It Exercises

- Mock Ollama: `GET /v1/models` and `POST /v1/chat/completions` on `127.0.0.1:11434`.
- Mock external providers: dummy-key Grok and Claude clients pointed at the loopback mock; `/health` must report `ollama`, `grok_api`, `claude_api`, and `embeddings_local` healthy in hybrid mode.
- Runtime prep: `data/personality`, `index`, and `logs` directories; `GROK_API_KEY=dummy`; `ANTHROPIC_API_KEY=dummy`; `CYCLAW_API_KEY` set to a dummy local key.
- RAG/API smoke: `/health`, `/query` vault-hit, alternate RAG query, offline-declined query, broad miss-style query, and prompt-injection rejection.
- Terminal console surfaces: `/`, `/static/terminal.html`, `/soul`, `/soul/reload`, unauthenticated fail-closed checks for `/soul/propose`, `/soul/apply`, `/soul/restore`, `/audit/summary`, `/ops/sync`, `/ops/agentic`, `/ops/fsconnect`, and `/ops/sqlconnect`.
- Runtime contracts: independent `gate.py` and `harness/server.py` FastAPI,
  telemetry-kill, endpoint-registration, and loopback checks. The harness
  contract receives a temporary `CYCLAW_HOME`, never the operator's real state.
- Targeted tests: `tests.ci_rag_smoke`, `tests/test_client.py`, `tests/test_health.py`, `tests/test_graph.py`, `tests/test_rag_integration.py`, `tests/test_terminal_contract.py`, and `tests/test_cyclaw_sandbox_skill.py`.

## Safety Rules

- Do not run authenticated `/soul/propose`, `/soul/apply`, or `/soul/restore` during smoke. The runner checks those mutation routes without auth and expects `401`.
- Do not bind outside `127.0.0.1`.
- If port `11434` already serves an OpenAI-compatible `/v1/models`, reuse it only if it returns the expected model id; otherwise stop and report the conflict.
- The runner parses sandbox `config.yaml`, temporarily enables hybrid
  Grok/Claude against the loopback mock, then restores the exact original text
  before writing the report.
- Use `--skip-install` only when dependencies are already installed. Use `--skip-index` only when the index already exists.

## Evidence limits

Provider client checks against a loopback mock do not prove production
`assert_online_destination` allowlists or the whole graph consent path. Pair
those connection checks with `tests/test_endpoint_trust.py` and graph tests.
Test both local nodes for malformed/untrusted URLs and explicit trusted host
access. Report mock fidelity, platform skips, and the actual tested SHA.

## Scripts

- `scripts/mock_ollama.py`: deterministic loopback Ollama, Grok, and Claude emulator.
- `scripts/run_sandbox_test.py`: clone/setup/start/smoke/report runner.
