# Unslop Integration Plan — Offline Prose-Quality Rail for the Agentic Loop

**Status:** planning only, no code written. **Date:** 2026-08-21. **Scope:** a new,
default-off, fully-offline observability+nudge rail on the local model's output
inside `agentic/real_repo_loop.py`. Nothing in this plan has been implemented yet;
this document is the deliverable for this task.

## Context

CyClaw's agentic loop supports a two-stage "cloud plans, local implements" recipe
(`docs/agentic/AGENTIC_README.md`): an online provider (Grok or Claude) can produce
an implementation plan via `agentic.cli`'s `real-repo-run-plan --provider {grok,claude}`,
a human reviews and approves that plan as a file on disk, and then `real-repo-run
--plan-file <path>` runs the actual patch/verify/commit loop entirely against the
local model (`qwen3.8:27b-mlx` via Ollama). From that point forward the loop never
talks to the network again — every iteration's patch proposal, and any prose
explaining it, comes from the local model alone.

The request behind this plan: once control has passed to the local model for "the
remainder of the offline cached loop," add a check on the quality of its prose —
specifically, detect and flag the kind of AI-writing tells (filler openers, false
agency, listicle rhythm, moralizing codas — collectively "slop") that a smaller
locally-hosted model is more prone to than the stronger cloud model that may have
written the plan it's implementing. The suggested source is
[theclaymethod/unslop](https://github.com/theclaymethod/unslop), an MIT-licensed,
stdlib-only slop-detection toolkit. This document plans how to wire a minimal,
non-blocking, fully-offline slice of that toolkit into the existing loop as CyClaw's
third such rail (alongside the injection/malicious-shape governance scan already in
this loop, and the separate NeMo-Guardrails-based input/output rails on the live RAG
query path) without adding any new runtime dependency, network call, or violation of
the six invariants in `CLAUDE.md` §3.

## Decisions Already Made

Three design forks were resolved with the user before this plan was written
(via `AskUserQuestion`), and this plan does not re-litigate them:

1. **Detect + log + nudge only — no auto-rewrite.** No second LLM call, no
   automatic text replacement. The rail is non-blocking and fail-open, matching the
   existing precedent of the Phase-4 output/grounding guardrail rail in `graph.py`
   (`guardrail_output_node`, `graph.py:460`), which changes only what gets logged,
   never which node runs next.
2. **Fires whenever the loop's proposer is local**, regardless of whether a cloud
   plan preceded that run. This matches the existing precedent in `graph.py:460`
   (`state.get("answer_model") != "local"`), which keys off which model produced the
   text, not the query's history. It also needs no new state to track "did an online
   plan precede this run" — the local/cloud choice is already a hard branch in
   `agentic/cli.py`, not a runtime flag.
3. **unslop's detection scripts will be vendored** into this repository under MIT
   attribution, since unslop has no PyPI package. This was flagged as a High-tier,
   stop-and-ask decision under `CLAUDE.md` §7 (equivalent in consequence to adding a
   new runtime dependency, and the code would sit adjacent to the agentic write
   path); the user approved vendoring a minimal detection-only subset.

## What unslop Actually Is

Verified directly against the upstream repository (README plus three of its scripts
fetched and read in full — the GitHub API directory listing itself returned HTTP 403
during this research and was not used; nothing below is inferred from a page that
could not be fetched).

- **License:** MIT. **Dependencies:** Python 3.8+ stdlib only for the scripts this
  plan uses — no third-party pip packages, no network calls in any of the three
  scripts read.
- **`scripts/banned_phrase_scan.py`** (~750 lines): regex/heuristic detector for
  ~30 literal banned phrases (e.g. "treasure trove", "as an AI language model") plus
  jargon collocations, with a domain-aware false-positive suppression layer (legal/
  medical/historical-narrative contexts) and code-block/backtick masking so it does
  not flag code syntax. JSON output: `total_violations`, `by_severity`,
  `by_category`, and a `violations` list with `phrase`/`category`/`severity`/
  `line_number`/`column`/`context`/`suggestion`. Emits `non_english: true` and
  **exits 0** (its "clean" exit code) when language detection fails — a caller that
  only checks the exit code will read a skipped scan as a clean one.
- **`scripts/structure_scan.py`**: ~36 structural pattern detectors (sentence-rhythm
  uniformity, listicle overuse, connective scaffolding, moralizing codas).
- **`scripts/suggest.py`**: wraps both scanners into one JSON document (`document`,
  `suggestions[]` with `span`/`severity`/`category`/`rationale`/
  `suggested_replacement`/`phrased_as_question`, and `counts`). Critically,
  **it never generates a replacement itself** — `suggested_replacement` is always
  emitted as `null`; the docstring says replacement generation is "delegated to a
  stronger model," but that delegation is entirely external to the script (a
  `--apply-replacements FILE` flag merges in replacement text some other caller
  produced). There is no bundled LLM client anywhere in this script.
