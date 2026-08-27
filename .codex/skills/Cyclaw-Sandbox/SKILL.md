---
name: cyclaw-sandbox
description: Comprehensive, mock-safe CyClaw sandbox verification against current origin/main. Use when asked to verify or test CyClaw, its RAG graph, terminal or harness consoles, REST endpoints, audit logs, installers, platform vault behavior, CI, unit/integration coverage, or browser-rendered screenshots.
metadata:
  short-description: Full current-main CyClaw sandbox verification
  tailored-for: "codex"
---

# CyClaw Sandbox

This Codex skill mirrors the runnable resources in the Claude
`.claude/skills/Cyclaw-Sandbox/` bundle under `.codex/skills/Cyclaw-Sandbox/`.
The copied runners are kept in that directory: `run_full_verification.py`,
`gate_runtime_check.py`, `harness_runtime_check.py`,
`terminal_emulation.py`, `harness_emulation.py`, `test_terminal_consoles.py`,
`verify.sh`, `smoke.sh`, `windows-smoke.ps1`, `macos-smoke.sh`,
`mock_ollama.py`, and the specifications. Invoke this skill as
`/cyclaw-sandbox`; it is explicit-only and does not replace Claude's skill.

The goal is an evidence-backed sandbox report, not a claim that one host proves
all platforms. Pin every result to the tested `origin/main` SHA and label each
check `PASS`, `FAIL`, `SKIP`, or `NOT RUN` with the command and reason.

## Safety and authority

1. Fetch `origin/main` before inspection. Use a fresh detached clone or
   detached worktree; never run the full verifier against a feature branch.
2. Isolate `CYCLAW_HOME`, `XDG_CONFIG_HOME`, `HOME`-equivalent temp state,
   `index/`, `logs/`, and server ports. Preserve committed
   `data/personality/soul.md`; do not expose corpus contents or raw audit lines.
3. Set only non-secret dummy values for local checks (`GROK_API_KEY=dummy` and
   a generated ephemeral `CYCLAW_API_KEY`). Never use or print real Grok,
   Anthropic, GitHub, Telegram, Ollama, or Keychain credentials.
4. External provider checks must use mock HTTP or connection-shape tests. Do
   not spend real provider tokens and do not call `curl | sh` installers.
5. Never weaken a security invariant, enable agentic writes, bind off loopback,
   or rewrite a user's checkout to make a check pass.
6. A Windows host can run the Python and PowerShell lanes but cannot prove a
   native macOS install, APFS behavior, Keychain, launchd, or Apple Silicon
   Ollama. Use native macOS CI or a real macOS sandbox for those claims and
   report local simulation separately.

## Current-main inspection

Record the current SHA, Python version, package/runtime availability, and live
configuration before running tests. Verify rather than copy these values:

- `config.yaml`: `app.mode`, loopback `api.host`/`port`, local model and
  `fallback` model, provider enablement, retrieval `rrf_k` and `min_score`,
  auth/memory/agentic/guardrails/fsconnect/sqlconnect/sync/Telegram/OpenTweet
  switches, Numbat, and harness defaults.
- `graph.py`: count live `graph.add_node` calls (current main is expected to
  have 12, including `pre_action_hook_grok` and `pre_action_hook_claude`),
  confirm `retrieve` is entry, policy routers are conditional edges, and every
  path converges at `audit_logger`.
- `gate.py`, `gate_auth.py`, `gate_memory.py`, `gate_ops.py`,
  `harness/server.py`, `static/terminal.html`, and `static/harness.html`:
  derive the actual route table and browser fetch calls instead of assuming
  the copied checklist is current.
- `pyproject.toml`, `requirements*.txt`, `constraints.txt`, `environment.yml`,
  `Dockerfile`, and `.github/workflows/*.yml`: derive the install profiles,
  CI commands, supported Python range, and platform exceptions.

## Full verification ladder

Run the ladder in order. The bundled `run_full_verification.py` is the
in-process/mock driver; `verify.sh` is the CI-shaped lifecycle driver. They
complement the focused checks below and do not make browser, native-platform,
or live optional-service claims by themselves.

