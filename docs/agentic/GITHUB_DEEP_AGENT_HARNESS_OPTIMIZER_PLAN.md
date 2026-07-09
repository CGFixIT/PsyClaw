# CyClaw GitHub Deep Agent + Harness Optimizer Implementation Plan

Status: Draft / planning only / no code implemented by this document.

## 1. Title and Status

This document uses the requested filename because `docs/agentic/` already uses
explicit, topic-scoped governance and roadmap names such as
`CyClaw_Safe_Agentic_Enhancement_Plan.md`,
`SKILLS_REGISTRY_GOVERNANCE.md`, and `FSCONNECT_SQL_ROADMAP.md`. The requested
name is precise and fits that convention.

## 2. Executive Summary

The target architecture adds two out-of-band features under `agentic/`:

- `agentic/harness_optimizer/`: a governed better-harness-style optimizer that
  evaluates candidate improvements to allowed harness surfaces using visible
  train cases and hidden holdout cases.
- `agentic/deepagent_github/`: an optional LangChain Deep Agents-backed local
  GitHub coding harness that can use LM Studio and scoped CyClaw tool wrappers.

Both features remain disabled by default and must not be imported by `gate.py`,
`graph.py`, `mcp_hybrid_server.py`, or the core request path. `agentic.enabled`
remains the master switch. GitHub writes remain disabled unless a separate
reviewed change explicitly enables them, and `agentic/writer.py` must keep its
current disabled/stubbed execution guarantee.

The optimizer adapts better-harness concepts clean-room:

- local target workspace
- explicit editable surface manifest
- separate proposer workspace
- visible train failures
- hidden holdout cases
- deterministic train plus holdout scoring
- keep/discard decision based on combined improvement and governance gates
- local artifacts as source of truth
- rollback through versioned artifacts, not silent mutation

The Deep Agents harness is optional:

- no required cloud provider
- no LangSmith requirement
- no Deep Agents import unless the feature is enabled and the command is
  invoked
- no `LocalShellBackend` by default
- no unrestricted filesystem backend over the real repository
- no GitHub write tool by default

LM Studio and MCP are treated as local proposer and tool-calling boundaries,
not as authority to mutate CyClaw. Every external read, refusal, proposal,
candidate evaluation, acceptance, rejection, and dry-run write plan must audit.
Any persistent skill, soul, prompt, or harness surface change follows:

`propose -> scan -> human reason -> explicit confirm -> atomic apply -> SHA-256 version record`

No autonomous self-modification and no hidden background loop are allowed.

## 3. Repo-Grounded Current-State Inventory

Verified current files and directories:

