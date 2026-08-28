# CyClaw Memory Foundation — Implementation Plan

| Field | Value |
|---|---|
| **Status** | IMPLEMENTED on `feature/memory-foundation` (draft PR) |
| **Repo** | `github.com/CGFixIT/CyClaw` |
| **Base branch** | `main` |
| **Feature branch** | `feature/memory-foundation` |
| **HEAD verified** | `010e9b4533b8c589933bf02f0327a108a605dbc9` (`010e9b4 fix(llm): reject non-finite retry delays (#845)`) |
| **Prompt SHA (stale)** | `be6dbfef0a67241cd64b4f75770bab6055e1e937` — **do not use** |
| **FEATURE FREEZE** | In force (CLAUDE.md §1). This plan is net-new; user task is the explicit override. |
| **Authority** | running code > `config.yaml` > this doc > prompt assumptions |

---

## 0. Bottom line

CyClaw has **identity** (`soul.md` + `PersonalityManager` + `interactions` hash log) and **zero** user/session memory (no facts, no episodes, no retrieval-integrated recall). This plan adds an optional, default-off `memory/` package with SQLite+FTS5 storage, propose/apply governance (mirroring soul), non-fatal episode staging at `audit_logger_node`, and optional RRF fusion inside `HybridRetriever.hybrid_search` **without changing its public signature**.

**`docs/memories/` is out of scope and is not this feature.** It holds sandbox audit notes / zOld material. All memory-subsystem docs live under **`docs/memory/`** (singular).

---

## 1. Verified baseline (what actually exists)

### 1.1 HEAD and tree

```
010e9b4533b8c589933bf02f0327a108a605dbc9
010e9b4 fix(llm): reject non-finite retry delays (#845)
```

No `memory/` package. No top-level `memory:` block in `config.yaml`. FTS5 is available in the sandbox SQLite (`ENABLE_FTS5` in `pragma compile_options`).

### 1.2 Identity ≠ memory (confirmed gap)

| Surface | Role today | Memory? |
|---|---|---|
| `data/personality/soul.md` | Identity statement prepended to LLM prompts | No |
| `PersonalityManager` (`utils/personality.py`) | Soul load/propose/apply/reload/restore + interaction hash log | No facts/episodes |
| `personality_db` tables `soul_versions`, `interactions` | Version history + `(query_hash, outcome, timestamp)` | Outcome strings only; no free-text recall |
| `audit_logger_node` | JSONL audit via `audit_log(event)` | Hashes query; no episode store |
| `HybridRetriever.hybrid_search` | Chroma + BM25 + RRF → `list[SearchResult]` | Corpus only |
| Sanitizer / soul patterns | Already include memory-poisoning banned patterns | Defense exists; no memory write path |

### 1.3 Live signatures (quoted — do not invent)

**Auth (gate.py ~99–123)** — name is `require_api_key`, fail-closed if `CYCLAW_API_KEY` unset:

```python
def require_api_key(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer_scheme),
):
    api_key = os.environ.get("CYCLAW_API_KEY", "")
    if not api_key:
        raise HTTPException(status_code=401,
                            detail="Soul mutation disabled: CYCLAW_API_KEY not set")
    if not credentials or not hmac.compare_digest(
        credentials.credentials.encode("utf-8"), api_key.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
```

Note: `utils/auth.py` has a **second** `require_api_key` for the harness. Memory admin routes must use **gate.py’s** dependency (injected like ops), not the harness copy.

**Soul routes live in `gate.py` (745–806), NOT `gate_ops.py`.**  
`gate_ops.py` is only `/ops/*` via `register_ops_routes(...)`.  
`gate_auth.py` is `/auth/*` via `register_auth_routes(...)`.

**Query handler** (`gate.py` 604–605):

```python
@app.post("/query", response_model=QueryResponse)
async def query_endpoint(request: Request, req: QueryRequest):
```

**Audit node** (`graph.py` 689–746):

```python
def audit_logger_node(state: GraphState, cfg: dict,
                      personality: PersonalityManager | None = None) -> dict:
    ...
    if personality and state.get("answer_model"):
        try:
            query_hash = hash_query(query)
            outcome = (
                f"{state.get('answer_model', 'unknown')}"
                f"|score={state.get('top_score', 0.0):.4f}"
                f"|hits={len(state.get('retrieved_docs', []))}"
            )
            personality.record_interaction(query_hash, outcome)
        except Exception as exc:
            logger.error("personality.record_interaction failed (non-fatal)", exc_info=True)
            event["personality_db_error"] = str(exc)

    audit_log(event)
    return {"audit_event": event}
```

