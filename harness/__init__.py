r"""CyClaw PowerShell coding harness (out-of-band).

A grok-build / kimi-code style local coding harness launched from Windows
PowerShell (``cyclaw``). It layers a slash-command-driven browser console and a
small FastAPI control plane on top of the existing out-of-band subsystems:

  - ``agentic/``            GitHub context + governed skills registry
  - ``agentic/harness_optimizer``  governed harness optimization runs
  - ``llm/client.py``-style local Ollama chat (with per-session token tallies)

Isolation contract (invariant I6): this package is NEVER imported by
``gate.py``, ``graph.py``, or ``mcp_hybrid_server.py``, and it never imports
them back. GitHub side-effects stay behind ``agentic.cli`` via the
``utils.ops_runner`` subprocess shim; nothing here shells out with user input
or writes outside the harness home (``%USERPROFILE%\.CyClaw`` by default).
"""
