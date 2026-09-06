---
name: cyclaw-advisor
description: Give read-only, source-backed CyClaw architecture, operations, and PR advice; inspect current code before describing routing, auth, retrieval, local models, or optional layers.
metadata:
  short-description: Current CyClaw architecture and operations advice
---

# CyClaw Advisor

Use `$cyclaw-project-guidance` and the affected source files. Advice alone does
not authorize implementation or publication. If the user requests fixes, carry
out that authorized work using the relevant implementation workflow.

Record the inspected base/head SHA. Fetch current main for repository/PR advice;
for runtime troubleshooting distinguish the running checkout and operator config
from upstream defaults. List open PRs before recommending overlapping work.

## Request path

`gate.py` validates the request and constructs clients; `graph.py` retrieves,
routes, generates, and converges on audit. The current graph has 12 nodes,
including the two provider pre-action hooks. Validate the node/edge sets with
`.claude/skills/invariant-guard/check_invariants.py`; a copied count is not a
substitute for reviewing routing. Guardrail input covers local and declined
paths when enabled. External providers remain gated by hybrid+enabled client
construction and confirmation+selection+availability in the graph.

Both local answer nodes call `assert_local_destination` before generation.
Loopback is accepted; exact `models.local_llm.trusted_hosts` entries support
operator-owned container/LAN model servers. Default is `[]`. Invalid URL syntax
becomes `ENDPOINT_TRUST`. Grok/Claude use `assert_online_destination`; the local
allowlist never grants provider consent. See `docs/DOCKER.md` for the host-model
opt-in and its local-context/soul exposure.

`generate_guard` can wrap synchronous generation through the maintained
`utils/guardrail_bridge.py`. Keep optional guardrails out of direct core imports.
MCP stays retrieval-only, sanitized, and without a generation/sampling path.

## Auth, state, and observation

Stage 3 attaches session/device-token authentication to `/query` when
`auth.enabled` is true. Same-origin checking is unconditional. Do not describe
Stage 3 as merely planned or claim all query requests require a credential.
The separate API-key surface fails closed unless the configured optional-key
bypass passes all peer, forwarding-header, and origin checks. RBAC is separate.

Read master switches before describing memory, auth, agentic, or guardrails as
active. The writer code flag, mode, and writes flag are armed, but the agentic
master switch defaults off. Keep the full reason/confirm/write chain intact.
Audit JSONL is authoritative; Numbat is a derived, fail-soft stream, enabled by
default. Read `utils/logger.py` and `utils/numbat_emitter.py` for redaction and
producer deduplication before changing observation behavior.

## Out-of-band execution

`harness/agent_routes.py` owns agent run/push/publish handlers registered by
`harness/server.py`. The ToolBroker in `utils/tool_broker.py` gates tool names;
empty allowlists deny, and broker approval does not replace reason/confirm.
Agentic execution travels through `ops_runner`, not direct core imports.

Real-repo approval uses acceptance manifests bound to run/base/path hashes and
a disposable-copy verification before finalize. Read `agentic/executor/` and
its tests before claiming a platform sandbox guarantee. Job Objects control
process trees; they are not a network namespace. See
[environment notes](CyClaw-environment.md) for the platform source map.

## Advice and verification

Cite symbols and exact inspected paths. Separate implemented behavior, shipped
config, operator opt-ins, tests, and unverified live services. For PR advice,
include validity of findings, base/head state, overlap, CI, and any required
merge dependency. Do not treat bot task summaries or old merge lists as proof
that a fix reached the remote head.
