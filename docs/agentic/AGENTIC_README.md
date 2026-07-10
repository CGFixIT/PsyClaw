# CyClaw Agentic Layer — User Guide (v0.1, experimental)

An **opt-in, out-of-band** layer that gives CyClaw read-only GitHub context and a
governed local skills registry. It runs strictly as `python -m agentic.cli` and is
**never imported** by the gateway, graph, or MCP server — so it cannot affect
retrieval, routing, or the MCP surface. **Disabled by default.**

> Security posture in one line: reads are local metadata via the `gh` CLI
> (argv-list, no shell, audited, no token forwarded by CyClaw); **writes are a
> disabled, stubbed scaffold that never executes in v0.1.**

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
```

## 4. Exit codes
| Code | Meaning |
|---|---|
| 0 | success (also the clean no-op when `agentic.enabled: false`) |
| 2 | operation failed (gh error, registry error) |
| 3 | config / environment problem (gh missing or too old, config invalid) |
| 4 | a write/apply was refused by the gate (e.g. missing `--confirm`) |

## 5. The write gate (why nothing is published)
`agentic/writer.py` requires **all** of: `mode == "write"`, `writes_enabled: true`,
a non-empty human `reason`, and per-call `confirm`. Even with all four satisfied,
v0.1 returns a **dry-run plan** (the `gh` argv it *would* run) and executes nothing
— `EXECUTION_ENABLED` is hard-`False` and the executor raises `NotImplementedError`.
Enabling real writes is a deliberate future change with its own review and tests.

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

The optional harness optimizer and LangChain Deep Agents-backed GitHub coding
harness remains disabled by default and out-of-band. Its canonical plan is
`docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md`; the implemented
phase 6-9 controls and operator boundaries are in
`docs/agentic/DEEP_AGENT_HARNESS_PHASES_6_9.md`.

The harness uses scoped proposer-workspace tools, a virtual in-state Deep Agents
backend, local-only memory/skills, fixture-only evaluation, and human-gated local
candidate artifacts. It does not execute shell commands, write the real repo,
write GitHub, expose unrestricted filesystem tools, or import from the core
request path.