**Retrieve** (`graph.py` 286–291):

```python
def retrieve_node(state: GraphState, retriever: HybridRetriever, cfg: dict) -> dict:
    query = state["query"]
    try:
        results = retriever.hybrid_search(query)
```

**Hybrid search public API** (`retrieval/hybrid_search.py` 47–63, 304):

```python
@dataclass
class SearchResult:
    text: str
    score: float
    source: str
    chunk_id: int
    stem_tags: list[str]
    retrieval_mode: str  # "semantic" | "keyword" | "hybrid"
    source_sha256: str = ""
    semantic_score: float | None = None
    ...
    rrf_keyword_contrib: float | None = None

def hybrid_search(self, query: str) -> list[SearchResult]:
```

**MUST NOT change** the `hybrid_search(self, query: str) -> list[SearchResult]` signature. MCP calls `retriever.hybrid_search(query)[:top_k]` (`mcp_hybrid_server.py` ~145).

**Soul propose/apply** (`utils/personality.py`):

```python
def propose_evolution(self, new_soul: str, reason: str) -> dict: ...
def apply_evolution(self, new_soul: str, reason: str, *, scan: bool = True) -> dict: ...
```

`apply_evolution` enforces non-empty `reason` (`ValueError`) and injection scan before write.

**Soul request schema** (`schemas/api.py` 80–87):

```python
class SoulEvolutionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    new_soul: str = Field(min_length=1, max_length=8192)
    reason: str = Field(min_length=1, max_length=4096)
```

**personality_db.connect** (`utils/personality_db.py`):

```python
def connect(db_path: Path, pers_cfg: dict) -> tuple[Any, str, str]:
    # returns (conn, placeholder, backend)  # "?" sqlite / "%s" postgres
```

SQLite path: owner-only create, harden to `0o600`. **WAL is not currently set** in personality_db; memory store will enable WAL explicitly (documented deviation / improvement for concurrent retrieval reads).

**Ops registration pattern** (`gate_ops.py` ~82+; called from `gate.py` ~852):

```python
def register_ops_routes(
    app: FastAPI,
    cfg: dict[str, Any],
    audit: Callable[[dict[str, Any]], Awaitable[None]],
    enforce_rate_limit: Callable[[Request], Awaitable[None]],
    sanitize_error: Callable[[Exception], str],
    require_api_key: Callable[..., Any],
) -> None:
```

**Audit log schema** (`utils/logger.py` `audit_log`):

- Accepts `event: dict`
- If `"query"` present → pop and set `query_hash = SHA256`
- Redacts all other string/nested values per privacy config
- Adds `timestamp` ISO-8601 UTC
- Never mutates caller dict (shallow copy)
- Write failures are non-fatal (warning only)

**I1–I6 (live invariant-guard numbering)** — follow this, not the prompt’s swapped I3/I4 labels:

| # | Name | Meaning |
|---|---|---|
| **I1** | RAG-first | `set_entry_point("retrieve")` only |
| **I2** | Topology = policy | routing only via named routers |
| **I3** | Triple-gated external | hybrid + enabled + user_confirmed (+ client) |
| **I4** | Audit convergence | all paths → `audit_logger` → END |
| **I5** | Soul governance | human reason + scan on apply |
| **I6** | Module isolation | agentic/sync/guardrails/harness/telegram never meet gate/graph/mcp |

### 1.4 Prompt assumption corrections

| Prompt said | Live truth | Plan decision |
|---|---|---|
| Soul admin in `gate_ops.py` | Soul is in **`gate.py`**; `gate_ops` = `/ops/*` only | Memory admin → **new `gate_memory.py`** + `register_memory_routes` (same shape as ops/auth). Not stuffed into ops. |
| Auth = `require_bearer` | **`require_api_key`** | Use gate’s `require_api_key` |
| Memory endpoints in gate_ops | Would couple core memory admin with OOB subprocess surface | **Reject.** Use `gate_memory.py`. |
| `docs/memories/` | Sandbox notes / zOld | **Ignore.** Docs path = `docs/memory/` |
| I3 = audit, I4 = triple-gate | Live: I3 = triple-gate, I4 = audit | Follow live `INVARIANTS.md` / invariant-guard |

---

## 2. Goals and non-goals

### 2.1 Goals (this PR)

