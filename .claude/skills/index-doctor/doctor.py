#!/usr/bin/env python3
"""doctor.py – retrieval-health check for the CyClaw hybrid index.

Usage:
    python .claude/skills/index-doctor/doctor.py [--rebuild] [--config config.yaml] [--json] [--baseline PATH]

What it does (read-only unless --rebuild):
    1. Preflight: corpus dir exists, has files with configured extensions,
       chunk config is sane (chunk_overlap < chunk_size).
    2. (optional --rebuild) build the index via retrieval.indexer.build_index().
    3. Count parity: BM25 chunk count vs ChromaDB collection count must agree
       (the indexer builds both from one chunk list, so a mismatch = corruption
       or a half-written index).
    4. Chunk hygiene: flag empty/whitespace-only chunks and exact-duplicate
       chunks in the BM25 store.
    5. Fixed-query probe set through the REAL HybridRetriever: every
       corpus-answerable query must clear retrieval.min_score, land on the
       expected source, populate RRF provenance, and show BOTH retrieval legs
       contributing across the set. semantic-only and keyword-only modes must
       also return hits (graceful per-leg behavior).
    6. Optional delta vs a saved --baseline JSON (chunk counts, top scores).

Exit codes (repo convention):
    0  index healthy, all probes pass
    2  a health check or probe failed
    3  env/config error (no deps, no corpus, unreadable config)

Requires the project venv. Run from the repo root. This is a RICHER superset of
tests/ci_rag_smoke.py — same real stack, more checks.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Each query is answerable by the committed corpus (data/corpus/cyclaw_overview.md),
# phrased near corpus headings so it clears the gate at stable runtime. The
# "one sentence" probe mirrors CyClaw-Sandbox P13, the canonical vault-hit test.
PROBES = [
    ("Describe CyClaw in one sentence.", "cyclaw_overview"),
    ("What fusion method blends semantic and keyword retrieval results?", "cyclaw_overview"),
    ("How does CyClaw combine ChromaDB embeddings with BM25 keyword search?", "cyclaw_overview"),
    ("What protects CyClaw against request-flood denial of service?", "cyclaw_overview"),
    ("How does CyClaw run local LLM inference offline?", "cyclaw_overview"),
]

_fail: list[str] = []
_warn: list[str] = []


def bad(msg: str) -> None:
    _fail.append(msg)
    print(f"  FAIL  {msg}")


def warn(msg: str) -> None:
    _warn.append(msg)
    print(f"  WARN  {msg}")


def good(msg: str) -> None:
    print(f"  ok    {msg}")


def chroma_count(cfg: dict) -> int | None:
    """Collection size for the chroma backend; None for other backends."""
    if cfg["indexing"].get("vector_backend", "chroma") != "chroma":
        return None
    import chromadb
    from chromadb.config import Settings
    path = cfg["indexing"]["chroma_path"]
    client = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
    return client.get_collection(cfg["indexing"]["collection_name"]).count()


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    repo_root = here.parents[2]
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--config", type=Path, default=repo_root / "config.yaml")
    p.add_argument("--rebuild", action="store_true", help="rebuild the index before checking")
    p.add_argument("--json", action="store_true")
    p.add_argument("--baseline", type=Path, default=None, help="compare against a saved report JSON")
    args = p.parse_args(argv)

    sys.path.insert(0, str(repo_root))
    try:
        import yaml
        from retrieval.indexer import build_index
        from retrieval.hybrid_search import HybridRetriever
    except ImportError as exc:
        print(f"env error: project deps not importable ({exc}). Run in the venv from repo root.",
              file=sys.stderr)
        return 3

    try:
        cfg = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        print(f"env error: cannot read {args.config}: {exc}", file=sys.stderr)
        return 3

    min_score = float(cfg["retrieval"]["min_score"])
    corpus_path = Path(cfg["corpus"]["path"])
    extensions = tuple(cfg["corpus"].get("extensions", [".md", ".txt"]))
    bm25_path = Path(cfg["indexing"]["bm25_path"])

    # ── 1. Preflight ─────────────────────────────────────────────────────────
    print("1. Preflight")
    if not corpus_path.exists():
        bad(f"corpus dir {corpus_path} does not exist")
        return 3
    corpus_files = [f for f in corpus_path.rglob("*") if f.suffix in extensions]
    if not corpus_files:
        bad(f"no {extensions} files under {corpus_path}")
        return 3
    good(f"{len(corpus_files)} corpus file(s) with extensions {extensions}")
    cs = int(cfg["indexing"]["chunk_size"])
    co = int(cfg["indexing"]["chunk_overlap"])
    if co >= cs:
        bad(f"chunk_overlap ({co}) >= chunk_size ({cs}) — indexer will raise ValueError")
    else:
        good(f"chunk config sane (size {cs}, overlap {co})")

    # ── 2. Rebuild ───────────────────────────────────────────────────────────
    if args.rebuild:
        print("2. Rebuild")
        try:
            build_index()
            good("build_index() completed")
        except Exception as exc:  # noqa: BLE001 - report any build failure verbatim
            bad(f"build_index() raised: {exc}")
            return 2
    else:
        print("2. Rebuild (skipped; pass --rebuild to force)")

    # ── 3. Count parity ──────────────────────────────────────────────────────
    print("3. Count parity")
    if not bm25_path.exists():
        bad(f"BM25 index {bm25_path} missing — run with --rebuild or python -m retrieval.indexer")
        return 2
    bm25_data = json.loads(bm25_path.read_text(encoding="utf-8"))
    chunks = bm25_data.get("chunks", [])
    bm25_n = len(chunks)
    good(f"BM25 chunk count: {bm25_n}")
    ch_n = None
    try:
        ch_n = chroma_count(cfg)
    except Exception as exc:  # noqa: BLE001
        bad(f"could not read ChromaDB collection: {exc}")
    if ch_n is None:
        warn("non-chroma vector backend — skipping chroma/BM25 parity check")
    elif ch_n != bm25_n:
        bad(f"count mismatch: ChromaDB has {ch_n}, BM25 has {bm25_n} (index inconsistent)")
    else:
        good(f"ChromaDB count matches BM25 ({ch_n})")

    # ── 4. Chunk hygiene ─────────────────────────────────────────────────────
    print("4. Chunk hygiene")
    empties = sum(1 for c in chunks if not str(c).strip())
    if empties:
        bad(f"{empties} empty/whitespace-only chunk(s)")
    else:
        good("no empty chunks")
    seen: dict[str, int] = {}
    dups = 0
    for c in chunks:
        key = str(c).strip()
        seen[key] = seen.get(key, 0) + 1
    dups = sum(v - 1 for v in seen.values() if v > 1)
    if dups:
        warn(f"{dups} exact-duplicate chunk(s) — corpus may have repeated content")
    else:
        good("no exact-duplicate chunks")

    # ── 5. Probe set ─────────────────────────────────────────────────────────
    print("5. Probe set (real HybridRetriever)")
    top_scores: dict[str, float] = {}
    legs_seen: set[str] = set()
    try:
        retriever = HybridRetriever(str(args.config))
    except Exception as exc:  # noqa: BLE001
        bad(f"HybridRetriever init failed: {exc}")
        return 2
    for query, expect_src in PROBES:
        hits = retriever.hybrid_search(query)
        if not hits:
            bad(f"zero hits: {query!r}")
            continue
        top = hits[0]
        top_scores[query] = round(float(top.score), 6)
        problems = []
        if top.score < min_score:
            problems.append(f"score {top.score:.4f} < min_score {min_score}")
        if expect_src not in top.source:
            problems.append(f"source {top.source!r} != expected ~{expect_src!r}")
        if top.rrf_score is None:
            problems.append("rrf_score provenance not populated")
        for h in hits:
            if h.semantic_score is not None:
                legs_seen.add("semantic")
            if h.keyword_score is not None:
                legs_seen.add("keyword")
        if problems:
            bad(f"{query!r}: " + "; ".join(problems))
        else:
            good(f"{query!r} -> {top.source} @ {top.score:.4f}")

    if legs_seen == {"semantic", "keyword"}:
        good("both retrieval legs contributed across the probe set")
    else:
        bad(f"only these legs contributed: {sorted(legs_seen) or 'none'} (expected both)")

    # Per-leg graceful behavior.
    if retriever.semantic_search(PROBES[0][0]):
        good("semantic-only search returns hits")
    else:
        bad("semantic-only search returned nothing for a corpus-answerable query")
    if retriever.keyword_search(PROBES[0][0]):
        good("keyword-only search returns hits")
    else:
        bad("keyword-only search returned nothing for a corpus-answerable query")
    retriever.close()

    # ── 6. Baseline delta ────────────────────────────────────────────────────
    report = {"bm25_count": bm25_n, "chroma_count": ch_n, "top_scores": top_scores}
    if args.baseline:
        print("6. Baseline delta")
        try:
            base = json.loads(args.baseline.read_text(encoding="utf-8"))
            if base.get("bm25_count") != bm25_n:
                warn(f"chunk count changed: {base.get('bm25_count')} -> {bm25_n}")
            for q, sc in top_scores.items():
                prev = base.get("top_scores", {}).get(q)
                if prev is not None and abs(prev - sc) > 0.01:
                    warn(f"top score drift for {q!r}: {prev} -> {sc}")
            good("baseline compared")
        except (OSError, json.JSONDecodeError) as exc:
            warn(f"could not read baseline: {exc}")

    if args.json:
        print(json.dumps(report, indent=2))

    print(f"\n{'FAIL' if _fail else 'OK'} — {len(_fail)} failure(s), {len(_warn)} warning(s)")
    return 2 if _fail else 0


if __name__ == "__main__":
    sys.exit(main())
