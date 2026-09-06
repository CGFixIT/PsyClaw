# Waku-Tools → CyClaw Harness — Implementation Plan (2026-08-17)

> **Status update — 2026-09-06 (docs review, Claude Code):** PARTIAL. Only Slice S1 shipped: `harness/tools_view.py:102,104` carries the `web-search`/`keys` catalog rows and `tests/test_harness_tools_contract.py` exists and enforces the two-way contract. S2 (`/memory search`), S4 (engine-backed web search), and S5 (`retrieval/corpus_report.py`) were never started — no `search` subcommand in `static/harness.html`'s `/memory` handler (verified `static/harness.html:931-969`), no `engine` code in `harness/web_search.py`, and `retrieval/corpus_report.py` does not exist. S3 (run manifests) remains a deliberate, documented deferral per the plan's own text.
>
> **What's left:**
> - Build S2 (`/memory search`, client-side substring filter over `GET /api/memory`) if the operator note count grows enough to want it — smallest of the remaining slices.
> - Build S5 (`retrieval/corpus_report.py` read-only corpus layout report) — independent of the harness, no dependencies on the others.
> - Build S4 (engine-backed `/web` search) only if wanted — largest remaining review surface (Medium-High risk tier per the plan), needs a `docs/THREAT_MODEL.md` amendment.