| Path | Purpose | Guarantees | Reuse | Must not be expanded casually |
|---|---|---|---|---|
| `agentic/config.py` | Validating loader for the `agentic:` config block. | Defaults disabled/read-only, validates repo slug, `gh` floor, registry path under `data/`, and read retry knobs. | Extend with nested disabled config for future `deepagent_github` and `harness_optimizer`. | Do not make optional features enabled by default. Do not allow unsafe paths or shell-shaped strings. |
| `agentic/gh_client.py` | Read-only `gh` subprocess wrapper. | Uses argv lists, resolves `gh`, validates repo slug, supports allow-listed read ops only, audits reads. | Reuse for GitHub context in future runners. | Do not add write ops here. Do not pass shell strings. |
| `agentic/writer.py` | GitHub write scaffold. | `EXECUTION_ENABLED = False`; `execute_write` refuses while disabled; full gate still returns dry-run plan only. | Mirror its gate pattern for harness write planning. | Do not weaken execution disablement. Do not turn it into generic filesystem writer. |
| `agentic/registry.py` | Governed skills registry. | `propose_skill` never writes; `apply_skill` requires write mode, `writes_enabled`, human reason, injection scan, atomic write, SHA-256 history, audit. | Reuse governance pattern for accepted optimizer improvements. | Do not create a bypass write path for skills or prompts. |
| `agentic/context.py` | Higher-level GitHub context bundles. | Calls read-only `gh_client` ops and respects `allowed_read_ops`. | Reuse for issue, PR, and repo context in `github_coding_runner.py`. | Do not mix write behavior into context fetchers. |
| `agentic/cli.py` | Out-of-band CLI entry point. | Honors `agentic.enabled` for context and registry ops; lazy-imports submodules; no request-path imports. | Add future subcommands here as the primary operator path. | Preserve existing commands and exit codes. |
| `agentic/selftest.py` | Operator pre-flight self-test. | Checks config, `gh` availability, read argv, write refusal, and registry scanner without contacting GitHub. | Add future no-network harness optimizer checks. | Do not make self-test require live GitHub, LM Studio, MCP, or Deep Agents. |
| `agentic/fsconnect/` | Optional filesystem connector. | Disabled by default, scoped roots, path safety, write gates, local connector isolation. | Use path-safety ideas for proposer workspace tooling. | Do not expose real-repo writes to Deep Agents by default. |
| `agentic/sqlconnect/` | Optional SQL connector. | Disabled by default, read-only intent, DSN via env var, out-of-band CLI. | Reuse disabled-by-default connector posture. | Do not add live DB dependency to optimizer tests. |
| `utils/personality.py` | Soul file governance. | SHA-256 drift detection, injection scan, explicit human reason, atomic writes. | Reuse governance requirements for soul/prompt proposals. | Do not add autonomous soul mutation. |
| `utils/logger.py` | Audit JSONL and redaction. | Query hashing, PII/secret redaction, shared `audit_log`. | Use for every optimizer and Deep Agents event. | Do not log secrets, raw tokens, or private corpus contents. |
| `utils/errors.py` | Typed error hierarchy. | Agentic errors are separate from core gateway errors. | Reuse `AgenticError` or add narrowly scoped subclasses if needed. | Do not raise bare `Exception` from trust-boundary code. |
| `mcp_hybrid_server.py` | Retrieval-only MCP server. | No LLM sampling, no agentic imports, retrieval-only tool surface. | Treat as evidence that MCP must remain narrow. | Do not import new agentic features into this gateway without separate review. |
| `llm/client.py` | Local LM Studio and external fallback clients. | Local OpenAI-compatible endpoint already exists for the core LLM path. | Inform `deepagent_github.model_adapter` design. | Do not make Deep Agents depend on core LLM routing or Grok fallback. |
| `config.yaml` | Single source of truth. | `agentic.enabled: false`, `mode: read`, `writes_enabled: false`; optional layers disabled by default. | Add nested disabled future config. | Do not enable network, writes, shell, or Deep Agents by default. |
| `tests/test_agentic_*.py` | Agentic unit tests. | Cover isolation, config, context, `gh_client`, registry, selftest, writer. | Extend with config and optimizer scaffold tests. | Do not introduce live network/model/tool dependencies. |

Files requested but not present under the exact names:

- No `agentic/deepagent_github/` package exists yet.
- No `agentic/harness_optimizer/` package existed before this plan effort.
- No `deepagents`, `langchain-mcp-adapters`, `fastmcp`, or `quickjs` dependency
  is currently declared in `pyproject.toml`.

Related modules that do exist:

- `langgraph` and `langchain-core` are already direct dependencies.
- `mcp_hybrid_server.py` is a retrieval-only MCP surface.
- `gate.py` is the FastAPI gateway.
- `llm/client.py` has LM Studio/OpenAI-compatible local chat-completion logic.
- Retrieval/RAG code lives under `retrieval/`.

## 4. Prior Recommendation vs Updated Recommendation

