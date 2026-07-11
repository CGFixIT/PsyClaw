# LangChain Fix Notes (Superseded)

## Reconciliation with shipped code (2026-07-10)

- This document's "Security Invariant 13" (module isolation) refers to CyClaw's
  invariant I6 (module isolation) — the repo defines exactly six invariants
  total (see `CLAUDE.md` section 3 "The Six Invariants" and `INVARIANTS.md`
  Rule 9, "the core three never import the out-of-band packages"). Any audit
  trail or commit message referencing this invariant should cite I6, not
  "Invariant 13."
- The env-var-driven `GithubAgentConfig` sketched in this document
  (`CYCLAW_GITHUB_AGENT_ENABLED`, `CYCLAW_GITHUB_WRITE_ENABLED`, and similar)
  contradicts the repo rule that `config.yaml` is the single source of truth
  for every *feature* tunable — this is narrower than "env vars are reserved
  for secrets," since the repo does use env vars for legitimate non-secret
  operator knobs elsewhere (`CYCLAW_EMBED_CACHE_SIZE` overrides the
  embedding-cache size fixed at import time, `gate.py`'s telemetry-kill vars,
  `CYCLAW_DB_URL`/`CYCLAW_RATELIMIT_DB_URL`). The rule this document violates
  is specifically: agentic feature enable/disable/write-permission flags
  belong in `config.yaml`, not env vars. The shipped `agentic/deepagent_github/config.py` (merged
  2026-07-09) already loads a validated config from `config.yaml` via
  `load_agentic_config()` in `agentic/config.py`, returning an
  `AgenticConfig`/`DeepAgentGitHubConfig` pair. A real implementation of the
  design below must extend that `config.yaml`-driven path rather than
  introduce new environment-variable configuration.
- The `agentic/deepagent_github/audit.py` sketch in this document appends raw
  JSON lines directly to `audit.jsonl` via `open(..., "a")`. This bypasses
  `utils/logger.py`'s `audit_log()`, which owns SHA-256 query hashing,
  PII/secret redaction, and the config-driven audit file path
  (`cfg["logging"]["audit_file"]`). Every agentic subsystem in the shipped
  code audits through `audit_log()`; a raw file append here would create an
  unredacted side channel outside that contract.
- A phase 0-5 scaffold already exists at `agentic/deepagent_github/`
  (containing `builder.py`, `config.py`, `permissions.py`, `subagents.py`,
  `tools.py`, `model_adapter.py`, plus placeholder modules `core.py`,
  `governance.py`, `memory.py`, `runners.py`, `skills.py`) with a different
  design than the repo layout this document proposed. See
  `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`, section
  "Unwired scaffold inventory (post-phase-5 audit, 2026-07-10)", for the
  current state of that scaffold.

## Additional corrections (2026-07-11)

- This document's claim that all agentic subsystems are "only reached via
  subprocess through `utils/ops_runner.py`" is wrong for NeMo/guardrails:
  the guardrails layer loads **in-process** via the `utils/guardrail_bridge.py`
  soft-import shim (still never imported directly by `gate.py`/`graph.py`);
  only the four `/ops/*` endpoints go through `ops_runner.py`.
- This document's builder sketch hardcoded `model="anthropic:claude-sonnet-4-6"`
  — a cloud-provider default that contradicted the shipped local-LM-Studio
  default and put a tunable outside `config.yaml`. The shipped design keeps
  the model string in `agentic.deepagent_github.model` and defaults to a
  local provider.

---

This root-level file held pasted research proposing two GitHub coding
subagent implementations (a `deepagents`-based variant and a LangGraph-native
variant). It was reduced to this pointer on 2026-07-11. Its load-bearing
content is preserved:

- The LangGraph-native alternative, its tradeoff verdict, the
  `Annotated[list, operator.add]` reducer note, and the ordered
  Allow/Deny default-deny permissions pattern now live in
  `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`, section
  "Alternative considered: LangGraph-native GitHub coding harness".
- Current status, owner decisions, and the phases 6-9 roadmap live in
  `docs/LG_Deep_Agentic_Harness_status_n_roadmap.md`.

Research provenance: Perplexity thread
`https://www.perplexity.ai/search/f3532bc5-763f-4916-8bf8-8e6ac564b595`.
