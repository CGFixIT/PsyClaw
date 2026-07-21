# CyClaw Sync — Rclone Dropbox Corpus Integration

**Module:** `sync/` (Dropbox corpus sync, v1.4.0 cycle)
**Status:** Out-of-band, audit-logged, **zero new Python dependencies**, no FastAPI surface.

CyClaw's sync module mirrors a Dropbox folder into your local `data/corpus/`
without weakening any of CyClaw's security invariants. It is a thin Python
wrapper around the `rclone` binary, runs as a **separate process** (cron /
systemd timer / launchd / Task Scheduler), and emits per-file audit events into
the same `logs/audit.jsonl` the gateway uses.

> **Sync is NOT a graph node and NOT a FastAPI endpoint.** It is invoked **only**
> via `python -m sync.cli`. CyClaw's request path — `gate.py`, `graph.py`,
> `mcp_hybrid_server.py` — never imports anything from `sync/`. There is no new
> listener, no new route, and no new dependency.

---

## File layout

```
CyClaw/
├── sync/
│   ├── __init__.py        public API re-exports + __version__
│   ├── config.py          RcloneConfig dataclass + validating YAML loader
│   ├── filters.py         cyclaw_filters.txt generator (hardened denylist)
│   ├── runner.py          rclone subprocess + JSON-log parser + SHA-256 audit
│   ├── scheduler.py       cron + Task Scheduler abstraction (idempotent)
│   ├── selftest.py        pre-flight self-test
│   └── cli.py             python -m sync.cli entry point
├── tests/
│   ├── test_sync_config.py
│   ├── test_sync_filters.py
│   ├── test_sync_runner.py
│   ├── test_sync_cli.py
│   └── test_sync_scheduler.py   fully mocked, no network
└── docs/
    └── SYNC_README.md     this guide
```

The Dropbox refresh token lives **only** in `rclone.conf`, owned by your user.
CyClaw's process never sees it; it is never written to `config.yaml`, the repo,
the audit log, or any argv.

---

## Security posture (read this before you sync anything)

| Default | Why |
|---|---|
| **`direction: pull`** | `rclone copy` never deletes at the destination. One-way pull is the safest default for an RAG corpus. Bidirectional `bisync` is a silent-rewrite path into governed state. |
| **`include_soul: false`** | `data/personality/` is governed via `POST /soul/apply` with a human reason string and an injection scan. Replicating it via cron bypasses that gate. **This is the single most important path-safety rule.** |
| **Hardened exclude list** | Model weights, indices, caches, venvs, logs, secrets, `.git`, and the soul DB (`*.db*`) are all excluded by default. See `sync/filters.py`. |
| **`max_delete: 20`** | rclone aborts the run if more than 20 deletions would occur. Tune up only when you understand exactly why. |
| **Per-file SHA-256 audit** | Every added/modified file under `data/corpus/` gets a SHA-256 hash logged in `logs/audit.jsonl`. |
| **No gateway surface** | No FastAPI endpoint, no socket, no listener, no graph node/edge. The only outbound call is `rclone` → Dropbox, operator-initiated, out-of-band. |
| **Zero new deps** | stdlib + existing `pyyaml`/`utils.*` only. `rclone` is an external binary, installed out-of-band like LM Studio. |

If you flip `include_soul: true`, `python -m sync.cli setup` prints a loud
`[WARN]` and the generated filter file carries a warning header — so it is never
an accident.

### Why these invariants hold

1. **RAG-First.** Sync only changes files on disk that are later consumed at
   index time. The retrieve node remains the graph entry point. `graph.py` is
   not modified.
2. **Topology = Policy.** Sync adds no StateGraph node and no edge. Corpus
   mutation stays *outside* the graph, exactly as soul evolution does.
3. **Triple-Gated External.** Dropbox is contacted only by the `rclone`
   subprocess you explicitly invoke — never triggered by a user query, never an
   inbound surface.
4. **Audit Convergence.** Sync events terminate in the same `audit.jsonl` with
   the same PII redaction and timestamp handling as the gateway.
5. **Soul Governance.** `data/personality/**` and `*.db*` are excluded by
   default; a synced file can never overwrite `soul.md` / `cyclaw_soul.db` and
   bypass the `apply_evolution()` injection scan behind `POST /soul/apply`.

