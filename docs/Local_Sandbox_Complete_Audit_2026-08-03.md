---
title: "CyClaw Local Sandbox Verification Report"
date: 2026-08-03
sandbox_commit: 544549ffc751bf29c5283d6e908b220b53c1f122
python_version: 3.12.13
---

# CyClaw Local Sandbox Verification Report - 2026-08-03

## Executive Summary

**Verdict: functional/runtime PASS; Windows telemetry-kill coverage is
incomplete.** A fresh isolated clone was fast-forwarded twice while `main` moved
and finished at exact commit `544549ffc751bf29c5283d6e908b220b53c1f122`. On stable
CPython 3.12.13, `.codex/skills/cyclaw-sandbox-test/scripts/run_sandbox_test.py`
passed **30/30** checks with a real local embedding model, real ChromaDB + BM25
index, live FastAPI gateway, and loopback model mocks. An independent live
harness-console emulation also passed every exercised API flow.

The exact-commit GitHub main CI matrix passed on real Ubuntu, macOS, and Windows
runners. The parallel Conda workflow initially failed in its real-index sandbox
stage, then passed every step on attempt 2 at the same SHA. The local all-tests run
still exposed six environment/profile-sensitive failures: five Windows
symlink-privilege failures and one test that assumes the optional PostgreSQL driver
is absent even under the requested `full` install.

No tracked application file, `config.yaml`, or `data/personality/soul.md` was
changed by verification. This report is the only intended tracked change.

This is a sandbox verification report, not a penetration test or complete release
audit. Live NeMo, real generation models, outbound providers, local PostgreSQL,
rclone, browser execution, and several timeout/auth edge cases remain outside the
local run and are named below.

## Scope and Method

- Fresh isolated clone of `origin/main`; the pre-existing working checkout was not
  reset, cleaned, or modified.
- Stable CPython **3.12.13** provisioned locally with uv.
- CPU-only PyTorch **2.13.0+cpu**, constrained requirements, editable `.[full]`,
  and `pip check`.
- Real sentence-transformer embeddings, ChromaDB, BM25, index construction, and
  retrieval.
- Loopback mock model APIs for Ollama/Qwen, Grok, and Claude; no real cloud key,
  billable request, or generation-model weight was used.
- Live gateway and harness servers on loopback only.
- Full pytest invocation with six failures classified below, subsystem self-tests,
  invariant guard, Ruff, runtime import checks, audit metrics, and current GitHub
  Actions results.
- Linux/macOS approximation was not overstated: WSL2 had no installed distro and
  Docker/Podman were unavailable. Git Bash supplied a POSIX shell but still ran on
  the Windows kernel. Real Ubuntu and macOS evidence therefore comes from hosted
  CI on the exact commit.

## Environment

| Item | Result |
|---|---|
| Git target | `origin/main` at `544549ffc751bf29c5283d6e908b220b53c1f122` |
| Python | CPython 3.12.13 |
| Torch | 2.13.0+cpu; CUDA unavailable |
| Dependency integrity | `pip check`: no broken requirements |
| Install profile | requirements + constraints, then editable `.[full]` |
| Guardrails extra | Not installed; `full` deliberately excludes NeMo/fastembed |
| Host | Windows; Git Bash 5.3.9 available |
| WSL | Runtime present, no Linux distribution installed |
| Container runtime | Docker and Podman unavailable |
| Local model backend | Loopback mock; no real Ollama/LM Studio model |

The legacy user-invoked `.agents` skill still describes LM Studio on port 1234,
Qwen-7B, and an older torch pin. The current repo-native skill and code use an
Ollama-compatible endpoint on port 11434, `qwen3.6:27b`, and torch 2.13.0+cpu.
Current repository contracts were used where the two disagreed.

The hosted macOS lane correctly uses plain `torch==2.13.0`; the `+cpu` wheel is
the Linux/Windows install path and is not available for macOS.

## Verification Results

### 1. Fresh clone and resync

