# `agentic/` — CyClaw Agentic Layer

**Status (2026-08-09):** Experimental, **disabled by default**, out-of-band.
Primary entry: `python -m agentic.cli` (plus sibling CLIs for filesystem and SQL).
Canonical longer guide: [`docs/agentic/AGENTIC_README.md`](../docs/agentic/AGENTIC_README.md).
Write-path enablement checklist: [`docs/agentic/GITHUB_WRITE_ENABLEMENT.md`](../docs/agentic/GITHUB_WRITE_ENABLEMENT.md).

An **opt-in** layer for governed tool calls against GitHub, local skills, a
jailed real-repo coding loop, scoped filesystem roots, and read-only SQL.
It is **never imported** by `gate.py`, `graph.py`, or `mcp_hybrid_server.py`
(I6 isolation) — so it cannot affect retrieval, routing, or the MCP surface.

> **Security posture in one line:** every subsystem ships **off**. Reads use
> audited, argv-list subprocesses (no shell). Writes need multiple independent
> gates (config flags + human reason + per-call confirm). Host shell tools and
> unrestricted filesystem tools are **not** exposed. There is no `cmd` / `ps1` /
> `bash` agent tool surface.

---

## Package map

| Path | What it does |
|---|---|
| `agentic/cli.py` | Main CLI: GitHub context, skills registry, real-repo loop, write publish |
| `agentic/config.py` | `agentic:` block schema + defaults |
| `agentic/context.py` | Read-only GitHub context (PR / issue / repo) via `gh` |
| `agentic/gh_client.py` | `gh` chokepoint: version floor, argv-list, retries, clone |
| `agentic/registry.py` | Governed local skills registry (propose / apply) |
| `agentic/writer.py` | GitHub write gate — executable op: draft `pr_create` only |
| `agentic/real_repo_loop.py` | Live plan → patch → verify → human-decides → commit pipeline |
| `agentic/real_repo_run_store.py` | Persisted run records under `workspace_root/runs/` |
| `agentic/executor/` | Sandboxed verification (`pytest` / `ruff` / custom checks) |
| `agentic/deepagent_github/` | Real-repo workspace tools + **retired** DeepAgents graph probe |
| `agentic/harness_optimizer/` | Fixture/harness optimizer + scoped proposer workspace tools |
| `agentic/fsconnect/` | Scoped filesystem connector (`python -m agentic.fsconnect.cli`) |
| `agentic/sqlconnect/` | Read-only SQL connector (`python -m agentic.sqlconnect.cli`) |
| `agentic/unslop_bridge.py` | Offline slop-detection rail for the real-repo loop; ships `unslop.enabled: false` |
| `agentic/vendor/unslop/` | Vendored UNSLOP scanners the bridge wraps (kept inside `agentic/` so they never cross I6) |
| `agentic/selftest.py` | `python -m agentic.cli test` — offline preflight for the agentic layer |

### UNSLOP prose-quality rail

`agentic/unslop_bridge.py` is an offline prose-quality check the real-repo loop
can run over a proposed patch's text. It ships **off**
(`config.yaml` → `unslop.enabled: false`, metrics to `logs/unslop.jsonl`), and
when enabled it is advisory: `agentic/real_repo_loop.py` takes it as an optional
probe, records an audit event if the probe itself raises, and appends a nudge to
rejection feedback rather than blocking a run on its own.

The vendored scanners live at `agentic/vendor/unslop/` **inside** `agentic/` on
purpose. Placing them here rather than under `utils/` means the core six
(`gate.py`, `gate_ops.py`, `gate_auth.py`, `gate_memory.py`, `graph.py`,
`mcp_hybrid_server.py`) never acquire a path to them, so the I6 module-isolation
invariant holds without needing a special case.

---

## How `harness/` and `skills_registry.json` relate

`harness/` is a **sibling** package, not a subpackage of `agentic/`. It is the
loopback coding console on `127.0.0.1:8790` (`python -m harness.server`). It
never imports `agentic` write paths; GitHub actions go through
`utils.ops_runner` → `python -m agentic.cli`, the same shim as `POST /ops/agentic`.
See [`harness/README.md`](../harness/README.md).

