"""Dataclasses for the memory subsystem. No I/O."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ProposalAction = Literal["add_fact", "update_fact", "deactivate_fact"]
ProposalStatus = Literal["pending", "applied", "rejected"]


@dataclass(slots=True)
class Fact:
    id: int
    content: str
    category: str = "general"
    tags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    source: str = "human"
    active: bool = True
    created_at: str = ""
    updated_at: str = ""
    applied_reason: str = ""
    content_sha256: str = ""


@dataclass(slots=True)
class Episode:
    id: int
    query_hash: str
    answer_summary: str = ""
    model_used: str = ""
    top_score: float | None = None
    retrieval_mode: str | None = None
    hit_count: int | None = None
    source_tag: str = "query"
    created_at: str = ""
    raw_query: str | None = None


@dataclass(slots=True)
class MemoryProposal:
    id: int
    action: ProposalAction
    payload: dict[str, Any]
    reason: str
    status: ProposalStatus = "pending"
    injection_flags: list[str] = field(default_factory=list)
    created_at: str = ""
    resolved_at: str | None = None
    resolved_reason: str | None = None
