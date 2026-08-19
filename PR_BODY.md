## Branch naming (required for agent-opened PRs)

`grok/spend-live-probe`

## Title

`[feat] - opt-in live Grok/Claude spend probe vs vendor usage`

## Proposed changes

Refs #958 after #1007 merged to `origin/main` (`61edda6b`). Owner asked for a real-API spend check, not another ledger rewrite.

- `utils.spend.compare_vendor_cost` — rate-table USD vs xAI `cost_in_usd_ticks` (Claude has no dollar field)
- `metrics.py` Spend section prints `table_usd` / `vendor_usd` / `delta_usd` when ticks exist
- `tests/spend_live_probe.py` — **not** `test_*.py`, so CI `pytest tests/` never collects it. Refuses unless `CYCLAW_SPEND_LIVE=1`. One tiny Grok and/or Claude `generate()` through the real clients (the emit seam). Never logs prompt, answer, or keys.

`gate.py` / `graph.py` / MCP untouched.

**Invariant / Governance Impact**
- None. I6 unchanged. Paid calls are operator-gated and off the CI path.

## Types of changes

- [ ] Bugfix
- [x] New feature
- [ ] Breaking change
- [ ] Documentation Update
- [ ] Invariant / Governance refinement

(The probe is a test/ops tool; the comparator is a correctness aid for the existing spend feature.)

## Benefits / why

- Operator can run one billed Grok/Claude call and see whether the ledger matches vendor ticks (Grok) or the official token formula (Claude).
- CI cannot spend money: discovery skip + `CYCLAW_SPEND_LIVE` fail-closed.

## Risks to monitor

- This agent shell’s `GROK_API_KEY` is a placeholder (`api.x.ai` 400 Incorrect API key). Live Grok check still needs a real console key. `ANTHROPIC_API_KEY` unset here.
- Option B `query_hash` / `route_path` still deferred.

## Checklist

- [x] Read latest architecture / SECURITY.md as needed
- [x] Six invariants + I6 isolation preserved
- [x] cyclaw-sandbox + CI emulation stamp written (`verify_ci_emulation.py`)
- [x] Draft PR only; no push to `main`

## Verify

- `ruff check --select E,F,I,B,C4,UP,S` on touched Python → exit 0
- `GROK_API_KEY=dummy python -m pytest tests/test_spend.py tests/test_metrics_spend.py tests/test_due_diligence_invariants.py -q --tb=short` → exit 0
- invariant-guard 35/35
- `CYCLAW_SPEND_LIVE=1 python tests/spend_live_probe.py` → Grok 400 invalid key on this machine; Claude skipped (no Anthropic key)
- `python ~/.grok/githooks/cyclaw/verify_ci_emulation.py` before push

## Merge order

- This PR: P1 of 1
- Full stack: P1

## Base

- GitHub base: `main` (`origin/main@61edda6b`)
