# CyClaw Agentic Layer — User Guide (v0.1, experimental)

**Status (2026-08-04):** Still experimental / default-off. Beyond the original
GitHub-context + skills-registry surface, this layer also owns
`real_repo_loop`, fsconnect/sqlconnect CLIs, and (retired but retained)
`deepagent_github` subgraph code — see §9 and
`docs/agentic/GITHUB_WRITE_ENABLEMENT.md`. Primary invocation remains
`python -m agentic.cli` (or authenticated harness/`/ops/*` subprocess shims).
The harness console (`python -m harness.server`) is a **sibling** package:
`/skills` / `/tools` are wiring diagrams, `/goal`+`/loop` are chat-only, and
`/web` is allowlist-only GET — none of them write this registry. See
[`harness/README.md`](../../harness/README.md).

An **opt-in, out-of-band** layer that gives CyClaw read-only GitHub context and a
governed local skills registry. It runs strictly as `python -m agentic.cli` and is
**never imported** by the gateway, graph, or MCP server — so it cannot affect
retrieval, routing, or the MCP surface. **Disabled by default.**

> Security posture in one line: reads are local metadata via the `gh` CLI
> (argv-list, no shell, audited, no token forwarded by CyClaw); **GitHub writes
> are implemented (`gh pr create --draft`) and ARMED** (`EXECUTION_ENABLED = True`,
> `mode: write`, `writes_enabled: true`) but the layer master switch
> (`agentic.enabled`) still ships false — enable the layer, then supply reason +
> confirm per call. A separate plan → patch → verify → commit pipeline
> (`agentic/real_repo_loop.py`) can commit inside a jailed clone when governed —
> push/PR still need their own gates. See §5 and
> `docs/agentic/GITHUB_WRITE_ENABLEMENT.md`.

## 1. Prerequisites
- The GitHub CLI `gh` ≥ the configured floor (default `2.40.0`), authenticated
  (`gh auth login`). CyClaw never reads or forwards your token — `gh` owns it.
- Nothing else: the layer adds **no Python runtime dependency**.

## 2. Enable it
Edit the `agentic:` block in `config.yaml`:
```yaml
agentic:
  enabled: true                  # default false — flip this to turn the layer on
  repo: "cgfixit/CyClaw"         # owner/name
  mode: "write"                  # ships write; still needs enabled + reason + confirm
  writes_enabled: true
  gh_min_version: "2.40.0"
  registry_path: "data/agentic/skills_registry.json"
  allowed_read_ops: [pr_view, pr_list, pr_diff, issue_view, issue_list, repo_view]
```
While `enabled: false`, every CLI command except `status` is a clean no-op (exit 0) —
both the GitHub `context` commands **and** the skills-registry `propose-skill` /
`apply-skill` writes. The master switch fully turns the layer off; only `status`
still runs so you can confirm the disabled state (and it never writes).

## 3. Commands
```bash
python -m agentic.cli status                 # config + gh availability + registry summary
python -m agentic.cli context --repo         # repo overview + open PRs/issues
python -m agentic.cli context --pr 123        # PR metadata + diff (JSON)
python -m agentic.cli context --issue 45      # issue metadata (JSON)
python -m agentic.cli test                   # pre-flight self-test (tolerates missing gh)

# Governed skills registry (local, human-gated):
python -m agentic.cli propose-skill --name deploy --desc "..." --body-file s.md --reason "draft"
python -m agentic.cli apply-skill   --name deploy --desc "..." --body-file s.md --reason "add deploy runbook" --confirm

# Real-repo coding pipeline -- clone, plan/patch/verify, human-gated commit (see §9):
python -m agentic.cli real-repo-run --repo --instruction "..." --checks-file checks.json \
    --branch claude/topic --commit-message "..." --reason "..." --confirm

# OPTIONAL: get a plan from a capable model FIRST, review it, then have the
# LOCAL model implement it across iterations -- see §9's "Two-stage: plan with
# cloud, implement locally" for why this is a separate step, not a flag on
# real-repo-run itself:
python -m agentic.cli real-repo-run-plan --repo --instruction "..." \
    --provider grok --confirm-online --out plan.md
# Review/edit plan.md by hand, THEN feed it to a run that omits --provider --
# see §9 for what happens if you don't omit it:
python -m agentic.cli real-repo-run --repo --instruction "..." --checks-file checks.json \
    --branch claude/topic --commit-message "..." --reason "..." --plan-file plan.md --confirm

python -m agentic.cli real-repo-run-status --run-id "<id>"
python -m agentic.cli real-repo-run-decide --run-id "<id>" --decision approve   # or reject
# Escalations past the local commit -- each its own decision, both disarmed by default:
python -m agentic.cli real-repo-run-push    --run-id "<id>"                       # needs allow_git_write_tools
python -m agentic.cli real-repo-run-publish --run-id "<id>" --reason "..." --confirm  # needs EXECUTION_ENABLED

# DeepAgents-graph probe -- read-only, never invokes the agent (see §9):
python -m agentic.cli deepagent-plan --repo --instruction "..."
```

