# CyClaw Dropbox Sync — Setup & Usage Guide

This guide walks you through enabling and running the CyClaw Dropbox corpus sync on **Windows** and **Linux** (macOS steps mirror Linux). It is written for end users, not developers — the internals live in `docs/SYNC_README.md`.

**What it does:** mirrors a Dropbox folder into your local `data/corpus/` so your knowledge base stays current across machines, without weakening any of CyClaw's security properties. Sync runs as a separate process on a schedule — never inline with the gateway.

**What it does NOT do:** touch `gate.py`, `graph.py`, or any live request path. No token is ever stored in CyClaw's config or logs. The soul layer (`data/personality/`) is excluded by default.

---

## Before you start

You need:
- CyClaw cloned and working (Python 3.12, `pip install -r requirements.txt` done)
- A Dropbox account
- Internet access on the machine you're setting up (one time only for OAuth)

---

## Part 1 — Install rclone

CyClaw requires **rclone ≥ 1.68.2**. It will refuse to run with an older version (security floor for CVE-2024-52522).

### Linux

```bash
# Official install script (installs the latest stable release)
curl https://rclone.org/install.sh | sudo bash

# Verify
rclone version
# Should show v1.68.2 or higher
```

If you prefer a package manager:
```bash
# Ubuntu / Debian
sudo apt install rclone      # check version — distro packages often lag; prefer the official script

# Fedora / RHEL
sudo dnf install rclone
```

If the distro package is older than 1.68.2, use the official script above instead.

### Windows

Open **PowerShell** (as a regular user — no admin needed for winget):

```powershell
winget install Rclone.Rclone

# Verify (open a new terminal after install)
rclone version
# Should show v1.68.2 or higher
```

