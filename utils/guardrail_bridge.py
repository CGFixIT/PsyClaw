"""Inversion shim binding the guardrails offline input rail into graph.py.

Neither gate.py nor graph.py may import guardrails (module isolation, I6 --
tests/test_guardrails_isolation.py forbids gate.py, graph.py, AND
mcp_hybrid_server.py from naming it). This factory is the one seam: it lives
in utils/ (untouched by that isolation test in either direction), is built
once at gate.py's startup construction step, and the resulting closure is
injected into build_graph() exactly like the existing personality/grok/claude
conditional-construction pattern. See
docs/NeMo/phase2_implementation_plan.md Decision 1.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


def build_input_guard(cfg: dict[str, Any]) -> Callable[[str], dict[str, Any]] | None:
    """Build the Phase 2 guardrail_input callable, or None when disabled.

    Returns None immediately when guardrails.enabled is falsy -- BEFORE
    importing guardrails at all (the import is lazy, inside this branch), so
    a disabled layer costs nothing: no import, no I/O, no state.
    """
    if not (cfg.get("guardrails") or {}).get("enabled", False):
        return None

    from guardrails.config import load_guardrails_config
    from guardrails.integration import check_input
    from guardrails.metrics import GuardrailMetrics

    gcfg = load_guardrails_config()
    metrics = GuardrailMetrics(gcfg.metrics_path)

    def _input_guard(query: str) -> dict[str, Any]:
        return check_input(query, cfg=gcfg, metrics=metrics)

    return _input_guard
