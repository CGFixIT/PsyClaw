# CyClaw — Consolidated Security Review Status

**Last updated:** 2026-07-27 (S5/S7/follow-ups reconciled against current `main`; see
below. Original table: 2026-06-20.)
**Scope:** Single source of truth for the status of every security finding raised across the
historical reviews. Supersedes the per-run status notes in
[`CODE_SECURITY_REVIEW_2026-06-20.md`](./CODE_SECURITY_REVIEW_2026-06-20.md) (the doc
formerly linked here, `CODE_SECURITY_REVIEW_2026-06-16.md`, no longer exists — this is its
successor in the same series) and the remediation notes in
[`../../tests/TEST_SUITE_AUDIT.md`](../../tests/TEST_SUITE_AUDIT.md), which remain as dated
historical records. **Where they disagree with this table, this table wins.**

Status reflects `main` plus the `cleanup/assurance-pass` hardening pass, S5/S7 status and the
"Recommended follow-ups" reconciled again on 2026-07-27 against current `main`. A more recent,
broader pass lives in [`2026-07-26-security-scan.md`](./2026-07-26-security-scan.md) — check
there too for anything found after this table's S1–S10 baseline.

---

## Status summary

| ID | Severity | Finding | Status | Where resolved / residual |
|----|----------|---------|--------|---------------------------|
| S1 | Medium | Rate limiter: unbounded per-IP growth **and** no lock (read-modify-write under threadpool) | **Resolved** | Idle-IP eviction landed earlier on `main`; the missing `threading.Lock` is now added by extracting the limiter into `utils/ratelimit.RateLimiter` (T1.1). Concurrency/eviction/window tests in `tests/test_rate_limit.py` (T1.4). |
| S2 | Medium | Soul governance: non-atomic write, no forensic `audit_log` on drift/apply, no TTL-prune-on-init, no write lock | **Resolved** | `utils/personality.py` on `main` now does atomic `os.replace`, imports `audit_log` and emits `soul_drift_detected` + `soul_evolution_applied`, has `maintenance(ttl_days)` called from `__init__`, and guards writes with `threading.Lock`. |
| S3 | Medium | `pickle.load` of the BM25 index → RCE if the artifact is tampered | **Resolved** | BM25 index is JSON end-to-end: `retrieval/indexer.py` writes `json.dump`, `retrieval/hybrid_search.py` reads `json.load` and rebuilds `BM25Okapi`. No `pickle` import in any production module. Regression-guarded by `tests/test_security.py::TestBM25PickleRejection`. |
| S4 | Medium | Injection filter weaker than documented (missing `do anything now` / `bypass safety` / `ignore safety`) | **Resolved** | `config.yaml` already carried `do anything now` and `bypass safety`; `ignore safety` is now added (T3.1). `tests/test_sanitizer.py::TestShippedConfigContract` asserts the **shipped** config blocks all documented phrases. |
| S5 | Medium | CI false-green: only a subset of tests run, exit code swallowed (`\|\| echo`) | **Resolved** | `.github/workflows/ci.yml`'s `test` job runs the full `pytest tests/` (not a hand-picked subset) with no swallowed exit code, plus a real ChromaDB+BM25 RAG smoke step. Matches the command documented in `CLAUDE.md` §8. |
| S6 | Low/Med | Dead config: `policy.prompt_filter.*` ignored by a hardcoded sanitizer | **Resolved** | `utils/sanitizer.py` on `main` is config-driven (`_load_filter(config_path)` reads `enabled` / `banned_patterns` / `max_input_chars`, caches per path, warns when enabled-but-empty). Verified by `tests/test_sanitizer.py` incl. the new `enabled:false` bypass / per-config tests. |
| S7 | Low | CORS allowlist contains an inert `null`/`None` entry + a hardcoded LAN IP | **Partially resolved** | The literal `"null"` entry is gone — `config.yaml` now carries an explicit `# NOTE: "null" is deliberately absent from this list — do not re-add it` guard comment. `http://10.0.0.112(:8787)` remains, documented in `config.yaml` as an intentional home-lab LAN origin (the gateway still binds `127.0.0.1` only). Reaffirmed as accepted, not a code defect, by `docs/audits/2026-07-26-security-scan.md`'s A5. |
| S8 | Low/Med | `/soul/propose` injection scan is advisory; `apply_evolution` wrote unconditionally (soul-poisoning vector: a flagged soul persisted to `soul.md` is prepended to every system prompt) | **Resolved** | `apply_evolution` now ENFORCES the scan at the write boundary (`main` commit `001e4a4`): injection patterns raise `PromptInjectionError` before any file/DB write, and `gate.py` maps it to `400 PROMPT_INJECTION_BLOCKED`. `propose` stays advisory (preview only); the trusted `restore_from_backup` path re-applies a vetted `.bak` via `scan=False`. Documented in the `propose_evolution`/`apply_evolution` docstrings and the README. Regression-guarded by `tests/test_personality.py::TestApplyEvolutionInjectionGate`. |
| S9 | Low | No auth on state-mutating `/soul/*` endpoints | **Resolved** | Bearer-token auth via `CYCLAW_API_KEY` on `/soul/propose`, `/soul/apply`, `/soul/reload`, `/soul/restore` (`gate.require_api_key`). Tested in `tests/test_security.py::TestAPIKeyAuth`. Auth is bypassed only when the env var is unset (single-user localhost default). |
| S10 | Info | Positive controls (XSS escaping, secret redaction, telemetry kill, no-sampling MCP, env-only key) | **OK** | Re-verified. MCP audit privacy is now at parity with the HTTP path (T1.3): the persisted MCP event stores only a `query_hash`, identical to what the HTTP/graph path writes. |

