# CyClaw Codebase Notes — Extension Points & Security Boundaries

> Subagent: Code-Scanner. Distilled from a full read of CyClaw v1.4.5 (HEAD on
> `claude/cyclaw-agentic-research-ouvzue`). All claims cite real `file:line`.
> Read-only research artifact; no code is proposed here.

## 1. Request path (what must NOT be touched)

```
POST /query (gate.py:220) → rate limit (gate.py:223) → check_input sanitizer
(gate.py:238) → GraphState (gate.py:250) → compiled_graph.invoke (gate.py:256)
→ 7-node graph → QueryResponse (gate.py:262)
```

The graph (`graph.py`) is a `langgraph` `StateGraph`:
`retrieve → route_by_score → {local_llm | user_gate → {grok_fallback | offline_best_effort}} → audit_logger → END`.

- Entry is hardcoded: `graph.set_entry_point("retrieve")` (`graph.py:464`).
- Routing is deterministic flag logic, never an LLM: `score_router` (`graph.py:389`),
  `user_gate_router` (`graph.py:395`).
- Grok is reachable only under the triple-gate: `mode=="hybrid" AND grok.enabled`
  (`gate.py:206`) AND `user_confirmed_online` (`graph.py:413`).
- All six paths converge at `audit_logger_node` (`graph.py:331`) → END.

## 2. The `sync/` precedent (the template to copy)

`sync/` is a complete, shipped, out-of-band feature and the model for any new one:

- **Never imported by the request path** — verified zero matches for
  `from sync` / `import sync` in `gate.py`, `graph.py`, `mcp_hybrid_server.py`.
- **Subprocess discipline** — `sync/runner.py:467` runs `rclone` as an **argv
  list, never `shell=True`**; binary resolved via `shutil.which`; version floor
  enforced (`check_rclone_version`, `sync/runner.py:54`).
- **Audit integration** — emits per-file + summary events via
  `utils.logger.audit_log` (`sync/runner.py:519-524`).
- **Exit-code contract** — `reindex_exit_code_for` (`sync/runner.py:529`): 0 / 10 / 1 / 2 / 3.
- **Config isolation** — additive `sync:` block (`config.yaml:209`), `enabled:false`
  default; CLI `cmd_sync` no-ops cleanly when disabled (`sync/cli.py:161`).
- **Zero new runtime deps** — `rclone` is an external binary, like LM Studio.
- **Own error hierarchy** — `SyncError` + subclasses (`utils/errors.py:45-89`).

## 3. The soul-governance pattern (the template for any self-proposal surface)

`utils/personality.py`:

- `propose_evolution(new_soul, reason)` (`:175`) — **never writes**; returns a diff
  plus *advisory* `injection_flags` / `safe_to_apply`.
- `apply_evolution(new_soul, reason, *, scan=True)` (`:202`) — **enforces** the
  injection scan at the write boundary, requires an explicit human `reason`, and
  writes atomically (`tmp` + `os.replace`, `:237`).
- Scanner = `policy.prompt_filter.banned_patterns` ∪ `OWASP_INJECTION_PATTERNS`
  (`_build_injection_patterns`, `:144`) — same set the query path uses, so the two
  never drift.
- SHA-256 versioning + drift detection on startup (`_load_soul`, `:111`).

## 4. Shared infra reused by out-of-band features

- `utils.logger.audit_log(event, config_path)` (`utils/logger.py:106`) — hashes
  any `query` field, redacts secrets/emails/IPs, appends JSONL. Safe to call from
  anywhere.
- `utils.logger._get_config` / `reset_config_cache` (`utils/logger.py:55,62`) —
  cached config load; the test seam every self-contained suite uses.
- `utils/errors.py` — `RAGError` base; add a feature-specific subclass tree
  (the `SyncError` convention).

## 5. Existing "agentic feel" WITHOUT invariant violations

- MCP server (`mcp_hybrid_server.py`) is **retrieval-only**: `CAPABILITIES["sampling"]
  = None` (`:17`), single `hybrid_search` tool, no LLM, no writes. Adding GitHub
  I/O here would *weaken* that property — hence the out-of-band package instead.
- Skills are filesystem-discovered `.claude/skills/<name>/SKILL.md` (YAML
  frontmatter + body); there is no governed registry today — a clean gap to fill
  with the propose/apply pattern.

## 6. CI / quality bar a new feature must clear

- `ci.yml` — Python 3.12, ubuntu+windows matrix, torch CPU installed first,
  RAG smoke + pytest, coverage `fail_under = 80` (`pyproject.toml:95`).
- `pip-audit.yml` / `osv-scanner.yml` — daily/weekly dep scans; only chromadb +
  nltk CVEs are ignore-listed with documented mitigations. **A feature that adds
  no runtime dependency keeps this bar untouched.**

## 7. Safe extension points (no request-path coupling)

1. New out-of-band CLI package (this is what `agentic/` is) — the cleanest.
2. New retrieval mode on `HybridRetriever` (additive method) — not needed here.
3. New audit event types via `audit_log` — used by `agentic/`.
4. New `PersonalityManager`-style governed store — generalized into `SkillRegistry`.