1. **Facts** — durable key/value-ish statements with provenance, confidence, tags, active flag.
2. **Episodes** — per-query interaction records (query hash + answer summary + model + scores), staged non-fatally from `audit_logger_node`.
3. **Propose/apply** — human-gated writes for facts (and optional episode annotations), mirroring soul I5 pattern.
4. **Retrieval fusion (optional)** — when enabled, FTS5 memory hits fuse into hybrid RRF results as additional `SearchResult`s with distinct `source` / `retrieval_mode` markers.
5. **Admin HTTP API** — status, list, propose, apply, reject, delete — Bearer + non-empty reason on mutating verbs.
6. **Export** — `GET /query/export/html` (or POST with last-query context) for a local HTML transcript dump; default off / auth-gated as specified below.
7. **Default off** — every memory switch false; zero behavior change when disabled.
8. **No new third-party deps** — stdlib `sqlite3` + FTS5 only.
9. **Tests** — `memory/selftest.py`, `tests/test_memory_isolation.py`, unit tests for store/policy/fusion; existing suite green.

### 2.2 Explicit non-goals (out of scope)

- Apple Notes / external note sync
- Web search as memory source
- Telegram integration beyond an optional `source` tag string on episodes
- Procedural memory UI / skill learning
- Consolidation job activation (stub only)
- Postgres backend for memory (SQLite-only v1; personality’s dual-backend is not cloned yet)
- Changing `hybrid_search` signature or MCP tool schema
- Auto-write of facts from untrusted RAG/LLM output (zombie-agent defense)
- Modifying `telegram/` or `agentic/` internals
- Touching soul write-path semantics
- Promoting anything under `docs/memories/` into the subsystem

---

## 3. Architecture

### 3.1 Package layout

```
memory/
  __init__.py          # public facade; no side effects at import
  models.py            # dataclasses / TypedDicts (Fact, Episode, MemoryProposal)
  store.py             # SQLite schema, CRUD, FTS5, WAL, 0600
  policy.py            # injection scan, reason gate, size caps, trust tiers
  retrieval_adapter.py # FTS query → list[SearchResult]-compatible hits
  mirror.py            # optional read-only projection helpers (status/export)
  consolidation.py     # STUB only (no-op + log); never auto-runs
  selftest.py          # runnable: python -m memory.selftest
gate_memory.py         # register_memory_routes(...) — HTTP surface
docs/memory/
  IMPLEMENTATION_PLAN.md   # this file
  README.md                # operator-facing (short; written at implement time)
```

### 3.2 Data model (SQLite)

Path default: `data/memory/cyclaw_memory.db` (config `memory.db_path`).  
Create parent dir; file mode `0o600`; `PRAGMA journal_mode=WAL`; `PRAGMA foreign_keys=ON`; `PRAGMA busy_timeout=5000`.

```sql
-- facts: durable operator-approved knowledge
CREATE TABLE IF NOT EXISTS facts (
  id            INTEGER PRIMARY KEY,
  content       TEXT NOT NULL CHECK(length(content) > 0 AND length(content) <= 8192),
  category      TEXT NOT NULL DEFAULT 'general',
  tags_json     TEXT NOT NULL DEFAULT '[]',
  confidence    REAL NOT NULL DEFAULT 1.0 CHECK(confidence >= 0.0 AND confidence <= 1.0),
  source        TEXT NOT NULL DEFAULT 'human',  -- human | import | system
  active        INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  applied_reason TEXT NOT NULL DEFAULT '',
  content_sha256 TEXT NOT NULL
);

-- episodes: query-path staging (hashed query; no raw query by default)
CREATE TABLE IF NOT EXISTS episodes (
  id            INTEGER PRIMARY KEY,
  query_hash    TEXT NOT NULL,
  answer_summary TEXT NOT NULL DEFAULT '',  -- redacted/truncated; cap length
  model_used    TEXT NOT NULL DEFAULT '',
  top_score     REAL,
  retrieval_mode TEXT,
  hit_count     INTEGER,
  source_tag    TEXT NOT NULL DEFAULT 'query',  -- query | telegram | import
  created_at    TEXT NOT NULL
);

-- proposals: I5-style human gate for fact mutations
CREATE TABLE IF NOT EXISTS memory_proposals (
  id            INTEGER PRIMARY KEY,
  action        TEXT NOT NULL CHECK(action IN ('add_fact','update_fact','deactivate_fact')),
  payload_json  TEXT NOT NULL,
  reason        TEXT NOT NULL,
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK(status IN ('pending','applied','rejected')),
  injection_flags_json TEXT NOT NULL DEFAULT '[]',
  created_at    TEXT NOT NULL,
  resolved_at   TEXT,
  resolved_reason TEXT
);

-- FTS5 content-sync for facts (active facts only via triggers or app-level rebuild)
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
  content,
  category,
  tags,
  content='facts',
  content_rowid='id'
);

-- triggers: keep FTS in sync on insert/update/delete of facts
-- (exact SQL in store.py; selftest verifies round-trip)

CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(active);
CREATE INDEX IF NOT EXISTS idx_episodes_query_hash ON episodes(query_hash);
CREATE INDEX IF NOT EXISTS idx_episodes_created ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON memory_proposals(status);
```

