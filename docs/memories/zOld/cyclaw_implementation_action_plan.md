# CyClaw Deep Agents Integration — Actionable Implementation Plan
**Synthesized from full planning thread | July 2026**
**Scope: Phases 0–9 gate-to-gate, with HITL decorator deferred to post-phase-7**

---

## Thread Summary (What Was Decided)

This thread resolved three blocking code issues, established the canonical LangChain Deep Agents subagent spec, and produced five planning artifacts:

1. **Dead code** — 6 unreferenced `deepagent_github/` files + `draft_plan()` → document with `# TODO(unwired)` + raise `NotImplementedError`
2. **`builder.py:94-99` toothless agent bug** — bare name strings + `tools=[]` silently report success; must use validated SubAgent spec dicts with resolved `Callable` objects
3. **Speculative API surface** — `GovernanceFinding`, `HarnessRunner` Protocol, 9 unused `SurfaceType` members → either classify as a Hook, delete, or gate behind explicit consumers
4. **HITL approval** — `interrupt_on` + checkpointer is the native Deep Agents mechanism; `when` predicates for conditional gating; subagent-specific policies override parent
5. **GitHub-specific HITL** — risk-tiered approval tiers (read → auto, create → approve/reject, merge/delete/secrets → approve/edit/reject)
6. **HITL decorator** — async approval queue + dynamic risk scorer + timeout handler; **deferred until phases 0–7 complete**

---

## Phase Gate Map

```
Phase 0 ✅  Project scaffold + CI baseline
Phase 1 ✅  Core graph + RAG-first topology
Phase 2 ✅  MCP server + tool routing
Phase 3 ✅  Agentic harness optimizer (PR #493 merged; NoReturn fix applied)
Phase 4 ✅  GitHub agentic integration scaffold
Phase 5 ✅  18/18 unit tests pass; invariant-guard 27/27 pass
─────────────────────────────────────────────────
Phase 6 →  builder.py fix + dead code cleanup + speculative surface removal
Phase 7 →  SubAgent validation + interrupt_on HITL integration
─────────────────────────────────────────────────
Phase 8 →  (post-gate) Qwen / open-weight model harness swap
Phase 9 →  (post-gate) HITL decorator production integration + GitHub tool gating
```

---

## Phase 6 — Dead Code Cleanup + builder.py Fix

**Goal:** No silent failure paths. No unreferenced modules linter will delete as cruft.

### Step 6.1 — Annotate unreferenced files

Add a `# TODO(unwired)` block to the top of each file. Minimum required fields per file:

```python
# TODO(unwired): runners.py — subagent task executor
# Intended import path: deepagent_github.runners
# Planned consumer:     deepagent_github.builder (factory → executor handoff)
# Blocker:              Awaiting validated SubAgent spec shape (see builder.py fix)
# Do not delete: intentionally dormant pending Phase 7 wiring.
```

Files requiring annotation:
- `core.py` — agent + context abstractions
- `runners.py` — subagent executor
- `memory.py` — memory management
- `skills.py` — capability/skill definitions
- `governance.py` — governance gate logic (reclassify as `hooks/governance_gate.py` in Phase 7)
- `config.py` — configuration schemas

### Step 6.2 — Fix `draft_plan()`

```python
def draft_plan(task_description: str, context: dict) -> Plan:
    """
    # STUB: Returns hardcoded constants. Do not call in production.
    # TODO(unwired): Implement real plan generation.
    # Blocker: (1) goal representation schema, (2) subagent delegation policy,
    #          (3) memory integration contract.
    """
    raise NotImplementedError(
        "draft_plan() is not implemented. See TODO comments in core.py. "
        "Do not route real tasks through this function."
    )
```

### Step 6.3 — Kill speculative API surface

| Symbol | Action |
|--------|--------|
| `governance_gate_strings` / `GovernanceFinding` | Move to `hooks/governance_gate.py`; remove from public exports until there is a real consumer |
| `HarnessRunner` Protocol | Delete entirely until ≥2 real implementers exist |
| 9 unused `SurfaceType` members | Prune to exactly what `builder.py` and `runners.py` reference; add a comment citing the trimming |

