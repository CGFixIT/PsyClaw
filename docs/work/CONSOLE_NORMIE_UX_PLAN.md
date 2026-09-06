# CyClaw console: "good for a dentist" — implementation plan

> **Status update — 2026-09-06 (implementation recheck):** MOSTLY COMPLETE. All
> four PRs in this plan's own status log (§4) are shipped and verified live on
> `main`: the advanced-mode toggle exists (`static/terminal.html:959-960`
> `#advancedTools`/`Advanced ▸`), `POST /index/build` + `GET /index/status`
> exist in `gate.py` (lines 913, 969), and `QueryResponse.llm_model`
> (`schemas/api.py:57`) carries the resolved model tag additively alongside
> the untouched `model_used` role vocabulary. Error-copy and privacy-lede work
> described in PR 1/3 is in `terminal.js`/`terminal.html` as claimed.
>
> Four smaller UX gaps remain. They were reverified against current code and
> are listed below; implementation details are tracked in GitHub issue #1331.

Original planning base: `origin/main` @ `730b979` (#1074 merged).
Status rechecked against `origin/main` @ `9bf7347` on 2026-09-06.
Discipline: `/karpathy-guidelines` + `/ponytail` — surgical diffs, no speculative
generality, every claim below verified against source (file:line) not recalled.

---

## Still to implement

1. Rewrite the confirmation prompt: it still says
   `Vault miss (best score: … < …)`, exposing operator jargon and raw scores.
2. Add the planned health-details expander and an inline Ollama help link;
   today this is tooltip-only text.
3. Render index-build elapsed time. The API returns it and chunk progress now
   works, but the panel does not show elapsed time.
4. Optional polish: translate answer metadata from `model: local`, `mode`, and
   `hits` into the plan's human wording ("answered from your documents," etc.).

---

## 0. Answers to your two direct questions

### "Is /ops the fsconnect and sqlagent (and if separate all 3 sections)?"

**Four, not two or three** — and the second name is `sqlconnect`, not "sqlagent":

| Route | Shims | Has a toolbar button? |
|---|---|---|
| `/ops/sync` | Dropbox corpus sync (`sync/`) | Yes — "Sync Console" |
| `/ops/agentic` | GitHub context + skills registry (`agentic/`) | Yes — "Agentic Console" |
| `/ops/fsconnect` | Local/SMB filesystem connector | Yes — "FS Console" |
| `/ops/sqlconnect` | SELECT/WITH-only SQL connector | Yes — "SQL Console" |

All four share one identical security posture and one shared handler body
(`gate_ops.py`, the `_run_ops_route` helper from #1066).

**But the advanced-mode question is bigger than `/ops`.** The toolbar has
**seven** operator surfaces, not four (`static/terminal.html:828-834`):

- **Always visible to every visitor, zero gating:** Soul Console, Sync, Agentic,
  FS, SQL — five buttons.
- **Already role-hidden:** Users, Audit — `hidden` attribute, toggled by
  `applyRoleChrome()` (`terminal.js:288-293`).

So a dentist's first screen today shows five subsystem consoles they must learn
to ignore. Everything else that's API-key-gated (`/audit/summary`, all eight
`/memory/*`, `/query/export/html`) has **no UI at all** — so the toggle only has
to hide what already exists: 7 buttons, 7 panels.

**Trap:** the existing `applyRoleChrome` gate can't be reused. It keys off
`authRole` from `/auth/whoami`, and `auth.enabled` ships `false` → that route
returns 503 → `authRole` stays `null` forever in the default posture
(`terminal.js:109-114`). Advanced mode must be an independent client-side flag.

### "If I merge in chron order, what would that mean?"

Chron order = the order I open PRs = the order I do the work, so the question is
really *what order avoids writing the same copy twice*. Two dependencies force it:

- **Item 3 (error copy) is the vocabulary items 1, 2 and 5 all draw from.** If
  it lands last, its sentences get written three times first.
- **Item 5 (privacy copy) needs a home, and item 1 rebuilds that home.** The
  empty state is `.remove()`d, not hidden (`terminal.js:352`), so first-run work
  reconstructs that node entirely.

Resolution that keeps your "4 and 5 first" instruction intact: author the item-3
**table** as a doc up front (cheap, no code), then implement it last. The table
is the shared vocabulary; the PR that ships it is independent.

---

## 1. What the survey found that changes the shape

Five parallel read-only slices over `terminal.html`/`terminal.js`,
`harness.html`, the error hierarchy, `gate_ops.py`, and the indexer wiring.

**Good news — items 1 and 2 need ZERO server change to detect state.**
`/health` already ships `index_ready`, `graph_ready`, and per-service
`{healthy, latency_ms, error}` (`schemas/api.py:64-71`, populated
`gate.py:921-925`). The console fetches all of it and **discards it** — the
strings `index_ready`, `graph_ready`, `services` appear nowhere in `terminal.js`.
A first-run banner can detect a missing index on the existing 15s poll.

**Bad news — the model name genuinely needs a schema change.**
`QueryResponse.model_used` carries the **role** (`"local"` / `"grok"` /
`"claude"` / `"offline-best-effort"`), never the config tag. The real name *is*
already computed dynamically from `config.yaml` — `graph.py:788-816`
`_llm_identity()` → `{"llm": "RAG local: qwen3.8:27b-mlx", "llm_model": "..."}` —
but it's written **only to `audit.jsonl`**. `QueryResponse` is
`extra='forbid', strict=True` (`schemas/api.py:43-61`), so there is no
UI-only path to your ask. **This is the one High-tier item in the plan.**

`graph.py:852-857` explicitly forbids renaming `model_used`'s role vocabulary —
`metrics.py` buckets its model histogram on that value's prefix, so changing it
silently reinterprets every historical audit line. The new field must be
**additive**, exactly as `_llm_identity` already is for audit.

**The role string already IS your hit/miss/online signal** — it's just unlabelled:

| `model_used` | What actually happened |
|---|---|
| `local` | Pure vault HIT — score ≥ min_score, answered from your documents |
| `offline-best-effort` | Vault MISS, you declined online, answered from model knowledge |
| `grok` / `claude` | Vault MISS, you confirmed online |
| `guardrail-blocked` / `hook-denied` / `external-unavailable` | Blocked/unavailable paths |

**The worst copy on the surface is server-side, not HTML.** On a vault MISS a
lawyer currently reads (`gate.py:795-802`):

> `CONFIRMATION REQUIRED` / `Vault miss (best score: 0.019 < 0.028). Choose Offline Best Effort or Grok or Claude.`

Two raw RRF floats and the word "vault miss." The *mechanics* are already sound
— `role=dialog` + `aria-modal`, focus on the safe default, focus trap, and
`available_providers` fail-closed so no dead "send online" button ever renders
(`terminal.js:514-589`). Only the words are wrong.

**"Best effort" appears in four places, spanning both files:**
`terminal.js:568` (button), `:569` (aria-label), `:602` (system entry), and
`gate.py:614-620` (server prose baked into `confirm_message`).

**Typography: 31 hardcoded px, zero `rem`, zero `clamp()`.** Body is 13px; the
*entire* telemetry chrome — mode badge, entry labels, meta row, sources toggle,
footer — is 10px. A CSS custom-property system exists but covers only
color/font/radius: **no type scale, no spacing scale, no light mode.**

**Two real contrast failures in the empty state:** hint text is `#454d59` on
`#0d0f11` ≈ **2.6:1** (WCAG AA needs 4.5:1), and the keyboard-hint line is
explicitly `color:var(--border)` = `#2a2f38` ≈ **1.4:1** — effectively invisible.

**Privacy copy today: three strings, all incidental.** One written for an
operator ("all loopback, audited, API-key gated"), one that vanishes on first
query, one deliberately styled unreadable. Grep for `never leaves` / `private` /
`privacy` / `on-device` / `air-gap`: **zero matches.**

**No index-build route exists anywhere.** `build_index()` returns `None` and
emits progress only as `logger.info` lines (including a per-batch
`"Indexed %d/%d chunks"`); no callback, no generator. But `gate.py`'s `retriever`
and `compiled_graph` globals **are** hot-reassignable — `/query` reads them at
call time, and three existing test fixtures already prove reassignment works.

---

## 2. The plan — four PRs, dependency-ordered

### PR 0 — doc bug fixes (independent, ~6 lines) — **SHIPPED (#1078)**

Clears the two Codex findings from #1074 that merged unfixed. Zero coupling to
anything else; can land first or ride with PR 1.

- `.trivyignore:21` + `SECURITY.md:33` — nltk `3.9.4` → `3.10.0` (all four
  manifests pin 3.10.0), and replace the "punkt runs only at corpus index time"
  rationale: `retrieval/stemmer.py` **never calls punkt** (hand-rolled `_WORD_RE`
  regex, documented as existing precisely to keep that CVE unreachable), and the
  tokenizer it does use runs at index time **and** on every keyword query. The
  acceptance stays valid — it rests on "punkt is never called," not "offline only."
- `.github/copilot-instructions.md:42` — split `memory/` out of the "never
  imported by core" row. `gate_memory.py` (one of the core six) lazily imports
  `memory.*` at 9 route-handler sites; `graph.py` imports `memory.store` on the
  enabled path. The file's own I6 row already gets this right.

**Verify:** `grep -n "3.9.4" .trivyignore SECURITY.md` → empty; `doc-sync` → 0 drift.

---

### PR 1 — advanced-mode toggle + fluid type + privacy lede — **SHIPPED (this PR)**

Pure front-end. No server change, no schema change, no new route.

**1a. Advanced toggle (item 4).** Wrap the five ungated console buttons plus the
`toolbar-hint` in a `<span class="advanced-tools" id="advancedTools" hidden>`,
add one `Advanced ▸` toggle button with `aria-expanded`/`aria-controls`.
Users/Audit keep their existing independent role gate — an admin sees them only
when *both* their role allows and advanced is on. Persist the choice in
`localStorage` (wrapped in try/catch — private windows throw).

Default: **off**. A dentist sees a search box, a status light, and nothing else.

**1b. Fluid type (item 5, the sizing half).** Introduce a type scale as custom
properties on `:root` and convert the 31 hardcoded px sites to it. Anchor with
`clamp()` so it tracks viewport *and* respects the browser's font-size
preference. Minimum bump: the 10px telemetry chrome → ~12px floor. Fix the two
contrast failures (`--text-muted` on the empty state, and the `var(--border)`
hint line) while I'm in there — same CSS, same review.

Harness gets the same token block but **not** the same scrutiny (your priority
call). Its contract test bans `innerHTML` file-wide and pins ~40 literal strings
plus positional `html.index()` slices that fix declaration *order* — so harness
edits are CSS-block-only, no markup reshuffling.

**1c. Privacy lede (item 5, the copy half).** One line, one place, normal weight
— not a banner, not repeated:

> Your documents never leave this machine. Every query is logged locally, and you can read the log.

Goes in the empty state above the existing hint. PR 2 rebuilds that node and
will carry the line forward verbatim.

**Verify:** `pytest tests/test_terminal_contract.py -q` (nothing here changes a
route, so `_POST_PATHS` is untouched); manual check that Escape-closes-all still
resets the five hardcoded button labels; contrast recheck with computed values.

---

### PR 2 — first-run + plain-language health (items 1 + 2)

These share one data source (`/health`'s `index_ready`) and one DOM region, so
splitting them means touching the same code twice.

**2a. Plain-language health (item 2).** Three states exist today, all
engineer-facing: `gateway ok` / `gateway degraded` / `gateway unreachable`, and
`degraded` paints the dot **red** — identical to a hard failure — even though
CLAUDE.md §4 says degraded-without-Ollama is *normal*. Translate using the
`services` detail already on the wire:

| Condition | Sentence | Dot |
|---|---|---|
| all healthy | Ready | green |
| Ollama down, index present | Your local AI engine isn't running — [how to start] | amber, not red |
| `index_ready: false` | No library yet — build one below | amber |
| build running | Building your library… | amber, pulsing |
| fetch threw | Can't reach CyClaw — is it still running? | grey |

Raw JSON stays one click away behind a details expander. The status chip keeps
its terse form for you.

**2b. First-run (item 1).** Two new routes on `gate.py`:

- `POST /index/build` — loopback-socket-peer + same-origin + rate-limited +
  audited, mirroring `/auth/bootstrap-password`'s precedent (the existing route
  that must work before any credential exists — key-gating would brick first-run
  since an unset `CYCLAW_API_KEY` fails **closed**). Single in-flight build
  behind a lock; 409 if already running. Runs `build_index` in a background thread.
- `GET /index/status` — `{state, started_at, elapsed, error?}`.

Progress: `build_index` gives no callback, so **v1 is an honest indeterminate
spinner + elapsed time**, not a fake percentage bar (ponytail — no invented
progress). If we want a real bar later, it's a `build_index` signature change,
tracked separately.

Hot-init: extract `gate.py`'s import-time retriever/graph construction into
`_init_retrieval()`, called at import **and** after a successful build, so
first-run needs no restart. Three test fixtures already reassign these globals,
proving it works — but `test_edge_cases` sets them *without* restoring, so the
refactor must keep the names stable.

UI: when `index_ready` is false, the empty state renders "Point me at your
documents" with the configured corpus path (read-only — changing it is config
mutation, High tier, not v1) and a Build button.