- **Not used by this plan:** `check_suggestions.py` and `validate_preservation.py`
  (only needed to validate auto-applied rewrites, which decision 1 above rules out
  for now), `wiki_sync.py` (makes network calls to Wikipedia — incompatible with
  CyClaw's offline-first posture), and every voice-profile/calibration script
  (`voice_profile.py`, `voice_card.py`, `voice_score.py`, `calibrate_pairs.py`,
  `harvest_samples.py` — a stylometric-mimicry feature set unrelated to slop
  detection).

The practical upshot: unslop's detection half is exactly as offline as CyClaw's own
sanitizer or governance scanners. There is no online model, no API key, and no I3
triple-gate question raised by using it — the only new question this plan needs to
answer is where in the local model's own text this offline scan should run.

## Architecture: Hook Placement and Signature

The natural-seeming template for a new optional rail is `utils/guardrail_bridge.py`
(the factory that builds the NeMo-Guardrails input/output rail closures for
`graph.py`), but that module lives in `utils/` specifically because both `gate.py`
and `graph.py` — two of the six core modules `CLAUDE.md`'s I6 invariant forbids from
importing `agentic/`, `guardrails/`, etc. directly — need to reach it, and `utils/`
is a module both already trust. That constraint does not apply here: every file this
new rail touches (`agentic/real_repo_loop.py`, `agentic/cli.py`) is already inside
`agentic/`, which sits entirely outside the isolated core six. `utils/` never
imports `agentic/` (confirmed: no `import agentic` / `from agentic` in `utils/`
anywhere in the tree), so a bridge module placed in `utils/` could not import the
vendored `agentic/vendor/unslop/` scripts without breaking that one-way layering.
**The bridge module belongs at `agentic/unslop_bridge.py`, not in `utils/`.** This
also means the feature never has to cross an I6 boundary at all — simpler than the
guardrails integration it superficially resembles.

A second, genuinely simpler alternative exists and should be named rather than
silently passed over: `utils/numbat_emitter.py`'s `emit_numbat_event()` (called
directly from `agentic/real_repo_loop.py:1081` and `:1126`) is a plain function that
never raises and checks its own enable flag internally — no factory, no injected
callable, no new parameter needed anywhere. That shape is rejected here for two
concrete reasons: (a) it has no way to structurally restrict itself to "only when
the proposer is local" the way a callable built once in `agentic/cli.py`'s
local-only branch can — a self-checking emitter would need a new runtime flag
threaded in just to recover what the call site already knows for free; (b) building
the phrase-scanner's compiled regex tables once per run (in the bridge factory)
rather than once per iteration (inside a self-checking function called every loop
turn) avoids repeated recompilation. This tradeoff should be stated in the bridge
module's own docstring, matching how `utils/guardrail_bridge.py`'s docstring
explains its own placement.

Proposed signature, added to `run_real_repo_loop`'s existing keyword-only parameter
list (`agentic/real_repo_loop.py:701-720`, which already ends with `cfg: dict | None
= None` at line 720 — this rail adds one line after it, not a new threading path):

```python
unslop_probe: Callable[[str, Mapping[str, str], int], dict[str, Any]] | None = None,
```

