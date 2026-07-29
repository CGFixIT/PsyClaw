# github_coding_repo fixture

Committed fixture repository for the `GitHubCodingRunner` tests
(`agentic/harness_optimizer/runners/github_coding_runner.py`). It stands in
for a real GitHub coding target so runner evaluations stay no-network and
deterministic (phase 7 of
`docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`).

## Files

- `planner.py` — original single surface; `render()` returns `"baseline"`.
- `scheduler.py` — second surface, so tests can change one file while a
  holdout case watches a file the candidate did not touch.
- `docs/usage.md` — nested file, so cases and surfaces can use multi-segment
  relative paths.

## Contract

- `planner.py` must keep returning `"baseline"` —
  `tests/test_agentic_harness_phase679.py` asserts the committed file is
  never mutated by runner overlays, and
  `tests/test_agentic_fixture_repo.py` hashes the whole tree before/after a
  run.
- Keep every file tiny, import-free, and side-effect-free: the runner copies
  the whole tree on every evaluation and nothing here is executed.
- No `test_*.py` files — `testpaths = ["tests"]` would collect them out of
  this directory.
- Add a file only when a test needs it (a new surface, a new path shape);
  this is a fixture, not a project skeleton.
