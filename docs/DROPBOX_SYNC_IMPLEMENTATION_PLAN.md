# CyClaw Dropbox Corpus Sync — Implementation Planning Guide

**Status:** **Implemented** (banner added 2026-07-19) — shipped as the out-of-band `sync/` package (`sync/cli.py`, `sync/runner.py`, `sync/scheduler.py`), drivable from the terminal's Sync Console (`POST /ops/sync`) and covered by `tests/test_sync_*.py`. This document is the original planning guide, kept for design rationale; shipped behavior is documented in `Dropbox_Sync_Guide.md`.
**Target:** `main` (via feature branch → reviewed PR)
**Author:** Planning synthesis (Claude) from the PsyClaw `sync/` prior art + two research passes
**Scope item:** README roadmap v1.4.0 ("Dropbox/cloud corpus sync") / v1.5.0 ("test Dropbox corpus sync integration")
**Date:** 2026-06-20

---

## 0. TL;DR

Add an **out-of-band Dropbox → local corpus sync** as a standalone `sync/` Python package that is a **thin wrapper around the `rclone` binary** (`subprocess`, no `shell=True`), invoked only via `python -m sync.cli` from cron / systemd timer / launchd / Task Scheduler — **never imported by `gate.py`, `graph.py`, or `mcp_hybrid_server.py`.**

It:
- adds **zero** new Python dependencies (stdlib + existing `pyyaml`, `utils.logger`, `utils.errors`),
- adds **no** FastAPI endpoint and **no** LangGraph node/edge,
- writes only to the local filesystem (default: `data/corpus/`) and appends to `logs/audit.jsonl` via the existing `utils.logger.audit_log()`,
- defaults to **one-way pull** (`rclone copy`, which never deletes), with `bisync` available but discouraged,
- keeps the Dropbox refresh token **entirely inside `rclone.conf`** — CyClaw's process never sees it,
- signals "corpus changed → reindex" via a **dedicated exit code** so a wrapper script can conditionally run `python -m retrieval.indexer`.

Every one of CyClaw's existing security invariants is preserved **by construction**, because the sync code never touches the request path.

This guide is the direct CyClaw port of the validated PsyClaw `sync/` v1.0 module. The mapping is ~1:1 (identical `audit_log()` signature, identical `RAGError(message, code, details)` base, identical `data/corpus`, `data/personality/soul.md`, `index/`, `.emb_cache`, `retrieval.indexer`, and `POST /soul/apply` soul-governance gate), which makes this a **low-risk** addition.

---

## 1. Background & motivation

CyClaw is an offline-first, RAG-first personal AI gateway. Its knowledge base lives in `data/corpus/` (`.md` / `.txt`), is indexed into ChromaDB + BM25 by `retrieval.indexer`, and is retrieved hybrid-style at query time. Today the corpus is populated and edited locally. The goal of this feature is to let a user **mirror a Dropbox folder into `data/corpus/`** so the knowledge base can be maintained from multiple machines / mobile, while **not weakening any security property** and **not coupling the sync to the live request path**.

An older sibling build (**PsyClaw Sync v1.0**, May 2026) already solved this exact problem with an `rclone`-wrapper `sync/` package. This plan adapts that design to CyClaw's current `main` and re-validates every decision against (a) Dropbox/rclone best practices as of late 2025/2026 and (b) CyClaw's actual code, CI, and security tooling.

### 1.1 What CyClaw already provides that we reuse (do NOT reimplement)

| Primitive | Location | Reuse for sync |
|---|---|---|
| `audit_log(event: dict, config_path="config.yaml")` | `utils/logger.py:106` | All sync audit events terminate here (same JSONL, same PII redaction) |
| `redact_sensitive(text, cfg)` / `hash_query(query)` | `utils/logger.py:93` / `:66` | Backstop redaction; `hash_query` only if we ever hash a sensitive string |
| `_get_config()` / `reset_config_cache()` | `utils/logger.py:55` / `:62` | Cached config load; tests reset the cache |
| `RAGError(message, code, details)` | `utils/errors.py:10` | Base class for new `SyncError` / `RcloneError` |
| `retrieval.indexer` full rebuild | `retrieval/indexer.py` (`build_index`, `__main__`) | Reindex trigger, invoked as `python -m retrieval.indexer` |
| `config.yaml` single source of truth | repo root | New additive `sync:` block |
| `.gitignore` never-commit list | repo root | Mirror into the rclone filter file |

---

## 2. Non-negotiable design constraints (the invariant contract)

These are derived from CyClaw's README "five security invariants" + security model, re-mapped to this feature. **Each must be demonstrably true in the PR.**