| Previous generic plan | Repo-grounded correction | Final merged decision |
|---|---|---|
| Build a new write system inside `harness_optimizer`. | `writer.py` already defines the write-gate philosophy and must stay narrow. | Create a proposed `harness_write` wrapper later that mirrors `writer.py` gates for harness surfaces. |
| Let Deep Agents use a local shell backend. | CyClaw treats shell execution as a trust boundary; `writer.py` is disabled and fsconnect is scoped. | Do not expose Deep Agents `LocalShellBackend` by default. |
| Expose filesystem tools directly over the real repo. | `fsconnect` shows path-scoped connector design; real-repo writes would bypass governance. | Use a scoped proposer workspace backend only. |
| Treat Deep Agents as a required runtime dependency. | Current agentic layer intentionally has no extra runtime dependency. | Plan Deep Agents as an optional extra and lazy import only. |
| Use only better-harness `module_attr` and `workspace_file` surfaces. | CyClaw has governed skills, soul fragments, prompts, policies, and local tool catalogs. | Map harness surfaces to CyClaw-owned surface types. |
| Let local-model judge decide success. | Local model output is not deterministic proof. | Deterministic train/holdout score is primary; local judge is optional second signal only. |
| Auto-apply accepted optimizer improvements. | Soul/registry governance requires explicit human reason and confirm. | Accepted candidates become proposed versions, never silent mutations. |
| Wire into the MCP gateway for convenience. | `mcp_hybrid_server.py` must remain retrieval-only unless separately reviewed. | Keep both features out-of-band. |

## 5. Target Architecture

Feature 1: `agentic/harness_optimizer/`

Purpose: a governed meta-agent loop that improves allowed harness surfaces using
train/holdout evals. It owns experiment definitions, candidate workspaces,
surface manifests, deterministic scoring, candidate decisions, and audit
events. It does not own persistent writes to soul, skills, GitHub, or the real
repository.

Feature 2: `agentic/deepagent_github/`

Purpose: an optional LangChain Deep Agents-backed local GitHub coding harness
using LM Studio and scoped CyClaw wrappers. It can read GitHub context, plan,
propose diffs, select tests, review diffs, and draft PR text. It cannot apply
diffs to the real repo or write to GitHub by default.

Relationship:

- `harness_optimizer` can optimize `deepagent_github` prompts, skills, and
  policies later.
- `deepagent_github` can be useful before the optimizer exists.
- Both must work independently.
- Neither touches the core request path.
- Neither imports into `gate.py`, `graph.py`, or `mcp_hybrid_server.py`.

## 6. Proposed Directory Layout

This layout is proposed for the complete design. Phase 2 may implement only a
small subset.

```text
agentic/
  harness_optimizer/
    __init__.py
    core.py
    config.py
    proposer.py
    patching.py
    governance.py
    memory.py
    scoring.py
    cli.py
    surfaces/
      definitions.py
      registry_adapter.py
    runners/
      base_runner.py
      github_coding_runner.py
    prompts/
      outer_better_agent.md
      judge_prompt.md
    mcp/
      proposer_workspace_server.py
      tools.py
    examples/
      cyclaw_github_coding_experiment.toml

  deepagent_github/
    __init__.py
    core.py
    config.py
    builder.py
    model_adapter.py
    tools.py
    governance.py
    permissions.py
    subagents.py
    skills.py
    memory.py
    runners.py
    prompts/
      system.md
      planner.md
      reviewer.md
      test_selector.md
      pr_writer.md
    examples/
      local_lmstudio_config.toml
```

## 7. better-harness Adaptation Plan

CyClaw should adapt better-harness concepts without copying code.

Core models:

| Model | Purpose |
|---|---|
| `Experiment` | Declares target workspace, allowed surfaces, visible train cases, hidden holdout cases, runner, scoring policy, and output directory. |
| `Surface` | Declares a CyClaw-owned editable or read-only surface by id, type, path, and governance policy. |
| `Variant` | Represents one candidate proposal with changed surfaces, local artifacts, and proposal text. |
| `RunReport` | Captures deterministic train/holdout results, command output summaries, lint/type/test status, governance findings, and score. |
| `CandidateDecision` | Records accepted/rejected, reason, gates, score delta, and next governance action. |

