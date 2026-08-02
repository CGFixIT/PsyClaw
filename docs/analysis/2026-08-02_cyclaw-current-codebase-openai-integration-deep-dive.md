# CyClaw Current-Codebase and OpenAI Integration Deep Dive

**Research date:** 2026-08-02
**Timed session:** 09:29:37 EDT to 10:30:05 EDT (60 minutes 27 seconds)
**Final live `origin/main` snapshot:** `8b198224f49a880d47a057ca4a5ac0dba11b7939`
**Repository:** `CGFixIT/CyClaw`
**Package version:** `1.9.0`
**Scope:** read-only code, history, test, CI, security-boundary, macOS harness, browser-console, agentic, guardrail, and current official OpenAI API research
**Implementation status:** research only; no product source, branch, commit, PR, provider key, or remote state was changed

## Executive verdict

CyClaw is no longer just an offline RAG server with optional external fallback. At the researched snapshot it has three materially different model-use planes:

1. The `:8787` RAG gateway and `static/terminal.html`: a stateless, retrieval-first query console with explicit Grok or Claude escalation.
2. The `:8790` coding/chat harness and `static/harness.html`: persistent local chat plus a governed GitHub coding workflow launched through a subprocess boundary.
3. The out-of-band agentic planner/coder: clone, plan, whole-file patch, scan, verify, human decision, local commit, then separately authorized push and draft-PR publication.

That separation is the correct architecture. It should be preserved when OpenAI is added. “Add ChatGPT” must not become one generic provider switch because each plane sends different data, has different consent semantics, and carries different risks.

The recommended OpenAI scope is therefore two independent changes:

- Add OpenAI as an explicit optional online answer provider in the RAG graph, alongside Grok and Claude.
- Add OpenAI initially as a planning-only provider for `real-repo-run-plan`, using Terra by default, Luna for cheap/simple work, and Sol only for explicitly selected hard work.

Do not initially add OpenAI to general harness cloud chat. That path would export CyClaw’s composed skill prompt, optional soul, and recent session history. Do not initially let OpenAI perform coding iterations either; paying once for a reviewed cloud plan and using the local model to implement it gives most of the value with less egress, cost, and attack surface.

The biggest current non-OpenAI issue discovered in this session is a verified Windows integrity gap: protected-path matching is case-sensitive and does not normalize Windows filename aliases. A model proposal for `Tests/...` bypasses the `tests/` protection but resolves into the same directory on Windows; `pyproject.toml.` similarly resolves to `pyproject.toml`. This should be fixed before relying on the coding harness to execute model-authored verification on Windows.

## Research method and evidence boundary

The dirty/divergent working checkout was preserved. Research used an isolated clean clone at `C:\tmp\cyclaw-deep-dive-20260802`, fast-forwarded to the live `origin/main`. The baseline advanced during the session from PR #747 to PR #748; the later SHA became the working source of truth.

The review covered:

- first-parent history and the recent PR sequence;
- `gate.py`, `gate_ops.py`, `graph.py`, `llm/`, `schemas/`, and `static/terminal.html`;
- `harness/`, `static/harness.html`, `macos/`, `powershell/`, and their tests;
- `agentic/cli.py`, `real_repo_loop.py`, workspace tools, executor, run store, handoff, provider adapters, configuration, docs, and tests;
- `guardrails/`, `utils/guardrail_bridge.py`, graph wiring, metrics, configuration, docs, and tests;
- `README.md`, `INVARIANTS.md`, `CLAUDE.md`, `AGENTS.md`, `remaining_work.md`, threat model, setup/harness docs, and CI workflows;
- current official OpenAI model, pricing, Responses API, data-retention, authentication, rate-limit, and billing documentation;
- the pinned LangChain `ChatOpenAI` integration seam already present in the agentic optional dependency set.

Claims below are labeled as current behavior, verified risk, or recommendation. Proposed OpenAI behavior does not exist in the researched code.

## Current architecture

```text
Browser: static/terminal.html                     Browser: static/harness.html
              |                                                  |
              v                                                  v
      gate.py on 127.0.0.1:8787                      harness.server on 127.0.0.1:8790
              |                                       |                       |
        sanitizer + auth                        persistent local chat     governed HTTP routes
              |                                       |                       |
              v                                       v                       v
      9-node LangGraph policy                    loopback Ollama       utils.ops_runner subprocess
              |                                                               |
       retrieval is always first                                              v
              |                                                        agentic.cli
     +--------+---------+                                                      |
     |                  |                                                      v
 local/offline     explicit external                              GitHub context + jailed clone
 Ollama            Grok or Claude                                             |
     |                  |                                                      v
     +--------+---------+                                            plan/patch/scan/verify
              |                                                               |
          audit logger                                               human decision before commit

Optional guardrails are injected through utils.guardrail_bridge; graph.py does not import
guardrails directly. The retrieval-only MCP server remains separate and has no model sampling.
```

### Important naming distinction

The **Agentic Console** inside `static/terminal.html` is not the new GitHub coding agent. It drives the older `/ops/agentic` context and governed skill-registry operations.