`data/agentic/skills_registry.json` is the **governed store** named by
`agentic.registry_path` (must resolve under the repo `data/` tree). The file
ships empty (`version: 0`, `skills: {}`, `history: []`). That is correct: it is
not a catalog of installed procedures, and it does not execute skills or feed
the RAG graph. Mutations are `propose-skill` / `apply-skill` only (master
switch + non-empty `reason` + `--confirm` + injection scan + atomic write +
sha256 history). Design: [`docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md`](../docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md).

The harness **reads** that store. `harness/registry_view.py` builds a merged
read-only view of three catalogs for `GET /api/registry` (sidebar / `/registry`):

| Catalog | Source | Who mutates it |
|---|---|---|
| Repo skills | `.claude/skills/*/SKILL.md` frontmatter | humans / PRs, not this layer |
| Governed registry | `data/agentic/skills_registry.json` | `agentic.cli apply-skill` only |
| MCP tools | AST-parsed `TOOLS` in `mcp_hybrid_server.py` | never imported (I6) |

The console slash commands `/skills` and `/tools` are **stricter** than that
merge. `/skills` reports what this console actually injects (`ponytail`,
`karpathy-guidelines`) or runs as `/agent checks` (`invariant-guard`,
`config-guard`); `/skills all` adds the repo/governed catalog. `/tools`
reports live FastAPI routes; MCP `hybrid_search` is catalog-only (`/tools all`).
`/web` is a separate allowlist-only GET (off by default) and is not an MCP
tool. Usage: [`harness/README.md`](../harness/README.md).

A fourth surface — the install-time `~/.CyClaw/skills/` copy used by the
console installers — is home-dir state, not the JSON store.

---

## Default config (shipped `config.yaml`)

Master switch is **off**. Mode / writes flags may be pre-armed in YAML so that
flipping `enabled: true` is the remaining layer gate — **per-call reason +
confirm are still mandatory** on every mutation.

```yaml
agentic:
  enabled: false                 # MASTER SWITCH — CLI no-ops (exit 0) while false
  repo: "cgfixit/CyClaw"
  mode: "write"                  # "read" | "write" (still needs enabled + reason + confirm)
  writes_enabled: true           # second write flag; not sufficient alone
  gh_min_version: "2.40.0"
  gh_timeout_sec: 30
  gh_retries: 2
  registry_path: "data/agentic/skills_registry.json"
  allowed_read_ops:
    - pr_view
    - pr_list
    - pr_diff
    - issue_view
    - issue_list
    - repo_view

  deepagent_github:
    enabled: false               # also gates real-repo-run / real-repo-run-plan
    provider: "ollama"           # "ollama" | "openai_compatible"
    base_url: "http://127.0.0.1:11434/v1"   # loopback only (validated)
    model: "qwen3.8:27b-mlx"
    planner_timeout_sec: 720
    planner_max_tokens: 3072
    allow_deepagents_dependency: false
    allow_filesystem_write_tools: false   # retired DeepAgents virtual surface only
    allow_shell_execution: false          # no host shell tool (hard-refused if true)
    allow_github_writes: false            # writer.py is the PR write boundary
    allow_git_write_tools: false          # REAL-REPO write_file/add/commit/push gate
    protected_write_paths:                # reward-hack defense (path-prefix match)
      - "tests/"
      - "conftest.py"
      - ".github/"
      - ".git/"
      - "pyproject.toml"
      - "setup.cfg"
      - "pytest.ini"
      - ".ruff.toml"
      - "ruff.toml"
      - "tox.ini"
      - "noxfile.py"
      - ".claude/"
      - ".codex/"
      - "config.yaml"
      - "CLAUDE.md"
    max_write_budget_bytes: 100000
    scan_code_shape: true
    max_handoff_chars: 200000
    workspace_root: "data/agentic/workspaces"
    allow_cloud_providers: true
    providers:
      grok:
        enabled: true
        model: "grok-4.5"
      claude:
        enabled: true
        model: "claude-sonnet-5"

  harness_optimizer:
    enabled: false
    max_iterations: 3
    require_human_confirm_for_accept: true
    output_dir: "data/agentic/harness_optimizer/runs"
    memory_dir: "data/agentic/harness_optimizer/memory"
    allow_local_model_judge: false
```

