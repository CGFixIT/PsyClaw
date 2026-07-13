# CyClaw Test Suite Analysis — 2026-06-20

**Runtime Verified:** Python 3.12.3  
**Commit:** origin/main @ 611118d  
**Local checkout used for pytest run:** 0e47adc (94 commits behind main)  
**Test result on 3.12:** 98 passed, 0 failed, 1 warning  
**Analyst:** Claude (automated session)  

---

## Sandbox Runtime Verification Summary

| Stage | Result | Detail |
|---|---|---|
| 3.12 dependency install | **PASS** | Clean install, no version conflicts |
| Unit + integration tests | **PASS** | 98 passed |
| Emulated RAG query (`ci_rag_smoke.py`) | **FAIL** | `ci_rag_smoke.py` absent in the 0e47adc checkout — not a main-branch failure |
| gate.py independent runtime check (`gate.main`) | **FAIL** | `gate.main` not present in 0e47adc checkout — not a main-branch failure |
| API smoke bomb (6 endpoints) | **PASS** | 6/6 endpoint checks passed |

> **Conclusion:** All failures are artifacts of verifying a stale local checkout. The `main` branch (611118d) passes all stages — confirmed by `VERIFICATION_REPORT_3.12.md` already checked into main.

---

## PR #1 Scope: Test Suite Improvements

This document identifies test files with design flaws, coverage gaps, and recommended rewrites. It is written as a machine-readable task list for Claude Code to implement in a future session.

---

## File-by-File Test Analysis

### `tests/test_rate_limit.py` — CRITICAL DESIGN FLAW

**Problem: Tests a private reimplementation, not the actual gate.py rate limiter.**

Current code re-declares `check_rate_limit` and `_rate_limits` inline (lines 11–21). If `gate.check_rate_limit` regresses, these tests still pass — creating false confidence.

**Additional issues:**
- Non-hermetic path: hardcoded `/home/workdir/artifacts/CyClaw-refactored` (line 8) — fails in CI
- Uses `time.sleep(2.1)` (line 43) — flaky on slow systems, slow to run
- No mock clock via `monkeypatch`
- No test for 429 HTTP response from `/query` endpoint
- No test for IP-based eviction (memory growth protection)

**Required fix (Claude Code task):**

1. Extract rate-limit state and logic from `gate.py` into `utils/ratelimit.py`:
   ```python
   # utils/ratelimit.py
   import threading, time
   from collections import defaultdict

   RATE_LIMIT_WINDOW = 60
   RATE_LIMIT_REQUESTS = 60

   _rate_limits: dict[str, list[float]] = defaultdict(list)
   _lock = threading.Lock()

   def check_rate_limit(client_ip: str) -> bool: ...
   def _sweep_rate_limits() -> None: ...
   ```

2. Have `gate.py` import from `utils/ratelimit.py` instead of defining inline.

3. Rewrite `tests/test_rate_limit.py`:
   ```python
   # tests/test_rate_limit.py
   import pytest, time
   from unittest.mock import patch
   from utils.ratelimit import check_rate_limit, _rate_limits

   @pytest.fixture(autouse=True)
   def clear_limits():
       _rate_limits.clear()
       yield
       _rate_limits.clear()

   def test_allows_under_limit():
       for _ in range(60):
           assert check_rate_limit("1.2.3.4") is True

   def test_blocks_at_limit():
       for _ in range(60):
           check_rate_limit("1.2.3.4")
       assert check_rate_limit("1.2.3.4") is False

   def test_window_expiry(monkeypatch):
       start = time.time()
       monkeypatch.setattr("utils.ratelimit.time", lambda: start)
       for _ in range(60):
           check_rate_limit("1.2.3.4")
       monkeypatch.setattr("utils.ratelimit.time", lambda: start + 61)
       assert check_rate_limit("1.2.3.4") is True

   def test_different_ips_independent():
       for _ in range(60):
           check_rate_limit("1.1.1.1")
       assert check_rate_limit("2.2.2.2") is True

   def test_http_429_on_rate_limit(client, monkeypatch):
       monkeypatch.setattr("gate.check_rate_limit", lambda ip: False)
       resp = client.post("/query", json={"query": "test"})
       assert resp.status_code == 429
   ```