**Verification:** `grep -r "GovernanceFinding\|HarnessRunner\|SurfaceType" . --include="*.py"` should return only definition sites, not consumers.

---

## Phase 7 — SubAgent Validation + HITL Integration

**Goal:** `builder.py` never silently succeeds. HITL gates real tool calls before execution.

### Step 7.1 — Fix `builder.py:94-99`

**The contract:** `tools` must be `list[Callable]`, not `list[str]`. `created=True` only fires after validation passes.

```python
from typing import Callable, Any
from langchain.agents.middleware.human_in_the_loop import (
    HumanInTheLoopMiddleware,
    InterruptOnConfig,
)

def build_subagent(
    config: dict[str, Any],
    tool_registry: dict[str, Callable],
    *,
    allow_empty_tools: bool = False,
) -> dict[str, Any]:
    """
    Build a validated SubAgent spec dict for use with SubAgentMiddleware.

    Args:
        config:          Dict with keys: name, description, system_prompt,
                         tool_names (list[str]), model (optional).
        tool_registry:   Dict[str, Callable] mapping tool name → actual callable.
        allow_empty_tools: If False (default), raises on tools=[].

    Returns:
        Valid SubAgent spec dict.

    Raises:
        ValueError: If required fields are missing or tools are unresolvable.
    """
    name         = config.get("name", "").strip()
    description  = config.get("description", "").strip()
    system_prompt = config.get("system_prompt", "").strip()

    if not name:
        raise ValueError("SubAgent requires 'name'")
    if not description:
        raise ValueError(f"SubAgent '{name}' requires 'description'")
    if not system_prompt:
        raise ValueError(f"SubAgent '{name}' requires 'system_prompt'")

    requested: list[str] = config.get("tool_names", [])
    resolved:  list[Callable] = []
    missing:   list[str] = []

    for tool_name in requested:
        if tool_name in tool_registry:
            resolved.append(tool_registry[tool_name])
        else:
            missing.append(tool_name)

    if missing:
        raise ValueError(
            f"SubAgent '{name}': tool(s) not in registry: {missing}. "
            f"Available: {list(tool_registry.keys())}"
        )

    if not resolved and not allow_empty_tools:
        raise ValueError(
            f"SubAgent '{name}' has zero tools after resolution. "
            "Pass allow_empty_tools=True if this is intentional."
        )

    spec: dict[str, Any] = {
        "name":          name,
        "description":   description,
        "system_prompt": system_prompt,
        "tools":         resolved,
    }

    if "model" in config:
        spec["model"] = config["model"]

    # Only mark created after full validation passes.
    spec["_created"]    = True
    spec["_tool_names"] = [getattr(t, "name", getattr(t, "__name__", str(t))) for t in resolved]

    return spec
```

**CI gate:** Add to `tests/test_builder.py`:

```python
def test_bare_string_tools_rejected():
    with pytest.raises(ValueError, match="not in registry"):
        build_subagent(
            {"name": "x", "description": "x", "system_prompt": "x",
             "tool_names": ["ghost_tool"]},
            tool_registry={},
        )

def test_empty_tools_rejected_by_default():
    with pytest.raises(ValueError, match="zero tools"):
        build_subagent(
            {"name": "x", "description": "x", "system_prompt": "x"},
            tool_registry={},
        )

def test_created_only_after_validation():
    registry = {"read_file": lambda p: p}
    spec = build_subagent(
        {"name": "reader", "description": "reads files",
         "system_prompt": "You read files.", "tool_names": ["read_file"]},
        tool_registry=registry,
    )
    assert spec["_created"] is True
    assert len(spec["tools"]) == 1
```

### Step 7.2 — Wire `interrupt_on` for HITL

**Minimum required pattern:**

