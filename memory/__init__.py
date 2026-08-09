"""CyClaw optional memory subsystem (facts + episodes + propose/apply).

Default-off. Lazy-imported from hooks only — never import this package at
module top-level from gate.py, graph.py, mcp_hybrid_server.py, or
retrieval/hybrid_search.py (see tests/test_memory_isolation.py).
"""

from __future__ import annotations

__all__ = ["__version__"]
__version__ = "0.1.0"