**PASS.** The isolated clone began on `b623c8f`, detected that upstream moved,
inspected the delta, and fast-forwarded to `544549f`. The delta was limited to
macOS install/uninstall scripts, CI, and associated documentation. Both changed
shell scripts passed `bash -n`. A final GitHub API check confirmed that
`544549f` was still `origin/main` before this report branch was created.

### 2. Dependency and import verification

**PASS.** The clean Python 3.12.13 environment installed the documented CPU torch
first, then constrained requirements and `.[full]`. The following imported
successfully: FastAPI, LangGraph, ChromaDB, sentence-transformers, rank-bm25,
Deep Agents, psycopg, pgvector, and torch. `pip check` found no broken dependency.

### 3. Live RAG and gateway sandbox

**PASS - 30 PASS / 0 FAIL / 0 WARN.** The current targeted runtime/API runner,
`.codex/skills/cyclaw-sandbox-test/scripts/run_sandbox_test.py`, executed on the
exact final main commit and verified:

- a real 70-chunk ChromaDB + BM25 index build;
- loopback model discovery for `qwen3.6:27b`, `grok-4.5`, and
  `claude-sonnet-5`;
- dummy-key Grok and Claude direct-client generation against loopback only;
- live Uvicorn gateway readiness;
- `/health` status `ok`, graph/index ready, hybrid mode, and all four provider
  health contracts;
- `/`, `/static/terminal.html`, vault-hit query, RRF query, declined-online
  fallback, and miss-style query;
- prompt injection refusal with HTTP 400;
- soul read/reload auth behavior and unauthenticated mutation refusal;
- authenticated audit summary;
- subprocess-isolated `/ops/sync`, `/ops/agentic`, `/ops/fsconnect`, and
  `/ops/sqlconnect` status routes;
- `tests.ci_rag_smoke`, the current targeted API/RAG pytest selection, and
  `metrics.py`.

Model discovery and direct Grok/Claude client generation succeeded against
deterministic loopback mocks. Gateway queries exercised local/offline graph paths;
no Grok or Claude request traversed the graph's human-confirmation gate. The four
`/health` entries prove service discovery/availability, not generation through each
graph route. Grok/Claude sentinel replies were asserted by the direct-client probes;
the gateway query checks did not independently assert local-answer provenance.
Retrieval, embedding, indexing, HTTP routing, auth, auditing, and response contracts
were real.

### 4. Harness console

**PASS for the implemented subset.** `harness_runtime_check.py` passed
independently. A second live server run, backed by the loopback Ollama mock, passed
the currently implemented `harness_emulation.py` flow:

- status, registry, and session listing;
- authenticated session create/read/rename;
- unknown-session 404;
- harness-local soul toggle and restoration;
- model selection;
- chat HTTP 200 with the expected response fields; reply content/provenance and
  persisted message history were not asserted;
- read-only GitHub status;
- harness run listing and named agent check profiles;
- bad-key rejection on agent run and decision routes.

Not exercised: a real agentic run; run-status/approve/push/publish/discard flows;
missing/wrong-CSRF behavior; hostile Origin, `Sec-Fetch-Site`, or Host; chat-message
persistence after reload; or browser JavaScript. Both child processes were
terminated and reaped. Graceful lifespan shutdown and post-termination port release
were not asserted.

### 5. Static invariant checker

**PASS.** Independent gateway and harness import/runtime checks passed. The
current invariant checker reported **33 passed, 0 failed**, covering:

- I1 RAG-first entry and exclusive retrieve edge;
- I2 exact topology-as-policy nodes, edges, and router targets;
- I3 triple-gated Grok and Claude construction/routing;
- I4 structural audit convergence for all nine upstream nodes;
- I5 empty-reason rejection and atomic soul-write shape;
- I6 direct-import/source-dependency isolation for optional layers (enabled
  guardrails can still load and execute in-process through
  `utils.guardrail_bridge`);
- telemetry-kill import ordering, auth fail-closed behavior, sanitizer contract,
  BM25 format, and MCP no-sampling.

