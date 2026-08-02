# CyClaw Safe Agentic Enhancement — Master Plan (v0.1)

> Status: **implemented in this branch as an experimental, opt-in, disabled-by-default
> layer.** This document is the design record and review guide.

## Executive summary

CyClaw gains a **controlled, auditable, human-governed** agentic layer without
weakening any of its five security invariants. The layer is **out-of-band** —
modeled exactly on the shipped `sync/` module — so it is never imported by
`gate.py`, `graph.py`, or `mcp_hybrid_server.py`, and cannot influence retrieval,
routing, or the MCP surface.

Two capabilities, both off by default:
1. **Read-only GitHub context** via the `gh` CLI (argv-list, no shell, audited).
2. **A governed skills registry** reusing the soul `propose/apply` pattern.

A **write scaffold** exists only as a **disabled, stubbed** module: its gate is the
out-of-band analogue of CyClaw's triple-gate, and v0.1 physically never executes a
write (`EXECUTION_ENABLED = False`; the executor raises `NotImplementedError`).

## Hard constraints (non-negotiable)
- No invariant weakened. Out-of-band preferred. Human approval on anything that
  could mutate external state. **Not ambitious** — minimal, reviewable surface.
- Zero new runtime dependencies (`gh` is an external binary, like `rclone`).
- New code clears the same CI / pip-audit / osv / Python 3.12 bar as `main`.

## Current CyClaw state relevant to this work
See `cyclaw_codebase_notes.md`. Key anchors: request path (`gate.py:220`→`graph.py`),
the `sync/` isolation template (`sync/runner.py:467`, `:519`), the governance
pattern (`utils/personality.py:175-246`), shared audit (`utils/logger.py:106`).

## What makes each surveyed tool excellent (and where it conflicts)
See `subagent_researcher_notes.md` for citations + confidence. Summary:

| Tool | Borrowed lesson | Rejected (conflicts with invariant) |
|---|---|---|
| Claude Code | deterministic hooks; context isolation; skills-as-files | autonomous orchestration / tool auto-invoke |
| Copilot SDK 2026 | permission/preToolUse gates; MCP allow-list shape | agent autonomously invoking tools |
| Hermes Agent | persist "what worked" as versioned skills (file-as-truth) | **autonomous** self-modifying skill loop |
| OpenClaw/ClawHub | named, versioned, searchable skill registry + moderation hook | public networked marketplace / remote install |
| Rust harnesses | push heavy work into a fast isolated external process | wholesale Rust rewrite (unwarranted) |

## Trade-off analysis (route selection)
| Route | Power | Invariant risk | New deps | Verdict |
|---|---|---|---|---|
| **A. Out-of-band `agentic/` via `gh`, read-first, writes stubbed** | opt-in GitHub context + governed skills | **lowest** (mirrors `sync/`) | none | **Primary (chosen)** |
| B. Extend MCP server with a GitHub tool | same reads | **weakens** MCP "retrieval-only, no sampling" | none | Rejected |
| C. PyGithub/SDK library in-process | richer API | adds dep → new SCA surface | +1 | Deferred alt |
| D. In-graph agentic node | "native" feel | **breaks** topology=policy / RAG-first | — | Rejected outright |

**Primary route = A.** Alternative if `gh` cannot be required: **C** (PyGithub
behind the same `agentic.enabled` + mode flags), accepting the extra SCA surface.

## Implemented design (v0.1)
- `agentic/config.py` — validated `agentic:` block (repo slug, mode, writes_enabled,
  gh floor, registry path under `data/`). `enabled` defaults **False**.
- `agentic/gh_client.py` — `check_gh_version`, `build_read_argv` (allow-listed
  `_READ_OPS` only), `run_read` (argv list, `shutil.which`, audited). No token in argv.
- `agentic/context.py` — structured PR/issue/repo bundles over `run_read`.
- `agentic/writer.py` — **disabled** triple-gate (`mode==write` AND `writes_enabled`
  AND non-empty `reason` AND `confirm`) → still only a **dry-run plan**; executor
  unimplemented.
- `agentic/registry.py` — `SkillRegistry.propose_skill` / `apply_skill`: injection
  scan (config ∪ OWASP) at the write boundary, human reason required, atomic write,
  sha256 versioning, audit. Direct mirror of `personality.py`.
- `agentic/cli.py` + `selftest.py` — `python -m agentic.cli {status,context,
  propose-skill,apply-skill,test}`; `enabled:false` no-ops; gh-absent tolerated.
- Additive only elsewhere: `agentic:` block in `config.yaml`; `AgenticError`
  hierarchy in `utils/errors.py`; `agentic` added to coverage source.
- Tests: `tests/test_agentic_*.py` (config, gh_client, writer, registry, cli,
  selftest, **isolation**). 54 tests, subprocess mocked, no live `gh`.

## Prioritized roadmap (low-risk first)
1. **(done)** Docs + read-only skeleton + governed registry + stubbed writer + tests.
2. Wire `context` output into a session-side helper (human reads PR context). No
   exec. **[Status note 2026-08-02]:** not done as a standalone step, but
   subsumed by later work — `agentic/context.py`'s structured PR/issue/repo
   bundles are now consumed by `harness/` and the real-repo pipeline's planning
   stage instead of a separate helper.
3. Optional: enable real writes — implement `execute_write` behind the same gate
   with its own tests; only then consider flipping `EXECUTION_ENABLED`. **Requires
   explicit human sign-off and a security review.** **[Status correction
   2026-08-02]: shipped, but still disarmed.** This became a materially larger
   effort than this stub envisioned: a plan → patch → verify → human-decides →
   commit loop (`agentic/real_repo_loop.py`) against a jailed real clone, backed
   by a sandboxed verification layer (`agentic/executor/`). `EXECUTION_ENABLED`
   remains hardcoded `False` in `agentic/writer.py` — consistent with this
   plan's own precondition that arming needed explicit sign-off first.
4. Optional: surface registry skills to the operator tooling (still read-only at
   runtime). Still open as a discrete deliverable as of 2026-08-02 — no
   dedicated doc or module claims this was done as specified.

## Risks & mitigations
- *Scope creep into writes* → executor unimplemented; flag-flip is insufficient.
- *gh absent in CI/dev* → all tests mock subprocess; selftest SKIPs gracefully.
- *Unverifiable external claims* → confidence-labeled; patterns adopted, not products.
- *Config drift* → `agentic:` additive, `enabled:false`; absent/disabled = pure no-op.

## Invariant Preservation Checklist (filled honestly)
- **RAG-first unconditional entry:** PASS — no graph entry added.
- **Topology = Policy (no LLM routing):** PASS — CLI-only, deterministic; no edges.
- **Triple-Gated External + user-confirmed:** PASS — writes need mode+flag+reason+confirm
  and still only dry-run; reads are local metadata, off by default.
- **Audit Convergence on every path:** PASS — every read/refusal/registry op audits.
- **Soul Governance (human reason + atomic/propose-apply):** PASS — registry mirrors it.
- **Out-of-band isolation (no request-path coupling):** PASS — enforced **and
  unit-tested** (`test_agentic_isolation.py`).
- **Reproducible CI / pip-audit / Python 3.12:** PASS — zero new runtime deps.
- **Overall:** Safe to proceed with human review. Real write execution deferred.

## User-executable next steps
See `AGENTIC_README.md` for enabling, the CLI, and the `gh` setup. To verify:
`GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q` and `python -m agentic.cli test`.
