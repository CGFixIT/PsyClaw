# NeMo Phase 5 — wrap `POST /api/agent/run` through ToolBroker (contract only)

**Status (2026-08-27):** **contract only.** This note is the planning doc for
the next ToolBroker caller after WebTool (#1157) and harness `/loop` (#1158).
It does **not** implement the wrap.

Audience: the maintainer who next opens `grok/1134-phase5-agent-run-broker`.
Read this before touching `harness/server.py` `agent_run`.

Related:

- Issue [#1134](https://github.com/cgfixit/CyClaw/issues/1134) Phase 5 remainder
- [#1157](https://github.com/cgfixit/CyClaw/pull/1157) `utils.tool_broker` (canonical)
- [#1158](https://github.com/cgfixit/CyClaw/pull/1158) `/loop` wrap (restack before relying on it; parent moved to `utils.tool_broker`)
- [`phase4b_soul_leak.md`](./phase4b_soul_leak.md) (same “contract only” shape)
- `tests/test_tool_broker_adversarial.py` (benign `agent_run` name is already in the allow-table)

---

## Wrong target

Issue remaining text said “harness `/loop` + agentic proposer under the same
ToolBroker.” The proposer is **not a tool path**.

`agentic/harness_optimizer/proposer.py` `build_proposer_workspace` creates
local artifact directories only. Its module docstring states it does not
expose file tools, run commands, call models, or apply proposals. Wrapping
that function as `proposer_workspace` would be a name-gate on `mkdir`.

NVIDIA ToolRailAction / IORails tool rails validate `ToolCall` / `ToolResult`.
They do not apply to workspace builders or to `/loop` chat. Do not enable
IORails as the production engine to “cover” this route.

---

## Right target

`POST /api/agent/run` in `harness/server.py` (`agent_run`).

Live chain today:

1. FastAPI `dependencies=guarded` (API key + CSRF).
2. `resolve_check_profiles(req.checks)` — profile **names**, never argv.
3. `_agentic_call("real-repo-run", lambda: run_agentic_op("real-repo-run", ...))`.
4. `utils.ops_runner` subprocess into `agentic.cli` (argv list, not `shell=True`).

`AgentRunRequest` already requires `instruction`, `reason`, `confirm`
(default **false**). Omitting confirm hits the CLI refusal (exit 4). The
broker is a **name-gate in front of that subprocess**, not a replacement
for confirm/reason.

---

## Decisions a future PR must satisfy

### 1 — Import `utils.tool_broker`, never `guardrails`

After #1157, canonical code is `utils/tool_broker.py`. Harness must not
import the `guardrails` package (I6 sibling-OOB AST guard). Use:

```python
from utils.tool_broker import ToolDenied, assert_allowed
```

`guardrails.tool_broker` remains a re-export for guardrails-side tests only.

### 2 — Call site

After `resolve_check_profiles` succeeds, **before** `_agentic_call`:

```python
assert_allowed(
    "agent_run",
    ("real-repo-run",),
    allowlist=frozenset({"agent_run"}),
)
```

On `ToolDenied`: HTTP 403, code `TOOL_DENIED`. `run_agentic_op` must not
run (spy in the test). Empty-allowlist monkeypatch is the deny path; the
production allowlist on this route is `{agent_run}`.

Ordinary `POST /api/chat` (`loop=false`) does not call `decide`.

### 3 — Argv is the op name, not the instruction

Argv is exactly `("real-repo-run",)`.

**Forbidden in argv and in the audit event:** `instruction`, `reason`,
`commit_message`, `branch`, `read_files`, URLs, file paths. Audit already
stores name + argv digest only; do not add free-text fields.

### 4 — Existing gates stay

Do not delete or bypass: API key, CSRF, `confirm` default false, non-empty
`reason`, check-profile allowlist, ops_runner argv list. ToolBroker cannot
grant a run when confirm is missing.

### 5 — Out of scope for that PR

| Surface | Why later / never |
|---|---|
| `POST .../decision` / `push` / `publish` | Separate tool names (`agent_decide`, `agent_push`, `agent_publish`) on their own PRs |
| MCP `tools/call` | I6: `mcp_hybrid_server.py` must not import brokers |
| `build_proposer_workspace` | Not a tool |
| `guardrails.enabled = true` | Not planned by #1134 |
| IORails / `LLMRails.generate_async` on `/query` | Forbidden |
| 13th graph node | Forbidden |

### 6 — Topology

Stack on #1157 (`utils.tool_broker`) or on `main` after #1157 merges. Do not
edit `tests/test_tool_broker_adversarial.py` or this file except to mark the
contract shipped. Do not restack by rewriting #1158 in the same PR.

Files the wrap PR is expected to touch:

- `harness/server.py` (one call site)
- `tests/test_harness_agent_routes.py` (or a small new test module)
- not `docs/NeMo/README.md` (owned by other open drafts)

---

## Verify the wrap PR must run

```
GROK_API_KEY=dummy python -m pytest tests/test_harness_agent_routes.py tests/test_tool_broker_adversarial.py tests/test_harness_isolation.py -q --tb=short
ruff check --select E,F,I,B,C4,UP,S harness/server.py
```

Empty allowlist → 403 `TOOL_DENIED` and zero `run_agentic_op` calls. Happy
path still requires `confirm=true` + `reason`. soul.md unchanged.
`gate.py` / `graph.py` / `mcp_hybrid_server.py` untouched.
