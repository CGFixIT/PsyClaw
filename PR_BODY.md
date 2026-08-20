## Branch naming (required for agent-opened PRs)

`grok/spend-probe-delta`

## Title

`[fix] - compare spend delta only on vendor-ticked rows`

## Proposed changes

Refs #958 after #1009 merged (`feeeeacf`). Lands audit findings **2–5** that were meant for #1009 before merge.

- **2:** `delta_usd` is now `ticked_table_usd - vendor_usd` (paired rows only). All-rows `table_usd` is unchanged.
- **3:** live probe `SystemExit` if `rate_unknown` or `table_usd is None`.
- **4:** `ticks_mismatch` — 5% relative gate, `1e-8` floor, `$0.01` cap (the old absolute-only `$0.01` could not fire on a one-word call).
- **5:** probe loads shipped `config.yaml` `models.grok` / `models.claude`, caps `max_tokens` at 2048, forces `retry.max_retries: 0`, prints the model used.

Finding 6 (`usage_missing` → confident `$0.00`) and finding 1 (agentic plane vs `/query` AC) are **not** in this PR.

**Invariant / Governance Impact**
- None. I6 unchanged. Probe still opt-in (`CYCLAW_SPEND_LIVE=1`).

## Types of changes

- [x] Bugfix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation Update
- [ ] Invariant / Governance refinement

## Benefits / why

- Spend CLI delta no longer mixes pre-ticks Grok rows and Claude rows into a fake vendor comparison.
- Live probe fails closed on an unpriced model and uses the deployed model IDs.

## Risks to monitor

- Print line now includes `ticked_table_usd`.
- Live billed reconciliation still needs real xAI + Anthropic keys.

## Checklist

- [x] Read latest architecture / SECURITY.md as needed
- [x] Six invariants + I6 isolation preserved
- [x] cyclaw-sandbox + CI emulation stamp written (`verify_ci_emulation.py`)
- [x] Draft PR only; no push to `main`

## Verify

- ruff on touched Python → exit 0
- `GROK_API_KEY=dummy python -m pytest tests/test_spend.py tests/test_metrics_spend.py tests/test_due_diligence_invariants.py -q --tb=short` → exit 0
- invariant-guard 35/35
- `python ~/.grok/githooks/cyclaw/verify_ci_emulation.py` before push

## Merge order

- This PR: P1 of 1
- Full stack: P1

## Base

- GitHub base: `main` (`origin/main@feeeeacf`)
