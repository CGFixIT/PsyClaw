# `agentic/harness_optimizer/` — governed fixture optimizer

Opt-in scaffold under the agentic layer. Ships **disabled**
(`agentic.harness_optimizer.enabled: false`). It scores fixture-based harness
runs, proposes candidate artifacts in a jailed workspace, and records
human-gated accept/reject decisions. It does **not** call GitHub, spawn a host
shell, or import `gate.py` / `graph.py` / `mcp_hybrid_server.py` (I6).

Accept still requires a human when
`require_human_confirm_for_accept: true` (the shipped default).

## Package map

| Module | Role |
|---|---|
| `core.py` | Experiment / variant / scorecard types and `decide_candidate` |
| `governance.py` | Injection + visible-case-hardcoding inspection |
| `proposer.py` | Build a proposer workspace (`current/`, holdout hidden) |
| `patching.py` | Propose / apply a candidate artifact |
| `scoring.py` | Case results → scorecard / run report |
| `model_adapter.py` | Local proposer client |
| `runners/` | `HarnessRunner` + mock runner; `GitHubCodingRunner` lives here |
| `loop_driver.py` | Multi-iteration loop |
| `mcp/` | `ProposerWorkspaceTools` — **not** an MCP server (see `mcp/README.md`) |

`GitHubCodingRunner` and `loop_driver` are **not** re-exported from
`__init__.py` (circular import with `agentic.deepagent_github`). Import them
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