---

## Step 1 — Install rclone (**≥ 1.68.2**)

A hard floor of **rclone ≥ 1.68.2** is enforced at runtime. This version fixes
**CVE-2024-52522** (insecure symlink handling with `--links`/`--metadata`).
CyClaw never passes `--links` or `--metadata`, but the floor is asserted anyway
as defense in depth. Older rclone → `RCLONE_VERSION_TOO_OLD` (exit 3).

### Linux / macOS / WSL
```bash
curl https://rclone.org/install.sh | sudo bash
```

### Windows
```powershell
winget install Rclone.Rclone
```

Verify:
```bash
rclone version   # must show v1.68.2 or higher
```

After install, restrict the config file once you've authenticated:
```bash
chmod 600 ~/.config/rclone/rclone.conf      # Linux/macOS; restrict ACLs on Windows
```

---

## Step 2 — Create an App-Folder-scoped Dropbox remote

Use an **App Folder**-scoped Dropbox app (least privilege), **not** Full
Dropbox. Run rclone's interactive config and complete the browser OAuth flow:

```bash
rclone config
# n) New remote
# name>  dropbox_cyclaw         (must match config.yaml remote_name)
# Storage>  dropbox
# Use App Folder access (scoped), not Full Dropbox.
# Complete the browser OAuth — choose "offline access".
```

The resulting **refresh token lives only in `rclone.conf`**
(`~/.config/rclone/rclone.conf`, or `%APPDATA%\rclone\rclone.conf` on Windows),
owned by your user, managed entirely by rclone. CyClaw never holds it.

> Higher-security option: enable rclone config encryption
> (`rclone config` → `s) Set configuration password`). Trade-off: an unattended
> scheduled run then needs the password supplied (e.g. via
> `RCLONE_CONFIG_PASS`), which is its own secret to manage.

---

## Step 3 — Edit the `sync:` block in `config.yaml`

The additive `sync:` block was appended to `config.yaml`. No secrets go here —
only metadata. Absence of the block disables sync entirely.

```yaml
sync:
  enabled: false                 # off by default; `sync.cli sync` no-ops (exit 0) while false
  local_path: "data/corpus"      # validated: absolute, under repo data/corpus, a dir
  remote_name: "dropbox_cyclaw"  # must match `rclone listremotes`
  remote_path: "CyClaw/corpus"   # folder inside the Dropbox App Folder
  direction: "pull"              # "pull" (safe default) | "bisync" (opt-in, discouraged)
  include_soul: false            # leave false — data/personality/ is NOT sync-safe
  reindex_on_change: true        # exit 10 when data/corpus/** changed
  checksum: true                 # rclone --checksum (hash compare, not mtime)
  max_delete: 20                 # safety fuse: abort if > N deletions
  max_transfer: "1G"             # safety fuse: abort if run would move > this
  schedule_hour: 2               # 24h local time (cron / Task Scheduler)
  schedule_min: 0
  conflict_resolve: "newer"      # bisync-only: newer modtime wins
  conflict_loser: "rename"       # bisync-only: loser saved as .conflict1 (never deleted)
  # extra_excludes:              # optional, appended AFTER the hardened block
  #   - "scratch/**"
```

---

## Step 4 — Validate

```bash
cd /path/to/CyClaw

# Pre-flight self-test (rclone present + version, config valid,
# filter file writable, remote reachability dry-run, soul-exclusion asserted)
python -m sync.cli test

# The pytest unit suite (mocked, no network)
pytest tests/test_sync_*.py
```

With rclone absent, `python -m sync.cli test` / `status` exits **3** with
`RCLONE_NOT_INSTALLED` — that clean failure is itself the not-installed path
working as intended.

---

## Daily usage

```bash
python -m sync.cli setup             # validate config + write filter file + print OAuth steps
python -m sync.cli setup --schedule  # also register the daily job in one shot
python -m sync.cli sync --dry-run    # preview — nothing changes
python -m sync.cli sync              # live sync (pull by default)
python -m sync.cli status            # current state + last schedule
python -m sync.cli schedule          # (re-)register the daily job
python -m sync.cli unschedule        # remove the daily job
```

Sync is invoked **only** via `python -m sync.cli`; it is never imported by the
gateway or the graph.

### Exit codes

