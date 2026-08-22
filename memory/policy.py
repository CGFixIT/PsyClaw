"""Reason gate, size caps, and injection scanning for memory writes."""

from __future__ import annotations

import re
from typing import Any

from utils.errors import PromptInjectionError
from utils.personality import ENFORCED_SOUL_PATTERNS, OWASP_INJECTION_PATTERNS

# Reused rather than re-implemented, same reasoning utils/authn_store.py's
# import of personality_db._harden_pg_conninfo documents: this is the exact
# NFKC-fold + invisible-character-strip utils/sanitizer.py's check_input
# already applies before matching, and a second hand-rolled copy would only
# ever drift from it, not improve on it.
from utils.sanitizer import _normalize_for_match


def require_reason(reason: str) -> None:
    """Raise ValueError if reason is missing/blank (mirrors soul apply)."""
    if not reason or not str(reason).strip():
        raise ValueError("reason must not be empty")


def _compile_patterns(base: list[str], cfg: dict[str, Any]) -> list[tuple[str, re.Pattern[str]]]:
    sources: list[str] = list(base)
    pf = (cfg.get("policy") or {}).get("prompt_filter") or {}
    for p in pf.get("banned_patterns") or []:
        if isinstance(p, str) and p not in sources:
            sources.append(p)
    compiled: list[tuple[str, re.Pattern[str]]] = []
    for p in sources:
        try:
            # IGNORECASE | DOTALL, matching utils/sanitizer.py's compile flags:
            # DOTALL so a pattern whose halves straddle a newline still
            # matches (e.g. 'maintenance\s+mode.*safety\s+filters\s+disabled'
            # split across two lines would otherwise slip through).
            compiled.append((p, re.compile(p, re.IGNORECASE | re.DOTALL)))
        except re.error:
            continue
    return compiled


def scan_content(content: str, cfg: dict[str, Any], *, enforced: bool = True) -> list[str]:
    """Return matched pattern sources. enforced=True uses critical set only."""
    base = ENFORCED_SOUL_PATTERNS if enforced else OWASP_INJECTION_PATTERNS
    # Match against a normalized copy, same as utils/sanitizer.py's
    # check_input: NFKC folds fullwidth/compatibility Unicode forms back to
    # the ASCII the patterns are written in, and stripping invisible
    # characters closes the zero-width-splitting evasion. Only ever folds
    # TOWARD what the patterns already catch, so this cannot stop catching
    # something the unnormalized text used to match.
    probe = _normalize_for_match(content or "")
    return [src for src, pat in _compile_patterns(base, cfg) if pat.search(probe)]


def enforce_content(content: str, cfg: dict[str, Any]) -> None:
    """Raise PromptInjectionError if critical injection patterns match."""
    flags = scan_content(content, cfg, enforced=True)
    if flags:
        raise PromptInjectionError(
            "Proposed memory fact contains critical injection patterns; refusing to apply",
            details={"injection_flags": flags, "injection_flag_count": len(flags)},
        )


def check_content_size(content: str, cfg: dict[str, Any]) -> None:
    mem = cfg.get("memory") or {}
    facts = mem.get("facts") or {}
    max_chars = int(facts.get("max_content_chars", 8192))
    if not content or not str(content).strip():
        raise ValueError("fact content must not be empty")
    if len(content) > max_chars:
        raise ValueError(f"fact content exceeds max_content_chars={max_chars}")


def check_tags(tags: list[str] | None, *, max_tags: int = 32, max_tag_len: int = 64) -> list[str]:
    cleaned: list[str] = []
    for t in tags or []:
        s = str(t).strip()
        if not s:
            continue
        if len(s) > max_tag_len:
            raise ValueError(f"tag exceeds max length {max_tag_len}")
        cleaned.append(s)
    if len(cleaned) > max_tags:
        raise ValueError(f"too many tags (max {max_tags})")
    return cleaned
