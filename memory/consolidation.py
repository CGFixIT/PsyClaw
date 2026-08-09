"""Consolidation stub — never auto-runs in v1."""

from __future__ import annotations

from typing import Any


def run_consolidation(cfg: dict[str, Any]) -> dict[str, Any]:
    """Stub. Returns disabled unless explicitly extended later."""
    mem = cfg.get("memory") or {}
    consol = mem.get("consolidation") or {}
    if consol.get("enabled") is not True:
        return {"status": "disabled", "reason": "consolidation not implemented"}
    # Even if an operator flips the flag, v1 does not consolidate.
    return {"status": "disabled", "reason": "consolidation not implemented"}
