# CyClaw contracts and navigation

Read current AGENTS.md, CLAUDE.md, INVARIANTS.md, config.yaml, and the relevant
source before making a claim. Use docs/THREAT_MODEL.md for intended security
scope. Code establishes behavior; it does not authorize changing policy.

## Load-bearing boundaries

- I1: retrieval is the unconditional graph entry before any generation.
- I2: graph edges own routing. The inspected baseline has 12 nodes, including
  provider pre-action hooks; verify the actual node/edge sets before changes.
- I3: gateway client construction enforces hybrid mode and provider enablement;
  the graph enforces confirmation, selected provider, and client availability.
  Destination allowlists and hooks can restrict access, not replace consent.
- I4: all graph paths converge on audit_logger and then END.
- I5: soul evolution needs a human reason, injection scanning, and atomic write.
  Missing soul self-initializes at boot; read-only checks must avoid that write.
- I6: gate.py, gate_ops.py, gate_auth.py, gate_memory.py, graph.py, and
  mcp_hybrid_server.py must not import agentic, sync, guardrails, harness,
  telegram, or opentweet. Use maintained bridges/subprocess boundaries.
  memory is a separate optional subsystem, not an I6 forbidden import.

## Current behavior to reverify

| Surface | Implementation / contract |
|---|---|
| Local and offline-best-effort answers | graph.py and utils/endpoint_trust.py enforce loopback or exact models.local_llm.trusted_hosts entries. Empty list is the default. Malformed URLs return typed ENDPOINT_TRUST failures. |
| Container/LAN model | docs/DOCKER.md explains explicit trusted host setup. Such models receive local context and soul without cloud confirmation; hostname matching is not DNS/IP pinning. |
| Query auth | gate.py attaches session/device-token auth when auth.enabled is literal true; same-origin protection is unconditional. |
| API-key bypass | security.api_key_optional is separately gated by loopback peer, forwarding headers, and origin; it does not disable auth/RBAC. |
| Optional state | Shipped auth, memory, agentic, and guardrails masters are off. Hybrid and external providers are enabled; Numbat is enabled. Read actual config before claiming active behavior. |
| MCP | Retrieval-only, input-sanitized, no generation/sampling path. |
| Harness/agent execution | harness/server.py, harness/agent_routes.py, utils/tool_broker.py, agentic/executor/. ToolBroker permission is not reason/confirm authorization. |

Use the current invariant checker and targeted endpoint/graph/auth tests.
Neither a static pass nor a configured flag proves every runtime path.
