# Deep Agent Harness Phases 6-9

Status: implemented as an experimental, out-of-band surface. All feature flags
remain disabled in `config.yaml` by default.

## Scope

This record covers the implementation after the phase-5 scaffold described in
`GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`. It does not enable real GitHub
writes, host shell execution, real-repository writes, remote memory, or an
external approval decorator.

## Phase 6: Deep Agents Wiring

- `build_deepagent_github()` now materializes valid subagent dictionaries with
  `name`, `description`, `system_prompt`, `model`, and real callable tools.
- The only callable tools bind `ProposerWorkspaceTools`: manifest/read/RAG
  reads, plus optional writes limited to `current/` and `proposal.md`.
- The Deep Agents backend is `StateBackend`, not `FilesystemBackend` or
  `LocalShellBackend`. Its built-in filesystem reads and writes are denied so
  the agent only has CyClaw-owned scoped wrappers. `StateBackend` is not a
  sandbox backend and has no `execute` method, so the built-in execute tool
  cannot run host commands.
- Applied registry skills are converted to virtual `/skills/*/SKILL.md` files.
  Local memory is read only from `data/agentic/deepagent_github/AGENTS.md`, is
  capped at 64 KB, and is never fetched remotely or created by the agent.
- When scoped workspace writes are enabled, `interrupt_on` requires approve or
  reject for both write tools and an in-memory LangGraph checkpointer is passed.
  `resume_deepagent_interrupt()` treats a timeout as a reject. No approval queue
  or event-loop lookup is introduced.

## Phase 7: Fixture Runner

`agentic.harness_optimizer.runners.github_coding_runner` copies a committed test
fixture repository into a temporary directory, overlays only declared candidate
surfaces, and evaluates deterministic visible/holdout file expectations. It
uses the existing read-only `agentic.context` wrappers for task context but
unit tests inject fake responses and perform no network calls.

The runner detects undeclared candidate files, undeclared changed surfaces,
proposal injection patterns, and visible-case hardcoding. It emits the existing
`RunReport` shape so `decide_candidate()` remains the deterministic acceptance
gate.

The `deepagents-harness` CI job installs the opt-in dependency set and runs the
real builder test plus the approve, reject, and timeout regression cases. The
default CI matrix does not install Deep Agents.

## Phase 8: Governed Candidate Records

`patching.py` provides a two-step local path:

1. `propose_candidate_application()` accepts only an already-accepted runner
   result and re-checks candidate text for injection patterns. It does not write
   a candidate artifact.
2. `apply_candidate_artifact()` requires all of: `agentic.enabled`, optimizer
   enabled, `mode: write`, `writes_enabled: true`, a non-empty human reason,
   `require_human_confirm_for_accept: true`, and explicit confirmation. It writes
   an atomic, SHA-256-versioned JSON record under the configured `data/` paths.
   It independently rechecks the proposal hash and injection gate, so callers
   cannot bypass `propose_candidate_application()` by constructing a proposal.

This is intentionally not a source-tree, registry, soul, or GitHub apply path.
The existing registry and soul commands remain the only governors for those
surfaces, and `agentic/writer.py` remains the disabled GitHub-write boundary.

## Phase 9: Security Gate

Before any future executor is considered, require:

- `pytest tests/test_agentic_harness_phase679.py tests/test_agentic_deepagent_optional.py -q`
- green `deepagents-harness` CI, including approve/reject/timeout coverage
- OSV and pip-audit review of the optional dependency set
- `GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q`
- `git diff --check` and `ruff check --select E,F,I,B,C4,UP,S .`
- a separate human security review for any request to add shell, host filesystem,
  GitHub mutation, or source-tree application

The separate approval-decorator concept remains absent. There is no sensitive
tool outside Deep Agents in this implementation that needs it, and adding one
would be a new security-sensitive feature rather than a prerequisite for the
current harness.

## Optional Installation

```bash
pip install ".[agentic-deepagents]" -c constraints.txt
```

This installs the optional `deepagents`, `langchain`, and `langchain-openai`
stack. It does not enable the feature. Enabling it still requires the nested
`agentic.deepagent_github` flags, a configured local model, and a scoped
proposer workspace supplied by the fixture/evaluation flow.