The GitHub coding agent is exposed through `static/harness.html` and `/api/agent/*`, or more completely through `python -m agentic.cli`. This distinction matters because the browser surfaces have different capabilities and security controls.

## Recent-main chronology

The recent merges form a coherent hardening sequence rather than isolated feature additions:

| Merge | Main effect |
|---|---|
| #734 `e6e5c06` | macOS/Linux harness port, platform launch/install work, and cross-platform embedding/index consistency fixes |
| #735 `d6c1f3d` | planner/operations timeouts, code-shape scanning, safer cloud failure handling |
| #736 `dc8c6dd` | two-stage plan generation, human file handoff, plan hashing |
| #737 `2f4a3be` | agentic documentation/dependency verification |
| #738 `8cc55c9` | dependency pin maintenance |
| #739 `8930dc6` | blind whole-file overwrite prevention when read context was truncated/omitted |
| #740 `9282359` | real socket, real `gh`, and real clone smoke coverage |
| #741 `661ad10` | macOS setup/docs and local-model migration to `qwen3.6:27b` |
| #742 `aa92365` | correction of stale `remaining_work.md` output-rail claims |
| #743 `2d02996` | routes offline best-effort through the input rail; optional-extra dependency scans and tooling updates |
| direct `bf0e4d0` | current documentation screenshots |
| #746 `472745a` | macOS-first documentation and environment-variable corrections |
| #747 `39f6158` | provider API audit fixes: usable-provider UI, credential-safe health errors, `Retry-After`, Claude response/temperature handling |
| #748 `8b19822` | scans `--instruction` at the agentic CLI chokepoint before context fetch, clone, or model use |

PRs #744 and #745 were closed rather than merged and are not part of this snapshot.

## RAG gateway and latest `terminal.html`

### What it is

`static/terminal.html` is a single-page operator console for the gateway. It looks conversational because it appends queries and answers to a transcript-like DOM, but `/query` is stateless: the server receives one query, optional confirmation, and optional provider. No prior turns are included in the next model prompt.

The page also contains five separate operator panels:

- Soul
- Sync
- Agentic skill/context operations
- Filesystem connector
- Read-only SQL connector

Those panels call explicit `/soul/*` or `/ops/*` routes. They do not turn the query endpoint into an autonomous agent.

### Current query flow

1. The UI submits `{query}` to `/query`.
2. The gateway enforces request size, authentication/rate-limit rules, sanitization, and prompt-injection filtering.
3. The graph always retrieves first and compares the top RRF score to the configured threshold.
4. A high-score query passes through the optional input-rail node and then the local model.
5. A low-score query pauses and returns `needs_confirm`, a message, and only the providers that are actually routable.
6. The browser keeps the pending query only in JavaScript. A provider button resubmits it with explicit consent and provider selection; reload loses the pending request.
7. Decline, unavailable provider, or offline mode routes through the input rail to local offline best effort.
8. Every graph terminal path converges on the audit node.

Recent UI work correctly stopped displaying dead Grok/Claude buttons. `gate.py` derives availability from the constructed client and a non-blank server-side key; `QueryResponse.available_providers` drives the buttons. Unknown provider IDs are not rendered.

There is still one hard-coded two-provider seam: `handleConfirm()` maps anything other than `claude` to the label `Grok`. Adding OpenAI requires replacing that ternary and extending the server-derived button specification.

### Current graph

The graph has nine explicit nodes:

1. `retrieve`
2. `route_by_score`
3. `guardrail_input`
4. `local_llm`
5. `user_gate`
6. `grok_fallback`
7. `claude_fallback`
8. `offline_best_effort`
9. `audit_logger`

The invariant checker locks the exact node set, conditional targets, provider construction gates, and audit reachability. An OpenAI query provider is therefore intentionally a topology change, not a prompt or registry change.

### External privacy behavior

For Grok and Claude today:

- the query is always sent after explicit consent;
- retrieval context is not sent unless its provider-specific config flag is enabled;
- the soul/personality prompt is never sent off-box;
- prompt characters are capped per provider;
- truncation and provider attempt are audited;
- there is no automatic fallback from one paid provider to another.

That behavior is the right template for OpenAI.

## macOS/Linux coding harness

### What “macOS CyClaw” means

The runtime is not a separate Mac implementation. `harness/` is pure Python and uses the same request, authentication, session, subprocess, and agentic code on macOS, Windows, and Linux. Platform-specific code is concentrated in installation/launch glue and the filesystem connector's distinct POSIX versus Windows containment implementations.

The macOS flow is:

1. `macos/install-cyclaw.sh` prepares `~/.CyClaw`, the repo, virtual environment, shims, and shell profile markers.
2. It supports zsh and bash, assumes BSD userland, and requires no Homebrew/GNU tools.
3. On Darwin it installs plain `torch==2.13.0`; the documented floor is Apple Silicon and macOS 14 because the pin has no Intel/macOS-13 wheel.
4. `macos/invoke-cyclaw.sh` exports `CYCLAW_HOME`/`CYCLAW_REPO`, starts `python -m harness.server`, and opens the browser.
5. The harness serves on `127.0.0.1:8790`; the RAG gateway remains separate on `:8787`.