## 4. Exit codes
| Code | Meaning |
|---|---|
| 0 | success (also the clean no-op when `agentic.enabled: false`) |
| 2 | operation failed (gh error, registry error) |
| 3 | config / environment problem (gh missing or too old, config invalid) |
| 4 | a write/apply was refused by the gate (e.g. missing `--confirm`) |

This set is closed, and `main()` is what closes it: it catches
`AgenticWriteRefused` → 4, `AgenticConfigError` → 3, and any other
`AgenticError` → 2 at the dispatch point, so a subcommand that raises without
its own handler still exits inside the table. That matters because
`utils/ops_runner.py`'s `_AGENTIC_LABELS` maps exactly these four codes and
reports everything else as `"unknown"` to the `/ops/agentic` caller — an
uncaught error exiting 1 was indistinguishable from a signal or an interpreter
crash. The handler is deliberately limited to the typed hierarchy: a genuine
bug still raises a traceback rather than being flattened into a tidy exit 2.

## 5. The write gate (what still blocks a PR on a shipped checkout)
`agentic/writer.py` requires **all** of: `agentic.enabled`, `mode == "write"`,
`writes_enabled: true`, a non-empty human `reason`, and per-call `confirm` --
five gates, re-checked on every call against the live config. A sixth,
`EXECUTION_ENABLED` (code, not config), is checked first and ships `True`
(armed 2026-08-07). The default checkout still cannot publish because
`agentic.enabled` ships `false` (CLI no-ops) and every mutation still needs a
fresh reason + confirm.

With any gate closed, `execute_write()` refuses via a typed
`AgenticWriteRefused`. `pr_create` is a real, tested implementation
(`gh pr create --repo … --draft`). Only `pr_create` executes;
`pr_comment`/`issue_comment` remain plan-only. Source of truth for arming:
`docs/agentic/GITHUB_WRITE_ENABLEMENT.md`.

## 6. Auditing
Every read, refusal, and registry change emits a JSONL event via the same
`utils.logger.audit_log` path as the gateway (secrets/emails/IPs redacted):
`agentic_read`, `agentic_write_refused`, `agentic_write_dryrun`,
`agentic_skill_applied`, `agentic_skill_injection_blocked`. Inspect with
`python -m metrics` or by reading `logs/audit.jsonl`.

## 7. Troubleshooting
- **`gh not found`** → install/authenticate `gh`; until then the layer SKIPs gh
  checks and `context` returns an env error (exit 3).
- **`apply-skill` refused (exit 4)** → add `--confirm` and a `--reason`.
- **Injection blocked** → the skill body matched a banned pattern; revise it. This
  is the same gate that protects the soul.
- **`registry_path must resolve under data/`** → point it inside the repo `data/` tree.

## 8. Tests
```bash
GROK_API_KEY=dummy pytest tests/test_agentic_*.py -q
python -m agentic.cli test
```

## 9. Governed GitHub coding harness

Two distinct subsystems share this directory tree; do not conflate them.

