# CyClaw Swarm Verification — PR #942 Rebase Audit (2026-08-15)

## Scope

`/CyClaw-Sandbox` thorough run against `claude/cyclaw-otel-langsmith-tracing-v2`
(PR #942, "pin the whole LangSmith tracing namespace, not two of four names")
after rebasing it onto the latest `main` (`eadee85`, which had picked up
PR #940 auth Stage 3-4 and PR #941 CodeQL CI fixes since #942 was cut from
`f561d06`). Rebase was clean — no conflicts, single commit re-applied as
`6d0871e`, force-pushed with `--force-with-lease`.

Environment: fresh container, `python3.12 -m venv` per `CLAUDE.md` §4's
documented trap (bare `python3`/`pytest` on this image resolve to 3.11).
`torch==2.13.0+cpu` installed first, then
`pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML`,
then `pip install -e . -c constraints.txt`.

## Result summary

| Check | Result |
|---|---|
| `GROK_API_KEY=dummy pytest tests/ -q --tb=short` | **PASS** — full suite green, exit 0 (skips only where documented, e.g. Darwin-only tests on Linux) |
| `invariant-guard` (six invariants + G1–G5) | **35 passed, 0 failed** |
| `config-guard` | **0 failures, 1 warning** (C9 — shipped `app.mode=hybrid` + both providers enabled; documented as the eighth threat-model amendment, not a regression) |
| `dep-guard` | **0 failures, 0 warnings** |
| `OTel-Hardening` `check_otel.py` | **0 failures, 0 warnings** — confirms PR #942's fix (all 4 LangSmith/LangChain tracing names pinned) is intact post-rebase |
| `OTel-Hardening` `verify.sh` | **ALL PASS** (7/7 mutation self-tests) |
| `doc-sync` | **2 drift items** — pre-existing, unrelated to #942 (see below) |
| `run_full_verification.py` (in-process swarm) | 157/189 checks — see "False-positive triage" below; every failure traced to the *skill script* being stale against the current architecture, not a product regression |

## False-positive triage — `run_full_verification.py` is stale, not the product

All 32 failing checks in this run trace to four places where the skill
script's assertions predate architecture changes already reflected in
`CLAUDE.md`. Each was independently re-verified against the real source:

1. **BM25/Chroma corpus build (`bm25_index_built`, `chroma_index_built`,
   both downstream query/triple-gate phases)** — the script does
   `from retrieval.stemmer import PorterStemmer` at module scope.
   `retrieval/stemmer.py` deliberately does **not** export `PorterStemmer`
   at module level; it's pulled lazily inside a private `_porter()` helper
   specifically to avoid loading `nltk` into every process
   (`retrieval/stemmer.py:15-28`, matches the module's own documented
   intent). The import error cascades into "BM25 index not found" for the
   5-query and triple-gate phases. This is a script bug, not a retrieval
   regression — `retrieval/indexer.py`'s real path was not exercised by
   this failure.
2. **`sk_ant_in_config_redact`** — the script reads
   `cfg["logging"]["audit"]["redact_secrets_like"]`
   (`run_full_verification.py:857`), a path that does not exist.
   The real key is `policy.privacy.redact_secrets_like`
   (`config.yaml:362`, confirmed present with the `sk-ant-[a-zA-Z0-9_-]{20,}`
   pattern at line 378) — the same file's own line 378 reads the *correct*
   path a few hundred lines earlier, so this looks like copy-paste drift
   within the script itself rather than a config regression.
3. **4 `/ops/*` endpoint-registration checks
   (`endpoint_POST__ops_sync/agentic/fsconnect/sqlconnect`)** — the script
   greps only `gate.py` for the route string literals. Per `CLAUDE.md`'s
   own module table, the four `/ops/*` routes are registered by
   `gate_ops.py`, injected onto `gate.py`'s app — confirmed present at
   `gate_ops.py:117,137,161,185`. The script was not updated for the
   `gate.py`/`gate_ops.py` split.
4. **13 Terminal HTML Contract checks + the button/handler subset repeated
   under Terminal Consoles** (`terminal_grok_button_text`,
   `terminal_claude_button_text`, `terminal_api_soul_*`,
   `terminal_auth_integration`, `terminal_health_poll`, etc.) — the script
   greps only `static/terminal.html` for `authHeaders()`, `/health`,
   `Send to Grok`, `handleConfirm`, etc. The console's JS was split out to
   `static/terminal.js` (`terminal.html:1061-1062` loads
   `/static/auth_admin.js` and `/static/terminal.js`); every one of those
   strings is present and correct in `terminal.js` (confirmed:
   `authHeaders()` at line 75, `/health` poll at line 201, the Grok/Claude
   confirm buttons at lines 498-499 with `handleConfirm` wiring at 510/520,
   `online_provider` in the request body at line 322, all four `/soul/*`
   calls with `authHeaders()` at lines 617-725). The script was not updated
   for the HTML/JS split.

**Recommendation:** file a follow-up chore PR updating
`.claude/skills/CyClaw-Sandbox/run_full_verification.py` to (a) call the
real `retrieval.indexer`/`_porter()` accessor instead of importing
`PorterStemmer` directly, (b) fix the config path in the redaction check,
(c) grep `gate_ops.py` for the `/ops/*` route literals, and (d) grep
`static/terminal.js` (not just `terminal.html`) for the console contract
checks. None of this blocks #942 — it's pre-existing skill-script drift
surfaced by running the full 14-phase procedure instead of Quick Mode.

## `doc-sync` drift (pre-existing, unrelated to #942)

- `/auth/users/{username}/role` is missing from `CLAUDE.md`'s route table.
- `/auth/audit/summary`, `/auth/password`, `/auth/users`,
  `/auth/users/{username}/role` are missing from `setup-guide.md`'s REST
  section.

These routes were added by PR #940 (auth Stage 3-4, merged into `main`
after #942 was originally cut) and the docs were not updated in that PR.
Not touched here — out of scope for a telemetry-only PR; flagging for the
#940 branch/owner rather than fixing inline on #942 to keep this PR's diff
focused per `CLAUDE.md` §6 ("the diff touches only files named in the
task").

## PR #942-specific verification

- `check_otel.py`: T1–T8 all pass against the rebased tree; `_TODAY`
  baseline stamp is 17 days old (within the 120-day freshness window).
- `verify.sh` mutation suite: 7/7 — restoring the pre-fix state (dropping
  `LANGSMITH_TRACING_V2` from the kill dict) is still correctly caught as a
  failure, confirming the new guard is not vacuous after the rebase.
- `invariant-guard` G1 confirms `_TELEMETRY_KILL` (gate.py) still precedes
  the first heavy import by name and position.

## Conclusion

PR #942 rebases cleanly onto current `main` and its fix remains fully
intact and independently verified (OTel-Hardening static + mutation
checks, full pytest, invariant-guard). No new risk introduced by the
rebase. The only findings from this thorough pass are pre-existing and
outside #942's scope: skill-script staleness in `run_full_verification.py`
and two doc-sync drift items from PR #940's auth routes.
