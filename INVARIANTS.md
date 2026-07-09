# INVARIANTS.md — read this before touching `gate.py`, `soul.md` handling, or the scanner

This file is the load-bearing contract for the three surfaces that break CyClaw's
security posture if handled carelessly: the **FastAPI gate** (`gate.py`), **soul.md
handling** (`utils/personality.py` + the `/soul/*` routes), and the **injection
scanner/sanitizer** (`utils/sanitizer.py`, `policy.prompt_filter`). It states what
must never change, which test proves each rule, and — critically — where a guarantee
holds only by convention so you do not mistake a comment for an enforcement.

Authority order (from `CLAUDE.md`): running code wins over `config.yaml` wins over
docs. This file describes the code as it actually behaves, cross-checked against
`docs/audits/2026-07-08-due-diligence-invariants.md`. Every "proven by" reference is
a test in `tests/test_due_diligence_invariants.py` unless another file is named.

Before editing any of the three surfaces, run:

```bash
GROK_API_KEY=dummy pytest tests/test_due_diligence_invariants.py -q
python3 .claude/skills/invariant-guard/check_invariants.py
```

---

## Rule 1 — `retrieve` is the unconditional graph entry (RAG-first)

**Must never change:** the compiled graph's single entry node is `retrieve`
(`graph.py` `set_entry_point("retrieve")`), and no node produces an answer before
retrieval has run. Do not add a pre-retrieval node (cache hit, greeting, classifier,
"fast path"). Do not add an entry point.

**Proven by:** `TestRagFirstEntry.test_graph_entry_point_is_retrieve` (AST) and
`test_retrieval_runs_before_any_answer_on_every_path` (the retriever is called on
every routing path). Also invariant-guard I1.

---

## Rule 2 — the external (Grok / Claude) call is triple-gated, and two gates live in `gate.py`, not the graph

**Must never change:** an external provider call (Grok **or** Claude — PR #441 added
Claude alongside Grok) requires **all three** of `app.mode == "hybrid"`,
`models.<provider>.enabled == true`, and `user_confirmed_online == true`, plus the
selected provider's usable client (`is_available()`, i.e. the provider's API key
present). The provider is chosen by `state.online_provider` (defaults to `"grok"`).

**Where each gate actually lives — do not assume "topology = policy" covers all
three:**

- `graph.user_gate_router` enforces only: confirmed, the selected `online_provider`
  matches, and that provider's client `is not None and is_available()`. It **does
  not read `app.mode` or `models.<provider>.enabled`.** Its return set is
  `{grok_fallback, claude_fallback, offline_best_effort, audit_logger}`.
- `app.mode == "hybrid"` and `models.<provider>.enabled` are enforced **exclusively**
  by `gate.py`'s client construction: each of `grok` / `claude` stays `None` unless
  both hold. A `None` client makes the router fall back to `offline_best_effort`.

**Consequence you must respect:** if you ever construct a `GrokClient` or
`ClaudeClient` outside its double-gated `if` in `gate.py` (a new entry point, a test
harness wired into production, an "always build the client" refactor), you will send
confirmed low-score queries to a paid external API in offline mode — the graph has no
backstop for mode/enabled. Keep both construction guards intact.

**Proven by:** `TestExternalCallGateRuntimeHalf` (no missing/unavailable/unconfirmed
/wrong-provider combination reaches Grok or Claude; each all-gates-pass case does),
`TestExternalCallGateConstructionHalf.test_external_client_construction_is_double_gated`
(AST, parametrized over `GrokClient` and `ClaudeClient`: each assignment is guarded by
an `if` mentioning both `hybrid` and `enabled`), and the tripwire
`test_graph_gate_does_not_consult_app_mode_by_design`. Also invariant-guard I3.

---

## Rule 3 — every path converges at `audit_logger` before END

**Must never change:** all six upstream nodes (`retrieve`, `route_by_score`,
`local_llm`, `user_gate`, `grok_fallback`, `offline_best_effort`) reach
`audit_logger`, and `audit_logger`'s only outgoing edge is `END`
(`add_edge("audit_logger", END)`). Do not add an edge out of `audit_logger`; do not
add a node with a path to `END` that skips it. Every query — including the
`user_gate` pause — must emit an audit event.

**Proven by:** `TestAuditConvergence.test_every_path_emits_an_audit_event` (property
sweep over all four terminal path configurations) and
`test_audit_logger_edges_to_end_only`. Also invariant-guard I4.

---

## Rule 4 — soul mutation via the HTTP path requires a human reason and passes the injection scan

**Must never change (write path):** `PersonalityManager.apply_evolution` refuses an
empty/whitespace `reason` (raises `ValueError`) and, with `scan=True` (the default,
used by `POST /soul/apply`), rejects a soul containing critical injection patterns
(`PromptInjectionError`) **before any file or DB write**. Writes are atomic
(`tmp` + `os.replace`). The only sanctioned `scan=False` caller is
`restore_from_backup` re-applying a previously vetted `.bak`.

**Proven by:** `TestSoulReasonGate` (empty-reason sweep refuses; non-empty applies),
`TestSoulInjectionScanBoundary.test_apply_evolution_blocks_injection_at_write_boundary`,
and existing `tests/test_personality.py::TestApplyEvolutionInjectionGate`. Also
invariant-guard I5.

---

