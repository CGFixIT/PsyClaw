# CyClaw Memory Subsystem

> **Status update — 2026-09-06 (docs review, Claude Code):** COMPLETE — this is the current operator-facing reference for a shipped feature, not a stale plan. Verified against the live tree: `config.yaml`'s `memory:` block (`:260-286`) matches every default/route claim here, the `memory/` package (including `flags.py`, which backs the `facts.retrieval_enabled` rename this doc describes) exists, `gate_memory.py` implements the `/memory/*` + `/query/export/html` routes, and `memory/selftest.py` is runnable.
>
> **What's left:**
> - Nothing outstanding as documentation — keep this file in sync if `memory/policy.py`'s injection-scan source or the route table in `gate_memory.py` changes. Not a deletion candidate; it is live operator documentation.

Optional, **default-off** facts + episodes store with propose/apply governance and optional retrieval fusion.

> **Not** `docs/memories/` (sandbox notes). This feature lives under `docs/memory/` and package `memory/`.
> **Not** the harness home `~/.CyClaw/memory/` (console-local log). `/goal` is session data on the harness, not a memory fact.

## Defaults

Every switch in `config.yaml` → `memory:` is **false**. With defaults, behavior is identical to pre-memory CyClaw.

## Enable progressively

1. `memory.enabled: true` + `episodes.enabled: true` — stage query episodes (hashed query by default).
2. `propose_apply.enabled: true` + set `CYCLAW_API_KEY` — use `/memory/propose` then `/memory/apply`.
3. `facts.retrieval_enabled: true` + `retrieval_fusion.enabled: true` — FTS fact hits fuse into `hybrid_search` as `retrieval_mode="memory"`.
4. `export_html.enabled: true` — `GET /query/export/html` (auth-gated).

`consolidation.enabled` is a **stub** and must stay false in v1.

Step 3 comes after step 2 on purpose: facts are proposed, applied and verified
**before** they are exposed to retrieval. `facts.retrieval_enabled` gates only
that last exposure — it is deliberately not a master switch for facts, and
turning it off does not stop persistence. **No switch gates fact persistence at
all**: `memory.enabled` + `propose_apply.enabled` is the whole story, so a fact
that has been applied stays stored and readable via `GET /memory/facts`
regardless. (This is why the flag was renamed from `facts.enabled`, which read
as a master switch that never existed. The old name still works and logs a
one-time warning — see `memory/flags.py`.)

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
