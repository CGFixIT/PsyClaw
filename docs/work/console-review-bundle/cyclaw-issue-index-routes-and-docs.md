<!--
Corrected 2026-08-28 against main @ 8a534ab.
The original draft's two headline items (H1, H3) were verified to be ALREADY
IMPLEMENTED and ALREADY DOCUMENTED. They are retained below under "Closed on
verification" with the file:line that closes each, so nobody re-opens them.
What replaces them is a finding the original review missed -- and it is the
inversion of what H1 assumed.
Suggested labels: security, docs, console
FILED AS: https://github.com/cgfixit/CyClaw/issues/1201
-->

# Gate hardening: `/query`'s cross-site check is conditional on `auth.enabled`; `/index/status` limiter posture; THREAT_MODEL control rows

**Labels:** `security` `docs` `console`

---

## Summary

A console security review (2026-08-28) originally proposed adding an origin check to
`POST /index/build` on the theory that `/query` had one and `/index/build` did not.
Verification against `main` inverted that: **`/index/build` carries the check
unconditionally, and `/query` carries it only when `auth.enabled` is true — which is not
the shipped default.** Two smaller items survive alongside it. Nothing here weakens an
invariant; each item adds a check, settles a posture, or fixes documentation.

## H1 — `POST /query`'s cross-site check is not attached in the shipped configuration

`_reject_cross_site_query` (`gate.py:271-286`) delegates to `_looks_cross_site` and returns
403 `CROSS_SITE_BLOCKED`. It is **not** declared in the `@app.post("/query", ...)`
decorator and is **not** middleware. It is attached dynamically at import time, and only
conditionally (`gate.py:1237-1246`):

```python
if auth_manager is not None:
    attach_identity_to_query(app, require_identity)
    attach_identity_to_query(app, _reject_cross_site_query)
```

`auth_manager` is None when `auth.enabled` is false, and `config.yaml:518` ships
`auth: enabled: false`. **On a default install, `POST /query` has no cross-site
protection.**

### Severity: defence-in-depth, not an exploitable hole — verified

Two other layers independently stop the browser attack today, so this is a consistency
and defence-in-depth gap rather than a live vulnerability. Both were tested, not assumed:

1. **The body cannot be crafted with a CORS-"simple" content-type.** `QueryRequest` is
   `extra='forbid', strict=True`. Posting a valid JSON body with `text/plain`,
   `text/plain;charset=UTF-8`, `application/x-www-form-urlencoded`, or
   `multipart/form-data` returns **422** in every case — only `application/json` parses.
   That rules out the cross-site HTML form vector entirely, since forms cannot send
   `application/json`.
2. **`application/json` forces a preflight, and CORS refuses it.** `gate.py:477-483`
   installs `CORSMiddleware` with `allow_origins` from `security.allowed_origins`
   (`config.yaml:663+` — a loopback/LAN allow-list, **not** `*`) and
   `allow_credentials=False`. A preflight from an origin outside that list gets no
   `Access-Control-Allow-Origin`, so the browser never sends the real request.

So a hostile public page **cannot** currently reach `/query`, with or without
`_reject_cross_site_query`. Do not describe this as budget-burnable or as a triple-gate
bypass.

**Why fix it anyway.** The asymmetry is the problem. `POST /index/build` takes a bare
`{ method: 'POST' }` with no body and no `Content-Type` — a genuinely CORS-simple
request — so `_looks_cross_site` there is load-bearing and correctly unconditional.
`/query` is protected only because its body requirement happens to force a preflight. That
is incidental, not designed: it evaporates the day someone widens `allowed_origins` to `*`
for a demo, or a future route variant accepts a form encoding. `/query` is the one
state-reading route with no independent check of its own, and attaching one costs nothing.

**Decide and document the intended posture.** Either is defensible; leaving it implicit
is not:
- attach `_reject_cross_site_query` unconditionally (independent of `auth.enabled`), or
- accept it deliberately and record in `docs/THREAT_MODEL.md` that `/query`'s cross-site
  posture rests on the CORS allow-list plus JSON-only body parsing, not on its own check.

**Acceptance criteria.**
- A test exercising `/query`'s cross-site posture with `auth.enabled` **false**. Today
  every case in `tests/test_gate_query_auth.py::TestQueryCrossSiteRejected` (`:153,162,171,181,197`)
  uses the auth-enabled fixtures, so the shipped default is untested either way.
- Whichever branch is taken, `docs/THREAT_MODEL.md` names it.
- Header semantics unchanged: `_looks_cross_site` (`gate.py:1276-1311`) reads
  `sec-fetch-site` and `origin`, never `Referer`, and **allows** a request carrying
  neither — curl, TestClient, PowerShell and schedulers must stay unaffected.

## H2 — Decide `GET /index/status` vs the 60 req/min limiter

