# `powershell/` — Windows install, Credential Manager, and Task Scheduler glue

Installer / launcher / scheduled-task **glue** for Windows 10/11.
Not request-path code: `gate.py`, `graph.py`, and `mcp_hybrid_server.py` never
import anything here (I6). The macOS sibling is `macos/`.

After install, `cyclaw` starts the coding console (`127.0.0.1:8790`). The RAG
gateway (`gate.py`, `127.0.0.1:8787` per `config.yaml`) is **not** launched by
the Windows shim — start it separately (unlike the macOS `invoke-cyclaw.sh`,
which starts both). Mutable state lives under `%USERPROFILE%\.CyClaw`.

## Scripts

| Script | What it does |
|---|---|
| `Install-CyClaw.ps1` | Home layout, venv, `cyclaw` shim, optional PATH / profile function. `-RepoPath`, `-SkipPythonDeps`, `-NoProfileEdit`, `-NoPathEdit`. |
| `Uninstall-CyClaw.ps1` | Removes the profile function and PATH entry. Keeps `~\.CyClaw` unless `-RemoveHome`. Optional `-RemoveFsConnect`. Best-effort unschedules Dropbox sync and deletes **known** CyClaw Task Scheduler names only (never a wildcard). |
| `Invoke-CyClaw.ps1` | Starts the harness console (`python -m harness.server`) from `~\.CyClaw\venv`. `-Port`, `-NoBrowser`, `-Repo`. Does **not** start `gate.py` (port 8787) — launch the RAG gateway separately. |
| `Setup-FsConnect.ps1` | Creates confined `%USERPROFILE%\CyClaw-FS` (current-user ACL). Unless `-PrepareOnly`, enables list/stat/read via `macos/_enable_fsconnect_readlist.py`. Writes stay off. |
| `CyClaw-CredMan-Set.ps1` | Interactive Credential Manager store. `Read-Host -AsSecureString` + `CredWriteW`. Secret never in argv. Requires a TTY. |
| `CyClaw-CredMan-Env.ps1` | Fetch one GENERIC credential, export it, run the wrapped command. Fail-closed if missing/empty. |

## Scheduled tasks

Dropbox sync is already scheduled with `python -m sync.cli schedule`
(live `schtasks /Create`, daily only, task name `CyClaw Dropbox Sync`).

`Uninstall-CyClaw.ps1` best-effort deletes a **fixed** list of CyClaw task
names (`CyClaw Dropbox Sync`, `CyClaw fsconnect-trash`,
`CyClaw telegram-poll`, `CyClaw telegram-health`, `CyClaw gate`,
`CyClaw harness`, `CyClaw opentweet`) so a later generator cannot outlive uninstall. It never
uses a wildcard `/TN`. A missing task is a no-op (query-then-delete;
Windows PowerShell 5.1 must not abort uninstall on `schtasks` stderr).
Credential Manager items are **not** deleted (same as macOS Keychain).

Telegram / API-key injection for those later generators goes through
`CyClaw-CredMan-Env.ps1` so tokens never appear in task XML.

## Related

- Dropbox sync scheduling: [`docs/SYNC_README.md`](../docs/SYNC_README.md)
- Telegram channel: [`docs/channels/TELEGRAM_DESIGN.md`](../docs/channels/TELEGRAM_DESIGN.md)
- Agentic / registry: [`agentic/README.md`](../agentic/README.md)
- Console slash commands (`/goal`, `/loop`, `/skills`, `/tools`, `/web`): [`harness/README.md`](../harness/README.md)
- macOS twin: [`macos/README.md`](../macos/README.md)
