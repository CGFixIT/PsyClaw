# Future LangChain Plans

Status: planning pointer for optional, out-of-band agentic work.

CyClaw already uses LangGraph and `langchain-core` in the governed RAG request
path. Future LangChain-related work should stay split by trust boundary:

- Core gateway work remains in `gate.py` and `graph.py` and must preserve
  RAG-first retrieval, topology-enforced policy, triple-gated external fallback,
  audit convergence, and human-gated soul changes.
- Agentic GitHub coding work belongs under `agentic/` and remains optional,
  disabled by default, and out-of-band.
- LangChain Deep Agents, MCP adapters, and related tools must be optional extras
  unless a separate dependency review approves them.

The detailed governed GitHub coding and harness optimization plan lives at:

- `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`

Implementation boundary for the current phase:

- allowed: disabled config keys, local data models, local proposer workspace
  builder, focused tests, docs
- not allowed: Deep Agents runtime dependency, model calls, live GitHub in unit
  tests, GitHub writes, shell execution, unrestricted filesystem tools, request
  path imports