**The real, live pipeline** (`agentic/real_repo_loop.py`): clones a real
repository (`agentic.deepagent_github.repo_workspace.RepoWorkspaceTools`,
gated on `deepagent_github.allow_git_write_tools`, ships `false`), asks a
configured model (local by default, or a gated cloud provider via
`--provider`/`--confirm-online`) for a patch, writes it into the clone, and
verifies it with `agentic/executor/`'s real `pytest`/`ruff`/invariant-guard
subprocesses against that worktree -- an accepted candidate is a real git
commit, gated behind a separate human decision
(`real-repo-run-decide`/the harness's approve-reject endpoint). It never
pushes or opens a GitHub PR on its own (`agentic/writer.py`, §5, is the only
path that can, and remains disarmed). Reachable via `agentic.cli`'s
`real-repo-run`/`real-repo-run-status`/`real-repo-run-decide` subcommands and,
authenticated, via the harness's `POST /api/agent/run` /
`GET /api/agent/runs/{id}` / `POST /api/agent/runs/{id}/decision` routes.

**Two-stage: plan with cloud, implement locally.** `real-repo-run-plan`
(`agentic/real_repo_loop.py`'s `generate_plan`) is a separate, one-shot
subcommand: it asks a model for a short implementation plan (files to touch,
one-line rationale each — never code) and prints or writes it, with **no
clone, no iteration, no write of any kind**. The design rationale, stated in
`generate_plan`'s own docstring: a capable (typically cloud) model reasons
about the approach *once*; a human reads and approves the result; a cheaper
local model then implements it across however many iterations that takes.
Pass the approved plan to `real-repo-run` via `--plan-file` and it is folded
into every iteration's prompt ahead of any GitHub context.

**`--provider`/`--confirm-online` mean two different things depending on
which subcommand carries them** — this is easy to get backwards:
- On `real-repo-run-plan`, `--provider` drives *only* the one-shot plan call.
- On `real-repo-run` itself, `--provider` drives *every iteration of the
  whole loop* — the cloud model proposes every patch attempt, not just the
  plan. `real-repo-run` and `real-repo-run-plan` each read `--provider`
  independently; there is no cross-check between them.

To get "cloud plans, local Qwen implements": pass `--provider`/
`--confirm-online` to `real-repo-run-plan` only, and **omit `--provider`
entirely on the follow-up `real-repo-run` call**. Passing `--provider` to
*both* is allowed and does something real (the plan text still reaches the
prompt) but silently defeats the two-stage economics above — the cloud model
is now billed on every `--max-iterations` attempt, not once, with no warning
from the CLI either way. As of this writing this whole two-stage recipe is
CLI-only: the harness console's `/api/agent/run` has no `--provider`/
`--plan-file` equivalent, so drive this step from a terminal even if you
otherwise use the console for the run itself.

**The DeepAgents-graph path, retired (owner decision, 2026-07-31)**
(`agentic/deepagent_github/builder.py`'s `create_deep_agent` integration,
plus the harness optimizer's fixture-based evaluation loop): this is the
subsystem the now-superseded "fixture-only evaluation... does not write the
real repo" description below used to describe accurately. It was never
live-fired against a real model -- `agentic.cli`'s `deepagent-plan`
subcommand deliberately probes the build's gate state without ever calling
`.invoke()` on the constructed agent -- and no further development is
planned on it: the pipeline above is the one live real-repo coding path
going forward. The code, its tests, and its `deepagents-harness` CI lane
remain in the repository unmodified (this is a documentation-only decision,
not a deletion), so the rest of this description stays accurate for anyone
reading it: it remains disabled by default and out-of-band. Its canonical
plan is `docs/work/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md` (see its
own retirement note); the implemented phase 6-9 controls and operator
boundaries are in `docs/work/DEEP_AGENT_HARNESS_PHASES_6_9.md`. It uses
scoped proposer-workspace tools, a virtual in-state Deep Agents backend,
local-only memory/skills, fixture-only evaluation, and human-gated local
candidate artifacts, and does not execute shell commands, write the real
repo, write GitHub, expose unrestricted filesystem tools, or import from the
core request path -- all still true of THIS subsystem specifically, not of
the agentic layer as a whole now that the pipeline above exists.

See `docs/THREAT_MODEL.md`'s third and fifth amendments for the fuller,
dated account of what changed and when.
