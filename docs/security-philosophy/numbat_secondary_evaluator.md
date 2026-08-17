# Numbat 0.1.2 as CyClaw’s secondary evaluator

**Audience:** operators and reviewers who need to know what Numbat is *for* in this repo, not how to reimplement it.

**Code and `config.yaml` win.** This note explains the 0.1.2 CLI CyClaw’s fixture job actually runs. The emitter (`utils/numbat_emitter.py`) writes **0.2.0-shaped** NDJSON (`schema_version: "0.2.0"`, `source_agent: "unknown"`). Those are not the same artifact.

Related: `config.yaml` `numbat:` block, `logs/numbat-events.ndjsonl`, `.github/workflows/numbat-rules.yml` (static fixtures only), `docs/THREAT_MODEL.md`. `audit.jsonl` stays authoritative. Numbat is never imported by `gate.py` / `graph.py` / `mcp_hybrid_server.py` (I6). A live-jail event generator was tried and removed: 0.1.2 rejects extra `command.exec` fields, so the job never scored rules.

---

## ELI5

Numbat is a security notebook for AI helpers: it writes down “the helper ran this command” or “it tried to read this file.” CyClaw already has its own diary (`audit.jsonl`). Numbat is a second, simpler copy of the *optional* helper stuff (tools, files, checks), not the main Q&A brain. Version **0.1.2** is the Numbat program CyClaw’s tests actually run, to ask: “does this notebook look like someone stealing a key or sending a secret out?” It does not decide what CyClaw answers, and if Numbat breaks, CyClaw is supposed to keep working.

---

## Tech 101

In CyClaw, Numbat is a **side-channel detector**, not part of `POST /query`. `utils/numbat_emitter.py` appends NDJSON to `logs/numbat-events.ndjsonl` when the executor, fsconnect, or similar out-of-band tools fire; `gate.py` / `graph.py` / MCP never import it (I6). CI pins the **Numbat 0.1.2** binary and runs `numbat rules test --fixture` against canned (and live-jail) events, looking for rules like `secrets.read_private_key` and `exfil.curl_post_file`. Benefit: you get a vendor-shaped “did the agent do something nasty?” check without putting Numbat on the RAG path. Risk: CyClaw’s emitter labels events **schema 0.2.0**, so 0.1.2 can reject extra fields (`exit_code` / `file_path` on `command.exec`) before any rule runs — a red check that is a format fight, not a catch.

---

## Expert

Numbat 0.1.2 is the **on-device rules evaluator** CyClaw uses as a fail-soft **EDR-style projection** of agent telemetry: a closed schema (`additionalProperties: false`), `source_agent=unknown`, CyClaw identity only in tags, dual-written beside the authoritative SHA-256/PII-redacted `audit.jsonl`. Purpose in this integration is **detection and CI attestation**, not enforcement — `policy.fallback.pre_action_hook.command` can invoke Numbat later, but ships empty; emitter `enabled: true` never raises into the LangGraph topology. Benefit: independent, replayable matching of high-signal agent TTPs (secret-file read, curl file-exfil) against a stable fixture contract, without expanding I1–I4 attack surface. Risks: **schema skew** (0.2.0 emit vs 0.1.2 per-type allowlists) produces false-red or skipped scoring; `continue-on-error` means a real hit would not gate merge; the stream is host-local and can include command/path residue, so it is a second sensitive log, not a privacy improvement; and treating 0.1.2 `--expect-none` as proof of containment overclaims what a best-effort jail plus a forensic sidecar can guarantee.