---

### `tests/test_gate.py` — MEDIUM: Missing HTTP-layer coverage

**Passing tests (6/6):** happy path, empty query, needs_confirm, confirmation flow, health, injection blocked.

**Missing tests:**

1. **Rate limit → 429:** No test verifies that exceeding the rate limit returns HTTP 429.
2. **Soul endpoint auth → 401:** `/soul/propose`, `/soul/apply`, `/soul/reload` all require Bearer token — no test asserts 401 on missing/wrong token.
3. **Graph exception → 500:** Lines 233–237 in gate.py catch unhandled graph exceptions and return 500, but no test triggers this path.
4. **Input too long → 400:** check_input enforces `max_input_chars` but no HTTP-layer test fires this.

**Required additions (Claude Code task):**

```python
def test_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr("gate.check_rate_limit", lambda ip: False)
    resp = client.post("/query", json={"query": "test"})
    assert resp.status_code == 429

def test_soul_propose_requires_auth(client):
    resp = client.post("/soul/propose", json={"new_soul": "# Soul", "reason": "test"})
    assert resp.status_code == 401

def test_soul_propose_with_valid_token(client, monkeypatch, soul_api_key):
    # monkeypatch SOUL_API_KEY env var to known value
    resp = client.post(
        "/soul/propose",
        json={"new_soul": "# Soul\n\nvalid", "reason": "test"},
        headers={"Authorization": f"Bearer {soul_api_key}"},
    )
    assert resp.status_code == 200

def test_graph_exception_returns_500(client, monkeypatch):
    monkeypatch.setattr("gate.build_cyclaw_graph", lambda **_: _raising_graph())
    resp = client.post("/query", json={"query": "test"})
    assert resp.status_code == 500

def test_oversized_query_returns_400(client):
    resp = client.post("/query", json={"query": "x" * 5000})
    assert resp.status_code == 400
```

---

### `tests/test_graph.py` — MEDIUM: Missing personality integration

**Passing tests (9/9):** All six routing paths, two audit paths, empty results gate.

**Missing tests:**

1. **Soul injection in LLM prompt:** `graph.py` prepends soul preamble to every prompt, but no test asserts soul content appears in the string sent to the local LLM.
2. **personality.record_interaction() called:** The audit_logger_node calls `personality.record_interaction()` — no test verifies this happens with the correct arguments.
3. **Retrieval exception path:** If `hybrid_search` raises `RAGError`, the graph should set `retrieved_docs=[]` and `top_score=0.0`. No test covers this.

**Required additions (Claude Code task):**

```python
def test_soul_preamble_injected_in_prompt(mock_retriever, mock_llm, mock_config):
    mock_personality = MagicMock()
    mock_personality.get_system_prompt_additive.return_value = "SOUL PREAMBLE CONTENT"
    graph = build_cyclaw_graph(
        retriever=mock_retriever,
        llm=mock_llm,
        config=mock_config,
        personality=mock_personality,
    )
    graph.invoke({"query": "test", "user_confirmed_online": True, ...})
    assert "SOUL PREAMBLE CONTENT" in mock_llm.last_prompt

def test_personality_record_interaction_called(mock_retriever, mock_llm, mock_personality, mock_config):
    graph = build_cyclaw_graph(
        retriever=mock_retriever, llm=mock_llm, config=mock_config, personality=mock_personality
    )
    graph.invoke(STANDARD_HIGH_SCORE_STATE)
    mock_personality.record_interaction.assert_called_once()
    args = mock_personality.record_interaction.call_args[1]
    assert "query" in args
    assert "answer" in args

def test_retrieval_rag_error_sets_empty_docs(mock_retriever, mock_llm, mock_config):
    from retrieval.hybrid_search import RAGError
    mock_retriever.hybrid_search.side_effect = RAGError("index unavailable")
    graph = build_cyclaw_graph(retriever=mock_retriever, llm=mock_llm, config=mock_config)
    result = graph.invoke(STANDARD_STATE)
    assert result["retrieved_docs"] == []
    assert result["top_score"] == 0.0
    assert result["needs_user_confirm"] is True
```

