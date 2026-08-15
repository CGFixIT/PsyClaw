# `macos/` — POSIX install and launchd glue

Installer / launcher / launchd **glue** for macOS (Apple Silicon) and Linux.
Not request-path code: `gate.py`, `graph.py`, and `mcp_hybrid_server.py` never
import anything here (I6). The Windows sibling is `powershell/`.

After install, `cyclaw` starts the RAG gateway (`127.0.0.1:8787`) and the
coding console (`127.0.0.1:8790`). Mutable state lives under `~/.CyClaw`.

Full harness walkthrough: [`docs/HARNESS_MACOS.md`](../docs/HARNESS_MACOS.md).
Console package: [`harness/README.md`](../harness/README.md).

## Scripts

| Script | What it does |
|---|---|
| `install-cyclaw.sh` | Home layout, venv, `cyclaw` shim, optional PATH / rc function. `--repo-path`, `--skip-python-deps`, `--no-profile-edit`, `--no-path-edit`, `--no-fsconnect`. |
| `uninstall-cyclaw.sh` | Removes the rc function and PATH entry. Keeps `~/.CyClaw` unless `--remove-home`. Optional `--remove-fsconnect`. Also best-effort unschedules Dropbox sync. |
| `invoke-cyclaw.sh` | Starts gate + harness from `~/.CyClaw/venv`. `--no-gate` / `--no-harness` / `--no-browser` / `--port` / `--gate-port`. |
| `setup-fsconnect.sh` | Creates confined `~/CyClaw-FS` (`chmod 700`). Unless `--prepare-only`, enables list/stat/read via `_enable_fsconnect_readlist.py`. |
| `_enable_fsconnect_readlist.py` | Writes the confined read/list `fsconnect:` profile into `config.yaml` (writes stay off). |
| `cyclaw-keychain-set.sh` | Interactive Keychain store. Bare `-w` (secret never in argv); `-T /usr/bin/security`. Requires a TTY. |
| `cyclaw-keychain-env.sh` | Fetch one Keychain item, export it, `exec` the wrapped command. Fail-closed if missing/empty. |

Target shells: bash (including macOS 3.2) and zsh. BSD userland on macOS —
no Homebrew required.

## `LaunchAgents/` — templates only

These plists are **not** installed or loaded by the installer.

| File | Prefer generating with |
|---|---|
| `com.cgfixit.cyclaw.fsconnect-trash.plist` | `python -m agentic.fsconnect.cli trash-empty-plist` |
| `com.cgfixit.cyclaw.telegram-poll.plist` | `python -m telegram.cli poll-plist` |
| `com.cgfixit.cyclaw.telegram-health.plist` | `python -m telegram.cli health-plist` |

Generators write resolved paths and (for Telegram) chain the Keychain
wrapper so tokens never appear in the plist. They print a `launchctl
bootstrap` command; they never load the agent themselves.

Hand-editing a template: replace every `REPLACE_*` value, create
`~/Library/Logs/CyClaw`, test `ProgramArguments` by hand, then copy to
`~/Library/LaunchAgents/` and load **explicitly**.

## Related

- Dropbox sync scheduling: [`docs/SYNC_README.md`](../docs/SYNC_README.md)
- Telegram channel: [`docs/channels/TELEGRAM_DESIGN.md`](../docs/channels/TELEGRAM_DESIGN.md)
- Agentic / registry: [`agentic/README.md`](../agentic/README.md)