POSIX scoped-root containment uses `openat`/`O_NOFOLLOW`, so the Mac filesystem path is not a weaker copy of the Windows implementation.

### Cross-platform retrieval consistency

Recent macOS work also changed the core retrieval layer. `sentence-transformers` previously auto-selected MPS on Apple Silicon while Linux/Windows used CPU, and the resulting numeric drift changed retrieval rankings materially. Current main pins the small embedding model to `EMBED_DEVICE = "cpu"`; this does **not** disable Metal/GPU acceleration for the separate Ollama LLM.

Fresh Chroma indexes now record a `{model, dim, device}` fingerprint. A present mismatch fails closed through the existing index-not-found/503 path; a pre-fingerprint index is fatal when MPS is available and warning-only elsewhere. Existing Mac operators may therefore need to rebuild with `python -m retrieval.indexer` after upgrading. The pgvector backend currently has no analogous stored fingerprint, so this protection is Chroma-specific.

### Harness chat

Harness chat is genuinely multi-turn and persistent:

- sessions are human-readable JSON, locked, path-validated, capped, and atomically replaced;
- the last 20 stored user/assistant turns are sent on each request;
- the system prompt composes the Ponytail and Karpathy skills and, when enabled, up to 8,000 characters of soul text;
- the model client only accepts loopback OpenAI-compatible endpoints;
- changing the model name does not change provider or endpoint.

Typing a `gpt-*` model name in the UI therefore does not call OpenAI. It asks the local Ollama-compatible endpoint for that model name and will normally fail if Ollama does not have it.

### Harness security posture

Guarded routes apply:

1. per-IP rate limiting;
2. `Sec-Fetch-Site` and Origin checks;
3. fail-closed constant-time `CYCLAW_API_KEY` authentication;
4. a per-process CSRF token.

The browser keeps the API key in the DOM only, not cookies or local storage. OpenAPI docs are disabled, output is escaped, and agent check profiles resolve to fixed argv lists.

### Harness findings

#### High: loopback enforcement can be bypassed by a direct Uvicorn launch

`python -m harness.server` refuses a non-loopback host. `uvicorn harness.server:app --host 0.0.0.0` bypasses that entry-point check. `TrustedHostMiddleware` validates the supplied Host header, not the peer address; a remote client can send `Host: localhost`. Guarded routes retain API-key and CSRF protection, but open metadata routes can become remotely readable. The README warns that direct Uvicorn can open a public socket, so this is an explicit operational hazard rather than a hidden feature.

#### High: synchronous coding runs can lose a manageable handle on timeout

`POST /api/agent/run` waits for the CLI subprocess, with a derived budget capped at 3,600 seconds. The run ID is created inside that subprocess after context/clone work and is returned only when stdout completes. A parent timeout can leave a clone or `running` record without a browser-visible run ID. Dynamic timeout sizing reduced the frequency; it did not create asynchronous job control or orphan recovery.

#### Medium: open routes expose conversation excerpts and local paths

`GET /api/sessions` is deliberately open and includes the last 80 characters of conversation. Separate open routes expose local paths: `/api/status` returns home/repo/session roots, and `/api/harness/runs` returns optimizer run directories. That is more sensitive than ordinary health metadata and becomes material if bind containment is bypassed.

#### Medium: no gateway-style pre-buffer body cap

The gateway rejects oversized declared bodies before parsing. The harness relies on Pydantic field limits after Starlette has buffered the request.

#### Medium: installer/readiness sharp edges

The default installer can recursively replace an invalid default repo directory, and the launcher opens the browser after a fixed two seconds instead of a readiness probe. The macOS CI installer smoke is non-blocking, skips dependencies, and does not launch a real server/browser.

#### Medium: browser contract coverage is static

Tests inspect HTML/JavaScript strings, but there is no real browser end-to-end flow through authentication, chat, subprocess, agentic run, and decision.

## GitHub agentic coding agent

### Live path versus retired path

The live coding path is `agentic/real_repo_loop.py`. The older DeepAgents graph/builder remains as a retired compatibility/probe surface. Future work should extend the real-repo loop, not revive the retired path.

### End-to-end flow

```text
config and CLI/HTTP gates
  -> scan operator instruction (#748)
  -> fetch GitHub PR/issue/repo context
  -> scan third-party context
  -> optional approved plan-file load + scan
  -> real `gh repo clone` into jailed workspace
  -> save running record
  -> proposer emits whole-file replacements
  -> canonicalize paths and reject duplicates
  -> scan every proposed body for injection and suspicious code shape
  -> protected-path and total-byte gates
  -> write eligible files
  -> run fixed/operator-selected verification argv
  -> accept, retry with feedback, or exhaust
  -> pending_decision
  -> human reviews diff
  -> approve creates local commit; reject discards
  -> separate push decision
  -> separate draft-PR publication decision
```

### Strong controls