**Privacy default:** episodes store `query_hash` (same `hash_query` as audit), not raw query. Optional config `memory.episodes.store_raw_query: false` (default false). If ever true, raw query must pass through `redact_sensitive` before write.

### 3.3 Config block (append after `personality:`; all defaults false / safe)

```yaml
memory:
  enabled: false                    # master switch
  db_path: "data/memory/cyclaw_memory.db"
  facts:
    retrieval_enabled: false        # fusion exposure ONLY; not persist/apply/read
    max_content_chars: 8192
    max_active: 10000
  episodes:
    enabled: false
    store_raw_query: false
    max_answer_summary_chars: 2000
    ttl_days: 365
    prune_every: 100
  retrieval_fusion:
    enabled: false                  # fuse FTS hits into hybrid_search
    max_hits: 3
    rrf_k: 60                       # match corpus RRF k
    source_prefix: "memory:fact:"   # SearchResult.source prefix
  propose_apply:
    enabled: false                  # admin propose/apply routes useful only if true
  export_html:
    enabled: false
  consolidation:
    enabled: false                  # stub; must stay false
```

Boot validation (optional small `validate_memory_config` in `utils/config_validation.py`): types/ranges only; missing block = all off.

### 3.4 Trust model (agent-security)

Aligned with zombie-agent / memory-poisoning defense:

| Write path | Allowed? | Gate |
|---|---|---|
| Human `POST /memory/propose` + `POST /memory/apply` with reason | Yes | API key + reason + injection scan |
| Episode auto-stage from `audit_logger_node` | Yes (metadata only) | Config `episodes.enabled`; never promotes to fact |
| Auto-extract facts from LLM answer / RAG chunk | **No** | Not implemented |
| Consolidation promoting episodes → facts | **No** | Stub only |
| Import without propose/apply | **No** in v1 | |

Injection scan on fact content reuses the same critical pattern family as soul (`ENFORCED_SOUL_PATTERNS` / config `policy.prompt_filter.banned_patterns`) via `memory/policy.py` — either import the pattern lists carefully without creating cycles, or compile from `cfg` the same way `PersonalityManager._build_patterns` does. Prefer **cfg-driven compile** to avoid importing `utils.personality` from memory (keeps identity and memory separable).

---

## 4. Insertion points (literal)

### 4.1 `retrieval/hybrid_search.py` — fusion (lazy, signature-stable)

**Where:** end of `hybrid_search`, after corpus merge / single-path normalize, **before** `return`.

**How:**

```python
# at end of hybrid_search, after `merged` (or single-path list) is built:
results = merged  # or the single-path return value
try:
    mem_cfg = (self.cfg.get("memory") or {})
    fusion = (mem_cfg.get("retrieval_fusion") or {})
    if mem_cfg.get("enabled") is True and fusion.get("enabled") is True:
        from memory.retrieval_adapter import fuse_memory_hits  # lazy
        results = fuse_memory_hits(query, results, self.cfg)
except Exception:
    logger.exception("memory fusion failed (non-fatal)")
    # results unchanged
return results
```

**Rules:**

- No top-level `import memory` in `hybrid_search.py`.
- Do not change method signature.
- Memory hits become `SearchResult` with:
  - `source = f"{source_prefix}{fact_id}"` (e.g. `memory:fact:42`)
  - `chunk_id = fact_id` (stable int)
  - `retrieval_mode = "memory"` (extend comment on field; callers treat unknown modes as opaque strings today)
  - `stem_tags` may include `["memory", category, ...]`
  - `score` / `rrf_score` on RRF scale via `1/(rrf_k + rank)` so they remain comparable to `min_score` without blowing the gate open
