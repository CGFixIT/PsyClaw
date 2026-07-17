## Lang Harness + Deep Agents through pt 5 but carefully consider and reply to these in repo file or in cc:

1.	
6 of 12 deepagent_github/ files are fully unreferenced — core.py, runners.py, memory.py, skills.py, governance.py, config.py are on no import path, in no test, and not in __all__. draft_plan() ignores its input and returns hardcoded constants.
* by design with out of hand but specifics should be added plus #2 leads into the same type of q

2.	
builder.py:94-99 reports created=True with tool_names populated, but calls the creator with tools=[] and bare subagent name strings — against real deepagents this builds a toothless agent while advertising success. Fixing it means deciding the seam’s intended semantics, which I didn’t want to guess.
# good instinct; think about and research this after understanding langchain deep agents better - also why did grok call the "better harness" a doorbell hahahaha that sounds more like a hook or pattern not a harness... anyways the planning guide was way more but the question is more of the deep agent can that still be cyclaw mcp Qwen cached? With basically similar security design I did with cyclaw but with a harness and agentic coding - use the agentic github integration and the python coding agent w ponytail and karpathy skills and rrk (or ptk or caveman if research shows benefit with ponytail and custom token efficient skill or toll or something - sidenote find out if there's a somewhat generic reference to these folders besides obvious like skills or synonyms of hooks or tools etc, does each thing have their own or is there a standard?)
I'm talking to myself but that q is the most specific to what I understand the deep agent purpose to be which I filled every detail in the planning guide except that part ha 

3.	
Speculative API surface: governance_gate_strings/GovernanceFinding exported with zero consumers; HarnessRunner Protocol with only a mock implementer; 9 of 11 SurfaceType members unused.
Ha I read that as two things but either way research more the next few days but this planning doc and cleanup is the only changes left really other than deep agents due to it being way cheaper if I can out a Chinese open weight model in a harness and use the agentic github thing. Might be worth making a new web interface for that hahahaha there I go again

--

--

--

