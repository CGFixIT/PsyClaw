---
description: >-
  Rebuild and validate the CyClaw hybrid retrieval index (ChromaDB semantic + BM25 keyword + RRF fusion), then run a fixed query probe set to confirm min_score gating, source correctness, RRF provenance, and both retrieval legs contributing. Use when asked to rebuild or validate the index, when retrieval quality degrades, or after any change to data/corpus/, the indexer, embeddings, stemmer, or retrieval config.
---

Invoke the `index-doctor` skill for the given task. $ARGUMENTS

See `.claude/skills/index-doctor/SKILL.md` for full detail.

## Notes

- `min_score: 0.028` is on the RRF scale, not cosine — do not "fix" it upward.
- Do not switch BM25 to pickle storage; it must stay JSON (RCE risk).
- Run after any change to `data/corpus/`, the indexer, embeddings, stemmer, or retrieval config.
