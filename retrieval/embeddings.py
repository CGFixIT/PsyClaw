"""Local embedding service using sentence-transformers.

No Ollama. No external service required. The embedding model's device is
hardcoded to CPU -- see EMBED_DEVICE below for why this is pinned rather than
auto-selected.
Caches the model AND the parsed config across calls to avoid reload/reparse
overhead on the hot query path.

Security note (2026-06):
- We delegate model loading to sentence-transformers.
- Prefer safetensors format for any custom or local models.
- Historical: CVE-2025-32434 showed that torch.load(..., weights_only=True) was bypassable for RCE on torch<2.6.0.
- We now pin torch==2.13.0+cpu (see pyproject.toml) and treat untrusted .pth/.bin files as high risk.
- Model weights should come from verified/trusted sources only (HF official or local hashed files).
"""

import logging
import os
import time
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from utils.errors import EmbeddingServiceError

if TYPE_CHECKING:
    # Type-only: the real import stays inside _load_model so importing this
    # module never drags in sentence-transformers (and torch) at startup.
    from sentence_transformers import SentenceTransformer

log = logging.getLogger("cyclaw.retrieval.embeddings")

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

# sentence-transformers auto-selects a device via its own get_device_name()
# ladder (cuda -> mps -> npu -> xpu -> hpu -> cpu) whenever SentenceTransformer(...)
# is constructed with no explicit device=. On Apple Silicon that lands on "mps"
# (confirmed via macOS CI: get_device_name() == "mps", and the loaded model's own
# .device == device(type="mps", index=0)) -- and that measurably shifts retrieval
# rankings vs. the CPU path Linux/Windows take, not mere floating-point noise (a
# query's expected top hit moved from rank 3 to outside the top 30 semantic
# candidates; PR #734). Pinned to CPU so every platform embeds identically.
# This is the ~22M-parameter embedding model ONLY -- it has no effect on the
# local LLM (Ollama, configured separately under models.local_llm), which keeps
# using whatever GPU/Metal acceleration is available to it.
EMBED_DEVICE = "cpu"


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
def _load_model(model_name: str, cache_dir: str) -> "SentenceTransformer":
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

    The env vars alone do NOT make this process offline: huggingface_hub.
    constants.HF_HUB_OFFLINE is read from os.environ once, at huggingface_hub's
    own import time, and _model_offline_eligible's own `from huggingface_hub
    import try_to_load_from_cache` above has already forced that import (and
    latched the constant to whatever the environment said BEFORE this
    function ran) -- so setting os.environ here, after the probe, is too late
    to change huggingface_hub's in-process is_offline_mode() for this run.
    (Verified 2026-07-30: a fresh interpreter that imports huggingface_hub via
    the probe, then sets HF_HUB_OFFLINE=1, still reports is_offline_mode() ==
    False.) The env vars are kept anyway -- for subprocess inheritance, and so
    an operator's own pre-set stricter value (present before this process
    started, i.e. before the latch) is honored, which still works correctly.
    What actually enforces offline behavior IN this process is the
    `local_files_only` argument below, passed straight through by
    sentence-transformers to huggingface_hub's download path, which gates
    independently of the (broken-for-this-purpose) is_offline_mode() global.
    """
    eligible = _model_offline_eligible(model_name, cache_dir)
    if eligible:
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"

    from sentence_transformers import SentenceTransformer

    for attempt in range(_MODEL_LOAD_MAX_ATTEMPTS):
        try:
            return SentenceTransformer(
                model_name,
                cache_folder=cache_dir or None,
                local_files_only=eligible,
                device=EMBED_DEVICE,
            )
        except (OSError, RuntimeError):
            if attempt == _MODEL_LOAD_MAX_ATTEMPTS - 1:
                raise
            time.sleep(_MODEL_LOAD_RETRY_DELAY_SEC)
    # Unreachable: the final attempt either returns or re-raises. Spelled out
    # rather than left to fall off the end, mirroring llm/client.py's retry
    # helper -- an implicit `return None` here would reach callers as
    # `None.encode(...)`, and AttributeError is NOT in _cached_embedding's
    # catch tuple, so it would escape the documented degrade-to-keyword path.
    log.error("embedding model load loop exited without return or raise -- this is a bug")  # pragma: no cover
    raise EmbeddingServiceError(  # pragma: no cover
        "embedding model load loop exited without return/raise",
        details={"model": model_name},
    )

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


def embedding_fingerprint(embeddings_cfg: dict) -> dict[str, str]:
    """Build the {model, dim, device} stamp recorded on a freshly built index.

    Read back at query time (via the vector store's own ``fingerprint()``) to
    detect an index built under a different embedding model, dimension, or
    device than the one currently configured -- most notably an index built
    before EMBED_DEVICE existed, while sentence-transformers auto-selected
    mps/cuda. ``embeddings_cfg`` is the raw ``models.embeddings`` config
    section (whatever ``.get()`` chaining callers already use), not the
    ``_embeddings_cfg``-cached tuple -- callers that only have the model name
    can pass ``{"model": name}``. Values are always stringified: they end up
    as ChromaDB collection metadata, which only stores str/int/float/bool.
    """
    return {
        "model": str(embeddings_cfg.get("model", "")),
        "dim": str(embeddings_cfg.get("dim", "")),
        "device": EMBED_DEVICE,
    }


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
