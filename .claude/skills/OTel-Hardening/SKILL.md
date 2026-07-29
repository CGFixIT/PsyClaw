---
name: OTel-Hardening
description: >-
  Re-verify that CyClaw's telemetry-kill switches (utils/telemetry_kill.py, the
  conditional HF Hub offline wiring in retrieval/embeddings.py) still actually
  block every telemetry path for the dependency vendors CyClaw ships —
  chromadb, langchain/langsmith/langgraph, huggingface_hub/transformers,
  sentence-transformers, onnxruntime, opentelemetry, nemoguardrails — by
  statically re-checking the kill dict plus a live web/forum search for
  vendor-side drift since the last verified date, then proposing or applying
  additive, low-risk kill-switch fixes when a real gap is found. Use when
  asked to audit/harden/re-verify telemetry, check for phone-home leaks, after
  bumping any of the vendor pins above, or periodically as a standing
  "did a vendor change its telemetry contract on us" sweep — CyClaw's threat
  model forbids telemetry outright, so this gap doesn't announce itself.
---

# OTel-Hardening

**Persona:** You are a privacy/telemetry auditor for CyClaw with one question:
*does every dependency this stack ships still have its phone-home path
verifiably cut off, using CURRENT vendor behavior, not a stale memory of it?*
You do not review general code quality or unrelated security surface — other
skills (`invariant-guard`, `dep-guard`, `config-guard`) do that.

**Why this skill exists:** CyClaw's threat model
(`docs/THREAT_MODEL.md`) states plainly that nothing in this stack may phone
home. The actual enforcement is a fixed environment-variable block
(`utils/telemetry_kill.py`) applied before any SDK import, plus one
deliberately conditional pair (`HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` in
`retrieval/embeddings.py`). Both were verified against each vendor's *actual
installed source* on a specific date — not against a general assumption, and
not continuously. A vendor can rename an env var, add a new telemetry
mechanism, or change a default in a later release without any of CyClaw's own
files changing, so a clean `git diff` proves nothing about whether the kill
switches still work against what's *actually pinned today*. This skill is the
standing process that re-asks the question instead of assuming the last answer
still holds.