- Every write/commit/push gate ships closed.
- Context, approved plan, and verification feedback are fenced as untrusted data.
- PR #748 scans the operator’s own pasted instruction before any fetch, clone, or model call.
- Candidate file content is fully scanned before any file in that iteration is written.
- Protected paths and a 100,000-byte per-iteration write budget reduce reward hacking.
- Existing files must be explicitly declared/read; a file omitted or truncated by the read budget cannot be blindly replaced.
- Verification uses list-form argv, no shell, a scrubbed environment, fixed cwd, network-hostile environment variables, and timeouts.
- Success stops at `pending_decision`; commit, push, and PR creation are separate actions.
- Push excludes token environment variables and relies on a host credential helper.
- Draft-PR execution remains hard-disabled by a source constant in the shipped tree.

### Six-gate cloud chain

Cloud planning/coding is independent of `app.mode`. `app.mode: offline` is not a global egress kill switch for agentic code.

The chain is:

1. `agentic.enabled`
2. `agentic.deepagent_github.enabled`
3. `allow_cloud_providers`
4. `providers.<name>.enabled`
5. provider key present in a server-side environment variable
6. per-run `--confirm-online`

Current provider IDs are `grok` and `claude`. Every outbound user prompt is injection-scanned, redacted, capped, hashed, and audited. Cloud error messages log exception type rather than provider text that could echo secrets or prompt content.

### Two-stage plan handoff

`real-repo-run-plan` makes one model call and produces no clone, patch, commit, push, or PR. A human can read/edit the text and pass it to `real-repo-run --plan-file`. The consumed plan hash is recorded.

This is the best OpenAI seam because a capable cloud model can be paid once for judgment while a local model performs iterative coding. Current provenance is incomplete: the run stores the plan hash, but not the planning provider/model, and the hash proves bytes—not that a human reviewed them.

### Browser/CLI capability gap

The CLI exposes plan generation, approved plan files, PR/issue selectors, read files, provider choice, online confirmation, iteration count, and arbitrary operator check manifests.

The browser harness exposes none of the cloud/plan/read-file controls. Its run request uses a local proposer, fixed check-profile names, and no declared existing files. Because the loop’s safe whole-file edit rule requires existing files to be declared/read, meaningful existing-file edits through the current browser can be impractical. Cloud planning is currently CLI-only.

### Verified high: Windows protected-path alias bypass

The scope gate compares canonical-looking strings case-sensitively. Windows resolves paths case-insensitively and strips some trailing-dot aliases.

This session directly reproduced on the clean clone:

```text
canonical_repo_path("Tests/test_probe.py") -> "Tests/test_probe.py"
_matches_protected_path(..., ("tests/",)) -> False
Path("Tests").resolve() == Path("tests").resolve() -> True

canonical_repo_path("pyproject.toml.") -> "pyproject.toml."
protected match -> False
Path("pyproject.toml.").exists() -> True
Path("pyproject.toml.").resolve() -> ...\pyproject.toml
```

A model can therefore address verifier-controlled files through an alias before verification runs. This is a production integrity defect on Windows. The case-only form is also expected to alias on the default case-insensitive APFS used by macOS, although no Mac host was available to reproduce it here; the trailing-dot form is Windows-specific. Protection must compare a normalized destination identity using platform-aware rules, and regression tests must prove the old implementation fails on the affected platforms.

### High: verifier containment is not a sandbox

Model-authored code is executed before human diff review. The executor scrubs environment variables and discourages normal proxy/package egress, but any executed test can open raw sockets or invoke binaries available on `PATH`. The source explicitly describes this as best-effort software containment.

On macOS, use an OS-level containment strategy if untrusted repositories or hostile issue/PR content enter scope. At minimum, treat the current workflow as suitable only for a trusted operator and a trusted repository, not hostile multi-tenant code execution.

### Other run-integrity risks

- Run-state files are atomic but not transactionally locked across processes; concurrent decide/push/publish commands can race.
- Audit events do not consistently carry `run_id`, weakening correlation across concurrent work.
- A `save_run` failure after clone creation can leak the clone.
- Iterations are cumulative, not transactional; earlier successful writes remain for later iterations and can enter a later approved commit.
- Direct CLI check timeout values are not comprehensively validated as positive integers.
- Local proposer audit labels can say `ollama` even when the configured local provider is another OpenAI-compatible server.

## Guardrails current state

### What is wired

The gateway sanitizer always runs. If `guardrails.enabled` is true, `utils.guardrail_bridge.build_input_guard()` lazily imports the optional package and injects a synchronous, model-free input checker into the graph.

PR #743 fixed a real coverage gap: both high-score local answers and low-score offline/declined answers now pass through `guardrail_input`. Confirmed external provider legs deliberately do not, because the user already selected external escalation. All paths still converge on audit.

The graph-side guard fails open if the optional guard raises. This is defensible for defense in depth, but it is not an enforcement boundary and should not be described as one.

### What is not wired

There is no production `guardrail_output` node. The NeMo `safe_generate()` flow performs its own generation and grounding checks out of band; inserting it after an existing graph generation would double-generate. A real output rail would require a new synchronous non-generating checker, bridge, node, router, path-specific design, and audit updates.

