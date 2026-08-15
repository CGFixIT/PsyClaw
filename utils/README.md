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
| `telemetry_kill.py` | Canonical telemetry-kill env mapping. Stdlib-only; must be applied **before** heavy imports (`invariant-guard` G1). |
| `guardrail_bridge.py` | Inversion shim: the only module through which `graph.py` reaches `guardrails/` (I6). Returns `None` for a disabled rail. |

## Soul / audit / errors

| Module | Role |
|---|---|
| `personality.py` | Soul versioning, SHA-256 drift detection, injection scan + human-`reason` gate on write (invariant I5), atomic writes. |
| `personality_db.py` | Soul DB backend: SQLite default, Postgres via `CYCLAW_DB_URL`. |
| `logger.py` | Audit JSONL: SHA-256 query hashing, recursive PII redaction. Raw query text is never persisted. |
| `errors.py` | Typed exception hierarchy rooted at `RAGError` (`.code`/`.message`/`.details`). Never raise bare `Exception`. |

## Serving / ops

| Module | Role |
|---|---|
| `ratelimit.py` | Per-IP rate limiting; in-memory / SQLite / Postgres backends. |
| `health.py` | `check_all()` behind `/health`. `degraded` without Ollama is normal. External-provider probes are opt-in (`api.health_probe_external_providers`, ships false). |
| `config_validation.py` | Boot-time config validation; fails fast on a broken `config.yaml`. |
| `ops_runner.py` | `subprocess.run([...])` shim behind the four `/ops/*` endpoints — core never imports `sync`/`agentic` (I6). |
| `launchd_plist.py` | Stdlib-only plist builder shared by the `macos/` + `sync`/`telegram`/`fsconnect` launchd generators. |
| `agent_identity.py` | Driver-agnostic committer identity + branch-prefix allowlist for all agent write surfaces. |
| `repo_paths.py` | Repo-root anchoring so nothing resolves paths against cwd. |
| `selftest.py` | Shared self-test plumbing used by the out-of-band subsystems' `test` subcommands. |

## Related

- Which invariant each guarantee belongs to: repo-root `INVARIANTS.md`
- Threat model and scope: `docs/THREAT_MODEL.md`
