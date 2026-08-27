# Auth, Memory, and Console Security Review — Planning Doc

Date: 2026-08-27
Scope: `utils/auth.py`, `utils/authn.py`, `utils/authn_store.py`,
`utils/authn_manager.py`, `utils/authn_cli.py`, `utils/gen_cert.py`,
`gate_auth.py` (this repo has no top-level `auth/` — these modules together
are "auth" per `CLAUDE.md`'s own module map); the `memory/` package plus
`gate_memory.py`; `static/terminal.html`/`static/terminal.js`,
`static/harness.html`, `static/auth_admin.js`; `harness/server.py` and the
rest of the `harness/` package (slash commands, skills/tools registries, the
29-guarded-route surface — see F-07 below on `CLAUDE.md`'s stale count of 28).
Method: four parallel code-review passes (one per area above) cross-checked
against `docs/AUTHENTICATION_DESIGN.md`, `docs/memory/README.md` +
`IMPLEMENTATION_PLAN.md`, `harness/README.md`, `docs/HARNESS_POWERSHELL.md`,
and `docs/HARNESS_MACOS.md`, followed by live verification: both `gate.py`
and `harness/server.py` were booted in a Python 3.12 venv
(`GROK_API_KEY=dummy`, an ephemeral `CYCLAW_API_KEY`) and rendered headlessly
with Playwright/Chromium (`/opt/pw-browsers/chromium`) to confirm or refute
the static findings against real console/network/console-error behavior.
Baseline: branch `claude/cyclaw-optimize-scanner-config-sync` (this doc is a
scope addition to that branch/PR — see the note at the end of this file).

No code changes are proposed as part of *this* doc — it is a findings +
planning artifact per the request that produced it. Suggested fixes are
described, not implemented, so each can be reviewed and split into its own
PR per the project's "one reviewable concern per PR" convention.

## Verdict

No Critical or High-severity findings. The auth subsystem in particular is
unusually well-defended — most classes of bug the review hunted for (SQL
injection, session fixation, privilege escalation, timing side-channels)
were already anticipated and closed in code, and were verified rather than
assumed. The two most consequential findings are on the harness console (the
codebase's own description of it: "the MORE privileged of the two surfaces"):
its shared Users admin panel silently never loads, and its CSP is
effectively empty. Both are confirmed live, not just in static code.

## Evidence Summary

| Area | Method | Result |
| --- | --- | --- |
| Auth (`utils/auth*.py`, `gate_auth.py`) | Static review vs. `docs/AUTHENTICATION_DESIGN.md` | 1 Low, 5 Informational |
| Memory (`memory/`, `gate_memory.py`) | Static review vs. `docs/memory/*` | 1 Medium, 2 Low, 4 Informational |
| Harness backend (`harness/server.py` + package) | Static review vs. `harness/README.md`, `docs/HARNESS_*.md` | 1 Low, 3 Informational |
| Consoles (`terminal.html`/`.js`, `harness.html`, `auth_admin.js`) | Static review + live Playwright render | 2 Medium (both live-confirmed), 1 Informational |
| `gate.py` boot + `/` + `/health` | Live (venv + `python gate.py`) | PASS — index missing is fail-soft (503 on `/query` only), matches documented behavior |
| `harness/server.py` boot + `/` | Live (venv + `python -m harness.server`) | PASS |
| Console rendering (`terminal.html`) | Live Playwright, full-page screenshot | PASS — no console exceptions; only 4xx seen is `/auth/whoami` 503 (expected, `auth.enabled` ships `false`) and a harmless `/favicon.ico` 404 |
| Console rendering (`harness.html`) | Live Playwright, full-page screenshot | PARTIAL — renders and functions (COMMANDS/SESSIONS/REGISTRY tabs, `/skills all` listed 32 skills, `/tools all` listed 27 tools), but see F-01 and F-02 |
| `/skills`, `/tools` slash commands | Live, interactive (typed into the console, screenshotted) | PASS — correct wired-vs-total counts (4/32 skills, 27/27 tools) |
| Users tab on harness console | Live, interactive | FAIL — see F-01 |

## Findings

Ordered by severity, then by area.

### F-01 (Medium, confirmed live): harness console's shared Users panel never loads

`static/harness.html:233` loads `<script src="/static/auth_admin.js">`, but
`harness/server.py`'s `create_app()` never calls `app.mount("/static", ...)`
the way `gate.py:455` does for the terminal console. Live reproduction:
booting `harness/server.py` and requesting `GET /static/auth_admin.js`
returns `404 {"detail":"Not Found"}`; Chromium additionally refuses to
execute the 404 body because it comes back as `application/json` under
strict MIME-type checking. `window.CyClawAuthAdmin` is confirmed `undefined`
in the live page (`page.evaluate("() => typeof window.CyClawAuthAdmin")` →
`"undefined"`). Clicking the "Users" tab (or typing `/users`) only prints
"users panel opened — same accounts as the gate console" and leaves the
panel body empty — confirmed by screenshot, no rows, no error banner.

This fails closed (no data exposure, no false-success state), so it is not
independently exploitable. But it means the exact hardening
`tests/test_auth_admin_contract.py` locks in — the `mutate()` helper and
`reload(preserveStatus)` behavior that make a refused role-change/delete/
password-reset surface its own failure instead of silently reloading a
stale-looking success — never actually executes on the harness surface, and
no existing test catches the break (the contract tests assert the HTML
*text* contains `onStatus:`/`usersPanelStatus`, not that the script tag
resolves at runtime).

Suggested fix: mount `/static` in `harness/server.py`'s `create_app()`
(mirroring `gate.py:455`), or add an explicit route serving
`auth_admin.js`. Add a runtime check (even a lightweight one, e.g. a live
smoke test that boots the harness app and asserts `GET /static/auth_admin.js`
returns 200) so a future refactor of either app's static-mount wiring can't
silently reintroduce this.

### F-02 (Medium, confirmed live): harness console ships with effectively no CSP

`harness/server.py`'s `GET /` handler sets
`Content-Security-Policy: frame-ancestors 'none'` and nothing else. Because
`_SecurityHeadersMiddleware` applies defaults via `response.headers.setdefault(...)`,
this explicit header completely overrides the strict default
(`default-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'`)
every other harness route gets. Live-confirmed: Playwright's response headers
for `GET /` on port 8790 show `content-security-policy: frame-ancestors 'none'`
verbatim — no `script-src`, `style-src`, or `connect-src` restriction at all.
By contrast the terminal console (port 8787) returns the full policy
(`default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; ...`).

This is a known, deliberate tradeoff to accommodate `harness.html`'s inline
`<script>` block (documented in `tests/test_harness_security_headers.py`),
not a demonstrated exploit — no live XSS was found to combine with it. But
it means CSP provides zero defense-in-depth against any *future* XSS bug on
the more privileged console (the one that can run checks, push branches, and
open PRs), while the less-privileged terminal console keeps `script-src
'self'`.

Suggested fix: mint a per-process nonce alongside the harness console's
existing per-process CSRF token, inject it into both the CSP header
(`script-src 'self' 'nonce-...'`) and the inline `<script nonce="...">` tag
at serve time, instead of dropping `script-src` (and friends) entirely.

### F-03 (Medium): `memory.facts.enabled` gates retrieval fusion only, not persistence

Traced `create_proposal`/`apply_proposal` (`memory/store.py:488-524`,
`595-694`): neither checks `cfg["memory"]["facts"]["enabled"]`. The only
propose/apply gate is `gate_memory.py`'s `_propose_on()`, which checks the
master `memory.enabled` + `propose_apply.enabled` — not `facts.enabled`. So
with `memory.enabled: true` and `propose_apply.enabled: true` but
`facts.enabled: false` (a state `docs/memory/README.md`'s own progressive-
enablement steps walk an operator through), a caller can propose and apply
real, durably persisted, FTS-indexed facts while the flag literally named
`facts.enabled` reads `false`. In this codebase `facts.enabled` actually only
gates whether facts are *fused into retrieval* (`retrieval_adapter.py:32-34`,
`hybrid_search.py:304-309`) — it does not gate persistence or read access
(`GET /memory/facts` in `gate_memory.py:91-100` only checks the master
switch). The inconsistency is operator-visible: `memory/mirror.py:12-37`'s
`status_dict` can legitimately report `{"facts_enabled": false,
"active_facts": 3}` simultaneously. Contrast with episodes, where
`stage_episode` (`store.py:792-801`) *does* self-check `episodes.enabled`
before writing — the codebase's own internal gating pattern is inconsistent
between the two data types.

The progressive-enablement doc narrative arguably intends this ordering, so
it may be by design rather than an outright bug — but the flag's name and
its appearance in the status dict create a real operator-expectation and
data-retention surprise.

Suggested fix: either rename `facts.enabled` to something like
`facts.retrieval_enabled` (and update `config.yaml`'s comment + both memory
docs to say explicitly it only gates fusion), or — if the intent really is
"no facts exist while this is false" — add an explicit `facts.enabled` check
to `create_proposal`/`apply_proposal`.

### F-04 (Low): `GET /api/harness/runs` has no auth or rate limit, leaks an absolute filesystem path

`harness/server.py:1455-1483` has no `dependencies=` at all, unlike its
sibling `GET /api/agent/runs/{run_id}` which the code itself justifies
guarding because "the record names ... the clone's absolute location"
(server.py:1358-1360). It returns `{"run_id": ..., "path": str(path)}` for
every accepted harness-optimizer artifact under
`data/agentic/harness_optimizer/runs/`, where `path` is an absolute
filesystem path that on a POSIX box typically embeds the operator's OS
username.

Given the loopback-only threat model, any local caller is already
semi-trusted, and the leaked value is a predictable path derived from
public source, not a secret — this is Low, not a boundary crossing on its
own. But it's inconsistent with how every structurally similar route in the
same file is guarded.

Suggested fix: add `dependencies=guarded` (or at minimum
`Depends(_enforce_rate_limit)`) to this route, matching its sibling
agent-run routes.

### F-05 (Low): `AuthManager.create_user` has no unique-violation handling, unlike `bootstrap_if_empty()`

`utils/authn_manager.py:384-399`: two concurrent `create_user("bob", ...)`
calls (e.g. the HTTP admin route racing a concurrent `cyclaw-user add bob`)
can both pass the `SELECT`-then-`INSERT` check; the loser's `INSERT` raises
an uncaught `sqlite3.IntegrityError` (or a Postgres unique-violation) that
isn't one of `AuthLastAdmin`/`AuthUserExists`/`AuthUserNotFound`/
`PasswordPolicyError`, so `gate_auth.py`'s `_raise_auth_error` re-raises it
verbatim. `bootstrap_if_empty()` already has the equivalent guard for its own
race. Impact is bounded — FastAPI's default handler returns a generic 500
with no exception content, so there's no information disclosure, only a
wrong status code (500 instead of the intended 409 `AUTH_USER_EXISTS`) under
a narrow, hard-to-trigger race between two already-authenticated
admin/operator actors.

Suggested fix: wrap the `INSERT` in the same try/except-`_is_unique_violation()`
pattern `bootstrap_if_empty()` already uses, raising `AuthUserExists` on
conflict.

### F-06 (Low): memory injection scan (I5-analog) covers `content` only, not `category`/`tags`

`memory/policy.py:enforce_content` is only ever called against the `content`
field (`memory/store.py:497-498` at propose time, `:626-627,644-645` at
apply time). `category`/`tags` are validated for size/count
(`policy.py::check_tags`) but never scanned for injection content, at either
propose or apply time. Both fields are written into the `facts_fts` virtual
table and returned verbatim via `GET /memory/facts`
(`gate_memory.py:91-100`). Practical impact is low: writing either field
requires the same Bearer `CYCLAW_API_KEY` as content writes (no new trust
boundary), `fuse_memory_hits` only surfaces `content` into RAG synthesis
(`category`/`tags` never reach answer-generation context), and
`export_html` HTML-escapes everything. This matches the scope
`docs/memory/IMPLEMENTATION_PLAN.md:364` explicitly states ("injection scan
on fact content"), so it reads as intentional scoping rather than drift —
flagged because it's a real, traceable gap if `category`/`tags` are ever
surfaced somewhere less defensive later.

Suggested fix: extend `scan_content`/`enforce_content` to cover `category`
and `tags` at apply time for defense-in-depth, or explicitly document the
exclusion in `docs/memory/IMPLEMENTATION_PLAN.md` so it reads as a decision,
not an omission.

### F-07 (Low, doc drift): `CLAUDE.md`'s harness guarded-route count is stale

`CLAUDE.md` states "the harness console's 28 `guarded` routes." The actual
count in `harness/server.py` is 29 (`grep -c "dependencies=guarded"
harness/server.py` → 29), matching `tests/test_harness_auth.py`'s own
`GUARDED` parametrize list (29 `(method, path, body)` tuples). That test
file's own comment already documents that `GET`/`POST /api/keys` were
"guarded in `harness/server.py` from the start but absent from this list" —
i.e. this is a previously-identified gap whose fix (adding the two
`/api/keys` test cases) wasn't accompanied by bumping `CLAUDE.md`'s stated
count. The code is *more* protected than documented, not less — no security
exposure, just a stale number.

Relatedly, `docs/HARNESS_POWERSHELL.md`'s "Security posture" section (which
`docs/HARNESS_MACOS.md` explicitly mirrors) never mentions `/api/keys*` in
its guarded-route enumeration even though both routes are guarded in code,
and its phrase "the two reads that leak more than a summary" undercounts —
the guarded GET set is actually five routes (`GET /api/memory`,
`GET /api/sessions/{id}`, `GET /api/keys`, `GET /api/github/status`,
`GET /api/agent/runs/{run_id}`), not two.

Suggested fix: bump `CLAUDE.md`'s count to 29 (ideally derived from
`tests/test_harness_auth.py::GUARDED`'s length so it can't drift again), and
update both `docs/HARNESS_POWERSHELL.md` and `docs/HARNESS_MACOS.md`'s
security-posture sections to list `/api/keys*` and name all five guarded GET
routes (or drop the specific count and reference `harness/README.md`'s route
table instead).

### F-08 (Low, doc drift): two dead/inaccurate spots in the memory docs

1. `docs/memory/IMPLEMENTATION_PLAN.md:301-307` specifies an external-content
   FTS5 table (`content='facts', content_rowid='id'`), but the shipped
   schema (`memory/store.py:168-173`) is a standalone FTS5 table synced via
   explicit `AFTER INSERT/UPDATE/DELETE` triggers (`store.py:175-195`), with
   an added `tokenize = 'porter'` the plan doesn't mention. Functionally
   sound and exercised by `memory/selftest.py`/`tests/test_memory_store.py`;
   the plan itself says "Authority: running code > config.yaml > this doc,"
   so this is informational drift, not a defect.
2. `retrieval_fusion.min_fts_score` (`config.yaml:214`, referenced in
   `memory/selftest.py:28`) is never read anywhere in
   `memory/retrieval_adapter.py::fuse_memory_hits` or
   `retrieval/hybrid_search.py`. An operator tuning it expecting it to
   filter weak BM25 matches out of fusion gets no effect.

Suggested fix: update `IMPLEMENTATION_PLAN.md`'s FTS5 schema section to
match the shipped trigger-synced design, and either wire `min_fts_score`
into `fuse_memory_hits`'s filtering or remove the dead config key and its
doc/selftest references.

### Informational (no action required, or already-accepted tradeoffs)

These were traced and confirmed but don't warrant a standalone fix:

- **Auth**: `GET /auth/setup-status` is the one `/auth/*` route without an
  `_enforce_same_origin` dependency (`gate_auth.py:336`) — read-only,
  discloses only `{needs_password, username}` where `username` is always the
  hardcoded `"admin"`, so no real confidentiality/CSRF impact.
- **Auth**: `utils/gen_cert.py`'s `--hostname` isn't sanitized before
  embedding into `-subj`/SAN — a local, operator-invoked CLI tool with no
  remote/untrusted input path, so not a boundary crossing.
- **Auth**: device tokens never expire (`authn_manager.py:495-496`) — an
  explicitly accepted GitHub/GitLab-PAT-style tradeoff, revocable but
  unbounded lifetime.
- **Auth doc**: `CLAUDE.md`'s route table for `/auth/users`,
  `/auth/audit/summary` lists auth as "session" without noting bearer-token
  support, though the code (`gate_auth.py:591,733`) accepts either; the
  outcome is unaffected since role checks apply regardless of credential
  type.
- **Auth doc**: `docs/AUTHENTICATION_DESIGN.md` §4.3 documents `/query`'s
  missing CSRF token but not its same-site check
  (`gate.py::_reject_cross_site_query`) — incomplete, not incorrect, and the
  gap is in the safe direction.
- **Memory**: store-layer functions (`insert_fact`, `apply_proposal`, etc.)
  don't independently re-check `memory.enabled` — all three current call
  sites (`gate_memory.py`, `graph.py`, `hybrid_search.py`) do gate correctly
  today, so this is a fragility for a future call site to watch for, not a
  live bug.
- **Memory**: DB-file `os.chmod` hardening (`memory/store.py:89-94`) only
  logs a warning on failure rather than refusing to proceed — low likelihood
  in the single-operator threat model.
- **Memory**: aggressively lowering `retrieval_fusion.rrf_k` could in theory
  push a memory-only hit above `retrieval.min_score` on its own, letting an
  approved fact substitute for corpus evidence in the I3 routing decision —
  not an external-attacker vector since facts require the propose/apply gate
  to exist at all, just a config-interaction worth knowing before retuning
  `rrf_k`.
- **Harness**: `_looks_proxied()` (`server.py:112-123`) is a
  header-presence heuristic for reverse-proxy detection; a proxy forwarding
  no `X-Forwarded-*`/`Forwarded`/`X-Real-IP` header wouldn't be detected.
  This mirrors `gate.py`'s identical mechanism and only matters if an
  operator has already opted into `api_key_optional`, which ships `false`.
- **Console**: `gate.py`'s CSP includes `style-src 'self' 'unsafe-inline'`
  to accommodate `terminal.html`'s inline `style="..."` attributes
  (documented in-file); cannot execute script on its own, no attribute-
  injection sink found to pair it with.

### Confirmed clean (explicitly checked, nothing found)

- No SQL injection anywhere in `utils/authn_store.py` or `memory/store.py`
  (parameterized queries throughout, including FTS5 `MATCH` — tokens are
  stripped of metacharacters and individually quoted before binding).
  No `shell=True` anywhere in `harness/`, `utils/ops_runner.py`, or
  `agentic/executor/` — every subprocess call is list-form argv.
- No XSS in `terminal.js`, `harness.html`'s inline script, or
  `auth_admin.js` — every render path uses `textContent`/`createElement` or
  the `escHtml()` helper; traced end-to-end from `/query`'s `data.answer`,
  corpus filenames, and LLM chat replies through to their sinks. `escHtml()`
  is text-context-safe only — it leaves quotes unescaped and its own comment
  (`static/terminal.js:1564-1569`) warns against using it in an attribute —
  and every current use is an element-text interpolation, never an attribute.
- No CWE-1022 (reverse tabnabbing) — neither console contains a
  `target="_blank"` or `window.open()` call at all.
- No `eval()`/`new Function()`/string-form `setTimeout` anywhere in either
  console.
- No hardcoded secrets in either console's source.
- No CSRF gap on any mutating fetch in either console; `/query`'s
  documented CSRF exemption is backed by `SameSite=Strict` plus an explicit
  same-site check, not bare trust.
- No `postMessage` handlers in either console.
- I6 module isolation holds in both directions for `harness/` — confirmed by
  grep. `memory/` is not one of the six modules I6 names (it's an optional
  core feature, not an out-of-band subsystem — `graph.py:908` and
  `retrieval/hybrid_search.py:310` both lazily import it on enabled paths),
  so "I6" doesn't apply to it; what holds instead is a narrower lazy-import
  boundary, confirmed by grep plus `tests/test_memory_isolation.py`'s
  AST-based check (deferred imports into the core six, a restricted reverse
  dependency set).
- The harness console's `/web` allowlist is genuinely default-deny (DNS
  re-resolution + `is_global` check at both allow-time and fetch-time, no
  redirect-following, no substring/subdomain bypass) and
  `harness/env_keys.py` never returns a raw secret value, only presence and
  a masked tail.

## Next Steps

1. F-01 and F-02 (both Medium, both live-confirmed, both on the harness
   console's static-serving path) are good candidates for a single focused
   PR — same root area, same file (`harness/server.py`'s `create_app()`),
   reviewable together.
2. F-03 (memory facts-enabled semantics) needs a product decision — rename
   the flag vs. change its enforcement — before a fix PR, since either
   choice changes documented/expected behavior for anyone already using
   propose/apply.
3. F-04 through F-08 are independent, narrow, low-risk fixes each
   reviewable as its own small PR or grouped as a "harness/memory hygiene"
   batch.
4. None of the six invariants (`CLAUDE.md` §3) or I6 module isolation are
   implicated by any finding above — no graph edge, auth gate, or import
   structure change is required by any suggested fix.

---

*Scope note: this doc was written and pushed onto
`claude/cyclaw-optimize-scanner-config-sync` (PR #1118) at the requester's
explicit direction, alongside that PR's original, unrelated
`.trivyignore`/`.osv-scanner.toml` CVE-sync change. It is a documentation-only
addition (no code touched) and is called out separately in the PR thread so
it reviews as its own concern rather than being read as part of the CVE-sync
diff.*