- Fusion must **not** drop corpus hits; append/merge then re-sort by score.
- Cap at `retrieval_fusion.max_hits`.
- MCP benefits automatically (calls same `hybrid_search`) without importing `memory` itself.

### 4.2 `graph.py` — episode staging in `audit_logger_node`

**Where:** immediately after the existing personality `record_interaction` try/except block (~731–742), **before** `audit_log(event)`.

**How:**

```python
# Non-fatal episode staging (memory foundation). Mirrors personality block.
try:
    mem_cfg = (cfg.get("memory") or {})
    if (
        mem_cfg.get("enabled") is True
        and (mem_cfg.get("episodes") or {}).get("enabled") is True
        and state.get("answer_model")
    ):
        from memory.store import stage_episode  # lazy
        stage_episode(cfg, state)
except Exception as exc:
    logger.error("memory.stage_episode failed (non-fatal)", exc_info=True)
    event["memory_episode_error"] = str(exc)
```

**Rules:**

- No top-level `import memory` in `graph.py`.
- Never raise into the request path.
- Stamp errors on the audit event (like `personality_db_error`).
- Only when an answer was produced (`answer_model` truthy) — same as personality recording; pause path does not stage.
- `stage_episode` reads only fields already on `GraphState` / event; uses `hash_query` from `utils.logger`.

**I4:** still ends with `audit_log(event)`; memory failure cannot skip audit.

### 4.3 `gate_memory.py` — new module (preferred over gate_ops)

**Why not `gate_ops.py`:** ops is the OOB subprocess surface (I6). Memory admin is core-optional state mutation closer to `/soul/*`. Cloning the **registration injection** pattern from ops/auth without living inside ops.

```python
def register_memory_routes(
    app: FastAPI,
    cfg: dict[str, Any],
    audit: Callable[[dict[str, Any]], Awaitable[None]],
    enforce_rate_limit: Callable[[Request], Awaitable[None]],
    require_api_key: Callable[..., Any],
    # optional: memory_store factory / None when disabled
) -> None:
    ...
```

**Call site in `gate.py`:** after `register_ops_routes` / near `register_auth_routes` (~852–870):

```python
from gate_memory import register_memory_routes  # module import OK; gate_memory must NOT import memory at top level if disabled path matters for isolation tests — see §6
register_memory_routes(
    app,
    cfg=cfg,
    audit=_audit,
    enforce_rate_limit=_enforce_rate_limit,
    require_api_key=require_api_key,
)
```

**Isolation nuance:** `import gate_memory` is fine (like `gate_ops`). `gate_memory` must **lazy-import** `memory.*` inside handlers when `memory.enabled`, and return 404 when disabled — matching soul’s `if personality is None: 404`.

### 4.4 HTTP routes (all rate-limited + `require_api_key` unless noted)

| Method | Path | Auth | Reason required | Behavior when disabled |
|---|---|---|---|---|
| GET | `/memory/status` | API key | No | 404 or `{enabled: false}` — prefer **200 with enabled flags** so consoles can probe |
| GET | `/memory/facts` | API key | No | 404 if master off |
| GET | `/memory/episodes` | API key | No | 404 if master off |
| GET | `/memory/proposals` | API key | No | 404 if master/propose off |
| POST | `/memory/propose` | API key | **Yes** (body) | 404 if off |
| POST | `/memory/apply` | API key | **Yes** (body) | 404 if off |
| POST | `/memory/reject` | API key | **Yes** (body) | 404 if off |
| GET | `/query/export/html` | API key | No | 404 if `export_html.enabled` false |

**As implemented:** no dedicated `DELETE /memory/facts/{id}` route exists. Fact
deactivation is soft-only, reached exclusively through
`POST /memory/propose` (`action: "deactivate_fact"`) followed by
`POST /memory/apply` — the §13.4 "soft deactivate via propose/apply" default,
with the hard-DELETE alternative not built. See `gate_memory.py`.

Mutating bodies use Pydantic models in `schemas/api.py` (same `extra='forbid', strict=True`):

```python
class MemoryProposeRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal["add_fact", "update_fact", "deactivate_fact"]
    content: str | None = Field(default=None, max_length=8192)
    fact_id: int | None = Field(default=None, ge=1)
    category: str = Field(default="general", max_length=64)
    tags: list[str] = Field(default_factory=list, max_length=32)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    reason: str = Field(min_length=1, max_length=4096)

class MemoryApplyRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    proposal_id: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=4096)

class MemoryRejectRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    proposal_id: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=4096)
```