Surface types to support:

- `registry_skill`
- `soul_fragment`
- `github_coding_prompt`
- `deepagent_system_prompt`
- `deepagent_subagent_prompt`
- `deepagent_skill_file`
- `deepagent_tool_policy`
- `deepagent_permissions_policy`
- `mcp_tool_catalog`
- `harness_optimizer_prompt`
- `evaluation_runner_policy`

Proposer workspace layout:

```text
<output_dir>/<experiment_id>/<variant_id>/
  current/
  history/
  train_visible/
  holdout_hidden/
  proposal.md
  surface_manifest.json
  run_report.json
  decision.json
  audit_summary.json
```

Rules:

- `current/` contains editable surface snapshots only.
- `history/` contains visible prior proposals and decisions.
- `train_visible/` contains visible failures and cases.
- `holdout_hidden/` is runner-owned and not exposed to the proposer.
- `proposal.md` is required for acceptance.
- `surface_manifest.json` is the local source of truth for allowed surfaces.

Acceptance rule:

- candidate train passed
- candidate holdout passed
- candidate combined score improves over baseline
- no critical governance finding
- no unallowed surface changed
- no visible-case hardcoding
- proposal exists and is non-empty
- registry/soul/prompt surfaces pass injection scan
- human reason is required before persistent apply

Scorecard validation:

- deterministic runner result is primary
- optional local-model judge can annotate but never prove success alone
- hidden holdout must be evaluated after candidate generation
- final scorecard includes baseline, candidate, score delta, gates, and artifact hashes

Rollback:

- accepted candidates become proposed versions or proposed registry/soul/harness
  configs
- persistent apply uses atomic write plus SHA-256 version record
- rejected candidates remain in run artifacts
- no mutation happens without a human reason and explicit confirm

Audit events:

- experiment loaded
- baseline started/finished
- proposer workspace created
- candidate created
- candidate evaluated
- candidate accepted/rejected
- apply proposed/refused
- memory recorded

Unit-test strategy:

- config defaults disabled
- unsafe paths rejected
- surface manifest validates ids and paths
- candidate requires score improvement
- candidate rejects no-op
- candidate rejects visible-case hardcoding
- candidate rejects unallowed surface
- workspace path traversal rejected
- no live network, GitHub, LM Studio, MCP, LangChain, or Deep Agents dependency

## 8. LangChain Deep Agents GitHub Coding Feature Plan

Dependency and feature flags:

- Add optional extra only, for example `[agentic-deepagents]`.
- Do not import `deepagents` unless `agentic.deepagent_github.enabled` is true
  and a `deepagent-github` command is invoked.
- Tests must pass without Deep Agents installed.
- Missing extras should produce a clean disabled/stub response.

Model provider:

- default provider: local LM Studio
- base URL: local OpenAI-compatible endpoint
- no cloud provider requirement
- no LangSmith requirement
- no Grok fallback reuse unless separately reviewed

Backends and tools:

- no `LocalShellBackend` by default
- no unrestricted `FilesystemBackend` over the real repo
- use scoped temp/proposer/worktree backend only
- no raw shell tool
- no unrestricted file tool
- no GitHub write tool
- no secret-read tool
- no network tool unless separately approved

Subagents:

| Subagent | Purpose | Allowed tools | Denied tools | Input contract | Output contract | May call subagents | Audit events |
|---|---|---|---|---|---|---|---|
| `repo-context-reader` | Gather GitHub and local repo context. | read-only `gh_client`, local read-only repo context, RAG readonly search. | write, shell, secrets, network beyond approved GitHub reads. | repo, issue/PR ids, scope. | structured context bundle. | no | started, finished, tool allowed/denied |
| `issue-planner` | Turn issue/PR context into an implementation plan. | context bundle, planner prompt, readonly docs. | write, shell, GitHub write. | issue/PR context. | plan with files, tests, risks. | may call `repo-context-reader`. | started, finished |
| `patch-proposer` | Propose diffs in workspace only. | scoped proposer workspace writes under `current/`. | real repo writes, shell, GitHub writes. | plan plus surface manifest. | proposed patch and proposal.md. | may call `test-selector`. | started, finished, proposal created |
| `test-selector` | Select validation commands. | repo metadata, test file list, readonly config. | shell execution by default. | changed files and plan. | deterministic command list. | no | started, finished |
| `diff-reviewer` | Review proposed diff for regressions. | proposed diff, repo context, readonly docs. | writes, shell, GitHub writes. | diff plus plan. | findings with severity. | may call `security-reviewer`. | started, finished |
| `security-reviewer` | Check injection, secrets, path traversal, supply-chain risks. | diff, policy docs, sanitizer patterns. | writes, shell, network. | diff and changed surfaces. | security findings. | no | started, finished |
| `pr-writer` | Draft PR title/body/checklist only. | plan, diff summary, validation results. | GitHub write by default. | accepted proposed diff and checks. | PR title/body text. | no | started, finished |
| `harness-proposer` | Used by optimizer to improve prompts/skills/policies. | proposer workspace and visible train history. | holdout files, real repo writes, shell, GitHub writes. | experiment manifest and failures. | candidate proposal. | may call reviewer agents. | started, finished |

Deep Agents functionality:

- `create_deep_agent` builder seam in `deepagent_github/builder.py`
- tools list built from CyClaw wrappers only
- filesystem permissions restricted to temp/proposer workspace
- `interrupt_on` for sensitive tools if Deep Agents is used
- checkpointer only when human-in-the-loop is enabled
- memory from local `AGENTS.md`-style files under `data/agentic`, not remote memory
- skills generated from governed registry entries only after apply
- streaming/events mapped to audit logs where practical
- structured outputs for planner, reviewer, and scoring agents

## 9. LM Studio and MCP Integration Plan

LM Studio:

- local provider only by default
- OpenAI-compatible base URL option
- default base URL `http://localhost:1234/v1`
- no cloud provider requirement
- base URL and model strings validated for shell metacharacters

MCP/FastAPI:

- `gate.py` FastAPI gateway exists, but this plan does not route through it.
- `mcp_hybrid_server.py` exists and is retrieval-only; do not import Deep Agents
  or optimizer features into it.
- A future out-of-band MCP proposer workspace server may be added under
  `agentic/harness_optimizer/mcp/`.

CyClaw-owned MCP tools:

- strict allowlists
- no raw shell tool
- no unrestricted file tool
- no GitHub write tool
- no secret-read tool
- no network tool unless separately approved

Proposer workspace tools:

- `list_workspace`
- `read_file`
- `write_current_file`
- `read_surface_manifest`
- `read_train_failures`
- `read_visible_history`
- `rag_search_readonly`
- `finish_proposal`

Security requirements:

- resolve paths before access
- reject absolute path escape
- reject symlink escape
- reject path traversal
- writes only under `current/`
- `proposal.md` update through an explicit tool
- audit all tool calls

## 10. GitHub Runner Plan

`agentic/harness_optimizer/runners/github_coding_runner.py` should:

- use `agentic/gh_client.py` and `agentic/context.py` for read-only GitHub context
- use local fixture repos for tests
- support cloned or copied temp worktrees only when explicitly configured
- avoid live network in unit tests
- call existing CyClaw agentic context helpers when possible
- invoke Deep Agents only through optional `deepagent_github` builder
- score deterministically first
- allow optional local-model judge second
- never accept a local-model judge alone as proof of success

Artifacts:

- issue context
- PR context
- proposed patch
- selected test commands
- test command outputs
- lint outputs
- mypy outputs
- governance results
- reviewer findings
- scorecard

## 11. Governance and Acceptance Gates

Harness optimizer candidate acceptance:

- candidate train passed and candidate holdout passed
- candidate score improves over current
- no critical governance finding
- no unallowed surface changed
- no visible-case hardcoding
- `proposal.md` present and non-empty
- registry/soul/prompt surfaces pass injection scan
- human reason required before persistent apply
- accepted candidate creates proposed soul/skill/harness version, not silent mutation

Deep GitHub task completion:

- generated plan exists
- diff is proposed, not auto-applied to real repo
- tests are selected
- validation commands are dry-run or sandbox-run depending on config
- PR body is drafted
- human confirmation required for any external GitHub write
- `writer.py` still controls GitHub write planning/execution policy

## 12. Soul, Registry, and Memory Plan

Accepted improvements should become one of:

- proposed registry skill
- proposed soul fragment
- versioned harness config
- versioned prompt/policy artifact

Rules:

- failures logged as rejected attempts
- no autonomous apply
- SHA-256 for stored artifacts
- reuse `registry.py` persistence pattern for skills
- use `utils/personality.py` governance for soul changes
- optimizer memory under `data/agentic/harness_optimizer/`
- Deep Agent memory under `data/agentic/deepagent_github/`
- local-only memory

## 13. Config Plan

Backwards-compatible proposed shape:

```yaml
agentic:
  enabled: false
  mode: read
  writes_enabled: false

  deepagent_github:
    enabled: false
    provider: lmstudio
    base_url: "http://localhost:1234/v1"
    model: ""
    allow_deepagents_dependency: false
    allow_filesystem_write_tools: false
    allow_shell_execution: false
    allow_github_writes: false
    workspace_root: "data/agentic/workspaces"

  harness_optimizer:
    enabled: false
    max_iterations: 3
    require_human_confirm_for_accept: true
    output_dir: "data/agentic/harness_optimizer/runs"
    memory_dir: "data/agentic/harness_optimizer/memory"
    allow_local_model_judge: false
```

Validation requirements:

- paths must resolve under `data/` unless explicitly documented as temp dirs
- booleans must be real booleans
- `model` and `base_url` strings must not contain shell metacharacters
- default off
- disabled means clean no-op

## 14. CLI Plan

Primary path should be `python -m agentic.cli ...` because it is the existing
operator entry point and already handles config path, exit codes, disabled
no-op behavior, and lazy imports.

Preferred additions:

```bash
python -m agentic.cli deepagent-github plan ...
python -m agentic.cli deepagent-github review ...
python -m agentic.cli deepagent-github draft-pr ...
python -m agentic.cli harness-optimizer validate ...
python -m agentic.cli harness-optimizer run ...
python -m agentic.cli harness-optimizer report ...
```

Secondary direct module entry points may exist for developer convenience:

```bash
python -m agentic.harness_optimizer ...
python -m agentic.deepagent_github ...
```

Existing CLI behavior and exit codes must be preserved.

## 15. Audit Event Plan

New audit event names:

- `agentic_deepagent_invoked`
- `agentic_deepagent_tool_allowed`
- `agentic_deepagent_tool_denied`
- `agentic_deepagent_subagent_started`
- `agentic_deepagent_subagent_finished`
- `agentic_harness_experiment_loaded`
- `agentic_harness_baseline_started`
- `agentic_harness_baseline_finished`
- `agentic_harness_proposer_workspace_created`
- `agentic_harness_candidate_created`
- `agentic_harness_candidate_evaluated`
- `agentic_harness_candidate_accepted`
- `agentic_harness_candidate_rejected`
- `agentic_harness_apply_proposed`
- `agentic_harness_apply_refused`
- `agentic_harness_memory_recorded`

Every event must avoid raw secrets, tokens, full private corpus text, and raw
unbounded model prompts.

## 16. Testing Plan

Unit tests:

- config defaults disabled
- config rejects unsafe paths
- `gh_client` still read-only
- `writer.py` still cannot execute
- registry propose never writes
- registry apply requires reason and mode flags
- harness surface path validation
- proposer workspace path validation
- MCP tool path escape rejection
- no symlink escape
- candidate acceptance requires improvement
- candidate rejection on no-op
- candidate rejection on hardcoded visible case
- Deep Agents optional import is lazy
- Deep Agents disabled mode does not import `deepagents`
- `deepagent_github` tools deny writes by default
- subagent specs expose minimal tools

Integration tests with mocks:

- fake LM Studio response
- fake MCP calls
- fake `gh_client` output
- fake fixture repo
- fake runner report
- audit log captured
- no live network
- no live GitHub
- no real LM Studio required

Security regression tests:

- path traversal
- leading dash repo slug
- shell metacharacter config
- prompt injection in skill/soul/prompt surface
- secret-looking file denied
- symlink escape denied
- Deep Agents `LocalShellBackend` unavailable by default

## 17. Dependency Review

Current state:

- The agentic layer is intentionally lightweight and has no Deep Agents runtime
  dependency.
- `langgraph` and `langchain-core` already exist in `pyproject.toml`.
- `deepagents`, `langchain-mcp-adapters`, `fastmcp`, and `quickjs` are not
  currently declared.

New surfaces if added:

- `deepagents`
- `langchain`
- `langgraph` extension usage
- `langchain-mcp-adapters`
- `fastmcp`
- `quickjs`

Recommendation:

- optional extras only
- lazy imports
- tests pass without extras installed
- fallback disabled/stub behavior when extras are missing
- require `pip-audit` or OSV review before enabling
- update `pyproject.toml`, constraints, requirements, Docker, and CI together
  if a future phase adds dependencies

## 18. Implementation Phases

### Phase 0: Planning document only

Likely files changed:

- `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`
- optional `future_Langchain_plans.md`

Tests required:

- docs review
- `git diff --check`

Rollback:

- delete the added docs

Remains disabled:

- all runtime behavior

### Phase 1: Config and docs only

Likely files changed:

- `config.yaml`
- `agentic/config.py`
- `tests/test_agentic_config.py`
- `docs/agentic/AGENTIC_README.md`

Tests required:

- `GROK_API_KEY=dummy pytest tests/test_agentic_config.py -q`
- YAML parse through existing config loader

Rollback:

- remove nested config keys and dataclass fields

Remains disabled:

- Deep Agents
- optimizer loop
- GitHub writes
- filesystem writes
- shell execution

### Phase 2: Harness optimizer data models and workspace builder, no LM calls

Likely files changed:

- `agentic/harness_optimizer/__init__.py`
- `agentic/harness_optimizer/core.py`
- `agentic/harness_optimizer/proposer.py`
- `tests/test_agentic_harness_optimizer.py`

Tests required:

- candidate decision tests
- workspace path validation tests
- audit event capture
- isolation tests

Rollback:

- remove `agentic/harness_optimizer/` and related tests

Remains disabled:

- optimizer CLI
- proposer model invocation
- Deep Agents
- GitHub writes
- real repo writes
- MCP tools

### Phase 3: Mocked runner and acceptance gate

Likely files changed:

- `agentic/harness_optimizer/runners/base_runner.py`
- `agentic/harness_optimizer/scoring.py`
- tests for mocked runner reports

Tests required:

- fake runner report
- accepted/rejected decisions
- scorecard artifact validation

Rollback:

- remove runner modules and tests

Remains disabled:

- live model calls
- live GitHub
- persistent apply

### Phase 4: Local LM Studio proposer invocation with scoped workspace tools

Likely files changed:

- `agentic/harness_optimizer/proposer.py`
- `agentic/harness_optimizer/mcp/tools.py`
- `agentic/harness_optimizer/mcp/proposer_workspace_server.py`

Tests required:

- fake LM Studio response
- mocked tool calls
- path escape tests
- audit tests

Rollback:

- disable config flag or remove proposer invocation modules

Remains disabled:

- Deep Agents
- GitHub writes
- real repo writes

### Phase 5: Optional `deepagent_github` skeleton with no writes

Likely files changed:

- `agentic/deepagent_github/__init__.py`
- `agentic/deepagent_github/config.py`
- `agentic/deepagent_github/builder.py`
- `agentic/deepagent_github/model_adapter.py`

Tests required:

- lazy import tests
- disabled no-op tests
- missing extras tests

Rollback:

- remove `deepagent_github/` package and config references

Remains disabled:

- Deep Agents dependency import by default
- writes
- shell
- filesystem write tools

### Phase 6: Deep Agents subagents, skills, memory, permissions, interrupts

Likely files changed:

- `agentic/deepagent_github/subagents.py`
- `agentic/deepagent_github/skills.py`
- `agentic/deepagent_github/memory.py`
- `agentic/deepagent_github/permissions.py`
- prompts under `agentic/deepagent_github/prompts/`

Tests required:

- subagent tool allowlist tests
- interrupt configuration tests
- local memory path tests
- no remote memory tests

Rollback:

- disable feature flag and remove subagent wiring

Remains disabled:

- GitHub writes
- shell by default
- real repo writes

### Phase 7: Real GitHub coding eval runner using local fixture repos

Likely files changed:

- `agentic/harness_optimizer/runners/github_coding_runner.py`
- fixture repos under tests
- runner artifact tests

Tests required:

- fake `gh_client` output
- fixture repo validation
- no network tests
- deterministic score tests

Rollback:

- remove runner module or disable runner selection

Remains disabled:

- live GitHub in unit tests
- persistent apply

### Phase 8: Governed propose/apply for accepted harness improvements

Likely files changed:

- `agentic/harness_optimizer/governance.py`
- `agentic/harness_optimizer/patching.py`
- registry and soul adapters

Tests required:

- propose never writes
- apply requires human reason and confirm
- SHA-256 version record
- injection scan refusal
- atomic write behavior

Rollback:

- disable apply command and keep proposals as artifacts only

Remains disabled:

- autonomous apply
- GitHub write execution

### Phase 9: Security review before any real write execution

Likely files changed:

- documents and tests only unless enabling a reviewed executor

Tests required:

- security regression suite
- dependency audit
- CI parity
- manual review checklist

Rollback:

- keep execution disabled

Remains disabled:

- all real write execution until separately approved

## 19. Open Questions

- Exact future soul evolution API surface name for optimizer proposals.
- Whether LM Studio gateway helpers should reuse `llm/client.py` or stay
  separate to avoid coupling with the core graph.
- Whether Deep Agents should live behind optional extra
  `[agentic-deepagents]` or a separate plugin package.
- Whether harness experiments should use TOML files or only `config.yaml`.
- Whether scorecard runs should be manual-only.
- Whether a future GitHub write executor should ever be enabled.
- Whether a future MCP proposer workspace should be a standalone process or
  plain local tool wrappers.
- Whether local model judging should be permitted for non-security score
  annotations.

## 20. Acceptance Criteria For This Planning Document

This planning document is acceptable only if it:

- is grounded in actual repo files
- does not invent existing APIs without labeling them as proposed
- preserves disabled-by-default behavior
- preserves out-of-band isolation
- treats Deep Agents as optional
- clearly separates better-harness optimization from Deep Agents GitHub coding
- includes security and test plans
- includes implementation phases
- includes exact proposed file layout
- includes CLI and config plans
- includes audit event plan
- explicitly says no code was implemented by this document

This document does not implement code. A separate phase 0-2 implementation may
add:

- the document itself
- disabled config keys and validating config objects
- local harness optimizer data models
- local proposer workspace builder
- focused tests

That implementation must not add:

- Deep Agents dependency
- LangChain MCP dependency
- LM Studio calls
- live GitHub calls in tests
- GitHub writes
- shell execution
- real repo filesystem writes
- request-path imports
