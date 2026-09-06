---
name: cyclaw-project-guidance
description: Read current CyClaw architecture, operating rules, configuration, and verification sources before substantive repository work.
---

# CyClaw Project Guidance

Read `AGENTS.md`, `CLAUDE.md`, and `.codex/Codex_instructions.md`, then the
smallest relevant source set below. Code and config outrank copied snapshots.
This refresh was checked against `origin/main@ef76d7f7` on 2026-09-06; fetch
and inspect the live diff before carrying those observations forward.

| Task | Sources |
|---|---|
| Routing, local/container models | `graph.py`, `utils/endpoint_trust.py`, `llm/client.py`, `config.yaml`, `tests/test_endpoint_trust.py`, `tests/test_graph.py` |
| Auth or public HTTP surface | `gate.py`, `gate_auth.py`, `utils/auth.py`, `utils/authn_manager.py`, `INVARIANTS.md`, `docs/THREAT_MODEL.md` |
| Harness/agent runs | `harness/server.py`, `harness/agent_routes.py`, `agentic/executor/`, `utils/tool_broker.py`, `docs/agentic/` |
| Install, launch, or dependencies | `setup-guide.md`, `macos/`, `powershell/`, manifests, Docker, active workflows |
| Skills/docs | `.codex/README.md`, relevant skill plus UI metadata, `.claude/skills/doc-sync/` |

Current contracts that old guidance often misses:

- Both local answer nodes enforce loopback or `models.local_llm.trusted_hosts`;
  malformed URLs produce typed endpoint-trust failures. An explicitly trusted
  host receives local context/soul; cloud-provider confirmation is separate.
- Auth Stage 3 is wired to `/query` only when auth is enabled; same-origin is
  always enforced. API-key optional bypass has peer/proxy/origin conditions.
- Hybrid and both providers ship enabled, while auth/memory/agentic/guardrails
  master switches ship off. Read actual config before changing or reporting them.
- Darwin dotenv loaders pin `/usr/bin/stat`, enforce 600/400, preserve source
  failure status for fallback, and restore the prior allexport state.
- Core/out-of-band isolation covers all six core modules and the package list
  in the maintained invariant checker, including OpenTweet.

For any behavior-sensitive change, trace callers and nearest tests. Read
`INVARIANTS.md` and the threat model for trust boundaries. Use the relevant
checker; never change runtime behavior merely to satisfy stale prose.

For roadmap work, use current project/product docs. Dated market conclusions
are not current measurements, and this skill does not authorize new features.
