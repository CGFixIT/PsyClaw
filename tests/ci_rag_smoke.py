#!/usr/bin/env python3
"""Real (non-mocked) offline RAG smoke test for CI.

Unlike a fully-mocked smoke (a hardcoded MockRetriever that returned a constant
dict), this exercises the *actual* retrieval stack end to end:

  1. Build a real ChromaDB (semantic) + BM25 (keyword) index from data/corpus
     via retrieval.indexer.build_index().
  2. Instantiate the real HybridRetriever and run real hybrid_search() calls for
     several questions the committed corpus (data/corpus/cyclaw_overview.md)
     answers.
  3. Assert every query returns a hit whose fused score clears the configured
     retrieval.min_score gate (the 0.028 "vault hit" threshold from config.yaml)
     AND lands on the expected source doc -- i.e. the same path that the
     static/terminal.html /query endpoint exercises for a real RAG reply.

The min_score is read from config.yaml (NOT hardcoded) so the assertion tracks
the real gate and cannot silently drift if the threshold is retuned.

Queries are deliberately phrased close to the committed corpus headings so they
land well above the gate at stable CI runtime -- this is a regression smoke,
not a recall-tuning benchmark.

No LLM is involved: CI has no LM Studio, so this stops at retrieval (the part
ChromaDB + BM25 + RRF actually own). Generation is covered separately by the
gate/graph unit tests with a mocked LLM.

Exit non-zero on any failure so the CI step goes red on a real retrieval regression.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Direct-script execution needs the repository root on sys.path before these
# project imports; module execution already has the same root naturally.
import yaml  # noqa: E402

from retrieval.indexer import build_index  # noqa: E402
from retrieval.hybrid_search import HybridRetriever  # noqa: E402

# (query, expected_source_substring) — each answerable by data/corpus/cyclaw_overview.md.
# Phrased near-verbatim to corpus content to clear the min_score gate reliably in CI.
QUERIES = [
    ("What fusion method does CyClaw use to blend semantic and keyword results?", "cyclaw_overview"),
    ("How does CyClaw combine ChromaDB vector embeddings with BM25 keyword search?", "cyclaw_overview"),
    ("What does CyClaw use for rate limiting to protect against DoS attacks?", "cyclaw_overview"),
    (
        "According to the CyClaw Deployment section, what does CyClaw use "
        "for local LLM inference offline?",
        "cyclaw_overview",
    ),
]


def main() -> int:
    print("=== Real Offline RAG Query Smoke (ChromaDB + BM25 + RRF) ===")

    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    min_score = float(cfg["retrieval"]["min_score"])
    print(f"Configured min_score gate: {min_score}")

    print("Building real index from", cfg["corpus"]["path"], "...")
    build_index()

    retriever = HybridRetriever()

    failures = 0
    for i, (query, expected_source_substr) in enumerate(QUERIES, start=1):
        print(f"\n[{i}/{len(QUERIES)}] Query: {query}")
        results = retriever.hybrid_search(query)

        if not results:
            print("  FAIL: retriever returned zero hits for a corpus-answerable query")
            failures += 1
            continue

        top = results[0]
        print(f"  Top source: {top.source}")
        print(f"  Top score:  {round(top.score, 6)} (gate: {min_score})")
        print(f"  Mode:       {top.retrieval_mode}")

        if top.score < min_score:
            print(f"  FAIL: top score {top.score} below min_score gate {min_score} (vault miss)")
            failures += 1
            continue

        if expected_source_substr not in top.source:
            print(f"  FAIL: top source {top.source!r} did not contain {expected_source_substr!r}")
            failures += 1
            continue

        print("  PASS: vault hit above gate, correct source")

    if failures:
        print(f"\nFAIL: {failures}/{len(QUERIES)} RAG smoke queries failed")
        return 1

    print(f"\nAll {len(QUERIES)} real RAG queries passed (vault hits above the {min_score} gate)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
