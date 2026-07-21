# Future LangChain Plans

Status: root-level planning pointer for optional, out-of-band LangChain work.

CyClaw already uses LangGraph and `langchain-core` in the governed RAG request
path. Future LangChain-related work should stay split by trust boundary:

- Core gateway work remains in `gate.py` and `graph.py` and must preserve all
  six security invariants: RAG-first retrieval, topology-enforced policy,
  triple-gated external fallback, audit convergence, human-gated soul
  changes, and module isolation (the core three never import `agentic/`,
  `sync/`, or `guardrails/`).
- Agentic GitHub coding work belongs under `agentic/` and remains optional,
  disabled by default, and out-of-band.
- LangChain Deep Agents, MCP adapters, and related tools must be optional
  extras unless a separate dependency review approves them.

The detailed governed GitHub coding and harness optimization plan lives at:

- `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md` (canonical
  design plan, with per-phase implementation ledgers)

Phases 0-5 are merged on `main`. Phases 6-9 are implemented in draft PR #515
(`agent/deepagent-harness-phases-6-9`). The PR #515 review checklist, the
recorded owner decisions (including Grok/Claude provider parity for the
harness), the verified `deepagents` API reference, and the forward roadmap
live in `docs/LG_Deep_Agentic_Harness_status_n_roadmap.md`.