Called with `(response_text, proposed_files, step)`; returns `{}` to mean "nothing
to report," or `{"nudge": str, "counts": {...}}` when it found something. `None`
(the default) means pass-through, exactly like `build_input_guard`/
`build_output_guard` returning `None` when `guardrails.enabled` is not `True`
(`utils/guardrail_bridge.py:36-37,58-59`).

**One call site.** `run_real_repo_loop`'s per-iteration loop already computes both
of this callable's inputs at a specific point: `response = client.invoke(...)` at
`agentic/real_repo_loop.py:843` and `proposed_files = _parse_file_blocks(response.content)`
at `:851`. The probe call goes immediately after that `try`/`except` block, before
the governance-scan loop that starts at `:873`. This placement observes every
iteration — including ones later quarantined by the critical-finding gate at `:883`
and `:899` (a post-write hook would miss those) — and adds nothing to the
scan-before-write quarantine region CyClaw's own comments (`:863-872`) describe as
deliberately ordered; the probe call must not be interleaved into that region.

**Built in `agentic/cli.py`.** `cmd_real_repo_run` (`agentic/cli.py:651-938`)
constructs `LocalProposerClient` in its non-cloud branch (`:851-857`), with the
loaded app config already available (`:688`). The bridge factory is called there,
and the resulting callable (or `None`) is passed to `run_real_repo_loop(...,
unslop_probe=probe)` alongside the existing `cfg=cfg` argument at `:877`. The cloud
branch (`:846-851`, used only by `real-repo-run-plan`, a different subcommand from
the iterating loop) never constructs it. This is a stronger scoping mechanism than
`graph.py:460`'s runtime `state.get("answer_model") != "local"` check: here the
branch a run takes **is** the discriminator, so there is no possibility of a
misconfigured flag arming the probe against cloud-generated text.

## What Gets Scanned (Prose-Surface Selection)

unslop detects prose slop ("Let's dive in," listicle rhythm, moralizing codas).
`agentic/real_repo_loop.py`'s per-iteration artifact is mostly Python source
(`proposed_files: dict[str, str]`, path → whole new file body) — pointing a prose
scanner at raw `.py` bodies wholesale would mostly flag identifiers and produce
noise, not signal. This plan scans two surfaces in v1, defers a third, and
explicitly excludes several others:

1. **The model's response prose outside the file blocks (highest-value surface).**
   `PLANNER_SYSTEM_PROMPT` (`agentic/real_repo_loop.py:208-215`) tells the model
   explicitly: "Any text outside those blocks is rationale, not code." That text is
   the complement of the spans `_FILE_BLOCK_RE` (`:163-166`) matches over the
   CRLF-normalized response (`:466`, inside `_parse_file_blocks`). It is
   protocol-declared prose, it never touches the repository even if flagged, and its
   line numbers are the response's own — the cheapest and safest surface to start
   with.
2. **Whole `.md`/`.rst`/`.txt` bodies inside `proposed_files`.** These are the
   surface that actually gets committed, so this is where a real prose-quality
   problem in a PR description or docs file would be caught before a human reviews
   it. Known source of noise: `structure_scan.py`'s 36 patterns will also fire
   inside fenced code blocks and Markdown tables within those bodies. Accepted for
   v1 specifically because this rail is log-only — the false-positive rate on this
   surface should be measured from real `logs/unslop.jsonl` data before anyone
   builds a stripper for it.
3. **Deferred: comments and docstrings extracted from `.py` bodies.** If ever
   built, use `tokenize`, not `ast`. CyClaw's own house rule (`CLAUDE.md` §1's
   "Critical Python Coding Requirement": no docstrings as multi-line comments except
   at the top of a file or a function, use `#`-prefixed lines everywhere else) means
   that on CyClaw's own codebase, almost all explanatory prose is `#`-prefixed —
   exactly the surface `ast.get_docstring` cannot see and `tokenize`'s `COMMENT`
   tokens can. A real implementation would need to build one synthetic document of
   extracted comment lines in original order, plus a parallel line-number map, and
   feed that combined document to the scanner rather than scanning each comment
   span individually (avoiding a 750-line scanner invocation per comment). Both
   `ast` and `tokenize` can raise on a model-produced file that doesn't parse, so
   this surface needs its own fail-open handling if it's ever added — reason enough
   to leave it for a phase 2, not fold it into this rail's first version.
