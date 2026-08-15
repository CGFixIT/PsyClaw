# `sync/` — out-of-band Dropbox corpus sync

Optional rclone-bisync wrapper that keeps `data/corpus/` in step with a
Dropbox folder. Runs **strictly out-of-band** (`python -m sync.cli`);
`gate.py`, `graph.py`, and `mcp_hybrid_server.py` never import it, and it
never imports them (invariant I6). The core server reaches it only through
the `/ops/sync` subprocess shim (`utils/ops_runner.py`).

The full design, setup walkthrough, and threat-model notes live in
[`docs/SYNC_README.md`](../docs/SYNC_README.md) — that document is the
authority; this file is the in-tree map.

## Modules

| Module | Role |
|---|---|
| `cli.py` | Entry point: `setup` / `sync` / `test` / `schedule` / `unschedule` / `status`. `--dry-run` previews; `--resync` rebuilds the bisync baseline. |
| `config.py` | Loads the `sync:` block of `config.yaml`; validation and defaults. |
| `filters.py` | Generates the rclone filter file (what syncs, what never does). |
| `runner.py` | Drives `rclone bisync` as an argv-list subprocess with timeouts. |
| `scheduler.py` | Scheduled-job backends: cron (default), launchd (Darwin-only, opt-in via `sync.scheduler_backend`, writes a plist but never auto-loads it), Windows Task Scheduler. |
| `selftest.py` | Pre-flight checks behind `sync test`. |

## Exit codes (an API — keep them)

`0` success/no change · `2` operation failed · `3` env/config problem ·
`10` corpus changed → caller should reindex (`python -m retrieval.indexer`).

## Related

- Scheduling on macOS (plist generation, never auto-load):
  [`docs/work/MACOS_LAUNCHD_INTEGRATION_PLAN.md`](../docs/work/MACOS_LAUNCHD_INTEGRATION_PLAN.md)
- Corpus rules: [`data/README.md`](../data/README.md)
