# CyClaw Sandbox Findings Report

Date: 2026-08-27
Repository: `CGFixIT/CyClaw`
Baseline: `origin/main` at `57e052ed9222d42a20b95ceb4dec71d21e169f57`
Host: Windows
Python: `3.12.0rc3` (`C:\py3dot12\python.exe`)

## Verdict

The Codex sandbox bundle is synchronized with the Claude `Cyclaw-Sandbox`
resource set and updated for the current source tree. The mock-safe full driver
passes `229/229` checks, the pristine real RAG smoke passes `4/4`, the live
terminal helper passes `41/41`, and the live harness/terminal emulations pass.

No CyClaw product runtime files were changed by this work. The changes are
limited to the Codex skill bundle, its inventory references, and verification
helpers/documentation.

## Findings Fixed

1. The copied verifier used removed/currently different APIs for stemming,
   Chroma mocks, graph execution, UTF-8 files, telemetry state, redaction
   configuration, and routes split across gateway modules. It now follows the
   current source contracts and uses a functional in-process graph/index mock.
2. Audit verification now writes a synthetic probe through the real hashing and
   redaction path, validates JSONL integrity and metrics counters, and checks
   the optional Numbat projection for privacy-safe output.
3. Terminal HTML inspection now includes the dynamically built provider/fetch
   wiring in `static/terminal.js`, not only the mostly structural HTML file.
4. The Codex helper output is Windows-console safe. Live console checks use
   case-insensitive HTTP header lookup, accept FastAPI's `422` schema boundary
   before runner-level `400`, and treat the disabled SQL connector's safe no-op
   as non-executing behavior.
5. Smoke reports default to an ignored temporary report directory instead of
   writing generated output into a tracked skill directory.

## Executed Evidence

| Surface | Result |
| --- | --- |
| Full mock driver: config, telemetry, corpus, five queries, provider gates, audit, Numbat, terminal, harness | PASS, `229/229` |
| Pristine real ChromaDB + BM25 + RRF smoke | PASS, `4/4`; hybrid scores exceeded the live `0.028` gate |
| Invariant guard | PASS, `35/35` |
| Ruff `E,F,I,B,C4,UP,S` | PASS |
| Focused graph/RRF/security/audit/gateway/harness/auth/MCP/telemetry groups | PASS, no failures in `52` modules; optional skips retained |
| Install and OS-glue contract groups | `76 passed, 82 skipped` |
| Independent gateway runtime check | PASS |
| Independent harness runtime check | PASS |
| Live terminal REST emulation | PASS |
| Live harness REST/slash emulation | PASS |
| Live terminal console helper | PASS, `41/41` |
| Windows installer-only profile with dependencies skipped and isolated `USERPROFILE` | PASS, exit `0`, expected layout present |
| macOS setup wrapper under Git Bash: syntax, dry-run, help, unknown option | PASS |
| GitHub Actions at pre-resync PR head `78d2d1e6` | PASS, all 14 workflow runs; main CI, Conda, Codex Skills, and security scans concluded successfully |
| Current-main rebase rerun at `57e052ed` | PASS, full mock-safe driver `229/229` after PRs #1122 and #1123 landed |

The full aggregate `pytest tests/ -q --tb=short` run exposed four failures in
the repository's macOS shell tests because they invoke the disabled WSL path
`C:\Windows\System32\bash.exe`, which returns `Access is denied` on this host.
The same scripts pass syntax, dry-run, help, and unknown-option checks using the
installed Git Bash path. This is host-tooling evidence, not a source assertion
failure.

## Deliberate Skips

- Browser rendering and screenshots: `SKIP`; Playwright and Chromium are not
  installed. The bundle includes `browser_render_check.py` with desktop/mobile
  viewports, page/console/request error capture, safe DOM checks, and explicit
  `SKIP` behavior when the dependency is unavailable.
- Native macOS install, APFS/Keychain/launchd behavior, and Apple Silicon
  Ollama: `SKIP` on Windows. Cross-platform static/simulated checks ran.
- Real Ollama daemon, real Grok/Claude calls, Numbat CLI, Postgres/pgvector,
  Telegram/OpenTweet live services, and opt-in groundedness evaluation: not
  run. Provider checks remained mock/connection-shape only.

## Privacy Boundary

The report contains aggregate results and sanitized sentinel descriptions only.
It does not contain raw audit lines, private corpus passages, API keys,
authorization values, or screenshots.
