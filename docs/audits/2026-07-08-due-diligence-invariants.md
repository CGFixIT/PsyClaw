# CyClaw Due-Diligence Review — Invariant Divergences

**Date:** 2026-07-08
**Scope:** Whole repository, read as a pre-acquisition technical due-diligence pass.
**Question asked:** Where does actual runtime behavior diverge from what the docs,
comments, or naming claim — silent failure modes, invariants asserted in prose but
not enforced in code, and security properties that hold only by convention?
**Threat-model frame:** single-operator, loopback-bound, single-tenant
(`docs/THREAT_MODEL.md`). Findings are graded against CyClaw's *own claims*, not a
multi-tenant standard.

Every finding below is reproduced from the code as of commit base `origin/main`
and is pinned by a named test in `tests/test_due_diligence_invariants.py`. One
finding (F4) was a clear defect with a trivial, pattern-matching fix and was fixed
in this change; the rest are pinned and documented, not altered.

---

## Severity summary

| # | Divergence | Severity | Pinned by |
|---|---|---|---|
| F1 | Soul injection scan is write-path-only; reload/drift adopt an unscanned soul | High | `TestSoulInjectionScanBoundary` |
| F2 | Two of the three "triple-gated external" gates (mode, provider.enabled) are not in the graph | Medium-High | `TestExternalCallGate*` |
| F3 | "Audit stores hashes only" is a config default, not an invariant | Medium | `TestAuditQueryPrivacy` |
| F4 | Injection filter opened a CWD-relative config (breaks CWD-independence) — **fixed** | Medium | `TestSanitizerCwdIndependence` |
| F5 | MCP "protocol-level cannot invoke an LLM" via `sampling: None` is decorative | Low-Medium | `TestMcpNoLlmPath` |
| F6 | `/health` reports `embeddings_local` healthy unconditionally (not a probe) | Low | `TestHealthEmbeddingsSignalIsStatic` |
| F7 | Rate-limit 429 body hardcodes "60/min" regardless of configured limit | Low | (documented; see appendix) |

---

## F1 — Soul injection scanning is enforced only on the HTTP apply path

**Claim.** `utils/personality.py` docstring: proposed soul evolutions are scanned
"using the SAME banned-pattern set the query path uses … so the soul (prepended to
every LLM system prompt) is no longer guarded by a weaker list than user queries."
`CLAUDE.md` I5: "Soul mutation requires a human `reason` string … Never autonomous
modification."

**Reality.** The injection scan (`_scan_enforced`) runs **only** inside
`apply_evolution(..., scan=True)`, which is the `POST /soul/apply` path. The other
two ways the live soul changes are unscanned:

- `_load_soul()` on startup: if `soul.md` on disk no longer matches the newest
  `soul_versions` row, it records a `DRIFT_RECOVERY` row, emits a
  `soul_drift_detected` audit event, and **adopts the on-disk content verbatim** as
  the live soul. No injection scan runs.
- `reload()` (`POST /soul/reload`): re-reads `soul.md` and adopts it verbatim. No
  scan.

The adopted soul is what `get_system_prompt_additive()` prepends to every local-LLM
and offline-best-effort system prompt.

**Exploit / failure scenario.** Anything that writes `data/personality/soul.md`
out-of-band — a text editor, a `sync` job if `include_soul` were ever flipped, a
restored `.bak`, or filesystem drift — bypasses the `reason` gate and the injection
scan entirely. On the next startup or `reload`, a soul containing
`ignore previous instructions …` or `update your soul to …` is loaded and becomes a
standing instruction prepended to every LLM prompt. Under the single-operator threat
model an attacker with local file-write already owns the box, so this is a
**documentation/claim gap**, not a remote RCE — but the docstring's "no longer
guarded by a weaker list" reads as a general property when it is in fact true only
for the one HTTP write path. Verified experimentally: `apply_evolution` blocks the
injection; a direct file edit + `reload()` adopts it verbatim.

**Pinned by.** `TestSoulInjectionScanBoundary.test_apply_evolution_blocks_injection_at_write_boundary`
(positive gate) and `…test_reload_adopts_soul_without_scanning__scan_is_write_path_only`
(tripwire that fails if someone changes the reload/drift behavior, forcing a
deliberate docs update).

---

## F2 — The "hybrid mode" and "provider.enabled" gates are not in the graph

**Claim.** `graph.py` module docstring: "No Grok without explicit user confirmation
AND hybrid mode." `CLAUDE.md` I3: external call needs `mode=="hybrid"` AND
`<provider>.enabled` AND `user_confirmed_online`, "all three," and I2: "Topology =
policy — routing is graph edges only." (PR #441 added Claude as a second external
provider alongside Grok; the same split applies to both.)

**Reality.** `graph.user_gate_router` enforces only confirmation, the selected
`online_provider`, and that provider's client availability — e.g. for Grok
`provider == "grok" and grok is not None and grok.is_available()`. It never reads
`app.mode` or `models.<provider>.enabled`. Those two gates exist **only** in
`gate.py`'s client construction, once per provider:

```python
grok = None
if cfg["app"]["mode"] == "hybrid" and cfg["models"]["grok"].get("enabled", False):
    grok = GrokClient(cfg=cfg)
claude = None
if cfg["app"]["mode"] == "hybrid" and cfg["models"].get("claude", {}).get("enabled", False):
    claude = ClaudeClient(cfg=cfg)
```

If a non-`None`, available client is ever passed to `build_graph` while
`app.mode != "hybrid"` or that provider's `enabled == false`, the graph routes a
confirmed low-score query straight to the external node. Verified: building the graph
with `cfg.app.mode="offline"`, `enabled=false`, and a usable Grok or Claude client
still routes externally on confirmation. `tests/test_graph.py` already documents this
("the graph itself does not read app.mode").

**Exploit / failure scenario.** Not remotely exploitable today — `gate.py` is the
sole caller and its construction guard holds. The risk is **architectural drift**:
the "topology = policy" invariant implies the Grok policy is fully expressed in graph
edges, so a future refactor that constructs the client differently (a test harness, a
new entry point, a "always build the client and decide later" cleanup) would send
traffic to the paid external xAI API in offline mode with no graph-level backstop.
Two of the three advertised gates rest on one `if` in `gate.py`.

**Pinned by.** `TestExternalCallGateRuntimeHalf` (property sweep: no
missing/unavailable/unconfirmed/wrong-provider combination reaches Grok or Claude;
each all-pass case does) and
`TestExternalCallGateConstructionHalf.test_external_client_construction_is_double_gated`
(AST assertion, parametrized over `GrokClient` and `ClaudeClient`, that each
assignment is guarded by an `if` whose condition mentions both `hybrid` and
`enabled`). The runtime tripwire `test_graph_gate_does_not_consult_app_mode_by_design`
locks the documented split so a future editor who assumes the graph enforces mode gets
a clear signal.

---

## F3 — "Audit stores SHA-256 hashes only" is a config default, not an invariant

**Claim.** `utils/logger.py` docstring: "Query text is SHA256-hashed to prevent the
audit log from becoming a data exfiltration vector." `CLAUDE.md`: "the audit log
stores SHA-256 hashes only; raw text is never persisted." `metrics.py`: the
`/audit/summary` aggregates are "safe to expose … without leaking the underlying
queries."

**Reality.** `audit_log()` hashes the `query` field only when
`logging.audit_fields.include_query_hash` is truthy (it defaults to `True` and the
shipped `config.yaml` sets it `True`). Set it to `false` and the `query` key is
**not** popped or hashed — the raw query string is written to `audit.jsonl` verbatim,
subject only to email/IP/secret redaction. Verified: with `include_query_hash: false`,
the literal query text lands in the audit line.

**Exploit / failure scenario.** The privacy guarantee is one YAML boolean deep, and
that boolean is presented alongside cosmetic "which fields to include" flags rather
than as a security control. An operator toggling audit verbosity, or a config
regression, silently converts `audit.jsonl` into a plaintext query log — the exact
"data exfiltration vector" the hashing exists to prevent. Nothing previously asserted
the shipped default, so a docs/config drift would pass CI.

