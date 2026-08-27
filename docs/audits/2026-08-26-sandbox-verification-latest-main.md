# CyClaw Sandbox Verification Findings

Date: 2026-08-26
Baseline: `origin/main` at `47106f8a3c645990a99154e3cd03d61562577b67`
Version: `1.9.0`
Branch under test: `main`, before this report-only branch

## Verdict

The latest-main sandbox verification is **PARTIAL**, not a clean pass.

The in-process swarm verifier completed with `157/189` checks passed. Its
largest failures are verifier drift against the current repository: it imports
a removed `PorterStemmer` symbol, reads the wrong config path for secret
redaction, and checks obsolete inline terminal HTML strings even though the
console contract now spans `static/terminal.html` and `static/terminal.js`.

Independent runtime checks, the repository's focused contract tests, the clean
real RAG smoke, and the harness live emulator passed. The full test suite had
four environment-specific macOS-shell failures because this Windows host
resolves `bash` to disabled WSL at `C:\Windows\System32\bash.exe`.

## Test Environment

- Interpreter: `C:\py3dot12\python.exe`, Python `3.12.0rc3`.
- Ollama realism: tier 1, bundled deterministic mock over loopback HTTP.
- Real Ollama daemon: not used.
- Real Grok and Claude API calls: not used; the verifier's online checks are
  mocked connection-shape checks.
- An ephemeral 32-byte URL-safe `CYCLAW_API_KEY` was generated for the live
  gateway and harness probes. Its value was not printed or committed.
- The test process emitted non-fatal Hugging Face cache permission warnings;
  no model download or external API call was required.

## Evidence Summary

| Check | Result | Evidence |
| --- | --- | --- |
| In-process swarm verifier | PARTIAL, `157/189` | `run_full_verification.py` |
| Gate runtime check | PASS | 37 routes, 18 telemetry-kill variables, callable app |
| Harness runtime check | PASS | 43 routes, loopback guard, auto-docs disabled |
| Focused direct tests | PASS | terminal contract, due diligence, telemetry, harness contract |
| Real offline RAG smoke | PASS | 4/4 clean-corpus vault queries above the `0.028` gate |
| Live terminal emulator | PARTIAL | first cold vault query timed out at 10 seconds; warm rerun passed |
| Live harness emulator | PASS | all exercised status, registry, session, chat, goal, loop, cancel, and auth-gate flows |
| Full `tests/` suite | 4 environment failures | all four failures are Bash resolution/permission failures |
| Real Ollama/Grok/Claude | NOT RUN | intentionally outside this offline sandbox |

## Findings

### F-01: In-process verifier cannot build its mock index

Severity: High for verification coverage; not reproduced as a product defect.

The verifier reports:

```text
cannot import name 'PorterStemmer' from 'retrieval.stemmer'
cannot access local variable 'chunks' where it is not associated with a value
```

The first error prevents the BM25 fixture from being created. The second is a
dependent Chroma fixture failure. The later five-query and triple-gate phases
then fail because `index/bm25.json` does not exist. The clean real RAG smoke
passed after the verifier's temporary corpus files were removed, so these
failures currently block verifier coverage rather than proving a retrieval
regression.

Recommended follow-up: update the verifier fixture to the current
`retrieval.stemmer` and indexer contract, and make dependent phases report
`blocked by setup` instead of converting setup failure into unrelated query and
triple-gate failures.

### F-02: Verifier checks the wrong secret-redaction config path

Severity: Medium for verification accuracy; current direct redaction tests pass.

`config.yaml` stores `redact_secrets_like` under `policy.privacy`, while the
verifier reads `logging.audit.redact_secrets_like`. This produces the false
`sk_ant_in_config_redact` failure. The direct terminal, due-diligence,
telemetry, and harness contract tests passed, and the verifier itself passed
the Anthropic environment redaction, pattern, and sanitizer checks.

Recommended follow-up: read the canonical `policy.privacy` path and add a
fixture-level assertion that the verifier and product use the same config
shape.

### F-03: Verifier terminal HTML checks are stale

Severity: Medium for verification accuracy; current direct terminal contract
tests pass.

The verifier searches only `static/terminal.html` for old literal strings such
as `Send to Grok`, `handleConfirm(true, id, 'grok')`, and `authHeaders()`. The
current console keeps behavior in `static/terminal.js`, generates provider
buttons from the server-declared availability list, and uses updated auth
helpers. The direct terminal contract tests passed, including endpoint
extraction, explicit provider selection, health handling, auth boundaries, and
HTML/JavaScript wiring.