Applying a grounding rail to fallback paths is especially difficult: those paths are reached because retrieval is weak, so a context-grounding threshold would reject most fallback answers by construction.

### Guardrail drift and risks

- Several module/config comments still call the package a future, completely out-of-band skeleton, although the graph reaches it indirectly through the bridge.
- Some prose says five invariants; the current checker enforces six, including module isolation.
- `input_rails`, `output_rails`, and `topical_rails` config lists are displayed but do not dynamically select the implemented heuristic behavior.
- The NeMo singleton is process-global and can retain stale configuration across reload assumptions.
- Its optional dependency chain may fetch embedding assets remotely on first use, which conflicts with offline-first expectations.
- Guardrail metrics use a separate hash-only JSONL stream and do not share the main audit summarizer.

Do not use the NeMo `engine: openai` label as the place to add OpenAI cloud. In that block, `openai` means Ollama’s local OpenAI-compatible protocol, not OpenAI-hosted egress.

## OpenAI terminology, models, and cost

### “ChatGPT” versus the API

CyClaw would integrate the **OpenAI API**, not the ChatGPT consumer application. ChatGPT subscriptions and API billing are separate. The internal provider ID should be `openai`, the UI label should be “OpenAI,” and the key should remain server-side in `OPENAI_API_KEY`.

Never put that key in `terminal.html`, `harness.html`, browser storage, YAML, session JSON, run records, or logs. The official API reference says keys belong in a server environment variable or key-management service and use Bearer authentication.

### Current GPT-5.6 family

Official documentation checked during this session reports the following standard short-context prices per one million tokens:

| Model | Intended use | Input | Cached input | Cache write | Output | Recommended CyClaw role |
|---|---|---:|---:|---:|---:|---|
| `gpt-5.6-luna` | cost-sensitive/high-volume | $0.20 | $0.02 | $0.25 | $1.20 | cheap query fallback, task triage, simple plan |
| `gpt-5.6-terra` | intelligence/cost balance | $2.00 | $0.20 | $2.50 | $12.00 | default reviewed coding plan |
| `gpt-5.6-sol` | frontier complex professional/coding | $5.00 | $0.50 | $6.25 | $30.00 | explicit hard security/architecture plan |

All three are documented with a 1,050,000-token context window, 128,000 max output, and a 2026-02-16 cutoff. Requests over 272,000 input tokens are priced at 2x input and 1.5x output for the full request.

Approximate standard-mode examples, excluding cache effects, extra reasoning tokens, and the documented 10% regional-processing uplift where applicable:

| Shape | Luna | Terra | Sol |
|---|---:|---:|---:|
| 2,000 input + 1,000 output | $0.0016 | $0.016 | $0.040 |
| 50,000 input + 2,000 output | $0.0124 | $0.124 | $0.310 |

The agentic handoff cap is 200,000 **characters**, not tokens. Actual cost must be measured from provider usage fields rather than estimated only from character counts.

### Model-selection policy

Do not silently route between Luna, Terra, and Sol. Silent escalation makes cost and latency unpredictable and weakens consent.

Use one configured model per plane:

- RAG external answer: start with Luna at low reasoning for cheap ordinary fallback; switch the configured model to Terra only if representative evaluation proves a quality need.
- Coding plan: Terra at medium reasoning is the balanced default.
- Hard security/architecture plan: Sol at high or xhigh only through an explicit operator config/selection and cost warning.
- `max`/pro mode: do not ship initially. Add only if measured plan-quality gains justify latency and spend.

GPT-5.6 supports `none`, `low`, `medium`, `high`, `xhigh`, and `max`; omitted effort defaults to medium. CyClaw should set it explicitly.

## Three possible OpenAI integrations

| Plane | Current data sent | Current consent | OpenAI recommendation |
|---|---|---|---|
| RAG `/query` | query; optional capped retrieval context; never soul | hybrid mode + provider enabled/key + per-query provider button | implement as explicit third graph provider |
| agentic plan/coder | instruction, approved plan, GitHub context, declared files, verification feedback; capped/redacted/audited | six-gate chain + `--confirm-online` | first add plan-only; evaluate coding parity later |
| harness general chat | composed skills, optional soul, last 20 turns, new message | currently local-only | defer; requires a separate disclosure and per-call cloud-chat design |

These should not share a single `online_provider` abstraction across packages. Reuse discipline and tests, not imports that violate I6.

## Recommended OpenAI RAG-provider design

### Minimal architecture-consistent change

1. Add disabled `models.openai` config with a fixed official endpoint, configured model, output cap, reasoning effort, timeout, and retry budget.
2. Add `send_local_context_to_openai: false` and `openai_max_prompt_chars` beside the existing provider-specific privacy controls.
3. Add an `OpenAIServiceError` and a small `OpenAIClient` in `llm/client.py`.
4. Construct that client only under `app.mode == "hybrid"` and `models.openai.enabled`; availability is non-blank `OPENAI_API_KEY` only.
5. Add an explicit `openai_fallback` graph node through the existing generic external-node helper.
6. Extend the user-gate router with exact confirmation/provider/client/key checks.
7. Extend lifecycle close, health/redaction, response schema, UI button/label, metrics, audit provider sets, due-diligence tests, invariant checker, graph docs, README, and threat model.