One-line type fix in `agentic/harness_optimizer/mcp/tools.py` (from the phase-4 scaffold, PR #493): `_deny()` unconditionally raises `AgenticError` but was annotated `-> None`. Annotated it `typing.NoReturn` with a short comment explaining why.

## Why / benefit

`_resolve_existing_read` and `_resolve_current_write` are annotated `-> Path` but their `except OSError` branches end by calling `_deny(...)` with no `return`/`raise` after it. With `-> None`, that's an implicit-None fall-through path a type checker can't flag; with `NoReturn`, the contract is sound, and a future refactor that makes `_deny` stop raising becomes a visible type error instead of silently handing callers a `None` path.

## Risk to monitor

None — runtime behavior is byte-identical (an annotation plus a comment). Verified:
- `GROK_API_KEY=dummy pytest tests/test_agentic_harness_optimizer.py tests/test_agentic_harness_phase345.py -q` — 18/18 pass
- `ruff check --select E,F,I,B,C4,UP,S` on the file — clean
- `invariant-guard` — 27/27 pass


---
# phase 0-5 complete - local test then once questions above answered do 6 and 7 then another local test then refactor da shiot outta it

Also AnchorClaw.ai hahah update that later but since im stacking notes to self here

--

The deepagents research above confirms your hypothesis on all three — the real LangChain `deepagents` package treats subagents as typed spec dicts (`name`/`description`/`system_prompt`/`tools`), not bare name strings, and `tools=[]` is a hard failure mode for your `builder.py:94-99`, not a stylistic quirk. Here's the breakdown per point.

## Dead code (files 1-6)

- Fine to leave unreferenced-by-design for now, but log each explicitly in the planning doc with: intended import path, planned consumer, and a `# TODO(unwired): reason` comment in-file — otherwise a future cleanup pass (or linter) will delete them as cruft.
- `draft_plan()` returning hardcoded constants while ignoring input is not "unfinished," it's a landmine — anything that later calls it assuming real output will silently get garbage. Either raise `NotImplementedError` or gate it behind a `# STUB` docstring until it's wired.

## builder.py toothless agent (the real bug)

Per LangChain's actual `SubAgent` spec, `tools` must be a `list[Callable]`, and `name` is just a string field inside a dict alongside required `description` and `system_prompt` — passing bare name strings as if they were tool objects, with `tools=[]`, produces a subagent with zero tools and no task-specific instructions, i.e., exactly the "advertises success, delivers nothing" agent you flagged.[1]

- The seam's correct semantics: `builder.py` should construct proper `SubAgent` dicts (or `CompiledSubAgent` for prebuilt graphs) and only set `created=True`/populate `tool_names` after the tools list is non-empty and validated against the target subagent's actual tool set.[1]
- Recommended fix: add an assertion or validation step — `assert len(subagent["tools"]) > 0 or explicit_empty_flag` — so silent zero-tool creation can never report success.

On "doorbell" vs "harness": Grok's joke tracks — a harness is the full scaffolding (model + tools + skills + hooks + MCP + subagents working together), while a single clever prompt pattern or notification trigger is closer to a hook or a UX affordance (a "doorbell") than a harness in its own right [page:harness-engineering].

## Standard names for the "skills/hooks/tools" folders

There is a semi-standard taxonomy now, per LangChain and the broader agentic-engineering community — five primitives, each with its own dedicated folder/mechanism, no single umbrella term beyond "harness primitives" or "scaffolding":

- AGENTS.md — lightweight, always-loaded repo instructions [page:harness-engineering]
- Skills — named, on-demand directories with a SKILL.md, portable across harnesses (skills.sh is the emerging registry) [page:harness-engineering]
- MCP — authenticated connections to external services (Linear, Slack, Sentry-style), downside is context bloat from tool injection [page:harness-engineering]
- Subagents — bounded/parallel task delegation for context isolation, which is exactly your builder.py target [page:harness-engineering][1]
- Hooks — deterministic pre/post logic (lint, format, approval-gate) that shouldn't depend on the model remembering anything [page:harness-engineering]

So no, there isn't one universal folder name — "skills," "hooks," "MCP," and "subagents" are each their own first-class concept with different loading semantics, and conflating them (as your governance/harness speculative API risks doing) is part of why point 3's surface feels unmoored.

## Open-weight model in harness + Qwen/MCP caching

Qwen 3.7 Max now natively supports the Anthropic Messages protocol, so it can be dropped into an existing Claude Code-style harness without rewriting integration logic — directly relevant to swapping it in under your cyclaw MCP-cached design. Caveat: extended thinking is on by default, and unless you cap `max_tokens` manually, real-world cost runs 3-4x the $2.50/$7.50 per-million headline rate in agentic coding loops. For pure cost-per-task at similar capability, Cursor's Composer 2.5 (built on Kimi K2.5) scores ~63% on CursorBench at ~$0.50/task versus Opus-tier ~$7/task, which may be a more relevant cost benchmark 
than Qwen if you're optimizing spend rather than protocol compatibility.[2]

## Point 3: speculative API surface

Kill it or ship it — `governance_gate_strings`/`GovernanceFinding` with zero consumers, a `Protocol` with only a mock implementer, and 9 of 11 unused `SurfaceType` members is textbook YAGNI debt. Per the harness taxonomy above, if this is meant to become a hook (deterministic gate logic) or a skill (governance workflow), name it as such explicitly in the planning doc now — vague "governance surface" abstractions without a real consumer are exactly the kind of thing that rots silently like your unreferenced files did.

Sources
[1] The general-purpose subagent https://docs.langchain.com/oss/python/deepagents/subagents
[2] Three AI coding agents launched in 72 hours, just changed what developers pay for intelligence https://www.developer-tech.com/news/ai-coding-agents-cursor-anthropic-alibaba-price-floor-2026/
[3] Tools - Docs by LangChain https://docs.langchain.com/oss/python/deepagents/tools
[4] Builtin_tools (optional) https://pypi.org/project/deepagents/0.0.5rc4/
[5] subagents | deepagents - LangChain Reference https://reference.langchain.com/python/deepagents/subagents
[6] LangChain Deep Agents: Build Agents for Complex, Multi-Step Tasks https://www.langchain.com/deep-agents
[7] create_deep_agent | deepagents https://reference.langchain.com/python/deepagents/graph/create_deep_agent
[8] subagents.py - middleware - GitHub https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/subagents.py
[9] Building Deep Agents with LangChain: A Complete Hands-On Tutorial https://krishcnaik.substack.com/p/building-deep-agents-with-langchain
[10] Kimi K2.7 Code vs DeepSeek V4 vs Qwen3: Best Open-Weight Model for Your MCP Agent Stack (2026) https://pondero.ai/coding/guides/kimi-k27-vs-deepseek-v4-vs-qwen3-coding-agents-june-2026/
[11] tools | deepagents - LangChain Reference Docs https://reference.langchain.com/python/deepagents/middleware/async_subagents/AsyncSubAgentMiddleware/tools
[12] GitHub - langchain-ai/deepagents: The batteries-included agent harness. https://github.com/langchain-ai/deepagents
[13] CreateDeepAgentParams | deepagents - LangChain Reference https://reference.langchain.com/javascript/deepagents/types/CreateDeepAgentParams
[14] Part VIII - Project Structure and Organization - Agent Skill Hub https://agentskillhub.dev/u/Divith123/sk/part-viii-project-structure-and-organization
[15] Harness engineering | Software Mansion Agentic Engineering Guide https://agentic-engineering.swmansion.com/becoming-productive/harness-engineering/


—


In LangChain deepagents, a subagent spec is a structured config, not just a name. At minimum it needs `name`, `description`, and `system_prompt`/`systemPrompt`; in practice Python middleware expects fully specified subagents with `model` and `tools` too.[1][2]

## Python pattern

- Typical Python shape: `{"name": "researcher", "description": "Research agent", "system_prompt": "You are a researcher.", "model": "...", "tools": [search_tool]}`.[2]
- `SubAgentMiddleware(..., subagents=[...])` takes a sequence of `SubAgent | CompiledSubAgent`, and the docs explicitly say each raw `SubAgent` must specify `model` and `tools`. [2]

```python
from deepagents.middleware import SubAgentMiddleware

subagents = [
    {
        "name": "researcher",
        "description": "Researches topics on the web",
        "system_prompt": "You are a focused research assistant.",
        "model": "openai:gpt-5.5",
        "tools": [search_tool],
    }
]
```

## JS pattern

- The JS reference lists required fields as `name`, `description`, and `systemPrompt`; optional fields include `model`, `tools`, `middleware`, `interruptOn`, and `skills`.[1]
- Example from the docs: `const researcher = { name, description, systemPrompt, tools, skills }`.[1]

```ts
const researcher = {
  name: "researcher",
  description: "Research assistant for complex topics",
  systemPrompt: "You are a research assistant.",
  tools: [webSearchTool],
  skills: ["/skills/research/"],
};
```

## AGENTS.md variant

- In Deep Agents Code, a custom subagent can also be defined as `.deepagents/agents/{subagent-name}/AGENTS.md` with YAML frontmatter plus markdown body.[3]
- There, frontmatter requires `name` and `description`; the markdown body becomes the subagent’s `system_prompt`, and `model` is optional. `tools`, `middleware`, `interrupt_on`, and `skills` are not configurable through that frontmatter and instead inherit from the main agent.[3]

## Practical rule

- Brutally honest version: if your builder reports success while passing bare subagent name strings or `tools=[]`, it is not creating a valid useful deepagent seam; it is creating a cosmetically successful but operationally toothless subagent.[2][1]
- Safe rule: build validated spec objects, require non-empty tools unless intentionally inherited, and only mark `created=True` after the compiled or raw subagent config matches actual deepagents expectations.[2][3]

Sources
[1] SubAgent | deepagents - LangChain Reference Docs https://reference.langchain.com/javascript/deepagents/index/SubAgent
[2] SubAgentMiddleware | deepagents - LangChain Reference https://reference.langchain.com/python/deepagents/middleware/subagents/SubAgentMiddleware
[3] Use subagents in Deep Agents Code - Docs by LangChain https://docs.langchain.com/oss/python/deepagents/code/subagents
[4] The general-purpose subagent https://docs.langchain.com/oss/python/deepagents/subagents
[5] create_deep_agent | deepagents https://reference.langchain.com/python/deepagents/graph/create_deep_agent
[6] Building Multi-Agent Applications with Deep Agents - LangChain https://www.langchain.com/blog/building-multi-agent-applications-with-deep-agents
[7] Customize Deep Agents - Docs by LangChain https://docs.langchain.com/oss/python/deepagents/customization
[8] subagents.py - middleware - GitHub https://github.com/langchain-ai/deepagents/blob/main/libs/deepagents/deepagents/middleware/subagents.py
[9] subagents | deepagents - LangChain Reference https://reference.langchain.com/python/deepagents/middleware/subagents
[10] Deep Agents overview - Docs by LangChain https://docs.langchain.com/oss/python/deepagents/overview
[11] Programmatic subagents - Docs by LangChain https://docs.langchain.com/oss/javascript/deepagents/dynamic-subagents.md
[12] subagents | deepagents - LangChain Reference https://reference.langchain.com/python/deepagents/subagents
[13] deepagents · PyPI https://pypi.org/project/deepagents/0.0.4/
[14] subagents | deepagents - LangChain Reference Docs https://reference.langchain.com/javascript/deepagents/subagents
[15] TASK_SYSTEM_PROMPT | deepagents - LangChain Reference Docs https://reference.langchain.com/python/deepagents/middleware/subagents/TASK_SYSTEM_PROMPT




—




For CyClaw, the clean pattern is: keep **thread state** ephemeral and resumable, keep **memory** narrow and durable, and treat dynamic subagent spawning as a policy-controlled execution primitive, not a free-for-all. LangGraph itself recommends this split: checkpointers for short-term thread-scoped state, stores for long-term cross-thread facts; Deep Agents adds isolated subagent context and dynamic subagents for code-driven fan-out when scale or branching demands it.[1][2]

## Memory model

- Use a 3-tier model: `checkpoint` for active run/thread continuity, `store` for promoted durable facts, and file-backed audit/event logs for forensics and replay. This matches LangGraph’s short-term-vs-long-term persistence split and fits CyClaw’s existing offline, small-surface design better than one giant shared memory blob.[2][3][4]
- Do **not** let subagents write directly into shared long-term memory by default. Parent/subgraph state is namespace-isolated, and LangGraph explicitly warns parent graphs may not immediately see subgraph updates; shared store should be used only for data intentionally crossing boundaries.[2]

## Persistence rules

- Production rule: `InMemorySaver` is for dev only; use persistent checkpointing in production, and prune aggressively. LangGraph docs say in-memory savers die on restart and checkpoint tables can grow unbounded without retention policies.[2]
- For CyClaw specifically, SQLite is the sane local default and Postgres is only worth it if you truly need concurrent multi-session orchestration. Your current offline-first, loopback-only posture suggests local file-backed persistence first, not premature infra cosplay.[3][4][2]

## Subagent memory policy

- Give each subagent a private working set plus a read-only slice of parent context; promotion to shared memory should require an explicit `promote_memory()` step with schema + trust level. That prevents context rot and aligns with Deep Agents’ design goal of isolated subagent context and offloaded intermediate results.[1]
- Recommended promotion classes for CyClaw: `user_fact`, `project_convention`, `retrieval_hint`, `security_finding`, `tool_capability`, `rejected_hypothesis`. Only the first four should persist by default; rejected hypotheses should expire fast.[4][2]

## Dynamic deployment logic

- Only use dynamic subagents when the job is naturally fan-out, conditional, or multi-phase. LangChain’s own guidance is blunt: dynamic subagents are for deterministic coverage at scale and reliable orchestration patterns like fan-out+synthesis, classify-and-act, adversarial verification, and loop-until-done. 
- For CyClaw, that means: use them for corpus-wide repo review, per-file GitHub analysis, multi-document synthesis, verifier passes on security findings, and exhaustive sweeps. Do **not** spawn them for ordinary single-query RAG or simple tool calls; that just burns tokens and complicates state.[5]

## CyClaw-specific policy

- Best architecture fit: `controller -> policy gate -> planner -> subagent dispatcher -> result verifier -> memory promoter`. That preserves the CyClaw principle you already built elsewhere: enforcement in topology/policy, not in hopeful prompt wording.[6][7][8]
- Brutally honest recommendation: make dynamic subagent deployment conditional on hard gates — max fan-out, allowed subagent types, allowed tools, retrieval-first requirement, per-run budget, and approval for mutating tools. Otherwise you will recreate the same class of “agent had too much discretion” problem CyClaw was designed to avoid.[8][9]

A good starter policy table for CyClaw:

| Trigger | Deploy? | Memory access | Notes |
|---|---|---|---|
| Single local RAG question | No. [4] | Thread checkpoint only. [2] | Keep cheap and deterministic. [3] |
| Repo-wide file review | Yes.  | Read-only parent context, per-subagent scratchpad. [1] | Fan-out and synthesize.  |
| Security finding verification | Yes.  | No shared writes until verifier consensus.  | Adversarial verification pattern.  |
| Tool mutation or external action | Maybe, gated. [9][8] | No autonomous promotion. [8] | Require approval. [9] |
| Learning stable project conventions | No immediate fan-out. [4] | Promote to store after repeated confirmation. [2] | Prevent garbage memory. [2] |

My actual recommendation for CyClaw: keep subagent state **disposable**, shared memory **sparse**, and deployment logic **rule-based first, model-suggested second**. That is the safest way to get the cheap open-weight harness benefits you want without eroding the exact security and determinism that make CyClaw valuable.[3][4]

Sources
[1] LangChain Deep Agents: Build Agents for Complex, Multi-Step Tasks https://www.langchain.com/deep-agents
[2] Troubleshooting common issues https://docs.langchain.com/oss/python/langgraph/persistence
[3] Also describe how much of CyClaws (using GitHub connector) security strength lies in its smaller attack surface with it being mostly offline using cached open source models? I already know there’s at least some truth to that being why due to multiple...

...st in agentic coding evals maybe not real world coding but worth checking out bc in my mind the only reason we need online always (or mostly) ai for frontier is because it’s way better at certain things and can’t be cached via things like Huggingface https://www.perplexity.ai/search/de8d5905-3256-4cb9-8c32-a7e5e40a8575
[4] Review GitHub connector cgfixit CyClaw repository and provide thorough analysis and feedback https://www.perplexity.ai/search/8d25cace-b0c2-4e75-b91b-ff6866d267a9
[5] How about CyClaw? Thoroughly analyze and provide a score https://www.perplexity.ai/search/2c6f2bd6-4439-4883-9203-21e4d637de9c
[6] ***verbose**

Generate all aspects/modules of code required for this project described in the diagram (assume I will use .md files for my RAG corpus https://www.perplexity.ai/search/67ab2d78-5377-4b4a-8986-d6d773fdafbf
[7] ***brainstorm***
***velocity***

                     [ 1. User Query ]
                             |
                             v
             +-------------------------------+
             |    MCP Client Application     |
             |      (P...

...ring Only)   |      Llama 3.2 3B Instruct
             +-------------------------------+
                             |
                             | 8. Process & Reason Offline
                             v
                     [ 9. Final Output ] https://www.perplexity.ai/search/52b5da83-455a-46b4-a504-24f63af66fe3
[8] Psyclaw by design wouldn’t have many of those Cve’s though until I add more tool integrations but I’ll be careful ;) https://www.perplexity.ai/search/104b9437-e65f-4cb1-99d7-833ec8f8010a
[9] Deep Agents Code - Docs by LangChain https://docs.langchain.com/oss/python/deepagents/code/overview
[10] The general-purpose subagent https://docs.langchain.com/oss/python/deepagents/subagents
[11] Deep Agents overview - Docs by LangChain https://docs.langchain.com/oss/python/deepagents/overview
[12] subagents | deepagents - LangChain Reference https://reference.langchain.com/python/deepagents/subagents
[13] State Persistence and Memory Management in LangGraph - Part 8/14 https://www.youtube.com/watch?v=83_s2lfZm-E
[14] State Persistence and Memory Management in LangGraph - Part 11/14 https://www.youtube.com/watch?v=dS554YJRFIM
[15] Context Management for Deep Agents - LangChain https://www.langchain.com/blog/context-management-for-deepagents
[16] Best practice for managing LangGraph Postgres checkpoints for short-term memory in production? https://www.reddit.com/r/LangChain/comments/1qna46j/best_practice_for_managing_langgraph_postgres/
[17] Persistence https://langchain-ai.github.io/langgraphjs/how-tos/persistence/
[18] Subagents - Docs by LangChain https://docs.langchain.com/oss/python/langchain/multi-agent/subagents
[19] SubAgent | deepagents - LangChain Reference Docs https://reference.langchain.com/javascript/deepagents/middleware/SubAgent
[20] Introducing Dynamic Subagents in Deep Agents - LangChain https://www.langchain.com/blog/introducing-dynamic-subagents-in-deep-agents
[21] Deep Agents Middleware - Docs by LangChain https://langchain-5e9cc07a.mintlify.app/oss/python/deepagents/middleware






--


## Lang Harness + Deep Agents through pt 5 but carefully consider and reply to these in repo file or in cc:

1.	
6 of 12 deepagent_github/ files are fully unreferenced — core.py, runners.py, memory.py, skills.py, governance.py, config.py are on no import path, in no test, and not in __all__. draft_plan() ignores its input and returns hardcoded constants.
* by design with out of hand but specifics should be added plus #2 leads into the same type of q

2.	
builder.py:94-99 reports created=True with tool_names populated, but calls the creator with tools=[] and bare subagent name strings — against real deepagents this builds a toothless agent while advertising success. Fixing it means deciding the seam’s intended semantics, which I didn’t want to guess.
# good instinct; think about and research this after understanding langchain deep agents better - also why did grok call the "better harness" a doorbell hahahaha that sounds more like a hook or pattern not a harness... anyways the planning guide was way more but the question is more of the deep agent can that still be cyclaw mcp Qwen cached? With basically similar security design I did with cyclaw but with a harness and agentic coding - use the agentic github integration and the python coding agent w ponytail and karpathy skills and rrk (or ptk or caveman if research shows benefit with ponytail and custom token efficient skill or toll or something - sidenote find out if there's a somewhat generic reference to these folders besides obvious like skills or synonyms of hooks or tools etc, does each thing have their own or is there a standard?)
I'm talking to myself but that q is the most specific to what I understand the deep agent purpose to be which I filled every detail in the planning guide except that part ha 

3.	
Speculative API surface: governance_gate_strings/GovernanceFinding exported with zero consumers; HarnessRunner Protocol with only a mock implementer; 9 of 11 SurfaceType members unused.
Ha I read that as two things but either way research more the next few days but this planning doc and cleanup is the only changes left really other than deep agents due to it being way cheaper if I can out a Chinese open weight model in a harness and use the agentic github thing. Might be worth making a new web interface for that hahahaha there I go again