```python
from deepagents import create_deep_agent
from langchain.agents.middleware.human_in_the_loop import HumanInTheLoopMiddleware
from langgraph.checkpoint.memory import MemorySaver  # swap for SqliteSaver in prod

checkpointer = MemorySaver()

GITHUB_HITL_POLICY = {
    # Read-only: auto-approve
    "get_file_contents":    False,
    "list_branches":        False,
    "search_code":          False,
    "get_pull_request":     False,
    "get_issue":            False,

    # Medium: approve or reject
    "create_issue":         {"allowed_decisions": ["approve", "reject"]},
    "comment_issue":        {"allowed_decisions": ["approve", "reject"]},
    "create_pull_request":  {"allowed_decisions": ["approve", "reject"]},
    "push_branch":          {"allowed_decisions": ["approve", "reject"]},

    # High: full control
    "merge_pull_request":   {"allowed_decisions": ["approve", "edit", "reject"]},
    "delete_branch":        {"allowed_decisions": ["approve", "edit", "reject"]},
    "close_pull_request":   {"allowed_decisions": ["approve", "reject"]},

    # Critical: no inline edit allowed on secrets
    "create_or_update_secret": {"allowed_decisions": ["approve", "reject"]},
    "update_repo_settings":    {"allowed_decisions": ["approve", "reject"]},
}

agent = create_deep_agent(
    model="anthropic:claude-sonnet-4-6",
    tools=[...],
    interrupt_on=GITHUB_HITL_POLICY,
    checkpointer=checkpointer,
)
```

**Resume pattern:**

```python
from langgraph.types import Command

config = {"configurable": {"thread_id": "cyclaw-run-001"}}

result = agent.invoke(input_messages, config=config, version="v2")

if result.interrupts:
    decisions = [{"type": "approve"}]  # or reject/edit per tool
    result = agent.invoke(Command(resume={"decisions": decisions}), config=config, version="v2")
```

### Step 7.3 — Folder taxonomy lock-in

Establish these as first-class directories before Phase 8:

```
deepagent_github/
├── agents/          # SubAgent spec dicts + AGENTS.md templates
├── hooks/           # Deterministic pre/post logic (governance_gate.py goes here)
├── mcp/             # Authenticated MCP connections
├── skills/          # Reusable, portable skill directories
└── subagents/       # builder.py, runners.py, validated spec factory
```

No single umbrella term — each folder has different loading semantics and is its own first-class concept.

---

## Phase 8 — Qwen / Open-Weight Model Harness Swap

**Goal:** Replace Anthropic-hosted model with Qwen (or Kimi K2) at the harness level for cost reduction.

**Key decisions to make before starting:**

| Decision | Options | Recommendation |
|----------|---------|----------------|
| Model swap point | Harness-level `model=` arg | Change `create_deep_agent(model=...)` only |
| Qwen protocol | Qwen 3.7 supports Anthropic Messages API natively | Drop-in; no integration rewrite needed |
| Cost tradeoff | Qwen: $2.50/$7.50 per-million but 3–4x in agentic loops; Kimi K2 (Cursor 2.5): ~$0.50/task vs ~$7/task Opus | Kimi K2 if optimizing spend; Qwen if optimizing protocol compatibility |
| Extended thinking | Qwen extended thinking is ON by default | Cap `max_tokens` manually or cost runs 3–4x headline rate |
| Caching | Qwen supports prefix caching | Wire MCP caching layer here; same security posture as CyClaw offline design |
| Fallback | Keep Claude Sonnet as fallback if open-weight fails | Circuit breaker pattern: 3 failures → cooldown → fallback |

**Implementation:** Update `config.py` (now `# TODO(unwired)`) to expose `MODEL_PROVIDER` env var. The harness reads `MODEL_PROVIDER=qwen:qwen3-7b-max` and passes it to `create_deep_agent`. No other layer changes.

---

## Phase 9 — HITL Decorator Production Integration

**Gate condition:** Only start Phase 9 after Phase 7 HITL is verified working end-to-end in tests.

