# `utils/` — shared server-side helpers

Support modules for the core request path (`gate.py`, `graph.py`,
`mcp_hybrid_server.py`) and its harness endpoints. Deliberately **not** a
package — there is no `__init__.py`, which is why a bare repo-root
`mypy --strict .` errors with "source file found twice"; add
`--explicit-package-bases` (see `CLAUDE.md` §4 "Testing").

The authoritative one-line-per-module map lives in `CLAUDE.md` §2 "Key
modules"; this file groups them by concern.

## Security / policy

| Module | Role |
|---|---|
| `sanitizer.py` | Injection filter for `/query`; patterns come from `config.yaml` (`banned_patterns`). `lru_cache`d by config path — restart to pick up edits. |
| `auth.py` | Harness-only API-key auth: fail-closed on unset `CYCLAW_API_KEY`, `hmac.compare_digest`. `gate.py` keeps its own separate copy by design — do not refactor them together (see `CLAUDE.md` §2). |
| `authn.py` | Per-user authentication primitives (scrypt hash/verify, lockout arithmetic, session/CSRF/token id generation). Pure functions — no DB, no HTTP. Distinct from `auth.py` above. |
| `authn_store.py` | SQLite/Postgres backend for users/sessions/device tokens (`CYCLAW_AUTH_DB_URL`). |
| `authn_manager.py` | `AuthManager` gluing `authn.py` + `authn_store.py`; no HTTP awareness. |
| `authn_cli.py` | `cyclaw-user` console script — local-only user/token admin. |
| `gen_cert.py` | `cyclaw-gen-cert` console script — wraps `openssl req -x509` to write a self-signed TLS cert with hostname/LAN SAN (Stage 4 of `docs/AUTHENTICATION_DESIGN.md`); no new runtime dependency. |
| `telemetry_kill.py` | Canonical telemetry-kill env mapping. Stdlib-only; must be applied **before** heavy imports (`invariant-guard` G1). |
| `guardrail_bridge.py` | Inversion shim: the only module through which `graph.py` reaches `guardrails/` (I6). Returns `None` for a disabled rail. |
| `external_pre_hook.py` | Synchronous pre-action hook runner behind the `pre_action_hook_grok`/`pre_action_hook_claude` graph nodes: runs the configured command before any Grok/Claude call; exit 0 allows, exit 2 denies, anything else fails closed. |
| `numbat_emitter.py` | Numbat NDJSON dual-write emitter (`logs/numbat-events.ndjsonl`). Two planes: the **action** plane (direct `emit_numbat_*` calls from executor/ops_runner/real_repo_loop/fsconnect/sqlconnect) and the **mainline** plane (`project_audit_record`, every redacted audit record). `_AUDIT_ACTION_PLANE_EVENTS` keeps events that emit on both from being written twice. Never raises; audit.jsonl stays authoritative. |

## Soul / audit / errors

| Module | Role |
|---|---|
| `personality.py` | Soul versioning, SHA-256 drift detection, injection scan + human-`reason` gate on write (invariant I5), atomic writes. |
| `personality_db.py` | Soul DB backend: SQLite default, Postgres via `CYCLAW_DB_URL`. |
| `logger.py` | Audit JSONL: SHA-256 query hashing, recursive PII redaction. Raw query text is never persisted. `audit_log` also projects each record — *after* hashing and redaction — into the Numbat stream via a lazy, fail-soft `numbat_emitter` call, so the derived stream inherits the same privacy contract. |
| `errors.py` | Typed exception hierarchy rooted at `RAGError` (`.code`/`.message`/`.details`). Never raise bare `Exception`. |
| `spend.py` | Append-only Grok/Claude token ledger (`logs/spend.jsonl`, `logging.spend_file`). Tokens are ground truth; dollars derived at read time by `metrics.py`. Separate stream from `audit.jsonl`; never persists query/prompt content. See [`docs/spend/README.md`](../docs/spend/README.md). |
| `sequence_detect.py` | Offline forensic sequence detection over a join of `audit.jsonl` + `spend.jsonl`, keyed on the unsalted SHA-256 `query_hash`. Correlates blocked-injection events against later online escalations inside a 15-minute window. Spend rows are restricted to `source == "query"` so the agentic ledger plane never mixes in. Findings carry hashes, event names, timestamps, and provider/model tags — never query text, IPs, soul content, or secrets. Forensic/CLI only: `gate.py`, `graph.py`, and the MCP server must not import it (it is not a `/query` policy point). |

## Serving / ops

| Module | Role |
|---|---|
| `ratelimit.py` | Per-IP rate limiting; in-memory / SQLite / Postgres backends. |
| `health.py` | `check_all()` behind `/health`. `degraded` without Ollama is normal. External-provider probes are opt-in (`api.health_probe_external_providers`, ships false). |
| `config_validation.py` | Boot-time config validation; fails fast on a broken `config.yaml`. |
| `ops_runner.py` | `subprocess.run([...])` shim behind the four `/ops/*` endpoints — core never imports `sync`/`agentic` (I6). |
| `launchd_plist.py` | Stdlib-only plist builder shared by the `macos/` + `sync`/`telegram`/`fsconnect` launchd generators. |
| `win_schtasks.py` | Stdlib-only Windows Task Scheduler XML helper shared by `sync`/`agentic.fsconnect`/`telegram`/`windows/generate_service_task.py`; never calls `schtasks /Create` itself — returns the command an operator runs by hand. |
| `agent_identity.py` | Driver-agnostic committer identity + branch-prefix allowlist for all agent write surfaces. |
| `repo_paths.py` | Stdlib-only mirror of `agentic`'s repo-relative path-safety rule (no `..`, no absolute/drive-qualified path, no leading `-`), shared by `harness` and `ops_runner` so they can reject the same escapes without importing `agentic` (I6). |
| `selftest.py` | Shared self-test plumbing used by the out-of-band subsystems' `test` subcommands. |
| `mcp_manifest.py` | Pure compare/verify layer for the committed MCP tools manifest pin: fingerprints the registered `TOOLS` list so drift against `mcp_manifest.json` fails closed in `mcp_hybrid_server.py`. |

## Related

- Which invariant each guarantee belongs to: repo-root `INVARIANTS.md`
- Threat model and scope: `docs/THREAT_MODEL.md`
