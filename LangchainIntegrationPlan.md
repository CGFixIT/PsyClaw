# CyClaw GitHub Deep Agent + Harness Optimizer Implementation Plan

Target path: `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`

Status: Draft / planning only / no code implemented by this document.

## 1. Executive Summary

Add two independent, out-of-band `/agentic` features:

- `agentic/harness_optimizer/`: a governed better-harness-style loop for improving allowed CyClaw agentic surfaces through visible train cases, hidden holdout cases, deterministic scoring, audit logs, and human-confirmed apply.
- `agentic/deepagent_github/`: an optional LangChain Deep Agents-backed local GitHub coding harness using LM Studio/OpenAI-compatible local endpoints and scoped CyClaw tool wrappers.

Both remain disabled by default, read-only or dry-run by default, and isolated from `gate.py`, `graph.py`, and `mcp_hybrid_server.py`. The design reuses the current `gh_client.py`, `registry.py`, `writer.py` gate pattern, `utils.logger.audit_log`, `utils.personality` soul governance, SHA-256 records, and human-confirmed write philosophy.

## 2. Repo-Grounded Inventory

Verified current files:

| File / area | Current purpose | Reuse | Must not expand casually |
|---|---|---|---|
| `agentic/config.py` | Validates `agentic:` block; default disabled; `mode=read`; `writes_enabled=false`; registry path under `data/`. | Extend with nested `deepagent_github` and `harness_optimizer` config models. | Do not make enabled-by-default or accept unsafe paths. |
| `agentic/gh_client.py` | Read-only `gh` argv builder and runner; allow-listed ops only; audits reads. | GitHub runner should call this for PR/issue/repo context. | Do not add write ops here. |
| `agentic/writer.py` | Disabled GitHub write scaffold; dry-run plan only; `EXECUTION_ENABLED=False`; executor unimplemented. | Mirror its gate shape for harness writes. | Do not turn into a generic filesystem writer or enable execution. |
| `agentic/registry.py` | Governed local skills registry; propose never writes; apply requires reason, write-mode flags, scan, atomic write, SHA-256 history. | Store accepted skill/prompt proposals through a similar propose/apply flow. | Do not allow autonomous apply or remote skill install. |
| `agentic/context.py` | Bundles read-only GitHub context over `gh_client.run_read`. | Runner context source. | Do not bypass `allowed_read_ops`. |
| `agentic/cli.py` | Primary `python -m agentic.cli` interface for status/context/skills/selftest. | Add primary subcommands here. | Preserve existing commands and exit codes. |
| `agentic/selftest.py` | Offline preflight; no live GitHub required. | Add disabled/lazy-import checks. | Do not require LangChain/LM Studio/GitHub. |
| `agentic/fsconnect/` | Scoped filesystem connector; default disabled; path-safe read/write subsystem with separate gates. | Borrow path validation and workspace confinement ideas. | Do not expose real repo write tools to Deep Agents by default. |
| `agentic/sqlconnect/` | Read-only SQL scaffold; lazy driver imports; default disabled. | Pattern for optional/lazy deps and read-only tools. | Do not add write SQL paths. |
| `utils/personality.py` | Soul file-as-truth, SHA-256 drift detection, propose/apply, injection scan, atomic apply, human reason. | Template for soul fragments and harness version records. | No autonomous soul mutation. |
| `utils/logger.py` | Append-only JSONL audit with redaction and query hashing. | All reads/proposals/evals/refusals/tool calls audit here. | Do not log secrets or raw sensitive payloads. |
| `mcp_hybrid_server.py` | Retrieval-only MCP server; no sampling; one hybrid search tool. | Optional read-only RAG source only through wrappers. | Do not add agentic GitHub tools to this server. |
| `llm/client.py` | Local LM Studio OpenAI-compatible `/chat/completions`; Grok/Claude fallbacks. | Model adapter can reuse config style and timeout discipline. | Do not add cloud requirement or force tool-calling into core path. |

Repo facts also verified: `docs/agentic/` already uses descriptive uppercase/underscore planning doc names such as `CyClaw_Safe_Agentic_Enhancement_Plan.md` and `SKILLS_REGISTRY_GOVERNANCE.md`, so `GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md` fits the existing convention.

## 3. Prior Recommendation vs Updated Decision

| Previous generic plan | Repo-grounded correction | Final decision |
|---|---|---|
| Build a new write system in the optimizer. | `writer.py` is intentionally disabled/stubbed. | Add a proposed `harness_write` wrapper that mirrors gates and stays dry-run/default-off. |
| Expose Deep Agents shell/filesystem backend. | FsConnect proves writes need scoped roots, gates, audit, and no real repo write by default. | No `LocalShellBackend`; no unrestricted real-repo filesystem backend. |
| Use Deep Agents as required runtime. | Current agentic layer has zero new runtime deps. | Optional extra only, lazy import only. |
| Let optimizer apply winning prompts. | Registry/soul changes require propose -> scan -> human reason -> confirm -> atomic apply -> SHA-256. | Optimizer can create accepted proposals, not silent mutation. |
| Put GitHub tooling in MCP gateway. | MCP server is retrieval-only, no sampling. | Keep GitHub harness out-of-band under `agentic/`. |