Implementation plan for the harness-console work derived from the
`waku-agent` tools analysis (`waku/tools/` @ `4e59ab5`: `__init__.py`,
`registry.py`, `workspace.py`, `search.py`, `memory_admin.py`, `notes.py`),
plus the one adoptable fragment of `QiuYannnn/Local-File-Organizer`.
Grounded against this repository at branch
`claude/waku-agent-tools-analysis-xulrw1` (commit `19c511e`, which contains
upstream `cgfixit/CyClaw` main through PR #986). The comparative
architecture/security assessment lives in `notes.txt` (repo root,
2026-08-16) — this document does not repeat it; it turns its conclusions
into buildable slices.

The sharkdp/numbat calculator (`/calc`) plan is a separate document:
[`docs/work/HARNESS_CALC_PLAN.md`](HARNESS_CALC_PLAN.md).

Everything here is subordinate to `CLAUDE.md` §3 (the six invariants),
`.claude/rules/PROJECT_RULES.md`, and `docs/THREAT_MODEL.md`. No slice
below weakens an invariant, a sanitizer pattern, an auth check, or a test.

---

## Decision record (owner decisions, 2026-08-17)

Four open questions from the analysis were put to the repository owner and
answered on 2026-08-17. These decisions are inputs to this plan, not
proposals:

| # | Question | Decision |
|---|---|---|
| D1 | `_HARNESS_SURFACES` ↔ routes contract-test shape | **Curated catalog + explicit exemption list** in the test; two-way assertion |
| D2 | Engine-backed web search disposition | **Plan it for implementation** (slice S4 below), under the constraints that preserve the allowlist guarantee |
| D3 | Calculator naming vs. the `numbat:` config block (forensics emitter) | **`/calc` command + `calc` identity**; binary stays sharkdp `numbat`, never settable via HTTP |
| D4 | Calculator network hardening level | **Managed config + best-effort screens**; hardened-source build documented as optional appendix only |

D3/D4 are executed in [`docs/work/HARNESS_CALC_PLAN.md`](HARNESS_CALC_PLAN.md).

---

## Why a faithful waku-tools port is rejected

Waku's tools layer and CyClaw's harness `/tools` share a word and nothing
else. In waku, `waku/tools/registry.py` hands the model a `tools=` schema:
the model chooses and executes tools, including tools that rewrite its own
soul, memory, and skills, and `waku/tools/workspace.py` executes
model-authored code by default. CyClaw's harness console
(`harness/server.py`, loopback `127.0.0.1:8790`) is the inverse: slash
commands are dispatched client-side in `static/harness.html` to fixed REST
routes, and the local model has no tool-invocation path at all —
`harness/ollama.py` contains no tool/function-call parameter anywhere
(verified by inspection, 2026-08-17). A faithful port is therefore not a
feature addition; it is a threat-model change inside the one process that
holds `/api/agent/run`, `/api/agent/runs/{id}/push`, and
`/api/agent/runs/{id}/publish`.

Per-tool rejections, each with the rule that rejects it:

| waku tool | What it does there | Rejected because (CyClaw rule) |
|---|---|---|
| `registry.py` model dispatcher | Model selects + executes tools | I2 (topology = policy: routing is never an LLM decision) and the `harness/agent_policy.py` boundary — the console sends profile **names**, never argv; a model-driven dispatcher reverses that |
| `workspace.py` auto-run | Executes model-authored code by default | Inverts the name-not-argv boundary (`harness/agent_policy.py` module docstring); CyClaw's executor runs only operator-declared checks inside a jail, and `real_repo_loop` requires a human decision before any commit |
| `update_soul` | Model appends to its own soul file | I5 / PROJECT_RULES Hard Rule: soul mutation requires a human `reason` through `PersonalityManager`; the harness `/soul` toggle deliberately never writes `soul.md` |
| `create_skill` | Model writes new skill files for itself | Governed skills registry: writes to `data/agentic/skills_registry.json` stay behind `python -m agentic.cli apply-skill` (`docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md`); `GET /api/registry` is read-only by design |
| `search.py` open web search | Engine results fed raw into model context | The shipped `/web` design is the deliberate inverse (allowlist-only, DNS-checked, fail-closed). Note: engine-backed **discovery** is planned (S4, owner decision D2) — what stays rejected is waku's *shape*: raw engine output entering the model's context automatically |
| `memory_admin.py` / `notes.py` | Model-writable memory/notes | The harness `/memory` note store is operator-written, injection-scanned, capped, and `writable_from_harness: false` toward the RAG `memory/` store — already the CyClaw-shaped version of this idea (landed independently) |

`Local-File-Organizer` gets the same treatment: the model-driven bulk
move/rename and its Nexa/Tesseract dependency stack are rejected (new heavy
runtime dependencies against the exact-pin posture; autonomous file moves
against the dry-run-default rule in `CLAUDE.md` §5). The one adoptable
fragment is a read-only corpus-layout report — slice S5.

---

## Current-tree deltas since the analysis snapshot

The analysis was performed against upstream `177d2f2`. Between that commit
and `19c511e` (this branch's base), upstream independently landed features
that shrink two of the four originally proposed slices — verified in the
tree, not assumed:

- `/memory` operator notes shipped (`harness/memory_notes.py`: add / forget
  / clear / on / off, 20-note cap, injection scan on add, RAG flags echoed
  read-only). What remains of "read-only memory search" is only a search
  subcommand — S2.
- Run provenance substantially shipped: `agentic/real_repo_run_store.py`
  persists per-run JSON records (read back by `GET /api/agent/runs/{id}`),
  and `utils/numbat_emitter.py` (PR #973, issue #959) projects executor
  check runs, `ops_runner` invocations, and `real_repo_loop` decisions to
  `logs/numbat-events.ndjsonl`, keyed by `run_id`. This covers most of the
  "run manifests" idea — S3 is therefore a deferral with a thin residual.
- `/web` shipped with `fetch`, allowlist CRUD, `inject`, and a `search`
  subcommand that greps **already-allowlisted pages only** (no engine) —
  `harness/web_search.py`.
- A console↔routes contract test shipped for `static/harness.html`
  (`tests/test_harness_console_contract.py`) — but **no** test ties
  `harness/tools_view.py`'s `_HARNESS_SURFACES` catalog to the live app.
  Live drift exists today: `POST /api/web/search` is a registered route the
  console calls, yet the catalog has no `web-search` row, and the `/api`
  credentials command (`GET/POST /api/keys`) has no row either. S1 fixes
  and then pins this.

---

## Slice S1 — two-way `/tools` catalog contract test (P1)

**Goal.** Make `harness/tools_view.py`'s `_HARNESS_SURFACES` (the data
behind `GET /api/tools` and the `/tools` console command) mechanically
un-driftable against `harness/server.py`'s registered routes. This is the
structural lesson from waku's registry (one source of truth for the tool
surface), applied CyClaw-style: static data plus a test, no runtime
dispatcher.

**Catalog fixes (data-only edits to `_HARNESS_SURFACES`):**

- Add `("web-search", "/web search", POST, "/api/web/search", "grep
  allowlisted pages for a query (no search engine)")`.
- Add `("keys", "/api", GET, "/api/keys", "managed credential status
  (masked tail only, never a value)")`.
- Exact final row set is a review-time item (see Open items) — the test
  makes any choice explicit and permanent.

**New test file** `tests/test_harness_tools_contract.py` (separate from the
HTML contract test — different contract, same offline app-factory
technique: `create_app(HarnessConfig.load(tmp_path / ".CyClaw"))`):

1. **No phantom rows:** every `(method, path)` in `_HARNESS_SURFACES` is a
   registered `APIRoute` with that method. A renamed or removed route can
   no longer leave a stale catalog row rendering `○` forever.
2. **No invisible routes:** every registered `/api/*` route is either
   cataloged or named in an explicit `_CATALOG_EXEMPT` frozenset in the
   test, one comment per entry saying *why* it is exempt (owner decision
   D1). Proposed exemptions: sub-actions of cataloged families
   (`POST /api/web`, `/api/web/allow|deny|inject|forget`,
   `POST /api/memory`, `/api/memory/forget|clear`, `POST /api/soul`,
   `POST /api/sessions`, `GET /api/sessions/{id}`,
   `POST /api/sessions/{id}/rename`, `POST /api/keys`) and the
   `/api/auth/*` block (its own session+CSRF auth domain, driven by the
   `/users` command; a single catalog row for it is a review-time option).
   Adding a route then forces a deliberate choice: catalog row or
   commented exemption — the drift class that produced today's missing
   `web-search` row becomes a test failure.
3. **Guard contract:** every registered `POST`/`DELETE` route carries the
   API-key dependency (`_require_api_key_or_optional` in the route's
   dependant tree) unless named in an explicit `_KEY_EXEMPT` set (the
   `/api/auth/*` session-domain routes, which authenticate by session
   cookie + CSRF instead and 503 while `auth.enabled` is false). This
   turns `config.yaml`'s prose claim about the harness's guarded routes
   into a pinned, counted fact — if the documented count in `CLAUDE.md` /
   `config.yaml` comments disagrees with ground truth, fix the docs via
   `/doc-sync` in the same PR, not the test.

**Files touched:** `harness/tools_view.py` (data rows only),
`tests/test_harness_tools_contract.py` (new),
`tests/test_harness_console_contract.py` (only if a console string for
`/tools` output changes), docs touched by doc-sync findings.

**Security analysis:** no invariant touched; no route added or changed; the
`guarded` list is untouched. Adds enforcement only. Risk tier: **Low**
(test + data rows). `--cov=harness` is package-wide in `ci.yml`, so no
coverage wiring changes; new test files auto-discover.

**Rollback:** revert the test file and the two catalog rows.

---

## Slice S2 — `/memory search` (P2, residual)

**Goal.** Let the operator filter their pinned harness notes. The rest of
the original "read-only memory search" idea already shipped upstream
(`harness/memory_notes.py`).

**Smallest honest implementation (stated assumption, flagged for review):**
client-side. `GET /api/memory` already returns every note (hard caps:
20 notes × 500 chars — `harness/memory_notes.py` `_MAX_NOTES`,
`_MAX_NOTE_CHARS`), so `/memory search <term>` in `static/harness.html`
does a case-insensitive substring filter over the payload the console
already fetches and renders matching `[id] text` rows. Zero new routes,
zero new server code, zero new attack surface.

**Explicitly rejected variant:** a harness→gate loopback proxy to
`GET /memory/facts` (the RAG memory store on `127.0.0.1:8787`). It would
create a new credential-bearing egress channel from the harness process to
the gateway for a subsystem whose every switch ships `false`
(`config.yaml` `memory:`). If the RAG memory store is ever enabled in
practice, the operator already has a keyed read surface on the gateway
(`GET /memory/facts`); revisit only then, and as its own plan.

**Files touched:** `static/harness.html` (one `case` branch),
`tests/test_harness_console_contract.py`
(`test_console_documents_memory_slash_command`: assert the updated usage
string `'/memory [on|off|add|search|forget|clear]'` and the new branch).

**Security analysis:** read-only over data the console already holds; no
new route; no invariant touched. Risk tier: **Low**.

**Rollback:** revert the HTML branch and test strings.

---

## Slice S3 — run manifests (P3): deferred, with the residual named

**Original idea** (waku's provenance concept, minus its auto-run): a
per-run, read-only manifest of what an agentic run actually did.

**Finding against the current tree:** substantially shipped since the
analysis snapshot, by three independent artifacts —
`agentic/real_repo_run_store.py`'s persisted per-run JSON record (already
exposed read-only at `GET /api/agent/runs/{id}` / `/agent status`),
`utils/numbat_emitter.py`'s forensic NDJSON events keyed by `run_id`
(`logs/numbat-events.ndjsonl`, `config.yaml` `numbat:`), and the
convergence-guaranteed `logs/audit.jsonl` (I4). The provenance exists; only
a *joined console view* does not.

**Decision: defer.** The residual slice — a guarded
`GET /api/agent/runs/{id}/manifest` returning the run record merged with
that run's numbat events, surfaced as `/agent manifest <run_id>` — is
sketched here so reopening is cheap, but it does not currently earn its
keep: the operator can read both artifacts directly, and every event source
already writes them. Reopen if a real forensics or review workflow
demonstrates the join being done by hand repeatedly. Risk tier if built:
Low (read-only join of existing local files; same `guarded` +
rate-limit pattern as the other agent routes; S1's contract test would
force a catalog/exemption decision automatically).

---

## Slice S4 — engine-backed `/web` search (P4, owner decision D2)

**Goal.** Add web *discovery* to the harness console without breaking
either of the two guarantees the shipped `/web` design is built on
(`harness/web_search.py`): the harness fetches only operator-allowlisted
hosts, and the local model never sees web content the operator did not
explicitly fetch from an allowlisted page.

**Design constraints (these are the slice — an implementation that drops
any one of them is a different, rejected feature):**

1. **The engine is itself an allowlisted host.** One engine, configured by
   the operator as a base URL. Setting it passes the exact validation
   `/web allow` applies today (http/https only, no userinfo, public
   DNS-resolved address, no loopback/RFC1918/link-local/metadata — the
   `assert_public_host` path), **and** the engine host must already be on
   the `/web` allowlist. The egress invariant sentence stays literally
   true: the harness GETs allowlisted hosts only.
2. **Results are for the operator, not the model.** The response is parsed
   into bounded `(title, url, snippet)` rows and rendered in the console.
   Result URLs are **never** auto-allowlisted, **never** auto-fetched, and
   engine snippets are **excluded from `/web inject`** — the injectable
   extract remains exclusively the product of `/web fetch` / `/web search`
   over allowlisted pages (`harness/prompts.py` `_append_web` stays fed
   from the same single source as today). To read a result, the operator
   allowlists its host and fetches it — the existing gated flow, unchanged.
3. **Fail-closed at every step.** `/web` disabled → `409 WEB_DISABLED`
   (existing). No engine configured → new typed code `WEB_ENGINE_UNSET`.
   Engine host missing from the allowlist → existing `WEB_HOST_DENIED`.
   Non-JSON / oversized / malformed engine response → new
   `WEB_ENGINE_BAD_RESPONSE`; no partial parse enters the console.
4. **Bounded like everything else in the module.** Reuse
   `harness/web_search.py`'s existing budget constants where they fit
   (response byte cap, snippet length, timeout — `_MAX_BYTES`,
   `_MAX_SNIPPET`, `_TIMEOUT_SEC`); cap result rows (proposed: 10,
   review-time number). Query length capped by the existing `_MAX_QUERY`.
5. **Privacy is stated, not hidden.** Engine search sends operator query
   text to the operator-chosen engine host — a new category of egress
   (query content, not just page fetches). The slice therefore includes a
   `docs/THREAT_MODEL.md` amendment recording exactly that, same
   convention as the Telegram and provider-arming amendments. The audit
   line records the query **hash** only (`utils/logger` conventions — raw
   query text is never persisted, same rule as the gateway).

**Surface.** Console: `/web engine <query>` to search, `/web engine set
<base-url>` / `/web engine clear` to configure (subcommand name chosen to
avoid colliding with the shipped `/web search` page-grep; final naming is a
review-time item). Routes: `POST /api/web/engine` (search) and
`POST /api/web/engine/set` (configure), both in `harness/server.py`'s
`guarded` dependency list — which already bundles, in this order, the
expensive-route per-IP throttle, same-origin, Bearer `CYCLAW_API_KEY`
(honoring `security.api_key_optional`), and CSRF — exactly as the shipped
`/api/web/*` mutating routes do. Engine base URL
persists in the harness home alongside the allowlist (same
`_atomic_write_json` pattern), not in `config.yaml` — matching the shipped
precedent that `/web` runtime state (`web_enabled`, allowlist) is
harness-home state, not a repo tunable.

**Engine adapter, v1: exactly one.** A SearXNG-compatible JSON API
(`?q=<query>&format=json`), because it needs no API key, no new dependency
(`httpx` is already the module's client), and no vendor lock — the operator
picks the instance, and that trust decision is explicit via allowlisting.
Two consequences stated honestly: (a) a **self-hosted** SearXNG on
loopback/LAN is *refused* by the public-host SSRF rule — supporting it
would weaken a shipped security check and is deliberately not proposed
(that relaxation would need its own owner-approved plan per `CLAUDE.md`
§7 High tier); (b) keyed engines (Brave-style) are out of v1 — if ever
added, the key goes through `harness/env_keys.py`'s `MANAGED_KEYS`
allowlist with the existing never-echoed handling, and that is its own
follow-up.

**Tests** (all offline, `httpx.MockTransport`, same technique as
`tests/test_harness_web.py`): engine-set validation refusals (loopback /
private / userinfo / bad scheme); search refused when disabled, when
engine unset, when engine host not allowlisted; bounded parse (row cap,
snippet cap, byte cap); malformed-JSON → `WEB_ENGINE_BAD_RESPONSE`; the
**no-inject invariant test** — after an engine search, the prompt-assembly
path (`harness/prompts.py`) receives nothing new and `/web inject` still
injects only the last allowlisted-page extract; contract tests updated
(S1's two-way test forces the catalog/exemption decision for the two new
routes; console-contract strings for the new subcommand).

**Files touched:** `harness/web_search.py` (adapter + validation),
`harness/server.py` (two guarded routes), `harness/config.py` (engine
state field), `static/harness.html` (subcommand branch),
`harness/tools_view.py` (row per S1 policy), `docs/THREAT_MODEL.md`
(amendment), `harness/README.md`, tests as above.

**Security analysis:** no invariant touched (I1–I5 are gateway/graph
properties; I6 unaffected — `harness/` imports neither the core three nor
`agentic/`). The new risk is the query-content egress, mitigated by:
default-off (`/web` master switch), engine-in-allowlist requirement,
guarded + rate-limited routes, hash-only audit, and the threat-model
amendment. Risk tier: **Medium-High** → the PR body must argue the change
explicitly, `invariant-guard` re-run, and this slice ships **last** in the
build order.

**Rollback:** feature is dead code when no engine is set and `/web` is off
(both are the shipped defaults); full rollback = revert the module/route
diff.

---

## Slice S5 — corpus layout report (Local-File-Organizer fragment)

**Goal.** The one CyClaw-shaped fragment of `QiuYannnn/Local-File-Organizer`:
a read-only report that helps the operator keep `data/corpus/` organized —
no model, no network, no file mutation, no new dependency.

**Shape.** `python -m retrieval.corpus_report` (new module
`retrieval/corpus_report.py`, stdlib + existing config loader only):

- Reads `corpus.path` (`data/corpus`) and `corpus.extensions`
  (`.md`, `.txt`) plus `indexing.chunk_size` (512) from the loaded `cfg`
  dict — passed down, never re-read mid-module, per the config convention.
- Reports: per-directory file/byte counts; extension histogram including
  files the indexer would **skip** (extension not in `corpus.extensions`);
  empty and oversized files; staleness (mtime buckets); duplicate content
  groups (SHA-256 of file bytes); estimated chunk counts per file (bytes ÷
  `chunk_size` heuristic, labeled an estimate).
- Proposes (as text) a grouping by top-level Markdown heading or filename
  prefix — deterministic string heuristics, explicitly **not** a semantic
  classifier.
- Output: human-readable text to stdout, `--json` for machine use.
  **There is no `--apply` and no write path at all in v1** — this is a
  report, stronger than dry-run-by-default.
- Exit codes per the agentic convention: `0` ok · `2` scan failed
  (unreadable corpus) · `3` env/config error.

**Rejected from the source project:** model-driven rename/move (autonomous
file mutation), and the Nexa/Tesseract/OCR dependency stack (heavy new
runtime pins against the exact-pin, minimal-dependency posture).

**Files touched:** `retrieval/corpus_report.py` (new),
`tests/test_corpus_report.py` (new; tmp-dir corpus fixture, deterministic,
no network), `.github/workflows/ci.yml` (**required**: the retrieval
coverage flags are per-module — add `--cov=retrieval.corpus_report`;
`pyproject.toml` `[tool.coverage.run]` already lists `retrieval`), optional
`[project.scripts]` entry `cyclaw-corpus-report` (the `python -m` form
works regardless).

**Security analysis:** read-only, offline, out-of-band, imports nothing
from the core three and is imported by nothing (I6-irrelevant but clean).
Risk tier: **Low**.

---

## Build order, PR shape, quality gates

One draft PR per slice, conventional-commit titled, body = What / Why /
Risk to monitor, per `CLAUDE.md` §5–6. Order:

1. **S1** (contract test + catalog fixes) — merges first because it then
   *enforces* the catalog decisions every later slice must make.
2. **S2** (`/memory search`, client-side) — trivial after S1.
3. **S5** (corpus report) — independent of the harness entirely; can land
   in parallel with S2.
4. **S4** (engine search) — last: largest review surface, threat-model
   amendment, Medium-High tier.
5. **S3** — not scheduled (deferred; residual documented above).

Every slice, before its PR opens:

```bash
ruff check --select E,F,I,B,C4,UP,S .
GROK_API_KEY=dummy pytest tests/ -q --tb=short
python3 .claude/skills/invariant-guard/check_invariants.py   # must exit 0
python3 .claude/skills/doc-sync/doc_sync.py                  # no new drift
```

plus the CI-style coverage run for any slice adding a source module (S5),
and `config-guard` for any slice touching `config.yaml` (none do — S4 and
S2 state persists in the harness home by shipped precedent; S5 only reads
existing keys).

Shared-file collision note (the `CLAUDE.md` §4 trap): S1, S2, and S4 all
touch `static/harness.html` and/or `tests/test_harness_console_contract.py`
— cut each branch from the merged result of the previous one, or
trial-merge locally before opening the pair.

---

## Open items at review time

Small decisions deliberately left to the first PR review rather than
blocking the plan (each has a stated default):

- Exact final `_HARNESS_SURFACES` row set vs. exemption entries beyond the
  two named fixes (`web-search`, `keys`) — default: rows for user-facing
  slash families, exemptions for sub-actions and `/api/auth/*` (owner
  decision D1's shape).
- Whether the documented guarded-route count in `CLAUDE.md` /
  `config.yaml` comments needs a doc-sync correction once S1's test counts
  ground truth — default: fix docs to match the test, never the reverse.
- `/web engine` subcommand naming (`engine` vs. a separate top-level
  command) — default: `engine`, avoiding any collision with the shipped
  `/web search` page-grep.
- S4 result-row cap (proposed 10) and whether `/web engine set` requires
  the host to be allowlisted *first* or allowlists it in the same call —
  default: require it first (two explicit operator actions).
