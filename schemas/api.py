"""Pydantic models for the CyClaw FastAPI gateway.

Covers query request/response, source info, health, and soul evolution.

Hardened in feature/CyClaw-Agent: strict=True + extra='forbid' on all models
(prevents silent data injection or unexpected fields in agentic flows).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class QueryRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    # min_length=1 rejects empty queries at the schema boundary (HTTP 422)
    # before any retrieval/LLM work is done. max_length is an independent hard
    # DoS backstop: the configurable injection-filter length cap
    # (policy.prompt_filter.max_input_chars, default 4000) is bypassed entirely
    # when prompt_filter.enabled is false, so without a schema bound an operator
    # who disables the filter would let a multi-MB query flow straight into
    # retrieval + the LLM prompt. 65536 is far above any sane query yet caps the
    # hot path regardless of filter state (mirrors the new_soul/body limits below).
    query: str = Field(min_length=1, max_length=65536)
    user_confirmed_online: bool | None = None
    online_provider: Literal["grok", "claude"] | None = None

class SourceInfo(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    source: str
    score: float
    chunk_id: int
    source_sha256: str = ""
    stem_tags: list[str] = []
    semantic_score: float | None = None
    semantic_rank: int | None = None
    keyword_score: float | None = None
    keyword_rank: int | None = None
    rrf_score: float | None = None
    rrf_semantic_contrib: float | None = None
    rrf_keyword_contrib: float | None = None

class QueryResponse(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    answer: str
    sources: list[SourceInfo]
    retrieval_mode: str
    hit_count: int
    model_used: str
    needs_confirm: bool = False
    confirm_message: str | None = None
    error: str | None = None

class HealthResponse(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    status: str
    services: dict
    index_ready: bool
    graph_ready: bool
    mode: str  # app.mode ("offline" | "hybrid") — surfaced for the console mode badge
    # Server-side /query deadline (api.graph_timeout_sec). Surfaced so the web
    # console can bound its own fetch ABOVE this value — otherwise the browser
    # aborts first and hides the server's truthful 504 GRAPH_TIMEOUT message.
    # Defaulted so existing HealthResponse constructions stay valid.
    graph_timeout_sec: int = 330
    # Installed package version (importlib.metadata; "dev" when not installed).
    # The console footer renders `cyclaw v{version}`; without this field the
    # UI's data.version read is always undefined and the version never shows.
    version: str = "dev"

class SoulEvolutionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    # new_soul is prepended to EVERY LLM system prompt, so an oversized soul
    # inflates every query and can re-trigger the Ollama "0% processing" stall.
    # 8192 is the HTTP hard ceiling (DoS backstop); personality.soul_max_chars
    # (default 8000) is the operational cap enforced in utils/personality.py.
    new_soul: str = Field(min_length=1, max_length=8192)
    reason: str = Field(min_length=1, max_length=4096)


# --- Ops console request models -------------------------------------------------
# These back the terminal console's Sync + Agentic panels (/ops/sync, /ops/agentic).
# action is a closed Literal so an unknown verb is rejected at the schema boundary
# (HTTP 422) before any subprocess is spawned; extra='forbid' + strict=True block
# silent field injection. The gateway never imports sync/ or agentic/ — it shells
# out via utils.ops_runner — so these models are the only typed contract crossing
# the out-of-band boundary.
class OpsSyncRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal["status", "test", "sync", "schedule", "unschedule"]
    dry_run: bool = False


class OpsAgenticRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal["status", "test", "context", "propose-skill", "apply-skill"]
    # context selectors
    pr: int | None = Field(default=None, ge=1)
    issue: int | None = Field(default=None, ge=1)
    no_diff: bool = False
    # skills-registry fields (propose-skill / apply-skill)
    name: str | None = Field(default=None, max_length=128)
    desc: str | None = Field(default=None, max_length=512)
    body: str | None = Field(default=None, max_length=65536)
    reason: str | None = Field(default=None, max_length=4096)
    confirm: bool = False


# --- Filesystem connector (out-of-band, /ops/fsconnect) ----------------------
# Mirrors the fsconnect CLI subcommands. Read ops require root+path; write ops
# additionally need reason and body. action is a closed Literal so unknown verbs
# are rejected at the schema boundary (HTTP 422).
class OpsFsConnectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal["status", "test", "list", "read", "stat", "grep", "glob"]
    root: str | None = Field(default=None, max_length=1024)
    path: str | None = Field(default=None, max_length=4096)
    pattern: str | None = Field(default=None, max_length=1024)
    # Browser/API grep is literal-only. Regex grep remains available from the
    # local CLI where an operator can kill a pathological pattern without tying
    # up the FastAPI worker.
    regex: Literal[False] = False
    recursive: bool = True


# --- SQL connector (out-of-band, /ops/sqlconnect) -----------------------------
# Mirrors the sqlconnect CLI subcommands. Read-only by construction. action is a
# closed Literal; sql is capped to prevent oversized payloads.
class OpsSqlConnectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal["status", "test", "schema", "query"]
    sql: str | None = Field(default=None, max_length=65536)
    table: str | None = Field(default=None, max_length=256)
    explain: bool = False
    count: bool = False
    fmt: Literal["json", "csv"] = "json"
