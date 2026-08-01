#!/usr/bin/env python3
"""Temporary cross-platform diagnostic for the macos-latest RAG smoke gap.

PR #734's macos-latest leg fails tests/ci_rag_smoke.py on 2 of 4 queries
(scores land below the 0.028 min_score gate) while the identical commit
passes on ubuntu-latest. Read-only: reuses the same HybridRetriever the
smoke test does, just prints every score component (semantic, keyword, RRF
contributions) for the top 5 hits instead of asserting on the top 1, plus the
torch/numpy BLAS backend info, so the two CI runs' outputs can be diffed to
find exactly where the platforms' floating-point results diverge.

Not a test (no assertions, exit code always 0) and not meant to stay in the
repo -- delete once the root cause is found and either the fix or the
accepted-limitation decision lands.
"""

import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy
import torch
import yaml

from retrieval.hybrid_search import HybridRetriever
from retrieval.indexer import build_index

# The two queries that fail on macos-latest but pass on ubuntu-latest.
QUERIES = [
    "What fusion method does CyClaw use to blend semantic and keyword results?",
    "How does CyClaw deploy and run local LLM inference offline?",
]


def main() -> int:
    print("=== RAG cross-platform diagnostic ===")
    print(f"platform.platform(): {platform.platform()}")
    print(f"platform.machine():  {platform.machine()}")
    print(f"torch.__version__:   {torch.__version__}")
    print(f"torch backend info:\n{torch.__config__.show()}")
    print(f"numpy.__version__:   {numpy.__version__}")
    numpy.show_config()

    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    min_score = float(cfg["retrieval"]["min_score"])
    print(f"\nConfigured min_score gate: {min_score}")

    build_index()
    retriever = HybridRetriever()

    for query in QUERIES:
        print(f"\n--- Query: {query} ---")
        results = retriever.hybrid_search(query)
        for rank, r in enumerate(results[:5], start=1):
            print(
                f"  #{rank} source={r.source} mode={r.retrieval_mode}\n"
                f"      rrf_score={r.rrf_score!r} score={r.score!r}\n"
                f"      semantic_score={r.semantic_score!r} semantic_rank={r.semantic_rank!r} "
                f"rrf_semantic_contrib={r.rrf_semantic_contrib!r}\n"
                f"      keyword_score={r.keyword_score!r} keyword_rank={r.keyword_rank!r} "
                f"rrf_keyword_contrib={r.rrf_keyword_contrib!r}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