**What was built in this thread (deferred artifacts):**

- `cyclaw/hitl/risk_scorer.py` — Dynamic `RiskLevel` scorer (LOW/MEDIUM/HIGH/CRITICAL) using pattern matching on tool name + description + HTTP method annotation
- `cyclaw/hitl/approval_queue.py` — Async `ApprovalQueue` with `asyncio.wait_for` timeout, per-request `Event` + result slot, and `Decision` enum (APPROVE/EDIT/REJECT/TIMEOUT)
- `cyclaw/hitl/decorator.py` — `@require_approval` decorator (sync/async dual-path via `asyncio.iscoroutinefunction`) integrating risk scorer + queue
- `cyclaw/hitl/dashboard.py` — Mock terminal dashboard (curses-free) for approve/edit/reject during local dev; swap for web UI in production

**Integration steps:**

1. Wire `ApprovalQueue` as a singleton in `config.py` / DI container
2. Apply `@require_approval(queue=approval_queue)` to GitHub mutation tools before passing them to `build_subagent()`
3. Run dashboard consumer as a background `asyncio.Task` alongside the agent loop
4. In production, replace the terminal dashboard with a FastAPI webhook endpoint that calls `queue.resolve(decision)`
5. Add audit log writer that persists every `ReviewDecision` to SQLite (or Postgres)
6. Add timeout escalation: stale approvals past SLA → auto-reject + alert

---

## Post-Phase-9 Checklist (Before Calling It Production)

- [ ] `InMemorySaver` replaced with `SqliteSaver` (local) or `AsyncPostgresSaver` (multi-session)
- [ ] All `@task` decorators on external API calls (idempotent replay on checkpoint restart)
- [ ] Import audit CI script — `ruff check` + `invariant-guard` passing clean
- [ ] No bare string tools anywhere in `builder.py` call sites — enforced by grep gate in CI
- [ ] `draft_plan()` raises `NotImplementedError` — confirmed in test suite
- [ ] Speculative surface gone — `HarnessRunner` deleted, `GovernanceFinding` behind `hooks/`
- [ ] `GITHUB_HITL_POLICY` dict is the single source of truth — no approval logic in prompts
- [ ] Every approval decision logged with: tool name, args hash, reviewer identity, timestamp, decision
- [ ] Timeout + fallback behavior tested under simulated reviewer absence
- [ ] AGENTS.md subagent templates in `.deepagents/agents/` validated by `scripts/validate_agents.py`

---

## HITL Decorator — Defer Until This Condition Is True

> **Gate:** Phase 7 HITL (`interrupt_on` + checkpointer) is working end-to-end in a real agent run,
> with at least one approve, one reject, and one timeout test case passing in CI.

The decorator adds a second approval layer on top of `interrupt_on`. That's useful for tools that
live *outside* the Deep Agents harness (standalone scripts, CLI tools, non-deepagents LangChain chains).
Inside the harness, `interrupt_on` is the correct and sufficient mechanism — adding the decorator
on top of it creates double-gating with no additional security and extra complexity.

---

## Quick Reference: Canonical SubAgent Spec

```python
subagent = {
    "name":          "github-reviewer",           # required, unique
    "description":   "Reviews PRs and issues",    # required, action-oriented
    "system_prompt": "You are a code reviewer.",  # required, does NOT inherit from parent
    "tools":         [get_pull_request, comment_issue],  # list[Callable], not list[str]
    "model":         "anthropic:claude-sonnet-4-6",      # optional override
    "interrupt_on": {                              # overrides parent policy
        "comment_issue": {"allowed_decisions": ["approve", "reject"]},
        "get_pull_request": False,
    },
    "skills":        ["/skills/code-review/"],    # optional
}
```

**Three unconditionally required fields:** `name`, `description`, `system_prompt`
**`tools=[]` + `created=True` = silent failure.** Never allow this state.

---

*Generated from CyClaw planning thread synthesis | Phases 0-5 ✅ | Phases 6-9 queued*
