# `memory/` — optional facts + episodes store

Default-off SQLite+FTS5 store for facts and query episodes, with propose/apply
governance (non-empty human `reason` + injection scan on apply). Soul
(`data/personality/soul.md`) stays identity; this package is not soul mutation.

**Not** [`docs/memories/`](../docs/memories/) (sandbox session notes).
**Not** `~/.CyClaw/memory/` (harness-console `/memory` notes; see
[`harness/README.md`](../harness/README.md)). The harness `/memory` command
cannot enable this package. This package is the optional
facts/episodes store on the RAG path.

Every `memory:` switch in `config.yaml` ships `false`. With defaults, `/query`
behavior matches pre-memory CyClaw. Failures in this layer never fail `/query`.

`consolidation.enabled` is a stub (`consolidation.py`) and must stay false in
v1 — even if flipped, `run_consolidation` returns disabled.

## Isolation

No top-level `import memory` in `gate.py`, `graph.py`, `mcp_hybrid_server.py`,
`retrieval/hybrid_search.py`, or `gate_memory.py`
(`tests/test_memory_isolation.py`). Routes live in `gate_memory.py` and
lazy-import this package.

## Package map

| Module | Role |
|---|---|
| `store.py` | SQLite + FTS5, WAL, 0600, parameterized SQL |
| `models.py` | `Fact`, `Episode`, `MemoryProposal` |
| `policy.py` | Reason required, size/tag limits, injection scan |
| `retrieval_adapter.py` | Optional FTS fusion into hybrid search |
| `mirror.py` | Markdown export helpers |
| `consolidation.py` | Unimplemented stub |
| `selftest.py` | `python -m memory.selftest` |

## Operator contract

Enablement steps, HTTP table (`/memory/*`, `/query/export/html`), and
invariants: [`docs/memory/README.md`](../docs/memory/README.md).
Design: [`docs/memory/IMPLEMENTATION_PLAN.md`](../docs/memory/IMPLEMENTATION_PLAN.md).
