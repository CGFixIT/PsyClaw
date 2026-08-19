## Branch naming (required for agent-opened PRs)

`grok/spend-ledger-accuracy`

## Title

`[fix] - price Grok reasoning tokens and Claude cache TTLs from vendor usage`

## Proposed changes

Refs #958 (accuracy follow-up after #975 / #989). Does not re-implement the ledger.

Vendor docs (2026-08-19) showed the shipped rate table undercounted two billed token classes:

- xAI Chat Completions: `completion_tokens` is visible output only. Reasoning is `completion_tokens_details.reasoning_tokens` and is billed at the output rate. Official example: 9 completion + 94 reasoning. Persist `reasoning_tokens` and `cost_in_usd_ticks` (10_000_000_000 ticks = $1); prefer ticks at read time when present. `grok-4.5` ≥200k prompt uses the long-context band for **all** tokens ($4 / $0.60 cached / $12).
- Anthropic Messages: `usage.cache_creation.ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` split 5m ($2.50/M) vs 1h ($4/M) cache writes. Unsplit `cache_creation_input_tokens` still prices at 5m. `output_tokens` stays the inclusive Claude billing total.

`generate()` still returns `str`. `gate.py` / `graph.py` / MCP untouched. Dollars still computed at read time; never stored on the JSONL line. LocalLLM still does not emit.

**Invariant / Governance Impact**
- None of the six invariants change. I6: spend stays `utils/` imported by `llm/client.py` and `metrics.py` only.

## Types of changes

- [x] Bugfix
- [ ] New feature
- [ ] Breaking change
- [ ] Documentation Update
- [ ] Invariant / Governance refinement

## Benefits / why

- Fallback spend matches official xAI and Anthropic usage fields instead of dropping reasoning tokens and 1h cache writes.
- Vendor `cost_in_usd_ticks` is used when present so a rate-table lag cannot hide Grok's billed amount.

## Risks to monitor

- Historical 1M-token test fixtures now correctly price at the grok-4.5 long-context band.
- No live Grok/Claude invoice check in this PR (mocked usage fixtures only).
- `query_hash` / `route_path` still deferred (Option B would touch `graph.py`).

## Checklist

- [x] Read latest architecture / SECURITY.md as needed
- [x] Six invariants + I6 isolation preserved
- [x] cyclaw-sandbox + CI emulation stamp written (`verify_ci_emulation.py`)
- [x] Draft PR only; no push to `main`

## Verify

- `ruff check --select E,F,I,B,C4,UP,S` on touched Python → exit 0
- `GROK_API_KEY=dummy python -m pytest tests/test_spend.py tests/test_metrics_spend.py tests/test_client.py tests/test_ci_coverage_flag_contract.py tests/test_due_diligence_invariants.py -q --tb=short` → exit 0
- `python ~/.grok/skills/invariant-guard/check_invariants.py --repo-root <worktree>` → 35/35
- `python ~/.grok/githooks/cyclaw/verify_ci_emulation.py` — run before push
- No new CI workflow: `--cov=utils.spend` already in `ci.yml` and conda lane

## Merge order

- This PR: P1 of 1
- Full stack: P1

## Base

- GitHub base: `main` (`origin/main@5ac5df31`)