The graph would become ten nodes and audit reachability would cover nine upstream nodes.

Do not introduce a dynamic provider registry in this change. Explicit nodes are intentional because topology is policy and the invariant checker audits concrete provider legs.

### API shape

The official recommendation for new projects is the Responses API. A minimal stateless request should look conceptually like:

```json
{
  "model": "gpt-5.6-luna",
  "input": "<the already assembled CyClaw external prompt>",
  "max_output_tokens": 2048,
  "reasoning": {
    "effort": "low",
    "context": "current_turn"
  },
  "store": false
}
```

Use `POST https://api.openai.com/v1/responses` with server-side Bearer authentication. In the first version:

- no web search;
- no file search;
- no tools/functions;
- no remote MCP;
- no shell/code interpreter;
- no background mode;
- no Conversations API;
- no `previous_response_id`;
- no provider-side state or automatic cross-provider retry.

`llm/client.py::_extract_content` cannot parse this response because it expects Chat Completions’ `choices[0].message.content`. Add a dedicated typed Responses parser and test completed text, multiple output items, refusal, incomplete/max-output, empty/malformed JSON, and safe errors.

### Retry correction

The shared helper now honors numeric `Retry-After` and caps each sleep, which was a good #747 fix. It still has no jitter or total retry-duration cap and treats every HTTP 429 as retryable.

OpenAI’s current guidance says a direct HTTP client should:

- honor valid `Retry-After` as a minimum and add jitter;
- use exponential backoff with jitter when missing/invalid;
- cap both attempt count and total retry time;
- not retry quota, billing, or other action-required errors.

Do not blindly reuse the current 429 classifier for OpenAI. Either add a narrow provider-specific error-code classifier or use the official SDK and avoid stacking another retry loop on its built-in retries. For the core plane, direct `httpx` remains the smaller dependency choice if its Responses parser and retry classification are fully tested.

### Data and retention

OpenAI states API data is not used for training unless the customer explicitly opts in. Abuse-monitoring logs are generally retained up to 30 days. Responses are stored as application state by default for at least 30 days unless `store: false`; ZDR/MAM require organization approval and have feature limitations.

CyClaw should always send `store: false`, stay stateless, and avoid Conversations/previous-response chaining. `previous_response_id` would not avoid rebilling the prior input tokens anyway.

## Recommended OpenAI planning-provider design

### Smallest safe first tranche

Add OpenAI only to `real-repo-run-plan --provider openai`.

This gives the requested optional cloud planning without automatically letting OpenAI generate and execute patches across multiple iterations. The reviewed plan can be handed to the existing local coding loop.

Required changes:

1. Add `openai: OPENAI_API_KEY` to `CLOUD_KEY_ENVS` and a disabled `providers.openai` config entry, probably `gpt-5.6-terra`.
2. Replace `build_chat_model()`’s implicit “anything not Grok is Claude” fallthrough with explicit Grok, Claude, OpenAI, and unknown-provider-refusal branches. Current config/CLI validation rejects `openai`; if the valid-provider set were expanded without fixing dispatch, OpenAI settings and credentials would fall through into `ChatAnthropic`.
3. Reuse the already-pinned `langchain-openai==1.3.3`; no new dependency should be required for the agentic extra.
4. Construct `ChatOpenAI` with the official API base URL pinned explicitly—not an operator-configurable or ambient base URL—with the planner timeout, `use_responses_api=True`, explicit reasoning, explicit output cap, and `store:false` verified at the pinned package’s wire format. LangChain honors `OPENAI_API_BASE`/`OPENAI_BASE_URL` when no explicit base URL is supplied; the cloud path must not let those variables redirect the key or prompt.
5. Accept `openai` only on the planning parser in the initial patch. Add tests proving it is rejected on `real-repo-run` and the existing legacy/dormant `deepagent-plan` surface.
6. Preserve the existing six gates and `sanitize_handoff` before egress.
7. Extend secret-redaction tests for current OpenAI key formats. The current regex assumptions need verification before keys exist in this process.
8. Add real-class/fake-transport tests for request shape, output content blocks, refusal/incomplete responses, timeout propagation, and error redaction.
9. Record planning provider/model alongside the plan hash if reliable provenance is a requirement.

LangChain documents `use_responses_api=True` support from `langchain-openai>=0.3.9`; CyClaw pins 1.3.3. That version is new enough for Responses, but the current `reasoning_effort` convenience parameter is documented as requiring 1.4.1. At the existing pin, use and wire-test the provider-native `reasoning` mapping rather than assuming the shortcut works; exact `store:false`, output-token, and response-block serialization must also be locked by a no-network test before merge.

### If full coding-provider parity is later justified

After plan-only evaluation, OpenAI can be added to `real-repo-run --provider` behind the same six gates. That is a separate scope because it sends every iteration, file context, and verification feedback to OpenAI and can multiply spend. Require per-run cost estimates/limits and provider/model audit correlation before enabling it.

