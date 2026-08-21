# Remaining work

Live checklist as of **2026-08-16**, `origin/main` `10f6d03f`.

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

## Shipped in tree (GitHub issue may still be open)

- Spend ledger — merged as [#975](https://github.com/cgfixit/CyClaw/pull/975); issue [#958](https://github.com/cgfixit/CyClaw/issues/958) is still open (close-out, not a re-implement)
- Numbat emitter — merged as [#973](https://github.com/cgfixit/CyClaw/pull/973); issue [#959](https://github.com/cgfixit/CyClaw/issues/959) is **closed**
- Graph outcome battery — runner + rows in [#969](https://github.com/cgfixit/CyClaw/pull/969) / [#971](https://github.com/cgfixit/CyClaw/pull/971) / [#976](https://github.com/cgfixit/CyClaw/pull/976); issue [#960](https://github.com/cgfixit/CyClaw/issues/960) is **closed**
- Numbat rules-test fixture job — merged as [#981](https://github.com/cgfixit/CyClaw/pull/981); issue [#961](https://github.com/cgfixit/CyClaw/issues/961) is still open (close-out)
- Offline sequence detection — `utils/sequence_detect.py` joined to `cyclaw-metrics` (audit.jsonl + `source=query` spend.jsonl on `query_hash`). Forensic only; `/query` still has no cross-request policy state. On-path correlation id / checkpointer stays High-tier and out of this work.

## Open GitHub issues (re-list before acting)

| # | Title | Notes |
|---|---|---|
| [#958](https://github.com/cgfixit/CyClaw/issues/958) | Spend ledger | Shipped in #975; issue not closed |
| [#961](https://github.com/cgfixit/CyClaw/issues/961) | Numbat CI | Fixture job now pins CLI 0.2.0 (schema 0.3.0) with emitter-shaped fixtures plus an executor-jail pytest. Keep `continue-on-error`. |
| [#962](https://github.com/cgfixit/CyClaw/issues/962) | LLM-as-judge | Stretch |
| [#963](https://github.com/cgfixit/CyClaw/issues/963) | Pre-action hook | 2–3 day, graph-adjacent |
| [#964](https://github.com/cgfixit/CyClaw/issues/964) | Memory arena | Stretch |
| [#965](https://github.com/cgfixit/CyClaw/issues/965) | Deferred | Upstream Numbat artifact parser; first-party NDJSON projection shipped instead |
| [#966](https://github.com/cgfixit/CyClaw/issues/966) | Offline detector shipped | `cyclaw-metrics` Sequences section. Not on-path enforcement. Hook correlation id / checkpointer remain High-tier. |
| [#974](https://github.com/cgfixit/CyClaw/issues/974) | MCP hardening | stdio-first transport / pinned manifest |

Ignore PR #415 if it is still the labeled ignore-PR.