Server-side: empty/whitespace `reason` → 400 `INVALID_REASON` (mirror soul apply).  
Injection on content at **apply** (enforced) and advisory flags at **propose**.

Audit events (via injected `audit`):

- `memory_propose`, `memory_apply`, `memory_reject`, `memory_status`, `memory_export_html`
- On injection block: `memory_apply_injection_blocked`
- Never put raw fact content into audit if privacy redaction would strip it — prefer `content_sha256` + lengths; if content is logged, rely on `audit_log` redaction.

### 4.5 Export HTML

`GET /query/export/html` — **auth-gated**, config-gated:

- Renders a minimal static HTML page from recent episodes (and optional active facts summary).
- No external CDNs (offline-first).
- Escape all text (`html.escape`).
- Caps rows (e.g. last 100 episodes).
- Does not expose raw queries unless `store_raw_query` was true **and** operator explicitly enables export of raw (default: hashes only).

### 4.6 `config.yaml`

Insert full `memory:` block after `personality:` (~line 163 area). Defaults all off.

### 4.7 `.gitignore` / data dirs

Ensure `data/memory/` is gitignored if `data/personality/` pattern already covers `data/*` — verify existing ignore rules; add `data/memory/` if needed.

### 4.8 Not modified

- `telegram/**` internals  
- `agentic/**` internals  
- Soul apply/reload/restore semantics  
- Graph topology / routers / entry point  
- MCP tool list / sampling  
- `docs/memories/**`

---

## 5. Module responsibilities

### 5.1 `memory/models.py`

Dataclasses only:

- `Fact`, `Episode`, `MemoryProposal`
- No I/O

### 5.2 `memory/store.py`

- `connect(cfg) -> Connection` (sqlite only v1)
- Schema migrate/create idempotent
- `stage_episode(cfg, state: Mapping) -> None`
- `list_facts`, `get_fact`, `insert_fact`, `update_fact`, `deactivate_fact`
- `create_proposal`, `get_proposal`, `apply_proposal`, `reject_proposal`
- `search_facts_fts(query, limit) -> list[tuple[id, content, rank]]`
- Thread lock around writes (mirror personality `threading.Lock`)
- Prune episodes by TTL amortized (`prune_every`)

### 5.3 `memory/policy.py`

- `require_reason(reason: str) -> None`  # ValueError if blank
- `scan_content(content: str, cfg: dict) -> list[str]`  # matched pattern sources
- `enforce_content(content: str, cfg: dict) -> None`  # raises PromptInjectionError
- Size / tag count checks

### 5.4 `memory/retrieval_adapter.py`

- `fuse_memory_hits(query: str, corpus_hits: list[SearchResult], cfg: dict) -> list[SearchResult]`
- Imports `SearchResult` from `retrieval.hybrid_search` (memory → retrieval is OK; retrieval must only lazy-import memory)
- Opens store read-only path / shared connection carefully (WAL helps)

### 5.5 `memory/mirror.py`

- Status dict builder for `/memory/status`
- HTML export builder

### 5.6 `memory/consolidation.py`

```python
def run_consolidation(cfg: dict) -> dict:
    """Stub. Returns {'status': 'disabled'} unless explicitly extended later."""
    return {"status": "disabled", "reason": "consolidation not implemented"}
```

Never called from graph/gate unless config true **and** we still no-op in v1.

### 5.7 `memory/selftest.py`

Runnable offline:

```bash
python -m memory.selftest
```

Checks: schema create, propose/apply fact, FTS hit, episode stage, injection refuse, fusion pure function with fake corpus list, cleanup temp db.

---

## 6. Isolation test design (`tests/test_memory_isolation.py`)

Memory is **not** OOB like telegram. It is an optional core feature. Isolation means:

1. **No top-level import** of package `memory` in `gate.py`, `graph.py`, `mcp_hybrid_server.py` (AST — same helper pattern as `tests/test_telegram_isolation.py`).
2. **`gate_memory.py` may exist** and be imported by `gate.py`, but `gate_memory.py` itself must not top-level-import `memory` (lazy in handlers) — AST on `gate_memory.py` too.
3. **`retrieval/hybrid_search.py`** must not top-level-import `memory` (lazy inside `hybrid_search` only).
4. **Reverse:** `memory/` must not import `gate`, `gate_ops`, `gate_memory`, `graph`, `mcp_hybrid_server`, `telegram`, `agentic`, `sync`, `guardrails`, `harness`.
5. **Runtime (optional strengthening):** with default config (`memory.enabled` false), importing `graph` / loading `HybridRetriever` must not leave `memory` in `sys.modules` solely due to import side effects. (Lazy imports satisfy this if never called.)

