# Console Review Bundle — Reconciliation and Implementation Plan

**Date:** 2026-08-28
**Baseline:** `main` @ `8a534ab`
**Scope:** four artifacts from an offline console security review dated 2026-08-28 —
`F1-harness-api-timeout.patch`, `F3-terminal-pollIndexStatus.patch`,
`cyclaw-issue-index-routes-and-docs.md`, `cyclaw-dep-cve-scan-claude-code.md` — reconciled
against the code they describe, then implemented.
**Corrected artifacts:** `docs/work/console-review-bundle/`.
**Landed as:** PR #1200 (the F3 fix + this record) and issue #1201 (the surviving findings).

---

## Why this doc exists

The four artifacts were produced in a chat container with **no repo checkout and no
egress**. Every one of them says so: F1 flags its hunk offsets as estimates, the issue
ships an explicit "assumptions to verify against current code" checklist, and the CVE
runbook opens by explaining it cannot execute. They were written to be verified before
use, and this doc is that verification plus what followed from it.

The headline result: of the three proposed work items, **one patch survived review
intact**. The other two were each wrong in a way worth recording, because both failure
modes are ones an offline reviewer will hit again.

| Item | Verdict | Outcome |
|---|---|---|
| F1 — harness per-route timeout | Superseded, and would have turned CI red | Not applied; rewritten as a review record |
| F3 — terminal `resp.ok` guard | Correct, exact, real live bug | **Applied** with a regression test |
| Issue H1 / H3 | Already implemented and already documented | Closed on verification |
| Issue H2 | Real, arithmetic overstated | Carried into the corrected issue |
| Issue — new finding | Missed by the original review | Now the corrected issue's lead item |
| CVE runbook | False premise; much (not all) already automated in CI | Rescoped to the genuine gaps |

## What landed as code

One change, `static/terminal.js`'s `pollIndexStatus`: a `resp.ok` guard before the body
parse. The poller called `await resp.json()` unconditionally, so a JSON-bodied non-2xx — a
429 from the front-running rate limiter, a 503, FastAPI's `{"detail": ...}` — parsed fine,
`s.state` came back `undefined`, and the state machine read that as a **failed build**
while the build kept running server-side.

The tree was self-inconsistent about this. `checkHealth` (`static/terminal.js:559`) already
carried the guard, and its own comment asserts *"Every other fetch in this file guards on
`resp.ok`"* — false precisely because of this poller. The fix makes that existing comment
true.

Shipped with a regression test,
`tests/test_terminal_contract.py::test_index_status_poll_treats_a_non_ok_response_as_a_dropped_poll`,
modelled on the sibling `test_check_health_treats_a_non_ok_response_as_unreachable`. It
asserts the guard exists **and** precedes both `await resp.json()` and
`indexBuild.misses = 0` — a guard after either would silently do nothing. Verified to fail
without the fix and pass with it.

The 1.5s poll cadence was deliberately **not** touched: that is a server-side decision, and
the remedy is either/or (see "H2" below).

## Why F1 was not applied

The bug F1 diagnosed is real. `POST /api/agent/run` is deliberately synchronous
(`harness/server.py:1354`) with a per-request budget capped at 3600s
(`utils/ops_runner.py:103,106-131,521-524`), and the console's flat 15s abort fired mid-run
and invited a duplicate concurrent run. Two things killed the patch anyway.

