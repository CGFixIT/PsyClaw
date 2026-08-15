# `data/` — runtime state (mostly governed, partly generated)

Operator data and server state. Nothing here is code; several files are
**governed artifacts** with write rules — read this before editing anything
by hand.

| Path | What it is | Write rules |
|---|---|---|
| `corpus/` | The RAG knowledge base: Markdown/text documents ingested by `python -m retrieval.indexer`. Add/edit files here, then reindex — the server never picks up corpus changes on its own. | Free to edit. Chunks are sanitized at ingestion; write self-contained `##` sections (the corpus is chunked and searched section-by-section). |
| `personality/soul.md` | The soul file — CyClaw's persona/behavior contract, capped at `personality.soul_max_chars` (8000). | **Never hand-edit or script a write around `PersonalityManager`.** Mutation requires a non-empty human `reason` and passes an injection scan; writes are atomic (invariant I5). Use `/soul/propose` → `/soul/apply`, or the manager API. Deleting it does not break boot — it self-heals with a default. |
| `personality/cyclaw_soul.db` | Soul version history + SHA-256 drift baseline (SQLite; Postgres via `CYCLAW_DB_URL`). The newest `soul_versions` row is the drift baseline — there is no hash constant in code. | Managed by `utils/personality_db.py`; don't edit. |
| `agentic/skills_registry.json` | Governed skills registry for the out-of-band agentic layer. | Managed via `python -m agentic.cli` under the registry governance rules — see `docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md`. The harness console **reads** this file (sidebar `/registry`, `/skills all`); it never writes it. |

Not in this directory but adjacent in spirit: the retrieval indices live in
`index/` (regenerable — rebuild with `python -m retrieval.indexer`), the
embedding model cache in `.emb_cache/` (regenerable —
`python -m retrieval.clear_cache`), and the audit log at
`logs/audit.jsonl` (append-only JSONL; query text stored only as SHA-256
hashes). The coding console's mutable state is **not** here — it lives under
`~/.CyClaw` / `%USERPROFILE%\.CyClaw` (`sessions/`, `tools/web_allowlist.json`,
`config.json`). See [`harness/README.md`](../harness/README.md).

## Related

- Retrieval/index mechanics: [`retrieval/README.md`](../retrieval/README.md)
- Soul governance invariant (I5): repo-root `INVARIANTS.md`
- Dropbox sync of `corpus/`: [`docs/SYNC_README.md`](../docs/SYNC_README.md)
