# CyClaw Project Rules

Scoped behavioral rules and non-negotiable constraints for Claude Code sessions in this repository.

---

## Security Invariants (Enforced by Graph Topology)

1. **RAG-First Invariant**  
   `retrieve` is the unconditional entry node — no LLM call precedes retrieval. No exceptions.

2. **Topology = Policy**  
   Routing is enforced by LangGraph edges only, never by LLM decisions or runtime checks.

3. **Triple-Gated External Access**  
   Grok fallback requires **all three** conditions simultaneously:
   - `config.app.mode == "hybrid"`
   - `config.models.grok.enabled == true`
   - `user_confirmed_online == true`

4. **Audit Convergence**  
   All six execution paths must converge at `audit_logger` node before END. No shortcuts.

5. **Soul Governance**  
   Mutations to `data/personality/soul.md` require an explicit human `reason` string. Never autonomous modification.

---

## Code Standards (Mandatory)

### Python

- **Default:** Python 3.12 (range: 3.10–3.13+)
- **Typing:** Fully annotated. `from __future__ import annotations` when supporting <3.10.
- **Linting:** `ruff check` + `ruff format` (line-length 120, py312)
- **Type Checking:** `mypy --strict --python-version 3.12`
- **No Secrets:** Environment variables only via `pydantic-settings`. Never hardcode tokens.
- **No Mutations:** Data-modifying scripts must have `--dry-run` defaulting to safe mode.
- **Async:** Prefer `asyncio.TaskGroup` (3.11+) with `asyncio.gather` fallback.
- **Safety:** No `shell=True` with user input. Always use `subprocess.run([...], list)`.

### Database & Configuration

- **Config Source of Truth:** `config.yaml` only. No hardcoded tunables.
- **Soul File:** Must exist at `data/personality/soul.md` before server start.
- **Database Backend:** SQLite default; Postgres via `CYCLAW_DB_URL` env var or `config.personality.database_url`.
- **ChromaDB:** Embedded `PersistentClient` only — never HTTP client.

### Testing

- **Coverage Target:** 80% minimum (measured across `gate`, `graph`, `mcp_hybrid_server`, `metrics`, `llm`, `retrieval`, `utils`, `sync`, `agentic`)
- **Test Command:** `GROK_API_KEY=dummy pytest tests/ -q --tb=short`
- **No Live Services:** All external deps mocked via `tests/conftest.py`
- **Exit Codes:** Respect exit code conventions (0=success, 2=operation failed, 3=env/config error, 4=write refused)

---

## Module Isolation Rules

### Never Import Into Core Paths

The following modules **must never** import `agentic/`, `sync/`, or each other:
- `gate.py` — FastAPI server entry
- `graph.py` — LangGraph security topology
- `mcp_hybrid_server.py` — MCP server

Rationale: Architectural isolation preserves the five security invariants.

### Out-of-Band Execution

- **`agentic/`** — Run via `python -m agentic.cli`. Reads GitHub, proposes/applies skills, governs write gate.
- **`sync/`** — Run via `python -m sync.cli`. Dropbox corpus sync via `rclone`.

---

## Retrieval Invariants

### Hybrid Search Pattern

All retrieval must use the **RRF fusion** (Reciprocal Rank Fusion, k=60):
- **Leg 1:** ChromaDB semantic search (local CPU embeddings, `all-MiniLM-L6-v2`)
- **Leg 2:** BM25Okapi keyword search (local, no external dependencies)
- **Fusion:** Rank combining both signals; never bypass either leg.

See `retrieval/hybrid_search.py` for implementation.

---

## Git Workflow

- **Identity:** Before any commits, set:
  ```bash
  git config user.email noreply@anthropic.com
  git config user.name Claude
  ```

- **Feature Branches:** Develop on assigned branch (e.g., `cc/feature-name`). Do not push to `main` directly when a feature branch and open PR exist.

- **Commits:** Clear, descriptive messages. Reference issue numbers when applicable.

- **Force Push:** Never without explicit user approval. The stop hook blocks `--force-with-lease`.

---

## Documentation Standards

- **CLAUDE.md is authoritative** — update it when architecture, modules, or behavioral rules change.
- **.claude/ structure** — mirror documented skills/patterns/utilities; keep in sync.
- **Agentic Governance Docs** — `docs/agentic/AGENTIC_README.md` + `SKILLS_REGISTRY_GOVERNANCE.md` are binding.
- **Session Notes** — optional but encouraged; live notes go under `.claude/session-notes/` (`docs/SESSION_NOTES.md` is an empty template scaffold, not the active log).

---

## Risk Tier Classification

| Tier | Examples | Required Safeguard |
|---|---|---|
| **Low** | Local, reversible, no sensitive data, narrow scope | Standard checks |
| **Medium** | Shared code paths, moderate impact, recoverable | Expand tests; document rollback path |
| **High** | Production data, destructive commands, broad impact | Explicit user approval FIRST |

**Default:** Choose the higher tier when uncertain.

---

## Hard Rules (No Exceptions)

- ✋ **Never expose credentials, tokens, or secret files.**
- ✋ **Never run destructive operations without explicit user confirmation.**
- ✋ **Never push to `main` via GitHub MCP when a feature branch and open PR exist** — creates add/add rebase conflicts.
- ✋ **Never confirm a force-push without the user's explicit sign-off.**
- ✋ **Never modify `data/personality/soul.md` without a `reason` string.**
- ✋ **Never import `agentic/`, `sync/`, or out-of-band modules into `gate.py`, `graph.py`, or `mcp_hybrid_server.py`.**

---

## Dependency Notes

- **PyYAML:** Install with `pip install -r requirements.txt -c constraints.txt --ignore-installed PyYAML`
- **torch:** Install `torch==2.13.0+cpu` **before** `requirements.txt` (CVE-2025-32434 was fixed in 2.6.0; 2.13.0 is within the patched range — install order still matters to ensure CPU wheel resolves correctly)
- **ChromaDB CVE-2026-45829:** Accepted; threat model is embedded-only (`PersistentClient`), not HTTP.

---

## Environment Quirks

- `status: degraded` in `/health` is normal without LM Studio running.
- `TELEMETRY KILL` messages on startup are intentional (LangChain/Chroma/OTel env vars blocked).
- `GROK_API_KEY` must be set in test environment (any non-empty value works offline).

---

## Escalation Paths

- **Undefined behavior:** Post in `#cyclaw-dev` Slack.
- **Security concerns:** File a private security issue on GitHub.
- **Configuration drift:** Run `/sandbox-runtime-verification` and report findings.
- **Stuck on a blocker:** Call out in `.claude/session-notes/` + Slack before context compaction.