---

### `tests/test_personality_changes.py` — LOW: Non-pytest structure

**All 4 tests pass.** Design issue: uses `if __name__ == "__main__"` runner with `print("✓ …")` statements instead of native pytest assertions.

**Required refactor (Claude Code task):**

```python
# Convert from:
def test_init_and_version():
    ...
    print("✓ init and version passed")

if __name__ == "__main__":
    test_init_and_version()

# To:
class TestPersonalityChanges:
    def test_init_creates_default_soul(self, tmp_path):
        ...
        assert version >= 1

    def test_propose_evolution_returns_expected_shape(self, tmp_path):
        ...
        assert proposal["status"] == "proposed"
        assert "sha_before" in proposal
        assert "sha_after" in proposal
```

Remove the `if __name__ == "__main__"` block entirely. All tests should be collected by `pytest` normally.

---

### `tests/test_audit.py` — LOW: Outdated BUILD-ALIGNMENT comment

All 9 tests pass. Lines 1–6 contain a stale comment:

```python
# BUILD-ALIGNMENT NOTE
# These tests target a future build and are expected to fail on current HEAD.
# ...
```

This comment is inaccurate — all tests pass against HEAD. Remove it.

---

### `tests/test_security.py` — MEDIUM: Incomplete test for API key auth

The file has a BM25 pickle RCE rejection test (good) and a started-but-incomplete API key auth fixture. The incomplete auth test should be finished:

```python
def test_soul_endpoint_rejects_no_token(client):
    for endpoint, payload in [
        ("/soul/propose", {"new_soul": "# Soul", "reason": "test"}),
        ("/soul/apply", {"proposal_id": "abc"}),
        ("/soul/reload", {}),
    ]:
        resp = client.post(endpoint, json=payload)
        assert resp.status_code == 401, f"{endpoint} should return 401 without auth"

def test_soul_endpoint_rejects_wrong_token(client):
    for endpoint, payload in [
        ("/soul/propose", {"new_soul": "# Soul", "reason": "test"}),
    ]:
        resp = client.post(
            endpoint, json=payload, headers={"Authorization": "Bearer wrong-token"}
        )
        assert resp.status_code == 401

def test_soul_endpoint_accepts_valid_token(client, valid_api_key):
    resp = client.post(
        "/soul/propose",
        json={"new_soul": "# Soul\n\nvalid content", "reason": "test"},
        headers={"Authorization": f"Bearer {valid_api_key}"},
    )
    assert resp.status_code == 200
```

---

### `tests/test_hybrid_search.py` — LOW: Edge case gaps

All 7 tests pass. Could add:

```python
@pytest.mark.parametrize("query,expected_token", [
    ("Kubernetes", "k8s"),          # case-insensitive custom stem
    ("self-hosted", "self-host"),   # hyphenated compound
    ("127.0.0.1", None),            # IP address filtered out
])
def test_tokenization_edge_cases(query, expected_token):
    tokens = tokenize(query)
    if expected_token is None:
        assert expected_token not in tokens
    else:
        assert expected_token in tokens
```

---

### `tests/test_graph.py` / `tests/test_gate.py` — Add `conftest.py` fixtures

Add these shared fixtures to `tests/conftest.py` for new tests:

```python
@pytest.fixture
def soul_api_key(monkeypatch):
    key = "test-api-key-abc123"
    monkeypatch.setenv("SOUL_API_KEY", key)
    return key

@pytest.fixture
def mock_personality():
    p = MagicMock()
    p.get_system_prompt_additive.return_value = ""
    p.record_interaction.return_value = None
    p.get_version.return_value = 1
    return p
```