No **Critical** issues. No secrets committed.

---

## Architecture-invariant compliance (current)

| # | Invariant | Status | Evidence |
|---|-----------|--------|----------|
| 1 | RAG-first: `retrieve` is the unconditional entry node | ✅ | `graph.set_entry_point("retrieve")`; single `retrieve → route_by_score` edge |
| 2 | Topology = policy (routing is structural, not LLM/ad-hoc) | ✅ | conditional edges keyed on `needs_user_confirm` / confirmation; prompts cannot add edges |
| 3 | Triple-gated Grok (`mode=hybrid` AND `grok.enabled` AND `user_confirmed_online`) | ✅ | mode+enabled enforced by `grok=None` build in `gate.py`; confirmation by `user_gate_router` |
| 4 | Audit convergence (all answer paths → `audit_logger` → END) | ✅ | every terminal node edges to `audit_logger`; HTTP and MCP both route through `audit_log` |
| 5 | Soul governance: explicit human reason, atomic write, forensic log, enforced injection gate, no autonomous path | ✅ | `apply_evolution` requires `reason`, enforces the injection scan at the write boundary (`PromptInjectionError` → `400`), atomic `os.replace`, `audit_log` on drift/apply/block; evolution is an HTTP endpoint, never a graph node |
| — | Loopback-only default + pre-import telemetry kill + retrieval-only MCP (`sampling=None`) | ✅ | `api.host: 127.0.0.1`; `_TELEMETRY_KILL` set before any SDK import; `CAPABILITIES["sampling"] is None` |

---

## Recommended follow-ups (not blocking)

1. ~~**S5 residual** — point CI at the full `pytest tests/`.~~ **Done** — see S5 above.
2. **S7 LAN origin** — the inert `"null"` entry is gone (see S7 above); moving
   the LAN origin behind an env-specific override remains open but is a
   deployment-policy choice, not a defect — the operator may prefer it stay a
   checked-in default for this single-home-lab tool.
3. **Supply chain** — `pip-audit` now runs in CI (`.github/workflows/pip-audit.yml`).
   Hash-locked installs (`pip install --require-hashes`) remain **not done**:
   every dependency would need per-platform hashes across the ubuntu+windows CI
   matrix, which is a large enough change (and a real CI-breakage risk if a
   transitive dependency's hash is missing for either platform) to warrant its
   own dedicated, iterated PR rather than a drive-by fix.
4. ~~De-duplicate the two injection pattern lists.~~ **Done, architecturally** —
   `utils/personality.py`'s `_build_patterns` unions `config.yaml`'s
   `banned_patterns` with the OWASP/core set at runtime (see the module
   docstring: "config.yaml policy.prompt_filter, unioned with a legacy OWASP
   baseline"); the two lists remain textually separate by design (the enforced
   write-boundary set is deliberately narrower than the advisory review set)
   but no longer drift silently — config patterns are incorporated everywhere.