1. **RAG-First preserved.** Sync only changes files on disk that are later consumed at index time. The `retrieve` node remains the graph entry point. No change to `graph.py`.
2. **Topology = Policy preserved.** Sync adds **no** StateGraph node and **no** edge. `graph.py` is not modified. Corpus mutation — a side-effecting operation — stays *outside* the graph, exactly as soul-evolution is kept out of the graph today.
3. **Triple-Gated External preserved.** Sync is unrelated to the Grok fallback path. The only outbound network call is `rclone` → Dropbox, **operator-initiated, out-of-band**, never triggered by a user query. No new inbound listener.
4. **Audit Convergence preserved.** Sync emits its own events through the *same* `audit_log()` into the *same* `logs/audit.jsonl`. It does not alter or bypass the graph's `audit_logger` node.
5. **Soul Governance preserved.** `data/personality/**` is **excluded by default** from sync. A synced file can never overwrite `soul.md` or `cyclaw_soul.db` and thereby bypass the `apply_evolution()` injection scan behind `POST /soul/apply`. **This is the single most important path-safety rule.** Opt-in (`include_soul: true`) is loud and discouraged.
6. **Loopback/no-listener posture preserved.** Sync adds no socket, no `Depends`, no route. `rclone` makes an *outbound* HTTPS call only; there is no inbound surface.
7. **Zero-telemetry posture preserved.** `rclone` has no LangSmith/Chroma/OTel surface, but the subprocess must run with no remote-control (`--rc`), no usage reporting, and inherit CyClaw's clean env. (`gate.py`'s telemetry-kill block is request-path only; sync sets its own minimal clean env for the child.)
8. **Minimal-deps posture preserved.** **Zero** new entries in `requirements.txt` / `constraints.txt` / `pyproject.toml` dependencies. `rclone` is an external binary installed out-of-band (just like LM Studio at `127.0.0.1:1234` already is).

---

## 3. Transport decision (validated)

**Decision: rclone binary wrapper, invoked out-of-band. Confirmed.**