**It would have turned CI red, and its own safety argument missed why.** F1 reasoned
carefully about the contract test's route-extraction parser — `_API_CALL_START_RE =
re.compile(r"\bapi\(")` at `tests/test_harness_console_contract.py:31` — and correctly
concluded that a new `requestTimeoutMs` function is invisible to it. But the same file
carries a second, unrelated assertion: a plain literal-substring check keyed on the value
F1 replaces, at `:475`. The two mechanisms share nothing, and reasoning about one says
nothing about the other. **This is the generalizable lesson: "the parser won't see it" is
not the same claim as "no test asserts on it."**

**Its push/publish timeout had zero margin.** F1 proposed 120000 ms; the server budget for
those routes is `utils/ops_runner.py:53` `_TIMEOUT_SEC = 120` — identical. A client
deadline must sit above its server budget, not on it.

Open draft PR #1194 already fixes the same bug across strictly more routes with correct
margins (`AGENT_RUN_TIMEOUT_MS = 3630000`, `AGENT_CLI_TIMEOUT_MS = 130000`), and updates the
assertion F1 would have broken. Nothing from F1 was worth porting. Full record in
`console-review-bundle/F1-harness-api-timeout.patch`.

## Why the issue's two headline items were closed

**Origin-check parity on `POST /index/build`** was already implemented. `gate.py:868-910`
carries the loopback socket-peer check (`:879-886`), the cross-site check (`:887-892`), and
the 409 single-in-flight guard (`:896-902`) — all three audited, all three already tested in
`tests/test_gate_index_build.py` (`:72`, `:86`, `:94`, `:117`). Every acceptance criterion
the draft listed was already met.

**README route/auth-table drift** was already documented. `README.md:207` already enumerates
`GET /index/status` among the unauthenticated routes and already explains `/index/build`'s
loopback+same-origin gating and its 409 behavior. `CLAUDE.md:90-91` is correct too. The
draft was written against a stale snapshot.

Both are recorded in the corrected issue under "Closed on verification (do not re-open)"
with the file:line that closes each — the cheapest way to stop a future reader
re-discovering them.

## The finding the review missed, and why it is the interesting one

The original H1 assumed `/query` had a cross-site check that `/index/build` lacked.
Verification inverted it exactly: **`/index/build` has the check unconditionally, and
`/query` has it only when `auth.enabled` is true.**

`_reject_cross_site_query` (`gate.py:271-286`) is neither declared in the `/query` decorator
nor installed as middleware. It is attached dynamically at import, guarded by
`if auth_manager is not None` (`gate.py:1237-1246`) — and `config.yaml:518` ships
`auth: enabled: false`. **On a default install, `POST /query` has no cross-site protection.**

**Severity, measured rather than asserted.** The first draft of this write-up said a
hostile page could drive the local model and burn the operator's budget. That was wrong,
and testing it is what caught it. Two independent layers stop the browser attack today:

1. `QueryRequest` is `extra='forbid', strict=True`, and FastAPI parses the body only as
   `application/json`. A valid JSON body sent as `text/plain`,
   `text/plain;charset=UTF-8`, `application/x-www-form-urlencoded`, or
   `multipart/form-data` returns **422** — verified empirically against the real schema,
   all four. That eliminates the cross-site HTML form vector outright, since forms cannot
   send `application/json`.
2. `application/json` forces a CORS preflight, and `gate.py:477-483` installs
   `CORSMiddleware` with `allow_origins` from `security.allowed_origins`
   (`config.yaml:663+`, a loopback/LAN allow-list — **not** `*`) and
   `allow_credentials=False`. An off-list origin gets no `Access-Control-Allow-Origin`,
   so the browser never sends the real request.

So this is **defence-in-depth, not a live hole**. The reason to fix it is the asymmetry,
not an exploit: `POST /index/build` takes a bare `{ method: 'POST' }` with no body and no
`Content-Type` — a genuinely CORS-simple request — so `_looks_cross_site` there is
load-bearing and correctly unconditional. `/query` is safe only because its body
requirement *happens* to force a preflight. That is incidental rather than designed, and
it evaporates the day someone widens `allowed_origins` to `*` for a demo. `/query` is the
one route with no independent check of its own.

No test covers it either way: every case in
`tests/test_gate_query_auth.py::TestQueryCrossSiteRejected` uses the auth-enabled
fixtures, which is consistent with the code — with auth off there is nothing attached to
test.

## H2 — `/index/status` vs the limiter, with the arithmetic corrected

`GET /index/status` **is** rate-limited (`gate.py:913`). The console polls it every
`INDEX_POLL_MS = 1500` (`static/terminal.js:342`) = 40 req/min against
`RATE_LIMIT_REQUESTS = 60` per `RATE_LIMIT_WINDOW = 60`s (`gate.py:198-199`).

The draft claimed that budget is "shared with the 15s `/health` poll." It is not —
**`GET /health` is not rate-limited at all** (`gate.py:1164` carries no `dependencies=`).
The squeeze is real; the remaining 20 req/min is shared with operator traffic only.

There is also no exemption list to add to: the limiter is a per-route FastAPI dependency,
never global middleware, so "exempt" means dropping `Depends(_enforce_rate_limit)` from the
route with a comment saying why. Sequence against open PR #1173, which is reworking
`_enforce_rate_limit`'s audit path.

## Documentation gap that is real

`docs/THREAT_MODEL.md`'s control table (`:50-63`) has no row for either index route — zero
hits for `index/build` or `index/status` in the file. Its ninth amendment (`:961-1025`) is
the only in-depth treatment of `_looks_cross_site`, and it is scoped to
`security.api_key_optional`'s bypass, not to these routes or to `/query`'s conditionality.

Worth knowing for anyone gating that work: `doc-sync`'s D5 route check reads only CLAUDE.md
and `setup-guide.md` — **never README.md**. Its mechanical net would not have caught README
route drift even if there had been any.

## Why the CVE runbook was rescoped rather than run

Two independent problems.

**Its resolution premise was factually wrong.** The runbook called `constraints.txt`
"effectively the lockfile." `constraints.txt:12-15` says the opposite in its own words:
40 exact pins covering direct dependencies plus critical transitives, with "For complete
transitive reproducibility, run `pip-compile` and commit the result." Its instruction to
diff a full resolution against that file and "flag any drift as its own finding" would have
emitted one large false positive on the first run.

**CI already runs much of the plan on a schedule** — though a first pass at this write-up
overstated by how much, and a Codex review on PR #1200 caught it. `pip-audit.yml` and
`osv-scanner.yml` genuinely cover their steps, and `pip-audit.yml`'s `scan-optional` job
even auto-derives one requirements file per extra from `pyproject.toml` — the thing the
runbook asked to do by hand, done better, because a new extra is covered the day it lands.
Six findings are already risk-accepted with written rationale at `pip-audit.yml:165` (four
chromadb CVEs, one nltk, one setuptools), so a naive re-run reports them as new.

**The Trivy rows were the overstatement, in four separate ways** (two flagged by the
review, two found while verifying it):
- The fs job passes **no `scanners:` input at all** (`trivy.yml:41-53`), and Trivy's
  filesystem default is `vuln,secret` — so **license scanning is not covered**.
- The container job builds the checked-out Dockerfile as `cyclaw:ci` and scans that local
  tag; **nothing scans the published `ghcr.io/cgfixit/cyclaw` artifact**. The only
  workflow mention of that path is `publish-ghcr.yml:37`'s push target.
- Both jobs floor at `severity: 'CRITICAL,HIGH,MEDIUM'` (`:48`, `:121`), so **LOW findings
  have never surfaced**.
- The container job is **skipped entirely on docs-only PRs** (`:81-95`).

What is genuinely missing, therefore: **`trivy fs --scanners license`**, **`trivy image`
and `grype` against the published GHCR tag**, **zizmor** on the workflows, **tiered
reachability**, and the **three deliverables**. The corrected runbook
scopes to exactly those and tells the operator to reconcile the rest against CI rather than
rediscover it.

Smaller corrections: the `server` extra does not exist (the 11 real ones are listed);
`CVE-2026-45829` lives in `SECURITY.md:26-32` and the pin files, not `CLAUDE.md`; the
entrypoint list omitted `opentweet/cli.py`, the three `agentic/*connect/cli.py` CLIs,
`metrics.py`, `utils/authn_cli.py`, `utils/gen_cert.py` and `retrieval/clear_cache.py`;
and `sec-vuln-scanner` is a user-level synced skill, not a repo skill.

## Open items

| Item | Where it goes | Status |
|---|---|---|
| `/query` cross-site posture with `auth.enabled` false | issue #1201, lead item | Open — needs a decision |
| `/index/status` vs the limiter (exempt **or** raise `INDEX_POLL_MS`, never both) | issue #1201 | Open — sequence against PR #1173 |
| `THREAT_MODEL.md` control rows for both index routes and `/query` | issue #1201 | Open |
| F1's underlying bug | PR #1194 | Open — review posted, no port needed |
| grype / zizmor / reachability / deliverables | `console-review-bundle/cyclaw-dep-cve-scan-claude-code.md` | Ready to run |

## Reproducing the verification

```bash
python3 .claude/skills/invariant-guard/check_invariants.py

# The sandbox default python3 is 3.11; pyproject requires >=3.12,<3.13 (CLAUDE.md section 4)
python3.12 -m venv /root/.venv-cyclaw-312
/root/.venv-cyclaw-312/bin/pip install torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu
/root/.venv-cyclaw-312/bin/pip install -r requirements.txt -r requirements-test.txt \
    -c constraints.txt --ignore-installed PyYAML

GROK_API_KEY=dummy /root/.venv-cyclaw-312/bin/python -m pytest \
    tests/test_terminal_contract.py tests/test_gate_index_build.py -q --tb=short
GROK_API_KEY=dummy /root/.venv-cyclaw-312/bin/python -m pytest tests/ -q --tb=short
ruff check --select E,F,I,B,C4,UP,S .
python3 .claude/skills/doc-sync/doc_sync.py
```
