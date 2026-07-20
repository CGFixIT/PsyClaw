---
description: Iterative test coverage and quality loop — adds tests under tests/ until coverage reaches 100%, diagnoses and fixes failing tests, and continues until all test results exceed 85% pass rate (targeting 100%).
---

Run the tests refactor loop until the suite is green and coverage is maximized. $ARGUMENTS

Bring the test suite to full health: add tests until coverage hits 100%, diagnose every failure, and apply fixes until all tests pass. The pass-rate target is 85% minimum; 100% is the goal.

---

## Setup

```bash
PROJNAME=$(basename "$PWD")
TRACKER="/tmp/refactor-${PROJNAME}.md"
```

Initialize tracker if absent:

```bash
[ -f "$TRACKER" ] || cat > "$TRACKER" <<EOF
# Tests Refactor — $PROJNAME
Started: $(date -u +"%Y-%m-%dT%H:%M:%SZ")
Target: 100% coverage, ≥85% pass rate (goal: 100%)

## Baseline
(run measurement before first change)

## Progress
EOF
```

---

## Measurement Protocol

Run this exact command every iteration — do not vary flags between runs:

```bash
GROK_API_KEY=dummy pytest tests/ \
  --tb=short -q \
  --cov=. \
  --cov-report=term-missing \
  --cov-config=.coveragerc 2>&1 | tee /tmp/pytest-last-run.txt
```

If `.coveragerc` doesn't exist, create a minimal one first:

```bash
[ -f .coveragerc ] || cat > .coveragerc <<EOF
[run]
omit =
    tests/*
    .claude/*
    */__pycache__/*

[report]
exclude_lines =
    pragma: no cover
    if __name__ == .__main__.:
EOF
```

### Extract key metrics after each run

```bash
# Pass rate
TOTAL=$(grep -E "^\d+ passed" /tmp/pytest-last-run.txt | awk '{print $1+$4}')
PASSED=$(grep -E "^\d+ passed" /tmp/pytest-last-run.txt | awk '{print $1}')
PASS_RATE=$(echo "scale=1; $PASSED * 100 / $TOTAL" | bc)

# Coverage
COVERAGE=$(grep "TOTAL" /tmp/pytest-last-run.txt | awk '{print $NF}')

echo "Pass rate: ${PASS_RATE}% | Coverage: ${COVERAGE}"
```

Record both metrics in the tracker under `### Step N — Measurement`.

---

## Decision Tree at Loop Start

Read the current metrics and branch:

```
if pass_rate < 85%:
    → FIX FAILURES FIRST (see Fix Loop below)
elif pass_rate >= 85% and coverage < 100%:
    → ADD COVERAGE (see Coverage Loop below)
elif pass_rate >= 85% and coverage == 100% and pass_rate < 100%:
    → FIX REMAINING FAILURES (same Fix Loop)
else (pass_rate == 100% and coverage == 100%):
    → DONE
```

---

## Fix Loop — bring pass rate to ≥ 85%

Repeat until pass rate ≥ 85%:

### 1. Triage failures

```bash
GROK_API_KEY=dummy pytest tests/ --tb=long -q 2>&1 | grep -A 20 "FAILED\|ERROR"
```

Group failures by root cause — don't fix them one-by-one if they share a common cause:
- Import errors → missing dependency or broken module path
- Fixture errors → missing or misconfigured pytest fixtures
- AssertionError → logic bug in source or wrong test expectation
- Exception in setup/teardown → server state leak between tests

### 2. Identify root cause

For each failure group, read the traceback fully. Common patterns in this project:

- `GROK_API_KEY` not set → ensure env var is set in test or conftest
- ChromaDB / BM25 index not built → check if `tests/conftest.py` builds the index
- LangGraph node error → mock the LLM call; don't require LM Studio in tests
- Rate-limit state leaking between tests → reset `RateLimiter` state in fixture teardown
- Personality file missing → fixture must create `data/personality/soul.md`

### 3. Fix

Apply the minimal fix:
- If it's a source bug: fix the source, not the test
- If it's a test bug (wrong expectation, brittle assertion): fix the test
- If it's a missing fixture: add to `tests/conftest.py`
- If it's a missing mock: add `unittest.mock.patch` for external I/O

### 4. Re-measure

Run the full measurement command. Record new pass rate and coverage.