These are structural and import-order assertions, not live proofs of durable audit
persistence, NeMo behavior, provider escalation, timeout cancellation, or telemetry
egress suppression.

Ruff passed with the repository's exact rule selection after extending the local
exclude list for the throwaway `.venv-sandbox` and uv runtime directories. Those
directories do not exist in a clean CI checkout.

### 6. Full pytest suite

**WARN - 2472 passed, 173 skipped, 6 failed in 201.82 seconds.** The failures
were:

1. Five tests in `tests/test_agentic_repo_workspace.py` failed while arranging
   symlinks, before the production assertion was reached. Windows returned
   `WinError 1314` (symlink privilege unavailable). Repeating the five tests
   outside the Codex filesystem sandbox produced the same host-policy error.
2. `tests/test_sqlconnect_client.py::test_driver_absent` expected
   `SqlDriverNotInstalledError`, but `.[full]` intentionally installs psycopg.
   The test assumes a base environment instead of isolating the import condition
   it intends to test.

These are not evidence that the repository workspace containment or SQL client
failed at runtime. They are real test portability/hermeticity defects and prevent
calling this particular all-tests invocation green. They also leave the affected
Windows symlink-containment branches unverified on this host; exact-SHA hosted
Windows/Linux coverage supplies separate evidence, not a local substitute.

### 7. Optional subsystem self-tests

| Subsystem | Result | Notes |
|---|---:|---|
| `agentic` | PASS | 5/5 |
| `fsconnect` | PASS with portability finding | 5/5 under `PYTHONUTF8=1`; default CP1252 console exits 1 while printing the Unicode union symbol |
| `sqlconnect` | PASS | Core guards passed; live DSN skipped |
| `guardrails` | PASS | Offline/config checks passed; NeMo dependency skipped by design |
| `sync` | WARN | 6/8; optional `rclone` absent and default filter path was outside the writable sandbox |

The live `/ops/* status` routes still returned authenticated HTTP 200 with each
optional layer disabled, which is the shipped default.

### 8. Real operating-system CI evidence

