"""Local embedding service using sentence-transformers.

CPU-only. No Ollama. No external service required.
Caches the model AND the parsed config across calls to avoid reload/reparse
overhead on the hot query path.

Security note (2026-06):
- We delegate model loading to sentence-transformers.
- Prefer safetensors format for any custom or local models.
- Historical: CVE-2025-32434 showed that torch.load(..., weights_only=True) was bypassable for RCE on torch<2.6.0.
- We now pin torch==2.13.0+cpu (see pyproject.toml) and treat untrusted .pth/.bin files as high risk.
- Model weights should come from verified/trusted sources only (HF official or local hashed files).
"""

import os
import time
from functools import lru_cache
from pathlib import Path

import yaml

from utils.errors import EmbeddingServiceError

os.environ["TOKENIZERS_PARALLELISM"] = "false"

# On a cache miss, loading the model fetches it from HuggingFace (see
# _load_model below) -- a transient network hiccup during that one-time fetch
# would otherwise raise straight out of the first query or index build.
# Bounded retry matches the pattern already used for LLM calls
# (llm/client.py's _post_with_retry); no config knob for this one since it is
# a one-time load, not a hot path (see also CI's own actions/cache step for
# .emb_cache, which avoids the fetch entirely on a cache hit).
_MODEL_LOAD_MAX_ATTEMPTS = 3
_MODEL_LOAD_RETRY_DELAY_SEC = 2.0

# The one file every HF Hub model repo carries, used purely as a cheap presence
# probe below -- confirmed present for the shipped default (all-MiniLM-L6-v2)
# via a live fetch during development of this check.
_CACHE_PROBE_FILENAME = "config.json"


def _model_offline_eligible(model_name: str, cache_dir: str) -> bool:
    """True if huggingface_hub's own on-disk cache index already has this model.

    Scopes HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE (set by the caller below) to runs
    where they cannot break anything: forcing offline mode unconditionally would
    turn the documented cache-miss bootstrap fetch above into a guaranteed
    failure on any machine that has never run CyClaw before, since
    huggingface_hub.constants freezes HF_HUB_OFFLINE at its own import time --
    once set, no retry in this process can undo it.

    Uses ``try_to_load_from_cache`` -- huggingface_hub's own public, disk-only
    cache lookup (it makes no network call) -- rather than re-deriving the
    blobs/snapshots cache layout by hand. ``cache_folder`` (this module's
    parameter) flows straight through SentenceTransformer -> Transformer as
    huggingface_hub's own ``cache_dir``, so the same helper that library uses
    internally applies here unchanged.

    Checks a single well-known file, not the model's full file set: knowing
    every file a specific model needs would itself require asking the Hub.
    That is the same level of certainty SentenceTransformer's own internal
    cache check already accepts -- this is a cheap pre-check ahead of it, not a
    replacement for it.

    Any ambiguity -- huggingface_hub not importable yet, an unexpected return
    shape, a probe error -- resolves to False (not cached). That is the
    direction that preserves the existing online-fetch-on-cache-miss behavior;
    the alternative (assume cached on doubt) risks silently forcing offline
    mode on a machine that actually needs the network fetch to succeed.
    """
    try:
        from huggingface_hub import try_to_load_from_cache
    except ImportError:
        return False
    try:
        hit = try_to_load_from_cache(
            repo_id=model_name, filename=_CACHE_PROBE_FILENAME, cache_dir=cache_dir or None
        )
    except Exception:  # noqa: BLE001 -- a probe failure must never block model load
        return False
    # try_to_load_from_cache returns the file path (str) on a real hit; None
    # when never fetched; or a private sentinel object when the Hub previously
    # answered "this file does not exist" for this repo/revision (itself only
    # knowable from an earlier online call). Only the str case is a usable
    # local copy -- checking isinstance rather than importing that sentinel
    # keeps this from depending on a huggingface_hub internal name.
    return isinstance(hit, str)


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

    Retries a bounded number of times on OSError/RuntimeError: on a cache miss
    this call fetches the model over the network, and huggingface_hub surfaces
    a transient connection failure as one of those two types. lru_cache does
    not memoize a raised exception, so a still-failing final attempt propagates
    normally and the next call retries from scratch.

    Sets HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE when _model_offline_eligible finds
    this model already on disk -- see that function for why this is
    conditional rather than unconditional. Deliberately only ever SETS these to
    "1" here; it never clears or overrides them when the model is not yet
    cached, so an operator who has already opted into full lockdown by
    sourcing docs/security-philosophy/cyclaw_telemetry_kill.env by hand keeps
    that explicit, stricter choice regardless of what this probe finds.
    """
    if _model_offline_eligible(model_name, cache_dir):
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    from sentence_transformers import SentenceTransformer

    for attempt in range(_MODEL_LOAD_MAX_ATTEMPTS):
        try:
            return SentenceTransformer(model_name, cache_folder=cache_dir or None)
        except (OSError, RuntimeError):
            if attempt == _MODEL_LOAD_MAX_ATTEMPTS - 1:
                raise
            time.sleep(_MODEL_LOAD_RETRY_DELAY_SEC)

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