**Obligations that will fail CI if skipped:** `tests/test_terminal_contract.py`
`_POST_PATHS` += `/index/build`; CLAUDE.md route table; `docs/setup-guide.md`
REST section (doc-sync D5 counts routes — new routes change the count).

---

### PR 3 — error copy + route/model badge (item 3) ⚠️ contains the one High-tier change

**3a. The table.** Ten codes reach a `/query` user (`INDEX_NOT_FOUND`,
`PROMPT_INJECTION_BLOCKED`, `GRAPH_TIMEOUT`, `GRAPH_ERROR`, `RATE_LIMIT`,
`VALIDATION_ERROR`, `PAYLOAD_TOO_LARGE`, plus `AUTH_ROLE_DENIED` /
`CROSS_SITE_BLOCKED` / `AUTH_REQUIRED` only when auth is on), plus four that
arrive as a **200 with an `error` field** carrying graph's `"{code}: {message}"`
stamp. `utils/errors.py` declares 51 classes total — the rest are operator-only.

Two chokepoints make this cheap: **one** meta-row renderer and **one** error
renderer. `extractErrorMessage` already normalizes all three server envelope
shapes but *discards the code* — needs a parallel extractor reading
`err.detail?.code || err.code`, then a lookup table. Code stays visible in small
print beside the sentence.