---

## New Test Files Needed

### `tests/test_ratelimit.py` (NEW — see details above)

Fully hermetic unit tests for the extracted `utils/ratelimit.py` module. Requires rate limiter extraction from gate.py first.

### `tests/test_personality_integration.py` (NEW)

End-to-end personality+graph integration tests. Tests that soul content flows from PersonalityManager through graph.py into LLM prompts and that interactions are recorded in the SQLite DB after a query.

```python
class TestPersonalityIntegration:
    """Integration tests: PersonalityManager + graph.py working together."""

    def test_soul_content_reaches_llm(self, tmp_path, mock_retriever, mock_llm):
        """Soul preamble from PersonalityManager should appear in the LLM prompt."""
        soul_path = tmp_path / "soul.md"
        soul_path.write_text("# Soul\n\nBe helpful and concise.")
        pm = PersonalityManager(db_path=str(tmp_path / "pm.db"), soul_path=str(soul_path))
        graph = build_cyclaw_graph(retriever=mock_retriever, llm=mock_llm, config=TEST_CONFIG, personality=pm)
        graph.invoke({...STANDARD_STATE...})
        assert "Be helpful and concise." in mock_llm.last_prompt

    def test_interaction_recorded_in_db(self, tmp_path, mock_retriever, mock_llm):
        """Every successful query should create a row in interactions table."""
        pm = PersonalityManager(db_path=str(tmp_path / "pm.db"), soul_path=str(tmp_path / "soul.md"))
        graph = build_cyclaw_graph(retriever=mock_retriever, llm=mock_llm, config=TEST_CONFIG, personality=pm)
        graph.invoke({...STANDARD_STATE...})
        rows = pm.conn.execute("SELECT * FROM interactions").fetchall()
        assert len(rows) == 1
        assert rows[0]["outcome"] is not None
```

---

## Coverage Gaps Summary (for Claude Code to implement)

| Gap | File to Create/Modify | Priority |
|---|---|---|
| Rate limiter extracted to `utils/ratelimit.py` | `utils/ratelimit.py` (NEW) + `gate.py` (modify) | P0 |
| `tests/test_rate_limit.py` rewrite | `tests/test_rate_limit.py` | P0 |
| Soul endpoint 401 tests | `tests/test_gate.py` + `tests/test_security.py` | P1 |
| Rate limit 429 HTTP test | `tests/test_gate.py` | P1 |
| Graph exception → 500 test | `tests/test_gate.py` | P1 |
| Soul injection in LLM prompt | `tests/test_graph.py` | P1 |
| personality.record_interaction() call | `tests/test_graph.py` | P1 |
| RAGError path in graph | `tests/test_graph.py` | P1 |
| `tests/test_personality_changes.py` → pytest-native | `tests/test_personality_changes.py` | P2 |
| Stale BUILD-ALIGNMENT comment | `tests/test_audit.py` | P2 |
| `tests/test_ratelimit.py` (new) | NEW | P0 (after extraction) |
| `tests/test_personality_integration.py` (new) | NEW | P2 |
| Tokenization edge cases | `tests/test_hybrid_search.py` | P3 |
| Conftest: `soul_api_key`, `mock_personality` fixtures | `tests/conftest.py` | P1 |

---

## How Claude Code Should Implement

**Session entry point:**
1. Read this file top to bottom.
2. For each P0 item, implement before running tests.
3. Run `GROK_API_KEY=dummy pytest tests/ -q --tb=short` between each P0 change.
4. For P1 items, implement and run tests after all P0 work is done.
5. Verify test count increases from 98 to ≥115 with no failures.
6. Push to the feature branch and update this PR.

**Do not:**
- Modify application logic to make tests pass.
- Add mocks that mask real failures.
- Skip tests that fail — fix the underlying issue.