The exact-commit [main CI run](https://github.com/cgfixit/CyClaw/actions/runs/30823626174)
completed successfully. Its green jobs included:

- [Ubuntu Python 3.12](https://github.com/cgfixit/CyClaw/actions/runs/30823626174/job/91719511163)
- [macOS Python 3.12](https://github.com/cgfixit/CyClaw/actions/runs/30823626174/job/91719511081)
- [Windows Python 3.12](https://github.com/cgfixit/CyClaw/actions/runs/30823626174/job/91719511009)
- invariant guard, PostgreSQL backend, real-repo-run smoke, Ollama mock smoke,
  Deep Agents harness, workflow lint, and all discovered skill verifiers,
  including CyClaw-Sandbox.

The discovered skill-verifier matrix is explicitly advisory (`continue-on-error`);
the authoritative main gate is the test job's unit suite plus RAG smoke. Current
security workflows on the same commit were also green:
[CodeQL](https://github.com/cgfixit/CyClaw/actions/runs/30823626140),
[Semgrep](https://github.com/cgfixit/CyClaw/actions/runs/30823626674),
[Gitleaks](https://github.com/cgfixit/CyClaw/actions/runs/30823626211),
[OSV](https://github.com/cgfixit/CyClaw/actions/runs/30823627285),
[Trivy](https://github.com/cgfixit/CyClaw/actions/runs/30823626433),
[Fortify](https://github.com/cgfixit/CyClaw/actions/runs/30823626489),
[DevSkim](https://github.com/cgfixit/CyClaw/actions/runs/30823626832), and
[Defender/Bandit](https://github.com/cgfixit/CyClaw/actions/runs/30823626217).

The exact-commit [Conda run](https://github.com/cgfixit/CyClaw/actions/runs/30823626186)
is currently **green on attempt 2**. Attempt 1 passed pytest, then failed
`Run verify harness / smoke`:

- dependency install: pass;
- unit + integration tests: pass;
- emulated real-index RAG query: fail;
- gateway health: degraded (`index_ready=false`, `graph_ready=false`);
- terminal emulation: fail as a downstream consequence;
- independent gate and harness imports: pass;
- live harness-console emulation: pass.

Attempt 2 passed every step, including pytest and the sandbox harness. Attempt 1
did not print or upload the referenced RAG and server logs, so its root cause
remains unknown. The successful same-SHA rerun is consistent with environment or
verifier divergence, but the retained attempt-1 evidence is insufficient to prove
which one.

### 9. Artifact integrity

**PASS.** After teardown:

- tracked source diff: none before report creation;
- `config.yaml` SHA-256:
  `35e8c079240b74cbd0a4ed1c58c2017e23ac7f6c65e6bd212c71f28950340fac`;
- `data/personality/soul.md` SHA-256:
  `026a6c73e704da9e4e05907be41eeb9b8f69cedd15b0c67a87c6b7e27003c100`;
- external-model escalations recorded by metrics: 0.

## Findings and Recommendations

### P3 - Conda attempt-1 failure was non-diagnostic

The initial Conda attempt failed at real-index construction and reported degraded
gateway state, but referenced temporary logs were neither printed nor uploaded.
Attempt 2 passed on the same SHA, resolving the current CI state without explaining
the first failure.

**Recommendation:** upload or print the RAG, server, and terminal logs on failure;
record indexer exit code and final exception. Avoid treating model-cache
availability as proof of index readiness.

### P2 - `smoke.sh` can false-pass a crashed pytest invocation

`.claude/skills/CyClaw-Sandbox/smoke.sh` discards pytest's exit status with
`|| true` and decides success only from a parsed `N failed` phrase. A collection
crash, timeout, or truncated output can therefore produce `0 failed` and be marked
PASS even when pytest never completed.

**Recommendation:** retain the pytest return code and require both exit code zero
and a nonzero parsed pass count. Parsing should supplement, not replace, process
status.

### P2 - Guardrails quoted booleans violate opt-in semantics

`GuardrailsConfig` is a dataclass and does not validate `enabled`. A direct probe
with `enabled: "false"` produced `str 'false' True`; callers that use truthiness
will treat that common configuration typo as enabled. The shipped config uses the
real YAML boolean `false`, so current defaults remain off.

**Recommendation:** reject non-boolean `enabled` values in `__post_init__` and add
tests for quoted `"false"`, quoted `"true"`, integers, and null.

### P2 - Windows fsconnect self-test has an avoidable encoding failure

All five checks pass, but the default Windows CP1252 console raises
`UnicodeEncodeError` while printing `OWASP union banned_patterns`. Forcing UTF-8
makes the same command exit zero.

**Recommendation:** keep CLI diagnostic labels ASCII (for example, `OWASP +
banned_patterns`) or configure a safe output error policy at the CLI boundary.

### P1 - Windows ONNX telemetry opt-out is documented but not enforced

`utils/telemetry_kill.py` correctly states that `ORT_TELEMETRY_OPT_OUT` is unread
by ONNX Runtime and that the real API is `disable_telemetry_events()`, but it does
not call that API. The resolved runtime in this environment was 1.28.0 and exposed
the method.
This matters only for official Windows builds; Linux/macOS are documented in the
repository as off by construction.

**Recommendation:** either call the API before first ONNX use on Windows and add a
runtime assertion, or explicitly record this as an accepted privacy residual. The
current telemetry test proves environment state and import order, not vendor
runtime state.

### P2 - Authenticated soul restore can reapply advisory-flagged backup content

`restore_from_backup()` scans backup content only for advisory logging, then calls
`apply_evolution(..., scan=False)`. Backups are made from the raw on-disk soul, so
out-of-band-poisoned content can become a `.bak` and later be authenticated-restored.
The current behavior is deliberate and test-locked, not an accidental regression.

**Recommendation:** bind restorable backups to a previously vetted version/hash or
block critical injection matches on restore. Any change must update the documented
soul boundary and its tripwire tests deliberately.

### P2 - MSSQL linked-server reads cross the intended database boundary

The read-only SQL scanner accepted
`SELECT * FROM [linked_srv].[db].[dbo].[secrets]`. It remains a `SELECT`, but an
overprivileged MSSQL DSN can use four-part names to read through a linked server,
beyond the database an operator may believe was scoped.

**Recommendation:** enforce server/database/schema/table allowlists at the
connector boundary and retain least-privilege DSN credentials. Read-only parsing
alone is not an authorization boundary.

### P3 - Two tests encode environment assumptions instead of capabilities

- Symlink containment tests should probe or skip when Windows symlink creation is
  unavailable, while keeping hosted Windows coverage that can exercise the real
  branch.
- `test_driver_absent` should monkeypatch the driver import to be absent instead of
  assuming psycopg is not installed.

These changes would remove false red results without weakening the production
guards being tested.

## Documented Residual Boundaries

These were not newly introduced failures, but they remain important when reading
the green invariant result:

- I4 proves structural convergence on `audit_logger`; `utils.logger.audit_log`
  deliberately catches disk/permission `OSError` and returns the already-computed
  answer. Durable audit persistence therefore fails open under sink failure.
- `INVARIANTS.md` explicitly documents unscanned soul adoption on startup drift,
  `/soul/reload`, and direct harness prompt composition. The write-path scan is not
  an all-read-path guarantee.
- Input and output guard exceptions deliberately fail open, and the converged audit
  event does not record that protection degraded. `offline_best_effort` uses local
  generation but intentionally bypasses the output guard because its answer model
  is not `local`.
- Fixed-port process ownership was not PID-bound by the repo-native runner. A
  compatible pre-existing loopback service could theoretically satisfy a readiness
  probe, although the independent mock bind succeeded during the harness pass.
- No browser engine was used. Serving `terminal.html` and HTTP-level emulation do
  not prove DOM rendering, dynamic confirmation buttons, JavaScript error handling,
  downloads, or CSP behavior. `static/extractor.html` was not exercised.
- No stalled-backend timeout probe was run. `/query` wraps
  `compiled_graph.invoke` in `asyncio.wait_for(asyncio.to_thread(...))`; a 504 can
  cancel the await but cannot stop the worker thread, so model work and later
  audit/personality effects may continue after the response.
- No live NeMo engine, rclone remote, local PostgreSQL server, real Ollama model,
  graph-confirmed cloud-provider route, or billable Grok/Claude API was exercised
  locally. Graceful shutdown and timeout cancellation were also not verified. The
  hosted PostgreSQL job passed on the exact SHA; all other omitted integrations
  remain environment-bound.

## Retained-sandbox rerun commands

These commands assume the untracked `.uv-python` and `.venv-sandbox` created for
this audit are still present. They are rerun commands, not a standalone clean-host
bootstrap. Use the repository's setup documentation for a fresh environment.

```powershell
# Stable runtime and dependency integrity
.\.venv-sandbox\Scripts\python.exe --version
.\.venv-sandbox\Scripts\python.exe -m pip check

# Current targeted runtime/API sandbox
$env:PYTHONUTF8 = '1'
$env:GROK_API_KEY = 'dummy'
$env:CYCLAW_API_KEY = 'sandbox-test-key'
.\.venv-sandbox\Scripts\python.exe `
  .codex\skills\cyclaw-sandbox-test\scripts\run_sandbox_test.py `
  --in-place --skip-install --index-timeout 1800 --test-timeout 1800

# Full suite without coverage overhead
.\.venv-sandbox\Scripts\python.exe -m pytest tests\ `
  -o addopts= -q --tb=short --continue-on-collection-errors `
  -p no:cacheprovider

# Invariants and lint
.\.venv-sandbox\Scripts\python.exe `
  .claude\skills\invariant-guard\check_invariants.py --repo-root .
.\.venv-sandbox\Scripts\python.exe -m ruff check `
  --select E,F,I,B,C4,UP,S . `
  --extend-exclude .venv-sandbox `
  --extend-exclude .uv-python `
  --extend-exclude .uv-cache
```

## Appendix A - Full pytest summary

```text
6 failed, 2472 passed, 173 skipped in 201.82s (0:03:21)

FAILED tests/test_agentic_repo_workspace.py::test_read_file_rejects_a_symlink_escape
FAILED tests/test_agentic_repo_workspace.py::test_add_rejects_a_symlink_escape
FAILED tests/test_agentic_repo_workspace.py::test_write_file_refuses_a_symlink_that_points_at_the_git_directory
FAILED tests/test_agentic_repo_workspace.py::test_write_file_allows_a_symlink_to_an_ordinary_directory
FAILED tests/test_agentic_repo_workspace.py::test_write_file_rejects_a_symlink_escape_via_existing_ancestor
FAILED tests/test_sqlconnect_client.py::test_driver_absent
```

The full local traceback is intentionally not committed because it contains
machine-specific absolute paths.

## Appendix B - Sandbox result summary

```text
Result: 30 PASS / 0 FAIL / 0 WARN
Index: 70 chunks, ChromaDB + BM25
Gateway: ready on loopback
Health: ok; index_ready=true; graph_ready=true; mode=hybrid
Injection probe: HTTP 400
Soul unauthenticated read/mutation: HTTP 401
Authenticated soul read/reload: HTTP 200
Ops status routes: HTTP 200 (sync, agentic, fsconnect, sqlconnect)
CI RAG smoke: PASS
Targeted API/RAG pytest: PASS
```

## Appendix C - `metrics.py` output

This is a mixed aggregate from the full pytest fixtures plus live sandbox requests.
The audit file was not reset at a timestamp boundary, so these counts are useful as
format/aggregation evidence but are not phase-isolated runtime totals.

```text
Total events: 666

Event breakdown:
  agentic_repo_workspace_cloned: 127
  agentic_repo_workspace_denied: 83
  agentic_real_repo_loop_iteration: 43
  agentic_harness_proposer_model_invoked: 42
  agentic_harness_proposer_model_succeeded: 42
  agentic_executor_check_result: 40
  agentic_repo_workspace_write: 40
  agentic_real_repo_loop_started: 34
  agentic_repo_workspace_git_ok: 31
  agentic_repo_workspace_git_op: 31
  agentic_real_repo_loop_accepted_pending_decision: 20
  agentic_repo_workspace_read: 19
  agentic_skill_applied: 19
  agentic_real_repo_loop_exhausted: 14
  agentic_read: 11
  mcp_rag_query: 9
  rag_query: 8
  agentic_real_repo_change_decided: 5
  agentic_write_refused: 5
  sqlconnect_read: 4
  agentic_read_timeout: 3
  agentic_real_repo_change_approved: 3
  soul_evolution_applied: 3
  prompt_injection_blocked: 2
  soul_read: 2
  ops_sync_executed: 2
  ops_agentic_executed: 2
  ops_fsconnect_executed: 2
  ops_sqlconnect_executed: 2
  agentic_read_retry: 2
  agentic_skill_injection_blocked: 2
  agentic_repo_workspace_git_failed: 2
  mcp_rag_error: 2
  fsconnect_write_refused: 2
  fsconnect_read: 2
  agentic_repo_workspace_clone_failed: 1
  agentic_write_dryrun: 1
  local_llm_backend_selected: 1
  sync_started: 1
  sync_file_added: 1
  sync_completed: 1

RAG queries: 17
RAG scores - avg: 0.447, min: 0.016, max: 0.920

Retrieval modes:
  hybrid: 13
  semantic: 2
  keyword: 2

Model used:
  local: 8

Online escalations (external LLM): 0
```

All metrics were generated by isolated test fixtures and sandbox requests, not by
a production corpus or live user session.
