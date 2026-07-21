# Future LangChain Plans

Status: root-level planning pointer for optional, out-of-band LangChain work.
**Updated 2026-07-21** after re-verifying against current `main` — see the
"Status as of 2026-07-21" section below for what changed since this doc was
first written.

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

Phases 0-5 are merged on `main`. Phases 6-9 were implemented in PR #515
(`agent/deepagent-harness-phases-6-9`) — **merged 2026-07-13, not still in
draft** (this doc previously said otherwise; corrected). The PR #515 review
checklist, the recorded owner decisions (including Grok/Claude provider
parity for the harness), the verified `deepagents` API reference, and the
forward roadmap live in `docs/LangChain_Deep_Agentic_Harness_latest_roadmap.md`
(renamed from `docs/LG_Deep_Agentic_Harness_status_n_roadmap.md` — update any
other links pointing at the old filename).

## Status as of 2026-07-21

Re-verified `agentic/deepagent_github/` against the concerns logged in
`LangchainDeepAgent.md` (see that file for the full analysis). Summary:

- The "6 of 12 files unreferenced" and "toothless subagent" (`builder.py`
  reporting `created=True` on an empty tool list) concerns from before PR
  #515 are both **fully resolved** on current `main`. Subagent construction
  now lives in `agentic/deepagent_github/subagents.py`, builds real
  `SubAgent`-shaped dicts with a `list[Callable]` `tools` field, and raises
  `AgenticError` rather than reporting success if a subagent ends up with no
  wired tools.
- One real, still-open item: `agentic/deepagent_github/governance.py` has a
  single function (`validate_write_policy`) with zero callers anywhere in the
  package — everything it would gate is already enforced directly via
  `permissions.py`'s `refuse_unsupported_write_policy`, called straight from
  `builder.py`. Either wire `governance.py` in as the actual policy-gate
  layer the harness taxonomy implies it should be, or delete it — it
  currently duplicates a gate that already exists elsewhere without adding
  one, which is the caveman/YAGNI-textbook "ship it or kill it" situation
  `LangchainDeepAgent.md`'s point 3 flagged for a *different* file
  (`agentic/harness_optimizer/governance.py`) that has since been wired to
  real consumers — this is the same finding recurring in the sibling
  package, not a duplicate report.
- `agentic/harness_optimizer/core.py`'s `SurfaceType` enum still has 9 of 11
  members with zero non-test consumers. Lower priority than the
  `governance.py` item above (an unused enum member is inert; an unwired
  policy-gate module reads as enforcement that isn't actually enforcing
  anything) but same category of speculative-API debt.
