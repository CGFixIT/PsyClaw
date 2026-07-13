# CyClaw Security Policy

## Reporting a Vulnerability

Open a private security advisory on GitHub ([CGFixIT/CyClaw](https://github.com/CGFixIT/CyClaw/security/advisories)) or contact the maintainer via [cgfixit.com](https://cgfixit.com). Do not open public issues for exploitable findings.

## Security Model (Summary)

CyClaw is an **offline-first, loopback-only** local AI gateway. The enforced invariants:

1. **RAG-first** — `retrieve` is the unconditional LangGraph entry node; no bypass edge exists.
2. **Topology = policy** — routing is done by score gates in graph edges, never by prompts.
3. **Triple-gated external access** — Grok/Claude require `app.mode=="hybrid"` AND `models.<provider>.enabled` AND per-query human confirmation; both default off.
4. **Audit convergence** — every path terminates in `audit_logger` (SHA-256 query hashes, PII + secret redaction, append-only JSONL).
5. **Soul governance** — identity evolution requires a human-authored reason; atomic writes; SHA-256 drift detection on startup.
6. **Out-of-band connectors** — `agentic/`, `sync/`, `guardrails/` are never imported by `gate.py`, `graph.py`, or `mcp_hybrid_server.py`; they run only via audited subprocess shims, disabled by default, fail-closed.
7. **Zero telemetry** — kill-switch env vars set at import time before any langchain/chromadb import; verified at startup.
8. **Loopback binding** — `127.0.0.1:8787` (gateway) and `127.0.0.1:11434` (Ollama); API-key gate on all mutating endpoints; per-IP rate limiting; strict security headers + TrustedHost.

## Accepted Dependency Risks

These are tracked, deliberate exceptions — re-reviewed at every release and enforced via the `pip-audit` CI workflow.

### chromadb 1.5.9 — CVE-2026-45829 / [PYSEC-2026-311](https://osv.dev/vulnerability/PYSEC-2026-311) (Critical)

- **What it is:** pre-auth RCE in Chroma's **server mode** (`chroma run` / `HttpClient`). No upstream patch available.
- **Why accepted:** CyClaw never runs the Chroma server. It uses the **embedded `PersistentClient`** exclusively (path from `config.yaml`), in-process, with `anonymized_telemetry=False` and no `trust_remote_code`. There is no Chroma network listener to attack; the vulnerable code path is unreachable in this deployment.
- **Guardrails:** any future change introducing `chromadb.HttpClient` or a standalone Chroma server MUST be treated as a security regression and re-open this assessment.
- **Review date:** next chromadb release or 2026-10-01, whichever comes first.

### nltk 3.9.4 — CVE-2026-12243 / [PYSEC-2026-597](https://osv.dev/vulnerability/PYSEC-2026-597) (Medium)

- **What it is:** vulnerability in nltk with no fixed release available at the time of writing.
- **Why accepted:** nltk (punkt) runs only at **corpus index time** on local, operator-supplied `.md`/`.txt` files. No untrusted remote input reaches the tokenizer in normal operation.
- **Review date:** next nltk release or 2026-10-01, whichever comes first.

## Verification

- `python -m pytest tests/ -q` — full suite (mocked externals; no live services needed)
- `pip-audit -r requirements.txt` — dependency CVE sweep (also runs in CI)
- `python scripts`/swarm verification harness — config invariants, telemetry kill, due-diligence invariants, terminal contract
- Network audit: zero non-loopback connections expected in offline mode (see telemetry kill-switch docs in `docs/cyclaw_telemetry_kill.env`)