### 0. Fresh baseline and inventory

```powershell
git fetch origin main:refs/remotes/origin/main
git rev-parse origin/main
python --version
python -c "import sys; print(sys.version)"
```

Use `git worktree add --detach <temp-worktree> origin/main` or an equivalent
fresh clone, then run all commands from that checkout. Record the exact SHA,
not just the branch name.

### 1. Three installation profiles and parity

Verify the three documented installation methods in isolated temp homes. The
method names are deliberately distinct:

1. **Manual/core profile:** Python 3.12 venv, platform-correct Torch first,
   then the repository requirements/test profile with `constraints.txt`,
   followed by `python -m retrieval.indexer` and the two `python -m` servers.
2. **Installer-only profile:** `macos/install-cyclaw.sh` on native macOS or
   `powershell/Install-CyClaw.ps1` on Windows, using skip-dependency flags in a
   sandbox when needed. Verify home/venv/shim/profile layout and fail-closed
   path validation without deleting an existing user home.
3. **One-shot clone profile:** native macOS/Linux
   `macos/setup-from-clone.sh` (or `macos/setup-cyclaw.sh` entry point), with
   noninteractive skip flags for a test sandbox. Verify it chains the
   installer/key bootstrap/index/server lifecycle and does not inline secrets.

For every profile compare only non-secret facts: Python major/minor, package
pins, entry-point/module availability, config/template presence, expected
`~/.CyClaw`-style layout, loopback ports, index creation, and the same REST
smoke result. On Windows, validate the PowerShell 5.1 parser contract and
PowerShell smoke; on macOS, validate plain `torch==2.13.0` and removal of
Linux `+cpu`/PyTorch-index lines from temporary manifests. Do not call a
Windows or macOS profile PASS from a different operating system; mark the
native lane `SKIP` and cite CI/native evidence instead.

### 2. Static quality, unit, integration, and security gates

Use the commands actually present in current CI. At minimum, when dependencies
are installed:

```bash
python -m pytest tests/ -q --tb=short
python -m tests.ci_rag_smoke
ruff check --select E,F,I,B,C4,UP,S .
python .codex/skills/invariant-guard/check_invariants.py --repo-root .
```

Also run the relevant focused groups, not only the aggregate suite:

- `test_graph.py`, `test_graph_outcomes.py`, `test_hybrid_search.py`,
  `test_rag_integration.py`, `test_indexer.py`, and `test_llm_client_ollama.py`;
- `test_due_diligence_invariants.py`, `test_security.py`, `test_sanitizer.py`,
  `test_logger.py`, `test_metrics.py`, `test_numbat_emitter.py`,
  `test_numbat_audit_projection.py`, and `test_sequence_detect.py`;
- all `test_terminal*`, `test_harness*`, `test_gate*`, `test_gate_auth*`,
  `test_memory*`, `test_auth*`, `test_mcp*`, and `test_telemetry_kill.py`;
- installer and OS glue tests: `test_readme_install_contract.py`,
  `test_installer_python_contract.py`, `test_setup_from_clone.py`,
  `test_setup_cyclaw_keys.py`, `test_cyclaw_keychain_scripts.py`,
  `test_powershell_windows_parity.py`, `test_macos_scripts.py`,
  `test_macos_smoke.py`, `test_macos_fsconnect_setup.py`,
  `test_fsconnect_macos_policy.py`, `test_fsconnect_pathsafe_windows.py`,
  `test_generate_service_plist.py`, and `test_generate_service_task.py`;
- optional integration groups only when their isolated dependency/service exists:
  guardrails, Postgres/pgvector, Deep Agents, Telegram, OpenTweet, sync, and
  the opt-in groundedness evaluator.

Run workflow-specific checks from `.github/workflows/ci.yml` too: actionlint /
Zizmor, coverage, installer adversarial paths, mock-Ollama socket tests,
real-repo-run smoke, deepagents, Postgres, and the platform live-smoke jobs.
A local green result is not a substitute for GitHub Actions at the current
commit; record CI run/check URLs and terminal states when available.

