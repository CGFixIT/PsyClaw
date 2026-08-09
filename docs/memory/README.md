# CyClaw Memory Subsystem

Optional, **default-off** facts + episodes store with propose/apply governance and optional retrieval fusion.

> **Not** `docs/memories/` (sandbox notes). This feature lives under `docs/memory/` and package `memory/`.

## Defaults

Every switch in `config.yaml` → `memory:` is **false**. With defaults, behavior is identical to pre-memory CyClaw.

## Enable progressively

1. `memory.enabled: true` + `episodes.enabled: true` — stage query episodes (hashed query by default).
2. `propose_apply.enabled: true` + set `CYCLAW_API_KEY` — use `/memory/propose` then `/memory/apply`.
3. `facts.enabled: true` + `retrieval_fusion.enabled: true` — FTS fact hits fuse into `hybrid_search` as `retrieval_mode="memory"`.
4. `export_html.enabled: true` — `GET /query/export/html` (auth-gated).

`consolidation.enabled` is a **stub** and must stay false in v1.

## Invariants

- No top-level `import memory` in `gate.py` / `graph.py` / `mcp_hybrid_server.py` / `hybrid_search.py` / `gate_memory.py`.
- Memory failures never fail `/query` (non-fatal hooks).
- Mutating routes require Bearer API key + non-empty reason.
- Apply path runs injection scan before fact write.
- Soul (`personality`) remains identity, not memory.

## Operator API

| Method | Path | Notes |
|--------|------|--------|
| GET | `/memory/status` | Always 200 + flags when keyed |
| GET | `/memory/facts` | 404 if master off |
| GET | `/memory/episodes` | 404 if master off |
| GET | `/memory/proposals` | propose_apply gate |
| POST | `/memory/propose` | body: action, content/fact_id, reason |
| POST | `/memory/apply` | body: proposal_id, reason |
| POST | `/memory/reject` | body: proposal_id, reason |
| GET | `/query/export/html` | export_html gate |

## Selftest

```bash
python -m memory.selftest
```

## Design authority

See [IMPLEMENTATION_PLAN.md](./IMPLEMENTATION_PLAN.md).