4. **Explicitly never scanned, with reasons:** Python code bodies themselves
   (identifiers false-positive against a phrase list built for prose); the approved
   plan text (wrong layer — it is generated once, outside this loop, by
   `generate_real_repo_plan`, `agentic/real_repo_loop.py:638-698`, and on a
   `--provider` run it is the *cloud* model's text, which decision 2 above
   deliberately does not gate on; scanning it inside the per-iteration loop would
   also re-scan identical text on every iteration); `instruction`, `context`, and
   `read_paths` bodies (human- or third-party-authored input, not model output —
   and `context` is explicitly attacker-controlled GitHub-sourced text fenced by
   `_defuse_fence`, so persisting scan findings derived from it would be an
   injection-adjacent surface for zero analytic value); and the loop's own
   `feedback` string (`:990-1007` — CyClaw generated that text itself).

**Non-English handling.** Because `banned_phrase_scan.py` exits 0 (its "clean" code)
on a `non_english: true` result, any wrapper around it must check for that key
explicitly and record a `skipped: "non_english"` outcome rather than trusting the
exit code alone — otherwise every non-English response silently reads as a clean
scan forever.

## The Feedback-Loop Limitation

`run_real_repo_loop`'s `feedback` string starts empty (`agentic/real_repo_loop.py:815`)
and is only ever rebuilt once, on rejection, at `:990-1007` — a list of
`feedback_parts` starting with `decision.reason` (`:990`) and re-injected into the
next iteration's prompt at `:837`. The accepted path returns immediately at
`:975-986`, before that rebuild ever runs. This means a slop finding on a candidate
that gets **accepted on its first try** never reaches the model in any form — there
is no next iteration for the nudge to ride into. This is a real, permanent
limitation of a purely observability-based design, not an oversight to fix: adding
a second code path that could act on a finding after acceptance would make this a
blocking gate, which decision 1 above explicitly ruled out. The correct place to
append a nudge, given this constraint, is as one more entry in the existing
`feedback_parts` list at `:990-1007` (after the write-failure messages already
appended there), not as a separately-injected prompt section — a separate section
would still only ever be seen on iteration N+1, which only exists when some other
gate already rejected iteration N, so appending buys the same reach as a dedicated
prompt slot without adding one.

## Observability: the `logs/unslop.jsonl` Record

A new, separate JSONL stream — not the shared `audit_log()` in `utils/logger.py`,
by the same reasoning `guardrails/metrics.py` keeps its own `logs/guardrails.jsonl`
stream rather than writing into `audit.jsonl`. Shape, one line per scanned surface
per iteration:

```json
{"event": "unslop_scan", "step": 2, "surface": "response_prose",
 "path": null, "doc_sha256": "…", "chars": 4211,
 "counts": {"total": 7, "hard": 2, "soft": 5, "by_category": {"filler_opener": 3}},
 "structure_flags": ["listicle_rhythm"],
 "findings": [{"phrase": "treasure trove", "category": "cliche", "severity": "hard", "line": 18, "column": 4}],
 "truncated": false, "skipped": null, "timestamp": "…"}
```

(`surface` is `"response_prose"` or `"proposed_file"`; `path` is the canonical
proposed-file path — matching the canonicalization `_parse_file_blocks` already
performs, `agentic/real_repo_loop.py:468-475` — and is `null` for the response-prose
surface; `skipped` is `null` or `"non_english"`/`"scanner_error"`.)

