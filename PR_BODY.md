## Branch naming (required for agent-opened PRs)

`grok/authn-postgres-ci`

## Title

`[security] - add live Postgres CI coverage for the authn backend`

## Proposed changes

Closes #996.

`postgres-backend` already runs live tests for the soul DB, rate limiter, and pgvector. Nothing opened `utils/authn_store.py`'s Postgres path against a real server — `TestPostgresOptIn` only hits the missing-`psycopg` `ImportError` branch.

This PR adds `tests/test_authn_postgres.py` and wires it into that job. No production code change. The interleaved last-admin TOCTOU case stays on #997 (High-tier; needs sign-off).

Coverage, matching the 2026-08-19 issue comment:

- `users_column_names()` `information_schema` branch and `ensure_users_role_column()` `ALTER TABLE`
- Partial unique index `idx_device_tokens_live_label` (two live labels fail; revoked labels reuse)
- `connect()` contract: backend `postgres`, placeholder `%s`, inherited hardening (`application_name`, `statement_timeout`)
- One `AuthManager` lifecycle through `%s` SQL: bootstrap → create → login → validate → logout → device-token create/verify/revoke

DSN is passed via `auth.database_url` (config precedence). No new `CYCLAW_AUTH_DB_URL` workflow env. Skip-gate reuses the job's existing `CYCLAW_DB_URL`. Cleanup uses a separate autocommit connection because `authn_store.connect` is `autocommit=False`.

**Invariant / Governance Impact**
- None of I1–I6. Test + CI list + docs only. `utils/authn_store.py` / `utils/authn_manager.py` / graph / gate / soul untouched.
- Evidence: invariant-guard 35/35 on this tree.

## Types of changes

- [ ] Bugfix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [x] Documentation Update (if none of the other choices apply)
- [ ] Invariant / Governance refinement (use this for changes that strengthen or evolve the 6 invariants, I6 isolation, or harness phases)

**Optional free-text scope note:** Infrastructure / CI only (live Postgres tests for authn) plus the verification file lists in `docs/POSTGRES_BACKEND.md` and `AGENTS.md`.

## Benefits / why

- Every Postgres claim in `authn_store` (information_schema, ALTER TABLE role, partial unique live-token index, `%s` AuthManager SQL) now has a CI job that actually runs it.
- Default SQLite suite stays green: the new file skips unless a Postgres DSN is set.
- Builds the harness #997 needs without changing the last-admin write path.

## Risks to monitor

- This Windows host has no Docker and no importable `psycopg`. Live assertions are proven only when GitHub `postgres-backend` is green — do not treat the skip path as that proof.
- If Postgres rejects the claimed-portable partial unique index, that is a real product bug in `ddl_indexes()`, not a flaky test.
- scrypt cost: lifecycle test hashes bootstrap + one user only.
- 5000ms statement_timeout is unchanged; these tests do not block.
- #997 remains open. This PR does not prove the last-admin guard under concurrent Postgres writes.

## Checklist

- [x] I have read the latest `docs/CyClaw Architecture Guide` (and any relevant Phase docs) and `SECURITY.md`
- [x] This change preserves all 6 security invariants and I6 module isolation (explicit evidence or invariant matrix included for core changes)
- [x] Full sandbox validation has been run (`cyclaw-sandbox-validator` or equivalent pytest + smoke tests on core RAG/agentic paths) and passes with no regressions
- [x] No new external network dependencies or mandatory online LLM assumptions were introduced without explicit justification + offline fallback path
- [x] For any agentic/fsconnect/harness change: two-phase audit, quota enforcement, governed delete/trash, and write guards have been verified
- [x] Relevant architecture docs, threat model notes, or harness phase documentation have been updated if core behavior or topology changed
- [x] Commit messages follow the title prefix convention above
- [x] For large or complex changes: before/after invariant matrix + sandbox evidence is included in "Further comments" or linked
- [x] cyclaw-sandbox + CI emulation stamp written (`verify_ci_emulation.py`)
- [x] Draft PR only; no push to `main`

## Further comments

Test-only. `psycopg` is imported inside fixtures/tests so collection stays clean when the driver is absent. Cleanup drops `users` / `sessions` / `device_tokens` on a separate autocommit connection.

Grok-security: PASS
Repo: cyclaw
Bars: telemetry / privacy / auth / injection / egress / agent-tools
Delegated: invariant-guard (35/35)
Stop-and-ask: none
Verdict: no production auth path change; live Postgres coverage only.

## Verify

- `python -m ruff check tests/test_authn_postgres.py --select E,F,I,B,C4,UP,S` → exit 0
- `python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml', encoding='utf-8'))"` → ok
- `GROK_API_KEY=dummy python -m pytest tests/test_authn_postgres.py -q --tb=short` → 5 skipped, exit 0 (no DSN on this host)
- `GROK_API_KEY=dummy python -m pytest tests/test_authn_store.py tests/test_authn_manager.py tests/test_authn_rbac.py -q --tb=short` → exit 0
- `GROK_API_KEY=dummy python -m pytest tests/ -q --tb=short` → exit 0
- invariant-guard `--repo-root` this clone → 35 passed, 0 failed
- `python ~/.grok/githooks/cyclaw/verify_ci_emulation.py` before push
- GitHub Actions `postgres-backend` is the live proof (not runnable on this Windows host: no Docker, no psycopg)

## Merge order

- This PR: P1 of 1
- Full stack: P1
- Topology: single PR from `origin/main`

## Base

- GitHub base: `main`
- Forked from: `origin/main@ae32cc17`