Recommended follow-up: have the verifier use the same combined console source
as `tests/test_terminal_contract.py` and assert the current dynamic-provider
contract instead of historical literal call sites.

### F-04: Cold gateway query exceeds the emulator's 10-second timeout

Severity: Medium, runtime performance observation.

With a fresh loopback gateway, real index, bundled mock Ollama, and generated
API key, the first terminal emulator vault query timed out at the emulator's
fixed 10-second `httpx` timeout. The same server then passed the complete
terminal emulator on the immediate warm rerun. Health, local/declined query
flows, unauthenticated soul rejection, authenticated soul retrieval, and the
harness emulator all passed.

This does not establish a production latency breach because the test uses a
mock LLM and the emulator timeout is not the gateway's configured graph
deadline. It does establish a repeatable cold-start sensitivity in the
operator-facing smoke path.

Recommended follow-up: measure the first-request stages separately and either
warm the retrieval/model path during readiness or make the emulator timeout
and diagnostic distinguish cold startup from a stuck request.

### F-05: Windows test discovery selects an unusable Bash executable

Severity: Medium for Windows CI/local verification.

The full suite reported four failures with `Access is denied` from
`C:\Windows\System32\bash.exe`:

- `tests/test_macos_scripts.py::test_setup_cyclaw_syntax_is_valid`
- `tests/test_macos_scripts.py::test_setup_cyclaw_dry_run_takes_no_action`
- `tests/test_macos_scripts.py::test_setup_cyclaw_help_and_unknown_option`
- `tests/test_macos_smoke.py::test_macos_smoke_bash_syntax`

The explicit Git Bash executable at
`C:\Program Files\Git\bin\bash.exe` passed syntax checks for
`macos/setup-cyclaw.sh` and `macos-smoke.sh`. A native macOS runtime was not
available, so behavioral Darwin execution remains unverified on this host.

Recommended follow-up: make Windows test discovery reject the disabled WSL
stub with a clear skip/error, or use an explicit Git Bash path when available.

### F-06: Telemetry phase result conflicts with independent runtime evidence

Severity: Medium for verification accuracy.

The in-process verifier reports `Telemetry Kill: 0/10`, while the independent
gate and harness runtime checks report all 18 required telemetry-kill variables
active. The verifier output itself later prints the expected values, including
`LANGCHAIN_TRACING_V2=false`, `LANGGRAPH_CLI_NO_ANALYTICS=1`,
`ANONYMIZED_TELEMETRY=False`, and the OpenTelemetry exporters set to `none`.

Recommended follow-up: align the verifier's phase ordering and import-time
assertions with the independent runtime check, then retain one canonical
telemetry evidence function to avoid contradictory results.

## Passed Security and Governance Evidence

- Config invariants: `24/24` in the in-process verifier.
- Metrics and module-isolation checks: `11/11` in the in-process verifier.
- Focused due-diligence tests: passed.
- Focused telemetry-kill tests: passed.
- Focused terminal contract tests: passed.
- Focused harness console contract tests: passed.
- Harness HTML contract: `36/36` in the in-process verifier.
- Harness REST console: `41/41` in the in-process verifier.
- Loopback binding and expected API route registration: passed in independent
  runtime checks.
- No core source, config, workflow, dependency, or security behavior was
  changed by this report-only PR.

## Commands Run

```text
py -3.12 .claude/skills/CyClaw-Sandbox/run_full_verification.py
py -3.12 .claude/skills/CyClaw-Sandbox/gate_runtime_check.py
py -3.12 .claude/skills/CyClaw-Sandbox/harness_runtime_check.py
py -3.12 -m pytest tests/test_terminal_contract.py tests/test_due_diligence_invariants.py tests/test_telemetry_kill.py tests/test_harness_console_contract.py -q --tb=short
py -3.12 -m pytest tests/ -q --tb=short
py -3.12 -m tests.ci_rag_smoke
py -3.12 .claude/skills/CyClaw-Sandbox/terminal_emulation.py http://127.0.0.1:8787
py -3.12 .claude/skills/CyClaw-Sandbox/harness_emulation.py http://127.0.0.1:8790
```

The live emulators were run against loopback-only processes and were stopped
after the probes. No real provider credentials were used.

## Invariant Impact

This PR changes no product behavior and does not alter the six CyClaw security
invariants or I6 module isolation. It records verification evidence and
follow-up work for the verifier and host tooling only.
