# Unslop Integration Plan — Offline Prose-Quality Rail for the Agentic Loop

**Status:** v1 shipped in [#1029](https://github.com/cgfixit/CyClaw/pull/1029).
**Original plan date:** 2026-08-21. **Status verified:** 2026-09-02 against
`origin/main`. **v1 scope:** a default-off, fully-offline, non-blocking
observability+nudge rail on the local model's output inside
`agentic/real_repo_loop.py`.

V1 is implemented at `agentic/unslop_bridge.py`, wired only by the local-proposer
branch in `agentic/cli.py`, and covered by `tests/test_unslop_bridge.py`. It scans
response prose and proposed `.md`/`.rst`/`.txt` files only when
`unslop.enabled: true`; the shipped configuration is `false`. It logs redacted
findings to `logs/unslop.jsonl` and can add a nudge to existing rejection feedback,
but it never changes the acceptance decision or blocks a run. The vendored scanner
subset remains under `agentic/vendor/unslop/`, so no core module crosses the I6
module-isolation boundary.

The remaining work is measurement and explicitly deferred Phase 2 work, not
implementation of v1: run an opt-in local agentic job, measure false positives from
the redacted metrics, and only then decide whether a separately approved Phase 2
is warranted. Do not enable it by default, re-vendor it, add filters, add rewriting,
or put it on `/query`.

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
stdlib-only slop-detection toolkit. This document records how v1 wires a minimal,
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
   (`guardrail_output_node`), which changes only what gets logged,
   never which node runs next.
2. **Fires whenever the loop's proposer is local**, regardless of whether a cloud
   plan preceded that run. This matches the existing precedent in `graph.py`
   (`state.get("answer_model") != "local"`), which keys off which model produced the
   text, not the query's history. It also needs no new state to track "did an online
   plan precede this run" — the local/cloud choice is already a hard branch in
   `agentic/cli.py`, not a runtime flag.
3. **unslop's detection scripts are vendored** into this repository under MIT
   attribution, since unslop has no PyPI package. This was a High-tier, stop-and-ask
   decision under `CLAUDE.md` §7 (equivalent in consequence to adding a new runtime
   dependency, and the code sits adjacent to the agentic write path); the approved
   minimal detection-only subset shipped with v1.

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
triple-gate question raised by using it. V1 runs the offline scan only against the
local model's own text in the agentic loop.

## Shipped v1 Architecture: Hook Placement and Signature

The natural-seeming template for a new optional rail is `utils/guardrail_bridge.py`
(the factory that builds the NeMo-Guardrails input/output rail closures for
`graph.py`), but that module lives in `utils/` specifically because both `gate.py`
and `graph.py` — two of the six core modules `CLAUDE.md`'s I6 invariant forbids from
importing `agentic/`, `guardrails/`, etc. directly — need to reach it, and `utils/`
is a module both already trust. That constraint does not apply here: every file the
v1 rail touches (`agentic/real_repo_loop.py`, `agentic/cli.py`) is already inside
`agentic/`, which sits entirely outside the isolated core six. `utils/` never
imports `agentic/` (confirmed: no `import agentic` / `from agentic` in `utils/`
anywhere in the tree), so a bridge module placed in `utils/` could not import the
vendored `agentic/vendor/unslop/` scripts without breaking that one-way layering.
**The bridge module belongs at `agentic/unslop_bridge.py`, not in `utils/`.** This
also means the feature never has to cross an I6 boundary at all — simpler than the
guardrails integration it superficially resembles.

A second, genuinely simpler alternative exists and should be named rather than
silently passed over: `utils/numbat_emitter.py`'s `emit_numbat_event()` (called
directly from `agentic/real_repo_loop.py`) is a plain function that
never raises and checks its own enable flag internally — no factory, no injected
callable, no new parameter needed anywhere. That shape is rejected here for two
concrete reasons: (a) it has no way to structurally restrict itself to "only when
the proposer is local" the way a callable built once in `agentic/cli.py`'s
local-only branch can — a self-checking emitter would need a new runtime flag
threaded in just to recover what the call site already knows for free; (b) building
the phrase-scanner's compiled regex tables once per run (in the bridge factory)
rather than once per iteration (inside a self-checking function called every loop
turn) avoids repeated recompilation. V1 adopts that tradeoff while keeping the bridge
inside `agentic/`, matching the module-isolation rationale in its docstring.

V1 adds this signature to `run_real_repo_loop`'s keyword-only parameter list
(`agentic/real_repo_loop.py`):

```python
unslop_probe: Callable[[str, Mapping[str, str], int], dict[str, Any]] | None = None,
```

Called with `(response_text, proposed_files, step)`; returns `{}` to mean "nothing
to report," or `{"nudge": str, "counts": {...}}` when it found something. `None`
(the default) means pass-through, exactly like `build_input_guard`/
`build_output_guard` returning `None` when `guardrails.enabled` is not `True`
(`utils/guardrail_bridge.py`).

