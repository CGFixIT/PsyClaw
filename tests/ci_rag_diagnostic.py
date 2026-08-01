#!/usr/bin/env python3
"""Temporary cross-platform diagnostic for the macos-latest RAG smoke gap.

Round 1 (confirmed): on macOS, cyclaw_overview.md wins the BM25/keyword leg
for 2 of 4 ci_rag_smoke.py queries but doesn't place in the top-5 semantic
leg at all (an unrelated doc does instead) -- a real floating-point
difference between torch's Accelerate-backed macOS build and its MKL-backed
Linux build (Apple Silicon has no MKL option). A single-leg RRF hit tops out
at ~0.0167, below the 0.028 gate.

Round 2 (this version): the open question is whether cyclaw_overview.md is
just OUTSIDE today's top_k_semantic=5 window (in which case widening it is a
real, principled fix -- more robust to this exact kind of cross-platform
embedding variance) or whether its semantic similarity for these queries is
so low that no reasonable top_k_semantic would catch it (in which case the
fix has to be elsewhere, e.g. corpus content). Calls semantic_search(query,
k=30) directly -- bypassing top_k_semantic -- and reports cyclaw_overview.md's
actual rank and score wherever it falls.

Not a test (no assertions, exit code always 0) and not meant to stay in the
repo -- delete once the root cause is found and either the fix or the
accepted-limitation decision lands.
"""

import platform
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import torch  # noqa: E402
import yaml  # noqa: E402
from sentence_transformers.util import get_device_name  # noqa: E402

# Module-object import only (not `from retrieval.embeddings import _load_model`):
# _load_model is the exact attribute tests/test_embeddings.py monkeypatches, so
# binding its value here would not observe a patch -- CodeQL's
# py/import-of-mutable-attribute. Importing the same module both ways instead
# trips py/import-and-import-from, so this stays a single module import, matching
# the idiom at tests/test_harness_auth.py:20-23. hybrid_search/indexer below are
# different modules, so the plain from-import is fine for them.
import retrieval.embeddings as embeddings  # noqa: E402
from retrieval.hybrid_search import HybridRetriever  # noqa: E402
from retrieval.indexer import build_index  # noqa: E402

# The two queries that fail on macos-latest but pass on ubuntu-latest.
QUERIES = [
    "What fusion method does CyClaw use to blend semantic and keyword results?",
    "How does CyClaw deploy and run local LLM inference offline?",
]
WIDE_K = 30


def main() -> int:
    print("=== RAG cross-platform diagnostic (round 3: embedding device) ===")
    print(f"platform.machine(): {platform.machine()}  torch: {torch.__version__}")

    # THE question this round exists to settle. retrieval/embeddings.py passes no
    # device= to SentenceTransformer, so sentence-transformers auto-selects via
    # get_device_name()'s cuda -> mps -> ... -> cpu ladder. Whether macOS actually
    # lands on "mps" has been inferred from that code path but never observed.
    # model.device is the definitive answer: it is the device the tensors are
    # really on, for both the index build and every query.
    print("--- device selection ---")
    print(f"  torch.cuda.is_available():        {torch.cuda.is_available()}")
    print(f"  torch.backends.mps.is_built():    {torch.backends.mps.is_built()}")
    print(f"  torch.backends.mps.is_available(): {torch.backends.mps.is_available()}")
    print(f"  sentence_transformers get_device_name(): {get_device_name()!r}")
    # get_device_name() above is already decisive (embeddings.py passes no
    # device=, so whatever it returns IS what the model gets). This is the
    # belt-and-braces confirmation, reading the device off the real tensors.
    # Guarded because a model-load failure here must not cost us the probes
    # printed above -- they are the point of this run.
    try:
        model_name, cache_dir = embeddings._embeddings_cfg("config.yaml")
        print(f"  ACTUAL loaded model device:       {embeddings._load_model(model_name, cache_dir).device!r}")
    except Exception as exc:  # noqa: BLE001 - diagnostic must never abort on this
        print(f"  ACTUAL loaded model device:       <unavailable: {type(exc).__name__}>")

    with open("config.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    min_score = float(cfg["retrieval"]["min_score"])
    configured_top_k = int(cfg["retrieval"]["top_k_semantic"])
    print(f"Configured min_score gate: {min_score}  top_k_semantic: {configured_top_k}")

    build_index()
    retriever = HybridRetriever()

    for query in QUERIES:
        print(f"\n--- Query: {query} ---")
        wide_hits = retriever.semantic_search(query, k=WIDE_K)
        print(f"  Widened semantic_search(k={WIDE_K}) returned {len(wide_hits)} candidates:")
        found_overview = False
        for rank, hit in enumerate(wide_hits):
            marker = ""
            if "cyclaw_overview" in hit.source:
                marker = "  <-- cyclaw_overview.md"
                found_overview = True
            in_window = "IN current top_k_semantic window" if rank < configured_top_k else "OUTSIDE current window"
            print(f"    rank={rank} ({in_window}) score={hit.score!r} source={hit.source}{marker}")
        if not found_overview:
            print(f"  cyclaw_overview.md did NOT appear in the top {WIDE_K} semantic candidates at all.")

        print("\n  Full hybrid_search() top 5 (for reference):")
        results = retriever.hybrid_search(query)
        for rank, r in enumerate(results[:5], start=1):
            print(
                f"    #{rank} source={r.source} mode={r.retrieval_mode} "
                f"rrf_score={r.rrf_score!r} semantic_rank={r.semantic_rank!r} keyword_rank={r.keyword_rank!r}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
