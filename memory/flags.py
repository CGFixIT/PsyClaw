"""Resolution of the memory subsystem's facts flag, with legacy-name support.

Split out of retrieval_adapter so every reader resolves the flag identically:
the retrieval gate is duplicated in retrieval/hybrid_search.py (which keeps its
own pre-check to avoid importing this package when memory is off), and
mirror.py reports the same value on /memory/status. Three readers of one flag
drift; one function does not.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger("cyclaw.memory.flags")

# Renamed because the old name overpromised: this flag only ever gated
# retrieval fusion. It never gated persistence, apply, or read -- create_proposal
# and apply_proposal do not consult it, and GET /memory/facts checks only the
# master switch -- so `facts.enabled` read as a master switch for facts that
# does not exist. Episodes really do self-gate their write path
# (store.stage_episode), which is what made the asymmetry confusing.
_KEY = "retrieval_enabled"
# The legacy name stays honored, and this is load-bearing rather than politeness:
# nothing rejects unknown config keys (there is no validate_memory_config; gate.py
# runs five validators, none covering `memory:`) and every read site defaults to
# falsy. So an operator upgrading with `enabled: true` in their own config.yaml
# would silently lose fusion -- no exception, no log, and a /memory/status still
# reporting their facts as present. Honor it and say so instead.
_LEGACY_KEY = "enabled"

_warned = False


def facts_retrieval_enabled(mem_cfg: Mapping[str, Any] | None) -> bool:
    """True when approved facts should fuse into hybrid retrieval.

    Reads ``facts.retrieval_enabled``, falling back to the legacy
    ``facts.enabled``. The new key wins whenever it is present, even if it is
    False. Warns once per process when the legacy key is what supplied the
    value, naming both keys.
    """
    global _warned

    facts = (mem_cfg or {}).get("facts")
    if not isinstance(facts, Mapping):
        return False
    if _KEY in facts:
        return facts.get(_KEY) is True
    if _LEGACY_KEY in facts:
        if not _warned:
            _warned = True
            logger.warning(
                "config.yaml uses the legacy memory.facts.%s key; rename it to "
                "memory.facts.%s. It gates retrieval fusion only -- never "
                "persistence, apply, or read.",
                _LEGACY_KEY,
                _KEY,
            )
        return facts.get(_LEGACY_KEY) is True
    return False