### 5. Commit if improved

```bash
git add -p
git commit -m "test(fix): <root cause fixed — N tests now passing>"
```

Loop back to triage until pass rate ≥ 85%.

---

## Coverage Loop — bring coverage to 100%

Once pass rate ≥ 85%, shift to adding missing coverage:

### 1. Find uncovered lines

```bash
GROK_API_KEY=dummy pytest tests/ --cov=. --cov-report=term-missing -q \
  2>&1 | grep -E "^\S.*\d+%.*\d+-\d+" | sort -t'%' -k1 -n
```

Pick the module with the lowest coverage percentage first.

### 2. Read the uncovered lines

```bash
# Example: gate.py lines 142-155 are uncovered
# Read those lines and understand what they do
```

For each uncovered block, identify **what scenario** would execute it:
- Error handling branch → write a test that injects the error condition
- Edge-case input → write a test with that input
- Conditional path → write a test that satisfies the condition
- Dead code → if genuinely unreachable, add `# pragma: no cover` with a comment explaining why

### 3. Write the test

Add tests to the appropriate file under `tests/`. Match existing naming conventions:

```
tests/test_gate.py          ← API endpoint tests
tests/test_sanitizer.py     ← input sanitization
tests/test_security.py      ← security/injection checks
tests/test_rate_limit.py    ← rate limiting logic
tests/test_audit.py         ← audit logging
tests/test_personality.py   ← soul/personality system
```

If no file fits, create `tests/test_<module_name>.py`.

Test structure:

```python
def test_<specific_behavior>(client):
    """One sentence: what this test verifies."""
    # Arrange
    ...
    # Act
    response = client.post("/query", json={...})
    # Assert
    assert response.status_code == 200
    assert response.json()["field"] == expected
```

### 4. Re-measure

Run full measurement. Check both pass rate and coverage. If a new test reveals a source bug, fix it (don't skip the test).

### 5. Commit if coverage improved

```bash
git add tests/ .coveragerc
git commit -m "test(coverage): add tests for <module> — coverage N% → M%"
```

Loop back to find next uncovered lines.

---

## Stopping Criteria

Stop when **both** are true:
- Pass rate ≥ 85% (goal: 100%)
- Coverage = 100%

If pass rate is exactly 85–99% and coverage is 100%, run one more Fix Loop pass — the remaining failures are known and worth fixing now.

Append `## Final State` to the tracker:

```markdown
## Final State
Completed: <timestamp>

| Metric      | Baseline | Final  |
|-------------|----------|--------|
| Pass rate   | 62%      | 100%   |
| Coverage    | 41%      | 100%   |
| Tests total | 18       | 47     |
| Failures    | 7        | 0      |
```

---

## conftest.py Checklist

Every test session needs these fixtures in `tests/conftest.py`. Verify they exist before adding tests:

```python
import pytest
from fastapi.testclient import TestClient

@pytest.fixture(scope="session", autouse=True)
def build_index():
    """Build retrieval index once per session."""
    import subprocess, os
    env = {**os.environ, "GROK_API_KEY": "dummy"}
    subprocess.run(["python3", "-m", "retrieval.indexer"], env=env, check=True)

@pytest.fixture(scope="session")
def client(build_index):
    import os; os.environ.setdefault("GROK_API_KEY", "dummy")
    from gate import app
    return TestClient(app)

@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Prevent rate-limit state leaking between tests."""
    from utils.rate_limit import RateLimiter
    RateLimiter._store.clear()
    yield
    RateLimiter._store.clear()
```

---

## Notes

- **Fix source bugs, not tests** — if a test correctly describes expected behavior and the source is wrong, fix the source.
- **No `# pragma: no cover` without justification** — only mark code unreachable if you can prove it (e.g., defensive else on an exhaustive enum).
- **Don't mock what you can control** — mock external I/O (LM Studio, Grok API, disk), not internal logic.
- **`{projectname}`** in tracker path is `basename $PWD`, e.g. `CyClaw` → `/tmp/refactor-CyClaw.md`.
- **Pass rate formula**: `passed / (passed + failed + error) * 100` — skipped tests do not count toward or against the rate.
- Use `tests/conftest.py` fixtures; new tests must start no live service and stay deterministic.
- Coverage gate is 80% `fail_under` in `pyproject.toml` — do not lower it to make the loop exit early.