**One call site.** `run_real_repo_loop` computes both callable inputs per iteration:
`response = client.invoke(...)` and `proposed_files = _parse_file_blocks(response.content)`.
V1 invokes the probe immediately after parsing and before the governance scan. This
placement observes candidates that the governance gate later quarantines, while
leaving the scan-before-write quarantine ordering intact.

**Built in `agentic/cli.py`.** `cmd_real_repo_run` constructs `LocalProposerClient`
in its non-cloud branch, calls `build_unslop_probe(app_cfg)` there, and passes the
resulting callable (or `None`) to `run_real_repo_loop(..., unslop_probe=probe)`.
The cloud branch, used only by `real-repo-run-plan`, never constructs it. This is a
stronger scoping mechanism than
`graph.py`'s runtime `state.get("answer_model") != "local"` check: here the
branch a run takes **is** the discriminator, so there is no possibility of a
misconfigured flag arming the probe against cloud-generated text.

## What Gets Scanned (Prose-Surface Selection)

unslop detects prose slop ("Let's dive in," listicle rhythm, moralizing codas).
`agentic/real_repo_loop.py`'s per-iteration artifact is mostly Python source
(`proposed_files: dict[str, str]`, path → whole new file body) — pointing a prose
scanner at raw `.py` bodies wholesale would mostly flag identifiers and produce
noise, not signal. V1 scans two surfaces, defers a third, and explicitly excludes
several others:

1. **The model's response prose outside the file blocks (highest-value surface).**
   `PLANNER_SYSTEM_PROMPT` in `agentic/real_repo_loop.py` tells the model
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
   `generate_real_repo_plan` in `agentic/real_repo_loop.py`, and on a
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

`run_real_repo_loop` rebuilds `feedback` only after rejecting a candidate and
re-injects it into the next iteration. An accepted-first-try candidate therefore
produces observability data but no in-loop nudge. This is a deliberate limitation of
the non-blocking design: adding a post-acceptance action would turn the rail into a
gate. V1 appends any nudge to the existing rejection `feedback_parts`, rather than
creating another prompt path with the same next-iteration-only reach.

## Observability: the `logs/unslop.jsonl` Record

V1 uses a separate JSONL stream — not the shared `audit_log()` in `utils/logger.py`,
by the same reasoning `guardrails/metrics.py` keeps its own `logs/guardrails.jsonl`
stream rather than writing into `audit.jsonl`. Shape, one line per scanned surface
per iteration:

```json
{"event": "unslop_scan", "step": 2, "surface": "response_prose",
 "path": null, "doc_sha256": "…", "chars": 4211,
 "counts": {"total": 7, "hard": 2, "soft": 5, "by_category": {"filler_opener": 3}},
 "structure_flags": ["listicle_rhythm"],
 "findings": [{"phrase": "treasure trove", "category": "cliche", "severity": "hard", "line": 18, "column": 4}],
 "skipped": null, "timestamp": "…"}
```

(`surface` is `"response_prose"` or `"proposed_file"`; `path` is the canonical
proposed-file path — matching the canonicalization `_parse_file_blocks` performs in
`agentic/real_repo_loop.py` — and is `null` for the response-prose
surface; `skipped` is `null` or `"non_english"`/`"scanner_error"`.)

The one deliberate redaction rule: **keep `phrase`, drop `context` and
`suggested_replacement`.** A flagged phrase is drawn from a closed vocabulary shipped
inside the scanner itself — logging "treasure trove" discloses nothing about the
repository or the operator, and is the entire analytic value of the stream.
`context` is a window of real model/repository text, and `suggested_replacement` is
always `null` in this design anyway (decision 1). Persisting either would violate
the same no-raw-text discipline `utils/logger.py`'s `audit_log()` already enforces
(`hash_query`/`_redact_value`) and that this exact
loop already states for itself ("hashes, not payloads," `agentic/real_repo_loop.py`'s
own governance-scan commentary). `line`/`column`/`doc_sha256` give a human enough to
find the flagged text in the clone without the log ever holding it directly.

This finding must **not** become a new field on the frozen `RealRepoLoopIteration`
dataclass in `agentic/real_repo_loop.py` or an input to
`decide_real_repo_candidate`. Keeping it out of both is what keeps this
rail observability rather than a gate — the risk of adding it "just as a field" is
that a future change could start reading that field as a rejection reason without
anyone deciding that change out loud.

## Fail-Open Wiring

Three independent layers, matching the fail-open precedent already established
twice in this codebase (`graph.py` for the input and output rails):

1. **Build time**, inside `agentic/unslop_bridge.py`: wrap the vendored import and
   any construction in `try`/`except Exception`, returning `None` on failure. This
   is stricter than `utils/guardrail_bridge.py`, which lets an `ImportError`
   on the (declared, installed) `guardrails` package escape — justified here
   because the vendored code is not a declared dependency, and a broken or
   partially-synced vendor tree must never be able to abort an entire coding run.
