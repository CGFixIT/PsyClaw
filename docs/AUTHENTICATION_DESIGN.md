---
title: "CyClaw Authentication — Design"
date: 2026-08-08
status: landed
tags: [security, authentication, tls, threat-model, lan]
related:
  - docs/THREAT_MODEL.md
  - .github/SECURITY.md
  - config.yaml
---

# CyClaw Authentication — Design

**Status: all six stages landed.** Stage 1 (`utils/authn.py`, PR #829),
Stage 2 (sessions, login/logout, per-device tokens, `gate_auth.py`,
PR #830), Stage 3 (credential on `/query` + console login when
`auth.enabled` is true, PR #940), Stage 4 (TLS wired into `uvicorn.run` via
`gate._serve`, `cyclaw-gen-cert`, PR #940), Stage 5 (re-keying the #825
bind guard, landed as part of #825 itself), and Stage 6 (roles
`admin`/`operator`/`audit`, HTTP user admin, web Users panel + audit tab,
PR #940) have landed. `auth.enabled` and `api.tls.enabled` both still
ship `false`, so no existing install's behavior changed. Enabling TLS
without real cert files fails closed at boot instead of marking cookies
Secure on plaintext.

This document exists because the operator wrote the requirement down first, in
`docs/zIdeas/note.txt`:

> "Dont forget that curl requests or powershell api commands can still query
> cyclaw if on same lan - need to add authentication before truly considering
> this secure"

and then set the bar for answering it: **true authentication, or address it
later — no shortcuts.** The intent is to eventually let other machines on the
LAN reach CyClaw. This design is written to that bar.

---

## 1. Scope

**In scope.** Per-user authentication for the CyClaw gateway
(`gate.py`, `127.0.0.1:8787`) sufficient to allow non-loopback binding safely:
individual accounts, password verification, sessions for browsers, revocable
tokens for programmatic clients, lockout, audit attribution, and TLS so a
credential is not readable on the wire.

**Out of scope, deliberately.**

- **Multi-tenancy.** Accounts give *identity and revocation*, not isolation.
  Every authenticated user still reaches the same corpus, the same soul, and the
  same model. `docs/THREAT_MODEL.md` §1 stays single-tenant, and this document
  does not change that.
- **MCP server.** Separate app, stdio, `sampling: None`. Untouched.
- **The harness (`:8790`).** Still loopback-only with its own API-key +
  origin + CSRF chain on coding routes. Stage 6 shares the **same users
  table** and a separate `cyclaw_harness_session` cookie. It is not a LAN
  app.
- **Authorization / roles.** **Amended (Stage 6).** Accounts now carry a
  `role` of `admin`, `operator`, or `audit`. This is authorization on the
  same single-tenant corpus, not multi-tenancy. See §12.
- **Federation, SSO, OAuth, MFA.** Not now. §11 notes where MFA would attach if
  it is ever wanted.

---

## 2. What exists today (verified, not assumed)

| Fact | Evidence |
|---|---|
| `/query`, `/`, `/health`, `/static/*` require **no** authentication | route introspection of `gate.py` |
| `/soul/*`, `/audit/summary`, `/ops/*` require a **single shared** bearer secret | `require_api_key`, `gate.py:96` |
| The secret is compared in constant time and **fails closed** when unset | `hmac.compare_digest`, `gate.py:117`; unset `CYCLAW_API_KEY` → 401 |
| Failed auth is **already rate-limited** | `dependencies=[Depends(_enforce_rate_limit), Depends(require_api_key)]` — limiter runs *before* auth, pinned by `TestFailedAuthDoesNotBypassRateLimit` |
| The console **already sends** a bearer token on every call, including `/query` | `static/terminal.js` `authHeaders()` |
| Telegram **already sends** one to `/query` | `telegram/client.py:745`; config field documented *"Optional CyClaw API key for POST /query"* |
| No password, session, or TLS infrastructure exists | no argon2/bcrypt/passlib/itsdangerous/`SessionMiddleware`/`ssl_keyfile` anywhere |

**Amendment (2026-08-09, Stages 1-2 landed; amended 2026-08-15, Stages 3-4
and 6 landed, PR #940).** The table above reflects the state at proposal
time. Several rows are now stale. Password, session, AND TLS infrastructure
all exist: `utils/authn.py` (Stage 1, PR #829), the account/session/token
store (Stage 2, PR #830), and `gate._serve` passing
`ssl_certfile`/`ssl_keyfile` to `uvicorn.run` when `api.tls.enabled` is the
literal boolean true (Stage 4, PR #940 — missing or unreadable files fail
closed at boot). `/auth/login`, `/auth/logout`, and `/auth/whoami` also now
exist and are registered regardless of `auth.enabled` (returning 503 when
it is false, matching `gate_ops.py`'s convention for `/ops/*`) — so the
unauthenticated-route row above is stale too: `/auth/login` carries no
authentication either, by necessity (a caller has no credential yet to
present), gated only by the standard rate limit and the same-origin check.
Stage 3 attaches `require_session_or_token` to `POST /query` when
`auth.enabled` is the literal boolean `true` (session cookie or device
token; no CSRF on the query path). The shipped default still leaves
`/query` open. The console row is stale as well: `terminal.js` no longer
sends the typed API key on `/query` at all — once auth is on, `/query`
authenticates with the `cyclaw_session` cookie (or a named device token
for programmatic clients), and the key field serves `/soul/*` and `/ops/*`
only. The Telegram row stays accurate in form — it still sends a bearer
token to `/query` — but the token must now be a named device token, not
the shared `CYCLAW_API_KEY`.

**Amendment (2026-08-15).** A second, independent escape hatch landed
alongside the session/RBAC system this document specifies:
`config.yaml`'s `security.api_key_optional` (default `false`). It bypasses
`require_api_key` — the shared-secret mechanism row 2 above describes, in
both its `gate.py` and `harness/server.py` (`utils/auth.py`) copies — for
every route it gates, not just `/soul/*`/`/ops/*`/`/audit/summary` but also
the harness console's 26 `guarded` routes. It is orthogonal to everything in
this document: it does not touch `auth.enabled`, sessions, device tokens, or
`/auth/*`, and an operator can run with `auth.enabled: true` (this design)
and `api_key_optional: true` (bypassing the older mechanism) at the same
time without either one affecting the other. The bypass is granted only to
a **loopback socket peer**, so it never widens what a remote caller can
reach; `config-guard`'s C13 check warns when it is combined with a
non-loopback `api.host`. (C13 deliberately ignores `security.allowed_hosts`:
that list filters `Host` headers and opens no listening socket.)

One place the two systems **do** interact, and must not: §7's rule below
("a non-loopback bind is allowed when authentication is enabled and TLS is
enabled") is satisfied by `auth.enabled` + `api.tls.enabled` + a real
`/query` credential — all properties of the SESSION system. None of them
say anything about the API-key routes `api_key_optional` opens. Composed,
they would admit a LAN bind on the strength of a session while `/soul/*`
and the `/ops/*` subprocess shims sit unauthenticated. `_require_loopback_bind`
therefore refuses that route while `api_key_optional` is true; the
`CYCLAW_ALLOW_NON_LOOPBACK_BIND` env override (an explicit operator
statement of "my own auth is in front") still outranks it.

That bind-time refusal is defence in depth, not the primary control. The
primary one is per-request: the bypass requires a loopback peer, which holds
regardless of how the process was launched — including the container's
`uvicorn gate:app --host 0.0.0.0` and `uvicorn harness.server:app`, neither
of which reaches a bind guard at all.

Two consequences worth stating plainly.

**The transport is already built.** Both real clients speak `Authorization:
Bearer`. Requiring credentials on `/query` is not a new protocol — it is turning
on a check the clients already satisfy.

**What is missing is identity, not authentication.** A single shared secret *is*
authentication in the "something you know" sense. What it cannot do is say
*which* machine asked, or let one device be revoked without rotating the
credential for every other device. For a LAN with several machines, that is the
gap that matters, and it is why this design is per-user rather than a wider
allow-list on the existing key.

---

## 3. What changes in the threat model

`docs/THREAT_MODEL.md` §1 currently assumes network exposure is *exclusively*
`127.0.0.1:8787`. Allowing a LAN bind changes the adversary from "a local
process on the operator's own machine" to "anything that can route to the port,
plus anything that can observe the segment." Concretely, three new adversaries:

1. **Another device on the LAN** — an IoT device, a guest phone, a compromised
   laptop. It can reach the port and attempt credentials.
2. **A passive observer of the segment** — anyone who can sniff wireless or a
   mirrored port. This is the adversary TLS exists for, and the reason a login
   form over plain HTTP would be theatre.
3. **A device that was trusted and no longer should be** — a lost laptop, a
   departed housemate. This is the adversary *revocation* exists for, and the
   one a shared secret cannot answer.

Everything else in the threat model is unchanged. Host root is still trusted.
The agentic layer is still out-of-band and default-off. This design does not
make CyClaw safe to expose to the internet, and says so in §11.

---

## 4. Scheme

### 4.1 Password hashing — `hashlib.scrypt`, not argon2

**stdlib, no new runtime dependency.** `hashlib.scrypt` (RFC 7914) is
memory-hard, ships with CPython against OpenSSL 1.1+, and is verified working in
this environment at n=2¹⁴, r=8, p=1 → ~0.11 s per verification, which is the
right order for an interactive login and expensive enough to make offline
cracking costly.

This was chosen over argon2id specifically because `argon2-cffi` would be a new
runtime dependency, which CLAUDE.md §7 classifies High-tier and which would need
exact pins in both `pyproject.toml` and `constraints.txt` plus a `dep-guard`
pass. Stdlib-first is this repo's stated convention. argon2id is the stronger
primitive in the abstract; scrypt at these parameters is not the weak link in
this system, and the dependency cost is real.

Stored per user: `scrypt$n$r$p$<salt_b64>$<hash_b64>`. The parameters live in the
record so they can be raised later without invalidating existing accounts — a
verification that succeeds against outdated parameters transparently re-hashes.

### 4.2 Account store

Follows the same pattern as `utils/personality_db.py`: **SQLite by default**,
Postgres via either `auth.database_url` in `config.yaml` or the
`CYCLAW_AUTH_DB_URL` env var, `CREATE TABLE IF NOT EXISTS`, umask-safe file
creation. No new storage technology. One deliberate deviation from an exact
mirror: the env var is `CYCLAW_AUTH_DB_URL`, not the shared `CYCLAW_DB_URL`
personality uses — auth data (password hashes, session ids, device-token
hashes) is higher-sensitivity, and a shared env var would silently comingle
the two into whatever database an operator pointed `CYCLAW_DB_URL` at for a
different subsystem.

```
users(username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
      created_ts REAL, disabled INTEGER DEFAULT 0,
      last_login_ts REAL, failed_count INTEGER DEFAULT 0,
      locked_until_ts REAL, role TEXT NOT NULL DEFAULT 'operator')
sessions(session_id TEXT PRIMARY KEY, username TEXT NOT NULL,
         created_ts REAL, expires_ts REAL, revoked INTEGER DEFAULT 0,
         label TEXT)
device_tokens(token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
              label TEXT NOT NULL, created_ts REAL, last_used_ts REAL,
              revoked INTEGER DEFAULT 0)
```

(`role` arrived with Stage 6; `ensure_users_role_column` migrates pre-Stage-6
databases in place, promoting `BOOTSTRAP_USERNAME` to `admin` and defaulting
every other existing row to `operator`. The `sessions` block above is the
proposal-time sketch — the shipped table also carries `csrf_token` and
`last_seen_ts`, and no `label`; see `utils/authn_store.py` for the DDL of
record.)

Sessions are **server-side records**, not signed self-contained cookies. That is
the deliberate choice: a server-side row can be revoked instantly, which is the
whole point of per-user accounts. A stateless signed token cannot be withdrawn
before it expires without a revocation list, which is a session table wearing a
disguise.

### 4.3 Two credential paths, one identity

| Client | Credential | Why |
|---|---|---|
| Browser (`terminal.html`) | Session cookie (`HttpOnly`, `Secure`, `SameSite=Strict`) + CSRF token | A cookie is not readable by injected script; CSRF covers the state-changing routes |
| Programmatic (Telegram, smoke tests, `curl`, PowerShell) | `Authorization: Bearer <token>` | Already implemented on both real clients; no cookie jar needed |

Both resolve to a **username**, so the audit log can attribute every query. The
bearer path issues *named, per-device, individually revocable* tokens stored as
hashes — not the current single shared key. The existing `CYCLAW_API_KEY`
continues to govern `/soul/*` and `/ops/*` unchanged, so this is additive.

CSRF reuses the harness's proven pattern (`harness/server.py`:
`secrets.token_urlsafe(32)`, `hmac.compare_digest`) rather than a new
mechanism — with one deliberate deviation: the gate's token is **per
session**, minted at login and returned in the `/auth/login` response body,
then echoed back by the browser in the `X-CyClaw-CSRF` header on
state-changing routes. (The harness's own variant stores its token in a
`<meta>` tag; that is the harness surface, not the gate's.)

### 4.4 Lockout

Per-account exponential backoff on consecutive failures, recorded in
`failed_count`/`locked_until_ts`, cleared on success. This is *in addition to*
the existing per-IP rate limiter, which already runs before auth — the limiter
throttles a single source, lockout stops a distributed guess against one
account. Lockout is per-account, not per-IP, precisely because the LAN case
means many source addresses.

### 4.5 Ships disabled

`auth.enabled: false` by default, matching every other CyClaw subsystem
(`agentic`, `telegram`, `guardrails`, `fsconnect`). Nothing changes for an
existing loopback install until the operator turns it on. When enabled,
`/query` requires a credential **including on loopback** — a bypass for
`127.0.0.1` would be exactly the shortcut this design was asked to avoid, and
local processes are inside the stated adversary set. The smoke tests and
`CyClaw-Sandbox` get a token like any other programmatic client.

---

## 5. TLS

Without it, the session cookie and bearer token cross the LAN in plaintext and
adversary (2) in §3 simply reads them. TLS is therefore part of the feature, not
an enhancement.

- `api.tls.enabled`, `api.tls.certfile`, `api.tls.keyfile` in `config.yaml`;
  passed to `uvicorn.run(ssl_certfile=..., ssl_keyfile=...)`.
- A `cyclaw-gen-cert` helper producing a self-signed cert with the machine's
  LAN IP and hostname in `subjectAltName` (browsers reject CN-only certs). The
  operator installs it as trusted on each client device once.
- The session cookie is issued `Secure` when TLS is on. **`Secure` is not sent
  over plain HTTP**, so enabling auth without TLS on a non-loopback bind must be
  refused rather than silently downgrading the cookie — see §7.
- `security.allowed_origins` gains the `https://` forms; the console's CSP
  `connect-src` follows.

Self-signed is the right default for a home LAN: no external CA, no renewal
daemon, no internet dependency, consistent with CyClaw's offline-first posture.

---

## 6. Migration for existing clients

| Client | Change |
|---|---|
| `static/terminal.js` | Login form; session cookie replaces the typed key for `/query`. The existing API-key field stays for `/soul/*` and `/ops/*`. |
| `telegram/client.py` | None to the code — it already sends a bearer token. The operator issues it a named token instead of the shared key. |
| `tests/ci_rag_smoke.py`, `CyClaw-Sandbox` | Only affected when `auth.enabled` is true. CI leaves it false; a token is provided where a job needs it on. |
| MCP server | None. Separate app, no `/query`. |
| `utils/health.py` / `/health` | Stays unauthenticated — it exposes no corpus content and the container healthcheck depends on it. Revisit if it ever reports more. |

---

## 7. Interaction with the `main()` bind guard (#825)

PR #825 refuses a non-loopback `api.host` unless
`CYCLAW_ALLOW_NON_LOOPBACK_BIND=1`. That guard was originally keyed on the
wrong condition: it refused **because there is no auth**. #825's 2026-08-08
update re-keyed it to this design (Stage 5, §8), and the rule as actually
implemented in `gate.py`'s `_require_loopback_bind`/`_auth_and_tls_enabled` is

> a non-loopback bind is allowed when authentication is enabled **and** TLS is
> enabled **and** `/query` demonstrably enforces a credential; otherwise
> refuse.

The third condition is load-bearing, not optional. The two config flags are a
statement of *intent*; an operator who sets `auth.enabled: true` reasonably
believes authentication is on. `gate.py`'s
`_request_path_enforcement_active()` probes the live FastAPI app for the
`/query` credential dependency rather than trusting the flag. Stage 3
(PR #940) attaches that dependency when `auth.enabled` is the literal
boolean `true`, so the path past loopback opens by itself once both flags
are on — no further edit to the guard is required. The shipped default
(auth off) still leaves the probe false. This is a strictly better gate
than the env-var-only guard: it permits the operator's goal (LAN + TLS +
auth) and refuses the genuinely unsafe case (LAN + no auth, LAN + auth
claimed but not enforced). With Stage 4 landed in the same PR, the guard's
TLS half is now real too: `gate._serve` passes
`ssl_certfile`/`ssl_keyfile` to `uvicorn.run` when `api.tls.enabled` is the
literal boolean true, and missing or unreadable cert files refuse to boot
rather than falling back to plaintext. The residual gap is narrower than
the one this section previously documented: the Docker `CMD` and a hand-run
`uvicorn gate:app` bypass `_serve` entirely, so the guard's proof covers
the documented entry points (`python gate.py`, `cyclaw-server`) only — see
`docs/THREAT_MODEL.md`'s ninth amendment §5 for the precise scope.
`CYCLAW_ALLOW_NON_LOOPBACK_BIND` is retained only as a
deliberate escape hatch for someone fronting CyClaw with their own reverse
proxy. See `docs/THREAT_MODEL.md`'s ninth amendment for the fully verified,
currently-accurate statement of this rule.

---

## 8. Staged delivery

Each stage is independently reviewable and leaves the tree working.

| Stage | Content | Risk |
|---|---|---|
| **1** — landed, PR #829 | This document + `utils/authn.py` (scrypt hash/verify, lockout arithmetic) + tests. Pure functions only — no database, no HTTP, no CLI. **No request path touched.** | None at merge time; Stage 2 (below) is now the caller |
| **2** — landed, PR #830 | Account store (`utils/authn_store.py`), `AuthManager` (`utils/authn_manager.py`), `cyclaw-user` CLI (`add`/`list`/`disable`/`enable`/`passwd`/`token`), session store, `/auth/login`, `/auth/logout`, `/auth/whoami`, cookie issuance, CSRF, per-device bearer tokens | None while `auth.enabled: false` (ships false, unchanged) |
| **3** — landed, PR #940 | Enforce on `/query` and the console; audit log gains a `username` field | Behaviour change, gated by `auth.enabled` |
| **4** — landed, PR #940 | TLS config, `cyclaw-gen-cert`, origin/CSP updates, docs | Config surface only |
| **5** — landed, PR #825 | Re-key the #825 bind guard per §7; update `THREAT_MODEL.md` §1 and add an amendment | Docs + one condition (implemented as three — see §7) |
| **6** — landed, PR #940 | Roles (`admin`/`operator`/`audit`), HTTP user admin, shared web Users panel, audit tab | Gated by `auth.enabled` |

---

## 9. What could go wrong

- **Lockout as a denial-of-service.** An attacker who knows a username can lock
  it by failing repeatedly. Mitigated by exponential backoff with a ceiling
  rather than a permanent lock, and by the operator always retaining a local
  CLI path (`cyclaw-user`) that does not go through HTTP.
- **Self-signed certificate fatigue.** Browsers warn loudly, and operators learn
  to click through warnings — which trains exactly the wrong reflex. The
  `subjectAltName` + install-once-per-device flow exists to avoid a permanent
  warning state; if it is not followed, TLS degrades toward theatre.
- **Session fixation / theft.** Session id is `secrets.token_urlsafe(32)`,
  regenerated on login, `HttpOnly`, `SameSite=Strict`, and server-side
  revocable. XSS remains the realistic theft vector, which is why the strict CSP
  work in #818/#822 is load-bearing for this feature rather than incidental.
- **This does not make CyClaw internet-safe.** It makes a *trusted LAN* defensible.
  Internet exposure would additionally require rethinking rate limits, the
  unauthenticated `/health`, DoS budgets, and the chromadb CVE risk acceptance
  that is currently justified by local-only use.

---

## 10. Open decisions for the operator

1. **Username set.** Single account (`operator`), or one per person? One per
   device is handled by named bearer tokens regardless.
2. **Session lifetime.** **Resolved (2026-08-08):** 12 h idle / 7 d absolute,
   both configurable (`auth.session.idle_timeout_sec` /
   `absolute_timeout_sec` in `config.yaml`, 43200 / 604800 shipped). A session
   dies from either limit, whichever is reached first.
3. **Should `/health` stay open?** **Resolved: yes** (§6). It reports status,
   mode, and timeouts — no corpus content — and stays unauthenticated even
   with `auth.enabled: true`.
4. **Bootstrap.** Proposed: `cyclaw-user add` refuses to run over HTTP and must
   be run locally, so there is never a window where a default credential exists.
   No default account is ever created.
   **Resolved (2026-08-08, amended 2026-08-09):** first enable with an empty
   users table creates `admin` seeded with the hash of a random secret that is
   discarded inside the same call — never returned, printed, logged, or stored
   in plaintext — so the account exists but cannot be logged into until
   `cyclaw-user passwd admin` sets a real password locally via `getpass`.
   The first iteration of this decision printed a generated one-time password
   to the server console instead; CodeQL flagged it (alert #1057), and the
   objection is substantive, not pedantic: a service's stdout is persisted by
   systemd's journal, Docker's log driver, and any log shipper, so
   "printed once" was never really once. The discard design keeps this
   decision's actual requirement — no fixed default credential, ever — while
   removing every output channel a credential could reach.

---

## 11. Where MFA would attach, if ever wanted

A TOTP secret column on `users` and a second step between password verification
and session issuance. Deliberately not built now — it is the wrong thing to add
before per-user accounts and TLS exist, and adding it later requires no
redesign.

---

## 12. Roles (Stage 6)

Three canonical lowercase roles on `users.role`. Bootstrap `admin` is
`admin`. Existing rows without a column become `operator` except
`BOOTSTRAP_USERNAME`.

| Role | `/query` | Users panel | Audit tab | delete / set-role |
|---|---|---|---|---|
| `admin` | yes | yes | yes | yes (not the last enabled admin) |
| `operator` | yes | yes (no delete / set-role / touch admins) | no | no |
| `audit` | **denied** | no | yes | no |

`HIGH_PRIVILEGE` in `utils/authn.py` is the hook for later destructive
ops. HTTP admin lives on `gate_auth.py` (`/auth/users*`,
`/auth/audit/summary`, plus the self-service `POST /auth/password` any
authenticated role can call for its own account); `/auth/whoami` returns
`username` + `role`. The harness exposes the same store at `/api/auth/*`
with a separate cookie. Telegram still uses a named
device token (`cyclaw-user token create <user> telegram`).