Code-level defaults (when a key is absent) lean **more conservative** than the
shipped YAML for some write flags (`mode: "read"`, `writes_enabled: false` in
`agentic/config.py`). Always treat **`config.yaml` as source of truth** for a
checkout.

Sibling connectors (also default-off):

```yaml
fsconnect:
  enabled: false
  writes_enabled: false
  allowed_fs_ops: [fs_list, fs_stat, fs_read, fs_grep, fs_glob]
  # writable_roots default to OS share folder; writes need reason + confirm

sqlconnect:
  enabled: false
  driver: "postgres"             # or "mssql"
  dsn_env: "CYCLAW_SQL_DSN"      # DSN from env only — never YAML
  read_only: true                # hard requirement in v0.1
  allow_write: false             # must stay false
  allowed_sql_ops: [schema_list, table_preview, run_select, explain, row_count]
```

---

## How to enable

### 1. Layer master switch (GitHub context + skills + real-repo CLI)

```yaml
agentic:
  enabled: true
```

While `enabled: false`, every `agentic.cli` command **except** `status` and
`test` is a clean no-op (exit 0) — with one exception: `real-repo-run-publish`
invoked without `--confirm` exits 4 (gate refusal) before the master-switch
check. `status` always reports the disabled state.

Prerequisites: GitHub CLI `gh` ≥ `gh_min_version`, authenticated (`gh auth login`).
CyClaw never reads or forwards your token.

### 2. Real-repo coding loop

```yaml
agentic:
  enabled: true
  deepagent_github:
    enabled: true
    model: "qwen3.8:27b-mlx"          # required for local planner
    allow_git_write_tools: true   # required to write/commit/push in the clone
```

Then every run still needs **`--reason` + `--confirm`**. Cloud planner/coder
needs the full six-condition chain (see below).

### 3. Draft PR publish (`pr_create`)

`EXECUTION_ENABLED = True` in `agentic/writer.py` (armed). Still requires **all** of:

| # | Gate | Shipped default |
|---|---|---|
| 0 | `agentic.enabled` | `false` |
| 1 | `mode == "write"` | `write` (open in YAML) |
| 2 | `writes_enabled` | `true` (open in YAML) |
| 3 | non-empty human `reason` | per-call |
| 4 | `confirm=True` | per-call |
| code | `EXECUTION_ENABLED` and not `CYCLAW_AGENTIC_WRITE_DISABLE` | armed |

Only **`pr_create` (always `--draft`)** executes. `pr_comment` / `issue_comment`
are plan-only. Rollback without a source edit: `CYCLAW_AGENTIC_WRITE_DISABLE=1`.

### 4. Cloud providers (Grok / Claude) for planning or coding

Six conditions, all required:

1. `agentic.enabled: true`
2. `deepagent_github.enabled: true`
3. `allow_cloud_providers: true`
4. `providers.<name>.enabled: true`
5. API key in env only — `GROK_API_KEY` or `ANTHROPIC_API_KEY` (never in YAML)
6. Per-run **`--confirm-online`**

### 5. Filesystem connector

```yaml
fsconnect:
  enabled: true
  allowed_roots: ["/path/you/allow/read"]   # required when enabled
  writes_enabled: true                      # only if you want writes
```

Writes are confined to `writable_roots` (separate list), need reason + confirm,
and hard-delete needs `allow_hard_delete: true` plus `--purge`.

### 6. SQL connector (read-only)

```yaml
sqlconnect:
  enabled: true
```

Set `CYCLAW_SQL_DSN` (or whatever `dsn_env` names). v0.1 **cannot write**
(`read_only: true`, `allow_write: false` enforced at config load).

---

## Security best practices

These are operator practices on top of the built-in gates. The gates fail closed;
this section is how to **use** them without accidentally widening blast radius.

### Least privilege — open only what you need