## 4. Target Architecture

Feature 1: `agentic/harness_optimizer/`

Purpose: Governed meta-agent loop that improves allowed harness surfaces using train/holdout evaluation.

Core behavior:

- Builds a proposer workspace containing `current/`, `candidate/`, `proposal.md`, visible train failures, and surface manifests.
- Evaluates baseline and candidate against `train_visible` and `holdout_hidden`.
- Accepts only if combined train + holdout score improves and governance passes.
- Records every external read, refusal, candidate, evaluation, acceptance, rejection, and dry-run apply plan.

Feature 2: `agentic/deepagent_github/`

Purpose: Optional LangChain Deep Agents-backed local GitHub coding harness using LM Studio and scoped CyClaw tools.

Core behavior:

- Useful before the optimizer exists.
- Can later expose prompts/skills/policies as optimizer surfaces.
- Works independently from `harness_optimizer`.
- Never touches the core request path.

## 5. Proposed Directory Layout

Proposed only, not implemented:

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

## 6. better-harness Adaptation

Use concepts, not copied code.

Models to plan:

- `Experiment`: name, surfaces, runner, train/holdout fixtures, scoring policy, output dirs.
- `Surface`: governed target, type, path/key, read/apply policy, SHA-256.
- `Variant`: candidate content, proposer metadata, changed surfaces, proposal summary.
- `RunReport`: case results, command outputs, artifacts, deterministic score, optional model judge score.
- `CandidateDecision`: accepted/rejected, reason, baseline score, candidate score, governance findings.

Surface types:

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

Acceptance rule:

- `candidate_train.passed` and `candidate_holdout.passed`.
- Combined score improves over baseline.
- No critical governance finding.
- No unallowed surface changed.
- No visible-case hardcoding.
- `proposal.md` present and non-empty.
- Prompt/soul/skill surfaces pass injection scan.
- Persistent apply requires human reason and explicit confirm.
- Accepted candidate creates a proposed version record, never silent mutation.

Artifacts:

```text
data/agentic/harness_optimizer/runs/<run_id>/
  experiment.toml
  baseline/
  candidates/<candidate_id>/
  decisions.jsonl
  audit_refs.jsonl
  scorecard.md
```

## 7. Deep Agents GitHub Harness Plan

Dependency strategy:

- Add optional extra such as `[agentic-deepagents]`.
- Candidate deps for review: `deepagents`, `langchain`, `langgraph`, `langchain-mcp-adapters`, `fastmcp`, and possibly `quickjs`.
- Lazy import only inside enabled command paths.
- Tests must pass without extras installed.
- No LangSmith requirement.
- No cloud provider requirement.

Subagents:

| Subagent | Purpose | Allowed tools | Denied tools | Output |
|---|---|---|---|---|
| `repo-context-reader` | Read repo/PR/issue context. | `gh_client` reads, local read-only fixture reads, RAG read-only. | Writes, shell, secrets. | Context bundle. |
| `issue-planner` | Turn issue/PR into implementation plan. | Context bundle, read-only files. | Writes, shell, GitHub writes. | Structured plan. |
| `patch-proposer` | Propose diffs in temp workspace only. | Proposer workspace tools. | Real repo writes. | Unified diff + rationale. |
| `test-selector` | Pick validation commands. | Read-only repo metadata. | Shell execution unless sandbox enabled. | Test plan. |
| `diff-reviewer` | Review diff for regressions. | Diff/context reads. | Writes. | Findings. |
| `security-reviewer` | Check injection/secrets/path/supply-chain risks. | Diff/context reads, policy reads. | Writes, network. | Security findings. |
| `pr-writer` | Draft PR title/body/checklist. | Proposed diff and results. | GitHub writes. | Draft text only. |
| `harness-proposer` | Improve harness prompts/skills/policies for optimizer. | Proposer workspace. | Persistent apply. | Candidate proposal. |

Deep Agents functionality:

- `create_deep_agent` builder seam in `builder.py`.
- Tool list built from CyClaw wrappers, not raw host tools.
- Filesystem permissions limited to temp/proposer workspace.
- `interrupt_on` for sensitive tools when Deep Agents is enabled.
- Checkpointer only when human-in-the-loop is enabled.
- Memory from local files under `data/agentic/deepagent_github/`, not remote memory.
- Skills generated only from governed registry entries after apply.
- Streaming/events mapped to audit logs where practical.
- Structured outputs for planner/reviewer/scoring.

