# CyClaw Security Policy

> **Two files, one policy.** This copy owns the **security model summary** and the **Accepted Dependency Risks** register below — the acceptances `requirements.txt`, `.trivyignore`, `.osv-scanner.toml`, and the `pip-audit` workflow all encode. The full reporting, triage, severity, and coordinated-disclosure process lives in [`.github/SECURITY.md`](.github/SECURITY.md), which is the copy GitHub displays (`.github/` wins the community-health precedence). Update the file that owns the section rather than copying it across.

## Reporting a Vulnerability

Open a private security advisory on GitHub ([CGFixIT/CyClaw](https://github.com/CGFixIT/CyClaw/security/advisories)) or contact the maintainer via [cgfixit.com](https://cgfixit.com). Do not open public issues for exploitable findings — on a public repository every issue is public the moment it is filed. Full process: [`.github/SECURITY.md`](.github/SECURITY.md).

## Security Model (Summary)

CyClaw is an **offline-first, loopback-only** local AI gateway. The enforced invariants:

1. **RAG-first** — `retrieve` is the unconditional LangGraph entry node; no bypass edge exists.
2. **Topology = policy** — routing is done by score gates in graph edges, never by prompts.
3. **Triple-gated external access** — Grok/Claude require `app.mode=="hybrid"` AND `models.<provider>.enabled` AND per-query human confirmation. The shipped config satisfies the first two gates for both providers; the per-query confirmation cannot be pre-set, and a usable provider key is still required at the call site.
4. **Audit convergence** — every path terminates in `audit_logger` (SHA-256 query hashes, PII + secret redaction, append-only JSONL).
5. **Soul governance** — identity evolution requires a human-authored reason; atomic writes; SHA-256 drift detection on startup.
6. **Out-of-band connectors** — `agentic/`, `sync/`, `guardrails/` are never imported by `gate.py`, `graph.py`, or `mcp_hybrid_server.py`. They ship disabled by default. `sync/` and `agentic/` run via audited argv-list subprocess shims. Optional live NeMo (`nemoguardrails`) is **not** fail-closed on the request path: `check_input` / `check_output` are an offline heuristic floor; engine load or provider errors **degrade** (`guardrail_skipped`) and cannot grant a route. Graph nodes are pass-through while `guardrails.enabled` is not literal `True`. Agentic verification containment is **best-effort** software (`agentic/executor/runner.py`), not a network namespace.
7. **No unsolicited secondary telemetry** — every vendor telemetry/analytics path is disabled before the dependency that reads it initializes: canonical env maps (`utils/telemetry_kill.py`) applied at import time by every Python entry point AND delivered as literal environment at every process boundary (Docker ENV, launchers, generated launchd plists / Windows tasks / cron lines, verifier and `gh` children), plus the post-import ONNX Runtime API call at the load seams. `gate.py` prints a verification table at startup; the other appliers enforce silently and are pinned by tests + the `otel-hardening` checker. Stated precisely: these controls silence telemetry readers — they are **not** a general network kill switch, and CyClaw's *intentional* egress is governed by its own gates (see **Egress classification** below).
8. **Loopback binding** — `127.0.0.1:8787` (gateway) and `127.0.0.1:11434` (Ollama); API-key gate on all mutating endpoints; per-IP rate limiting; strict security headers + TrustedHost.

## Accepted Dependency Risks

These are tracked, deliberate exceptions — re-reviewed at every release and enforced via the `pip-audit` CI workflow.

### chromadb 1.5.9 — CVE-2026-45829 / [PYSEC-2026-311](https://osv.dev/vulnerability/PYSEC-2026-311) (Critical) and siblings CVE-2026-45830 / CVE-2026-45831 / CVE-2026-45833

- **What they are:** the Chroma **Python FastAPI server** (`chroma run` / `HttpClient` / `/api/v2`) surface. No upstream patch available for 1.5.9 as of 2026-08-26.
  - CVE-2026-45829: pre-auth RCE — embedding-function config is instantiated before the auth check (`trust_remote_code` passthrough).
  - CVE-2026-45830 ([GHSA-2wm9-hf6c-p5cr](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr)): authenticated IDOR — any authenticated HTTP user can read/write/update/delete any collection.
  - CVE-2026-45831: SimpleRBACAuthorizationProvider evaluates permissions without verifying tenant/database/collection scope.
  - CVE-2026-45833: authenticated code injection via embedding-function config for a caller with `UPDATE_COLLECTION` (post-auth sibling of 45829).
- **Why accepted:** CyClaw never runs the Chroma server. It uses the **embedded `PersistentClient`** exclusively (path from `config.yaml`), in-process, with `anonymized_telemetry=False` and no `trust_remote_code`. There is no Chroma network listener, no SimpleRBAC, and no `/api/v2` to attack; the vulnerable code paths are unreachable in this deployment. pip-audit on main first reported 45830/45831/45833 on 2026-08-25; they share the already-accepted 45829 HTTP-server surface.
- **Guardrails:** any future change introducing `chromadb.HttpClient` or a standalone Chroma server MUST be treated as a security regression and re-open this assessment.
- **Review date:** next chromadb release or 2026-10-01, whichever comes first.

### nltk 3.10.3 — pin bump (closes the 3.10.2 CVE cluster)

- **Pin:** `nltk==3.10.3` in `pyproject.toml`, `requirements.txt`, `constraints.txt`, and `environment.yml`. Dockerfile installs from those manifests.
- **Why bumped:** [#1256](https://github.com/cgfixit/CyClaw/issues/1256). CyClaw-reachable finding is [CVE-2026-81722](https://osv.dev/vulnerability/CVE-2026-81722) (PorterStemmer O(n²) DoS on a long run of `y` plus a matching suffix). `retrieval/stemmer.py` calls `PorterStemmer.stem()` on every keyword query and at index time. The rest of the 3.10.2 cluster (CVE-2026-79675 / 78680 / 79657 / 79676 / 79674 / 78682 / 81726) is the same unpatched pin; those APIs (Stanford JVM wrappers, Graphviz `dot`, pickle loaders, corpus readers, `nltk.data.load` / downloader) are not imported here.
- **Still true:** CyClaw never calls `nltk.data.load()` and never loads punkt. Tokenization stays on `_WORD_RE`. The old punkt path-traversal (CVE-2026-12243 / PYSEC-2026-597) remains unreachable; `.trivyignore` / `pip-audit` entries for it stay until a post-bump scan proves they are dead. Do not add the 3.10.2 cluster IDs to any ignore list.
- **Guardrails:** any future change introducing `nltk.data.load()`, `nltk.download()`, or `punkt`/`word_tokenize` MUST be treated as a security regression and re-open this assessment.
- **Residual:** if CVE-2026-81726 still flags 3.10.3 after merge (advisory text says “through 3.10.3”), re-triage — unused model-artifact APIs; do not pre-ignore.
- **Review date:** next nltk release or 2026-10-01, whichever comes first.

## Verification

- `python -m pytest tests/ -q` — full suite (mocked externals; no live services needed)
- `pip-audit -r requirements.txt -r requirements-test.txt` — dependency CVE sweep (also runs in CI)
- `python scripts`/swarm verification harness — config invariants, telemetry kill, due-diligence invariants, terminal contract
- Network audit: zero non-loopback connections expected in offline mode (see telemetry kill-switch docs in `docs/security-philosophy/cyclaw_telemetry_kill.env`)
- `python3 .claude/skills/otel-hardening/check_otel.py --strict` — telemetry-kill value oracle, boundary delivery, and egress-classification sweep; `bash .claude/skills/otel-hardening/verify.sh` runs its 21-scenario mutation self-test

## Egress classification

Every component that can touch a network carries exactly one class — the full
machine-readable inventory (with official source URLs, affected versions, and
review dates) lives in `.claude/skills/otel-hardening/check_otel.py`, whose
strict mode fails when a new dependency, executable, connector, scheduled job,
or launcher lands unclassified:

1. **Unsolicited telemetry/analytics, disabled via an official control** —
   LangSmith/LangChain tracing, ChromaDB PostHog + legacy 1.5.9 `CHROMA_OTEL_*`
   OTel, huggingface_hub's telemetry ping, NeMo Guardrails usage stats,
   ONNX Runtime (env `ORT_DISABLE_TELEMETRY=1` pre-import for the
   non-Windows 1DS path added in v1.29.0, plus
   `onnxruntime.disable_telemetry_events()` at the load seams — on Windows the
   ETW path only leaves the box when an external trace session collects it,
   the API cannot undo an init-time event, and absolute suppression requires a
   `--no_telemetry` private build CyClaw does not claim), the generic OTel SDK
   (with `OTEL_CONFIG_FILE`/`OTEL_EXPERIMENTAL_CONFIG_FILE` removed outright
   because declarative config outranks the SDK-disable values), GitHub CLI
   usage telemetry (`GH_TELEMETRY=false` forced on every `gh` child), and
   PowerShell host telemetry (`POWERSHELL_TELEMETRY_OPTOUT=1`, which pwsh
   reads once at its own startup — the installed cmd shim and generated task
   wrappers set it *before* the `powershell` line; setting it inside a running
   host cannot un-send that host's startup event).
2. **Ancillary update/version checks (egress, not telemetry)** — gh update
   notifiers, PowerShell update check, pip's version check, the hf CLI's
   update check and Homebrew analytics (both shell-only: no CyClaw code
   launches those programs; `macos/setup-from-clone.sh` exports
   `HOMEBREW_NO_ANALYTICS=1` before its own `brew` calls). Kept in a separate
   map (`UPDATE_CHECK_OPT_OUT`) so no report counts them as telemetry.
3. **Intentional, policy-gated feature traffic** — triple-gated Grok/Claude
   cloud fallbacks, the gated cloud-planner adapters, Telegram and OpenTweet
   (first-party httpx clients performing intentional remote API operations;
   there is no installed vendor SDK and therefore no SDK telemetry key to
   set), rclone/Dropbox corpus sync, operator-configured SQL endpoints, the
   harness's allowlist-only `/web`, and the one-time embedding-model
   bootstrap fetch (`HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` stay conditional
   on the model being cached). Never mislabeled as telemetry, never blocked
   by the kill maps.
4. **Local-only observability/storage** — `audit.jsonl`, `spend.jsonl`, and
   the Numbat projection (`logs/numbat-events.ndjsonl`): a **second sensitive
   local log**, not telemetry — every event carries hostname/username/uid
   endpoint metadata; it ships `numbat.enabled: true` and is disabled with
   `numbat.enabled: false`; no runtime HTTP sink exists or is implicitly
   configured, and it never belongs in the env kill map. Ollama traffic is
   loopback inference; the daemon's own cloud/web features are daemon policy —
   local-only mode requires `OLLAMA_NO_CLOUD=1` (or `disable_ollama_cloud`)
   set on the independently-running daemon, then a daemon restart.
5. **No mechanism found (negative findings, dated in the inventory)** — LM
   Studio (no documented telemetry env switch; its updater/model/cloud
   operations remain app-level policy), fastembed (no telemetry of its own;
   its first-use CDN model fetch is functional egress under the guardrails
   opt-in), uv, git (documented out of the overlay: it reads none of the
   canonical names), the vendored Unslop scanners, and the core
   web/runtime/dev libraries.

`CYCLAW_TELEMETRY_KILL` no longer exists: it was set in the Docker surfaces
but read by no code — a decorative marker advertising enforcement that
Python-side maps actually provided. The real canonical values now ride the
image ENV instead.
