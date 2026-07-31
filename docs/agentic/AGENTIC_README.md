# CyClaw Agentic Layer — User Guide (v0.1, experimental)

An **opt-in, out-of-band** layer that gives CyClaw read-only GitHub context and a
governed local skills registry. It runs strictly as `python -m agentic.cli` and is
**never imported** by the gateway, graph, or MCP server — so it cannot affect
retrieval, routing, or the MCP surface. **Disabled by default.**

> Security posture in one line: reads are local metadata via the `gh` CLI
> (argv-list, no shell, audited, no token forwarded by CyClaw); **GitHub writes
> are implemented (`gh pr create --draft`) but shipped disarmed** by
> `EXECUTION_ENABLED = False` plus config gates (§5) — this replaced an
> earlier "stub, never executes" description once P10 landed real execution
> behind the flag. A separate, real plan → patch → verify → commit pipeline
> (`agentic/real_repo_loop.py`, wired to `real-repo-run`/the harness's
> `/api/agent/run`) DOES commit to a locally-cloned repo when governed and
> confirmed — see `docs/THREAT_MODEL.md`'s third/fifth amendments for the
> full, current picture; this doc's §5/§9 below are being kept in sync with
> it but may lag a landed change briefly.

## 1. Prerequisites
- The GitHub CLI `gh` ≥ the configured floor (default `2.40.0`), authenticated
  (`gh auth login`). CyClaw never reads or forwards your token — `gh` owns it.
- Nothing else: the layer adds **no Python runtime dependency**.

## 2. Enable it
Edit the `agentic:` block in `config.yaml`:
```yaml
agentic:
  enabled: true                  # default false
  repo: "CGFixIT/CyClaw"         # owner/name
  mode: "read"                   # keep "read"; "write" only dry-runs in v0.1
  writes_enabled: false
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
python -m agentic.cli real-repo-run-status --run-id <id>
python -m agentic.cli real-repo-run-decide --run-id <id> --decision approve   # or reject

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

## 5. The write gate (why nothing is published on a shipped checkout)
`agentic/writer.py` requires **all** of: `agentic.enabled`, `mode == "write"`,
`writes_enabled: true`, a non-empty human `reason`, and per-call `confirm` --
five gates, re-checked on every call against the live config (an earlier
version was caught manufacturing `confirm=True` internally on a re-check;
fixed). A sixth, `EXECUTION_ENABLED` (code, not config, `agentic/writer.py`),
is checked first and ships `False`.

With any gate closed, `execute_write()` refuses via a typed
`AgenticWriteRefused` -- it does NOT raise `NotImplementedError` and it is not
a stub: `pr_create` is a real, tested implementation (`gh pr create --repo …
--draft`, run via `subprocess.run`). Even with the six gates armed, only
`pr_create` executes; `pr_comment`/`issue_comment` remain describable via
`plan_write` but refuse at execution. Arming `EXECUTION_ENABLED` is a
filed-checklist operator procedure, not a code change --
`docs/agentic/GITHUB_WRITE_ENABLEMENT.md` is the enablement doc, and its
status banner is the current source of truth for whether it's armed.

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

**The still-probe-only DeepAgents-graph path**
(`agentic/deepagent_github/builder.py`'s `create_deep_agent` integration,
plus the harness optimizer's fixture-based evaluation loop): this is the
subsystem the now-superseded "fixture-only evaluation... does not write the
real repo" description below used to describe accurately. It remains
disabled by default, out-of-band, and -- unlike the pipeline above -- has
never been live-fired against a real model: `agentic.cli`'s `deepagent-plan`
subcommand deliberately probes the build's gate state without ever calling
`.invoke()` on the constructed agent. Its canonical plan is
`docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`; the implemented
phase 6-9 controls and operator boundaries are in
`docs/agentic/DEEP_AGENT_HARNESS_PHASES_6_9.md`. It uses scoped
proposer-workspace tools, a virtual in-state Deep Agents backend,
local-only memory/skills, fixture-only evaluation, and human-gated local
candidate artifacts, and does not execute shell commands, write the real
repo, write GitHub, expose unrestricted filesystem tools, or import from the
core request path -- all still true of THIS subsystem specifically, not of
the agentic layer as a whole now that the pipeline above exists.

See `docs/THREAT_MODEL.md`'s third and fifth amendments for the fuller,
dated account of what changed and when.
