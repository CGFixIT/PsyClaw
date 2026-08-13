---
name: cyclaw-optimize
description: Find and implement one current, evidence-backed CyClaw improvement, using macOS as the primary operator path and Windows as a close secondary. Use for reliability, security, performance, CI, packaging, RAG, harness, fsconnect, GitHub agent, Dropbox sync, or documentation optimization.
---

# Optimize CyClaw

Optimize the current repository state, not a remembered finding. The successful
outcome is one small, demonstrated improvement or a clear no-change conclusion.
Do not manufacture architecture, speed work, or PR volume.

## Evidence standard

Label what the check actually proves:

1. **Static/configured** - source, config, or parser evidence.
2. **Simulated/mocked** - platform, subprocess, network, or service fixtures.
3. **Host-real** - the actual OS, filesystem, process, socket, or embedded
   component ran.
4. **Live external** - the real account, API, daemon, device, or managed service
   ran.

Never use a lower level as proof of a higher one.

## Mac-first platform map

Use `.claude/skills/CyClaw-Optimize/SKILL.md` as the detailed, current platform
map. In brief: macOS is the primary installer/harness/fsconnect path; Windows is
a blocking close secondary with its own installer, native handle-based
fsconnect reads, and live harness smoke. Both run real embedded RAG retrieval.
GitHub-agent CI uses local git plus fake `gh`, and Dropbox tests do not perform
live OAuth/transfers. Verify all mutable pins, commands, and coverage claims
against current code/workflows before acting.

## Workflow

1. Read `AGENTS.md`, `CLAUDE.md`, `INVARIANTS.md`,
   `docs/THREAT_MODEL.md`, `.codex/skills/cyclaw-project-guidance/SKILL.md`,
   and affected source, tests, workflows, config, installers, and docs.
2. Fetch `origin/main` explicitly. Preserve an unrelated, dirty, or divergent
   checkout with a clean isolated clone/worktree; never reset it to begin.
   Record the branch-point SHA and stop on fetch/auth/cleanliness failure.
3. Do a time-boxed read-only sweep across core RAG/MCP, `harness/`, `macos/`,
   `powershell/`, `agentic/` (especially fsconnect, SQL, and real-repo loop),
   `sync/`, optional integrations, config, manifests, workflows, and tests.
   Trace relevant callers before retaining a candidate.
4. Check recent commits and current open PRs using the available GitHub surface
   or official `gh`. Drop candidates already covered, retired, inherited from a
   known-red base, or dependent on an unapproved product/security decision.
5. Rank survivors by evidence, impact, effort, and regression risk. Select the
   smallest root-cause change whose benefit can be verified. If none clears the
   bar, report the evidence and stop.
6. Announce the proposed chunks, owned files, verification, and risk. There is
   no PR quota.
7. **Step 3.5:** map `file -> chunks` before branching. Consolidate related
   shared-file edits; stack only true dependencies and base a child PR on its
   parent branch. Trial-merge every related order in a throwaway clone/worktree;
   require both changes, zero conflict markers, and valid structured/shell files.
8. Make one minimal change using existing patterns and dependencies. Add the
   smallest regression that would fail without it; avoid speculative
   abstractions, new knobs, broad cleanup, and revived retired paths.
9. Run relevant static/runtime checks, inspect the final diff, and record
   skipped physical/live checks and residual risk.
10. Commit, push, and open a complete draft PR only when authorized. Fetch and
    rebase independent work before the first push if `main` moved. Monitor CI to
    terminal state and fix branch-caused failures with follow-up commits.

## Verification routing

Use the current command matrix in the canonical Claude skill rather than
duplicating it here. Always run `git diff --check` and the invariant guard; add
Ruff/targeted pytest for Python, doc-sync for skills/docs, dependency guards for
install guidance, and shell syntax checks for shell. On Windows, enumerate
pytest file globs because PowerShell does not reliably expand them. Expand to
the full suite for cross-cutting or release-risk changes, and state unavailable
physical/live checks explicitly.

## Guardrails

- Preserve I1 RAG-first, I2 topology-as-policy, I3 triple-gated external
  fallback, I4 audit convergence, I5 soul governance, and I6 optional-module
  isolation. `gate.py`, `gate_ops.py`, `gate_auth.py`, `gate_memory.py`,
  `graph.py`, and `mcp_hybrid_server.py` must not import `agentic`, `sync`,
  `guardrails`, `harness`, or `telegram`; those out-of-band modules must not
  import the core request-path modules either.
- Preserve loopback defaults, fail-closed behavior, redaction, human decisions,
  and default-off out-of-band integrations.
- Do not add a dependency, service, secret, license, config switch, or general
  abstraction without demonstrated need and complete alignment.
- Keep local, committed, pushed, draft, CI, review, and merged state distinct.
  Never push to `main`, force-push, merge, or request review without explicit
  authorization.

The fuller Claude workflow and operator evidence notes live in
`.claude/skills/CyClaw-Optimize/SKILL.md`; do not create a tracked `.agents`
copy.