| Goal | Leave off / keep closed |
|---|---|
| Read GitHub context only | `enabled: true`; keep `allow_git_write_tools: false`; do not publish |
| Local plan/patch/verify | `deepagent_github.enabled` + `allow_git_write_tools`; **no** push/publish |
| Local commit only | `real-repo-run-decide --decision approve` without `--push` |
| Push branch, no PR | `real-repo-run-push` (or decide with `--push` only) |
| Draft PR | `real-repo-run-publish` — highest gate surface |
| FS reads only | `fsconnect.enabled` with `writes_enabled: false` |
| SQL | keep `read_only: true` / `allow_write: false` (required in v0.1) |

Prefer **local model** for implement iterations; use cloud (`--provider` +
`--confirm-online`) for a **one-shot plan** only when needed. Do not leave
`allow_shell_execution` or `allow_filesystem_write_tools` true — shell remains
unimplemented/hard-refused; FS write tools are for the retired harness surface,
not a host shell.

### Gate hygiene

1. **Never treat a plan dict as authority.** `execute_write` rebuilds argv and
   requires a **fresh** `confirm` from its own caller. Do not craft or replay
   plan JSON to skip a human decision.
2. **Per-call reason + confirm are not optional ceremony.** Empty reasons and
   missing `--confirm` / `--confirm-publish` / `--confirm-online` are correct
   refusals (exit 4).
3. **Booleans must be real YAML booleans.** Quoted `"true"` / `"false"` are
   strings; config validation rejects non-bools on load-bearing gates so they
   cannot silently open a switch.
4. **Do not weaken protected paths or scanners to “make a run pass.”**
   `protected_write_paths` and `scan_code_shape: true` exist to stop reward
   hacking and exfil-shaped patches. If a legitimate change hits them, fix the
   change or document a one-off exception — do not ship `scan_code_shape: false`
   as a default.
5. **Keep `agentic.enabled: false` when idle.** Turning the master switch off
   is the normal “done for the day” posture; mode/writes flags can stay armed.

### Human review is the real control

Automated checks (injection scan, code-shape heuristics, executor pytest/ruff)
are **necessary but not sufficient**.

- Always inspect `real-repo-run-status` **diff + untracked new files** before
  `approve`. An empty tracked diff can still hide brand-new files.
- Review **`--instruction`**, **`--plan-file`**, and PR/issue context as
  untrusted: paste-from-ticket is a confused-deputy path; the CLI already
  scans these, but humans still decide.
- Prefer **two-stage** workflows: cloud (or human) plan → local implement →
  human approve → optional push → optional draft PR. Separate decisions so a
  single flag cannot silently escalate.
- Prefer **draft PRs only** (enforced: `pr_create` always `--draft`). Promote
  to ready-for-review yourself after reading the PR on GitHub.

### Secrets, credentials, and identity

| Do | Don’t |
|---|---|
| Keep API keys in env only (`GROK_API_KEY`, `ANTHROPIC_API_KEY`, `CYCLAW_SQL_DSN`, `CYCLAW_API_KEY`) | Put tokens in `config.yaml`, skill bodies, plans, or commits |
| Use `gh auth login` + `gh auth setup-git` for push | Inject `GH_TOKEN` into agentic subprocess env (deliberately not allowlisted — shared with executor) |
| Confirm `gh auth status` is the identity you intend before publish | Assume the process identity is “the bot” if your laptop `gh` is personal |
| Prefer least-privilege GitHub tokens / fine-scoped PATs for automation hosts | Broad classic PATs on a shared machine |
| Treat `workspace_root` clones as sensitive (may contain secrets from the target repo) | Leave old clones forever — `real-repo-run-discard` after decide |

Rollback kill switch (no source edit): `export CYCLAW_AGENTIC_WRITE_DISABLE=1`
(disable-only; cannot arm a closed build). For a permanent rollback, also set
`agentic.enabled: false` and re-check `EXECUTION_ENABLED` / mode / writes_enabled
per [`GITHUB_WRITE_ENABLEMENT.md`](../docs/agentic/GITHUB_WRITE_ENABLEMENT.md).

### Real-repo & executor surface

