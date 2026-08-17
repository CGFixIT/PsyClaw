## Branch naming (required for agent-opened PRs)

`grok/mcp-manifest-pin`

## Title

`[security] - pin MCP tool manifest with SHA-256 drift check`

Allowed prefixes: `[invariant]` `[governance]` `[fsconnect]` `[agentic]` `[rag]` `[harness]` `[security]` `[docs]` `[infra]` `[fix]` `[feat]`

## Proposed changes

#974 E2 only. Committed `mcp_manifest.json` is the pin for the exported MCP `TOOLS` list (`name` / `description` / `inputSchema`). `mcp_hybrid_server.main()` fingerprints registered tools against that pin **before** `HybridRetriever()` and refuses to serve on mismatch.

- `utils/mcp_manifest.py` — canonicalize, SHA-256, compare, verify; missing pin fails closed
- Audit event `mcp_manifest_drift` carries expected/actual hashes only
- Hatch wheel `force-include` + sdist include so `cyclaw-mcp` from a real wheel still has the pin
- `--cov=utils.mcp_manifest` in both pytest coverage lanes

Does **not** close #974 (E4 rename, E5 extras, E6 smoke remain). E3 sanitizer already shipped in #982. E1 is already stdio-only.

**Invariant / Governance Impact**
- G5: `sampling` remains `None`; still one retrieval tool.
- I6: MCP imports `utils.mcp_manifest` (same class as `check_input`). No `agentic` / `sync` / `guardrails` / harness / telegram.
- I1–I5 untouched. invariant-guard 35/35.

## Types of changes

- [x] Bugfix
- [x] New feature
- [ ] Breaking change
- [ ] Documentation Update
- [ ] Invariant / Governance refinement

## Benefits / why

- Tool-description rug-pull / poisoning now fails start the same way soul drift is an integrity event.
- Adding a tool requires a reviewable manifest diff.

## Risks to monitor

- A docstring/schema edit without updating `mcp_manifest.json` makes `cyclaw-mcp` exit 1. That is the point.
- Wheel omit of the pin would fail closed on every install — covered by hatch include + `test_packaging`.

## Checklist

- [x] Read latest architecture / SECURITY.md as needed
- [x] Six invariants + I6 isolation preserved
- [ ] cyclaw-sandbox + CI emulation stamp written (`verify_ci_emulation.py`)
- [x] Draft PR only; no push to `main`

## Verify

- `python -m ruff check utils/mcp_manifest.py tests/test_mcp_manifest.py mcp_hybrid_server.py tests/test_mcp_server.py tests/test_packaging.py --select E,F,I,B,C4,UP,S` → exit 0
- `GROK_API_KEY=dummy python -m pytest tests/test_mcp_manifest.py tests/test_mcp_server.py tests/test_packaging.py tests/test_ci_coverage_flag_contract.py -q --tb=short` → exit 0 (run twice)
- `python ~/.grok/skills/invariant-guard/check_invariants.py --repo-root <worktree>` → 35 passed, 0 failed
- `python ~/.grok/githooks/cyclaw/verify_ci_emulation.py` — run before push

## Merge order

- This PR: P1 of 1
- Full stack: P1
- Topology: parallel with any later leftover (no shared files expected)

## Base

- GitHub base: `main`
- Forked from: `origin/main@c9b6b866`

Refs #974