## General harness cloud chat: defer

If a later requirement genuinely needs OpenAI/Grok/Claude general chat in `static/harness.html`:

- add a dedicated cloud-chat adapter; never relax the existing loopback-only `HarnessChatClient`;
- keep credentials server-side;
- expose server-derived provider/model/capability metadata;
- require explicit disclosure that skills, optional soul, and recent history will leave the machine;
- require per-call confirmation or an unmistakable cloud session mode;
- audit provider, model, character/token scope, and whether soul/history were included without logging content;
- add auth/Origin/CSRF and real browser end-to-end tests.

This should not be hidden behind the model-name field. A model name is not a provider authorization.

## Prioritized current findings

| Priority | Finding | Status |
|---|---|---|
| High | Windows case/trailing-dot protected-path aliases can reach verifier-controlled files | verified during this session |
| High | verification executes model-authored code before review without an OS sandbox | documented and verified by code |
| High | direct Uvicorn launch can bypass the harness entry-point bind refusal | verified by code/docs |
| High | synchronous browser runs can time out without returning a manageable run ID | verified by lifecycle tracing |
| Medium | open harness routes expose conversation excerpts and local paths | verified |
| Medium | browser cannot use plan/provider/read-file capabilities and is poor for existing-file edits | verified |
| Medium | plan provenance and audit/run correlation are incomplete | verified |
| Medium | run state transitions are atomic-file writes but not interprocess transactions | verified/inferred race consequence |
| Medium | harness has no gateway-style pre-buffer body cap | verified |
| Medium | core guardrails have input heuristics but no query-path output rail | verified; design remains intentionally open |
| Medium | provider clients, health reload state, and worker-thread timeouts can diverge/continue after request timeout | verified |
| Low/Medium | docs and `remaining_work.md` contain post-merge drift | verified |

## Documentation drift to clean up

- `remaining_work.md` is anchored to `9282359`; #748 closed its item about unscanned agent instructions.
- The same file still lists older dev-tool pins even though #743 updated Ruff/mypy.
- Guardrail headers/config comments still say completely out-of-band/future skeleton and five invariants.
- `.codex/README.md` references a missing `.codex/instructions.md` while also discouraging compatibility copies.
- Browser/PowerShell text still says runs take “up to 15 minutes,” while the cap is 3,600 seconds.
- Some docs describe refusal after clone although current CLI gates fail before network work.
- Executor/workspace module headers retain “no live caller” language even though `real-repo-run` is live.

These are documentation defects, not reasons to broaden a provider implementation PR. Fix them in a separate narrow docs change.

## Recommended sequence

1. Fix and regression-test Windows protected-path identity before further coding-agent enablement.
2. Decide the trusted-code boundary for verification; document that current execution is not a sandbox, or add OS containment before hostile-repo use.
3. Add OpenAI as CLI planning-only with Terra default, explicit six-gate consent, Responses API, `store:false`, and real wire-format tests.
4. Add OpenAI as a third explicit RAG graph provider with Luna default, no context/soul forwarding, no tools/state, and full invariant/audit/health/UI coverage.
5. Expose the existing two-stage plan/read-file/provider controls in the harness only after run IDs/job lifecycle are made durable.
6. Evaluate full OpenAI coding-loop parity only if plan-only results show a measured need.
7. Add general harness cloud chat only if exporting skills/soul/history is an explicitly accepted product requirement.

## Acceptance tests for future OpenAI work

### Core query plane

- missing/blank key fails closed and provider is not offered;
- exact official URL, Bearer header, body, `store:false`, model, effort, and output cap;
- completed, multiple-text, refusal, incomplete, empty, malformed, timeout, 401, 429, 5xx behavior;
- quota/billing 429 is not retried;
- valid `Retry-After`, invalid header, jitter, attempt cap, and total-duration cap;
- no key/prompt/body leakage in health, exception, answer, logs, or audit;
- confirmation absent/false/wrong provider never calls OpenAI;
- selected usable OpenAI calls only OpenAI;
- local context remains off by default and soul never leaves;
- prompt truncation and egress attempt are audited;
- every success/error path reaches audit;
- topology checker fails against the old nine-node expectation and passes the new ten-node contract;
- terminal button, label, keyboard/focus, and unknown-provider behavior.

### Agentic planning plane

- all six gates independently fail closed;
- OpenAI accepted only by `real-repo-run-plan` in phase one;
- explicit adapter dispatch and unknown provider refusal;
- pinned `ChatOpenAI` fake-transport request/response contract;
- explicit official base URL wins over ambient `OPENAI_API_BASE`/`OPENAI_BASE_URL`;
- instruction, GitHub context, and edited plan scanning still occur before egress;
- prompt cap/redaction/hash/audit contain no content or secret;
- provider/model/plan hash provenance is preserved as designed;
- Luna/Terra/Sol model selection is explicit and never silently escalates;
- no clone, write, verification, commit, push, or PR from the plan command.

## Validation performed

