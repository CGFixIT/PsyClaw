---
description: Rebuild and validate the CyClaw hybrid retrieval index (ChromaDB semantic + BM25 keyword + RRF fusion), then run a fixed query probe set to confirm min_score gating, source correctness, RRF provenance, and both retrieval legs contributing.
---

Rebuild and validate the hybrid retrieval index, then probe it with a fixed query set. $ARGUMENTS

## Steps

1. Rebuild the index: `python3 .claude/skills/index-doctor/doctor.py --rebuild` (rebuilds both `index/chroma_db` and `index/bm25.json` from the same corpus pass).
2. Run the fixed probe query set and confirm: `min_score` (0.028, RRF scale) gating behaves correctly, sources are correct, RRF provenance is visible, and both legs (semantic + BM25) are contributing.
3. Report any stale index, un-reindexed corpus change, empty/duplicated chunk, or a leg that silently stopped contributing.

Follow `.claude/skills/index-doctor/SKILL.md` for the full diagnostic process.

## Notes

- `min_score: 0.028` is on the RRF scale, not cosine — do not "fix" it upward.
- Do not switch BM25 to pickle storage; it must stay JSON (RCE risk).
- Run after any change to `data/corpus/`, the indexer, embeddings, stemmer, or retrieval config.
