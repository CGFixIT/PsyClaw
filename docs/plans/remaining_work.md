# Remaining work

Live checklist as of **2026-09-02**, verified against `origin/main` `8baea94f`.

Code and `config.yaml` win. Re-list GitHub issues before acting.
History of closed 2026-08-02 items lives in
[`docs/zWork/remaining_work_STALE.md`](../zWork/remaining_work_STALE.md)
(do not treat that file's old statuses or suggested order as current). It is
retained because its contemporaneous refutation and containment rationale is
not duplicated in full elsewhere. Design history:
[`docs/ARCHIVE_AND_ROADMAP.md`](../ARCHIVE_AND_ROADMAP.md).
NeMo current-state matrix: [`docs/NeMo/README.md`](../NeMo/README.md).
Operator parking-lot / product ideas live in [`deploy/planning/todo.txt`](../../deploy/planning/todo.txt)
and are **not** I1–I6 defects.

Stable facts on this tip (do not "fix" them): graph is **12-node**;
invariant-guard is **47/47**; write path is armed (`EXECUTION_ENABLED=True`)
with `agentic.enabled: false`; OOB packages stay six
(`agentic` / `sync` / `guardrails` / `harness` / `telegram` / `opentweet`).
MCP `tools/call` is still unwrapped (I6). Do not wire `safe_generate` into
the graph. H1/H2/H3 are operating rules, not open tickets.

## Shipped posture (re-read `config.yaml` before citing)

| Surface | Ships as |
|---|---|
| `app.mode` | `hybrid` |
| `models.grok` / `claude` | `enabled: true` (still triple-gated per query) |
| `models.local_llm.model` | `qwen3.8:27b-mlx` |
| `telegram.enabled` | `false` |
| `telegram.mode` | `chat` (YAML; channel still off until `enabled: true`) |
| `agentic.enabled` / `fsconnect.enabled` / `sync.enabled` / `memory.enabled` / `auth.enabled` | `false` |
| `agentic.mode` / `writes_enabled` | `write` / `true` (held by master switch + reason + confirm) |
| `unslop.enabled` / `guardrails.enabled` / `numbat.cel.enabled` | `false` |
| `numbat.enabled` | `true` (forensic projection; not an on-path policy plane) |

## Still open in the tree (not GitHub issues)

- **httpx2 / TestClient** — `httpx==0.28.1`; owed before a Starlette major. `docs/analysis/TESTCLIENT_HTTPX_DEPRECATION.md`
- **`websockets` 15 → 16** — still `15.0.1`; blocked on `langgraph-sdk` import of `websockets.asyncio`
- **Agentic loop is GitHub-clone-only** — no local-directory attach. Capability boundary, not a bug

## Do not reopen (code is on main)

- Spend ledger — [#975](https://github.com/cgfixit/CyClaw/pull/975); issue [#958](https://github.com/cgfixit/CyClaw/issues/958) **closed**
- Numbat emitter + audit projection + hook runner + offline sequences — [#973](https://github.com/cgfixit/CyClaw/pull/973) / [#1033](https://github.com/cgfixit/CyClaw/pull/1033) / [#986](https://github.com/cgfixit/CyClaw/pull/986) / [#1037](https://github.com/cgfixit/CyClaw/pull/1037). Issues [#959](https://github.com/cgfixit/CyClaw/issues/959), [#963](https://github.com/cgfixit/CyClaw/issues/963), [#966](https://github.com/cgfixit/CyClaw/issues/966) **closed**
- Numbat Slice A hook-verdict emit — [#1138](https://github.com/cgfixit/CyClaw/pull/1138). Ships `emit_verdict: false`
- Numbat Slice B CEL monitor — [#1143](https://github.com/cgfixit/CyClaw/pull/1143). Ships `numbat.cel.enabled: false`. Do not import `cel-python` on `/query` while that flag is false
- Numbat rules-test fixture job — [#981](https://github.com/cgfixit/CyClaw/pull/981); issue [#961](https://github.com/cgfixit/CyClaw/issues/961) **closed**. Keep `continue-on-error` on that job
- Graph outcome battery — [#969](https://github.com/cgfixit/CyClaw/pull/969) / [#971](https://github.com/cgfixit/CyClaw/pull/971) / [#976](https://github.com/cgfixit/CyClaw/pull/976); issue [#960](https://github.com/cgfixit/CyClaw/issues/960) **closed**
- Groundedness evaluator — [#1048](https://github.com/cgfixit/CyClaw/pull/1048) (`tests/judge_eval.py`, opt-in `CYCLAW_EVAL_LIVE=1`). Issue [#962](https://github.com/cgfixit/CyClaw/issues/962) is **closed** (`not_planned`). A live judge run is operator eval, not a re-implement
- Memory arena — issue [#964](https://github.com/cgfixit/CyClaw/issues/964) is **closed**. Do not reopen until a live groundedness report shows a retrieval miss
- Agentic spend `source:"agentic"` + metrics split — [#1045](https://github.com/cgfixit/CyClaw/pull/1045) / [#1046](https://github.com/cgfixit/CyClaw/pull/1046). Ledger code is done
- Unslop v1 — [#1029](https://github.com/cgfixit/CyClaw/pull/1029). Do not re-vendor `agentic/vendor/unslop/`
- Auth unique-violation classification — [#1044](https://github.com/cgfixit/CyClaw/pull/1044) / [#1216](https://github.com/cgfixit/CyClaw/pull/1216) / [#1219](https://github.com/cgfixit/CyClaw/pull/1219) / [#1230](https://github.com/cgfixit/CyClaw/pull/1230). Integrity already held. Do not demand `INSERT … ON CONFLICT`
- NeMo 4b `check_soul_leak` — [#1155](https://github.com/cgfixit/CyClaw/pull/1155). Enforced on offline `check_output`, in `DEFAULT_OUTPUT_RAILS`, and the Colang flow. [`docs/NeMo/phase4b_soul_leak.md`](../NeMo/phase4b_soul_leak.md) + [`docs/NeMo/README.md`](../NeMo/README.md). Ignore the stale “NOT YET FULLY IMPLEMENTED” comment in `config.yaml`
- **#1134 NeMo program** — [#1134](https://github.com/cgfixit/CyClaw/issues/1134) **closed** (2026-08-27); `nemoguardrails==0.24.0`. Still do not wire `safe_generate` into the graph

Closed since the 2026-08-16 snapshot and not in the open table: [#958](https://github.com/cgfixit/CyClaw/issues/958), [#961](https://github.com/cgfixit/CyClaw/issues/961), [#962](https://github.com/cgfixit/CyClaw/issues/962), [#963](https://github.com/cgfixit/CyClaw/issues/963), [#964](https://github.com/cgfixit/CyClaw/issues/964), [#965](https://github.com/cgfixit/CyClaw/issues/965), [#966](https://github.com/cgfixit/CyClaw/issues/966), [#974](https://github.com/cgfixit/CyClaw/issues/974), [#1134](https://github.com/cgfixit/CyClaw/issues/1134).

## Open GitHub issues (re-list before acting)

Exactly five OPEN issues on `cgfixit/CyClaw` as of this verification. The table
records each issue's closure gate; consult the issue body for optional,
non-blocking cleanup that is not part of that gate.

| # | Title | What is actually left |
|---|---|---|
| [#1013](https://github.com/cgfixit/CyClaw/issues/1013) | Agentic spend ledger | Code ACs on main. Close-out is live-key Leg 1 (`tests/spend_live_probe.py`) + Leg 2 (`real-repo-run-plan --confirm-online`). Do not rebuild the emitter |
| [#1128](https://github.com/cgfixit/CyClaw/issues/1128) | Numbat remainder | Slices A+B on main. Left: Slice C parked (on-path sequence policy needs a hashed session/checkpointer; #1071 keeps the offline detector off `/query`) and Slice D tracking (`source_agent` stays `"unknown"` until upstream accepts `cyclaw`) |
| [#1129](https://github.com/cgfixit/CyClaw/issues/1129) | Unslop v1.1 leftover | v1 on main; plan header restamped (no longer claims "planning only"). Left: measure FP on an opt-in local run. Do not enable by default. Do not put it on `/query` |
| [#1252](https://github.com/cgfixit/CyClaw/issues/1252) | Authn process-local lock | Integrity already held. Origin port/scheme parity shipped in [#1267](https://github.com/cgfixit/CyClaw/pull/1267). Left: live two-process `create_user` test. Do not demand `INSERT … ON CONFLICT` |
| [#1255](https://github.com/cgfixit/CyClaw/issues/1255) | chromadb CVE / sqlite-vec | PostHog path is dead code at pin 1.5.9. sqlite-vec swap is a parked design (not this pass). Cheaper half: startup hash-pin of the telemetry maps + `Settings(anonymized_telemetry=False)` sites |

Ignore PR #415 if it reappears (it is **closed**; still skip it during agentic coding if reopened).
