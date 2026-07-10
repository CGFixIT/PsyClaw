# Future LangChain Plans

Status: planning pointer plus phase 0-5 scaffold status for optional,
out-of-band agentic work.

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

Implemented scaffold boundary through phase 5:

- allowed and present: disabled config keys, local data models, deterministic
  mock runner/scoring/governance helpers, local proposer workspace builder,
  scoped proposer workspace tools, fake-transport-testable local LM Studio
  proposer adapter, optional `deepagent_github` lazy builder skeleton, focused
  tests, docs
- not allowed: Deep Agents runtime dependency, model calls, live GitHub in unit
  tests, GitHub writes, shell execution, unrestricted filesystem tools, request
  path imports

Remaining future phases:

- Phase 6: real Deep Agents subagent wiring, skills, memory, permissions, and
  human-in-the-loop interrupts behind feature flags.
- Phase 7: GitHub coding eval runner using fixture repos and read-only GitHub
  context.
- Phase 8: governed propose/apply for accepted harness improvements.
- Phase 9: security review before any real write execution.
