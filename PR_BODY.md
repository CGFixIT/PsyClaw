## Branch naming (required for agent-opened PRs)

Head branch: `kimi/963-pre-action-hook`

---

## Title

[feat] - Add synchronous pre-action hook before Grok/Claude fallback (issue #963)

---

## Proposed changes

This PR implements the planned synchronous pre-action hook contract from issue #963:

- New `utils/external_pre_hook.py` runs an operator-configured command before any `grok_fallback` or `claude_fallback` call.
- The hook receives a JSON payload on stdin (`action`, `provider`, `model`, `query_hash`).
- Exit code 0 = allow; exit code 2 = deny (Numbat convention); any other exit / crash / timeout fails closed (deny + audit).
- Two provider-specific hook nodes (`pre_action_hook_grok`, `pre_action_hook_claude`) sit between `user_gate` and the fallback nodes in `graph.py`.
- The hook is disabled by default (`policy.fallback.pre_action_hook.enabled: false`) so existing deployments are unaffected.
- A deny verdict routes to `audit_logger` with `answer_model == "hook-denied"` and `pre_action_hook_denied: true` in the audit event.
- Config block, invariant-guard constants, workflow coverage flags, and docs (README, CLAUDE.md, INVARIANTS.md, copilot-instructions) are updated to reflect the 12-node topology.

**Invariant / Governance Impact**

- I1 RAG-first: unchanged; `retrieve` remains the unconditional entry point.
- I2 Topology = policy: routing still happens only through the named routers (`score_router`, `guardrail_router`, `user_gate_router`, `pre_action_hook_router`). The hook can only shrink the reachable state space (deny → audit); it cannot expand it.
- I3 Triple-gated external fallback: unchanged; the existing `mode == hybrid` + `provider.enabled` + `user_confirmed_online` + available client checks still gate entry to the hook path.
- I4 Audit convergence: all new hook nodes route to `audit_logger`; the deny branch lands there directly.
- I5 Soul governance: untouched.
- I6 Module isolation: `utils/external_pre_hook.py` does not import any out-of-band packages (`agentic`, `sync`, `guardrails`, `harness`, `telegram`).

---

## Types of changes

- [x] New feature (non-breaking change which adds functionality)
- [x] Invariant / Governance refinement (the hook strengthens the external-fallback gate)

Scope note: Core graph/gate/soul path.

---

## Benefits / why

- Gives operators a synchronous, fail-closed interception point before any paid/external LLM call.
- Uses Numbat's exit-code-2 deny convention so existing operator tooling can plug in without new semantics.
- Keeps the change topology-first: the graph decides whether the hook runs; the hook can only say "no".
- Disabled by default, so offline/air-gapped deployments are unaffected.

---

## Risks to monitor

- A misconfigured hook (exit 1, crash, or short timeout) will deny all external fallback calls. This is the intended fail-closed behavior, but operators enabling the hook should test the command first.
- The hook command is operator-configured argv; it must be trusted and must not introduce shell-injection. The runner uses `subprocess.run` with a list (no shell) and marks the line with `# noqa: S603` / `nosec`, matching `utils/ops_runner.py`.
- The synchronous call adds latency before external fallback. Default timeout is 5 s, capped at 30 s.
- No new telemetry paths: `external_pre_hook.py` does not import telemetry-capable libraries.

---

## Checklist

- [x] I have read the latest `docs/CyClaw Architecture Guide` and `SECURITY.md`
- [x] This change preserves all 6 security invariants and I6 module isolation
- [x] Full sandbox validation has been run (`verify_ci_emulation.py` + full pytest suite) and passes with no regressions
- [x] No new external network dependencies or mandatory online LLM assumptions were introduced
- [x] Relevant architecture docs and the invariant-guard checker have been updated for the new topology
- [x] Commit messages follow the title prefix convention

---

## Verify

Local gates run from `C:/Users/cgrady/CyClaw-repo` on branch `kimi/963-pre-action-hook`:

```bash
# CI emulation (config + invariant-guard + gate/harness runtime + due-diligence)
python C:/Users/cgrady/.grok/githooks/cyclaw/verify_ci_emulation.py

# Full pytest suite
GROK_API_KEY=dummy python -m pytest tests/ -q --tb=short

# Lint on touched paths
python -m ruff check --select E,F,I,B,C4,UP,S .
```

Also run: `python .claude/skills/invariant-guard/check_invariants.py`.

---

## Merge order

No stack; this branch is independent and can merge directly to `main` after review.

---

## Base

`main`

---

## Further comments

**Before/after invariant matrix**

| Invariant | Before | After | Evidence |
|---|---|---|---|
| I1 RAG-first | `retrieve` entry only | unchanged | `set_entry_point("retrieve")` preserved |
| I2 Topology = policy | conditional routing at `route_by_score`, `guardrail_input`, `user_gate` | adds `pre_action_hook_grok`/`claude` conditional sources; still only named routers | `check_invariants.py` I2 equality passes |
| I3 Triple-gated external | hybrid + enabled + confirmation + available client | unchanged; hook is an additional deny-only gate | existing `user_gate_router` conditions preserved |
| I4 Audit convergence | 9 upstream nodes reach audit | 11 upstream nodes reach audit | `check_invariants.py` I4 DFS passes; new test `test_hook_denied_path_emits_audit_event` |
| I5 Soul governance | untouched | untouched | no `utils/personality.py` changes |
| I6 Module isolation | core does not import OOB packages | `external_pre_hook.py` stdlib-only + no OOB imports | `check_invariants.py` I6 passes |

The hook layer is deliberately minimal: one synchronous subprocess call, one config block, two graph nodes. It does not cache, persist, or mutate state beyond the verdict it returns to the router.
