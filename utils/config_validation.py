"""Startup validation for config.yaml tunables.

Validates ``retrieval`` and ``personality`` blocks at boot so typos like
``min_score: 1.5`` or ``soul_max_chars: 0`` surface as a clear ``ConfigError``
instead of silent mis-routing or empty-prompt degradation at request time.

Mirrors the dataclass ``__post_init__`` validation that ``sync/config.py`` and
``agentic/config.py`` already perform for their blocks.
"""

from __future__ import annotations

import math
from typing import Any

from utils.errors import ConfigError

# Tunables that must be positive integers (they index ranked result lists and
# appear in the RRF weight denominator ``1 / (rrf_k + rank)``).
_POSITIVE_INT_KEYS = ("top_k_semantic", "top_k_keyword", "rrf_k")


def _is_real_number(value: Any) -> bool:
    """True for int/float but NOT bool (bool is an int subclass in Python)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_retrieval_config(cfg: dict[str, Any]) -> None:
    """Validate ``cfg['retrieval']``. Raise ``ConfigError`` on any invalid value.

    Checks:
      * the ``retrieval`` block exists and is a mapping;
      * ``min_score`` is a number in ``[0, 1]`` (RRF-fused scores live there);
      * ``top_k_semantic`` / ``top_k_keyword`` / ``rrf_k`` are positive integers.

    Valid configs (the shipped defaults: ``min_score: 0.028``, ``top_k_*: 5``,
    ``rrf_k: 60``) pass unchanged -- this only rejects out-of-range typos.
    """
    retrieval = cfg.get("retrieval")
    if not isinstance(retrieval, dict):
        raise ConfigError(
            "config.retrieval block is missing or not a mapping",
            details={"received_type": type(retrieval).__name__},
        )

    min_score = retrieval.get("min_score")
    if not _is_real_number(min_score) or not 0 <= min_score <= 1:
        raise ConfigError(
            f"retrieval.min_score must be a number in [0, 1], got: {min_score!r}",
            details={"received": min_score},
        )

    for key in _POSITIVE_INT_KEYS:
        val = retrieval.get(key)
        if not isinstance(val, int) or isinstance(val, bool) or val <= 0:
            raise ConfigError(
                f"retrieval.{key} must be a positive integer, got: {val!r}",
                details={"received": val, "key": key},
            )


# Documented in config.yaml: graph_timeout >= llm_timeout + 30 (retrieval +
# routing + cold embed headroom). Enforced as a boot hard-fail when both
# values are present.
_GRAPH_LLM_TIMEOUT_MARGIN_SEC = 30


def validate_boot_timeout_config(cfg: dict[str, Any]) -> None:
    """Validate ``api.graph_timeout_sec`` vs ``models.local_llm.timeout_sec``.

    Requires ``graph_timeout >= llm_timeout + 30`` when both are present
    (``config.yaml`` formula). A 1s headroom pair still makes the LLM timeout
    effectively unreachable under load.

    No-op when either value is absent or not a real number -- gate.py's own
    ``cfg.get(..., default)`` calls already treat those cases as "use the
    default."
    """
    api = cfg.get("api")
    models = cfg.get("models")
    if not isinstance(api, dict) or not isinstance(models, dict):
        return
    local_llm = models.get("local_llm")
    if not isinstance(local_llm, dict):
        return
    graph_timeout = api.get("graph_timeout_sec")
    llm_timeout = local_llm.get("timeout_sec")
    if not _is_real_number(graph_timeout) or not _is_real_number(llm_timeout):
        return
    if llm_timeout >= graph_timeout:
        raise ConfigError(
            f"models.local_llm.timeout_sec ({llm_timeout}) must be less than "
            f"api.graph_timeout_sec ({graph_timeout}) -- otherwise the graph "
            "deadline fires first and the per-call LLM timeout is unreachable",
            details={"llm_timeout_sec": llm_timeout, "graph_timeout_sec": graph_timeout},
        )
    min_graph = llm_timeout + _GRAPH_LLM_TIMEOUT_MARGIN_SEC
    if graph_timeout < min_graph:
        raise ConfigError(
            f"api.graph_timeout_sec ({graph_timeout}) must be at least "
            f"models.local_llm.timeout_sec + {_GRAPH_LLM_TIMEOUT_MARGIN_SEC} "
            f"({min_graph}); formula: graph_timeout >= llm_timeout + "
            f"{_GRAPH_LLM_TIMEOUT_MARGIN_SEC}",
            details={
                "llm_timeout_sec": llm_timeout,
                "graph_timeout_sec": graph_timeout,
                "required_margin_sec": _GRAPH_LLM_TIMEOUT_MARGIN_SEC,
            },
        )


def validate_fallback_confirm_placeholder(cfg: dict[str, Any]) -> None:
    """Reject a false ``policy.fallback.require_user_confirm`` placeholder.

    The key is **not wired** into graph routing (confirm pause is hardcoded).
    ``false`` would silently fail to skip confirmation — refuse it at boot so
    operators cannot believe the switch works. ``true`` or absent is fine.
    """
    policy = cfg.get("policy")
    if not isinstance(policy, dict):
        return
    fallback = policy.get("fallback")
    if not isinstance(fallback, dict):
        return
    if "require_user_confirm" not in fallback:
        return
    val = fallback["require_user_confirm"]
    if val is True:
        return
    raise ConfigError(
        "policy.fallback.require_user_confirm is not wired to the graph "
        "(confirmation is always enforced in user_gate_router). Only true "
        "is allowed; setting false has no effect and is rejected so it "
        "cannot be mistaken for a live safety switch.",
        details={"received": val, "hint": "Leave true or omit the key."},
    )


def validate_personality_config(cfg: dict[str, Any]) -> None:
    """Validate ``cfg['personality']`` when the subsystem is enabled.

    Checks:
      * ``soul_max_chars`` is a positive integer (0 silently truncates the soul
        to empty, dropping personality from every LLM prompt with no warning).

    No-op when ``personality.enabled`` is false or the block is absent.
    """
    personality = cfg.get("personality")
    if not isinstance(personality, dict) or not personality.get("enabled", False):
        return

    soul_max_chars = personality.get("soul_max_chars")
    if soul_max_chars is not None:
        if not isinstance(soul_max_chars, int) or isinstance(soul_max_chars, bool) or soul_max_chars <= 0:
            raise ConfigError(
                f"personality.soul_max_chars must be a positive integer, got: {soul_max_chars!r}",
                details={"received": soul_max_chars},
            )


def validate_auth_config(cfg: dict[str, Any]) -> None:
    """Validate ``cfg['auth']`` when the subsystem is enabled.

    Checks:
      * ``session.idle_timeout_sec`` / ``session.absolute_timeout_sec`` are
        positive numbers;
      * ``idle_timeout_sec <= absolute_timeout_sec`` -- otherwise the idle
        window can never be the one that expires a session first, which is a
        config mistake worth catching at boot rather than discovering that a
        "12h idle" setting silently never applies.

    No-op when ``auth.enabled`` is false or the block is absent (Stage 1/2's
    shipped-disabled default). A PRESENT but malformed ``auth`` block (not a
    mapping at all -- e.g. ``auth: "banana"``) always raises, even with
    enabled defaulting false, because gate.py's own ``cfg.get("auth",
    {}).get("enabled", False)`` has no isinstance guard: without this check
    running first, that construction crashes with an unhandled AttributeError
    instead of this module's own clear, typed ConfigError.
    """
    auth = cfg.get("auth")
    if auth is None:
        return
    if not isinstance(auth, dict):
        raise ConfigError(
            f"config.auth must be a mapping, got: {type(auth).__name__}",
            details={"received_type": type(auth).__name__},
        )
    # `is not True`, not truthy `not auth.get("enabled", False)`: gate.py
    # reads this same key strictly in two places (_boot_auth_enabled,
    # _flag_is_true via _auth_and_tls_enabled) specifically so a quoted
    # `enabled: "false"`/`"true"` string is never mistaken for the literal
    # boolean. A truthy read here disagreed with both: `"false"` (truthy)
    # would validate the session block for a config gate.py treats as OFF --
    # failing boot over a block that will never be read -- and `"true"`
    # (also truthy) would validate and pass clean for a config gate.py
    # treats as OFF too, signing off on a misconfiguration with no
    # diagnostic anywhere in the boot sequence.
    if auth.get("enabled") is not True:
        return

    session = auth.get("session", {})
    if not isinstance(session, dict):
        raise ConfigError(
            f"auth.session must be a mapping, got: {type(session).__name__}",
            details={"received_type": type(session).__name__},
        )

    idle = session.get("idle_timeout_sec", 43200)
    absolute = session.get("absolute_timeout_sec", 604800)
    for name, val in (("idle_timeout_sec", idle), ("absolute_timeout_sec", absolute)):
        # math.isfinite, not just `val <= 0`: NaN and +/-inf are real floats,
        # so _is_real_number accepts them, and EVERY comparison against NaN
        # is False -- `nan <= 0` is False, so a NaN timeout sailed through
        # this check silently. Downstream that is not cosmetic:
        # validate_session()'s idle-expiry comparison against a NaN idle
        # timeout is permanently False (sessions never idle-expire), a NaN
        # absolute timeout binds as SQL NULL into a NOT NULL column and
        # every login fails, and `int(manager.absolute_timeout_sec)` at
        # cookie-issuance time raises OverflowError for +inf.
        if not _is_real_number(val) or not math.isfinite(val) or val <= 0:
            raise ConfigError(
                f"auth.session.{name} must be a finite positive number, got: {val!r}",
                details={"received": val, "key": name},
            )
    if idle > absolute:
        raise ConfigError(
            f"auth.session.idle_timeout_sec ({idle}) must be <= "
            f"absolute_timeout_sec ({absolute}) -- otherwise the idle window "
            "can never be the one that expires a session",
            details={"idle_timeout_sec": idle, "absolute_timeout_sec": absolute},
        )
