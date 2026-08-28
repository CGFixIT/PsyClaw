"""Unit tests for memory retrieval fusion."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from memory import flags as memory_flags
from memory.retrieval_adapter import fuse_memory_hits
from memory.store import apply_proposal, create_proposal
from retrieval.hybrid_search import HybridRetriever, SearchResult


def _corpus() -> list[SearchResult]:
    return [
        SearchResult(
            text="corpus about kubernetes",
            score=0.04,
            source="doc.md",
            chunk_id=1,
            stem_tags=[],
            retrieval_mode="hybrid",
            rrf_score=0.04,
        )
    ]


@pytest.fixture
def mem_on(tmp_path: Path) -> dict:
    cfg = {
        "memory": {
            "enabled": True,
            "db_path": str(tmp_path / "m.db"),
            "facts": {"retrieval_enabled": True, "max_content_chars": 8192},
            "episodes": {"enabled": False},
            "retrieval_fusion": {
                "enabled": True,
                "max_hits": 3,
                "rrf_k": 60,
                "source_prefix": "memory:fact:",
            },
            "propose_apply": {"enabled": True},
            "export_html": {"enabled": False},
            "consolidation": {"enabled": False},
        },
        "policy": {
            "prompt_filter": {"banned_patterns": []},
            "privacy": {"redact_emails": False, "redact_ips": False, "redact_secrets_like": []},
        },
    }
    prop = create_proposal(
        cfg,
        "add_fact",
        {"content": "User runs CyClaw on MacBook Pro M5", "category": "hw"},
        reason="seed",
    )
    apply_proposal(cfg, prop.id, reason="seed apply")
    return cfg


def test_fusion_disabled_is_identity(mem_on):
    off = dict(mem_on)
    off["memory"] = {**mem_on["memory"], "enabled": False}
    corpus = _corpus()
    assert fuse_memory_hits("MacBook", corpus, off) == corpus


def test_fusion_adds_memory_hits(mem_on):
    corpus = _corpus()
    fused = fuse_memory_hits("MacBook Pro", corpus, mem_on)
    mem = [h for h in fused if h.retrieval_mode == "memory"]
    assert len(mem) >= 1
    assert mem[0].source.startswith("memory:fact:")
    assert mem[0].score == pytest.approx(1.0 / 60.0)  # rank 0 → 1/(60+0)
    # corpus hit preserved
    assert any(h.source == "doc.md" for h in fused)


def test_fusion_caps_hits(mem_on, tmp_path):
    # add more facts
    for i in range(5):
        p = create_proposal(
            mem_on,
            "add_fact",
            {"content": f"MacBook fact number {i} unique token tok{i}"},
            reason=f"r{i}",
        )
        apply_proposal(mem_on, p.id, reason=f"a{i}")
    fused = fuse_memory_hits("MacBook", _corpus(), mem_on)
    mem = [h for h in fused if h.retrieval_mode == "memory"]
    assert len(mem) <= 3


# -- the facts flag itself: renamed key + legacy fallback -----------------------
#
# Before this suite existed, NO test asserted that the facts flag gates fusion at
# all -- test_fusion_disabled_is_identity flips the MASTER switch instead. The
# gate is duplicated (memory/retrieval_adapter.py and the pre-check in
# retrieval/hybrid_search.py), so a change that updated only one of them passed
# CI green. These drive both paths.


def _facts(mem_on: dict, block: dict | None) -> dict:
    """Copy the fixture config with `facts` replaced (or removed if None)."""
    memory = {**mem_on["memory"]}
    if block is None:
        memory.pop("facts", None)
    else:
        memory["facts"] = block
    return {**mem_on, "memory": memory}


@pytest.fixture(autouse=True)
def _reset_legacy_warning():
    """The legacy-key warning is once-per-process; reset it per test."""
    memory_flags._warn_legacy_key.cache_clear()


@pytest.mark.parametrize(
    ("block", "fuses"),
    [
        ({"retrieval_enabled": True}, True),      # new key on
        ({"retrieval_enabled": False}, False),    # new key off
        ({"enabled": True}, True),                # legacy key still honored
        ({"enabled": False}, False),              # legacy key off
        ({"retrieval_enabled": True, "enabled": False}, True),   # new wins
        ({"retrieval_enabled": False, "enabled": True}, False),  # new wins
        ({}, False),                              # neither key present
        (None, False),                            # no facts block at all
    ],
)
def test_facts_flag_gates_fusion(mem_on, block, fuses):
    cfg = _facts(mem_on, block)
    fused = fuse_memory_hits("MacBook Pro", _corpus(), cfg)
    got = any(h.retrieval_mode == "memory" for h in fused)
    assert got is fuses


def test_legacy_facts_key_warns_once(mem_on, caplog):
    """A stale config keeps working, but says so -- once, naming both keys."""
    cfg = _facts(mem_on, {"enabled": True})
    with caplog.at_level(logging.WARNING, logger="cyclaw.memory.flags"):
        fuse_memory_hits("MacBook Pro", _corpus(), cfg)
        fuse_memory_hits("MacBook Pro", _corpus(), cfg)

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1, "legacy-key warning must fire once per process"
    assert "memory.facts.enabled" in warnings[0].getMessage()
    assert "memory.facts.retrieval_enabled" in warnings[0].getMessage()


def test_new_key_does_not_warn(mem_on, caplog):
    with caplog.at_level(logging.WARNING, logger="cyclaw.memory.flags"):
        fuse_memory_hits("MacBook Pro", _corpus(), _facts(mem_on, {"retrieval_enabled": True}))
    assert not [r for r in caplog.records if r.levelno == logging.WARNING]


@pytest.mark.parametrize(
    ("block", "fuses"),
    [
        ({"retrieval_enabled": True}, True),
        ({"enabled": True}, True),
        ({"retrieval_enabled": False}, False),
        ({"enabled": False}, False),
    ],
)
def test_hybrid_search_pre_check_honors_both_keys(mem_on, block, fuses):
    """The duplicated gate in HybridRetriever must agree with the adapter's.

    Calls _maybe_fuse_memory directly rather than through a real retriever: the
    method only touches self.cfg, so an unbound call proves the gate without
    standing up ChromaDB.
    """
    cfg = _facts(mem_on, block)
    retriever = SimpleNamespace(cfg=cfg)
    fused = HybridRetriever._maybe_fuse_memory(retriever, "MacBook Pro", _corpus())
    got = any(h.retrieval_mode == "memory" for h in fused)
    assert got is fuses
