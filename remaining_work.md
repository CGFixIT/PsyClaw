# Remaining work

Live checklist as of **2026-08-21**, `origin/main` `02447585`.

Code and `config.yaml` win. History of closed 2026-08-02 items lives in
[`docs/zWork/remaining_work_STALE.md`](docs/zWork/remaining_work_STALE.md)
(do not treat that file as the open list). Design history:
[`docs/ARCHIVE_AND_ROADMAP.md`](docs/ARCHIVE_AND_ROADMAP.md).

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

## Still open in the tree

- **httpx2 / TestClient** — `httpx==0.28.1`; owed before a Starlette major. `docs/TESTCLIENT_HTTPX_DEPRECATION.md`
- **`websockets` 15 → 16** — still `15.0.1`; blocked on `langgraph-sdk` import of `websockets.asyncio`
- **NeMo 4b `check_soul_leak`** — listed in `guardrails.output_rails`, not fully built. `docs/NeMo/phase4b_soul_leak.md`
- **Agentic loop is GitHub-clone-only** — no local-directory attach. Capability boundary, not a bug

## Shipped in tree (GitHub issue closed unless noted)

- Spend ledger — merged as [#975](https://github.com/cgfixit/CyClaw/pull/975); issue [#958](https://github.com/cgfixit/CyClaw/issues/958) is **closed**
- Numbat emitter — merged as [#973](https://github.com/cgfixit/CyClaw/pull/973); issue [#959](https://github.com/cgfixit/CyClaw/issues/959) is **closed**
- Graph outcome battery — runner + rows in [#969](https://github.com/cgfixit/CyClaw/pull/969) / [#971](https://github.com/cgfixit/CyClaw/pull/971) / [#976](https://github.com/cgfixit/CyClaw/pull/976); issue [#960](https://github.com/cgfixit/CyClaw/issues/960) is **closed**
- Numbat rules-test fixture job — merged as [#981](https://github.com/cgfixit/CyClaw/pull/981); issue [#961](https://github.com/cgfixit/CyClaw/issues/961) is **closed**. Keep `continue-on-error` on that job.
- Offline sequence detection — `utils/sequence_detect.py` joined to `cyclaw-metrics` (audit.jsonl + `source=query` spend.jsonl on `query_hash`). Forensic only; `/query` still has no cross-request policy state. On-path correlation id / checkpointer stays High-tier and out of this work.
- Groundedness evaluator — merged as [#1048](https://github.com/cgfixit/CyClaw/pull/1048) (`tests/judge_eval.py`, opt-in `CYCLAW_EVAL_LIVE=1`). Issue [#962](https://github.com/cgfixit/CyClaw/issues/962) is still **open** as close-out (live run), not a re-implement.
- Agentic spend `source:"agentic"` + metrics split — [#1045](https://github.com/cgfixit/CyClaw/pull/1045) / [#1046](https://github.com/cgfixit/CyClaw/pull/1046). Issue [#1013](https://github.com/cgfixit/CyClaw/issues/1013) is still **open** for the live-key bar.

Closed since the 2026-08-16 snapshot and not in the open table: [#958](https://github.com/cgfixit/CyClaw/issues/958), [#961](https://github.com/cgfixit/CyClaw/issues/961), [#963](https://github.com/cgfixit/CyClaw/issues/963) (pre-action hook shipped), [#965](https://github.com/cgfixit/CyClaw/issues/965), [#966](https://github.com/cgfixit/CyClaw/issues/966), [#974](https://github.com/cgfixit/CyClaw/issues/974).

## Open GitHub issues (re-list before acting)

| # | Title | Notes |
|---|---|---|
| [#962](https://github.com/cgfixit/CyClaw/issues/962) | LLM-as-judge | Suite on main (#1048). Close-out: accept `CYCLAW_EVAL_LIVE=1 python tests/judge_eval.py` (no Makefile / `JUDGE_API_KEY`) and/or run it live. |
| [#964](https://github.com/cgfixit/CyClaw/issues/964) | Memory arena | Stretch. Parked until a live groundedness report shows a retrieval miss. |
| [#1013](https://github.com/cgfixit/CyClaw/issues/1013) | Agentic spend ledger | Code ACs on main. Close-out is live-key Leg 1 (`spend_live_probe.py`) + Leg 2 (`real-repo-run-plan --confirm-online`). |
| [#1128](https://github.com/cgfixit/CyClaw/issues/1128) | Numbat implementation — remainder | Slice A (hook-verdict emission) + Slice B (CEL monitor) active. Slice C (on-path sequence policy) parked. |

Ignore PR #415 if it reappears (it is **closed**; still skip it during agentic coding if reopened).
