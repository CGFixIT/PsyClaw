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
| **Telemetry / data exfil via tracing** | Telemetry-kill env vars set before any import; raw query text never persisted (hashes only) | `gate.py`, `utils/logger.py` |
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
  arbitrary user-supplied code. The agentic layer is deliberately *non-executing*
  (see §5); it is not a hardened code-exec sandbox.
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

The residual blast radius is a governed, injection-scanned JSON registry write and
read-only GitHub/SQL access — **not** untrusted code execution. MicroVM
containment would add operational weight and privileged host requirements to
isolate a process that does not execute untrusted code. It remains a **conditional
future option** (see §6) *if and only if* CyClaw ever grows an untrusted-workload
mode.

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