## Rule 5 — the soul injection scan is WRITE-PATH-ONLY; reload/drift adopt `soul.md` unscanned

**This is a sharp edge, not a feature to "fix" casually.** The injection scan runs
only in `apply_evolution`. Two other code paths change the live soul **without any
scan**:

- **Startup drift recovery** (`_load_soul`): if `soul.md` on disk differs from the
  newest `soul_versions` row, the on-disk content is adopted verbatim (a
  `DRIFT_RECOVERY` version row + `soul_drift_detected` audit event are recorded).
- **`reload()`** (`POST /soul/reload`): re-reads and adopts `soul.md` verbatim.

The adopted text is prepended to every local-LLM and offline prompt via
`get_system_prompt_additive()`. So a soul edited out-of-band (editor, restore, drift)
is trusted with no scan. Under the single-operator threat model this is acceptable —
but do not describe the soul as "always guarded by the query-path banned list"; that
is true only for `POST /soul/apply`.

**If you intend to add scanning to the reload/drift path** (a legitimate hardening):
update `test_reload_adopts_soul_without_scanning__scan_is_write_path_only` and this
rule deliberately — do not silently delete the tripwire.

**Proven by:** `TestSoulInjectionScanBoundary.test_reload_adopts_soul_without_scanning__scan_is_write_path_only`.

---

## Rule 6 — soul endpoints fail closed; the scanner is CWD-independent

**Must never change (auth):** with `CYCLAW_API_KEY` unset, every `/soul/*`, `/ops/*`,
and `/audit/summary` route returns 401 — never "open mode." Key comparison uses
`hmac.compare_digest` (constant-time). Do not reintroduce an unauthenticated fallback.

**Must never change (scanner path resolution):** `utils/sanitizer.check_input` /
`sanitize_chunk` resolve a non-absolute `config_path` against the repo root
(`_REPO_ROOT`), so the injection filter works from any working directory. Do not
revert to a bare `open("config.yaml")` — that reintroduces the CWD-relative crash
that took down the entire `/query` path when the server was launched from outside the
repo root (see finding F4). `gate.py` calls `check_input(req.query)` with no path and
relies on this anchoring.

**Proven by:** `tests/test_gate.py::TestSoulAndErrorPaths` (401 fail-closed) and
`TestSanitizerCwdIndependence` (injection blocked / clean input passes from a foreign
CWD). Also invariant-guard G2.

---

## Rule 7 — audit query privacy depends on `include_query_hash: true`

**Must never change:** the shipped `config.yaml` keeps
`logging.audit_fields.include_query_hash: true`. With it true, `audit_log()` replaces
the `query` field with its SHA-256 hash and never persists raw query text. **Setting
it `false` makes the audit log persist the raw query string** (subject only to
email/IP/secret redaction) — turning `audit.jsonl` into a plaintext query log. Treat
this flag as a privacy control, not a verbosity toggle, and do not flip the shipped
default without a security review.

**Proven by:** `TestAuditQueryPrivacy.test_query_is_hashed_and_raw_text_never_persisted`
(property sweep), `test_shipped_config_enables_query_hashing` (config contract), and
`test_disabling_hashing_persists_raw_text__documented_leak`.

---

## Rule 8 — the MCP server has no LLM path (structurally, not by capability flag)

**Must never change:** `mcp_hybrid_server.py` imports no LLM client
(`LocalLLMClient`/`GrokClient` / anything under `llm/`) and never calls `.generate()`.
This — not the decorative `CAPABILITIES["sampling"] = None` — is what makes the MCP
server retrieval-only. `sampling` is a *client* capability in MCP; a server declaring
it `None` enforces nothing. Do not add an LLM import "because the capability is
already declared"; the flag is not a guard.

**Proven by:** `TestMcpNoLlmPath.test_mcp_server_imports_no_llm_client` and
`test_mcp_capabilities_declare_no_sampling`. Also invariant-guard G5.

---

## Rule 9 — the core three never import the out-of-band packages

**Must never change:** `gate.py`, `graph.py`, and `mcp_hybrid_server.py` never import
`agentic`, `sync`, or `guardrails` (and those never import the core three). The
`/ops/*` routes reach the out-of-band CLIs only through `utils/ops_runner.py`, a
`subprocess.run([...])` shim — never an import. This isolation is what keeps the
out-of-band subsystems from becoming a path around the security invariants.

**Proven by:** `TestCoreModuleIsolation.test_core_modules_never_import_out_of_band`
and `tests/test_agentic_isolation.py` (AST, both directions). Also invariant-guard I6.

---

## Signals that are weaker than their name suggests (do not rely on them)

- **`/health` `embeddings_local: healthy`** is a hardcoded literal, not a probe — a
  broken embedding model still reports healthy. Use `index_ready`/`graph_ready` for
  retrieval readiness. (`TestHealthEmbeddingsSignalIsStatic`.)
- **Rate-limit 429 bodies say "60/min"** as a literal string even if
  `api.rate_limit.max_requests` is changed. The enforced limit is correct; the
  message is not authoritative.
- **`security.require_env`** is decorative — no code reads it; the server boots
  without `GROK_API_KEY` (Grok just reports unavailable).
- **`banned_patterns`** is best-effort regex over raw text (33 patterns in the
  shipped config). It is defense-in-depth, not a completeness guarantee; homoglyph /
  zero-width evasion is out of scope per the threat model.