- **Checks files are operator-authored trust.** The harness sends profile
  *names*, not raw argv, over HTTP — keep it that way. A local checks file of
  `{"argv": ["git", "push", ...]}` can bypass branch scoping if you author it;
  never paste untrusted argv into checks manifests.
- **Declare `--read-file` paths explicitly.** The planner cannot browse the
  clone; over-sharing secrets-bearing files into the prompt is an operator
  choice — minimize declared reads.
- **Keep clones under `data/agentic/workspaces`.** Paths are validated under the
  repo `data/` tree; do not retarget workspace roots outside the repo.
- **Discard finished runs** so model-written worktrees and credentials-adjacent
  git state do not accumulate.

### Filesystem connector

- Split **read** (`allowed_roots`) and **write** (`writable_roots`) scopes;
  never point writable roots at the CyClaw source tree or home config dirs.
- Keep `follow_symlinks: false` (required). Do not enable UNC roots unless you
  accept egress risk (`allow_unc_roots`).
- Prefer trash deletes over `--purge`; leave `allow_hard_delete: false`.
- Turn on `block_on_injection_flags` if untrusted content may be written.
- Enable write rate limits and quotas before multi-user or scripted writers.
- See [`FSCONNECT_WRITE_ENABLEMENT_PLAYBOOK.md`](../docs/agentic/FSCONNECT_WRITE_ENABLEMENT_PLAYBOOK.md).

### SQL connector

- DSN **only** from environment (`dsn_env`). Never embed passwords in YAML.
- Use a **read-only DB role** at the server even though the client enforces
  SELECT-only and session read-only.
- Keep `max_rows` and `statement_timeout_ms` tight for exploratory use.
- Do not set `allow_write: true` / `read_only: false` — config load fails closed
  in v0.1 for a reason.

### Cloud egress & data minimization

- Local planner `base_url` **must** be loopback; non-loopback is a config error
  (prevents silent remote exfil of planner prompts).
- Every cloud call needs **`--confirm-online`** — treat that as “I accept this
  prompt (instruction + context + files) leaving the machine.”
- Prefer two-stage so large file bodies go to a local model after a short cloud
  plan, not on every iteration.
- Do not enable cloud providers you do not use; an enabled provider with a live
  key while `allow_cloud_providers` is false is a **config error** (fail loud).

### Skills registry

- Apply skills only after reading the body; injection patterns block many
  jailbreaks but not semantic policy violations.
- Keep `registry_path` under `data/`; refuse paths that escape the data tree.
- Prefer `propose-skill` dry-runs before `apply-skill --confirm`.

### Audit, observability, and incident response

1. **Watch** `logs/audit.jsonl` (or `python -m metrics`) for
   `agentic_write_*`, `*_tool_denied`, `*_injection_*`, and cloud-confirm events.
2. **Investigate** unexpected `agentic_write_executed` or `push` events
   immediately — match run ids, reasons, and `gh` identity.
3. **Contain:** `CYCLAW_AGENTIC_WRITE_DISABLE=1`, set `agentic.enabled: false`,
   revoke/rotate cloud keys and `gh` credentials if compromise is plausible.
4. **Preserve** audit logs and run records under `workspace_root/runs/` before
   discarding clones if you need forensics.

### What never to build or enable casually

- Host shell / PowerShell / bash tools for the model
- Unrestricted filesystem access over the live checkout
- Skipping human approve for push/PR “to save a step”
- Importing `agentic.*` into `gate.py` / `graph.py` / MCP (breaks I6)
- Force-push, non-draft PR create, or writes to `main` (not offered; do not add)

### Quick pre-flight checklist

```text
[ ] agentic.enabled only true while actively operating
[ ] gh auth status / identity confirmed
[ ] allow_git_write_tools true only for real-repo work you intend to write
[ ] scan_code_shape true; protected_write_paths intact
[ ] No secrets in YAML, skills, plans, or --instruction paste
[ ] Cloud: key in env + --confirm-online only when needed
[ ] Diff (+ new files) reviewed before approve
[ ] Push and publish are separate, intentional decisions
[ ] Know the kill switch: CYCLAW_AGENTIC_WRITE_DISABLE=1
[ ] Self-test green: python -m agentic.cli test
```