**Do not** add `memory` to invariant-guard I6 forbidden OOB list — that would incorrectly ban intentional lazy hooks. Document in plan/PR that I6 OOB set is unchanged.

---

## 7. I1–I6 impact analysis

| Inv | Impact | Verdict |
|---|---|---|
| **I1 RAG-first** | Fusion runs *inside* retrieve’s retriever call, still after entry at `retrieve`. No pre-retrieve node. | **Preserved** |
| **I2 Topology=policy** | No new graph nodes, edges, or routers. | **Preserved** |
| **I3 Triple-gate** | No external client construction changes. | **Preserved** |
| **I4 Audit convergence** | Episode staging inside `audit_logger_node` before `audit_log`; failures caught; still → END. | **Preserved** |
| **I5 Soul governance** | Soul paths untouched. Memory gets **parallel** reason+scan gate on its own apply. Do not overload `apply_evolution`. | **Preserved** (+ analogous gate for memory) |
| **I6 Module isolation** | No imports of agentic/sync/guardrails/harness/telegram from core. Memory is not OOB; lazy hooks only. | **Preserved** |

**Sharp edges to not introduce:**

- Do not auto-apply facts from retrieved corpus text (indirect prompt injection → persistent memory).
- Do not stage episodes on user_gate pause (no answer).
- Do not let fusion raise and fail `/query`.
- Do not log raw queries into memory when privacy hashing is the audit norm.
- Do not put memory admin on unauthenticated routes.

---

## 8. Dependency-ordered implementation tasks

1. **Branch** `feature/memory-foundation` from verified HEAD `010e9b4`.
2. **Config** — add `memory:` block (all false) to `config.yaml`; optional `validate_memory_config`.
3. **Schemas** — `MemoryProposeRequest`, `MemoryApplyRequest`, `MemoryRejectRequest` in `schemas/api.py`.
4. **Package** — `memory/models.py`, `policy.py`, `store.py` (schema+CRUD+FTS+WAL), `mirror.py`, `consolidation.py` stub.
5. **Selftest** — `memory/selftest.py` green on temp DB.
6. **retrieval_adapter** — pure fusion helper + unit tests with synthetic `SearchResult` lists.
7. **Hook hybrid_search** — lazy fusion tail; tests in `tests/test_hybrid_search.py` or new `tests/test_memory_fusion.py` (enabled vs disabled).
8. **Hook audit_logger_node** — non-fatal `stage_episode`; graph unit test with memory on/off.
9. **gate_memory.py** — routes + register; wire in `gate.py`.
10. **Export HTML** — config-gated route.
11. **Isolation tests** — `tests/test_memory_isolation.py`.
12. **Broader tests** — `tests/test_memory_store.py`, `tests/test_memory_policy.py`, gate route tests (401 without key, 404 when disabled, propose/apply happy path).
13. **Docs** — short `docs/memory/README.md` operator page; leave this plan in tree.
14. **Run** — `python -m memory.selftest`; `pytest` (full); ruff/mypy on touched paths.
15. **Commit** (explicit paths, no `git add .`); push; `gh pr create --draft`.

---

## 9. Affected / new tests

| Test | Change |
|---|---|
| `tests/test_memory_isolation.py` | **New** — AST isolation |
| `tests/test_memory_store.py` | **New** — schema, CRUD, FTS, TTL prune |
| `tests/test_memory_policy.py` | **New** — reason + injection |
| `tests/test_memory_fusion.py` | **New** — fuse ranking / caps / disabled no-op |
| `tests/test_memory_routes.py` | **New** — FastAPI TestClient auth + reason |
| `memory/selftest.py` | **New** — offline module selftest |
| `tests/test_hybrid_search.py` | Possibly extend: disabled fusion leaves results identical |
| `tests/test_graph.py` | Extend: audit node with memory episode on/off |
| `tests/test_gate.py` | Register routes present; disabled 404/flags |
| `tests/test_due_diligence_invariants.py` | Expect **no** failures if I1–I6 preserved |
| `tests/test_*_isolation.py` (telegram/agentic/…) | Unchanged |

---

## 10. Security checklist (implement-time)

