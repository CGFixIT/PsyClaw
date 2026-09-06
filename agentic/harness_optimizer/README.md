# `agentic/harness_optimizer/` — governed fixture optimizer

> **Retired** (owner decision 2026-07-31): no further development is planned —
> `agentic/real_repo_loop.py` superseded this surface as the live coding path.
> Code, tests, and CI remain in the repository unmodified; this is a
> documentation-only status, not a deletion. See
> `agentic/deepagent_github/README.md`'s retirement note and
> `docs/work/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`.

Opt-in scaffold under the agentic layer. Ships **disabled**
(`agentic.harness_optimizer.enabled: false`). It scores fixture-based harness
runs, proposes candidate artifacts in a jailed workspace, and records
human-gated accept/reject decisions. It never **writes** to GitHub
(`runners/github_coding_runner.fetch_github_task_context` does make read-only
GitHub context calls through `agentic.context`), does not spawn a host
shell, or import `gate.py` / `graph.py` / `mcp_hybrid_server.py` (I6).

Accept still requires a human when
`require_human_confirm_for_accept: true` (the shipped default).

## Package map

| Module | Role |
|---|---|
| `core.py` | Surface / experiment / variant / run-report / decision types and `decide_candidate` (`Scorecard` lives in `scoring.py`) |
| `governance.py` | Injection, visible-case-hardcoding, and code-shape (`scan_code_shape`) inspection |
| `proposer.py` | Build a proposer workspace (`current/`, holdout hidden) |
| `patching.py` | Propose / apply a candidate artifact |
| `scoring.py` | Case results → scorecard / run report |
| `model_adapter.py` | Local proposer client |
| `runners/` | `HarnessRunner` + mock runner; `GitHubCodingRunner` lives here |
| `loop_driver.py` | Multi-iteration loop |
| `mcp/` | `ProposerWorkspaceTools` — **not** an MCP server (see `mcp/README.md`) |

`GitHubCodingRunner` and `loop_driver` are **not** re-exported from
`__init__.py` (historically a circular import via `agentic.deepagent_github`;
92afb95 removed that import edge, but they stay off `__all__`). Import them
from their own modules:

```text
agentic.harness_optimizer.runners.github_coding_runner
agentic.harness_optimizer.loop_driver
```

## Config (shipped)

```yaml
agentic:
  harness_optimizer:
    enabled: false
    max_iterations: 3
    require_human_confirm_for_accept: true
    output_dir: "data/agentic/harness_optimizer/runs"
    memory_dir: "data/agentic/harness_optimizer/memory"
    allow_local_model_judge: false
```

## Related

- Parent: [`../README.md`](../README.md)
- Workspace tools: [`mcp/README.md`](mcp/README.md)
- Historical plan (not an action list): [`docs/work/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`](../../docs/work/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md)