## 8. LM Studio and MCP Integration

LM Studio:

- Local provider default.
- OpenAI-compatible endpoint option, default `http://localhost:1234/v1`.
- Model string from nested config.
- No cloud provider requirement.

MCP/tool boundary:

- CyClaw-owned MCP tools only, strict allowlists.
- No raw shell tool.
- No unrestricted file tool.
- No GitHub write tool.
- No secret-read tool.
- No network tool unless separately approved.

Proposer workspace MCP tools:

- `list_workspace`
- `read_file`
- `write_current_file`
- `read_surface_manifest`
- `read_train_failures`
- `read_visible_history`
- `rag_search_readonly`
- `finish_proposal`

Security requirements:

- Resolve paths.
- Reject absolute path escape.
- Reject symlink escape.
- Reject path traversal.
- Writes only under `current/`.
- `proposal.md` update through explicit tool.
- All tool calls audited.

## 9. GitHub Runner Plan

`agentic/harness_optimizer/runners/github_coding_runner.py` should:

- Use `agentic/gh_client.py` and `agentic/context.py` for read-only GitHub context.
- Use local fixture repos in tests.
- Support cloned/copied temp worktrees only when explicitly configured.
- Avoid live network in unit tests.
- Call existing context helpers where possible.
- Invoke Deep Agents only through optional `deepagent_github.builder`.
- Collect issue context, PR context, proposed patch, test/lint/mypy outputs, governance results, reviewer findings.
- Score deterministic checks first.
- Allow optional local-model judge second.
- Never accept local-model judge alone as proof.

## 10. Governance, Soul, Registry, and Memory

Harness candidate acceptance:

- Accepted improvements become proposed registry skills, proposed soul fragments, or versioned harness configs.
- Failures become rejected attempt records.
- No autonomous apply.
- SHA-256 every stored artifact.
- Reuse `registry.py` pattern where possible.
- Optimizer memory under `data/agentic/harness_optimizer/`.
- Deep Agent memory under `data/agentic/deepagent_github/`.
- Memory is local-only.

Deep GitHub task completion:

- Generated plan exists.
- Diff is proposed, not auto-applied to real repo.
- Tests selected.
- Validation commands dry-run or sandbox-run based on config.
- PR body drafted.
- Human confirmation required for any external GitHub write.
- `writer.py` remains the GitHub write policy authority.

## 11. Config Plan

Proposed additive shape:

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

Validation:

- Defaults off.
- Disabled means clean no-op.
- Paths resolve under `data/` unless explicitly temp dirs.
- Booleans must be real bools.
- `model` and `base_url` strings reject shell metacharacters and unsafe schemes.
- No optional dependency import unless enabled and command invoked.

## 12. CLI Plan

Primary path: extend `python -m agentic.cli`, because this is the existing operator entrypoint and preserves one out-of-band control surface.

Proposed commands:

```bash
python -m agentic.cli deepagent-github plan ...
python -m agentic.cli deepagent-github review ...
python -m agentic.cli deepagent-github draft-pr ...
python -m agentic.cli harness-optimizer validate ...
python -m agentic.cli harness-optimizer run ...
python -m agentic.cli harness-optimizer report ...
```

Secondary module CLIs may exist for direct developer use:

```bash
python -m agentic.harness_optimizer ...
python -m agentic.deepagent_github ...
```

Existing CLI behavior and exit codes must remain intact.

## 13. Audit Event Plan

Required new events:

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

Also audit all external reads, refusals, proposal creation, dry-run write plans, test command selections, local-model judge invocations, and optional dependency load failures.

## 14. Testing Plan

Unit tests:

- Config defaults disabled.
- Config rejects unsafe paths and shell metacharacters.
- `gh_client` remains read-only.
- `writer.py` still cannot execute.
- Registry propose never writes.
- Registry apply requires reason and write flags.
- Harness surface path validation.
- Proposer workspace path validation.
- MCP tool path escape rejection.
- No symlink escape.
- Candidate acceptance requires improvement.
- Reject no-op candidate.
- Reject visible-case hardcoding.
- Deep Agents optional import is lazy.
- Disabled mode does not import `deepagents`.
- Deep GitHub tools deny writes by default.
- Subagent specs expose minimal tools.

Integration tests with mocks:

- Fake LM Studio response.
- Fake MCP calls.
- Fake `gh_client` output.
- Fake fixture repo.
- Fake runner report.
- Audit log captured.
- No live network, GitHub, LM Studio, MCP, LangChain, or Deep Agents.

Security regression tests:

- Path traversal.
- Leading-dash repo slug.
- Shell metacharacter config.
- Prompt injection in skill/soul/prompt surface.
- Secret-looking file denied.
- Symlink escape denied.
- Deep Agents `LocalShellBackend` unavailable by default.