Precedent exists: exit-code-to-English mapping is already implemented **four
times** for the ops panels — same pattern, new table.

**3b. The badge — the High-tier bit.** Add one additive field to
`QueryResponse` (`extra='forbid'` blocks any workaround) carrying the resolved
model tag, populated in `gate.py` from the **same** `_llm_identity` mapping
`graph.py` already uses for audit. `model_used`'s role vocabulary is untouched —
`metrics.py` depends on it.

Then the meta row reads, e.g.:

- vault HIT → `answered from your documents · qwen3.8:27b-mlx · score 0.041 ≥ 0.028`
- vault MISS, declined → `not in your documents · answered by qwen3.8:27b-mlx from its own knowledge`
- vault MISS, confirmed → `not in your documents · sent to grok-4.5`

That kills "best effort" in all four places, including the server prose in
`gate.py:614-620` and the `confirm_message` floats at `gate.py:795-802`.

**Because this touches `schemas/api.py` + `gate.py` (core path), CLAUDE.md §7
puts it at High tier → explicit sign-off before I write it.**

---

## 3. What I need from you

1. **Confirm the `QueryResponse` schema addition** (PR 3b). It's the only way to
   show the real model name; `extra='forbid'` blocks every alternative. Additive
   field, `model_used` untouched, `metrics.py` unaffected.