---

## Tool-call capabilities

### A. GitHub read tools (`agentic.context` / `gh_client`)

Allowlisted ops (`allowed_read_ops`):

| Op | Purpose |
|---|---|
| `repo_view` | Repository overview |
| `pr_list` / `pr_view` / `pr_diff` | PR metadata + diff |
| `issue_list` / `issue_view` | Issue metadata |

`repo_clone` is **not** an `allowed_read_ops` entry: it is allow-listed
separately in `gh_client._READ_OPS` and only invoked internally by
`RepoWorkspaceTools.clone` (not a free-form agent tool).

Invocation:

```bash
python -m agentic.cli context --repo
python -m agentic.cli context --pr 123
python -m agentic.cli context --issue 45
```

GitHub-sourced text is injection-scanned before it is allowed into planner
prompts. Reads stay available for human inspection even when findings exist;
model-feeding paths refuse on injection / scanner-unavailable findings.

### B. Skills registry tools

Local governed registry at `registry_path` (must resolve under `data/`):

```bash
python -m agentic.cli propose-skill --name deploy --desc "..." --body-file s.md --reason "draft"
python -m agentic.cli apply-skill   --name deploy --desc "..." --body-file s.md --reason "add" --confirm
```

`apply-skill` requires master switch + reason + confirm + injection scan +
atomic write + sha256 versioning (soul propose/apply pattern).

### C. Real-repo workspace tools (`RepoWorkspaceTools`)

Jailed clone under `deepagent_github.workspace_root`. Containment uses
`fsconnect.pathsafe.ScopedRoots` for reads; git writes use argv-list `git` with
scrubbed env and `cwd` pinned to the clone.

| Tool / method | Default | What it does |
|---|---|---|
| `read_file` / `list_dir` / `stat_file` | always (once cloned) | Scoped reads inside the clone |
| `write_file` | `allow_git_write_tools` | Create/overwrite one text file (≤ 256 kB, 256,000 bytes) |
| `checkout_branch` | `allow_git_write_tools` | `git checkout -b <vendor>/<topic>` only |
| `add` | `allow_git_write_tools` | Stage validated paths |
| `commit` | `allow_git_write_tools` | Commit as CyClaw agent identity; `--no-verify` |
| `diff` / `untracked_files` | `allow_git_write_tools` | Review surface for humans |
| `push_branch` | `allow_git_write_tools` | **Only network write here** — push agent branch to origin |

Hard denials: `.git/**` paths, protected path prefixes, write-budget overruns,
code-shape scan hits (`scan_code_shape`), injection in proposed content.

Branch names must use an allowed vendor prefix (`agent/`, `grok/`, `kimi/`,
`claude/`, `codex/`, `CyClaw/`, …).

### D. Real-repo loop (live coding pipeline)

```text
clone → (optional cloud plan file) → model proposes patches → write_file
  → executor checks → pending_decision → human approve/reject
  → (optional) push → (optional) draft PR
```

```bash
# Optional two-stage: cloud plans once, local implements
python -m agentic.cli real-repo-run-plan --repo --instruction "..." \
    --provider grok --confirm-online --out plan.md
# Review/edit plan.md, then implement WITHOUT --provider:
python -m agentic.cli real-repo-run --repo --instruction "..." \
    --checks-file checks.json --branch claude/topic \
    --commit-message "..." --reason "..." --plan-file plan.md --confirm \
    --read-file path/to/existing.py

python -m agentic.cli real-repo-run-status --run-id "<id>"   # includes pending diff
python -m agentic.cli real-repo-run-decide --run-id "<id>" --decision approve  # or reject
python -m agentic.cli real-repo-run-push    --run-id "<id>"
python -m agentic.cli real-repo-run-publish --run-id "<id>" --reason "..." --confirm
python -m agentic.cli real-repo-run-discard --run-id "<id>"
```