- Clean isolated live-main clone used; dirty root checkout preserved.
- Invariant checker on the researched live snapshot: **33 passed, 0 failed**.
- GitHub check-runs inspected for the live snapshot: 39 total, with 33 successful and six expected conditional skips; no check was failed or pending. Successful lanes included Linux, Windows, macOS, deep-agent, real-repo smoke, invariant, workflow/security, and build coverage.
- Targeted `test_agentic_plan_handoff.py` plus `test_agentic_real_repo_run_cli.py` displayed all tests at 100%, but the process did not terminate within 120 seconds. That command is **not reported as a pass**; it timed out during/after teardown and the chained invariant command did not run.
- Independent pinned agentic run: 401 tests collected, 395 passed, five Windows symlink fixtures failed before product assertions with `WinError 1314`, and one documented POSIX-only smoke skipped. The optional real-SDK cloud wire-format test could not collect locally because `langchain_xai` was absent.
- No live OpenAI, xAI, Anthropic, Ollama, NeMo, GitHub write, commit, push, or PR operation was executed.

## Residual unknowns

- No real provider call validated current account access, model availability, latency, refusal behavior, or billed usage.
- No real NeMo/Colang production flow was executed.
- No real browser automation exercised either console.
- No macOS host was available in this session; macOS conclusions come from scripts, tests, CI definitions/results, and shared pure-Python behavior.
- OpenAI API/model/pricing facts are current to the research date and must be rechecked immediately before implementation because model and billing contracts change.
- The repository is moving quickly. Every future implementation request should begin from a new clean `origin/main` snapshot and trial-merge/reconcile intervening work.

## Repository evidence index

Primary current-code references:

- `README.md`
- `INVARIANTS.md`
- `docs/THREAT_MODEL.md`
- `remaining_work.md`
- `gate.py`, `gate_ops.py`, `graph.py`
- `retrieval/embeddings.py`, `retrieval/hybrid_search.py`, `retrieval/indexer.py`, `retrieval/vector_store.py`
- `llm/client.py`, `utils/health.py`, `utils/guardrail_bridge.py`
- `schemas/api.py`, `static/terminal.html`
- `harness/server.py`, `harness/sessions.py`, `harness/prompts.py`, `harness/ollama.py`
- `static/harness.html`, `utils/ops_runner.py`
- `macos/install-cyclaw.sh`, `macos/invoke-cyclaw.sh`, `macos/uninstall-cyclaw.sh`
- `docs/HARNESS_MACOS.md`, `docs/HARNESS_POWERSHELL.md`
- `agentic/config.py`, `agentic/cli.py`, `agentic/real_repo_loop.py`, `agentic/real_repo_run_store.py`
- `agentic/deepagent_github/model_adapter.py`, `agentic/deepagent_github/chat_client.py`, `agentic/deepagent_github/handoff.py`, `agentic/deepagent_github/repo_workspace.py`
- `agentic/fsconnect/pathsafe.py`
- `agentic/executor/runner.py`, `agentic/harness_optimizer/governance.py`
- `guardrails/config.py`, `guardrails/integration.py`, `guardrails/metrics.py`, `guardrails/rails.py`
- `.claude/skills/invariant-guard/check_invariants.py`
- `.github/workflows/ci.yml`, `.github/workflows/pip-audit.yml`
- relevant `tests/test_agentic_*`, `tests/test_harness*`, `tests/test_guardrail*`, `tests/test_graph.py`, `tests/test_client.py`, `tests/test_gate.py`, `tests/test_health.py`, and `tests/test_terminal_contract.py`

## External sources checked

OpenAI primary sources:

- [GPT-5.6 model guidance](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.6)
- [GPT-5.6 Sol](https://developers.openai.com/api/docs/models/gpt-5.6-sol)
- [GPT-5.6 Terra](https://developers.openai.com/api/docs/models/gpt-5.6-terra)
- [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna)
- [API pricing](https://developers.openai.com/api/docs/pricing)
- [Responses API migration](https://developers.openai.com/api/docs/guides/migrate-to-responses)
- [Responses API conversation state and billing](https://developers.openai.com/api/docs/guides/conversation-state)
- [API data controls](https://developers.openai.com/api/docs/guides/your-data)
- [API reference and authentication](https://developers.openai.com/api/reference/overview)
- [Rate limits and retry guidance](https://developers.openai.com/api/docs/guides/rate-limits#retrying-with-exponential-backoff)
- [ChatGPT and API billing separation](https://help.openai.com/en/articles/8156019-how-can-i-move-my-chatgpt-subscription-to-the-api)

Integration source:

- [LangChain `ChatOpenAI` Responses API integration](https://docs.langchain.com/oss/python/integrations/chat/openai)

---

**Bottom line:** preserve CyClaw’s plane separation. Add OpenAI first where it buys judgment cheaply and safely—a one-call, human-reviewed coding plan—then as an explicit third RAG graph leg. Do not turn a model-name field into provider authority, do not export soul/history by accident, and do not enable more coding execution until the Windows protected-path bypass and verifier trust boundary are addressed.
