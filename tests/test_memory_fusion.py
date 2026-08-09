"""Unit tests for memory retrieval fusion."""

from __future__ import annotations

from pathlib import Path

import pytest

from memory.retrieval_adapter import fuse_memory_hits
from memory.store import apply_proposal, create_proposal
from retrieval.hybrid_search import SearchResult


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
            "facts": {"enabled": True, "max_content_chars": 8192},
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
