---
title: "CyClaw Threat Model & Sandbox Scope"
date: 2026-06-26
tags: [security, threat-model, sandbox, hardening, scope]
related:
  - .github/SECURITY.md
  - docs/audits/SECURITY_REVIEW_STATUS.md
  - docs/SECCOMP_EBPF_HARDENING.md
  - deploy/falco/README.md
  - docs/AUTHENTICATION_DESIGN.md
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
| Network exposure | Host exposure is **exclusively** `127.0.0.1:8787` — never a non-loopback host interface. Bare-metal runs bind loopback directly; the container deployment publishes only to host loopback (`127.0.0.1:8787:8787`) while uvicorn binds the container-private network namespace (`0.0.0.0` inside the container) so the publish can reach it. **Enforced at runtime since 2026-08-08:** `gate.py`'s `main()` refuses to serve on a non-loopback `api.host` via **two** documented exceptions, both of which a deployment review must check: `CYCLAW_ALLOW_NON_LOOPBACK_BIND` being set, **or** `auth.enabled` + `api.tls.enabled` both literally `true` **and** `/query` demonstrably enforcing a credential (that third condition is probed at runtime, and is false until Stage 3 has attached `require_session_or_token` to `/query`, which happens only when `auth.enabled` is the literal boolean true). See the ninth amendment in §5 for why this was previously a convention rather than a control, and for what the second path does and does not prove. |
| Operators | **Single trusted operator** (or a small trusted home-lab/LAN). |
| Tenancy | **Single-tenant.** No mutual isolation between users is attempted. |
| Data store | Embedded ChromaDB (`PersistentClient`) + local BM25 + SQLite. No HTTP DB. |
| LLM | Local Ollama over loopback; optional Grok and/or Claude fallback (triple-gated per provider). **Since 2026-08-07 the shipped `config.yaml` satisfies two of those three gates** — `app.mode: "hybrid"` and both `models.grok.enabled` / `models.claude.enabled` are `true`. The third, `user_confirmed_online`, is per-request and cannot be pre-set by config. See the eighth amendment in §5. |
| Outbound model egress | **Three planes.** Core and agentic are not fully off by default; see the eighth amendment in §5. Core plane: two of its three gates ship satisfied (`app.mode: "hybrid"`, both providers `enabled: true`); only the per-request `user_confirmed_online` still stands. Agentic plane: `allow_cloud_providers` and both `providers.<name>.enabled` ship `true`; `agentic.enabled`, `deepagent_github.enabled`, the API-key env var, and `--confirm-online` still stand. Evaluation plane: `tests/judge_eval.py` is a standalone, default-off forensic tool requiring `CYCLAW_EVAL_LIVE=1` and `ANTHROPIC_API_KEY`; it is never imported by a production or live-request path. The core graph's triple-gated fallback is `mode==hybrid` AND `<provider>.enabled` AND `user_confirmed_online`. The out-of-band Deep Agents harness has a six-condition chain: `agentic.enabled`, `deepagent_github.enabled`, `allow_cloud_providers`, `providers.<name>.enabled`, the provider's API-key env var present, and a per-run `--confirm-online`. External destinations remain `api.x.ai` and `api.anthropic.com`; the evaluation plane permits only `api.anthropic.com`. `agentic/deepagent_github/handoff.py` implements a `HandoffEnvelope`/`sanitize_handoff` to record agentic egress as a SHA-256 of the outbound prompt, its length, the context doc ids, and a redaction count, never the prompt text. **The agentic chain has two consumers with different egress-recording states (see §5's fifth amendment):** `agentic/cli.py`'s `real-repo-run --provider`, wired to `agentic.deepagent_github.chat_client.ChatModelProposerClient`, calls `sanitize_handoff` on every real invocation. The separate, still-unwired `builder.py`/DeepAgents-graph path (`deepagent-plan`, probe-only) passes its cloud `BaseChatModel` straight to `creator(model=model, ...)` without `sanitize_handoff`; that path's egress is not recorded and remains out-of-scope follow-on work. |
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
| **Prompt injection** (direct) | 40-pattern sanitizer at `/query` and at index time | `utils/sanitizer.py`, `config.yaml` |
| **Indirect / RAG injection** (poisoned retrieved doc) | Retrieved context tagged untrusted in-prompt; topology never lets a doc redirect routing | `graph.py` (`UNTRUSTED_NOTE`, topology=policy) |
| **Corpus / memory poisoning** | Injection scan on ingestion; chunk sanitization | `retrieval/indexer.py`, `utils/sanitizer.py` |
| **Soul poisoning** (persisted identity hijack) | Soul writes require human `reason`; injection gate enforced at the write boundary; atomic `os.replace`; SHA-256 drift detection | `utils/personality.py`, `gate.py` |
| **Unauthorized soul mutation** | Fail-closed Bearer auth on all `/soul/*`; constant-time key compare | `gate.py` |
| **DNS-rebinding → state-changing POST** | `TrustedHostMiddleware` Host allow-list (outermost middleware) | `gate.py`, `config.yaml` |
| **Unauthorized cross-origin reads** | CORS allow-list | `gate.py`, `config.yaml` |
| **Uncontrolled external model calls** | Triple-gate: `mode=hybrid` **and** the selected provider's `grok.enabled`/`claude.enabled` **and** `user_confirmed_online` | `graph.py`, `config.yaml` |
| **Uncontrolled evaluation egress** | Standalone CLI refuses unless `CYCLAW_EVAL_LIVE=1` and `ANTHROPIC_API_KEY` are both present; Anthropic origin is exact-pinned; contestant is loopback-only; embedding downloads are forced offline | `tests/judge_eval.py`, `tests/test_judge_eval.py` |
| **Telemetry / data exfil via tracing** | Telemetry-kill env vars set before any third-party import from one shared mapping (`utils/telemetry_kill.py`), applied at import time by every maintained Python chokepoint — gateway, MCP server, metrics, harness server, the retrieval indexer/vector-store/cache CLIs, the auth/cert CLIs, and the sync/agentic/guardrails/telegram/opentweet packages (invariant-guard G1 pins 15 of these orderings) — AND delivered as literal environment before the interpreter starts at every process boundary: Docker ENV, the shipped launchers, generated launchd plists / Windows tasks / cron lines, verifier and `gh` children (`build_telemetry_safe_env`). An ambient value in the operator's environment is overwritten, not inherited; the declarative-OTel config names are removed outright. These controls silence telemetry readers only — they are not a network kill switch, and intentional policy-gated egress is classified separately (SECURITY.md); HF Hub network calls are additionally cut off via `local_files_only=True` once the embedding model is confirmed cached on disk (the `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE` env vars alone do not gate this in-process — huggingface_hub latches that constant at its own import time, which the eligibility probe itself triggers before the env vars are set; `local_files_only` is passed directly to `SentenceTransformer(...)` instead, which gates independently); raw query text never persisted (hashes only) | `utils/telemetry_kill.py`, `gate.py`, `mcp_hybrid_server.py`, `retrieval/vector_store.py`, `retrieval/embeddings.py`, `utils/logger.py` |
| **DoS (request flood / runaway process)** | Per-IP rate limit (60/min); container `mem`/`pids`/`cpus` limits | `utils/ratelimit.py`, `docker-compose.yml` |
| **Compromised out-of-band subprocess** (rclone/gh) | argv-list only (no `shell=True`); absolute binary paths; Docker builtin seccomp; non-root; `no-new-privileges`; `cap_drop: ALL`; read-only rootfs | `sync/`, `agentic/`, `Dockerfile`, `docker-compose.yml`, `deploy/seccomp/` |

---

## 3. What the sandbox layers DO cover

Container/OS-level controls currently enforced (see `Dockerfile` +
`docker-compose.yml`):

- **Loopback-only** publish (`127.0.0.1:8787`).
- **Non-root** runtime user (`uid:gid 1000:1000`), multi-stage minimal image.
- **`no-new-privileges:true`** — no setuid privilege escalation in-container.
- **`cap_drop: ALL`** — zero Linux capabilities.
- **Read-only root filesystem** with explicit writable carve-outs
  (`data`/`index`/`logs`/`checkpoints`/`.emb_cache` + `tmpfs:/tmp`).
- **Docker's engine-maintained builtin seccomp profile** is explicitly selected
  even if the daemon default is customized or unconfined. It
  blocks `mount`, `ptrace`, `reboot`, `bpf`, `io_uring_*`, restricted socket
  families, and other high-risk calls. The former repository profiles were
  removed because they were untraced and could not plausibly boot the gate.
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
- **A hard network boundary around the verification executor.** `agentic/executor`
  now requires `production_sandbox()` (issue #1134 Phase 4). On Windows that is a
  Job Object with `KILL_ON_JOB_CLOSE` (process-tree kill, active-process cap).
  Darwin uses `sandbox-exec` (network deny; writes limited to the worktree and
  a disposable `TMPDIR`). Linux uses `unshare --net`. Missing binary or failed
  probe fails closed — there is no silent `subprocess.run` fallback. POSIX
  timeouts kill the process group, not only the wrapper. Ordinary TCP/UDP
  sockets still work on Windows. Treat any claim that a verified worktree
  "had no network access" as unverified on Windows until a real namespace
  exists (§6, stage 5).
- **Kernel / hypervisor escape.** There is **no per-workload microVM**
  (gVisor/Firecracker). Container isolation shares the container host's Linux
  kernel. On Docker Desktop that kernel is in the managed Linux VM rather than
  macOS itself, but a container escape still compromises that VM; the desktop
  hypervisor is only the next boundary. This is acceptable only while the
  workload and host root remain trusted.
- **Hostile local root.** The host operator is trusted. CyClaw does not defend
  against a malicious root on the same machine.
- **Internet-facing / public multi-user deployment.** The loopback bind, CORS,
  and Host allow-list assume a trusted local caller. Exposing the port publicly
  voids the threat model.
- **Tight gate-specific syscall blocking.** Docker's builtin seccomp profile is
  enforced, but a trace-derived gate profile is not. It must be generated and
  replayed for the exact image on `arm64` and `amd64`; `/ops` children require
  a separate profile or a broader union policy (see §6).
- **Confidentiality against a compromised Ollama / Grok / Claude endpoint.** Prompt and
  retrieved context are sent to the configured model; trust in that endpoint is
  assumed.
- **Provider-side retention of anything sent to Grok or Claude.** Once bytes reach
  a provider, retention and processing are governed by that provider's agreement,
  not by CyClaw. This is why every egress path on the **answer** path ends in a
  per-use confirmation.

  Two qualifiers, both added 2026-08-07 after an audit found the older
  unqualified sentence ("every egress path ends in a per-use human
  confirmation") to be false as written:

  1. **`/health`'s provider-liveness probes are not on the answer path** and
     never consult `user_confirmed_online`. They are governed by
     `api.health_probe_external_providers` instead, which ships `false` — so a
     default checkout makes no such call — but when an operator opts in, that
     egress is triggered by an unauthenticated, unrate-limited route.
  2. **`user_confirmed_online` is a self-asserted request field**, not a
     server-enforced human act. `/query` is unauthenticated and the flag is a
     declared field on `QueryRequest`, so any local process can set it in a
     single POST. The confirmation pause is real in the graph
     (`user_gate_router`) and a genuine UX affordance in `terminal.html`, but
     it is a **client-side convention**, not a control that survives a hostile
     or automated local caller. Treat "a human confirmed this" as an assumption
     of the single-trusted-operator model, not as an enforced property.

---

## 5. Why microVM isolation is **not yet** required here

A 2026 review may call for gVisor/Firecracker around "agentic code that can
touch fs/sql." For the shipped CyClaw model that control remains conditional,
not because execution risk was removed, but because the operator, host, target
repository, and explicitly enabled workload are trusted and single-tenant:

- **GitHub writes are implemented and the code-level flag is armed** (amended by
  P10, then by the operator enablement of 2026-08-07 — see the fourth and
  eighth amendments in §5). `agentic/writer.py` ships `EXECUTION_ENABLED = True`
  and `agentic.mode`/`agentic.writes_enabled` ship open. What holds the line on
  a shipped checkout is now a **single** config gate — `agentic.enabled`, which
  ships `false` — plus the per-call `reason` and `confirm`, which are supplied
  by the same caller that requests the write. `execute_write()` re-runs the full
  gate on every call rather than trusting the plan it is handed, and
  `agentic.enabled` is checked first, ahead of everything else. A rollback that
  needs no source edit is available via `CYCLAW_AGENTIC_WRITE_DISABLE`.
- **SQL is read-only-guarded.** `agentic/sqlconnect/client.py` rejects every
  non-`SELECT` statement (and comments, and multi-statements) before execution.
  A *second*, distinct guard rejects side-effect **functions** by name
  (`pg_read_*`, `pg_ls_*`, `lo_export`, `dblink*`, `pg_sleep`, …). That one is
  explicitly defense-in-depth for an over-privileged DSN — the case a read-only
  connector exists to contain — and it was bypassable until 2026-08-08:
  PostgreSQL un-escapes a unicode-escaped identifier (`U&"pg_\0072ead_file"`)
  into the plain built-in *before* the grammar runs, while the name-scan saw the
  raw escaped text and matched nothing. Verified against a live PostgreSQL 16,
  including the schema-qualified form; `/etc/passwd` was read back through the
  guard. The `U&` prefix is now refused outright rather than decoded, matching
  the neighbouring `E'...'` rule — reimplementing Postgres's UIDENT rules
  (`\XXXX`, `\+XXXXXX`, surrogate pairs, a `UESCAPE` clause that redefines the
  escape character) would make any gap in that decoder a fresh bypass. The
  statement-keyword half above was never affected: those keywords cannot be
  spelled as quoted identifiers.
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

- **The shipped contract does not accept anonymous third-party workloads.** It is either nothing yet (as
  of this amendment, no live caller produces a worktree with a model-authored
  diff in it — see the module's own docstring for exactly what's wired and what
  isn't; **superseded by the third amendment below, where that caller now
  exists**), or, once a future phase wires a real planner and git-write flow, a
  patch the operator's own configured model proposed against the operator's own
  repository, already passed through the injection scan (§2), running through
  the operator's own pinned dev toolchain (`pytest`/`ruff`/the invariant guard —
  not an arbitrary command the patch gets to choose). This is a materially
  different threat than "run whatever an anonymous multi-tenant user uploads,"
  which is the threat gVisor/Firecracker primarily target. The later amendments
  supersede the "nothing yet" state: the real-repo path now exists, remains
  default-off and human-gated, and is only a soft process boundary. If the
  target repository or its tests are untrusted, Stage 5 becomes mandatory.

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
    inside the path-scoped clone `RepoWorkspaceTools.clone()` populates — still
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
  **Superseded by the third amendment below: that pipeline now exists, and
  the loop driver referenced here is a distinct, still-unwired module — see
  its own entry for what remains unconnected.**

  **[Third amendment: that pipeline now exists.]** `agentic/real_repo_loop.py::
  run_real_repo_loop` is the fusion the second amendment said didn't exist yet,
  and the first live caller of `run_verification` in this codebase. What it
  actually does, precisely: clone a real repository (`RepoWorkspaceTools`),
  ask the configured local model for a patch, write each proposed file through
  `RepoWorkspaceTools.write_file` (new in this change — the write-side
  counterpart to its existing reads, same path-safety validation as the git
  ops), and run the CALLER'S declared verification checks against the real
  worktree. It stops there — accepting a candidate does NOT commit it.
  Committing is a deliberate, separate later call, `finalize_real_repo_change`,
  driven by an explicit human `approve`/`reject` decision (a `reject` never
  touches git at all). Still local only: no `push`, no PR, no GitHub API call,
  regardless of the decision. `RepoWorkspaceTools.attach` exists so that
  decision can come from a LATER, separate process re-opening the same clone
  by path — the CLI-subprocess-per-call model I6 requires (see
  `utils/ops_runner.py`) can't hold a live Python object across two HTTP
  requests, so the clone's path on disk, not an in-memory handle, is what
  survives between "this passed its checks" and "a human said yes."

  What is gated, and how, stated exactly rather than "it's gated":

  - Three conditions checked once at the top of the run, mirroring
    `agentic/writer.py`'s own "no anonymous mutations" shape:
    `deepagent_github.allow_git_write_tools` is `True`, a non-empty human
    `reason` string, and explicit `confirm=True`. Each of `write_file`/`add`/
    `commit` ALSO re-checks `allow_git_write_tools` independently — the
    upfront check exists to fail fast (before spending a model call and a
    verification run), not to replace the low-level ones.
  - Every proposed file's content is injection-scanned
    (`inspect_candidate_text`) before verification runs; a critical finding
    skips verification entirely (no point running tests against known-bad
    content) and forces rejection.
  - Verification `checks` are a REQUIRED caller argument, never defaulted —
    `agentic.executor.default_checks()` assumes this repository's own
    toolchain (a CyClaw-specific invariant-guard path) and would be a wrong,
    invented assumption for an arbitrary configured target. An empty check
    list is rejected outright rather than silently accepting everything.
  - The git commit message is always a caller-supplied fixed string, never
    raw model output — the one place in this pipeline that deliberately does
    NOT trust the planner's own words.

  **The residual risk is real, and is exactly the executor's own risk,
  inherited unchanged.** `run_verification`'s environment scrub is still a
  soft, software-only control (§4's bullet above); it does not become a
  hard sandbox by having a real caller. A verification check that itself
  executes attacker-shaped code (a hostile test file the model proposed, that
  the caller's own declared checks happen to run) carries the same residual
  as before — a pinned working directory (not a filesystem jail), scrubbed env,
  and wall-clock timeout. API-key variables are removed, but `HOME` remains
  inherited and ordinary sockets remain available. An operator should read "accepted" as "the
  checks I named passed against this patch," not as "this patch is safe" —
  the human `reason` and `confirm` gates exist so that reading is a deliberate
  choice, not an assumption baked into the tooling.

  **[Fourth amendment, issue #1134 Phase 4 sandbox slice.]** The third
  amendment's "HOME remains inherited" line is **stale**. `run_verification`
  now assigns a disposable `HOME`/`USERPROFILE` and requires
  `production_sandbox()`: Windows Job Object (`KILL_ON_JOB_CLOSE`),
  Darwin Seatbelt (`sandbox-exec`, network deny, worktree+TMPDIR writes),
  Linux `unshare --net`. Missing backend fails closed. POSIX timeout
  kills the process group. Sockets still work on Windows.

  **[Fifth amendment, issue #1134 Phase 4 approval-manifest slice.]**
  `finalize_real_repo_change` now rebuilds an acceptance digest
  (`run_id + base HEAD + path→sha256`) and refuses approve on drift.
  Digest only in audit. This is not a cryptographic signature and not
  a fresh-clone apply (full issue §10 remaining). Reject remains a git
  no-op.

  **What still does not exist, as of this amendment:** no CLI subcommand, HTTP
  route, or background caller invokes `run_real_repo_loop` — it ships fully
  tested and standalone, the same deferred-wiring call made for every prior
  capability in this effort. No cloud-provider planner is wired here either
  (still local-only, `LocalProposerClient`). Those remain explicit future
  work, not silently implied to already exist.
  **Superseded by the fifth amendment below: both now exist.**

  **Two correctness gaps an external review caught, both fixed.** First: the
  planner received only the operator's free-text instruction and prior
  rejection feedback — never the repository content or task context. A
  `--pr`/`--issue` run's already-fetched, already-injection-scanned title,
  body, and diff were discarded before the model call, so the planner had to
  guess complete replacement files blind. `run_real_repo_loop` now accepts an
  optional, bounded `context` string folded into every iteration's prompt;
  `agentic/cli.py::cmd_real_repo_run` supplies it from the fetched bundle when
  one exists. This is plumbing, not a new read surface: the planner still
  cannot browse the clone or request specific files mid-loop. Second: because
  `write_file` mutates the same persistent clone across iterations (there is
  no reset between attempts — `feedback` is meant to build ON the prior
  attempt, not replace it), a rejected early iteration's file could remain on
  disk and be required for a LATER, accepted iteration's checks to pass, while
  only that later iteration's own file list was staged for commit. A `checks`
  configuration that depended on an earlier file could pass verification
  while the approved commit silently omitted it.
  `RealRepoLoopResult.changed_files` now unions every iteration's writes, and
  `cmd_real_repo_run`/`finalize_real_repo_change` use that union rather than
  the last iteration's own list. That union is then re-checked against
  `protected_write_paths` inside `finalize_real_repo_change` before anything is
  staged, because the list it receives comes back off a persisted JSON record
  rather than from the in-memory result the diff-scope gate cleared. The gap
  they can drift through is ordinary rather than adversarial: the decide command
  is a separate process invocation, so an operator who tightens
  `protected_write_paths` between the run and the decision would otherwise have
  the commit scoped by the policy that applied when the model proposed. The
  parameter is required, not defaulted, so no caller can skip it silently.
  Containment is a separate and already-closed concern — `tools.add` routes each
  path through `_validate_write_path`, so nothing can be staged outside the
  clone regardless.
- **The residual risk this changes is real and is named, not hidden.** A
  hostile test file (for example a `conftest.py` with a direct network or file
  operation) can run arbitrary code with the executor subprocess's privileges.
  The compensating controls are a pinned `cwd`, a scrubbed but not empty
  environment, fixed caller-declared argv, a hard per-check wall-clock timeout,
  and `check=False` so non-zero exit is data rather than an exception. None is a
  filesystem or network jail. In the shipped Compose container, the process can
  read anything UID 1000 can read and write the six mounted carve-outs. In a
  native macOS run, it executes as the operator account and can reach every
  operator-readable or operator-writable path. Both variants can use ordinary
  TCP/UDP sockets and see the inherited `HOME`. API keys are removed from the
  child environment, but files or credential helpers beneath `HOME` are not
  made unreachable.
- **What this does NOT change:** GitHub writes remain unreachable on a shipped
  checkout — though as of 2026-08-07 that is held by `agentic.enabled: false`
  alone rather than by four independent gates (see the fourth and eighth
  amendments below), SQL is still
  read-only-guarded, and filesystem writes are still triple-gated. The executor
  itself never chooses WHAT to run — the argv is fixed, non-attacker-chosen,
  operator- or config-supplied — but correcting an earlier overclaim: it does
  not follow that nothing it runs can write. Its `cwd` is the same worktree
  `run_real_repo_loop` just wrote model-authored content into, so the shipped
  default check (`pytest`) can and does write there in the ordinary course of
  test collection and execution — a hostile `conftest.py` reaching disk is
  exactly the residual risk named in the bullet above, not a separate one.

### Fourth amendment — the GitHub write path is implemented (P10)

- **What changed.** `execute_write()` was a stub that raised
  `NotImplementedError`. It now performs `gh pr create --draft` for real, and
  `RepoWorkspaceTools.push_branch()` can push one `claude/` branch to origin.
  The sentence "GitHub writes are hard-killed" was true of the code and is no
  longer; the §5 bullet above has been rewritten rather than left standing.
- **What did NOT change at P10 time: the shipped posture.** `EXECUTION_ENABLED`
  was still `False`, and `agentic.enabled` / `agentic.mode` /
  `agentic.writes_enabled` still shipped closed. Six independent gates, any one
  of which refuses (see `docs/agentic/GITHUB_WRITE_ENABLEMENT.md`'s gate-chain
  table). P10 deliberately did not flip the flag — arming it is a
  filed-checklist operator procedure, matching how the analogous fsconnect write
  enablement was handled.
  **Superseded 2026-08-07:** the checklist was signed and the flag and config
  gates were opened. See the eighth amendment. This bullet is retained as the
  P10-era record, not as a description of the current tree.
- **What is gated, exactly.** `execute_write()` checks `agentic.enabled` first
  -- an external review caught that this master switch was, until then,
  enforced only by the CLI's own disabled-no-op, not by `_require_gates()`
  itself, so a direct programmatic call could bypass it. `execute_write()` then
  requires a FRESH `confirm=True` from its own caller: an earlier version
  manufactured `confirm=True` internally when re-running the gate, which made
  that gate unconditionally satisfied rather than a real per-call check -- the
  same review caught this too. With both fixed, `execute_write()` re-runs the
  master switch plus all four numbered gates against the live config on every
  call, so possessing a plan dict is not authority to write. It rebuilds the
  argv from the plan's own params and refuses on mismatch, so a tampered
  `would_run` is inert. It refuses a plan naming a repo other than the
  configured one. It refuses any op outside `{pr_create}` even though three
  are describable. It never retries: both of `run_read`'s retry branches fire
  after the request has left the machine, so a retry could duplicate an
  accepted mutation; a timeout is reported as INDETERMINATE.
- **The residual risk, named.** With the flag armed and the config gates opened,
  this code can push a `claude/*` branch and open a draft PR against the
  configured repo as the authenticated `gh` identity. It cannot push to `main`,
  force-push, or delete. Two honest gaps: `push_branch` inherits the operator's
  ambient git credential helper (CyClaw passes no token of its own, deliberately
  — the environment it would live in is shared with the executor that runs
  model-proposed commands), and `agentic/executor`'s operator-supplied check
  argv can already run `git push` inside the clone, bypassing the `claude/*`
  scoping. The second is pre-existing, operator-supplied rather than
  model-supplied, and is called out as item H of the enablement checklist.
- **What still does not exist:** `pr_comment` and `issue_comment` execution,
  and any `/ops/*` route reaching a write. **Superseded in part by the sixth
  amendment below:** the other three items this bullet used to claim — a
  harness route reaching a write, a CLI write subcommand, and wiring into the
  real-repo loop's approve step — all now exist, and a GitHub mutation is
  network-triggerable once the flag is flipped. Only the `pr_comment`/
  `issue_comment` and `/ops/*` clauses still hold.

MicroVM containment would still add operational weight and privileged host
requirements most single-operator deployments of CyClaw cannot assume. It
remains the honest next step (§6, stage 5) if the threat model ever widens
beyond "verify a change to my own configured repo" — e.g., if this executor
were ever pointed at a repo, or dev-toolchain command, the operator did not
themselves configure. Until that widening happens, stage 5 stays conditional,
not because nothing executes, but because what executes is scoped, known, and
not attacker-chosen at the command level.

### Fifth amendment — `run_real_repo_loop` now has real callers, including one with a cloud-provider planner

- **What changed.** The third amendment's "what still does not exist" bullet
  is stale: `agentic/cli.py`'s `real-repo-run` subcommand calls
  `run_real_repo_loop` directly, and `harness/server.py`'s `POST
  /api/agent/run` reaches the same loop through the `utils.ops_runner`
  subprocess shim — a real CLI subcommand and a real HTTP route both exist
  now, not merely a tested-but-standalone module.
- **The HTTP route is not a new unauthenticated surface.** It is one of the
  harness's P9 routes: `require_api_key` (fail-closed on an unset
  `CYCLAW_API_KEY`; see the ninth amendment for the one operator opt-out, which
  is denied to non-loopback peers) plus an `Origin`/`Sec-Fetch-Site` check guard
  it, alongside the paired `GET /api/agent/runs/{id}` status read and `POST
  /api/agent/runs/{id}/decision` human approve/reject endpoint — the same
  decision point `real-repo-run-decide` already required at the CLI layer.
- **A cloud-provider planner is wired, and unlike the DeepAgents-graph path
  the egress table row above describes, this one records egress.**
  `agentic/cli.py`'s `--provider {grok,claude}` flag (paired with
  `--confirm-online`) constructs `agentic.deepagent_github.chat_client.
  ChatModelProposerClient` behind the same six-condition chain named in the
  egress table row (`allow_cloud_providers`, `providers.<name>.enabled`, the
  provider's API key, `--confirm-online`), gates checked eagerly before any
  network I/O. `ChatModelProposerClient.invoke` calls `sanitize_handoff` on
  every real invocation before the model call — the egress table row's
  caveat ("not recorded as egress") describes ONLY the separate, still-unwired
  `builder.py`/`create_deep_agent` path; it was never true of this path and
  should not be read as a blanket statement about all cloud egress in the
  agentic layer.
- **What still does not exist.** The `builder.py`/DeepAgents-graph path
  (`create_deep_agent`) remains probe-only — `agentic/cli.py`'s
  `deepagent-plan` subcommand deliberately never calls `.invoke()` on it, by
  its own docstring. **Retired (owner decision, 2026-07-31): no further
  development is planned on this path** — `run_real_repo_loop` is the one
  live real-repo coding pipeline going forward; see
  `docs/work/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`'s own
  retirement note.

### Sixth amendment — the write path is now network-reachable (still disarmed)

- **What changed.** The fifth amendment above said `push_branch`/`execute_write`
  were "still not forwarded through any harness HTTP route, which only passes
  `decision` today." That was true when written and is no longer. Both are now
  reachable three ways: `agentic.cli`'s `real-repo-run-decide --push/--publish`
  (one-shot), the standalone `real-repo-run-push`/`real-repo-run-publish`
  subcommands, and — the material change — `POST /api/agent/runs/{id}/push`
  and `POST /api/agent/runs/{id}/publish` on the harness. A third route,
  `POST .../discard`, reclaims a clone and reaches no write.
- **So "GitHub writes are un-triggerable over the network" is retired as a
  claim.** Once `EXECUTION_ENABLED` is flipped, an authenticated, same-origin
  caller on loopback can open a draft PR against an approved-and-pushed run.
  What still bounds it: `CYCLAW_API_KEY` (fail-closed, opt-out denied to
  non-loopback peers -- ninth amendment) plus an
  `Origin`/`Sec-Fetch-Site` check on every one of those routes, the run-state
  guards (`require_approved_for_push`, `require_pushed_for_publish`), and the
  six write gates themselves. The per-invocation blast radius is unchanged —
  one `claude/*` branch, one draft PR, no force-push, no delete.
- **What did NOT change at the time of this amendment: the shipped posture.**
  `EXECUTION_ENABLED` was still a hardcoded `False` that no config file could
  flip, and `allow_git_write_tools`/`agentic.enabled`/`mode`/`writes_enabled`
  all still shipped closed. Nothing in *that* amendment armed anything.
  **Superseded 2026-08-07** — the flag, `mode`, and `writes_enabled` are now
  open; `allow_git_write_tools` and `agentic.enabled` still ship `false`. See
  the eighth amendment.
- **This is the checklist's own trigger, and it fired.**
  `docs/agentic/GITHUB_WRITE_ENABLEMENT.md` item A said that if reachability
  ever changed the checklist was void and had to be re-run. It was re-run on
  2026-07-31; that document, not this one, is authoritative on the gate chain
  and carries the open operator decisions.

### Seventh amendment — Telegram channel (out-of-band, default-off)

- **What changed (2026-08-03; expanded 2026-08-04).** A new out-of-band package `telegram/` can
  reach the Telegram Bot API for outbound notify (mode `notify`) and, when
  configured, long-poll inbound chat (mode `chat`). Inbound text is turned into
  answers **only** by HTTP `POST /query` to the existing loopback CyClaw server
  (`telegram.query.base_url` is validated to loopback). Design:
  `docs/channels/TELEGRAM_DESIGN.md`.
- **What this does NOT change.** Graph topology, triple-gate hybrid, soul
  governance, agentic write disarm, and the I6 import boundary are unchanged.
  `gate.py` / `graph.py` / `mcp_hybrid_server.py` do **not** import `telegram`.
  The channel never derives `user_confirmed_online=true` from ordinary message
  text. When its default-off T3 master switch is enabled, only the exact,
  provider-specific `/online on <grok|claude>` command creates a one-shot,
  short-lived consent record; the next non-command message claims and deletes
  it before `/query`. Core `mode=hybrid` and provider-enabled gates remain the
  final authority.
- **Shipped posture.** `telegram.enabled: false`, `mode: notify`, empty
  `allowed_chat_ids`. Enabling with an empty allowlist is a **config load
  error**. CyClaw never persists the bot token: unattended jobs use
  `TELEGRAM_BOT_TOKEN` by default, while an explicit `--prompt-token` manual CLI
  run holds it only in process memory. T3 consent and T4 attachment staging are
  both independently default-off.
- **Threat surface added (honest).** Telegram's cloud sees message plaintext
  for any traffic the operator sends or receives through the bot. This is
  **not** offline end-to-end privacy for the chat channel; local RAG inference
  still runs on-box, but the *transport* is third-party. Branding and MSP
  narratives must not claim otherwise.
- **Controls.** Chat-id allowlist; loopback-only CyClaw base URL; audit events
  (`telegram_inbound` / `telegram_outbound` / `telegram_query`) with query
  hashing and token fingerprint only; no webhook listener (long-poll only);
  no multi-tenant isolation claims. T3 state contains only expiry/provider,
  uses per-session locking and atomic replacement, and is consumed before the
  query. T4 accepts only allowlisted private-chat photos/documents with an
  explicit caption confirmation, strips user-controlled filename/MIME from its
  write decision, bounds Bot API download size, requires fsconnect's enabled,
  write, strict-root, scan, injection-block, and persistent write-rate-limit
  gates; requires an absolute root outside the repository/corpus and separate
  from read roots; and runs the existing fsconnect CLI with fixed argv and
  stdin. T4 does not auto-index or write the corpus.
- **Residual risk.** A stolen bot token + knowledge of an allowlisted chat id
  can send messages as the bot and, if `mode: chat` and CyClaw is up, submit
  queries as the operator would over `/query`. Treat the token like an API key;
  rotate via BotFather if leaked. Compromised Telegram infrastructure is out of
  CyClaw's control (same class as any cloud chat product).
- **What still does not exist.** Webhook mode, media→corpus auto-ingest,
  group multi-user ACLs, Telegram routing into the terminal/harness UI, and an
  in-process scheduler inside `gate.py`. T4 remains rollout-partial pending a
  disposable-root live validation and a separately reviewed replay/ingest
  policy.

### Eighth amendment — the operator armed the write path and hybrid mode (2026-08-07)

**This amendment supersedes every "ships disarmed / off by default" statement
about the GitHub write path and the external providers elsewhere in this
document.** Where an earlier amendment says the posture did not change, it is
describing its own point in time and is retained as a historical record.

- **What happened.** The operator signed
  `docs/agentic/GITHUB_WRITE_ENABLEMENT.md`'s security-review checklist
  (`Sign-off: CG, 8.7.2026`) and, in the same commit, opened the write and
  hybrid gates. A follow-up PR reconciled the config so it validates and
  updated the tests that pinned the disarmed posture.
- **Core plane (I3), now two of three gates open.** `app.mode: "hybrid"` and
  both `models.grok.enabled` / `models.claude.enabled` ship `true`. Only
  `user_confirmed_online` remains, and it is a per-request field — see §4's
  qualifier: it is self-asserted by the caller on an unauthenticated `/query`,
  so it is a single-trusted-operator assumption, not an enforced control. An
  unset `GROK_API_KEY` / `ANTHROPIC_API_KEY` still fails the path closed
  (`is_available()` is presence-only, so a placeholder key such as the
  documented `GROK_API_KEY=dummy` satisfies it and the request still leaves
  the box before auth fails).
- **What is NOT sent.** Unchanged and still verified: `send_local_context_to_grok`
  and `send_local_context_to_claude` both ship `false`, so retrieved local
  context is excluded; the soul/identity block is never forwarded; the outbound
  payload is the query text only, capped at 8000 chars per provider.
- **Agentic plane.** `EXECUTION_ENABLED` ships `True`; `agentic.mode: "write"`
  and `agentic.writes_enabled: true` ship open; `allow_cloud_providers` and both
  `providers.<name>.enabled` ship `true`. Still closed: `agentic.enabled`,
  `deepagent_github.enabled`, `allow_git_write_tools`, `allow_github_writes`,
  `allow_filesystem_write_tools`, `allow_shell_execution`.
- **Gate depth on a GitHub mutation collapsed from six to three.**
  `agentic.enabled`, plus the per-call `reason` and `confirm` — and the latter
  two are supplied by the same request that asks for the write. **`agentic.enabled`
  is now the only standing barrier not self-attested by the caller.** Treat
  flipping `config.yaml`'s `agentic.enabled` as a High-tier change: it is no
  longer "turn on the read-only agentic CLI."
- **Rollback.** `CYCLAW_AGENTIC_WRITE_DISABLE=1` closes the code-level gate with
  no source edit. Disable-only by construction (AND-ed with `EXECUTION_ENABLED`,
  never OR-ed), so it cannot arm a build that ships disarmed.
- **Item H's sharpening is real but not yet live.** The checklist's accepted
  executor-argv gap (a `--checks-file` entry invoking `git push` reaches the
  operator's `HOME`-resident credential helper, bypassing `push_branch`'s
  `claude/*` scoping) requires `agentic.enabled` **and**
  `deepagent_github.enabled` **and** `allow_git_write_tools` — all still
  `false`, so the executor is unreachable today. What changed is that arming
  step 2 (`gh auth setup-git`) puts the ambient credential on the operator's
  machine permanently, where no in-repo control can see it. Before: two
  barriers. After: one. The harness HTTP path is unaffected — it sends check
  *profile names* against a fixed four-entry allowlist and never raw argv, so
  this remains a local-CLI-only gap.
- **A `--checks-file` is now a credential-bearing artifact.** It is parsed into
  arbitrary argv with no allowlist and executed with `HOME` present. Never
  accept one from a model, a PR diff, an issue body, or a corpus document.
- **`/health` egress.** `/health` carries neither auth nor a rate limit, and
  under `mode: hybrid` its provider-liveness probes were authenticated outbound
  calls any local process — or any page in the operator's browser, `GET` being
  CORS-simple — could trigger. Those probes are now opt-in via
  `api.health_probe_external_providers`, which ships `false`.
- **A posture regression cannot fail a build, though a config *error* can turn
  a leg red.** `invariant-guard` passes 47/47 because I1–I6 and G1–G5 are
  structural and none encodes a shipped-default posture — and invariant-guard
  is the ONLY skill checker CI runs blocking (`ci.yml`'s dedicated step).
  `config-guard` is **not** build-blocking, despite what this section used to
  claim: `ci.yml`'s `discover-skills` job does find every
  `.claude/skills/*/verify.sh` and run them as the `verify-skills` matrix, but
  that whole matrix is `continue-on-error: true` — a C1–C8/C10/C11 failure
  (or an otel-hardening oracle failure) marks the leg red without blocking
  the merge; the authoritative gate is the `test` job's unit suite + RAG
  smoke. Treat a red verify-skills leg as real work, not noise.
  A **warning** does not even turn the leg red: `C9` (external/online
  posture), `C7` (RRF-scale `min_score`) and `C12` (context arithmetic) exit
  0, and config-guard's `--strict` — which promotes warnings to failures — is
  used only inside `verify.sh`'s own mutation self-test, never against the
  shipped file. Wiring `--strict` against
  the real config would fail immediately and for the wrong reason: the hybrid
  posture is deliberate, so `C9` would have to be silenced to go green, which is
  the same "resolve a fail-closed objection by removing the objection" pattern
  this amendment warns about two bullets down. The honest statement is that the
  posture warning is advisory by design.

  *(Corrected 2026-08-07. This bullet previously said "no CI workflow runs it",
  which was wrong — the wiring is dynamic, so grepping the workflows for
  `config-guard` finds nothing and the literal search misleads. The conclusion
  it drew about posture regressions happened to be right for a different
  reason.)*
- **The rollback story is inverted.** Armed is now the shipped default, so
  disarming is the deviation: three tests pin the armed state, and a plain
  `git revert` of the flag fails CI. The `CYCLAW_AGENTIC_WRITE_DISABLE` switch
  exists precisely so a safety action does not have to fight the test suite.

---

## 6. Hardening maturity ladder

**Production isolation reality: 4/10 relative to hostile-workload containment
with gVisor/Firecracker.** The current baseline is proportionate for the stated
trusted, single-operator model, but it does not contain a container-host kernel
escape, ordinary-socket exfiltration, destructive writes inside carve-outs, or
hostile verification code. Falco is detection-only and disabled by default.

| Stage | Control | Status |
|---|---|---|
| 0 | Loopback bind, non-root, telemetry-kill, injection filter, topology invariants | ✅ Done |
| 1 | `no-new-privileges`, `cap_drop: ALL`, read-only rootfs, resource limits, explicit Docker builtin seccomp | ✅ Enforced |
| 2 | eBPF **detection** (Falco) over agentic/sync/gate, disabled-by-default | ✅ Scaffold shipped (`deploy/falco/`) |
| 3 | eBPF-profiled, tight gate-specific seccomp allowlist | 🟡 Capture procedure shipped; native traces/replay pending |
| 4 | AppArmor filesystem/network confinement; Landlock equivalent | 🟡 Opt-in single-container profile shipped; native enforcement pending; Landlock deferred |
| 5 | gVisor / Firecracker around any future *untrusted-workload* mode | ⏸ Not justified; mandatory only if the threat model changes |

Stage 3 deliberately depends on real tracing. The former 17-syscall x86-only
floor and the applied sync/rclone profile were deleted because neither was a
validated gate policy. A correct profile must be generated from real eBPF
traces, reviewed against the matching Moby default without dropping its
argument/capability filters, and replayed on both supported architectures.
Until then the gate runs under Docker's builtin. Stage 4's candidate confines
the whole gate container: external `/ops` executables fail closed, while
Python-only `/ops` children remain possible and inherit the same profile. It is
not process-role isolation; optional services need their own profiles. Stage 5
criteria and the full operator procedure are in
[`docs/SECCOMP_EBPF_HARDENING.md`](./SECCOMP_EBPF_HARDENING.md).

### Ninth amendment — the loopback bind is now a control, not a convention (2026-08-08)

The scope table in §1 has always said host exposure is *"exclusively
`127.0.0.1:8787` — never a non-loopback host interface."* Until this amendment,
nothing in the running system enforced that sentence.

**What was actually true.** `gate.py`'s `main()` read `api.host` from
`config.yaml` and passed it straight to `uvicorn.run`. `utils/config_validation.py`
— the boot-time validator whose whole job is to fail fast — never referenced
`host` at all. So a one-word edit in a working copy was sufficient to publish the
server to the network, and nothing at startup objected.

**Why the other layers did not catch it.**
`.claude/skills/config-guard/check_config.py`'s C4 does fail a non-loopback
`api.host`, and it is build-blocking in CI — but CI only ever sees config that was
committed and pushed. An operator editing their working copy and running
`python gate.py` reached no check. `security.allowed_hosts` is not a backstop
either: it ships as
`['127.0.0.1', 'localhost', '10.0.0.112', '10.0.0.111']`, so
`TrustedHostMiddleware` **admits** those LAN Hosts rather than rejecting them —
verified by probing the middleware directly with the shipped list.

**Why it matters here specifically.** `/health`, `/`, `/static/*`, and `/auth/login` carry no authentication
(`/auth/login` necessarily so -- a caller has no credential yet to present;
it is still rate-limited and same-origin-checked). `/query` is unauthenticated
when `auth.enabled` is false (the shipped default) and requires a session
or device token when it is true. A non-loopback bind with auth still off
therefore exposes the corpus (via answers), the soul-informed system prompt,
and the local model's compute to anything that can route to the port. That
is not a subtle degradation of the threat model; it is a different threat
model.

**The control.** `main()` now calls `_require_loopback_bind()` before it probes
the port or serves, and refuses with an actionable message. The whole of
`127.0.0.0/8` and `::1` are accepted (a superset of C4's literal list — anything
C4 allows, this allows); an empty host is refused because uvicorn reads it as all
interfaces, and an unresolvable hostname is refused rather than resolved, so the
bind decision never depends on DNS.

**The two ways past the guard, both of which a deployment review must check.**

1. `CYCLAW_ALLOW_NON_LOOPBACK_BIND=1` — the explicit opt-out, which logs a
   warning naming the unauthenticated routes. Note the polarity: this is
   opt-**in** to the risky state, the inverse of `agentic/writer.py`'s
   disable-only kill switch, because here the dangerous configuration is the
   non-default one.
2. `auth.enabled` **and** `api.tls.enabled` both set to the literal boolean
   `true` in `config.yaml`, **and** `/query` demonstrably carrying an
   authentication dependency. This is the durable path
   [`docs/AUTHENTICATION_DESIGN.md`](./AUTHENTICATION_DESIGN.md) §7 designs
   toward, added so a LAN operator is not left carrying an env var forever
   once the authentication stages ship.

The third condition on (2) is load-bearing and is checked at runtime rather
than assumed. The two config flags are a statement of *intent*; an operator who
sets `auth.enabled: true` reasonably believes they turned authentication on.
Stage 3 attaches `require_session_or_token` to `/query` only when that flag
is the literal boolean `true`. `gate.py`'s
`_request_path_enforcement_active()` still probes the live app for the
dependency instead of trusting the flag, so path (2) cannot open on a
quoted/`false` config and opens by itself when enforcement is actually
attached. A log warning was considered and rejected for this: the bind
happens either way, and the operator who most needs the warning is the
least likely to read it.

Both flags are compared against the literal boolean `True`, not with `bool()`.
YAML parses a quoted `enabled: "false"` as the string `"false"`, and every
non-empty string is truthy in Python — a truthiness read would have let a
stray pair of quotes open the very gate the operator was trying to keep shut.

What path (2) now also does (when launched via `python gate.py` /
`cyclaw-server`) is put TLS on the socket: `_serve` passes
`ssl_certfile`/`ssl_keyfile` when `api.tls.enabled` is the literal boolean
true and both files are readable. Missing files fail closed. The Docker
`CMD` and a hand-run `uvicorn gate:app` still bypass `_serve`.

**The boundary, stated precisely rather than overclaimed.** The guard covers
`python gate.py` and the `cyclaw-server` console script. It does **not** cover
`uvicorn gate:app` invoked directly — and, corrected under issue #1135, that
is no longer only a hand-run misuse case: the container `CMD` is exactly that
(there the bind is inside the network namespace and `docker-compose.yml` owns
exposure by publishing `127.0.0.1:8787:8787`), and the shipped macOS launcher
(`macos/invoke-cyclaw.sh`) starts the gateway as `python -m uvicorn gate:app
--host 127.0.0.1` — a supported launch path that bypasses `_serve`, so it gets
neither the bind guard (it hardcodes loopback instead) nor TLS wiring. Both
bare-uvicorn paths now receive the canonical telemetry environment *before*
uvicorn loads (the Docker ENV block; the launcher's eval of
`python -m utils.telemetry_kill --export shell`), so the earlier gap — gate's
module-level kill firing only after uvicorn's stack imported — is closed at
the environment layer. An operator who invokes uvicorn by hand with a
non-loopback host outside these two shipped paths still owns that exposure.

**Provenance.** The operator wrote this concern down first, in
`docs/zIdeas/note.txt`: *"curl requests or powershell api commands can still query
cyclaw if on same lan - need to add authentication before truly considering this
secure."* The note was investigated on the assumption the loopback bind refuted
it. It did not. This amendment closes the configuration half. Stage 3 now
enforces a credential on `/query` **when `auth.enabled` is true**; the
shipped default still leaves that path open. This still does not make
CyClaw internet-safe.

### Tenth amendment — harness `/web` DNS TOCTOU residual (2026-08-15)

The harness console `/web` fetch (`harness/web_search.py`) refuses non-global
resolved addresses in `assert_public_host` before the GET. That is a
pre-connect snapshot only: httpx re-resolves on connect, so a DNS-rebinding
window remains between the check and the socket. The residual is accepted for
this loopback, operator-allowlisted, default-off surface; a pinned-connect
transport is the future fix and is **not** in this tree. Do not treat the
pre-check as a connect-time pin.

### Eleventh amendment — per-user RBAC lands on Stage 3's auth surface (Stage 6, PR #940, merged 2026-08-15)

The ninth amendment above described Stage 3 (`require_session_or_token` on
`/query`) as a binary gate: authenticated or not. That is no longer the whole
picture. PR #940 landed Stage 6 of
[`docs/AUTHENTICATION_DESIGN.md`](./AUTHENTICATION_DESIGN.md) alongside it:
every account now carries a `role` of `admin`, `operator`, or `audit`
(`utils/authn.py`'s `ROLES`), and role is enforced, not advisory.

- **This is authorization on the same single-tenant corpus, not
  multi-tenancy.** Every authenticated user — any role — still reaches the
  same corpus, the same soul, and the same local model. §1's single-tenant
  assumption is unchanged by this amendment.
- **The `audit` role is explicitly denied `/query`.** `gate.py`'s
  `_forbid_audit_query` (invoked from the `/query` handler) checks
  `actor.role == "audit"` and refuses with `403 AUTH_ROLE_DENIED` before the
  graph is ever invoked. `admin` and `operator` may query; `audit` may only
  read (`GET /auth/audit/summary`, gated to `admin`/`audit`).
  `operator` can manage non-admin users (create, disable/enable, reset
  password) but cannot delete a user, set a role, or touch an admin account;
  only `admin` can do those. Both checks live in `gate_auth.py`
  (`_assert_can_create`, `_assert_can_touch`) and are re-checked per request,
  not cached from the session.
- **Last-admin protection is atomic, not check-then-act.** The guard against
  deleting/disabling/demoting the sole remaining enabled admin is folded
  directly into the SQL statement (`utils/authn_manager.py`'s
  `last_admin_guard` clause). SQLite serializes that statement with
  single-writer file locking. Postgres (READ COMMITTED) additionally
  `SELECT ... FOR UPDATE` on enabled admin rows in username order in the
  same transaction, so two concurrent writes against *different* admin
  rows cannot both pass the count. PR #940 review findings 1/2/5
  (`a599c35`) closed the original check-then-act window; issue #997 closed
  the cross-statement Postgres race. The same #940 fix also moved the
  `/query` 401 rejection path inside the auth dependency so it is
  rate-limited and audited like any other auth failure, rather than only
  after a downstream check.
- **Shipped posture is unchanged.** `auth.enabled` still ships `false`; RBAC
  only has an effect once an operator turns Stage 3 on. This amendment
  documents a control that now exists in the code, not a change to the
  shipped default.

### Twelfth amendment — optional memory subsystem exists, ships fully disabled (`gate_memory.py` + `memory/`)

A default-off facts/episodes store (SQLite + FTS5) now exists alongside the
corpus. Every `memory:` key in `config.yaml` ships `false`
(`memory.enabled` is the master switch; `facts`, `episodes`,
`retrieval_fusion`, `propose_apply`, `export_html`, and `consolidation` each
carry their own independent switch, also `false`). With shipped defaults,
`/query` behavior is identical to a checkout with no `memory/` package at
all — `memory/README.md` states this explicitly, and `gate_memory.py` lazy
imports the `memory` package only inside a handler, never at module load, so
an unenabled checkout never even imports it.

- **Isolation matches the rest of the out-of-band layers.** `gate.py`,
  `graph.py`, `mcp_hybrid_server.py`, and `retrieval/hybrid_search.py` carry
  no top-level `import memory` (`tests/test_memory_isolation.py`). This is
  the same I6 shape as `agentic/`/`sync`/`guardrails`, applied to a module
  that is registered onto the app (like `gate_ops.py`/`gate_auth.py`) rather
  than reached out-of-band.
- **Every mutating route requires Bearer `CYCLAW_API_KEY` (subject to the
  ninth amendment's loopback-peer opt-out) plus a non-empty `reason`.** `/memory/propose`, `/memory/apply`, and `/memory/reject` all
  route through `memory/policy.py::require_reason`; `/memory/apply`
  additionally injection-scans the payload before writing, mirroring soul's
  I5 write-path gate without overloading soul's own governance path.
- **Read routes 404, not silently empty, when disabled.** `/memory/facts`,
  `/memory/episodes`, `/memory/proposals`, and `GET /query/export/html`
  return `404` while their governing switch is `false`; only
  `/memory/status` always returns `200` with an `enabled` flag, so a probe
  can distinguish "off" from "misconfigured."
- **Privacy default inside the feature itself.** Even if `episodes.enabled`
  were turned on, `episodes.store_raw_query` ships `false` — episodes record
  a query hash, not raw text, by the same convention `utils/logger.py` uses
  for the audit log. `consolidation.enabled` is a stub wired to always
  no-op regardless of its own flag; `config.yaml` says it "must stay false
  in v1," which is a documentation intent, not a code-enforced ceiling.
- **Failures never fail `/query`.** The memory package's own contract
  (`memory/README.md`) states retrieval-fusion and episode-staging hooks are
  lazy and non-fatal — this amendment does not independently re-verify that
  claim beyond confirming the shipped switches are `false`.

### Thirteenth amendment — opt-in groundedness evaluation is a third outbound plane

`tests/judge_eval.py` is an evaluation and forensic CLI, not a server feature.
It is never imported by `gate.py`, `gate_ops.py`, `gate_auth.py`,
`gate_memory.py`, `graph.py`, `mcp_hybrid_server.py`, or `harness/`, and it is
not called by any live request path. I1-I5 remain production graph properties;
I6 isolation is locked by `tests/test_judge_eval.py`.

- **The plane is hard default-off.** The CLI returns exit 2 before indexing or
  model construction unless `CYCLAW_EVAL_LIVE` is exactly `1` and a non-empty
  `ANTHROPIC_API_KEY` is present. Required CI never sets the flag or runs the
  live script. Offline tests import helpers and verify refusal only.
- **The destination set is closed.** The judge reuses `ClaudeClient`, including
  `trust_env=False`, but first requires the configured endpoint to be exactly
  `https://api.anthropic.com/v1`; URL credentials, alternate ports, query
  strings, fragments, redirects, proxies, and alternate hosts are not allowed.
  The contestant endpoint must be loopback and its fallback is disabled. The
  script forces Hugging Face and Transformers offline before retrieval imports,
  so a missing embedding cache fails the run instead of adding another host.
- **Only synthetic fixture data may cross the boundary.** The CLI builds a
  dedicated embedded ChromaDB plus JSON BM25 index from the six fixed documents
  under `tests/fixtures/groundedness/corpus/`. It accepts no corpus, index,
  endpoint, or report path overrides and rejects unexpected source IDs and
  symlink/path escapes. It never reads `data/corpus/` or production `index/`.
- **Disclosure is explicit.** Each live judge request sends the case query, the
  local contestant answer, expected and forbidden public-safe claims, and the
  retrieved public-safe evidence to Anthropic. Those bytes are subject to the
  provider's retention, access, and processing terms. This plane is opt-in
  forensic evaluation only and must never be described as the production answer
  path or as a security control.
- **Persistence is metadata-only.** Ignored files under `logs/evals/` contain
  per-case scores, bounded reason codes, claim IDs, source IDs, token counts,
  and model/index fingerprints. They contain no raw query, answer, evidence
  excerpt, claim text, API key, or authorization header. Judge spend uses a
  dedicated `source=eval` ledger rather than contaminating production-query
  attribution.
- **Spend and judge behavior are bounded.** The fixture is exactly 24 cases,
  each client is capped at 512 output tokens, cloud retries are disabled, and
  malformed or free-form judge output fails the run. The judge is probabilistic
  measurement evidence, not formal proof. Residual risks are Anthropic retention,
  key theft, billed usage, model drift, and evaluator variance.

---

## 7. Reporting

Security issues: follow [`.github/SECURITY.md`](../.github/SECURITY.md). Resolved
findings and their status live in
[`docs/audits/SECURITY_REVIEW_STATUS.md`](./audits/SECURITY_REVIEW_STATUS.md).

---

## Ninth amendment (2026-08-15) — `security.api_key_optional`

`config.yaml`'s `security.api_key_optional` (ships **false**) is a deliberate
operator opt-out from the shared-secret gate. When true, `require_api_key`
returns without checking `CYCLAW_API_KEY` on every route it guards: `gate.py`'s
`/soul/*`, `/ops/*`, `/memory/*` and `/audit/summary`, and the harness console's
guarded set including `/api/agent/run|push|publish` and `/api/keys`. Statements
elsewhere in this document that describe those routes as unconditionally
fail-closed are qualified by this amendment. It does **not** touch the separate
session/RBAC `/auth/*` system, which stays governed by `auth.enabled`.

Two controls bound it, and the first is the load-bearing one:

1. **Loopback peer AND no forwarding header, per request.** The bypass is
   granted only when the socket peer is loopback *and* the request carries no
   `X-Forwarded-For`/`-Host`/`-Proto`, `X-Real-IP` or `Forwarded` header. The
   second half is not redundant: a reverse proxy on this host (a deployment the
   `CYCLAW_ALLOW_NON_LOOPBACK_BIND` escape hatch explicitly anticipates)
   terminates the remote connection and opens its own from `127.0.0.1`, so the
   peer check alone would grant the bypass to every caller behind that proxy.
   Header presence is the signal; the value is attacker-controlled and never
   parsed. **Residual gap:** a proxy that strips those headers is
   indistinguishable from none, so this flag must not be enabled on a host where
   anything fronts CyClaw. This is keyed on the peer rather than the
   `Host` header (attacker-supplied; `TrustedHostMiddleware` is a DNS-rebinding
   control, not authentication) and rather than the bind address (unknown to a
   request handler, and the bind guards below run only under `main()` — the
   shipped container's `CMD` is `uvicorn gate:app --host 0.0.0.0`, and
   `uvicorn harness.server:app` likewise skips the harness's own check). An
   absent ASGI `client` fails closed. `X-Forwarded-For` does not defeat it:
   `gate._serve` passes `proxy_headers=False`, and uvicorn's default
   `forwarded_allow_ips="127.0.0.1"` rewrites `scope["client"]` only when the
   real peer is already loopback.
2. **Not cross-site, per request.** CORS does not protect the API-key routes:
   a bodyless cross-origin POST is a CORS-*simple* request, so it reaches the
   handler and its side effect happens before `CORSMiddleware` withholds the
   response. The Bearer key was previously doing CSRF duty implicitly — a page
   cannot attach an `Authorization` header to a simple request without forcing
   a preflight the browser blocks — so removing the key removed that defense
   with nothing behind it. Verified exploitable before the fix: an
   `Origin: https://evil.example` POST to `/soul/reload` returned 200 and ran
   `personality.reload()`. The bypass is now refused for any request whose
   `Origin` is non-loopback or whose `Sec-Fetch-Site` is not `same-origin`/
   `none`. Header-less clients (curl, PowerShell) keep the bypass — they are
   not CSRF vectors — matching `harness/server.py`'s `_enforce_same_origin`,
   which already covered the harness for this.

3. **Bind-time refusal, defence in depth.** `_require_loopback_bind` refuses a
   non-loopback `api.host` while the flag is true, including via the auth+TLS
   route past loopback — that route proves `/query` carries a session, which
   says nothing about the API-key routes this flag opens.
   `CYCLAW_ALLOW_NON_LOOPBACK_BIND` still outranks the bind guard (an explicit
   "my own auth fronts this"), but never the peer check.

**Docker:** the flag is inert in a container. NAT rewrites the source address,
so the peer is the bridge gateway rather than loopback and the routes stay
key-gated. That is not a defect to route around: from inside the container a
request published on `127.0.0.1:8787` and one published on `0.0.0.0:8787` are
indistinguishable, so trusting the bridge would re-expose every containerised
deployment. Containers should set `CYCLAW_API_KEY`.

`config-guard`'s C13 warns when the flag is combined with a non-loopback
`api.host`. It deliberately does **not** consider `security.allowed_hosts`:
that list filters `Host` headers and opens no listening socket, so LAN names
there do not make a loopback-bound server reachable.