The console polls every 1.5s during a build (`INDEX_POLL_MS = 1500`, `static/terminal.js:342`)
= **40 req/min**, against `RATE_LIMIT_REQUESTS = 60` per `RATE_LIMIT_WINDOW = 60`s
(`gate.py:198-199`). `GET /index/status` **is** rate-limited (`gate.py:913`). Pick one:

- exempt `GET /index/status` from the limiter — a read-only, unauthenticated-by-design
  progress counter; or
- keep the limiter and raise `INDEX_POLL_MS` to 3000.

Don't do both.

**Correction to the original draft:** it claimed the budget is "shared with the 15s
`/health` poll." It is not — **`GET /health` is not rate-limited** (`gate.py:1164` carries
no `dependencies=` clause). The remaining 20 req/min is shared with operator traffic only.
The squeeze is real; it is smaller than first stated.

Note there is no exemption list to add to: the limiter is a per-route FastAPI dependency
(`Depends(_enforce_rate_limit)`), never global middleware, so "exempt" means removing that
dependency from the route and saying why in a comment. Sequence this against open
PR #1173, which is reworking `_enforce_rate_limit`'s audit path.

The client side is already fixed independently: `static/terminal.js`'s `pollIndexStatus`
now guards on `resp.ok`, so a 429 no longer renders a false "build failed". That guard
makes a limiter hit **survivable**, not **desirable**.

**Acceptance criteria.** A documented decision plus a test for whichever branch: a
limiter-exemption test, or the `INDEX_POLL_MS` change with `tests/test_terminal_contract.py`
green.

## H3 — `docs/THREAT_MODEL.md` control-table rows

`docs/THREAT_MODEL.md`'s control table (`| Threat | Primary control | Where |`, `:50-63`)
has **no row** for either index route — zero hits for `index/build` or `index/status` in
the whole file. Its ninth amendment (`:961-1025`) is the only place `_looks_cross_site` is
discussed in depth, and that amendment is scoped to `security.api_key_optional`'s bypass on
`/soul/*`, `/ops/*`, `/memory/*`, `/audit/summary` and the harness console.

Add rows for:
- `POST /index/build` — loopback socket peer + same-origin + rate-limited + audited.
- `GET /index/status` — unauthenticated read-only progress, with whatever H2 lands on.
- `POST /query` — with whatever H1 lands on, **explicitly stating the `auth.enabled`
  conditionality** either way.

**README.md and CLAUDE.md need no change here — do not "fix" them.** See below.

**Acceptance criteria.** `python3 .claude/skills/doc-sync/doc_sync.py` green. Note it
cannot gate this item on its own: its D5 route check reads only CLAUDE.md and
`setup-guide.md`, never README.md.

---

## Closed on verification (do not re-open)

The original draft carried two items that verification found already shipped.

**Origin-check parity on `POST /index/build` — ALREADY IMPLEMENTED.** `gate.py:868-910`
already carries all three controls the draft asked for:

| Control | Location | Behavior |
|---|---|---|
| Loopback socket peer | `gate.py:879-886` | 403 `INDEX_BUILD_LOOPBACK_ONLY`, audited `index_build_rejected` |
| Cross-site (`_looks_cross_site`) | `gate.py:887-892` | 403 `CROSS_SITE_BLOCKED`, audited |
| Single in-flight build | `gate.py:896-902` | 409 `INDEX_BUILD_IN_PROGRESS` |

All three are already tested in `tests/test_gate_index_build.py`: cross-site `:86`,
malformed-Origin fail-closed `:94`, non-loopback peer `:72`, and the 409 concurrent-build
guard `:117`. Every acceptance criterion the draft listed was already met.

**README route/auth-table drift — ALREADY DOCUMENTED.** The draft quoted README as saying
"only `/health`, `/query`, `POST /auth/login`, and the console pages are unauthenticated."
The actual sentence at `README.md:207` already enumerates `GET /index/status` and already
explains `/index/build`'s gating and its 409 behavior in the same detail the code shows.
`CLAUDE.md:90-91` documents both routes correctly too. The draft was written against a
stale snapshot.

## Companion client patch (same review, already landed)

`static/terminal.js` — `pollIndexStatus` called `await resp.json()` with no `resp.ok`
guard, so a JSON-bodied 429/503 parsed fine, `s.state` came back undefined, and the state
machine read that as a failed build while the build kept running server-side. `checkHealth`
(`:552`) already carried that guard and its comment at `:555` claimed "every other fetch in
this file guards on resp.ok" — this poller was the exception. Fixed, with a regression test
(`test_index_status_poll_treats_a_non_ok_response_as_a_dropped_poll`).

## Invariants preserved (explicit)

Adds a check and documentation only. No route gains write capability; the loopback-only
bind is untouched; limiter-first ordering is untouched; every affected route already
converges on the audit logger (I4). H1's "attach unconditionally" branch would *widen* a
protection, never narrow one. H2's exemption branch narrows the limiter's scope to exclude
one read-only probe — call that out in the PR description if taken.

---
_Generated by [Claude Code](https://claude.ai/code)_