Also reachable (authenticated) via harness routes:
`POST /api/agent/run`, `GET /api/agent/runs/{id}`, and
`POST /api/agent/runs/{id}/decision|push|publish|discard` — the full run
lifecycle including the draft-PR publish.
Two-stage `--provider` (cloud plan) is **CLI-only** today —
`real-repo-run-plan` is not in `ops_runner._AGENTIC_ACTIONS`. `--plan-file`
**is** reachable over HTTP via `POST /api/agent/run`'s `plan` body field,
which the shim materializes to a temp file.

**`--provider` semantics differ by subcommand:**

- On `real-repo-run-plan` → only the one-shot plan call
- On `real-repo-run` → every iteration of the whole loop

For “cloud plans, local implements”: pass `--provider` only to `real-repo-run-plan`.

### E. GitHub write tools (`agentic.writer`)

| Op | Plan | Execute |
|---|---|---|
| `pr_create` | yes | yes — always `--draft` |
| `pr_comment` | yes | **no** (plan-only) |
| `issue_comment` | yes | **no** (plan-only) |

### F. DeepAgents tool catalog (retired graph path)

Code retained; **no further development planned** (owner decision 2026-07-31).
`deepagent-plan` probes gate state and **never** `.invoke()`s the agent.

| Tool | Default allowed | Notes |
|---|---|---|
| `repo_context_read` | yes | Surface manifest |
| `local_repo_read` | yes | Scoped fixture/workspace file |
| `rag_search_readonly` | yes | Injected read-only RAG |
| `proposal_workspace_write_current` | `allow_filesystem_write_tools` | Write under `current/` only |
| `finish_proposal` | `allow_filesystem_write_tools` | Write `proposal.md` |
| `local_shell` | always denied in practice | Unimplemented / build-refused |
| `github_write` | always denied here | Real path is `writer.py` |

### G. Harness optimizer workspace tools (`ProposerWorkspaceTools`)

Plain Python boundary (future MCP shape). **No shell, no GitHub writes, no
holdout reads, no unrestricted FS.**

| Tool | Purpose |
|---|---|
| `list_workspace` | List visible entries (skips `holdout_hidden`) |
| `read_file` | Read one visible file (≤ 256 kB, 256,000 bytes) |
| `read_surface_manifest` | Local surface manifest |
| `read_train_failures` | Visible train artifacts |
| `read_visible_history` | Prior-attempt artifacts |
| `rag_search_readonly` | Injected RAG or empty results |
| `write_current_file` | Atomic write under `current/` only |
| `finish_proposal` | Atomic write of `proposal.md` |

### H. Filesystem connector tools (`fsconnect`)

```bash
python -m agentic.fsconnect.cli status
python -m agentic.fsconnect.cli list  --root ... --path ...
python -m agentic.fsconnect.cli read  --root ... --path ...
python -m agentic.fsconnect.cli stat  --root ... --path ...
python -m agentic.fsconnect.cli grep  --root ... --path ... --pattern ...
python -m agentic.fsconnect.cli glob  --root ... --pattern '*.md'
# Writes (gated):
python -m agentic.fsconnect.cli write   --path ... --body-file f --reason ... --confirm
python -m agentic.fsconnect.cli append  --path ... --body-file f --reason ... --confirm
python -m agentic.fsconnect.cli mkdir   --path ... --reason ... --confirm
python -m agentic.fsconnect.cli move    --src ... --dst ... --reason ... --confirm
python -m agentic.fsconnect.cli delete  --path ... --reason ... --confirm   # trash; --purge needs allow_hard_delete
python -m agentic.fsconnect.cli trash-empty / trash-restore / quota-status
python -m agentic.fsconnect.cli trash-empty-plist   # Darwin-only: generates the launchd plist, never loads it
python -m agentic.fsconnect.cli trash-empty-task     # Windows-only: generates the scheduled-task XML, never registers it
python -m agentic.fsconnect.cli index   [--apply] [--reindex]
python -m agentic.fsconnect.cli reveal
python -m agentic.fsconnect.cli test
```

Content-agnostic: never calls the LLM. Operator supplies bytes via
`--body` / `--body-file` / stdin. Symlinks under a root are always denied
(`follow_symlinks` must be false).

### I. SQL connector tools (`sqlconnect`) — read-only

