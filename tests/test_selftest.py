"""Direct unit tests for utils/selftest.py — the shared self-test helpers.

Five subsystems build their operator-facing ``selftest`` output on these four
functions (agentic, sync, guardrails, fsconnect, sqlconnect), but every existing
test exercises them only indirectly through one of those callers. That leaves
the contract itself — in particular "a SKIP counts as passed" — pinned nowhere,
so a change to it would surface as a confusing failure in an unrelated
subsystem's test rather than here.
"""

from __future__ import annotations

from utils.selftest import fail, finalize, ok, skip


# -- individual result constructors ---------------------------------------------

def test_ok_passes_and_is_tagged():
    passed, text = ok("rclone present")
    assert passed is True
    assert "[OK  ]" in text
    assert "rclone present" in text


def test_fail_does_not_pass_and_carries_the_reason():
    passed, text = fail("rclone present", "binary not found")
    assert passed is False
    assert "[FAIL]" in text
    assert "rclone present" in text
    assert "binary not found" in text


def test_skip_counts_as_passed():
    # Load-bearing: a skipped check is an intentional non-failure (an optional
    # dependency absent, a platform-specific probe on the wrong platform), so it
    # must not drag the self-test's exit status down. Flipping this to False
    # would make every optional-subsystem self-test report failure on a machine
    # that simply does not have that subsystem installed.
    passed, text = skip("pgvector backend", "psycopg not installed")
    assert passed is True
    assert "[SKIP]" in text
    assert "psycopg not installed" in text


def test_the_three_tags_are_distinguishable():
    tags = {ok("x")[1].strip(), fail("x", "r")[1].strip(), skip("x", "r")[1].strip()}
    assert len(tags) == 3


# -- finalize -------------------------------------------------------------------

def test_finalize_counts_and_preserves_order():
    results = [ok("a"), fail("b", "boom"), skip("c", "absent")]
    passed, total, lines = finalize(results)
    # ok + skip pass; fail does not.
    assert (passed, total) == (2, 3)
    assert [line.split("]")[1].strip().split(":")[0] for line in lines] == ["a", "b", "c"]


def test_finalize_on_empty_results():
    # A subsystem whose every probe was compiled out still has to produce a
    # printable summary rather than raise.
    assert finalize([]) == (0, 0, [])


def test_finalize_all_passed():
    assert finalize([ok("a"), ok("b")])[:2] == (2, 2)


def test_finalize_all_failed():
    assert finalize([fail("a", "x"), fail("b", "y")])[:2] == (0, 2)


def test_finalize_does_not_mutate_its_input():
    results = [ok("a"), fail("b", "boom")]
    snapshot = list(results)
    finalize(results)
    assert results == snapshot
