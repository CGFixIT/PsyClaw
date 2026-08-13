---
name: cyclaw-optimize
description: Find and implement one current, evidence-backed CyClaw improvement, using macOS as the primary operator path and Windows as a close secondary. Use for reliability, security, performance, CI, packaging, RAG, harness, fsconnect, GitHub agent, Dropbox sync, or documentation optimization.
---

# Optimize CyClaw

Optimize the current repository state, not a remembered finding. The successful
outcome is one or two small, demonstrated improvement or a clear no-change conclusion.
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

Treat these as routing hints, then verify them against current source and CI:

- **macOS:** primary installer and harness path. Setup enables only
  `fs_list`, `fs_stat`, and `fs_read` inside `~/CyClaw-FS`; writes and indexing
  stay off. Darwin tests cover held-fd descent, APFS aliases, metadata policy,
  and mounted-volume opt-in, but hosted CI cannot prove interactive TCC or real
  iCloud behavior.
- **Windows:** close secondary whose CI leg is blocking, with a PowerShell
  installer, native checked-handle list/stat/read tests, and live loopback
  gateway/harness smoke.
  There is no fsconnect setup helper, and filesystem writes are hard-refused.
- **RAG/MCP:** both platforms run real embedded Chroma, BM25, and RRF retrieval;
  that is not evidence of live model generation. MCP remains retrieval-only.
- **GitHub coding:** optimize the active governed real-repo loop, not retired
  DeepAgents construction. CI uses local git and fake `gh`, not a live GitHub
  account, hosted repository mutation, or live model.
- **Dropbox/rclone:** operational sync code is default-off and cross-platform,
  but ordinary tests mock rclone/network and do not validate Dropbox OAuth or
  transfers. macOS scheduling uses cron; Windows uses `schtasks`.
- **Other surfaces:** include SQL, memory/auth, guardrails, and Telegram when
  relevant; verify each surface's actual default and platform/service boundary.

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

Use `.github/workflows/ci.yml` and the affected subsystem tests as the mutable
command source of truth. Always run `git diff --check` and the invariant guard;
add Ruff/targeted pytest for Python, doc-sync for skills/docs, dependency guards
for install guidance, and syntax checks for shell. On Windows, enumerate pytest
file globs because PowerShell does not reliably expand them. Expand to the full
suite for cross-cutting or release-risk changes, and state unavailable
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
