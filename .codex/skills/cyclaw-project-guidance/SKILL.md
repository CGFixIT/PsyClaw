---
name: cyclaw-project-guidance
description: Read current CyClaw repository guidance, architecture, security invariants, commands, tests, and product constraints before substantive work in CGFixIT/CyClaw. Use for code, configuration, CI, dependency, security, agentic, sync, retrieval, or roadmap changes.
---

# CyClaw Project Guidance

Read current repository sources instead of copied snapshots:

1. `AGENTS.md` for the Codex map, repo layout, commands, and safety rules.
2. `CLAUDE.md` for the detailed architecture and operating contract.
3. `.github/copilot-instructions.md` and active workflows for current CI commands.
4. `docs/THREAT_MODEL.md` and `.github/SECURITY.md` for security-sensitive work.
5. The current code, `config.yaml`, tests, and subsystem docs for the requested scope.

For roadmap, optimization, or packaging work, also read the current business
sources linked from `AGENTS.md`. Treat dated market conclusions as dated.

Verify mutable facts such as model IDs, dependency versions, workflow commands,
and branch state from current sources before relying on them.

Preserve these invariants:

- retrieval runs before any LLM call
- graph edges enforce routing policy
- external fallback remains provider-enabled, hybrid-mode, and human-confirmed
- every execution path converges on audit logging
- soul mutation requires an explicit human reason
- optional `agentic/`, `sync/`, and `guardrails/` code stays out of the core request path

Prefer polish, proof, testability, packaging, and repo coherence over speculative
features unless the user explicitly asks for new product behavior.