2. **Inside the closure**, around both the vendored scanner call and the
   `logs/unslop.jsonl` append: catch broadly (third-party regex code can raise
   `re.error`, `RecursionError`, `UnicodeDecodeError`; the file-append additionally
   needs `(OSError, TypeError, ValueError)`, matching `guardrails/metrics.py`),
   log only `type(exc).__name__` (never the scanned text or the exception's own
   message, which could echo it back), and return `{}`.
3. **At the call site** in `run_real_repo_loop`: wrap the `unslop_probe(...)` call
   itself, defaulting to an empty nudge on any exception. The callable is
   caller-supplied, and this loop cannot assume every future caller wrapped it
   correctly — the same belt-and-braces stance `graph.py` takes for its own,
   in-repo-built guard callables.

## Config: the `unslop` Block

Modeled directly on the existing `guardrails:` block's opt-in discipline
(`utils/guardrail_bridge.py`: enabled only when the value is the *literal*
boolean `True`, since a YAML `enabled: "false"` typo is a non-empty, and therefore
truthy, string). The v1 top-level block ships disabled like every other optional
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

V1 vendors a fixed detection-only subset at `agentic/vendor/unslop/`:
`banned_phrase_scan.py`, `structure_scan.py`, `suggest.py`, `_lang.py`, and
`readability_metrics.py`, plus `LICENSE`, `README.md`, and package initializers.
The vendor README records upstream commit
`d81f5196167ded24f46fced04958c0c12d681798`, the 2026-08-21 vendor date, the MIT
license, file list, exclusions, and import-rewrite delta.

**Import rewrite.** The shipped files use relative imports so they are importable as
`agentic.vendor.unslop` without `sys.path` mutation. They are never imported while
`unslop.enabled` is false.

`check_suggestions.py`, `validate_preservation.py`, `wiki_sync.py`, and every
voice-profile/calibration script remain deliberately excluded. In particular, v1
does not vendor network-touching `wiki_sync.py` or any auto-rewrite support.

**Dependency manifests:** unchanged. Vendored stdlib-only source is not an
installable dependency, and v1 adds no package, API key, or network call.

## CI Fallout

V1 includes the required third-party-source accommodations:

1. `pyproject.toml` excludes `agentic/vendor` from Ruff and
   `agentic/vendor/*` from coverage.
2. `setup.cfg` grants `agentic/vendor/*.py` the existing third-party flake8/WPS
   per-file ignores.
3. Hatchling already packages `agentic/`; the vendored package initializers, license,
   and README therefore ship without a packaging-table change.

These exclusions apply to the vendored scanners, not the bridge or its tests.

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
  never crosses the boundary I6 protects. Every shipped file it touches
  (`agentic/real_repo_loop.py`, `agentic/cli.py`, `agentic/unslop_bridge.py`, and
  `agentic/vendor/unslop/`) already lives inside `agentic/`, one of the
  out-of-band subsystems I6 lists — it is not one of the six core modules
  (`gate.py`, `gate_ops.py`, `gate_auth.py`, `gate_memory.py`, `graph.py`,
  `mcp_hybrid_server.py`) and does not need to become one. No new import crosses
  from those six core modules into `agentic/`, or from `agentic/` back into them.

## Shipped v1 Tests

`tests/test_unslop_bridge.py` verifies that the bridge is disabled when the block is
absent, false, or a non-boolean string; it imports no vendored scanner when disabled;
and it creates a callable only for literal `true`. It also covers response-prose and
Markdown scanning, Python-body exclusion, non-English skipping, redacted metrics,
and scanner exceptions returning `{}`. The wrapper remains in coverage; only the
vendored subtree is omitted.

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
  is no automatic update mechanism beyond the pinned-commit README recording
  what was vendored and when, to make a manual re-sync tractable.

## V1 Delivery Record

[#1029](https://github.com/cgfixit/CyClaw/pull/1029) delivered the vendored
offline scanners, `agentic/unslop_bridge.py`, the local-proposer-only probe wiring,
the all-false `unslop:` configuration block, third-party-source CI accommodations,
and `tests/test_unslop_bridge.py`. This document’s former implementation checklist
is complete; it is retained above as design rationale rather than pending work.

## Remaining Measurement and Phase 2 (Not v1 Work)

1. An operator may run a deliberately opted-in local agentic job and review the
   redacted `logs/unslop.jsonl` records to establish the Markdown/prose
   false-positive rate. This status update does not claim that measurement was run.
2. Keep `unslop.enabled: false` by default throughout measurement. The rail remains
   offline, fail-open, and non-blocking, and stays outside `/query` and the six I6
   core modules.
3. Only evidence from that measurement can justify a separately approved Phase 2,
   such as `tokenize`-based extraction of Python comments/docstrings with a
   line-number map. Do not add filtering, rewriting, new scanner surfaces, or a
   re-vendor as part of measurement.