### 3. RAG, local vault, and cross-platform scoring

Use a sanitized fixture corpus with identical files, chunking config, embedding
model/fingerprint, `top_k` values, and `rrf_k` on each platform. Verify:

- the indexer rejects corpus escapes and writes an atomic BM25 JSON index;
- semantic and BM25 legs return the expected fixture documents;
- RRF follows `1 / (rrf_k + rank)` and a document present in both legs ranks
  above a single-leg hit at the same effective position;
- `top_score` is compared to the live `retrieval.min_score` as an RRF score,
  never as cosine similarity;
- high-score vault hits use the local path and sources are chunks actually
  placed in the prompt; low-score misses pause at `user_gate`;
- a Windows and a macOS/Linux run produce the same deterministic rank/order
  for the same fixture and fingerprint. Differences caused by model download,
  filesystem permissions, Apple Silicon embedding behavior, or a changed
  fingerprint are failures to investigate, not silently normalized.

Use `test_hybrid_search.py`, `test_rag_integration.py`, `test_indexer.py`, and
`tests/ci_rag_smoke.py`. For filesystem-vault policy, add the platform-specific
`pathsafe` tests and real native macOS tests only on Darwin. Do not claim that
Linux simulation proves APFS metadata, `/Volumes` policy, Keychain, or native
Windows ACL behavior.

### 4. Five-query graph and provider gates

Run the bundled mock driver with CYCLAW_REPO and CYCLAW_SKIP_ENSURE=1 when using the prepared detached checkout, then inspect the resulting report. It must cover:

1. two high-score vault hits through `retrieve -> route_by_score ->
   guardrail_input -> local_llm -> guardrail_output -> audit_logger`;
2. a vault miss denied online through `offline_best_effort`;
3. Grok connection shape with `hybrid + enabled + key + fresh confirmation`;
4. Claude connection shape with the same gates and Anthropic headers.

Confirm unavailable providers and `confirmed is None` fail closed, the offline
branch is input-railed, external prompts never include the soul preamble,
and external `answer_sources` remains `[]`. Never use live keys.

### 5. Audit and observability integrity

Create an isolated `logs/audit.jsonl`, drive local, denied, blocked,
external-unavailable, and harness/operator actions, then verify:

- every graph route reaches `audit_logger` exactly once;
- records contain hashed queries and redacted fields, never plaintext queries,
  corpus passages, API keys, or raw authorization values;
- `metrics.py` summarizes the expected model/escalation counts and does not
  confuse `claude` with local output;
- the optional Numbat projection is fail-soft, uses the separate NDJSON stream,
  and preserves the authoritative audit log when projection fails;
- spend/sequence outputs, when enabled in the current tree, remain separate,
  privacy-safe, and joined only by the documented hash/ID contract.

Report the paths and aggregate counts only. Do not paste raw log records into
reports or screenshots.

### 6. Gateway and terminal REST surface

Start `mock_ollama.py`, the loopback gateway, and an isolated index/home. Run
`gate_runtime_check.py`, `terminal_emulation.py`,
`test_terminal_consoles.py`, and the platform smoke script available on the
host. Derive and probe every current route, including:

- `/`, `/health`, `/query`, `/audit/summary`;
- `/soul`, `/soul/propose`, `/soul/apply`, `/soul/reload`;
- `/ops/sync`, `/ops/agentic`, `/ops/fsconnect`, `/ops/sqlconnect`;
- auth and memory routes when their current config/test profile enables them.

Check status codes for missing keys, missing reasons, invalid origins, rate
limits, schema errors, disabled optional layers, and successful read-only
operations. Never execute a real agentic write, SQL mutation, filesystem
escape, sync upload, or external-provider request in the sandbox.

### 7. Harness REST surface and slash commands