The one deliberate redaction rule: **keep `phrase`, drop `context` and
`suggested_replacement`.** A flagged phrase is drawn from a closed vocabulary shipped
inside the scanner itself — logging "treasure trove" discloses nothing about the
repository or the operator, and is the entire analytic value of the stream.
`context` is a window of real model/repository text, and `suggested_replacement` is
always `null` in this design anyway (decision 1). Persisting either would violate
the same no-raw-text discipline `utils/logger.py`'s `audit_log()` already enforces
(`hash_query`/`_redact_value`, `utils/logger.py:219-220,281-299`) and that this exact
loop already states for itself ("hashes, not payloads," `agentic/real_repo_loop.py`'s
own governance-scan commentary). `line`/`column`/`doc_sha256` give a human enough to
find the flagged text in the clone without the log ever holding it directly.

This finding must **not** become a new field on the frozen `RealRepoLoopIteration`
dataclass (`agentic/real_repo_loop.py:554-561`) or an input to
`decide_real_repo_candidate` (`:531-551`). Keeping it out of both is what keeps this
rail observability rather than a gate — the risk of adding it "just as a field" is
that a future change could start reading that field as a rejection reason without
anyone deciding that change out loud.

## Fail-Open Wiring

Three independent layers, matching the fail-open precedent already established
twice in this codebase (`graph.py:433-435` for the input rail, `:480-482` for the
output rail):

1. **Build time**, inside `agentic/unslop_bridge.py`: wrap the vendored import and
   any construction in `try`/`except Exception`, returning `None` on failure. This
   is stricter than `utils/guardrail_bridge.py:39-41`, which lets an `ImportError`
   on the (declared, installed) `guardrails` package escape — justified here
   because the vendored code is not a declared dependency, and a broken or
   partially-synced vendor tree must never be able to abort an entire coding run.
2. **Inside the closure**, around both the vendored scanner call and the
   `logs/unslop.jsonl` append: catch broadly (third-party regex code can raise
   `re.error`, `RecursionError`, `UnicodeDecodeError`; the file-append additionally
   needs `(OSError, TypeError, ValueError)`, matching `guardrails/metrics.py:71-79`),
   log only `type(exc).__name__` (never the scanned text or the exception's own
   message, which could echo it back), and return `{}`.
3. **At the call site** in `run_real_repo_loop`: wrap the `unslop_probe(...)` call
   itself, defaulting to an empty nudge on any exception. The callable is
   caller-supplied, and this loop cannot assume every future caller wrapped it
   correctly — the same belt-and-braces stance `graph.py:433-435` takes for its own,
   in-repo-built guard callables.

## Config: the `unslop` Block

Modeled directly on the existing `guardrails:` block's opt-in discipline
(`utils/guardrail_bridge.py:19-26`: enabled only when the value is the *literal*
boolean `True`, since a YAML `enabled: "false"` typo is a non-empty, and therefore
truthy, string). A new top-level block, ships all-false like every other optional
subsystem in this repository (`guardrails:`, `memory:`, `telegram:`):

```yaml
unslop:
  enabled: false
  metrics_path: "logs/unslop.jsonl"
```

No further sub-keys are proposed. Ponytail's YAGNI rule applies directly here: a
severity threshold, a category allow/deny list, or a scope toggle would each need a
real caller before they earn a place in this block, and none exists yet — "detect +
log + nudge, scoped to local-only" is not a tunable, it's the whole feature as
decided.

## Vendoring: Files, License, Import Rewrite

Vendor target: `agentic/vendor/unslop/`, containing only `banned_phrase_scan.py`,
`structure_scan.py`, `suggest.py`, and the internal `_lang` helper module
`banned_phrase_scan.py` imports (its exact name and contents were not fetched during
this planning pass — the vendoring step must pull and read it before copying, since
this plan cannot vouch for code it has not read). `check_suggestions.py`,
`validate_preservation.py`, `wiki_sync.py`, and every voice-profile/calibration
script are deliberately not vendored (see "What unslop Actually Is" above for why).

**Import rewrite (the one deliberate delta from upstream).** Upstream uses flat,
same-directory imports (`import banned_phrase_scan`, `from _lang import ...`), which
do not work once these files sit inside a Python package. Rewrite to relative
imports (`from . import banned_phrase_scan`, `from ._lang import ...`) rather than
manipulating `sys.path` — a `sys.path` mutation inside code that ships in the
installed wheel is a real smell for no benefit here. This requires
`agentic/vendor/__init__.py` and `agentic/vendor/unslop/__init__.py` (both
docstring-only), and means the vendored `suggest.py` can no longer be run directly
as `python suggest.py`; document `python -m agentic.vendor.unslop.suggest` as its
replacement in the vendor directory's own README.

