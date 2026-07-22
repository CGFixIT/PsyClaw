"""Tests for agentic.gh_client -- argv building, version check, run_read.

No live gh binary required: subprocess and shutil.which are mocked.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
import yaml

from agentic import gh_client
from agentic.gh_client import (
    _is_transient_gh_error,
    build_read_argv,
    check_gh_version,
    run_read,
)
from utils.errors import AgenticError, GhNotInstalledError, GhVersionError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _temp_audit(tmp_path: Path):
    """Prime the global config cache with a temp config so audit_log writes to tmp."""
    cfg = {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
           "policy": {"privacy": {}}}
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    reset_config_cache()
    from utils.logger import _get_config
    _get_config(str(path))  # prime cache
    check_gh_version.cache_clear()
    yield
    check_gh_version.cache_clear()
    reset_config_cache()


def _completed(stdout="", stderr="", returncode=0):
    return SimpleNamespace(stdout=stdout, stderr=stderr, returncode=returncode)


# --- argv building ---------------------------------------------------------

def test_build_pr_view_argv():
    argv = build_read_argv("pr_view", "owner/repo", number=42)
    assert isinstance(argv, list)
    assert argv[0] == "gh" and argv[1:3] == ["pr", "view"]
    assert "42" in argv and "owner/repo" in argv and "--json" in argv


def test_build_pr_diff_argv():
    argv = build_read_argv("pr_diff", "owner/repo", number=7)
    assert argv[1:3] == ["pr", "diff"] and "7" in argv and "--json" not in argv


def test_build_repo_view_argv():
    argv = build_read_argv("repo_view", "owner/repo")
    assert argv[1:3] == ["repo", "view"] and "owner/repo" in argv


def test_pr_view_argv_requests_richer_fields():
    argv = build_read_argv("pr_view", "owner/repo", number=1)
    fields = argv[argv.index("--json") + 1]
    for f in ("labels", "assignees", "createdAt", "mergeable", "changedFiles"):
        assert f in fields


def test_issue_and_repo_view_request_richer_fields():
    issue_fields = build_read_argv("issue_view", "owner/repo", number=1)
    ifields = issue_fields[issue_fields.index("--json") + 1]
    assert "assignees" in ifields and "milestone" in ifields and "comments" in ifields
    repo_argv = build_read_argv("repo_view", "owner/repo")
    rfields = repo_argv[repo_argv.index("--json") + 1]
    assert "repositoryTopics" in rfields and "primaryLanguage" in rfields


def test_build_rejects_unknown_op():
    with pytest.raises(AgenticError):
        build_read_argv("pr_merge", "owner/repo", number=1)


def test_build_rejects_write_like_op():
    # A write-shaped op name must not be buildable here.
    with pytest.raises(AgenticError):
        build_read_argv("pr_comment", "owner/repo", number=1)


def test_build_requires_number_for_view():
    with pytest.raises(AgenticError):
        build_read_argv("pr_view", "owner/repo")


def test_build_limit_is_clamped():
    # A five-digit limit must not flow verbatim into the gh CLI.
    argv = build_read_argv("pr_list", "owner/repo", limit=99999)
    assert argv[argv.index("--limit") + 1] == str(gh_client.MAX_LIST_LIMIT)


@pytest.mark.parametrize(
    "bad_repo",
    [
        "-X/y",            # leading-dash owner -> gh would parse as a flag
        "owner/-rf",       # leading-dash name
        "--repo=x/y",      # whole slug shaped like a flag
        "owner",           # missing '/name'
        "owner/name/extra",  # too many segments
        "owner /name",     # space (would split argv)
        "owner;rm -rf/name",  # shell-metachar shape
        "",                # empty
    ],
)
def test_build_rejects_argument_injection_repo(bad_repo):
    """A repo slug that gh could parse as a flag (or that splits argv) is refused."""
    with pytest.raises(AgenticError):
        build_read_argv("repo_view", bad_repo)


def test_build_rejects_injection_repo_across_all_ops():
    # The guard runs for every read op, not just repo_view.
    for op, kwargs in (
        ("pr_view", {"number": 1}),
        ("pr_diff", {"number": 1}),
        ("issue_view", {"number": 1}),
        ("pr_list", {}),
        ("issue_list", {}),
        ("repo_view", {}),
    ):
        with pytest.raises(AgenticError):
            build_read_argv(op, "-evil/repo", **kwargs)


def test_build_accepts_valid_repo_slugs():
    # Dots, hyphens and underscores are fine when not leading a segment.
    for good in ("owner/repo", "My-Org/Cy.Claw_1", "a/b"):
        argv = build_read_argv("repo_view", good)
        assert good in argv


# --- version check ---------------------------------------------------------

def test_check_gh_version_parses():
    with patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run",
                      return_value=_completed(stdout="gh version 2.55.0 (2024-08-21)\n")):
        assert check_gh_version() == (2, 55, 0)


def test_check_gh_version_is_cached():
    result = _completed(stdout="gh version 2.55.0 (2024-08-21)\n")
    with patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run", return_value=result) as mrun:
        assert check_gh_version("gh", (2, 40, 0)) == (2, 55, 0)
        assert check_gh_version("gh", (2, 40, 0)) == (2, 55, 0)
    assert mrun.call_count == 1

def test_check_gh_version_missing_raises():
    with patch.object(gh_client.shutil, "which", return_value=None):
        with pytest.raises(GhNotInstalledError):
            check_gh_version()


def test_check_gh_version_too_old_raises():
    with patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run",
                      return_value=_completed(stdout="gh version 2.10.0 (2022-01-01)\n")):
        with pytest.raises(GhVersionError):
            check_gh_version(min_version=(2, 40, 0))


def test_check_gh_version_unparseable_raises():
    with patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run", return_value=_completed(stdout="garbage")):
        with pytest.raises(GhVersionError):
            check_gh_version()


# --- run_read --------------------------------------------------------------

def test_run_read_json_op(tmp_path: Path):
    with patch.object(gh_client, "check_gh_version", return_value=(2, 55, 0)), \
         patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run",
                      return_value=_completed(stdout='{"number": 1, "title": "x"}')) as mrun:
        out = run_read("pr_view", "owner/repo", number=1)
    assert out["data"]["number"] == 1
    # Proves no shell: first positional arg is an argv LIST, and shell kwarg absent/false.
    called_argv = mrun.call_args.args[0]
    assert isinstance(called_argv, list)
    assert mrun.call_args.kwargs.get("shell", False) is False


def test_run_read_diff_op():
    with patch.object(gh_client, "check_gh_version", return_value=(2, 55, 0)), \
         patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run",
                      return_value=_completed(stdout="diff --git a b\n")):
        out = run_read("pr_diff", "owner/repo", number=1)
    assert out["diff"].startswith("diff --git")


def test_run_read_diff_is_truncated():
    # A multi-hundred-KB diff is capped, with a marker, before reaching consumers.
    big = "x" * (gh_client.MAX_DIFF_CHARS + 1000)
    with patch.object(gh_client, "check_gh_version", return_value=(2, 55, 0)), \
         patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run", return_value=_completed(stdout=big)):
        out = run_read("pr_diff", "owner/repo", number=1)
    assert len(out["diff"]) < len(big)
    assert out["diff"].endswith(f"[diff truncated at {gh_client.MAX_DIFF_CHARS} chars]")


def test_run_read_nonzero_exit_raises():
    with patch.object(gh_client, "check_gh_version", return_value=(2, 55, 0)), \
         patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run",
                      return_value=_completed(stderr="not found", returncode=1)):
        with pytest.raises(AgenticError):
            run_read("pr_view", "owner/repo", number=999)


def test_run_read_bad_json_raises():
    with patch.object(gh_client, "check_gh_version", return_value=(2, 55, 0)), \
         patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run", return_value=_completed(stdout="not json")):
        with pytest.raises(AgenticError):
            run_read("pr_list", "owner/repo")


# --- transient retry -------------------------------------------------------

def test_is_transient_gh_error_classifies():
    assert _is_transient_gh_error("error: HTTP 503 Service Unavailable")
    assert _is_transient_gh_error("dial tcp: i/o timeout")
    assert _is_transient_gh_error("API rate limit exceeded")
    # gh's 404 message and auth/usage errors must NOT be treated as transient.
    assert not _is_transient_gh_error("Could not resolve to a PullRequest (not found)")
    assert not _is_transient_gh_error("unknown flag: --bogus")
    assert not _is_transient_gh_error("")


def test_run_read_retries_transient_then_succeeds():
    transient = _completed(stderr="error: HTTP 503 Service Unavailable", returncode=1)
    success = _completed(stdout='{"number": 1}', returncode=0)
    with patch.object(gh_client, "check_gh_version", return_value=(2, 55, 0)), \
         patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run", side_effect=[transient, success]) as mrun, \
         patch.object(gh_client.time, "sleep"):
        out = run_read("pr_view", "owner/repo", number=1, retries=2, retry_backoff_sec=0)
    assert out["data"]["number"] == 1
    assert mrun.call_count == 2  # one transient retry, then success


def test_run_read_does_not_retry_nontransient():
    # A 404 ("could not resolve to a PR") is deterministic -> single attempt, raises.
    notfound = _completed(stderr="Could not resolve to a PullRequest (not found)", returncode=1)
    with patch.object(gh_client, "check_gh_version", return_value=(2, 55, 0)), \
         patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run", return_value=notfound) as mrun, \
         patch.object(gh_client.time, "sleep"):
        with pytest.raises(AgenticError):
            run_read("pr_view", "owner/repo", number=999, retries=3, retry_backoff_sec=0)
    assert mrun.call_count == 1  # never retried


def test_run_read_retries_on_timeout_then_succeeds():
    success = _completed(stdout='{"number": 2}', returncode=0)
    with patch.object(gh_client, "check_gh_version", return_value=(2, 55, 0)), \
         patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run",
                      side_effect=[subprocess.TimeoutExpired(cmd="gh", timeout=1), success]) as mrun, \
         patch.object(gh_client.time, "sleep"):
        out = run_read("pr_view", "owner/repo", number=1, retries=1, retry_backoff_sec=0)
    assert out["data"]["number"] == 2
    assert mrun.call_count == 2


def test_run_read_timeout_exhausted_raises():
    with patch.object(gh_client, "check_gh_version", return_value=(2, 55, 0)), \
         patch.object(gh_client.shutil, "which", return_value="/usr/bin/gh"), \
         patch.object(gh_client.subprocess, "run",
                      side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=1)), \
         patch.object(gh_client.time, "sleep"):
        with pytest.raises(AgenticError):
            run_read("pr_view", "owner/repo", number=1, retries=1, retry_backoff_sec=0)
