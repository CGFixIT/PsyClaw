"""
Enhanced Porter-like stemmer — optimized for technical/AI vocabulary.

Extends NLTK PorterStemmer with custom rules for:
- AI/ML terms (embedding, transformer, attention, etc.)
- DevOps/infra terms (kubernetes, docker, nginx, etc.)
- CyClaw-domain terms (retrieval, augmented, langgraph, etc.)
"""

import re
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nltk.stem import PorterStemmer


@lru_cache(maxsize=1)
def _porter() -> "PorterStemmer":
    # Deferred to first stem: the module-level `from nltk.stem import ...` +
    # PorterStemmer() pulled the whole nltk package into every process that
    # imports retrieval/ — ~140 ms of gate.py / mcp_hybrid_server.py cold start
    # (measured via python -X importtime) paid even when no token is ever
    # stemmed (e.g. server boot with a missing index). First stem_token() call
    # pays it once; steady-state cost is unchanged (lru_cache hit).
    from nltk.stem import PorterStemmer

    return PorterStemmer()

# Tokenizer: extracts words starting with a letter (2+ chars). Avoids nltk.data.load()
# so the NLTK URL-encoded path-traversal CVE (punkt tokenizer) is not reachable.
# Compiled once at import rather than on every call. findall() returns only
# maximal runs of this exact shape, so every token is already letter-led and
# >= 2 chars by construction — no second-pass validation is required.
_WORD_RE = re.compile(r'[a-z][a-z0-9_-]+')

_CUSTOM_STEMS = {
    "embedding": "embed", "embeddings": "embed",
    "transformer": "transform", "transformers": "transform",
    "attention": "attn", "attentional": "attn",
    "retrieval": "retriev", "retrieve": "retriev", "retrieved": "retriev",
    "augmented": "augment", "augmentation": "augment",
    "kubernetes": "k8s", "docker": "docker", "nginx": "nginx",
    "langgraph": "langgraph", "langchain": "langchain",
    "chromadb": "chroma", "chroma": "chroma",
    "cyclaw": "cyclaw", "safeclaw": "safeclaw",
    "personality": "person", "soul": "soul",
}

@lru_cache(maxsize=100_000)
def stem_token(token: str) -> str:
    lower = token.lower()
    if lower in _CUSTOM_STEMS:
        return _CUSTOM_STEMS[lower]
    return _porter().stem(lower)

@lru_cache(maxsize=4096)
def _tokenize_and_stem_cached(text: str) -> tuple[str, ...]:
    # _WORD_RE.findall() already guarantees each token matches [a-z][a-z0-9_-]+
    # (letter-led, length >= 2). The previous `if _TOKEN_RE.match(t)` filter
    # re-validated that exact same shape and therefore always returned True —
    # a redundant per-token regex match on the index/query hot path. Dropping it
    # produces byte-for-byte identical output with one fewer regex op per token.
    #
    # Returns a tuple, not a list: stem_token() is already memoized per-token,
    # but repeated identical queries (common on the retrieval hot path) still
    # paid the regex findall() + list-build on every call. Caching here skips
    # that too. A tuple (immutable) is cached rather than a list so a caller
    # can never mutate the cached object in place and corrupt it for the next
    # hit — tokenize_and_stem() below converts back to a fresh list per call.
    return tuple(stem_token(t) for t in _WORD_RE.findall(text.lower()))


def tokenize_and_stem(text: str) -> list[str]:
    return list(_tokenize_and_stem_cached(text))