**License compliance.** No `vendor/` directory and no `LICENSE`/`NOTICE` file exists
anywhere in this repository today — the closest precedent, `.claude/skills/karpathy-guidelines/SKILL.md`,
records only a source URL and an MIT claim in prose, with no copyright text and no
pinned commit, which is adequate for a prose skill file but not for code shipped in
the installed package. Add two files: `agentic/vendor/unslop/LICENSE` (the verbatim
upstream MIT license text, including its copyright line) and
`agentic/vendor/unslop/README.md` (matching the existing shape of
`agentic/README.md`/`agentic/deepagent_github/README.md`/`agentic/harness_optimizer/README.md`),
recording: the upstream repository URL, the exact commit SHA vendored, the vendor
date, the vendored-file list, the exclusion list with reasons, and the import-rewrite
delta described above — so a future re-sync has a mechanical diff to reapply rather
than needing to rediscover this reasoning.

**Dependency manifests: no change anywhere.** `pyproject.toml` dependencies,
`constraints.txt`, `requirements.txt`, and `environment.yml` all pin installable
distributions; vendored source copied directly into the tree is not one, and none of
CyClaw's dependency-guard skills (`dep-guard`, which reads only the pin manifests)
would have anything to check. One caveat: the `verify-deps` skill's E3 check
(`.claude/skills/verify-deps/check_env_drift.py`) AST-walks every `.py` file
including vendored ones, looking for imports outside the pin manifests, and has no
existing skip rule for a `vendor/` path — so it will likely flag whatever the
`_lang` module and the three main scripts import beyond stdlib. This plan could not
verify unslop's full import list (only three files were fetched and read during this
planning pass); confirm it at vendoring time, and prefer dropping a file from the
vendored set over adding a new pip dependency to satisfy it. E3 is advisory
(`warn`, not `fail`) and its CI job already runs with `continue-on-error: true`, so
this cannot block a PR on its own — but it should still be checked, not ignored.

## CI Fallout

Three separate CI mechanisms would touch the vendored files, and only one of them is
handled by the obvious fix:

1. **ruff** (`pyproject.toml`'s `[tool.ruff]`/`[tool.ruff.lint]` blocks,
   `line-length = 120`, `target-version = "py312"`, `select = ["E","F","I","B","C4","UP","S"]`,
   currently `exclude = [".claude"]`). Add `agentic/vendor` to that `exclude` list (or
   use `extend-exclude` to leave the existing list untouched) — the exact shape of
   the existing `.claude` precedent. Third-party code written for Python 3.8 will
   otherwise fail `UP` (pyupgrade) checks near-certainly, plus likely `S`
   (bandit-style) findings on its regex tables. Tradeoff to accept explicitly: the
   excluded tree gets no lint safety net at all, including `F` (undefined names) —
   acceptable specifically because this is unmodified third-party code, not code
   CyClaw authors or maintains line-by-line.
2. **wemake-python-styleguide via flake8, separately from ruff — the blocker ruff's
   fix does not cover.** CyClaw's lint workflow runs flake8 7.3.0 + wemake 1.6.2
   (pinned versions) over exactly the `.py` files changed in a pull request's diff
   against its merge-base, with no `continue-on-error`. Adding ~800+ lines of
   unmodified third-party regex-heavy code in one PR would run wemake's full
   style/complexity ruleset over it and fail the job — `ruff`'s `exclude` has no
   effect on this separate tool. The fix is `setup.cfg`'s `[flake8]` `per-file-ignores`
   list, which already carries this exact pattern for other non-production
   scaffolding (`tests/*.py: WPS, S101, ...`, `.claude/*.py: WPS, E, F, C, B, S`,
   `.codex/*.py: WPS`): add `agentic/vendor/*.py: WPS, E, F, C, B, S` alongside them.
