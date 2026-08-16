# Remaining work

Live checklist as of **2026-08-16**, `origin/main` `961f2c59`.

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

## Open GitHub issues (re-list before acting)

| # | Title | Notes |
|---|---|---|
| [#958](https://github.com/cgfixit/CyClaw/issues/958) | Spend ledger | Researched; later session |
| [#959](https://github.com/cgfixit/CyClaw/issues/959) | Numbat emitter | 1–2 day |
| [#960](https://github.com/cgfixit/CyClaw/issues/960) | Graph outcome battery | Runner landed in #969; more JSONL rows remain |
| [#961](https://github.com/cgfixit/CyClaw/issues/961) | Numbat CI | Blocked on #959 |
| [#962](https://github.com/cgfixit/CyClaw/issues/962) | LLM-as-judge | Stretch |
| [#963](https://github.com/cgfixit/CyClaw/issues/963) | Pre-action hook | 2–3 day, graph-adjacent |
| [#964](https://github.com/cgfixit/CyClaw/issues/964) | Memory arena | Stretch |
| [#965](https://github.com/cgfixit/CyClaw/issues/965) / [#966](https://github.com/cgfixit/CyClaw/issues/966) | Deferred | Wait on #959 / #963 |

Ignore PR #415 if it is still the labeled ignore-PR.
