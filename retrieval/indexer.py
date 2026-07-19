"""Build ChromaDB (semantic) and BM25 (keyword) indices from .md corpus.

Uses sentence-transformers for embeddings (CPU, no Ollama).
Sanitizes chunks at ingestion time via prompt filter.
"""

import hashlib
import json
import logging
from pathlib import Path

import yaml

from utils.errors import CorpusEmptyError
from utils.sanitizer import sanitize_chunk

from .embeddings import get_embeddings_batch
from .stemmer import tokenize_and_stem
from .vector_store import get_vector_writer, vector_backend

logger = logging.getLogger(__name__)
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _resolve_config_path(config_path: str = "config.yaml") -> Path:
    path = Path(config_path).expanduser()
    if not path.is_absolute():
        path = _REPO_ROOT / path
    return path.resolve()


def _resolve_relative_path(value: str, base_dir: Path) -> str:
    path = Path(value).expanduser()
    if path.is_absolute():
        return str(path)
    return str((base_dir / path).resolve())


def load_config(config_path: str = "config.yaml") -> dict:
    with open(_resolve_config_path(config_path), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _anchor_index_paths(cfg: dict, config_path: Path) -> dict:
    base_dir = config_path.parent
    path_keys = (
        ("corpus", "path"),
        ("indexing", "bm25_path"),
        ("indexing", "chroma_path"),
    )
    for section, key in path_keys:
        value = cfg.get(section, {}).get(key)
        if isinstance(value, str) and value:
            cfg[section][key] = _resolve_relative_path(value, base_dir)
    return cfg


def load_corpus(corpus_path: str, extensions: list[str]) -> list[tuple[str, str]]:
    docs = []
    corpus_dir = Path(corpus_path)
    if not corpus_dir.exists():
        raise CorpusEmptyError(f"Corpus directory does not exist: {corpus_path}")
    corpus_resolved = corpus_dir.resolve()
    normalized_extensions = {ext.lower() for ext in extensions}
    for file_path in corpus_dir.rglob("*"):
        if not file_path.is_file():
            continue
        # Match file extension case-insensitively (e.g., .MD matches .md config entry).
        if file_path.suffix.lower() not in normalized_extensions:
            continue
        # Reject symlinks that escape the corpus directory — rglob follows
        # symlinks by default, so a link pointing outside data/corpus could
        # pull arbitrary filesystem content into the index.
        if not file_path.resolve().is_relative_to(corpus_resolved):
            logger.warning("Skipping %s: resolves outside corpus directory", file_path)
            continue
        try:
            content = file_path.read_text(encoding="utf-8")
            docs.append((str(file_path), content))
        except (UnicodeDecodeError, OSError) as e:
            logger.warning("Skipping %s: %s", file_path, e)
    if not docs:
        raise CorpusEmptyError(f"No documents found in {corpus_path} with extensions {extensions}")
    return docs


def chunk_document(text: str, chunk_size: int = 512, overlap: int = 50) -> list[str]:
    # Fail loudly on a caller-supplied misconfiguration. build_index() validates
    # config values, but chunk_document() is also called directly (tests, ad-hoc
    # tooling) where overlap >= chunk_size would otherwise silently degrade to a
    # one-word stride and explode a modest corpus into one chunk per word.
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
    if overlap < 0:
        # A negative overlap makes step = chunk_size - overlap > chunk_size, so
        # each window jumps FORWARD past its own end and silently drops the
        # words in the gap — a corpus indexed with a sign-typo'd overlap loses
        # content from both retrieval legs with no error.
        raise ValueError(f"overlap must be >= 0, got {overlap}")
    if overlap >= chunk_size:
        raise ValueError(f"overlap ({overlap}) must be < chunk_size ({chunk_size})")
    # With the guards above this is always >= 1; keep the clamp as a belt-and-
    # suspenders floor so no future code path can ever spin forever.
    step = max(1, chunk_size - overlap)
    words = text.split()
    chunks = []
    start = 0
    while start < len(words):
        end = min(start + chunk_size, len(words))
        chunks.append(" ".join(words[start:end]))
        start += step
    return chunks


def build_index(config_path: str = "config.yaml") -> None:
    resolved_config_path = _resolve_config_path(config_path)
    config_path_str = str(resolved_config_path)
    cfg = _anchor_index_paths(load_config(config_path_str), resolved_config_path)
    corpus_path = cfg["corpus"]["path"]
    extensions = cfg["corpus"]["extensions"]
    bm25_path = cfg["indexing"]["bm25_path"]
    chunk_size = cfg["indexing"]["chunk_size"]
    chunk_overlap = cfg["indexing"]["chunk_overlap"]
    batch_size = cfg["indexing"]["batch_size"]

    # Fail fast on chunking misconfiguration rather than building a corrupt index.
    # chunk_size < 1 makes every window empty (end == start), so collection.add()
    # ingests blank documents. chunk_overlap >= chunk_size forces the stride down
    # to a single word, exploding a modest corpus into one chunk per word and
    # exhausting memory. The chunk_document() max(1, ...) clamp keeps direct
    # callers finite, but the config boundary is where a human-set value belongs.
    if chunk_size < 1:
        raise ValueError(f"chunk_size must be >= 1, got {chunk_size}")
    if chunk_overlap < 0:
        raise ValueError(f"chunk_overlap must be >= 0, got {chunk_overlap}")
    if chunk_overlap >= chunk_size:
        raise ValueError(f"chunk_overlap ({chunk_overlap}) must be < chunk_size ({chunk_size})")
    # batch_size drives range(0, N, batch_size) over the chunk list: a negative
    # value yields an empty range so the semantic (Chroma) leg is never written
    # — every query then falls back to BM25-only, whose rebased scores top out
    # below min_score, silently failing the confidence gate for the whole
    # corpus — and 0 raises "range() arg 3 must not be zero" mid-build.
    if batch_size < 1:
        raise ValueError(f"batch_size must be >= 1, got {batch_size}")

    logger.info("Loading corpus from %s", corpus_path)
    docs = load_corpus(corpus_path, extensions)
    logger.info("Loaded %d documents", len(docs))

    all_chunks = []
    all_metadata = []
    # Tokenize each chunk exactly once. The full token list feeds the BM25
    # index; the first 20 tokens become the stem_tags metadata. Re-tokenizing
    # the whole corpus a second time for BM25 (the previous behaviour) doubled
    # the regex/stemming work at index time for no benefit.
    tokenized_corpus = []

    for source, content in docs:
        source_sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
        chunks = chunk_document(content, chunk_size, chunk_overlap)
        if not chunks:
            # An empty/whitespace-only file passes load_corpus (it reads fine)
            # but splits to zero words -- without this warning it would vanish
            # from both indices with no trace in the build log.
            logger.warning("Document %s produced 0 chunks (empty or whitespace-only); skipping", source)
            continue
        untokenized_chunks = 0
        for i, chunk in enumerate(chunks):
            clean_chunk = sanitize_chunk(chunk, config_path_str)
            tokens = tokenize_and_stem(clean_chunk)
            if not tokens and clean_chunk.strip():
                untokenized_chunks += 1
            all_chunks.append(clean_chunk)
            tokenized_corpus.append(tokens)
            all_metadata.append(
                {"source": source, "chunk_id": i, "source_sha256": source_sha256, "stem_tags": json.dumps(tokens[:20])}
            )
        if untokenized_chunks:
            # The BM25 tokenizer is deliberately ASCII-only (see
            # retrieval/stemmer.py _WORD_RE -- avoids the nltk punkt CVE), so
            # non-Latin content yields zero keyword tokens. Such chunks are
            # still indexed semantically but are invisible to the keyword leg;
            # surface that at build time instead of silently degrading hybrid
            # fusion to semantic-only for those documents.
            logger.warning(
                "Document %s: %d/%d chunk(s) produced no BM25 tokens "
                "(non-ASCII or symbol-only content); keyword search will not see them",
                source, untokenized_chunks, len(chunks),
            )

    logger.info("Total chunks: %d", len(all_chunks))

    # Semantic (vector) index. The backend is pluggable — ChromaDB by default
    # (embedded, offline-first), or pgvector when indexing.vector_backend=pgvector.
    # Cosine space / `1 - distance` scoring and the per-chunk metadata are identical
    # across backends, so RRF order and the min_score gate are unaffected (PR #99
    # #1/#6); only where the vectors live changes.
    backend = vector_backend(cfg)
    writer = get_vector_writer(cfg)
    writer.reset()
    logger.info("Building semantic (vector) index [%s]...", backend)
    try:
        for batch_start in range(0, len(all_chunks), batch_size):
            batch_end = min(batch_start + batch_size, len(all_chunks))
            batch_chunks = all_chunks[batch_start:batch_end]
            batch_meta = all_metadata[batch_start:batch_end]
            batch_embeddings = get_embeddings_batch(batch_chunks, config_path_str)
            batch_ids = [f"chunk_{batch_start + i}" for i in range(len(batch_chunks))]
            writer.add(batch_ids, batch_chunks, batch_embeddings, batch_meta)
            logger.info("Indexed %d/%d chunks", batch_end, len(all_chunks))
        writer.finalize()
    finally:
        writer.close()

    logger.info("Building BM25 (keyword) index...")
    # tokenized_corpus was built alongside all_chunks above (single tokenization pass).
    Path(bm25_path).parent.mkdir(parents=True, exist_ok=True)
    with open(bm25_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "tokenized_corpus": tokenized_corpus,
                "chunks": all_chunks,
                "metadata": all_metadata,
            },
            f,
        )

    logger.info("Done. Semantic backend: %s, BM25: %s", backend, bm25_path)


def main() -> None:
    """Console entry point for ``cyclaw-index`` (see pyproject [project.scripts]).

    Thin wrapper over :func:`build_index`. The declared
    ``cyclaw-index = "retrieval.indexer:main"`` script previously raised
    AttributeError because this module only defined ``build_index``.

    Configures root logging here (not at import time) so the CLI keeps showing
    progress, while importers of ``build_index`` control their own log handlers.
    """
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    build_index()


if __name__ == "__main__":
    main()