## 15. Dependency Review

Current verified posture: the existing agentic layer adds no Python runtime dependency; `pyproject.toml` already includes `langgraph==1.2.6` and `langchain-core==1.4.8`, but not Deep Agents, `langchain`, `langchain-mcp-adapters`, `fastmcp`, or `quickjs`.

Recommendation:

- Optional extras only.
- Lazy imports only.
- Tests pass without extras.
- Stub/planning-only behavior when extras missing.
- Run `pip-audit` and OSV review before enabling.
- Update `pyproject.toml`, constraints, Docker/CI docs only in a later reviewed implementation PR.

## 16. Implementation Phases

| Phase | Work | Likely files | Tests | Rollback | Still disabled |
|---|---|---|---|---|---|
| 0 | Planning document only. | `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md` | Docs review only. | Delete doc. | Everything. |
| 1 | Config/docs only. | `config.yaml`, docs. | Config parse tests. | Revert config/docs. | Both features. |
| 2 | Harness models/workspace builder, no LM. | `agentic/harness_optimizer/*` | Unit tests. | Remove package. | Runner/apply. |
| 3 | Mock runner and acceptance gate. | runner/scoring/governance. | Mock integration. | Disable command. | LM/deepagent/write. |
| 4 | LM Studio proposer with scoped workspace tools. | proposer/model/mcp tools. | Fake LM Studio. | Disable proposer. | Apply/write. |
| 5 | Optional `deepagent_github` skeleton, no writes. | `agentic/deepagent_github/*` | Lazy import tests. | Remove extra/package. | Writes/shell. |
| 6 | Subagents, skills, memory, HITL interrupts. | subagents/permissions/memory. | Mock Deep Agents tests. | Feature flag off. | Real apply/GitHub writes. |
| 7 | GitHub coding eval runner with fixtures. | `github_coding_runner.py` | Fixture repo tests. | Disable runner. | Live GitHub. |
| 8 | Governed propose/apply for accepted improvements. | registry adapter/governance. | Apply refusal/apply proposal tests. | Disable apply command. | Auto-apply. |
| 9 | Security review before any real write execution. | Docs/tests only unless approved. | Full security suite. | Keep execution disabled. | Real write execution. |

## 17. Open Questions

- Exact soul evolution extension API name: reuse `PersonalityManager` directly or introduce a separate proposed-fragment store?
- Whether a local LM Studio gateway beyond `llm/client.py` already exists for tool-calling; current verified client is plain chat completions.
- Whether Deep Agents should live behind an optional extra or a separate plugin package.
- Whether optimizer experiments should use TOML files, `config.yaml`, or both.
- Whether scorecard runs should be manual-only.
- Whether a future GitHub write executor should ever be enabled.
- Whether Windows-specific workspace path safety should reuse FsConnect fallback logic or have a stricter temp-only policy.

## 18. Acceptance Criteria For This Planning Document

The document is acceptable only if it:

- Is grounded in actual repo files.
- Labels proposed APIs/files as proposed.
- Preserves disabled-by-default behavior.
- Preserves out-of-band isolation.
- Treats Deep Agents as optional.
- Separates better-harness optimization from Deep Agents GitHub coding.
- Includes security and test plans.
- Includes implementation phases.
- Includes exact proposed layout.
- Includes CLI, config, dependency, and audit plans.
- Explicitly says no code was implemented.

## 19. Final Response Facts

Planning document path: `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`

Major architectural decisions:

- Keep both new capabilities out-of-band under `agentic/`.
- Preserve `agentic.enabled` as the master switch.
- Keep GitHub writes disabled and governed by the existing `writer.py` policy.
- Add a separate harness workspace write wrapper instead of expanding `writer.py`.
- Treat Deep Agents as optional, lazy-imported, and feature-flagged.
- Use LM Studio/OpenAI-compatible local endpoint as the default model boundary.
- Require deterministic scoring before any optional local-model judge.

Repo facts verified:

- Fresh clone is on `main...origin/main`.
- `docs/agentic/` exists and uses planning/governance doc naming compatible with the requested path.
- Existing `agentic/` contains `config.py`, `gh_client.py`, `writer.py`, `registry.py`, `context.py`, `cli.py`, `selftest.py`, `fsconnect/`, and `sqlconnect/`.
- `data/personality/soul.md` exists.
- `utils/personality.py` implements soul SHA-256/versioning/drift detection and human-gated apply.
- `mcp_hybrid_server.py` is retrieval-only and has no sampling capability.
- `pyproject.toml` includes `langgraph` and `langchain-core`, but no Deep Agents dependency.

Verification:

- No tests run. (need ci verification and sandbox runtime testing for python 3.12 before any phase commit
- follow typical pr process

