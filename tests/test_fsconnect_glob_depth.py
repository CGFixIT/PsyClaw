"""fs_glob must bound its own traversal depth, not just its result count.

_MAX_GLOB_MATCHES caps RESULTS; it never bounded the recursion. _walk recursed
once per directory level, so a tree deeper than the interpreter's frame limit
raised RecursionError out of FsClient.fs_glob -- not an FsConnectError, so
agentic/fsconnect/cli.py never caught it and the CLI exited 1, outside its
documented 0/2/3/4 contract. No symlink is needed to build such a tree, so
pathsafe's O_NOFOLLOW containment does not apply here.

These tests prove the bound directly (by observing how deep the walk actually
descends) rather than by materializing a tree past the frame limit -- such a
tree exceeds PATH_MAX on Linux and makes the fixture, not the code, the thing
under test.

POSIX-only, same as the rest of the fsconnect client suite.
"""

from __future__ import annotations

import os

import pytest
import yaml

from agentic.fsconnect.client import _MAX_GLOB_DEPTH, FsClient
from agentic.fsconnect.config import load_fsconnect_config
from utils.logger import _get_config, reset_config_cache

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX fixtures")


@pytest.fixture(autouse=True)
def _reset():
    reset_config_cache()
    yield
    reset_config_cache()


def _env(tmp_path, depth: int):
    """A share holding one chain of `depth` nested dirs plus a top-level file.

    A marker file sits at the very bottom, so a walk that reaches it is
    distinguishable from one that stopped early.
    """
    share = tmp_path / "share"
    share.mkdir(parents=True)
    leaf = share
    for level in range(depth):
        leaf = leaf / f"d{level}"
    leaf.mkdir(parents=True)
    (leaf / "deep.txt").write_text("bottom\n", encoding="utf-8")
    (share / "top.txt").write_text("top\n", encoding="utf-8")

    cfg_doc = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": []}, "privacy": {}},
        "fsconnect": {"enabled": True, "allowed_roots": [str(share)]},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    return _get_config(str(cfg_path)), load_fsconnect_config(str(cfg_path)), str(cfg_path)


def test_traversal_never_descends_past_the_depth_cap(tmp_path):
    """The regression proof: recursion is bounded no matter how deep the tree.

    Spies on the pathsafe descent and records the deepest relative path it is
    ever asked to enumerate. Unbounded recursion is what made RecursionError
    reachable; a hard ceiling on observed depth is what rules it out.
    """
    cfg, fs_cfg, cp = _env(tmp_path, _MAX_GLOB_DEPTH + 40)
    seen_depths: list[int] = []

    with FsClient(cfg, fs_cfg, config_path=cp) as c:
        real_list_dir = c._roots.list_dir

        def _spy(rel, *args, **kwargs):
            seen_depths.append(len(rel.split("/")) if rel else 0)
            return real_list_dir(rel, *args, **kwargs)

        c._roots.list_dir = _spy
        res = c.fs_glob("", "*.txt")

    assert seen_depths, "the walk never ran"
    assert max(seen_depths) <= _MAX_GLOB_DEPTH
    assert res["truncated"] is True, "an un-walked subtree must be reported, not hidden"


def test_glob_reports_truncated_when_the_depth_cap_engages(tmp_path):
    cfg, fs_cfg, cp = _env(tmp_path, _MAX_GLOB_DEPTH + 5)
    with FsClient(cfg, fs_cfg, config_path=cp) as c:
        res = c.fs_glob("", "*.txt")
    assert res["truncated"] is True
    # The shallow file is found; the one past the cap is not.
    assert [m["path"] for m in res["matches"]] == ["top.txt"]


def test_glob_walks_a_tree_just_inside_the_cap_completely(tmp_path):
    """The bound must not clip legitimately deep-but-reasonable layouts."""
    cfg, fs_cfg, cp = _env(tmp_path, _MAX_GLOB_DEPTH - 1)
    with FsClient(cfg, fs_cfg, config_path=cp) as c:
        res = c.fs_glob("", "*.txt")
    paths = [m["path"] for m in res["matches"]]
    assert "top.txt" in paths
    assert any(p.endswith("deep.txt") for p in paths), "the bottom marker should be reached"
    assert res["truncated"] is False


def test_depth_cap_does_not_discard_siblings_of_a_deep_branch(tmp_path):
    """One pathological branch must not abort enumeration of the rest."""
    cfg, fs_cfg, cp = _env(tmp_path, _MAX_GLOB_DEPTH + 5)
    share = tmp_path / "share"
    (share / "sibling").mkdir()
    (share / "sibling" / "found.txt").write_text("x", encoding="utf-8")

    with FsClient(cfg, fs_cfg, config_path=cp) as c:
        res = c.fs_glob("", "*.txt")
    paths = {m["path"] for m in res["matches"]}
    assert "sibling/found.txt" in paths
    assert "top.txt" in paths
    assert res["truncated"] is True