2. **Sanity-check the privacy sentence** in PR 1c — it's your pitch, not mine,
   and it's the one line a lawyer will actually read.
3. **Anything in the "advanced" bucket you'd rather keep in the lobby?** My cut
   hides all seven (Soul, Sync, Agentic, FS, SQL, Users, Audit) and leaves the
   query box, status light, and mode badge.

Not blocking — I'll start PR 0 + PR 1 on your go-ahead since neither touches a
schema or a route.


---

## 4. Status log

- **PR 0** — shipped as #1078 (`claude/doc-accuracy-fixes`).
- **PR 1** — shipped as the PR carrying this document.
  Added during implementation, not in the original plan: a global
  `[hidden] { display: none !important }` rule. Rendering the console in a real
  browser showed `#usersToggleBtn`/`#auditToggleBtn` visible despite carrying
  `hidden`, because `.toolbar-btn`'s `display: inline-flex` outranks the UA
  stylesheet — so `applyRoleChrome()`'s role gate had been visually inert.
  Pre-existing, unrelated to the toggle, found only because the verification
  read computed style rather than source.
- **PR 2** — shipped as #1080 (`claude/console-first-run`). Items #1 and #2
  turned out to be one change: the first-run screen and the health chip share
  the same `/index/status` state machine.
- **PR 3** — shipped as the PR carrying this update
  (`claude/console-error-copy-model-badge`). `QueryResponse.llm_model`
  (approved, additive) carries the resolved tag from `graph._llm_identity` --
  `model_used` keeps its role vocabulary untouched, `metrics.py` unaffected.
  `_confirm_choices` and the three client-side "Offline Best Effort" strings
  now name the real local model instead of an opaque label. Added a 14-entry
  `ERROR_COPY` table in terminal.js (the ~10 HTTPException codes reachable from
  `/query` plus the 4 that arrive as a 200 with an `error` field) with a
  parallel `extractErrorCode` so the code stays visible in small print beside
  the plain-language sentence, never discarded.