| Code | Meaning | Caller action |
|---|---|---|
| 0 | Success, no corpus change | none |
| **10** | Success **and `data/corpus/**` changed** | run `python -m retrieval.indexer`, then **restart the gateway** |
| 1 | Aborted by a safety fuse (`--max-delete` / `--max-transfer`) | investigate the remote; do **not** blindly raise the fuse |
| 2 | Sync failed (other) | inspect the audit log / rclone log |
| 3 | Config / environment problem (rclone missing or old, config invalid) | fix per the error details |

The retriever is constructed at import time in `gate.py`, so a **gateway
restart is required** to pick up the rebuilt index after a reindex.

### Cron-friendly reindex chain

```bash
python -m sync.cli sync; rc=$?
if [ "$rc" -eq 10 ]; then
    python -m retrieval.indexer       # full rebuild: Chroma + BM25
    # then restart the gateway so it loads the new index
fi
```

`retrieval.indexer` runs as a **separate process** — never inline, never in the
gateway event loop.

---

## Scheduling

`python -m sync.cli schedule` registers a single tagged daily job and is
idempotent (re-running replaces our own entry, never touches yours).

| Platform | Mechanism | Tag |
|---|---|---|
| Linux / macOS / WSL | `crontab` — one line `MIN HOUR * * * <cmd> # CYCLAW_DROPBOX_SYNC` (via `crontab -l` / `crontab -`, never `crontab -e`) | comment `CYCLAW_DROPBOX_SYNC` |
| Windows | `schtasks /Create /SC DAILY /ST HH:MM /RL LIMITED /F` | task name `CyClaw Dropbox Sync` |

The scheduled command `cd`s into the repo root (so `config.yaml` resolves) and
runs `python -m sync.cli sync`, propagating `--config` when the loaded config's
identity differs from the default (e.g. `--config /alt/cfg.yaml` at setup time).
Every path is safely escaped: the POSIX cron line is `shlex.quote`d per token,
and the Windows `.bat` launcher quotes and `%`-doubles each path — so a repo or
config path containing spaces or shell/batch metacharacters (`$()`, backticks,
`%VAR%`) is passed through literally, never interpreted. On Windows the task
points at the generated `cyclaw_sync.bat` launcher (written next to the rclone
logs) rather than an inline `cmd /c` string — this avoids the quote fragility of
passing a full command through `schtasks /TR`.

> **Overlap protection:** `run_sync` holds a single-instance OS-backed lock
> on `sync.lock` under the rclone log dir, storing PID + start timestamp while
> held. A scheduled run and a manual run therefore cannot drive rclone
> concurrently — the second exits with `SYNC_RUNTIME` rather than racing. The
> descriptor remains open through the optional post-sync check, and the OS
> releases ownership automatically on a clean exit or crash. The empty lock file
> remains for reuse; its existence does not mean a sync is active and it must not
> be manually deleted while a run is in progress.
>
> **More robust Linux option:** a systemd `--user` `Type=oneshot` service driven
> by a timer unit additionally gives journald logging and `Persistent=true`
> catch-up after downtime. Cron is the implemented portable baseline (it works on
> macOS/WSL/BSD too).

---

## Conflict resolution (bisync mode only)

`direction: bisync` is **opt-in and discouraged.** If you enable it:

| Scenario | Behavior |
|---|---|
| Both sides changed the same file | rclone detects via its `.lst` baseline |
| Winner | Newer `modtime` wins (`conflict_resolve: newer`) |
| Loser | Renamed `.conflict1` — **never silently deleted** |
| Your job | Review `.conflict*` files, delete the unwanted version |
| Next sync | Your choice propagates to both sides |

---

## What is NOT synced (filter exclusions)