**Worked precedent (2026-07-29):** the first full run of this process found
that `HF_HUB_DISABLE_TELEMETRY=1` and `DO_NOT_TRACK=1` were both missing from
the unconditional kill dict. Verified by reading huggingface_hub's own
`utils/_telemetry.py` that `HF_HUB_DISABLE_TELEMETRY` only gates a background
HEAD request to `{ENDPOINT}/api/telemetry/{topic}` — a code path entirely
separate from file downloads or cache lookups — so, unlike `HF_HUB_OFFLINE`,
it carries none of the first-run-bootstrap risk that keeps that pair
conditional, and is safe to add unconditionally. `DO_NOT_TRACK` was confirmed
for NeMo Guardrails (NVIDIA's own docs) but sources disagreed on whether
huggingface_hub's current release honors it — added anyway as a harmless
belt-and-suspenders convention, documented as unconfirmed rather than
asserted. The same pass also caught a real, pre-existing doc/code drift: the
reference file `docs/security-philosophy/cyclaw_telemetry_kill.env` was
missing `NEMO_GUARDRAILS_NO_USAGE_STATS`, which had been in the code's kill
dict since PR #703 merged. Both fixes landed in `utils/telemetry_kill.py`,
`tests/test_telemetry_kill.py`, and the reference `.env` file — read those
diffs as the template for what "an enhancement from this skill" looks like:
additive, narrowly scoped, precisely sourced, never removing an existing var.

---

## Run

### Step 1 — Deterministic checker (stdlib only, ~1 second)

```bash
python3 .claude/skills/OTel-Hardening/check_otel.py
```

Static only — it AST-parses `utils/telemetry_kill.py` (never imports it, so
running the checker has zero `os.environ` side effects), diffs the vendor pins
in `pyproject.toml` against a baseline recorded inside the script, and checks
the reference `.env` doc hasn't drifted from the code. Exit codes follow the
repo convention: `0` contract holds · `2` a FAIL check tripped · `3`
env/config error. Add `--strict` to escalate every `WARN` to a failure.

A `WARN` here does **not** mean telemetry is leaking — it means a vendor pin
moved past the version last verified against real source, which is exactly
the prompt to do Step 2 for that vendor. A `FAIL` means something the process
had already verified regressed (a key vanished, the HF Hub conditional wiring
disappeared, credential-scrubbing shrank) — that is a real, provable defect,
fix it before anything else.

### Step 2 — Live vendor-doc sweep (needs web search)

For each vendor `check_otel.py` flagged as pin-drifted (`WARN [T5]`) or listed
as installed-but-unverified (`info [T6]`, since those have no direct pin to
diff), search for whether that vendor's *current* release changed its
telemetry env-var contract. Concretely, for each vendor this skill targets,
the question to answer and verify (don't just pattern-match a search
snippet — read the actual source or an official doc page, the way the
worked precedent did for huggingface_hub):

| Vendor | What to re-verify |
|---|---|
| `chromadb` | Does `chroma_otel_granularity` still default to unset/`None`, and does `otel_init()` still early-return only on literal `"none"`? Does `ANONYMIZED_TELEMETRY` still gate the separate PostHog path via `Settings(anonymized_telemetry=...)`? |
| `langchain` / `langchain-core` / `langgraph` | Are `LANGCHAIN_TRACING_V2` and `LANGSMITH_TRACING` still both live var names (LangChain has renamed tracing vars before)? Any new LangSmith-branded var superseding both? |
| `nemoguardrails` | Is `NEMO_GUARDRAILS_NO_USAGE_STATS` still read, and does `DO_NOT_TRACK` / the `~/.config/nemoguardrails/do_not_track` file convention still apply? |
| `huggingface_hub` / `transformers` | Re-read (don't assume) `utils/_telemetry.py` (or its current equivalent) for what triggers a network call and which env vars gate it. Confirm `HF_HUB_DISABLE_TELEMETRY` still only affects the telemetry ping, not downloads — if that ever changed, the "safe unconditionally" reasoning in `utils/telemetry_kill.py`'s docstring would need to be revisited. |
| `sentence-transformers` | Still no telemetry mechanism of its own (confirmed as of the worked precedent)? It calls into `huggingface_hub` for any network activity, so a change there is really a `huggingface_hub` change. |
| `onnxruntime` | Is `ORT_TELEMETRY_OPT_OUT` still unread (grep the installed package for the literal string)? Is `onnxruntime.disable_telemetry_events()` still the real API, and is telemetry still Windows-official-build-only per its `Privacy.md`? |
| `opentelemetry-sdk` | Do `OTEL_SDK_DISABLED` / `OTEL_TRACES_EXPORTER=none` / `OTEL_METRICS_EXPORTER=none` / `OTEL_LOGS_EXPORTER=none` still fully disable the SDK per the current OTel spec? |

Prefer reading the vendor's actual installed source (`pip show -f <pkg>` to
find it, then read the relevant module) or its official docs page over a
search-engine AI summary — the worked precedent found sources that
*disagreed* with each other on `DO_NOT_TRACK`, and the only way to resolve
that was reading the real `_telemetry.py`. If sources still conflict after
checking, say so explicitly rather than picking one silently — this mirrors
the general discipline in `.claude/skills/fable-protocol/SKILL.md` §3.3.

### Step 3 — Propose or apply the fix

If Step 2 surfaces a genuine gap (a new unconditional-safe var to add, a
renamed var, a vendor default that changed in CyClaw's disfavor):

- **Additive and unconditional-safe → apply directly.** Add the key to
  `TELEMETRY_KILL` in `utils/telemetry_kill.py` with a comment explaining
  exactly what it does and why it's safe unconditionally (cite the source you
  read, the way the worked precedent's comment does). Update
  `BASELINE_KEYS` in `check_otel.py`, add a test to
  `tests/test_telemetry_kill.py` (both a dedicated per-var test and, if the
  var should be exercised under a hostile ambient environment, an entry in
  `_HOSTILE_ENV`), and add the var to the reference
  `docs/security-philosophy/cyclaw_telemetry_kill.env`.
- **Would break something if unconditional (e.g. blocks a real network call
  needed for first-run bootstrap, the way `HF_HUB_OFFLINE` does) → do not add
  it to the unconditional dict.** Follow the same conditional pattern
  `retrieval/embeddings.py::_model_offline_eligible` already uses, or stop and
  ask the user (CLAUDE.md §7 High tier: this is exactly the kind of tradeoff
  that needs a human decision, as it was the last time this came up).
  Never weaken or remove an existing kill var to work around a vendor
  change without explicit user approval — the direction of error here should
  always favor over-blocking network calls, never under-blocking them.
- After any change, update the `LAST_VERIFIED_VENDOR_PINS` entry (if that
  vendor's pin was the trigger) and the `Verified <date>` stamp inside
  `utils/telemetry_kill.py`'s relevant comment block, so `check_otel.py`'s
  staleness check (T4) reflects the new verification date rather than the old
  one.

### Step 4 — Re-run and validate

```bash
python3 .claude/skills/OTel-Hardening/check_otel.py
GROK_API_KEY=dummy pytest tests/test_telemetry_kill.py tests/test_embeddings.py -q
python3 .claude/skills/invariant-guard/check_invariants.py
ruff check --select E,F,I,B,C4,UP,S utils/telemetry_kill.py tests/test_telemetry_kill.py
```

`invariant-guard` matters here specifically for G1: `gate.py` must keep its
`_TELEMETRY_KILL = apply_telemetry_kill()` binding, since that guard finds it
by name via AST and checks it precedes the first heavy import. Nothing in
this skill should ever touch that binding's name or position.

If no gap is found in Step 2 for any flagged vendor, **say so explicitly and
stop** — do not manufacture a change to have something to show. A clean sweep
is a valid, useful outcome; it's the point of running the process regularly.

---

## Guardrails (CyClaw invariants — do not violate)

- **I6 module isolation.** `utils/telemetry_kill.py` and every file this skill
  touches must continue to avoid importing `agentic`/`sync`/`guardrails`.
- **G1 (telemetry-kill precedes heavy imports).** Never rename or move
  `gate.py`'s `_TELEMETRY_KILL = apply_telemetry_kill()` binding —
  `invariant-guard` finds it by exact name via AST.
- **Never make `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` unconditional.** That
  reintroduces the first-run bootstrap failure the conditional split in
  `retrieval/embeddings.py` was written to avoid. If Step 2 finds a vendor
  telemetry var with similar bootstrap risk, it gets the same conditional
  treatment or a stop-and-ask, never a blind add to `TELEMETRY_KILL`.
- **Additive only, by default.** Removing or weakening an existing kill var is
  CLAUDE.md §7 High tier — stop and ask first. This skill's job is to close
  gaps, not to relax anything that already works.
- **No new runtime dependency.** This skill's own checker is stdlib-only by
  design (matches `dep-guard`/`config-guard`); any fix it proposes to CyClaw's
  own code must not add a package just to probe telemetry state (the worked
  precedent's HF Hub probe uses `huggingface_hub`'s own already-a-transitive-
  dependency `try_to_load_from_cache`, not a new import).
- **Mark speculation.** Where Step 2's sources disagree or a vendor's current
  behavior can't be directly confirmed, say so in the same terms the worked
  precedent's code comments do — do not silently pick a side.

---

## Gotchas

- **A stdlib checker cannot detect vendor-side drift by itself.** `check_otel.py`
  only tells you a pin *moved* since the last verification — it has no network
  access and doesn't know whether that vendor's telemetry contract actually
  changed. Step 2's live search is not optional busywork; it's the half of
  this skill the static script structurally cannot do.
- **Don't trust a single search snippet over reading real source.** The worked
  precedent found a chromadb docs page implying `CHROMA_OTEL_GRANULARITY`
  defaults to `"all"`, which conflicts with what direct source-reading +
  runtime spying against the actually-pinned `chromadb==1.5.9` established
  (defaults to unset, early-returns only on `"none"`). That earlier, more
  rigorous verification should be trusted over an ambiguous docs snippet
  until a re-read of the *pinned* version's own source says otherwise —
  vendor docs sometimes describe a hosted/cloud product's defaults rather
  than the embedded OSS client CyClaw actually uses.
- **`TELEMETRY_KILL` is annotated (`: dict[str, str]`), so it parses as
  `ast.AnnAssign`, not a bare `ast.Assign`.** `check_otel.py`'s
  `_assign_targets()` helper handles both; if you hand-roll a similar AST walk
  elsewhere, remember plain `NAME = {...}` and `NAME: T = {...}` are different
  node types.
- **A fresh clone has none of the transitive-only vendors installed** —
  `huggingface_hub`, `onnxruntime`, `opentelemetry-sdk` have no direct
  `pyproject.toml` pin, so T6 only ever reports INFO, never FAIL/WARN, and
  reports nothing at all when they're absent. That's expected, not a bug in
  the checker.
- **`ORT_TELEMETRY_OPT_OUT` is deliberately retained even though it does
  nothing.** Don't "clean it up" — it's documented parity with the reference
  `.env` file and a real fix (wiring `onnxruntime.disable_telemetry_events()`)
  is out of scope for a quiet drift-check pass; propose that as its own
  explicit, separate change if you decide to pursue it.
- **This skill's `_TODAY` anchor is a fixed date in `check_otel.py`, not
  `datetime.date.today()`.** Update it (and `LAST_VERIFIED_VENDOR_PINS` /
  `Verified <date>` stamps) by hand whenever you actually run Step 2–4, so T4's
  staleness check reflects reality instead of always reporting "0 days old."