```bash
python -m agentic.sqlconnect.cli status
python -m agentic.sqlconnect.cli schema
python -m agentic.sqlconnect.cli query --sql 'SELECT ...'
python -m agentic.sqlconnect.cli query --sql 'SELECT ...' --explain   # Postgres plan
python -m agentic.sqlconnect.cli query --table schema.table
python -m agentic.sqlconnect.cli query --table schema.table --count
python -m agentic.sqlconnect.cli test
```

Session-level read-only + SELECT-only query guard. No write path in v0.1.

### J. Explicitly **not** available

- Host shell / PowerShell / bash agent tools (no Grok-Build / Kimi-style shell harness)
- Unrestricted filesystem tools over the live repo
- Autonomous push/PR without human gates
- Core request-path import of any of the above

---

## Main CLI commands

```bash
python -m agentic.cli status
python -m agentic.cli context --repo | --pr N | --issue N
python -m agentic.cli propose-skill ...
python -m agentic.cli apply-skill ... --reason ... --confirm
python -m agentic.cli real-repo-run-plan ...
python -m agentic.cli real-repo-run ... --checks-file ... --branch ... --commit-message ... --reason ... --confirm
python -m agentic.cli real-repo-run-status --run-id ...
python -m agentic.cli real-repo-run-decide --run-id ... --decision approve|reject
python -m agentic.cli real-repo-run-push --run-id ...
python -m agentic.cli real-repo-run-publish --run-id ... --reason ... --confirm
python -m agentic.cli real-repo-run-discard --run-id ...
python -m agentic.cli deepagent-plan --repo --instruction "..."   # retired probe, no invoke
python -m agentic.cli test
```

### Exit codes

| Code | Meaning |
|---|---|
| 0 | success (also clean no-op when disabled) |
| 2 | operation failed |
| 3 | config / environment problem |
| 4 | write/apply refused by a gate |

Mapped by `utils/ops_runner.py` for `/ops/agentic`.

---

## Auditing

Every read, refusal, tool allow/deny, registry change, clone, git op, and write
plan/execute emits JSONL via `utils.logger.audit_log` (secrets/emails/IPs
redacted). Representative event names:

- `agentic_read`, `agentic_write_refused`, `agentic_write_dryrun`, `agentic_write_executed`
- `agentic_skill_applied`, `agentic_skill_injection_blocked`
- `agentic_repo_workspace_*`, `agentic_deepagent_tool_*`, `agentic_harness_workspace_tool_*`
- `agentic_real_repo_*`

Inspect with `python -m metrics` or `logs/audit.jsonl`.

---

## Tests

```bash
GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q
python -m agentic.cli test
python -m agentic.fsconnect.cli test
python -m agentic.sqlconnect.cli test
```

---

## Related docs

| Doc | Topic |
|---|---|
| [`docs/agentic/AGENTIC_README.md`](../docs/agentic/AGENTIC_README.md) | Full user guide |
| [`docs/agentic/GITHUB_WRITE_ENABLEMENT.md`](../docs/agentic/GITHUB_WRITE_ENABLEMENT.md) | Arming draft PR create |
| [`docs/agentic/FSCONNECT_WRITE_ENABLEMENT_PLAYBOOK.md`](../docs/agentic/FSCONNECT_WRITE_ENABLEMENT_PLAYBOOK.md) | FS write enablement |
| [`docs/agentic/FSCONNECT_SECURITY_REVIEW_CHECKLIST.md`](../docs/agentic/FSCONNECT_SECURITY_REVIEW_CHECKLIST.md) | FS security review checklist |
| [`docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md`](../docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md) | Skills registry governance |
| [`harness/README.md`](../harness/README.md) | Coding-console package (`:8790`) |
| [`macos/README.md`](../macos/README.md) | launchd glue — the fsconnect trash-empty plist generator's runtime home |
| [`docs/HARNESS_MACOS.md`](../docs/HARNESS_MACOS.md) | macOS/Linux harness install |
| [`docs/HARNESS_POWERSHELL.md`](../docs/HARNESS_POWERSHELL.md) | Windows harness install |
| [`docs/THREAT_MODEL.md`](../docs/THREAT_MODEL.md) | Threat-model amendments for this layer |