| Pattern | Reason |
|---|---|
| `data/personality/**` | Soul layer — governed via `POST /soul/apply`, never via file replication |
| `*.gguf`, `*.bin`, `*.safetensors`, `*.onnx`, `*.pt`, `*.pth` | AI model weights — managed locally |
| `index/**`, `.chroma/**` | ChromaDB + BM25 — rebuildable via `python -m retrieval.indexer` |
| `.emb_cache/**` | embeddings cache — auto-downloads |
| `venv/**`, `.venv/**`, `env/**` | virtualenvs — rebuild via `pip install -r requirements.txt` |
| `__pycache__/**`, `*.pyc`, `*.pyo` | bytecode |
| `logs/**`, `*.log`, `*.jsonl` | local forensic data incl. `audit.jsonl` — never share across machines |
| `.env`, `*.pem`, `*.key`, `*_secret*`, `credentials*` | secrets |
| `*.db`, `*.db-wal`, `*.db-shm` | governed soul DB state (`cyclaw_soul.db`) |
| `.git/**`, `.gitignore` | use `git push/pull` separately |
| `.DS_Store`, `Thumbs.db`, `desktop.ini`, `.idea/**`, `.vscode/**` | OS/editor noise |
| `.rclone-state/**`, `*.rclone.lst*` | rclone's own state |

`extra_excludes:` in the `sync:` block is appended **after** the hardened block.
First-match-wins, most-restrictive-first — you can tighten further but cannot
accidentally re-include something the hardened rules already excluded.

---

## Audit events

Every sync run emits these into `logs/audit.jsonl` via `utils.logger.audit_log()`
— the same path the gateway uses, with the same PII redaction. Only metadata is
logged; never a token and never raw rclone stderr that could echo a secret.

| Event | When | Key fields |
|---|---|---|
| `sync_started` | run begins | `direction`, `dry_run`, `remote`, `local_path`, `include_soul` |
| `sync_file_added` | per file | `file`, `sha256` |
| `sync_file_modified` | per file | `file`, `sha256` |
| `sync_file_deleted` | per file | `file` (no hash — bytes gone) |
| `sync_completed` | success | `direction`, `duration_sec`, `rclone_exit_code`, `counts`, `errors_n`, `aborted_for_safety`, `dry_run`, `corpus_changed` |
| `sync_failed` | failure (rclone ran, exit ≠ 0) | same field set as `sync_completed` |
| `sync_failed` | **exceptional** exit (timeout, retry-budget exhaustion, subprocess error) | same field set as above, **plus `error_type`** (the exception's type name only — never its message); `rclone_exit_code` is the last observed code or `null` if rclone never returned one. Any change evidence harvested before the exception fired is preserved (e.g. a file copied just before a timeout still counts toward `corpus_changed`/`counts`). |

No field is ever named `query` (that would be SHA-256-hashed by the logger).

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `RCLONE_NOT_INSTALLED` (exit 3) | Install rclone (Step 1) |
| `RCLONE_VERSION_TOO_OLD` (exit 3) | Upgrade to **v1.68.2** or higher (CVE-2024-52522 fix) |
| `SYNC_CONFIG_INVALID` (exit 3) | Check the `sync:` block in `config.yaml` — error details name the failing field |
| `aborted_for_safety: true` (exit 1) | A safety fuse tripped (`--max-delete`/`--max-transfer`). Either many files were genuinely changed upstream (raise the fuse only if intentional) or the remote is wrong — investigate, don't blindly raise it. |
| `SYNC_CONFIG_INVALID` naming an unknown `sync:` key (exit 3) | A typo such as `max_delte` is now **fatal** (fail closed): a misspelled safety fuse would otherwise silently keep its default while the operator believes it is set. Correct the key name — the error details list the offending keys. (`enabled` is exempt: it is CyClaw's own on/off toggle, not an rclone parameter.) |
| `SYNC_SCHEDULER_ERROR` | `crontab`/`schtasks` not on PATH (e.g. running schtasks under WSL), or the scheduler write failed — see the error details |
| Soul changed on a second machine | `include_soul` was set to true. Set it back to false and rebuild soul from the canonical machine's `data/personality/` via `POST /soul/apply`. |
| Stale answers after a sync | The gateway caches the index at import time. After exit 10 + reindex, **restart the gateway**. |

---

## Why rclone (and not Maestral or the Dropbox SDK)

| Tool | Verdict |
|---|---|
| **rclone** ✅ | Battle-tested transport, **zero Python deps**, runs as a separate process, refresh token stays in `rclone.conf`. The CLI surface is the security boundary. Native Windows support. |
| Maestral | Always-on **daemon** — wrong shape for an offline-first agent; no native Windows. |
| `dropbox` Python SDK | Forces CyClaw to **hold the refresh token** and drags `requests`/`urllib3` into a deliberately minimal dependency tree. |
