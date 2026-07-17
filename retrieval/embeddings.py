"""Local embedding service using sentence-transformers.

CPU-only. No Ollama. No external service required.
Caches the model AND the parsed config across calls to avoid reload/reparse
overhead on the hot query path.

Security note (2026-06):
- We delegate model loading to sentence-transformers.
- Prefer safetensors format for any custom or local models.
- Historical: CVE-2025-32434 showed that torch.load(..., weights_only=True) was bypassable for RCE on torch<2.6.0.
- We now pin torch==2.12.1+cpu (see pyproject.toml) and treat untrusted .pth/.bin files as high risk.
- Model weights should come from verified/trusted sources only (HF official or local hashed files).
"""

import os
from functools import lru_cache
from pathlib import Path

import yaml

from utils.errors import EmbeddingServiceError

os.environ["TOKENIZERS_PARALLELISM"] = "false"


def resolve_cache_dir(config_path: str, cache_dir: str | None) -> str:
    """Resolve a configured embedding cache path relative to its config file."""
    if not cache_dir:
        return ""
    if cache_dir.startswith(("/", "\\")):
        return cache_dir
    path = Path(cache_dir).expanduser()
    if path.is_absolute():
        return str(path)
    return str((Path(config_path).expanduser().resolve().parent / path).resolve())


@lru_cache(maxsize=1)
def _load_model(model_name: str, cache_dir: str):
    """Load SentenceTransformer with security-conscious defaults.

    Note: sentence-transformers will use safetensors when available.
    If a .pth or .bin file is explicitly provided via model_name, it may still
    hit torch.load paths. Treat such cases as requiring extra scrutiny.
    """
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(model_name, cache_folder=cache_dir or None)

@lru_cache(maxsize=8)
def _embeddings_cfg(config_path: str) -> tuple:
    """Read models.embeddings from config once per path (cached).

    Returns (model_name, cache_dir). Uses a context manager so the config file
    handle is always closed -- the previous ``yaml.safe_load(open(path))`` form
    leaked a descriptor on every call.
    """
    with open(config_path, encoding="utf-8") as f:
        emb_cfg = yaml.safe_load(f)["models"]["embeddings"]
    return emb_cfg["model"], resolve_cache_dir(config_path, emb_cfg.get("cache_dir", ""))

def _default_query_cache_size() -> int:
    """Resolve the query-embedding LRU cache size from CYCLAW_EMBED_CACHE_SIZE.

    functools.lru_cache fixes maxsize at decoration time -- config.yaml isn't
    parsed until the first call, so the size can't be read from it directly.
    An env var lets operators tune the cache (memory footprint vs. hit rate)
    per deployment without a code change; falls back to the prior hardcoded
    2048 when unset or invalid.
    """
    raw = os.environ.get("CYCLAW_EMBED_CACHE_SIZE", "")
    if raw:
        try:
            size = int(raw)
        except ValueError:
            size = 0
        if size > 0:
            return size
    return 2048


@lru_cache(maxsize=_default_query_cache_size())
def _cached_embedding(text: str, config_path: str) -> tuple:
    """Memoize query embeddings keyed on (text, config_path).

    Encoding a query is a full SentenceTransformer forward pass -- the most
    expensive step on the retrieval hot path. Identical queries (common in
    practice) previously re-ran the model every time. The cached value is an
    immutable tuple so it can be safely shared across callers.

    Failures are wrapped as EmbeddingServiceError so hybrid_search's
    documented degrade-to-keyword-only catch actually fires -- before this
    wrap, a real model failure (missing package, corrupt cache_dir, OOM)
    escaped as a raw ImportError/OSError/RuntimeError that nothing on the
    query path caught, crashing the request instead of degrading.
    lru_cache does not memoize exceptions, so a transient failure is retried
    on the next call rather than poisoning the cache.
    """
    try:
        model_name, cache_dir = _embeddings_cfg(config_path)
        model = _load_model(model_name, cache_dir)
        return tuple(model.encode(text, normalize_embeddings=True).tolist())
    except EmbeddingServiceError:
        raise
    except (ImportError, OSError, RuntimeError, ValueError) as e:
        raise EmbeddingServiceError(
            f"query embedding failed: {e}",
            details={"error_type": type(e).__name__},
        ) from e

def get_embedding(text: str, config_path: str = "config.yaml") -> list[float]:
    return list(_cached_embedding(text, config_path))

def reset_embedding_cache() -> None:
    """Clear ALL embedding caches so a config/model swap takes full effect.

    A model swap edits ``models.embeddings`` in config.yaml, so clearing only the
    query-embedding memo (``_cached_embedding``) is not enough: the parsed config
    (``_embeddings_cfg``) and the loaded SentenceTransformer (``_load_model``) are
    independently cached and would keep serving the OLD model name and weights —
    silently defeating the swap. Clear all three together so the next call
    reloads from the current config.

    ``cache_clear`` is resolved defensively because tests monkeypatch
    ``_load_model`` with a plain callable that has no ``cache_clear`` attribute.
    """
    for cache in (_cached_embedding, _embeddings_cfg, _load_model):
        clear = getattr(cache, "cache_clear", None)
        if clear is not None:
            clear()

def get_embeddings_batch(texts: list[str], config_path: str = "config.yaml") -> list[list[float]]:
    # Deliberately NOT wrapped in EmbeddingServiceError: this is the index-build
    # path (cyclaw-index), where a model failure must abort the build loudly --
    # degrading here would silently produce a semantic index with missing
    # vectors. Only the query path (_cached_embedding) soft-degrades.
    model_name, cache_dir = _embeddings_cfg(config_path)
    model = _load_model(model_name, cache_dir)
    return model.encode(texts, normalize_embeddings=True, show_progress_bar=True).tolist()
