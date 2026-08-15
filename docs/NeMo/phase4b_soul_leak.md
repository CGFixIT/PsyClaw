# NeMo Phase 4b — soul-leak output rail (contract only)

**Status (2026-08-15):** **contract + Colang polarity only.** Phase 4a
(`check_output` grounding on `local_llm`) stays the live `/query` floor.
This note records the decisions that a future 4b implementation PR must
satisfy. It does **not** implement the rail.

Audience: the maintainer who next opens a 4b PR. Read this before writing
`detect_soul_leak` or touching `check_output`.

Related:

- [`!phase4_implementation_plan.md`](./!phase4_implementation_plan.md) Decision 2
  (original deferral) and the 2026-08-15 addendum
- [`README.md`](./README.md) status table
- [`later_development_guideline.md`](./later_development_guideline.md) soul-rail table
- `.claude/skills/injection-redteam/` (FP-sweep methodology to copy)

---

## What is shipped today

| Surface | What runs | Soul-leak? |
|---|---|---|
| Live `/query` (`guardrail_output` → `check_output`) | Grounding only (`check_grounding`) | **No.** `check_soul_leak` is listed in `config.yaml` `output_rails` and is silently skipped. Pinned by `test_check_soul_leak_is_configured_but_not_offline_enforced`. |
| Offline CLI / `safe_generate` floor | Input injection + soul-mutation heuristics | **No** output soul-leak check. |
| Live NeMo Colang (`safe_generate` + `nemoguardrails` installed + `enabled: true`) | `check soul leak` flow in `guardrails/config/rails.co` | **Cheap leftover only.** The flow still calls `check_injection(text=$bot_message)` — the input scanner reused on output. That reuse is a known residual risk, not the 4b design. |

Listing `check_soul_leak` in config is **not** enforcement. The offline
floor only implements the names it has code for. Same silent-skip shape as
`check_jailbreak` on the input side.

---

## Decision A — new primitive; do not reuse `scan_injection`

`scan_injection` (`guardrails/rails.py`) is the SMALL input scan: seven
fixed substrings (`_INJECTION_MARKERS`), designed to sit *behind*
`utils/sanitizer.py`'s 40-pattern fail-closed filter. Its own docstring
says its only production caller is the query-path input rail.

One of those markers is `"you are now"`. On **input**, a user typing that
as a jailbreak is comparatively rare. On **output**, it is ordinary
technical prose ("you are now connected", "you are now in the main menu").
Longer, helpful answers have more surface area and will trip it
incidentally. That is a category error, not a cheap win.

A future 4b PR **must** add a dedicated primitive (suggested name:
`detect_soul_leak`) whose job is identity / config **exfiltration**:

- model restating the system prompt / soul.md / instruction block
- "my instructions are…" / "I was told to…" / verbatim soul-preamble echo
- not generic jailbreak phrasing already covered on the input side

It must **not** call `scan_injection` on `$bot_message`.
`test_check_output_does_not_reuse_scan_injection` pins that
`check_output` does not do so today; a 4b PR that starts reusing it will
fail that test on purpose and must replace the pin with the new primitive
plus the FP-sweep evidence below.

The leftover Colang `check soul leak` flow may keep calling
`check_injection` as a cheap floor **only** until the new primitive is
registered as a NeMo action and the flow is switched over. Do not treat
that leftover as approval to wire the same scan into `check_output`.

---

## Decision B — wire only after a measured false-positive sweep

Do not add `check_soul_leak` to `check_output` (and do not enable it on
`offline_best_effort` or the external fallbacks) until a dedicated
adversarial sweep has been run against a representative sample of **real
model answers**, not queries.

Copy the methodology in `.claude/skills/injection-redteam/redteam.py`:

1. Seed a corpus of labeled answers: `expect: allowed` (benign technical
   prose, including "you are now …", identity questions answered safely,
   corpus restatements) and `expect: blocked` (system-prompt echo, soul.md
   dump, instruction restatement).
2. Run the **new** primitive, not `scan_injection`, against that corpus.
3. Exit non-zero on any false positive (`expect: allowed` but blocked) or
   new bypass (`expect: blocked` but allowed, not flagged open).
4. Hold the false-positive budget at zero on the benign set before any
   graph wiring PR is opened.

Until that sweep exists and is green, `check_output` stays grounding-only.
A "just list it in `output_rails`" change is not a 4b ship.

Scope when it *does* wire: start on `local_llm` only, same as Phase 4a
grounding. The other answer paths were excluded from 4a because their
prompts invite ungrounded content; they are equally the wrong first
target for an unmeasured leak check.

---

## Decision C — Colang polarity is `True` = allowed

Every flow in `guardrails/config/rails.co` uses the same contract:

```
$allowed = execute <action>
if not $allowed
  bot refuse …
  stop
```

NeMo actions in `guardrails/rails.py` return `True` when the turn is
**allowed** and `False` when it is blocked. The soul-leak flow previously
named the result `$leaked`, which inverted the English. The behaviour
(`if not $…`) happened to be correct, but the next editor who "fixed"
`if not $leaked` to `if $leaked` would refuse every clean answer.

The flow is now `$allowed` / `if not $allowed`, matching
`check injection`, `check soul mutation`, and `check jailbreak`.
`test_check_soul_leak_colang_uses_allowed_polarity` pins the names so the
old `$leaked` wording cannot return.

When the new primitive lands, register it as `check_soul_leak` (or a new
action name) with the **same** True=allowed polarity, then switch the
flow to `execute` that action. Do not reintroduce a `$leaked` /
`$blocked` / `$hit` name.

---

## What this note's landing PR does *not* do

- Does not implement `detect_soul_leak`.
- Does not call any leak check from `check_output`.
- Does not implement Auth Stages 3–4 (`docs/AUTHENTICATION_DESIGN.md`).
- Does not change retrieve-first / the 10-node graph / `gate.py` / soul
  writes / I6.
- Does not auto-invoke `/agent` from the harness `/loop` command.

Those remain out of scope until their own approved PRs.

---

## Implementation checklist (future 4b PR)

1. Write `detect_soul_leak(answer: str) -> bool` (True = leak detected —
   Python-side, matching `detect_soul_mutation_intent`) plus unit tests
   for echo / paraphrase / benign technical prose.
2. Register a NeMo action that **inverts** to True=allowed
   (`return not detect_soul_leak(text)`), same shape as
   `_action_check_injection`.
3. Run the FP sweep (Decision B). Attach the corpus + results in the PR.
4. Switch the Colang flow to `execute` the new action; keep `$allowed` /
   `if not $allowed`.
5. Only then teach `check_output` to call the new primitive when
   `check_soul_leak` is in `cfg.output_rails`. Update
   `test_check_soul_leak_is_configured_but_not_offline_enforced` and
   `test_check_output_does_not_reuse_scan_injection` in the same PR —
   they are supposed to break.
6. Leave `guardrail_output_node`'s `answer_model != "local"` skip in
   place unless a later design explicitly widens scope.
7. Do not import `guardrails` from `gate.py` / `graph.py` /
   `mcp_hybrid_server.py`. The existing inversion shim stays.

---

## Verification (this contract PR, not the future rail)

```bash
GROK_API_KEY=dummy pytest \
  tests/test_guardrails_rails.py \
  tests/test_guardrails_integration.py \
  tests/test_guardrails_isolation.py -q --tb=short
```

Expect `test_check_soul_leak_colang_uses_allowed_polarity` and
`test_check_output_does_not_reuse_scan_injection` to pass, and
`test_check_soul_leak_is_configured_but_not_offline_enforced` to still
show `check_soul_leak` absent from `check_output`'s `rails` list.
