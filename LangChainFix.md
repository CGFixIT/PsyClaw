Given CyClaw's hard architectural invariant — the agentic layer (GitHub/FS/SQL/Dropbox/NeMo) is never imported by `gate.py`, `graph.py`, or `mcp_hybrid_server.py` and is only reached via subprocess through `utils/ops_runner.py` — both implementations below preserve that boundary instead of importing deepagents or LangGraph subagents directly into the core graph.[1][2]

## Why this matters for your build

Security Invariant 13 (module isolation) is enforced architecturally, not by code discipline, specifically so a compromised or buggy agentic subsystem can't touch the core RAG/audit pipeline. That means neither implementation below can live inside `graph.py`. Both must be spawned as a subprocess, read config/args over stdin or CLI flags, and return JSON over stdout — exactly like the existing 5 agentic subsystems (GitHub/FS/SQL/Dropbox/NeMo), all disabled by default with explicit opt-in plus a human reason required for any write path.[2]

***

## Repo layout — deepagents version

```
CyClaw/
├── gate.py                          # untouched — never imports agentic/
├── graph.py                         # untouched — 9-node LangGraph controller
├── mcp_hybrid_server.py             # untouched
├── utils/
│   └── ops_runner.py                # existing subprocess dispatcher — entrypoint
└── agentic/
    └── deepagent_github/
        ├── __init__.py
        ├── cli.py                   # subprocess entrypoint (stdin JSON in, JSON out)
        ├── config.py                # env-driven, read-only default
        ├── subagents/
        │   ├── __init__.py
        │   ├── reviewer.py           # read-only: PR review, issue triage
        │   └── executor.py           # write-capable: branch/commit/PR, gated
        ├── builder.py                # create_deep_agent() assembly
        ├── permissions.py            # Allow/Deny path + repo scoping
        └── audit.py                  # SHA-256 hashed event emission
```

`agentic/deepagent_github/config.py`:

```python
import os
from dataclasses import dataclass

@dataclass(frozen=True)
class GithubAgentConfig:
    enabled: bool = os.getenv("CYCLAW_GITHUB_AGENT_ENABLED", "false").lower() == "true"
    write_enabled: bool = os.getenv("CYCLAW_GITHUB_WRITE_ENABLED", "false").lower() == "true"
    repo: str = os.getenv("CYCLAW_GITHUB_REPO", "CGFixIT/CyClaw")
    allowed_paths: tuple[str, ...] = tuple(
        p for p in os.getenv("CYCLAW_GITHUB_ALLOWED_PATHS", "src/**,tests/**").split(",") if p
    )
    require_human_reason: bool = True  # non-negotiable per Invariant 13 write gate

CONFIG = GithubAgentConfig()
```

`agentic/deepagent_github/subagents/reviewer.py`:

```python
from deepagents import SubAgent
from deepagents.middleware import FilesystemMiddleware

def gh_get_pr_diff(pr_number: int) -> str: ...
def gh_list_issues(state: str = "open") -> str: ...

reviewer = SubAgent(
    name="gh_reviewer",
    description="Read-only GitHub reviewer: PR review, issue triage, risk flags. No writes.",
    system_prompt=(
        "You review CyClaw GitHub activity. Never propose direct pushes. "
        "Return: findings, risk level, suggested patch plan, and whether "
        "human sign-off is required before any write action."
    ),
    tools=[gh_get_pr_diff, gh_list_issues],
    middleware=[FilesystemMiddleware(tools=["read_file", "ls", "glob", "grep"])],
)
```

`agentic/deepagent_github/subagents/executor.py`:

```python
from deepagents import SubAgent
from deepagents.middleware import FilesystemMiddleware
from ..permissions import repo_scoped_permissions
from ..config import CONFIG

def gh_create_branch(name: str) -> str: ...
def gh_commit_and_push(branch: str, message: str) -> str: ...
def gh_open_pr(branch: str, title: str, body: str) -> str: ...

executor = SubAgent(
    name="gh_executor",
    description="Write-capable GitHub agent: branch/commit/PR only. Gated by human reason.",
    system_prompt=(
        "You make the smallest viable change, on a new branch, "
        "then open a PR. Never push to main. Summarize exactly what changed."
    ),
    tools=[gh_create_branch, gh_commit_and_push, gh_open_pr],
    middleware=[FilesystemMiddleware(tools=["read_file", "write_file", "edit_file", "ls", "glob", "grep"])],
    permissions=repo_scoped_permissions(CONFIG.allowed_paths),
) if CONFIG.write_enabled else None
```

`agentic/deepagent_github/permissions.py`:

```python
from deepagents.permissions import Allow, Deny

def repo_scoped_permissions(allowed_paths: tuple[str, ...]) -> list:
    rules = [Allow(p) for p in allowed_paths]
    rules += [Deny(".env"), Deny("secrets/**"), Deny("**")]
    return rules
```

`agentic/deepagent_github/builder.py`:

```python
from deepagents import create_deep_agent
from .subagents.reviewer import reviewer
from .subagents.executor import executor
from .config import CONFIG

def build_agent():
    subagents = [reviewer]
    if executor is not None:
        subagents.append(executor)

    return create_deep_agent(
        model="anthropic:claude-sonnet-4-6",
        system_prompt=(
            "You are the CyClaw GitHub subagent supervisor. "
            "Use gh_reviewer first. Only call gh_executor if write_enabled "
            "and a human reason string is present in the task input."
        ),
        subagents=subagents,
        debug=False,
    )
```

`agentic/deepagent_github/cli.py` (the actual subprocess entrypoint `ops_runner.py` calls):

```python
import sys, json
from .builder import build_agent
from .config import CONFIG
from .audit import emit_audit_event

def main():
    payload = json.loads(sys.stdin.read())
    task = payload.get("task", "")
    human_reason = payload.get("human_reason")

    if not CONFIG.enabled:
        print(json.dumps({"error": "github_agent_disabled"}))
        return

    if payload.get("write") and not human_reason:
        print(json.dumps({"error": "write_requires_human_reason"}))
        return

    agent = build_agent()
    result = agent.invoke({"messages": [{"role": "user", "content": task}]})

    emit_audit_event(task=task, write=bool(payload.get("write")), human_reason=human_reason)
    print(json.dumps({"result": str(result)}))

if __name__ == "__main__":
    main()
```

`agentic/deepagent_github/audit.py` — must match the existing `audit.jsonl` contract (SHA-256 hashed query text, event-typed, no plaintext leak):[2]

```python
import hashlib, json, time

def emit_audit_event(task: str, write: bool, human_reason: str | None) -> None:
    event = {
        "event_type": "agentic_github_write" if write else "agentic_github_read",
        "task_hash": hashlib.sha256(task.encode()).hexdigest(),
        "human_reason_present": bool(human_reason),
        "ts": time.time(),
    }
    with open("audit.jsonl", "a") as f:
        f.write(json.dumps(event) + "\n")
```

`ops_runner.py` integration (existing file, add one dispatch branch):

```python
def run_github_agent(payload: dict) -> dict:
    proc = subprocess.run(
        [sys.executable, "-m", "agentic.deepagent_github.cli"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=120,
    )
    return json.loads(proc.stdout or "{}")
```

This keeps `gh_reviewer` read-only by tool omission, gates `gh_executor` behind both `CYCLAW_GITHUB_WRITE_ENABLED` and a mandatory human-reason string, mirrors the "WRITES DISABLED" safe-default UI pattern already shown in the FS/agentic console, and never lets deepagents touch the process running `gate.py`.[2]

***

## LangGraph-native version — explicit nodes/reducers

If you'd rather avoid the deepagents dependency and fold this into a graph that mirrors your existing 9-node style, here's the explicit-node version, still spawned as a subprocess:

```
agentic/langgraph_github/
├── __init__.py
├── cli.py               # subprocess entrypoint
├── state.py              # TypedDict + reducers
├── nodes.py               # explicit node functions
├── graph.py                # StateGraph wiring
└── tools_github.py          # raw GitHub API calls
```

`state.py`:

```python
from typing import TypedDict, Annotated
import operator

class GithubAgentState(TypedDict):
    task: str
    human_reason: str | None
    write_enabled: bool
    review_notes: Annotated[list[str], operator.add]
    plan: str | None
    diff_summary: str | None
    pr_url: str | None
    audit_events: Annotated[list[dict], operator.add]
    error: str | None
```

Using `Annotated[..., operator.add]` as the reducer means every node that returns `review_notes` or `audit_events` appends rather than overwrites — this is the standard LangGraph reducer pattern for accumulating state across nodes.

`nodes.py`:

```python
import hashlib, time
from .state import GithubAgentState
from .tools_github import get_open_prs, get_pr_diff, create_branch, commit_and_push, open_pr

def node_review(state: GithubAgentState) -> dict:
    prs = get_open_prs()
    notes = [f"PR #{pr['number']}: {pr['title']}" for pr in prs]
    return {"review_notes": notes}

def node_plan(state: GithubAgentState) -> dict:
    plan = f"Proposed action for task: {state['task']}"
    return {"plan": plan}

def node_gate_write(state: GithubAgentState) -> dict:
    if not state["write_enabled"]:
        return {"error": "write_disabled"}
    if not state.get("human_reason"):
        return {"error": "human_reason_required"}
    return {}

def node_execute(state: GithubAgentState) -> dict:
    branch = f"cyclaw-agent-{int(time.time())}"
    create_branch(branch)
    diff_summary = commit_and_push(branch, message=state["plan"] or "agent change")
    pr_url = open_pr(branch, title="CyClaw agent change", body=state["plan"] or "")
    return {"diff_summary": diff_summary, "pr_url": pr_url}

def node_audit(state: GithubAgentState) -> dict:
    event = {
        "event_type": "agentic_github_write" if state.get("pr_url") else "agentic_github_read",
        "task_hash": hashlib.sha256(state["task"].encode()).hexdigest(),
        "human_reason_present": bool(state.get("human_reason")),
        "error": state.get("error"),
        "ts": time.time(),
    }
    return {"audit_events": [event]}
```

`graph.py`:

```python
from langgraph.graph import StateGraph, END
from .state import GithubAgentState
from .nodes import node_review, node_plan, node_gate_write, node_execute, node_audit

def route_after_gate(state: GithubAgentState) -> str:
    return "execute" if not state.get("error") else "audit"

def build_graph():
    g = StateGraph(GithubAgentState)
    g.add_node("review", node_review)
    g.add_node("plan", node_plan)
    g.add_node("gate_write", node_gate_write)
    g.add_node("execute", node_execute)
    g.add_node("audit", node_audit)

    g.set_entry_point("review")
    g.add_edge("review", "plan")
    g.add_edge("plan", "gate_write")
    g.add_conditional_edges("gate_write", route_after_gate, {"execute": "execute", "audit": "audit"})
    g.add_edge("execute", "audit")
    g.add_edge("audit", END)

    return g.compile()
```

`cli.py` (subprocess entrypoint, same contract as the deepagents version):

```python
import sys, json
from .graph import build_graph

def main():
    payload = json.loads(sys.stdin.read())
    graph = build_graph()

    result = graph.invoke({
        "task": payload.get("task", ""),
        "human_reason": payload.get("human_reason"),
        "write_enabled": bool(payload.get("write")),
        "review_notes": [],
        "plan": None,
        "diff_summary": None,
        "pr_url": None,
        "audit_events": [],
        "error": None,
    })

    with open("audit.jsonl", "a") as f:
        for event in result["audit_events"]:
            f.write(json.dumps(event) + "\n")

    print(json.dumps({
        "review_notes": result["review_notes"],
        "plan": result["plan"],
        "pr_url": result.get("pr_url"),
        "error": result.get("error"),
    }))

if __name__ == "__main__":
    main()
```

***

## Which to actually run in CyClaw

| Dimension | deepagents version | LangGraph-native version |
|---|---|---|
| Fits existing 9-node graph style | No — separate harness | Yes — same StateGraph pattern as `graph.py` |
| Read-only vs write isolation | Achieved via tool omission + `permissions` | Achieved via explicit `node_gate_write` + conditional edge |
| State accumulation model | Deep agent internal state schema | Explicit `Annotated[list, operator.add]` reducers, fully visible |
| Debuggability given CyClaw's "brutal honesty" bug-tracking culture [2] | Harder — internal deepagents orchestration is a black box | Easier — every transition is a named node you can log/test individually |
| Dependency footprint | Adds `deepagents` package | Zero new dependency beyond `langgraph`, already in your stack [2] |

Given CyClaw already runs LangGraph 1.2.6 for the core 9-node controller and prioritizes topology-enforced invariants you can point at in an audit, **the LangGraph-native version is the better architectural fit** — it's dependency-free, matches your existing node/audit conventions exactly, and every gate (`node_gate_write`) is a single testable function rather than something buried inside a deepagents harness you'd have to trust. The deepagents version is worth it only if you plan to add many more agentic connectors and want the batteries-included subagent/permission machinery instead of hand-rolling gates for each one.[2]

Sources
[!!!] - 
[1] CyClaw_Architecture_Guide_v1.9.0_crisp.pdf https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/collection_bb753fae-2826-4344-be5a-7a4bfcfb2760/4e2a11ff-ab32-4e8f-b3dc-1efbd4149ef1/CyClaw_Architecture_Guide_v1.9.0_crisp.pdf
[2] CyClaw_Swarm_Verification_Report_2026-07-09.pdf https://ppl-ai-file-upload.s3.amazonaws.com/web/direct-files/collection_bb753fae-2826-4344-be5a-7a4bfcfb2760/e0dcc5c1-b3f5-4171-8f15-6df2022f7e25/CyClaw_Swarm_Verification_Report_2026-07-09.pdf
[3] https://docs.langchain.com/oss/python/integrations/providers/overview
[4] https://docs.langchain.com/oss/python/deepagents/overview
[5] https://docs.langchain.com/oss/python/learn