Alternative: download the `rclone-*-windows-amd64.zip` from [rclone.org/downloads](https://rclone.org/downloads/), extract, and add the folder to your `PATH`.

---

## Part 2 — Create the Dropbox remote

Run this once, on the machine that has a browser. If you're on a headless Linux server, see the [remote auth section](#headless-linux-no-browser).

### Step 1: Start rclone config

```bash
rclone config
```

### Step 2: Walk through the prompts

```
n) New remote
name> dropbox_cyclaw
```

> The name `dropbox_cyclaw` must match `remote_name` in your `config.yaml`. Change both if you want a different name.

```
Storage> dropbox
```

At the client ID / secret prompts, press **Enter** to accept defaults (uses rclone's shared Dropbox app).

```
Edit advanced config? n
Use auto config? y
```

A browser window will open. Log into Dropbox and click **Allow**. Return to the terminal — it should print:

```
Success!
```

### Step 3: Verify the connection

```bash
rclone lsd dropbox_cyclaw:
# Lists the root of your Dropbox App Folder — should show folders (or be empty)
```

Create the folder that CyClaw will sync into:

```bash
rclone mkdir dropbox_cyclaw:CyClaw/corpus
```

### Step 4: Protect the token file

The Dropbox refresh token lives only in `rclone.conf`, not in CyClaw.

**Linux:**
```bash
chmod 600 ~/.config/rclone/rclone.conf
```

**Windows** (PowerShell):
```powershell
# View the file location
rclone config file

# Restrict access to your user only (run in the folder shown above)
$conf = (rclone config file)
icacls $conf /inheritance:r /grant:r "$($env:USERNAME):(R,W)"
```

### Headless Linux (no browser)

On a headless server, use another machine to generate the token and copy the config:

```bash
# On your laptop / desktop (any machine with a browser and rclone installed):
rclone config
# Create a remote named dropbox_cyclaw, complete the OAuth

# Find the config file
rclone config file
# Copy it to the server:
scp ~/.config/rclone/rclone.conf user@yourserver:~/.config/rclone/rclone.conf
```

---

## Part 3 — Edit `config.yaml`

Open `config.yaml` in the CyClaw project root. Find the `sync:` block (it's near the bottom). Change **these two lines** to enable sync:

```yaml
sync:
  enabled: true                  # was: false — change this to enable
  local_path: "data/corpus"      # leave as-is unless you moved the corpus
  remote_name: "dropbox_cyclaw"  # must match the name you gave in Step 2
  remote_path: "CyClaw/corpus"   # folder inside your Dropbox App Folder
  direction: "pull"              # pull = Dropbox→local only (safe default)
  include_soul: false            # deprecated no-op — soul is never synced (sync root is confined to data/corpus)
  reindex_on_change: true        # auto-trigger reindex when files change
  checksum: true
  max_delete: 20                 # safety: abort if >20 files would be deleted
  max_transfer: "1G"             # safety: abort if run would transfer >1 GB
  schedule_hour: 2               # daily run at 02:00 local time
  schedule_min: 0
```

No Dropbox credentials go in this file — only paths and behaviour settings.

---

## Part 4 — Validate the setup

From the CyClaw directory:

```bash
cd /path/to/CyClaw

# Run the pre-flight self-test
python -m sync.cli test
```

Expected output:
```
Self-test: 5/5 passed
```

If rclone is missing or too old, the test prints the error and exits 3. If your config.yaml is wrong, it names the failing field.

Also run a dry-run sync (reads Dropbox but writes nothing locally):

```bash
python -m sync.cli sync --dry-run
```

---

## Part 5 — Run a sync manually

```bash
# From the CyClaw directory:

# Preview — shows what would change, changes nothing
python -m sync.cli sync --dry-run

# Live sync
python -m sync.cli sync
```

### Reading the output

```
Sync complete
  direction............... pull
  exit_code............... 0
  duration_sec............ 2.41
  added................... 3
  modified................ 1
  deleted................. 0
  corpus_changed.......... True
```

### Exit codes

| Code | Meaning | What to do |
|------|---------|------------|
| `0` | Success, nothing changed | Nothing |
| `10` | Success, corpus changed | Run `python -m retrieval.indexer`, then restart the gateway |
| `1` | Safety fuse tripped | Check if many files were genuinely deleted upstream; don't blindly raise the fuse |
| `2` | Sync failed | Check `logs/audit.jsonl` and the rclone log in `~/.config/rclone/logs/` |
| `3` | Config or environment problem | Read the error message — usually rclone missing, too old, or `config.yaml` bad |

### After a sync that changed files

When exit code is `10`, rebuild the index so the gateway sees the new content:

```bash
python -m retrieval.indexer
```

Then restart CyClaw's gateway (the index is loaded at startup).

---

## Part 6 — Set up scheduled (daily) sync

### Linux (cron)

```bash
python -m sync.cli schedule
```

This adds exactly one tagged line to your crontab. Default time is 02:00 (change `schedule_hour`/`schedule_min` in `config.yaml` first if needed). The line looks like:

```
0 2 * * * cd "/path/to/CyClaw" && "/usr/bin/python3.12" -m sync.cli sync # CYCLAW_DROPBOX_SYNC
```

To see the current schedule:
```bash
python -m sync.cli status
```

To remove the schedule:
```bash
python -m sync.cli unschedule
```

**For a more reliable setup on Linux**, use a systemd user timer instead of cron (journald logging, runs after resume from suspend, no cron daemon needed):

```bash
mkdir -p ~/.config/systemd/user

# Create the service unit
cat > ~/.config/systemd/user/cyclaw-sync.service <<'EOF'
[Unit]
Description=CyClaw Dropbox corpus sync

[Service]
Type=oneshot
WorkingDirectory=/path/to/CyClaw
ExecStart=/path/to/CyClaw/.venv/bin/python -m sync.cli sync
EOF

# Create the timer unit (runs daily at 02:00)
cat > ~/.config/systemd/user/cyclaw-sync.timer <<'EOF'
[Unit]
Description=Daily CyClaw Dropbox sync

[Timer]
OnCalendar=*-*-* 02:00:00
Persistent=true

[Install]
WantedBy=timers.target
EOF

# Enable and start
systemctl --user daemon-reload
systemctl --user enable --now cyclaw-sync.timer

# Check status
systemctl --user status cyclaw-sync.timer
systemctl --user list-timers cyclaw-sync.timer
```

> Replace `/path/to/CyClaw` with your actual path (e.g. `~/CyClaw`), and adjust the Python path to your venv or system Python.

### Windows (Task Scheduler)

```powershell
python -m sync.cli schedule
```

This registers a daily Task Scheduler job named **"CyClaw Dropbox Sync"** running at the time set in `config.yaml` (`schedule_hour`:`schedule_min`). A small launcher batch file (`cyclaw_sync.bat`) is written to the rclone log folder — the task points at this file to avoid Windows quoting issues with paths that contain spaces.

To verify it was created:
```powershell
python -m sync.cli status
# or
schtasks /Query /TN "CyClaw Dropbox Sync" /FO LIST
```

To remove it:
```powershell
python -m sync.cli unschedule
```

To run it immediately (without waiting for the schedule):
```powershell
schtasks /Run /TN "CyClaw Dropbox Sync"
```

**Important on Windows:** The task runs under your user account (`/RL LIMITED`). Rclone reads `%APPDATA%\rclone\rclone.conf` for your token. If the task fails, open Event Viewer → Windows Logs → Application and look for rclone errors, or check the rclone log at `%APPDATA%\rclone\logs\rclone_cyclaw.log`.

---

## Part 7 — One-shot full setup

The `setup` subcommand combines Steps 4–6:

```bash
# Validate + write filters + print OAuth reminder
python -m sync.cli setup

# Validate + write filters + register the daily schedule
python -m sync.cli setup --schedule
```

---

## Part 8 — Check status

```bash
python -m sync.cli status
```

Shows enabled state, local path, remote, sync direction, schedule time, filter file path, rclone version, and whether the daily job is registered.

---

## Part 9 — Advanced: cron-friendly reindex automation

For a fully automated pipeline (sync → reindex → restart) on Linux, wrap sync in a shell script:

```bash
#!/usr/bin/env bash
# /path/to/CyClaw/scripts/sync_and_reindex.sh
set -euo pipefail

REPO="/path/to/CyClaw"
cd "$REPO"

python -m sync.cli sync
rc=$?

if [ "$rc" -eq 10 ]; then
    echo "[sync] corpus changed — rebuilding index"
    python -m retrieval.indexer
    echo "[sync] index rebuilt — restart the gateway to pick up changes"
    # Add your gateway restart command here, e.g.:
    # systemctl --user restart cyclaw-gateway
elif [ "$rc" -eq 0 ]; then
    echo "[sync] no corpus changes"
else
    echo "[sync] failed with exit code $rc" >&2
    exit "$rc"
fi
```

Register this script in cron or systemd instead of the bare `sync.cli sync`.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `RCLONE_NOT_INSTALLED` | Install rclone (Part 1) |
| `RCLONE_VERSION_TOO_OLD` | Upgrade to rclone ≥ 1.68.2 |
| `SYNC_CONFIG_INVALID: sync.enabled` | Set `enabled: true` in `config.yaml` |
| `SYNC_CONFIG_INVALID: local_path` | `local_path` must point inside `data/corpus/` |
| `rclone lsd dropbox_cyclaw:` fails | Re-run `rclone config` and redo the OAuth flow |
| Safety fuse tripped (exit 1, `aborted_for_safety: true`) | More than `max_delete` files would be deleted. Check Dropbox for unexpected deletions. Raise `max_delete` only when intentional. |
| Stale answers after sync | After exit 10 + reindex, **restart the CyClaw gateway** — the index is loaded at startup, not hot-reloaded. |
| Schedule doesn't fire on Windows | Check Task Scheduler → "CyClaw Dropbox Sync" → Last Run Result. Code `0x1` usually means the batch file path has changed; run `python -m sync.cli unschedule` then `python -m sync.cli schedule` to re-register. |
| Another sync is running (exit `SYNC_RUNTIME`) | A previous run is in progress, or it crashed and left a lock. The lock in `~/.config/rclone/logs/sync.lock.d` (Linux) or `%APPDATA%\rclone\logs\sync.lock.d` (Windows) is auto-cleaned after 3 hours; or remove it manually. |
| Soul changed unexpectedly | Not via sync — the sync root is confined to `data/corpus` and `include_soul` is a deprecated no-op. The soul was edited out-of-band; use `POST /soul/apply` to manage `data/personality/` intentionally. |

---

## What is never synced

These are always excluded regardless of your settings:

- `data/personality/**` — soul layer; governed via `POST /soul/apply` only
- AI model weights (`.gguf`, `.bin`, `.safetensors`, `.onnx`, `.pt`, `.pth`)
- ChromaDB / BM25 indices (`index/**`, `.chroma/**`) — rebuild with `python -m retrieval.indexer`
- Embeddings cache (`.emb_cache/**`)
- Virtual environments
- Logs and audit events (`logs/**`, `*.jsonl`)
- Secrets (`.env`, `*.pem`, `*.key`, `*_secret*`, `credentials*`)
- Soul DB (`*.db`, `*.db-wal`, `*.db-shm`)
- Git state (`.git/**`)
- rclone's own state files

To add additional exclusions, use `extra_excludes:` in your `config.yaml`:

```yaml
sync:
  ...
  extra_excludes:
    - "scratch/**"
    - "*.tmp"
```

---

## Quick reference

```bash
python -m sync.cli setup            # validate + write filters
python -m sync.cli setup --schedule # validate + write filters + schedule
python -m sync.cli sync --dry-run   # preview only
python -m sync.cli sync             # live sync
python -m sync.cli status           # current state + schedule
python -m sync.cli schedule         # (re-)register daily job
python -m sync.cli unschedule       # remove daily job
python -m sync.cli test             # pre-flight self-test
```