Start `harness.server` on isolated loopback port `8790` with a temp
`CYCLAW_HOME`, then run `harness_runtime_check.py` and
`harness_emulation.py`. Verify all routes used by the current
`static/harness.html`, including status/registry, sessions, goals, soul/model
local toggles, chat fallback/rate limit, cancellation, tools/skills, web
allow/deny/fetch/search/inject/forget, memory-local notes, keys masking,
GitHub status, harness runs, and agent-run decision gates.

Probe every guard independently: missing API key, missing CSRF, cross-origin,
non-loopback bind, unknown IDs, and disabled features. Do not start a real
`/api/agent/run`; verify its auth gate and human decision transition only.
Confirm no secret values enter `.env` output, audit records, browser DOM, or
reports; only masked tails/key names may appear.

### 8. Browser rendering and screenshots

HTTP emulation and HTML string contracts are not full browser verification.
When Playwright or an equivalent browser is available, run it against the
isolated mock-backed gateway and harness:

1. Load `http://127.0.0.1:8787/` and `http://127.0.0.1:8790/` in Chromium;
   wait for network idle and the application readiness signal.
2. Capture desktop and narrow/mobile viewport screenshots of the terminal and
   harness, including each visible console/pane, status/error banners, and
   responsive overflow behavior. Save only to an ignored temp/report folder.
3. Exercise the browser's actual click/input/fetch flows for query, health
   refresh, soul/ops auth errors, harness sessions/goal/loop/cancel, and the
   current safe chat fallback. Assert no uncaught page errors, failed required
   requests, console errors, or unexpected navigation.
4. Inspect the rendered DOM for the current security contracts: no model or
   registry content is inserted through unsafe `innerHTML`, CSP/frame headers
   are present, API keys are masked, and screenshots contain no real secret,
   private corpus text, or raw audit record.
5. Test at least one desktop and one mobile viewport. If Playwright is not
   installed, run the HTML contract plus HTTP emulations and report browser
   rendering as `SKIP`, never as `PASS`.

A browser screenshot is evidence of presentation and browser wiring only; it
does not prove native macOS/Windows behavior, LLM quality, CI, or security
invariants by itself. Review each screenshot before publication and include
only sanitized images in a PR/report.

### 9. Platform live smoke and OS glue

On native Windows run `windows-smoke.ps1` with the mock provider and isolated
homes. On native macOS run `macos-smoke.sh` with the same contract. Both must
cover the gateway and harness live HTTP surfaces and be compared for endpoint
parity. On Linux, run static/platform simulation checks only and label the live
bombs `SKIP`.

Also verify platform installers, launchers, Keychain/CredMan wrappers,
launchd/Task Scheduler generators, scheduler backend selection, and refusal of
cross-platform invocation. Secrets must come from a protected store or
process-local environment and never appear in argv, shell history, plist/task
XML, screenshots, or logs.

### 10. Report and evidence ledger

Use this compact final record, expanding it with exact commands and failures:

```text
CyClaw sandbox
HEAD: <origin/main SHA>
Python: <version>
Install profiles: manual <P/F/S>, installer <P/F/S>, one-shot <P/F/S>
Graph nodes: <live add_node count>
Invariant guard: <passed>/<total>
Unit/integration suite: <passed/failed/skipped>
RAG 5-query: <P/F/S>
Vault RRF parity: <P/F/S; platform/evidence noted>
Triple-gate providers (mocked): <P/F/S>
Audit + metrics + Numbat/sequence: <P/F/S/SKIP by surface>
Gateway/terminal REST: <P/F/S>
Harness REST/slash: <P/F/S>
Browser rendering/screenshots: <P/F/S; tool + viewports>
Windows native lane: <P/F/S>
macOS native lane: <P/F/S>
CI current-head checks: <P/F/S; terminal state>
Known residuals: <reconfirmed list>
Recommendations: <none or evidence-backed items>
```

Keep an evidence ledger with command, exact result, environment, and privacy
classification. A missing optional service is `SKIP`; an assertion failure is
`FAIL`; an unavailable browser is not a rendering pass. Never publish raw
sandbox output, credentials, private corpus files, or unreviewed screenshots.