| Option | Verdict for CyClaw |
|---|---|
| **rclone** ✅ | Zero Python deps; no daemon; refresh token stays in `rclone.conf` (never in CyClaw's process); single static binary on Linux/macOS/WSL/**Windows**; very mature (stable 1.72.1, Dec 2025). The CLI surface is the security boundary. |
| Maestral ❌ | Always-on **daemon** (violates "no daemons"); **no native Windows** (CyClaw targets Windows); inflates the venv. |
| `dropbox` Python SDK ❌ | Forces CyClaw to **hold the refresh token**; drags `requests`/`urllib3` into a deliberately minimal, CVE-annotated tree; you reimplement delta/retry/auth. |
| `dbxcli` ❌ | Effectively abandoned. |

**Mandatory rclone hardening (from research):**
- **Pin `rclone ≥ 1.68.2`** as a hard floor — fixes **CVE-2024-52522** (insecure symlink handling with `--links`/`--metadata`). Assert version at startup; refuse to run if older. (PsyClaw used ≥1.65; **raise CyClaw's floor to 1.68.2** for the CVE fix. If `bisync` is ever enabled, the practical floor is ≥1.66 for the snapshot-model redesign — 1.68.2 covers both.)
- **Never** pass `--links` or `--metadata` (not needed for a text corpus; avoids the CVE class entirely).
- Use an **App Folder**-scoped Dropbox app (least privilege), not Full Dropbox.
- `chmod 600 ~/.config/rclone/rclone.conf` (restrict ACLs on Windows). Document rclone config-encryption as the higher-security option (trade-off: needs a password per unattended run).

---

## 4. Sync semantics

### 4.1 Direction — default **pull** (one-way), `bisync` discouraged

- **Default `direction: pull`** → `rclone copy remote:path data/corpus`. `rclone copy` **never deletes** at the destination — safest possible default for an RAG corpus.
- `direction: bisync` is **opt-in and discouraged.** Rationale: bidirectional sync creates a second, uncontrolled write path into governed state and is an advanced rclone command with known rough edges (e.g. `--max-delete` evaluated before `--track-renames`). If ever adopted, require rclone ≥1.66, `--resync` baseline, `--check-access`, `--conflict-resolve newer`, `--conflict-loser rename` (never silent delete), and `--max-lock`.

### 4.2 Safety fuses (applied every run)

- `--max-delete N` (default **20**) — abort if more than N deletions would occur. (Only meaningful for `rclone sync`/`bisync`; harmless under `copy`.)
- `--max-transfer <size>` — stop if the run would move more than expected (returns exit 8).
- `--check-first` — do all comparisons before any transfer (deterministic ordering; no interleaved deletes-before-copies).
- `--checksum` — compare by hash+size, not mtime (immune to clock skew; Dropbox stores a content hash so this is cheap correctness).
- `--dropbox-batch-mode sync` (rclone default) — stay within Dropbox rate limits.
- `--use-json-log --stats-one-line` — machine-readable output for the wrapper to parse "what changed".
- Let rclone own transport retries via `--retries` / `--low-level-retries`. The wrapper retries only the *whole run*, bounded, with backoff, then surfaces a `SyncError` (no crash-loop — there's no daemon).
- **`--dry-run` discipline:** `sync --dry-run` previews and changes nothing.

### 4.3 Filtering — hardened exclude list (denylist), corpus-scoped

Two valid styles surfaced in research:
- **Denylist** (PsyClaw style): exclude the dangerous/rebuildable categories, sync the rest.
- **Allowlist** (corpus-only): exclude everything, then `+ /*.md`, `+ /*.txt`.

**Recommendation: ship the denylist as the hardened baseline** (it's battle-tested in PsyClaw and fails safe even if the remote layout changes), and document the allowlist as an optional tightening via `extra_excludes`. The filter file is generated by `sync/filters.py` to the rclone state dir and is `--filter-from`'d on every run. First-match-wins, most-restrictive-first.

Built-in hardened excludes (mirror of `.gitignore` never-commit categories), in order:

```
- data/personality/**          # SOUL LAYER — governed via POST /soul/apply only
- *.gguf, *.bin, *.safetensors, *.onnx, *.pt, *.pth   # model weights
- index/**, .emb_cache/**, .chroma/**                 # rebuildable indices/caches
- venv/**, .venv/**, env/**, __pycache__/**, *.pyc, *.pyo, *.egg-info/**
- logs/**, *.log, *.jsonl      # local forensic data incl. audit.jsonl — never share
- .env, .env.*, *.env, *.pem, *.key, *_secret*, credentials*   # secrets
- *.db, *.db-wal, *.db-shm     # governed soul DB state
- .git/**, .gitignore          # use git separately
- .DS_Store, Thumbs.db, desktop.ini, *.swp, .idea/**, .vscode/**   # OS/editor noise
- .rclone-state/**, *.rclone.lst*    # rclone's own state
```

`extra_excludes:` from config is appended **after** the hardened block (users can tighten further, never accidentally re-include something the hardened rules already excluded — first-match-wins guarantees this).

### 4.4 Reindex trigger — only on actual corpus change

`retrieval.indexer.build_index()` is a **full rebuild** (deletes + recreates the Chroma collection, overwrites `bm25.json`) — it re-embeds the entire corpus and is expensive. Therefore:
- The wrapper parses rclone's JSON stats / log to detect whether any file under `data/corpus/**` was added/modified/deleted.
- If yes → CLI exits **10** ("corpus changed → reindex recommended"); a cron wrapper conditionally runs `python -m retrieval.indexer` as a **separate process** (never inline, never in the gateway event loop).
- If no change → exit 0; no reindex.
- A gateway restart is required to pick up the rebuilt index (the retriever is constructed at import time in `gate.py`).

---

## 5. Proposed file layout

All new code lives under `sync/` plus two small additive edits to existing files.

```
CyClaw/
├── sync/                          # NEW package — never imported by gate/graph/mcp
│   ├── __init__.py                # public API re-exports + __version__
│   ├── config.py                  # RcloneConfig dataclass + validating loader of config.yaml `sync:` block
│   ├── filters.py                 # hardened filter-file generator (denylist above)
│   ├── runner.py                  # rclone subprocess + JSON-log parse + SHA-256 audit + reindex exit code
│   ├── scheduler.py               # cron / systemd-timer / launchd / Task Scheduler abstraction (idempotent install/remove)
│   ├── selftest.py                # pre-flight self-test (version, config, filter, remote reachability dry-run)
│   └── cli.py                     # `python -m sync.cli {setup,sync,test,schedule,unschedule,status}`
├── tests/
│   └── test_sync.py               # NEW — fully mocked (patch subprocess.run + shutil.which); no network
├── utils/errors.py                # EDIT (additive): add SyncError + subclasses subclassing RAGError
├── config.yaml                    # EDIT (additive): append `sync:` block (no secrets)
├── pyproject.toml                 # EDIT (additive): add "sync" to [tool.coverage.run] source
├── .gitignore                     # EDIT (additive): ignore any local rclone.conf copy / sync state
└── docs/
    └── SYNC_README.md             # NEW — operator guide (Step 1 install rclone … troubleshooting)
```

### 5.1 Module responsibilities

**`sync/config.py`** — `RcloneConfig` dataclass + `load_sync_config()`.
- Reads the `sync:` block via `utils.logger._get_config()` (consistent caching + test reset).
- **Validates** (raises `SyncConfigError`):
  - `local_path` → expand user/vars, must be **absolute**, must resolve to a directory **under the repo's `data/corpus`** (reject `..`, reject symlink escape via `Path.resolve()`).
  - `remote_name` / `remote_path` → strict whitelist regex (e.g. `^[A-Za-z0-9_.-]+$` for name, no shell metacharacters, no leading `-` so it can't be parsed as a flag).
  - `direction ∈ {pull, bisync}`; `max_delete ≥ 0`; `schedule_hour ∈ 0..23`; `schedule_min ∈ 0..59`; `conflict_resolve ∈ {newer,older,larger,smaller,none}`.
  - `include_soul` bool (default False).
- Computes default state paths under `XDG_CONFIG_HOME`/`~/.config/rclone` (filter file, log dir, bisync workdir) — all overridable.
- **Never** holds any secret. Unknown keys are collected and surfaced as a non-fatal warning (typo visibility).

**`sync/filters.py`** — `generate_filters(cfg)` / `write_filter_file(cfg)`.
- Emits the §4.3 hardened denylist; conditionally drops the `data/personality/**` line **only** if `include_soul=true` (and writes a loud WARNING header into the file when it does).

**`sync/runner.py`** — `run_sync(cfg, dry_run=False, resync=False)` → `SyncResult`.
- `rclone_bin = shutil.which("rclone")`; `None` → `RcloneNotInstalledError` (defeats Bandit S607 partial-path).
- `check_rclone_version()` asserts ≥ 1.68.2 → `RcloneVersionError` if older.
- Builds an **argv list** (never a string, never `shell=True`); all argv elements come from **validated config** + a fixed flag list (no taint → satisfies CodeQL/DevSkim/Fortify).
- `subprocess.run(argv, capture_output=True, text=True, timeout=..., check=False)`; inspects `returncode` (no `check=True`).
- Parses the JSON log → per-file `FileEvent(kind, path, sha256)`; hashes added/modified files under `data/corpus/` with stdlib `hashlib.sha256` (streamed, 64 KiB chunks).
- Emits audit events (see §6). Maps nonzero exit → `SyncResult(success=False)` / `RcloneError` with `details={"rclone_exit": N}`.
- `reindex_exit_code_for(result, cfg)` → 0 / 10 / 1 (safety abort) / 2 (other failure).

**`sync/scheduler.py`** — `get_scheduler(cfg)` factory → platform impl with idempotent `install()` / `remove()` / `status()`.
- **Linux:** prefer a **systemd `--user` timer + `Type=oneshot` service** (inherent overlap protection, journald logging, `Persistent=true` catch-up). Provide a **cron fallback** (tagged line, `flock -n` lockfile to prevent overlap).
- **macOS:** launchd `LaunchAgent` plist (`StartCalendarInterval`; won't relaunch a running job).
- **Windows:** `schtasks` Task Scheduler with "do not start a new instance" policy.
- Every registered job is tagged (`CYCLAW_DROPBOX_SYNC`) so install/remove only ever touches our own entry.
- **Wrapper-level lockfile** (`os.O_CREAT|os.O_EXCL` / `flock`) as belt-and-suspenders so a manual run can't collide with a scheduled one (rclone has no built-in single-instance guard).

**`sync/cli.py`** — `python -m sync.cli <subcommand>`. argparse; subcommands `setup [--schedule]`, `sync [--dry-run] [--resync]`, `test`, `schedule`, `unschedule`, `status`. No import of `gate`/`graph`/`mcp_hybrid_server`. Exit codes per §7.

**`sync/selftest.py`** — pre-flight checks (rclone present + version, config valid, filter file writable, `rclone lsd` reachability dry-run, soul-exclusion asserted). Drives `cli test`.

---

## 6. Audit event schema

All through `utils.logger.audit_log()`. **Key rules** (from logger behavior at `utils/logger.py:106-119`):
- Do **not** name any field `query` (it would be SHA-256-hashed and lose readability). Use `file` / `local_path`.
- String fields (except `event`/`timestamp`/`query_hash`) pass through `redact_sensitive` — emails/IPs/secret-regex scrubbed automatically; path text otherwise preserved.
- Counts/booleans are ints/bools (not redacted) — keep them typed.
- **Never** place a refresh token or raw rclone stderr that could echo a secret into any field. Log only metadata.

| Event | When | Key fields |
|---|---|---|
| `sync_started` | run begins | `direction`, `dry_run`, `remote`, `local_path`, `include_soul` |
| `sync_file_added` | per file | `file`, `sha256` |
| `sync_file_modified` | per file | `file`, `sha256` |
| `sync_file_deleted` | per file | `file` (no hash — bytes gone) |
| `sync_completed` | success | `direction`, `duration_sec`, `rclone_exit_code`, `counts`, `corpus_changed`, `dry_run` |
| `sync_failed` | failure | `direction`, `rclone_exit_code`, `errors_n`, `aborted_for_safety` |

---

## 7. Exit-code contract (CLI)

| Code | Meaning | Caller action |
|---|---|---|
| 0 | Success, no corpus change | none |
| **10** | Success **and `data/corpus/**` changed** | run `python -m retrieval.indexer`, then restart gateway |
| 1 | Aborted by safety fuse (`--max-delete`/`--max-transfer`) | investigate the remote; do **not** blindly raise the fuse |
| 2 | Sync failed (other) | inspect audit log / rclone log |
| 3 | Config/environment problem (rclone missing/old, config invalid) | fix env per error details |

Cron-friendly chain:
```bash
python -m sync.cli sync; rc=$?
if [ "$rc" -eq 10 ]; then python -m retrieval.indexer; fi
```

---

## 8. `config.yaml` additive block

Append a new top-level block — **touch no existing keys, no secrets**:

```yaml
# ===========================
# sync: Dropbox corpus sync (out-of-band, rclone-based)
# ===========================
# Absence of this block disables sync entirely. Sync runs strictly via
# `python -m sync.cli` — never imported by gate.py, graph.py, or the MCP server.
# The Dropbox refresh token lives ONLY in rclone.conf, never here.
sync:
  enabled: false                 # off by default; out-of-band only
  local_path: "data/corpus"      # validated: absolute, under repo data/corpus, a dir
  remote_name: "dropbox_cyclaw"  # must match `rclone listremotes`
  remote_path: "CyClaw/corpus"   # folder inside the Dropbox App Folder
  direction: "pull"              # "pull" (safe default) | "bisync" (opt-in, discouraged)
  include_soul: false            # leave false — data/personality/ is NOT sync-safe
  reindex_on_change: true        # exit 10 when data/corpus/** changed
  checksum: true                 # rclone --checksum (hash compare, not mtime)
  max_delete: 20                 # safety fuse: abort if > N deletions
  max_transfer: "1G"             # safety fuse: abort if run would move > this
  schedule_hour: 2               # 24h local time (cron/timer/Task Scheduler)
  schedule_min: 0
  conflict_resolve: "newer"      # bisync-only: newer modtime wins
  conflict_loser: "rename"       # bisync-only: loser saved as .conflict1 (never deleted)
  # extra_excludes:              # optional, appended AFTER hardened block
  #   - "scratch/**"
```

Loader note: `utils/logger._get_config()` caches `config.yaml`; `sync/` should load through it (and tests call `reset_config_cache()`). `gate.py` and `retrieval.indexer` each `yaml.safe_load` independently — they are not cache-coupled, so adding this block cannot perturb them.

---

## 9. Errors — additive to `utils/errors.py`

Follow the existing subclass pattern exactly (each subclass calls `super().__init__(message, code=..., details=details)`):

```python
class SyncError(RAGError):
    def __init__(self, message, details=None):
        super().__init__(message, code="SYNC_ERROR", details=details)

class RcloneNotInstalledError(SyncError):   # code="RCLONE_NOT_INSTALLED"
class RcloneVersionError(SyncError):        # code="RCLONE_VERSION_TOO_OLD"
class SyncConfigError(SyncError):           # code="SYNC_CONFIG_INVALID"
class SchedulerError(SyncError):            # code="SYNC_SCHEDULER_ERROR"
class SyncRuntimeError(SyncError):          # code="SYNC_RUNTIME_ERROR"
```

Centralizing in `utils/errors.py` keeps the typed-error hierarchy uniform (matches `IndexNotFoundError`, `CorpusEmptyError`, etc.). These are never raised in the request path.

---

## 10. Security & CI compliance checklist

CyClaw runs **Ruff (incl. Bandit `S`), mypy strict, CodeQL, DevSkim, Fortify, OSV-Scanner, pip-audit**. The `sync/` package must pass all of them.

| Gate | Requirement for `sync/` |
|---|---|
| **Ruff `select = [E,F,I,B,C4,UP,S]`** (`pyproject.toml:73`) | The `S` (Bandit) rules are the main concern: **`S602`** (`shell=True`) — never use it; **`S603`** (subprocess call) — argv list only, justify any residual with a targeted `# noqa: S603` + comment; **`S607`** (partial exe path) — resolve via `shutil.which("rclone")` to an absolute path. `sync/*` is **not** under the `tests/*` per-file-ignore, so it must be clean or explicitly suppressed with justification. Line length 120 (E501 ignored). |
| **mypy strict** (`:84`) | Full type annotations on every function/param/return, matching `utils/logger.py` style. |
| **CodeQL** (python, build-mode none) | No **tainted** value into argv. All argv elements come from validated/whitelisted config — not env, not network, not file contents. Static flag list + validated `remote`/`local_path` ⇒ no command-injection taint path. |
| **DevSkim** | Scans Python (ignores `*.md`/`*.json`). Add justified inline `# DevSkim: ignore <RULE>` only where provably safe (mirrors existing `config.yaml` usage), with a loopback/offline rationale. |
| **Fortify** | Only runs with `FOD_TENANT`/`SSC_URL`; same subprocess hygiene. Keep `SSC_TOKEN` out of logs (already redacted by `gate._sanitize_error`, but sync should never log it). |
| **OSV-Scanner + pip-audit** | Trigger on `requirements.txt`/`constraints.txt`/manifests. **Zero new deps ⇒ these files unchanged ⇒ nothing new to flag.** Do not add to `.osv-scanner.toml` (it ignores only the two accepted CVEs: chromadb, nltk). |
| **Coverage `fail_under = 80`** (`pyproject.toml:95`) | Add `"sync"` to `[tool.coverage.run] source` (`:91`) **and** ship `tests/test_sync.py` with adequate coverage. (Alternatively keep `sync` out of `source` to stay outside the gate — but adding it is the honest choice and PsyClaw shipped 25 mocked tests.) |

**Secrets discipline (structural, not just scanner-satisfying):**
- Refresh token only in user-owned `rclone.conf` (mode 600 / restricted ACL). Never in repo, config.yaml, logs, audit events, or argv.
- `.gitignore`: add patterns for any local `rclone.conf` copy and sync state so they can never be committed.
- Sync logs rclone *metadata* only — never raw stderr that could echo a token.

**Subprocess env hygiene:** spawn rclone with a minimal clean env; do **not** enable `--rc` (remote control) or any rclone usage reporting; keep its network strictly Dropbox-API-only.

---

## 11. Test plan (`tests/test_sync.py`)

Follow CyClaw conventions (`tests/conftest.py`, `test_audit.py`, `test_gate.py`): **fully mocked, no network, plain sync tests** (the suite avoids `pytest.mark.asyncio`; sync is synchronous CLI code).

- `autouse` fixture calling `reset_config_cache()` before/after each test (sync exercises `audit_log`/`_get_config`).
- `tmp_path` fixture writing a temp `config.yaml` with a `sync:` block and `logging.audit_file` → `tmp_path`.
- **Mock the boundary:** `patch("sync.runner.subprocess.run", return_value=MagicMock(returncode=0, stdout="...", stderr=""))` and `patch("sync.runner.shutil.which", return_value="/usr/bin/rclone")`. No rclone binary required.

Coverage targets (port PsyClaw's 25-test suite intent):
1. **argv is a list**, contains no `shell=True`, no untrusted interpolation; binary path absolute.
2. Version gate: `< 1.68.2` raises `RcloneVersionError`; missing binary raises `RcloneNotInstalledError`.
3. Config validation: rejects relative `local_path`, `..` escape, path outside `data/corpus`, bad `direction`, bad `remote_name` (metacharacters / leading `-`), out-of-range schedule.
4. Filter generation: `data/personality/**` present by default; **removed** only when `include_soul=true` (and WARNING header emitted); `extra_excludes` appended after hardened block.
5. Log parsing → `FileEvent`s; `corpus_changed` true only when a `data/corpus/**` path appears; exit code 10 wiring.
6. Audit events: correct event names, `sha256` populated for added/modified, **no secret-bearing fields**, no field named `query`.
7. Safety: simulated `--max-delete` abort → `success=False`, `aborted_for_safety=True`, exit 1.
8. Scheduler: idempotent install/remove with mocked `subprocess`/crontab/schtasks; tagged-line add/replace/remove; no touching of unrelated entries.
9. CLI: each subcommand returns the documented exit code (mock the runner).

---

## 12. Documentation deliverables

- **`docs/SYNC_README.md`** — operator guide: install rclone (≥1.68.2), App-Folder OAuth via `rclone config`, edit the `sync:` block, `setup`/`test`/`sync --dry-run`/`schedule`, exit-code chain, troubleshooting table, and an explicit "why pull / why soul is excluded" security section.
- **`README.md`** — one paragraph under the roadmap marking v1.4.0 "Dropbox corpus sync" delivered + link to `docs/SYNC_README.md`. (Additive; no security-claim changes.)
- **`docs/SETUP.md`** — mention rclone as an optional external binary (like LM Studio).

---

## 13. Phased delivery (recommended PR sequencing)

To keep review tractable, land in small reviewed PRs on the feature branch:

1. **PR-1 — Scaffolding & errors:** `utils/errors.py` additions, `sync/__init__.py`, `sync/config.py` + `sync/filters.py`, `config.yaml` block, `tests/test_sync.py` (config + filter tests only), coverage source update. *No subprocess yet.* Easiest to review; establishes the contract.
2. **PR-2 — Runner:** `sync/runner.py` (version check, argv builders, run, log parse, hashing, audit), runner tests (mocked subprocess). Security-tooling review focus.
3. **PR-3 — CLI & self-test:** `sync/cli.py`, `sync/selftest.py`, CLI tests.
4. **PR-4 — Scheduler:** `sync/scheduler.py` (systemd/cron/launchd/schtasks), scheduler tests.
5. **PR-5 — Docs:** `docs/SYNC_README.md`, README/SETUP touch-ups.

(If preferred, ship as one larger PR mirroring PsyClaw v1.0 — but the phased route gives the security scanners a smaller surface per review.)

---

## 14. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Synced file overwrites `soul.md`/soul DB, bypassing governance | Low (excluded by default) | Hardened filter excludes `data/personality/**` + `*.db*`; `include_soul` opt-in is loud + discouraged; test asserts exclusion |
| Command injection via config into argv | Low | Whitelist-validate `remote_name`/`local_path`; argv list; no `shell=True`; CodeQL/DevSkim gates |
| Refresh token leaks into repo/logs | Low | Token only in `rclone.conf` (600); sync logs metadata only; `.gitignore` guards |
| rclone CVE (symlink) | Mitigated | Pin ≥1.68.2; never pass `--links`/`--metadata` |
| Expensive needless reindex | Medium | Reindex only on `data/corpus/**` change (exit 10); never inline/in-process |
| Overlapping scheduled + manual run | Low | `Type=oneshot`/launchd/Task "no new instance" + wrapper lockfile |
| Accidental mass deletion from remote | Low | Default `pull`/`copy` never deletes; `--max-delete`/`--max-transfer`/`--check-first` fuses |
| Stale index served after sync | Medium | Document required gateway restart after reindex |

---

## 15. Definition of done

- [ ] `sync/` package added; **no** import of it in `gate.py`/`graph.py`/`mcp_hybrid_server.py` (grep-verified in CI or review).
- [ ] No FastAPI route, no listener, no graph node/edge added.
- [ ] Zero new entries in `requirements.txt` / `constraints.txt` / `pyproject` dependencies.
- [ ] `config.yaml` `sync:` block additive; no existing key changed; no secret in it.
- [ ] `data/personality/**` excluded by default; `include_soul` opt-in is loud.
- [ ] All audit events flow through `utils.logger.audit_log`; no field named `query`; no secret fields.
- [ ] rclone invoked via argv list, `shutil.which`, version ≥1.68.2 asserted, no `--links`/`--metadata`, no `shell=True`.
- [ ] Ruff(S)/mypy-strict/CodeQL/DevSkim/Fortify clean (justified inline suppressions only); OSV/pip-audit unaffected.
- [ ] `tests/test_sync.py` fully mocked (no network), `reset_config_cache()` hygiene, ≥80% coverage with `"sync"` in coverage source.
- [ ] `docs/SYNC_README.md` ships; README roadmap updated.
- [ ] Each of the 8 invariants in §2 demonstrably preserved in the PR description.

---

## 16. Three-subagent implementation delegation

The implementation is split across **three subagent roles** working against a **frozen interface contract** (Appendix C). Because Roles B and C both import the types Role A owns (`RcloneConfig`, the `SyncError` hierarchy), the safe ordering is **A first (solo), then B + C in parallel**. This is the only hard dependency edge; once A's contract is on disk, B and C touch **disjoint files** and never import each other's *implementations* (only the frozen signatures), so they parallelize cleanly.

### 16.1 Role A — Foundation (runs first, solo)

Owns the data contract everything else codes against.

| Deliverable | Notes |
|---|---|
| `utils/errors.py` (additive edit) | Add `SyncError` + 5 subclasses (Appendix C-1). No change to existing classes. |
| `sync/__init__.py` | Public re-exports + `__version__`. |
| `sync/config.py` | `RcloneConfig` dataclass (Appendix C-2) + `load_sync_config()`; full validation; loads via `utils.logger._get_config()`. |
| `sync/filters.py` | `generate_filters(cfg)` / `write_filter_file(cfg)` / `filter_summary(cfg)` — hardened denylist (§4.3), conditional soul line. |
| `config.yaml` (additive edit) | Append the `sync:` block (§8). Touch no existing keys. |
| `.gitignore` (additive edit) | Ignore local `rclone.conf` copies + `.rclone-state/`. |
| `pyproject.toml` (additive edit) | Add `"sync"` to `[tool.coverage.run] source`. |
| `tests/test_sync_config.py`, `tests/test_sync_filters.py` | Self-contained; `--noconftest`-runnable. |

**Exit criterion for A:** `python3.12 -c "from sync.config import RcloneConfig, load_sync_config; from sync.filters import generate_filters; from utils.errors import SyncError"` succeeds, and A's tests pass under `pytest --noconftest`.

### 16.2 Role B — Runner + CLI (after A; parallel with C)

| Deliverable | Notes |
|---|---|
| `sync/runner.py` | `check_rclone_version()` (floor **1.68.2**), argv builders (argv list, no `shell=True`), `run_sync()`, log parse → `FileEvent`, `hashlib` SHA-256, audit emit, `reindex_exit_code_for()`. |
| `sync/selftest.py` | `run_self_test()` pre-flight (drives `cli test`). |
| `sync/cli.py` | argparse entry `python -m sync.cli {setup,sync,test,schedule,unschedule,status}`. **Imports `sync.scheduler.get_scheduler` lazily inside the `schedule`/`unschedule`/`setup --schedule` handlers** so B is testable with the scheduler mocked and B↔C stay decoupled. |
| `tests/test_sync_runner.py`, `tests/test_sync_cli.py` | Patch `sync.runner.subprocess.run` + `sync.runner.shutil.which`; assert argv is a list, no `shell=True`, version gate, exit codes, audit fields (no `query` key, no secrets). |

### 16.3 Role C — Scheduler + Docs (after A; parallel with B)

| Deliverable | Notes |
|---|---|
| `sync/scheduler.py` | `ScheduleEntry` dataclass + `get_scheduler(cfg)` factory → `CronScheduler` (Linux/macOS) and `WindowsTaskScheduler`; idempotent `install`/`remove`/`status`; tagged `CYCLAW_DROPBOX_SYNC`. (systemd-timer note documented; cron is the portable baseline with overlap caveat.) |
| `tests/test_sync_scheduler.py` | Patch `subprocess.run`/`crontab`/`schtasks`; assert tagged add/replace/remove, no touching of unrelated entries. |
| `docs/SYNC_README.md` | Operator guide (install rclone ≥1.68.2, App-Folder OAuth, config, usage, exit codes, troubleshooting, security rationale). |
| `README.md` (additive touch) | Mark roadmap item delivered + link to `docs/SYNC_README.md`. |

### 16.4 Sequencing & "use 1 subagent until you can parallelize" rule

1. **Launch Role A alone.** Wait for completion + verify the exit criterion myself.
2. **If A's contract matches Appendix C**, launch **Role B and Role C in parallel** (single message, two agents, disjoint files).
3. **If A deviated** from the contract (renamed a field, changed a signature), I reconcile it myself first (or re-task A) so B and C build against a stable interface — *only then* parallelize. Any ambiguity → fall back to a single sequential subagent rather than risk a parallel interface clash.
4. **Integration verification is mine, not the subagents'** (§16.5). Subagents do **not** run `git` and do **not** commit.

### 16.5 Verification protocol (orchestrator-owned, this environment)

- **Import/runtime under real Python 3.12:** `/usr/bin/python3.12` (has pyyaml) must import the whole `sync` package and run `python3.12 -m sync.cli status`/`test` against a temp config — with rclone absent, the expected, *clean* outcome is `RCLONE_NOT_INSTALLED` / exit 3 (this itself verifies the not-installed path).
- **Test suite under Python 3.12:** `/tmp/py312venv/bin/python -m pytest tests/test_sync_*.py --noconftest -q`. (`--noconftest` is required because the repo's `tests/conftest.py` imports `chromadb`, which isn't installed in this sandbox; sync tests are deliberately self-contained so they don't need it.)
- **Lint/type gates:** `ruff check sync tests/test_sync_*.py` (must be clean on the `S`/`B`/`UP`/`I` rules; justified inline `# noqa: S603` only) and `mypy sync` (strict-compatible annotations).
- **Invariant grep:** confirm no `import sync` / `from sync` appears in `gate.py`, `graph.py`, `mcp_hybrid_server.py`.
- **No-auth guarantee:** confirm no Dropbox token/key/secret is written anywhere; rclone is never actually authenticated or invoked against the network during verification (all subprocess calls are mocked in tests; live CLI runs stop at the rclone-missing/version gate).

---

## Appendix A — PsyClaw → CyClaw mapping (why this is low-risk)

| PsyClaw | CyClaw | Same? |
|---|---|---|
| `utils.logger.audit_log(event, config_path)` | identical | ✅ |
| `utils.errors.RAGError(message, code, details)` | identical | ✅ |
| `data/corpus/` | `data/corpus/` | ✅ |
| `data/personality/soul.md` + `psyclaw_soul.db` | `soul.md` + `cyclaw_soul.db` | ✅ (rename) |
| `index/chroma_db`, `index/bm25.json` | identical | ✅ |
| `.emb_cache/` | identical | ✅ |
| `python -m retrieval.indexer` | identical | ✅ |
| `POST /soul/apply` governance | identical | ✅ |
| config.yaml single source | identical | ✅ |
| offline-first / minimal-deps / no telemetry | identical | ✅ |

The PsyClaw `sync/` v1.0 module (config/filters/runner/scheduler/cli/tests) ports almost verbatim; the substantive **net-new** decisions for CyClaw are: **rclone floor raised to 1.68.2** (CVE-2024-52522), **systemd `--user` oneshot timer preferred** over bare cron on Linux, **`*.db*` added** to the hardened excludes (soul DB), and **`"sync"` added to coverage source** to satisfy CI's 80% gate.

## Appendix C — Frozen interface contract (so Roles B & C parallelize safely)

Role A produces exactly these public surfaces; Roles B and C import only these.

**C-1 — `utils/errors.py` additions** (each subclass sets its own `code`):

```python
class SyncError(RAGError):              # code="SYNC_ERROR"
class RcloneNotInstalledError(SyncError):   # code="RCLONE_NOT_INSTALLED"
class RcloneVersionError(SyncError):        # code="RCLONE_VERSION_TOO_OLD"
class SyncConfigError(SyncError):           # code="SYNC_CONFIG_INVALID"
class SchedulerError(SyncError):            # code="SYNC_SCHEDULER_ERROR"
class SyncRuntimeError(SyncError):          # code="SYNC_RUNTIME_ERROR"
# signature for all: __init__(self, message: str, details: Optional[dict] = None)
```

**C-2 — `sync/config.py` public surface:**

```python
@dataclass
class RcloneConfig:
    local_path: str                  # validated absolute, under repo data/corpus, a dir
    remote_name: str = "dropbox_cyclaw"
    remote_path: str = "CyClaw/corpus"
    direction: str = "pull"          # "pull" | "bisync"
    include_soul: bool = False
    reindex_on_change: bool = True
    checksum: bool = True
    max_delete: int = 20
    max_transfer: str = "1G"
    conflict_resolve: str = "newer"
    conflict_loser: str = "rename"
    schedule_hour: int = 2
    schedule_min: int = 0
    workdir: Optional[str] = None    # bisync state dir (default under rclone state dir)
    filter_file: Optional[str] = None
    log_dir: Optional[str] = None
    extra_excludes: List[str] = field(default_factory=list)
    REINDEX_EXIT_CODE: int = 10
    # properties:
    @property
    def remote(self) -> str          # f"{remote_name}:{remote_path}"
    @property
    def log_path(self) -> str        # os.path.join(log_dir, "rclone_cyclaw.log")
    @property
    def is_windows(self) -> bool

def load_sync_config(config_path: str = "config.yaml") -> RcloneConfig: ...
```

**C-3 — `sync/filters.py` public surface:**

```python
def generate_filters(cfg: RcloneConfig) -> str: ...
def write_filter_file(cfg: RcloneConfig) -> str: ...   # returns abs path written
def filter_summary(cfg: RcloneConfig) -> dict: ...
```

**C-4 — `sync/scheduler.py` public surface (Role C; consumed lazily by Role B's cli):**

```python
@dataclass
class ScheduleEntry:
    platform_name: str
    command: str
    cron_or_time: str
    raw: str

def get_scheduler(cfg: RcloneConfig): ...   # -> CronScheduler | WindowsTaskScheduler
# scheduler objects expose: install() -> ScheduleEntry
#                           remove() -> bool
#                           status() -> Optional[ScheduleEntry]
TASK_TAG = "CYCLAW_DROPBOX_SYNC"
WINDOWS_TASK_NAME = "CyClaw Dropbox Sync"
```

**C-5 — `sync/runner.py` public surface (Role B):**

```python
MIN_RCLONE_MAJOR, MIN_RCLONE_MINOR, MIN_RCLONE_PATCH = 1, 68, 2
def check_rclone_version(rclone_bin: str = "rclone") -> Tuple[int, int, int]: ...
@dataclass
class FileEvent: kind: str; path: str; sha256: Optional[str] = None
@dataclass
class SyncResult: success: bool; direction: str; ...; corpus_changed: bool
def run_sync(cfg, dry_run=False, resync=False, rclone_bin="rclone") -> SyncResult: ...
def reindex_exit_code_for(result: SyncResult, cfg: RcloneConfig) -> int: ...
```

## Appendix D — Test isolation in CI vs. this sandbox

The repo's `tests/conftest.py` imports `retrieval.hybrid_search` (→ `chromadb`). In CI those deps are installed, so the full suite (incl. `tests/test_sync_*.py`) collects normally. In a minimal sandbox without chromadb, run sync tests with **`pytest --noconftest tests/test_sync_*.py`**. To keep that possible, **sync tests must be self-contained**: they may import `reset_config_cache` directly and use the builtin `tmp_path` fixture, but must **not** depend on `conftest.py` fixtures. (`tests/*` are exempt from Bandit `S101/S603/S108` via `pyproject.toml` per-file-ignores, so `assert` and mocked subprocess in tests are fine.)

## Appendix B — Key source references

- rclone: filtering, bisync, docs (max-delete/max-transfer/check-first/exit codes), Dropbox backend — rclone.org
- CVE-2024-52522 (GHSA-hrxh-9w67-g4cv) — fixed in rclone 1.68.2
- Dropbox OAuth (offline access, scoped apps, App Folder, content_hash) — developers.dropbox.com / dropbox.tech
- CyClaw code: `gate.py`, `graph.py`, `utils/logger.py:106`, `utils/errors.py:10`, `config.yaml`, `retrieval/indexer.py`, `pyproject.toml:73,91,95`, `.gitignore`
- Prior art: PsyClaw Sync v1.0 (`sync/` package + `SYNC_README.md`)