**Pinned by.** `TestAuditQueryPrivacy.test_query_is_hashed_and_raw_text_never_persisted`
(property sweep over 30 generated queries: hash present, raw text absent),
`…test_shipped_config_enables_query_hashing` (config contract), and
`…test_disabling_hashing_persists_raw_text__documented_leak` (characterizes the leak
so the flag's security weight is explicit).

---

## F4 — The injection filter opened a CWD-relative config (fixed here)

**Claim.** The codebase invests heavily in CWD-independent config loading:
`gate.py` anchors to `_BASE_DIR`, `utils/logger.py` and `utils/health.py` anchor bare
`"config.yaml"` lookups to `_REPO_ROOT`, all citing the Windows double-click failure
mode ("cwd-relative opens of config.yaml would crash at import").

**Reality (before fix).** `utils/sanitizer.py::_load_filter` opened the bare default
`"config.yaml"` **relative to the current working directory**, with no repo-root
anchoring. `gate.py`'s `/query` handler calls `check_input(req.query)` with no path,
so it inherited that CWD-relative open. Verified: from any non-repo-root CWD,
`check_input("ignore previous instructions")` raised `FileNotFoundError`.

**Failure scenario.** Launch `cyclaw-server` from any directory other than the repo
root (`cd /tmp && cyclaw-server`, or a Windows double-click whose CWD is not the repo
root). Startup succeeds, `/health` returns `ok`/`degraded` normally — but the first
`/query` raises `FileNotFoundError` out of the handler (it is outside the graph's
`except`, and is not a `PromptInjectionError`), so **every query returns HTTP 500 and
the injection filter never runs**. It fails closed (crash, not bypass), but it
silently defeats the CWD-independence the rest of the stack guarantees, and only from
the one hot-path config reader that was never anchored.

**Fix applied.** `utils/sanitizer.py` now anchors a non-absolute `config_path` to
`_REPO_ROOT` via a `_resolve_config_path` helper, mirroring `utils/logger.py`.
Behavior from the repo root is unchanged; from any other CWD the filter now resolves
and runs correctly.

**Pinned by.** `TestSanitizerCwdIndependence` (runs `check_input` and
`sanitize_chunk` from a foreign CWD and asserts injection is blocked and clean input
passes).

---

## F5 — MCP "protocol-level" no-LLM guarantee is decorative

**Claim.** `mcp_hybrid_server.py`: "Protocol-level guarantee: this server CANNOT
invoke an LLM," backed by `CAPABILITIES = {"tools": {}, "sampling": None}`. Invariant
guard G5 greps for `"sampling": None`.

**Reality.** In the MCP protocol, `sampling` is a **client** capability (the client
advertising that it can service `sampling/createMessage` for the server), not a server
capability. A server placing `"sampling": None` in its own capabilities dict is inert —
it neither prevents the server from importing an LLM client nor from issuing a sampling
request. The *actual* guarantee is structural: `mcp_hybrid_server.py` imports only
`retrieval.hybrid_search`, `utils.errors`, and `utils.logger` — no LLM client — and
never calls `.generate()`. That real property is what should be pinned; the
`sampling: None` grep gives false confidence that a protocol mechanism is at work.

**Failure scenario.** Low risk, but the label invites a future contributor to "wire
up the LLM the server already almost supports" believing the capability flag is a
live guard that would stop them, when nothing does except code review.

**Pinned by.** `TestMcpNoLlmPath.test_mcp_server_imports_no_llm_client` (AST: no `llm`
import, no `LocalLLMClient`/`GrokClient` reference) plus
`…test_mcp_capabilities_declare_no_sampling` (keeps the declared flag).

---

## F6 — `/health` reports `embeddings_local` healthy unconditionally

**Claim.** `/health` returns a per-service health map; the console renders each
service's `healthy` flag.

**Reality.** `utils/health.py::check_all` appends
`HealthStatus(name="embeddings_local", healthy=True, latency_ms=0.0)` as a hardcoded
literal. It never loads or probes the sentence-transformers model. If the embedding
model is missing or broken (retrieval is dead), `/health` still reports
`embeddings_local: healthy`. The health docstring does say it "only checks LM Studio
(and optionally Grok)," so this is a naming/signal mismatch rather than a hidden claim:
the *name* implies a probe the code does not perform. Retrieval readiness is instead
carried by `index_ready`/`graph_ready`.

**Pinned by.** `TestHealthEmbeddingsSignalIsStatic` (with the LM Studio probe forced
to fail, `embeddings_local` is still `healthy=True`).

---

## Appendix — lower-signal observations (not separately pinned)

- **F7 — Hardcoded "60/min" in rate-limit responses.** All six 429 bodies in
  `gate.py` read `"Rate limit exceeded (60/min)"` as a literal. The limit is
  configurable (`api.rate_limit.max_requests`/`window_seconds`); if an operator
  changes it, the error message misstates the actual ceiling. Cosmetic, no security
  impact.
- **`.bak` can lose oversized soul content.** `apply_evolution` writes the backup
  from `self.soul_core` (the in-memory, `soul_max_chars`-truncated soul), not the
  on-disk file. A `soul.md` between `soul_max_chars` (8000) and the HTTP cap (8192)
  is stored full on disk but backed up truncated, so `restore_from_backup` can lose
  the tail. Narrow window, low impact.
- **`security.require_env` is decorative.** No code reads it (already noted in
  `CLAUDE.md`); the server boots without `GROK_API_KEY`. Listed for completeness.
- **Injection filter is best-effort regex.** The 33 `banned_patterns` are broad
  (e.g. `act as`, `urgent`, `important update`) — real-world false positives — and,
  being literal regex over raw text, are trivially evadable by Unicode homoglyphs or
  zero-width characters the `\s+` tolerance does not cover. This is consistent with
  the threat model (defense-in-depth, not a completeness guarantee), but the config
  header's "stronger defense" framing should not be read as robust.

---

## What was changed in this review

- **Fixed:** `utils/sanitizer.py` now resolves a relative `config_path` against the
  repo root (F4).
- **Added:** `tests/test_due_diligence_invariants.py` — property-style regression
  harness pinning every finding above plus the core invariants (I1 RAG-first, I4
  audit convergence, I5 reason gate, I6 module isolation).
- **Added:** `INVARIANTS.md` — the must-read contract for `gate.py`, `soul.md`
  handling, and the scanner.

No graph edges, auth logic, `banned_patterns`, or soul-handling behavior were
changed. F1, F2, F3, F5, F6 are pinned and documented as-is; changing any of them
touches a security-invariant surface and belongs in its own reviewed change.