3. **Coverage.** `agentic` is already one of CI's explicit `--cov=` targets, so
   `agentic/vendor/` would be measured automatically and would sit at or near 0%
   coverage (nothing exercises unslop's own internals in this design — CyClaw tests
   the wrapper, not the vendored scanner). Add `"agentic/vendor/*"` to
   `pyproject.toml`'s existing `[tool.coverage.run]` `omit` list, the same shape as
   its two existing `agentic/…/*` entries (`agentic/fsconnect/*`,
   `agentic/sqlconnect/*`). Skipping this could plausibly cost several points off
   the total coverage percentage and trip the 80% `fail_under` gate on an otherwise
   unrelated PR.
4. **mypy:** not CI-wired at all today (confirmed absent from every workflow file;
   `CLAUDE.md` §4 already documents the bare repo-root invocation as broken on
   `utils/errors.py`), so vendored code needs no mypy accommodation.
5. **Packaging:** the build backend (hatchling) already lists `"agentic"` as a
   package by path, so a new `agentic/vendor/unslop/` subdirectory — including its
   `LICENSE` and `README.md` — ships automatically in both the wheel and the sdist
   with no `pyproject.toml` packaging-table edit, once the required `__init__.py`
   files exist (needed anyway for the relative-import rewrite above).
6. **`.gitignore`/`.dockerignore`:** neither file's existing rules match anything
   under an `agentic/vendor/` path; no change needed.

## Invariants and Module Isolation

Walking `CLAUDE.md` §3's six invariants against this design:

- **I1 (RAG-first)** and **I2 (topology=policy)** are untouched — this rail lives
  entirely inside the agentic patch loop, nowhere near `graph.py`'s retrieval/routing
  topology.
- **I3 (triple-gated external fallback)** is not implicated: this design makes zero
  network calls of its own (see "What unslop Actually Is" above), so there is no
  external fallback to gate.
- **I4 (audit convergence)**: not applicable — `agentic/real_repo_loop.py` is not
  part of the `graph.py` node topology I4 governs, and this rail writes to its own
  separate `logs/unslop.jsonl` stream rather than the `audit_logger` node.
- **I5 (soul governance)**: untouched — nothing here reads or writes `soul.md`.
- **I6 (module isolation)**: as established in "Architecture" above, this feature
  never needs to cross the boundary I6 protects. Every file it touches
  (`agentic/real_repo_loop.py`, `agentic/cli.py`, the new `agentic/unslop_bridge.py`,
  and `agentic/vendor/unslop/`) already lives inside `agentic/`, one of the
  out-of-band subsystems I6 lists — it is not one of the six core modules
  (`gate.py`, `gate_ops.py`, `gate_auth.py`, `gate_memory.py`, `graph.py`,
  `mcp_hybrid_server.py`) and does not need to become one. No new import crosses
  from those six core modules into `agentic/`, or from `agentic/` back into them.

## Testing Plan

A new `tests/test_unslop_bridge.py`, matching the shape of the existing
`tests/test_guardrail_bridge.py`: the bridge factory returns `None` when
`unslop.enabled` is absent, `False`, or the non-boolean string `"false"`; returns a
real callable only when it is the literal boolean `True`; the disabled path imports
zero `agentic.vendor.unslop.*` modules (a subprocess-based import-count check, same
technique `test_guardrail_bridge.py` already uses); the built callable correctly
flags a text sample containing a known banned phrase and returns `{}` for a clean
sample; a scanner exception is caught and surfaced as an empty result rather than
propagating; and the `logs/unslop.jsonl` record contains `phrase`/`category`/
`severity`/`line`/`column`/`doc_sha256` but never `context` or
`suggested_replacement`. No change to `pyproject.toml`'s `[tool.coverage.run]`
`source` list is needed for the wrapper itself — `agentic` is already in that list —
only the `omit` addition for the vendored subtree described above.

## Explicitly Out of Scope

Named here so it is a decision, not a silent omission that resurfaces as "wait, why
doesn't this rewrite anything":