- [ ] All memory switches default `false`
- [ ] Mutating routes: `require_api_key` + non-empty reason
- [ ] Apply-path injection scan before fact write
- [ ] No auto-fact from untrusted content
- [ ] Episode/query defaults to hash-only
- [ ] DB file `0600`, WAL, parameterized SQL only
- [ ] HTML export escapes content; auth-gated
- [ ] Exceptions never escape hybrid_search or audit_logger_node
- [ ] No new third-party dependencies
- [ ] Audit events for admin actions (prefer hashes/ids over raw content)
- [ ] `sec-vuln-scanner` posture: no new deps ⇒ no new CVE surface from packages

---

## 11. Rollout / operator story

1. Deploy code (defaults off) → behavior identical to today.  
2. Set `memory.enabled: true` + `episodes.enabled: true` → episodes start staging.  
3. Set `propose_apply.enabled: true`, set `CYCLAW_API_KEY`, propose/apply facts.  
4. Optionally enable `facts.retrieval_enabled: true` + `retrieval_fusion.enabled: true` after verifying FTS quality — both are required to fuse.  
5. Export HTML only if needed.

---

## 12. PR shape

- **Title:** `feat(memory): foundation store, propose/apply, optional retrieval fusion`
- **Draft PR** against `main`
- **Body sections:** summary, config defaults, invariant table, test plan, out of scope, link to this plan
- **Commits:** preferably stacked logical commits (store → hooks → routes → tests) or one clean commit if small enough; match repo style (`feat(memory): ...`)

---

## 13. Self-check (plan gate)

### 13.1 Invariant violations introduced by this plan?

| Check | Result |
|---|---|
| New graph entry / pre-retrieve node? | **No** |
| New conditional edges / routers? | **No** |
| External client construction changed? | **No** |
| Path that skips `audit_logger`? | **No** |
| Soul apply without reason/scan? | **No** (untouched) |
| Core imports agentic/sync/guardrails/harness/telegram? | **No** |
| Memory exceptions fail the request? | **No** (required non-fatal) |

**Verdict: no I1–I6 violations in the plan as written.**

### 13.2 Invented APIs?

| Claim | Verified against |
|---|---|
| `require_api_key` | `gate.py:99` |
| `query_endpoint` | `gate.py:604-605` |
| `audit_logger_node(state, cfg, personality=None)` | `graph.py:689` |
| `hybrid_search(self, query: str) -> list[SearchResult]` | `hybrid_search.py:304` |
| `SearchResult` fields | `hybrid_search.py:47-63` |
| `register_ops_routes` injection shape | `gate_ops.py` + `gate.py:852` |
| `SoulEvolutionRequest` reason min_length=1 | `schemas/api.py:80-87` |
| `audit_log` hashes query | `utils/logger.py:241+` |
| `personality_db.connect` | live module |
| Soul routes in gate.py not gate_ops | `gate.py:745-806` |

**New APIs this plan introduces (explicit, not claimed as pre-existing):**

- `memory.*` package API
- `gate_memory.register_memory_routes`
- `/memory/*` and `/query/export/html` routes
- `Memory*Request` schemas
- `retrieval_mode="memory"` string on `SearchResult` (additive; field already free `str`)

### 13.3 Deviations from user prompt (documented)

1. **Endpoints not in `gate_ops.py`** — use `gate_memory.py` registration pattern; soul template is `gate.py`, ops is OOB.
2. **Auth name `require_api_key` not `require_bearer`.**
3. **I3/I4 labels follow live invariant-guard**, not prompt swap.
4. **HEAD is `010e9b4`**, not prompt SHA `be6dbfe`.
5. **`docs/memories/` ignored**; docs under `docs/memory/`.
6. **WAL enabled for memory DB** even though personality_db does not set WAL today.
7. **MCP gets fusion only via shared `hybrid_search`**, no MCP code change required.

### 13.4 Open decisions for approver (non-blocking defaults chosen)

| Topic | Default in this plan | Alternative |
|---|---|---|
| Admin module home | `gate_memory.py` | Inline in `gate.py` next to soul |
| `/memory/status` when disabled | 200 + flags | 404 |
| Fact delete | soft deactivate via propose/apply | hard DELETE endpoint |
| `retrieval_mode="memory"` | new mode string | keep `"hybrid"` + source prefix only |
| Export method | GET `/query/export/html` | POST with body |

---

## 14. Approval gate

**Approved and implemented.** Branch `feature/memory-foundation` from HEAD `010e9b4`. Consolidation and auto-fact extraction remain out of scope / unactivated.

---

*End of plan — HEAD `010e9b4533b8c589933bf02f0327a108a605dbc9`.*
