"""Tests for agentic.context -- read-only GitHub context fetchers.

No live gh binary required: ``agentic.context.run_read`` is patched, so these
exercise the bundling logic and the ``allowed_read_ops`` guard without touching
the network or subprocess. Covers the module that was previously at 0% coverage
even though ``--cov=agentic`` was on (the package-level number masked it).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from agentic import context
from agentic.config import AgenticConfig
from agentic.context import (
    fetch_issue_context,
    fetch_issue_list,
    fetch_pr_context,
    fetch_pr_list,
    fetch_repo_context,
)
from utils.errors import AgenticError


def _cfg(allowed: list[str] | None = None) -> AgenticConfig:
    """Build an AgenticConfig with an optional restricted allow-list."""
    kwargs: dict = {"repo": "owner/repo", "mode": "read"}
    if allowed is not None:
        kwargs["allowed_read_ops"] = allowed
    return AgenticConfig(**kwargs)


def _fake_run_read(op: str, repo: str, **kwargs):
    """Mirror gh_client.run_read's real return contract exactly: every op returns
    the top-level envelope {"op", "repo", ...} — diff ops add "diff", all others
    add "data" (list for list ops, dict for view ops). A previous version dropped
    the "op"/"repo" envelope keys, so a consumer regression reading them (e.g.
    result["repo"]) would have stayed green here while breaking in production.
    """
    if op == "pr_diff":
        return {"op": op, "repo": repo, "diff": "diff --git a/f b/f\n+x"}
    if op in ("pr_list", "issue_list"):
        return {"op": op, "repo": repo, "data": [{"number": 1}]}
    return {"op": op, "repo": repo, "data": {"kwargs": kwargs}}


# --- fetch_pr_context ------------------------------------------------------


def test_fetch_pr_context_includes_diff_by_default():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        bundle = fetch_pr_context(_cfg(), 42)
    assert bundle["repo"] == "owner/repo"
    assert bundle["number"] == 42
    assert isinstance(bundle["pr"], dict)  # pr_view "data" payload
    assert bundle["diff"].startswith("diff --git")
    # Two reads: pr_view then pr_diff.
    ops = [c.args[0] for c in mr.call_args_list]
    assert ops == ["pr_view", "pr_diff"]


def test_fetch_pr_context_without_diff_skips_diff_read():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        bundle = fetch_pr_context(_cfg(), 7, include_diff=False)
    assert "diff" not in bundle
    ops = [c.args[0] for c in mr.call_args_list]
    assert ops == ["pr_view"]


def test_fetch_pr_context_guard_rejects_when_pr_view_not_allowed():
    # allow-list without pr_view -> guard raises before any read.
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        with pytest.raises(AgenticError):
            fetch_pr_context(_cfg(allowed=["repo_view"]), 1)
    mr.assert_not_called()


def test_fetch_pr_context_guard_rejects_when_pr_diff_not_allowed():
    # pr_view allowed but pr_diff not -> pr_view read happens, then diff guard trips.
    with patch.object(context, "run_read", side_effect=_fake_run_read):
        with pytest.raises(AgenticError):
            fetch_pr_context(_cfg(allowed=["pr_view"]), 1)


# --- fetch_issue_context ---------------------------------------------------


def test_fetch_issue_context_bundles_issue():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        bundle = fetch_issue_context(_cfg(), 99)
    assert bundle["repo"] == "owner/repo"
    assert bundle["number"] == 99
    assert isinstance(bundle["issue"], dict)  # issue_view "data" payload
    assert [c.args[0] for c in mr.call_args_list] == ["issue_view"]


def test_fetch_issue_context_guard_rejects_when_not_allowed():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        with pytest.raises(AgenticError):
            fetch_issue_context(_cfg(allowed=["repo_view"]), 1)
    mr.assert_not_called()


# --- fetch_repo_context ----------------------------------------------------


def test_fetch_repo_context_includes_lists_when_allowed():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        bundle = fetch_repo_context(_cfg())  # default allow-list has pr_list + issue_list
    assert bundle["repo"] == "owner/repo"
    assert isinstance(bundle["overview"], dict)  # repo_view "data" payload
    # Shortlists now carry pagination metadata, not a bare list.
    assert bundle["open_prs"]["items"] == [{"number": 1}]
    assert bundle["open_prs"]["count"] == 1
    assert bundle["open_prs"]["has_more"] is False
    assert bundle["open_issues"]["items"] == [{"number": 1}]
    assert [c.args[0] for c in mr.call_args_list] == ["repo_view", "pr_list", "issue_list"]


def test_list_with_more_signals_truncation():
    # When the +1 probe row comes back, has_more is true and items are capped.
    def fake(op, repo, **kwargs):
        limit = kwargs["limit"]  # _list_with_more requests limit+1
        return {"data": [{"number": i} for i in range(limit)]}  # always returns the full limit+1

    with patch.object(context, "run_read", side_effect=fake):
        out = context._list_with_more(_cfg(), "pr_list", limit=10)
    assert out["has_more"] is True
    assert out["count"] == 10 and len(out["items"]) == 10


def test_list_with_more_no_truncation():
    def fake(op, repo, **kwargs):
        return {"data": [{"number": 1}, {"number": 2}]}  # fewer than the limit

    with patch.object(context, "run_read", side_effect=fake):
        out = context._list_with_more(_cfg(), "issue_list", limit=10)
    assert out["has_more"] is False
    assert out["count"] == 2


def test_read_threads_timeout_and_retries_from_cfg():
    captured: dict = {}

    def fake(op, repo, **kwargs):
        captured.update(kwargs)
        return {"data": {"ok": True}}

    cfg = _cfg()
    cfg.gh_timeout_sec = 17
    cfg.gh_retries = 4
    with patch.object(context, "run_read", side_effect=fake):
        context._read(cfg, "repo_view")
    assert captured["timeout"] == 17
    assert captured["retries"] == 4
    assert captured["min_version"] == cfg.gh_min_tuple


def test_fetch_repo_context_omits_lists_when_not_allowed():
    # Only repo_view allow-listed -> no pr_list / issue_list reads.
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        bundle = fetch_repo_context(_cfg(allowed=["repo_view"]))
    assert "open_prs" not in bundle
    assert "open_issues" not in bundle
    assert [c.args[0] for c in mr.call_args_list] == ["repo_view"]


def test_fetch_repo_context_guard_rejects_when_repo_view_not_allowed():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        with pytest.raises(AgenticError):
            fetch_repo_context(_cfg(allowed=["pr_view"]))
    mr.assert_not_called()


def test_fetch_repo_context_passes_max_prs_and_max_issues_to_list():
    """max_prs / max_issues must be forwarded as the limit to _list_with_more."""
    captured: list[tuple[str, int]] = []

    def fake(op: str, repo: str, **kwargs: object) -> dict:
        if op in ("pr_list", "issue_list"):
            captured.append((op, int(kwargs["limit"])))  # type: ignore[arg-type]
            return {"data": []}
        return {"data": {"op": op, "repo": repo}}

    with patch.object(context, "run_read", side_effect=fake):
        fetch_repo_context(_cfg(), max_prs=5, max_issues=3)

    # _list_with_more probes limit+1, so we see 5+1=6 and 3+1=4.
    assert ("pr_list", 6) in captured
    assert ("issue_list", 4) in captured


# --- fetch_pr_list -----------------------------------------------------------


def test_fetch_pr_list_returns_paginated_result():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        result = fetch_pr_list(_cfg())
    assert "items" in result and "count" in result and "has_more" in result
    assert result["items"] == [{"number": 1}]
    assert result["count"] == 1
    assert result["has_more"] is False
    assert mr.call_args_list[0].args[0] == "pr_list"


def test_fetch_pr_list_guard_rejects_when_not_allowed():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        with pytest.raises(AgenticError):
            fetch_pr_list(_cfg(allowed=["repo_view"]))
    mr.assert_not_called()


def test_fetch_pr_list_respects_max_items():
    """max_items is forwarded as limit so the +1 probe reflects the cap."""
    captured: dict = {}

    def fake(op: str, repo: str, **kwargs: object) -> dict:
        captured.update(kwargs)
        return {"data": []}

    with patch.object(context, "run_read", side_effect=fake):
        fetch_pr_list(_cfg(), max_items=25)

    assert captured["limit"] == 26  # 25 + 1 probe


# --- fetch_issue_list --------------------------------------------------------


def test_fetch_issue_list_returns_paginated_result():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        result = fetch_issue_list(_cfg())
    assert "items" in result and "count" in result and "has_more" in result
    assert result["items"] == [{"number": 1}]
    assert mr.call_args_list[0].args[0] == "issue_list"


def test_fetch_issue_list_guard_rejects_when_not_allowed():
    with patch.object(context, "run_read", side_effect=_fake_run_read) as mr:
        with pytest.raises(AgenticError):
            fetch_issue_list(_cfg(allowed=["repo_view"]))
    mr.assert_not_called()


def test_fetch_issue_list_respects_max_items():
    captured: dict = {}

    def fake(op: str, repo: str, **kwargs: object) -> dict:
        captured.update(kwargs)
        return {"data": []}

    with patch.object(context, "run_read", side_effect=fake):
        fetch_issue_list(_cfg(), max_items=50)

    assert captured["limit"] == 51