- **Active rewriting or auto-correction of flagged text**, including a second local
  qwen call to fill in `suggested_replacement` values and unslop's own
  `check_suggestions.py` four-gate validation of them. This was one of the three
  resolved forks (decision 1) — a real option, deliberately not chosen for v1.
- **Scanning `.py` code bodies**, including their comments and docstrings via
  `tokenize` (see "What Gets Scanned," item 3) — a concrete, scoped phase 2 if the
  measured false-positive rate on the markdown/prose surfaces in v1 turns out low
  enough to justify the added complexity of line-number remapping.
- **Scanning the approved plan text** or gating on "did an online plan precede this
  run" (decision 2) — the local/cloud branch in `agentic/cli.py` is the only gate.
- **`wiki_sync.py`** or any other network-touching part of upstream unslop.
- **Extending this rail to the live RAG query path** (`graph.py`'s `local_llm`
  node) — the user's request and this plan are scoped to the agentic loop only;
  the query path already has its own, separate Phase-4 grounding rail.

## Risks to Monitor

- **False-positive rate on Markdown/docs bodies.** `structure_scan.py`'s 36
  patterns will fire inside fenced code blocks and tables in committed `.md` files.
  This is log-only in v1 specifically so the real rate can be measured from
  `logs/unslop.jsonl` before any filtering logic is built on top of it.
- **The accepted-first-try blind spot** (see "The Feedback-Loop Limitation" above)
  means this rail's practical value is concentrated on multi-iteration runs; a loop
  that converges in one pass produces observability data but no in-loop nudge.
  Acceptable given decision 1, but worth remembering when reading early metrics.
- **Vendor drift.** If upstream unslop changes `banned_phrase_scan.py`'s or
  `suggest.py`'s output schema, CyClaw's copy will not follow automatically — there
  is no update mechanism proposed here beyond the pinned-commit README recording
  what was vendored and when, to make a manual re-sync tractable.
- **The `_lang` helper module's contents and import list are unverified** by this
  planning pass (only three top-level scripts were fetched and read). Read it before
  vendoring; do not assume it is stdlib-only just because the three scripts checked
  are.

## Implementation Checklist

1. Fetch and read `_lang.py` and confirm its imports are stdlib-only; fetch the
   exact upstream commit SHA to pin in the vendor README.
2. Create `agentic/vendor/unslop/` with `__init__.py`, the three scanner scripts
   (import-rewritten to relative imports), `_lang.py`, `LICENSE`, and `README.md`
   as specified above.
3. Add `agentic/unslop_bridge.py`: `_unslop_enabled(cfg)` (literal-`True` check,
   mirroring `utils/guardrail_bridge.py:19-26`) and `build_unslop_probe(cfg) ->
   Callable[[str, Mapping[str, str], int], dict[str, Any]] | None`, lazy-importing
   the vendored scanners only inside the enabled branch, with the fail-open wiring
   from "Fail-Open Wiring" above.
4. Add the `unslop_probe` keyword-only parameter to `run_real_repo_loop`
   (`agentic/real_repo_loop.py:701-720`), the call site after `:851`, and the
   `feedback_parts` append inside the rejection branch (`:990-1007`).
5. Wire construction into `agentic/cli.py`'s `cmd_real_repo_run` (`:851-857`),
   passed only on the local-proposer branch.
6. Add the `unslop:` block to `config.yaml` (all-false).
7. Add `agentic/vendor` to `pyproject.toml`'s ruff `exclude`/`extend-exclude`, add
   `agentic/vendor/*.py: WPS, E, F, C, B, S` to `setup.cfg`'s flake8
   `per-file-ignores`, and add `"agentic/vendor/*"` to `pyproject.toml`'s coverage
   `omit` list.
8. Add `tests/test_unslop_bridge.py` per "Testing Plan" above.
9. Run `python3 .claude/skills/invariant-guard/check_invariants.py`,
   `ruff check --select E,F,I,B,C4,UP,S .`, and
   `GROK_API_KEY=dummy pytest tests/ -q --tb=short` before opening a PR.
10. Open a draft PR with a **Risk to monitor** section covering the false-positive
    and vendor-drift items above.
