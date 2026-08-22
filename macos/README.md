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
| `setup-from-clone.sh` | **One-shot after `git clone`** on Apple Silicon. Chains `install-cyclaw.sh` + `setup-cyclaw-keys.sh` (prompts for Telegram / Claude / Grok / GitHub), checks Ollama, builds the retrieval index, then starts both servers. `--dry-run`, `--skip-prompts`, `--no-start`, `--small-model`, `--ollama-model TAG`. Note `--skip-prompts` implies no server start; pass `--start` to launch anyway. The script accepts a wider flag set than the common ones listed here — including `--skip-install`, `--skip-python-deps`, `--skip-keys`, `--skip-ollama`, `--skip-index`, `--skip-advisor`, `--no-browser`, `--no-fsconnect`, `--no-profile-edit`, `--no-path-edit`, `--grok-dummy`, `--rotate-key`, `--ollama-install-script`, and `--yes`; run it with `--help` for the authoritative list. |
| `install-cyclaw.sh` | Home layout, venv, `cyclaw` shim, optional PATH / rc function. `--repo-path`, `--skip-python-deps`, `--no-profile-edit`, `--no-path-edit`, `--no-fsconnect`. |
| `uninstall-cyclaw.sh` | Removes the rc function, PATH entry, and the `cyclaw keys` source block. Keeps `~/.CyClaw` unless `--remove-home`. Optional `--remove-fsconnect`. Best-effort unschedules Dropbox sync and `launchctl bootout`s CyClaw LaunchAgent labels (telegram-poll/health, fsconnect-trash, gate, harness, keys-rotate, opentweet). |
| `invoke-cyclaw.sh` | Starts gate + harness from `~/.CyClaw/venv`. `--no-gate` / `--no-harness` / `--no-browser` / `--port` / `--gate-port` / `--repo` (point at a checkout other than the default). |
| `setup-cyclaw-keys.sh` | Apple Silicon key bootstrap. Autogenerates `CYCLAW_API_KEY`; prompts for Telegram / Claude (`ANTHROPIC_API_KEY`) / Grok / GitHub (skip allowed). Persists to Keychain + `~/.CyClaw/.env` (chmod 600), failing before dotenv writes if a requested Keychain write fails. `--rotate`, `--no-env-file`, `--fill-browser` (loopback `#apiKey` / `#apiKeyInput` only — never localStorage), `--schedule-rotate monthly\|weekly\|never` (writes, never loads, a LaunchAgent), `--unschedule-rotate` (removes that LaunchAgent again). Further flags exist — `--no-keychain`, `--no-repo-env`, `--print-key`/`--no-print-key`, `--copy-key`/`--no-copy-key`, `--clipboard-ttl N`, `--open-consoles`, `--gate-port`, `--harness-port`, `--repo-path`, `--skip-prompts`, `--grok-dummy`; run it with `--help` for the authoritative list. |
| `setup-fsconnect.sh` | Creates confined `~/CyClaw-FS` (`chmod 700`). Unless `--prepare-only`, enables list/stat/read via `_enable_fsconnect_readlist.py`. |
| `_enable_fsconnect_readlist.py` | Writes the confined read/list `fsconnect:` profile into `config.yaml` (writes stay off). |
| `cyclaw-keychain-set.sh` | Interactive Keychain store. Bare `-w` (secret never in argv); `-T /usr/bin/security`. Requires a TTY. |
| `cyclaw-keychain-env.sh` | Fetch one Keychain item, export it, `exec` the wrapped command. Fail-closed if missing/empty. |
| `generate_service_plist.py` | Supervised LaunchAgent for `gate.py` or the harness (`--service gate\|harness`). Highest-risk generator: refuses to write without `--confirm` **and** a non-empty `--reason`. `KeepAlive: {SuccessfulExit: false}` (crash-only restart, never after a clean stop), `ThrottleInterval` 30s default, optional `--api-key-service` chains the Keychain wrapper. Never loads the agent itself. |
| `ollama-mlx.env` | KEY=value tunings sourced before `ollama serve` (context 16384, keep-alive 30m, one model, no parallel slots, flash-attn + KV q8_0). No secrets. `setup-from-clone.sh` sources it when *it* launches Ollama; an already-running .app ignores it until quit. |

Target shells: bash (including macOS 3.2) and zsh. BSD userland on macOS —
no Homebrew required.

## One-shot after clone (Apple Silicon)

`setup-from-clone.sh` is the operator-facing "I just cloned this, make it
run" path. It does **not** reimplement the scripts above — it chains them
and fills the four holes `setup-guide.md` documents that Option A leaves
open (Ollama, the retrieval index, API keys, starting both servers).

```bash
git clone https://github.com/CGFixIT/CyClaw.git && cd CyClaw
bash macos/setup-from-clone.sh
```

Privacy matches `cyclaw-advisor`: secrets are never logged, never written
to `config.yaml`, never placed on a child process argv. Key persist is
Keychain + `~/.CyClaw/.env` (chmod 600) via `setup-cyclaw-keys.sh`.
fsconnect writes and indexing stay off. LaunchAgents are **not** generated
or loaded (those still need `--confirm --reason`).

## Key bootstrap

`setup-cyclaw-keys.sh` is the operator-facing path for the env vars
`setup-guide.md` otherwise tells you to `export` by hand. It is Darwin /
arm64 only (CyClaw's torch pin has no Intel macOS wheel). After it runs:

```bash
source ~/.CyClaw/.env          # this tab
# new tabs inherit via the `# >>> cyclaw keys >>>` block
```

If `CYCLAW_HOME` is set, the dotenv and the rc source block follow that
directory instead of `~/.CyClaw`.

LaunchAgents still read Keychain through `cyclaw-keychain-env.sh`. They do
not read `.env`, and this script never writes a token into a plist or the
`cyclaw` shim.

The terminal (`#apiKeyInput`) and harness (`#apiKey`) consoles hold the
operator key **in the input element only** — never `localStorage`, never a
cookie. `--fill-browser` injects that field on `127.0.0.1` tabs after
opening the consoles. A scheduled rotate updates Keychain + `.env`; it exits
nonzero before changing `.env` if the Keychain write fails. Neither a manual
nor scheduled rotate changes an already-running server environment: restart
`gate.py`, then paste once or re-run `--fill-browser`.

```bash
# first run (prompts; skip any; fill the consoles if they are up)
bash macos/setup-cyclaw-keys.sh --grok-dummy --fill-browser
source ~/.CyClaw/.env

# rotate now, then refill the in-memory fields
bash ~/.CyClaw/bin/setup-cyclaw-keys.sh --rotate --skip-prompts --fill-browser

# write (do not load) a monthly rotator
bash ~/.CyClaw/bin/setup-cyclaw-keys.sh --schedule-rotate monthly
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cgfixit.cyclaw.keys-rotate.plist
```

The rotate LaunchAgent runs a frozen copy at
`$CYCLAW_HOME/bin/setup-cyclaw-keys.sh`. Re-run `--schedule-rotate` after
updating CyClaw so an existing schedule receives script fixes.

| Env var | How | Keychain service |
|---|---|---|
| `CYCLAW_API_KEY` | Autogenerated | `com.cgfixit.cyclaw.api-key` |
| `TELEGRAM_BOT_TOKEN` | Prompt (skip ok) | `com.cgfixit.cyclaw.telegram-bot-token` |
| `ANTHROPIC_API_KEY` | Prompt (skip ok) | `com.cgfixit.cyclaw.anthropic-api-key` |
| `GROK_API_KEY` | Prompt (skip ok) or `--grok-dummy` | `com.cgfixit.cyclaw.grok-api-key` |
| `GH_TOKEN` (+ `GITHUB_TOKEN`) | Prompt (skip ok) | `com.cgfixit.cyclaw.gh-token` |

Claude is `ANTHROPIC_API_KEY` because that is the only name `llm/client.py`
reads.

## `LaunchAgents/` — templates only

These plists are **not** installed or loaded by the installer.

| File | Prefer generating with |
|---|---|
| `com.cgfixit.cyclaw.fsconnect-trash.plist` | `python -m agentic.fsconnect.cli trash-empty-plist` |
| `com.cgfixit.cyclaw.telegram-poll.plist` | `python -m telegram.cli poll-plist` |
| `com.cgfixit.cyclaw.telegram-health.plist` | `python -m telegram.cli health-plist` |
| `com.cgfixit.cyclaw.opentweet.plist` | `python -m opentweet.cli schedule-plist` |

Generators write resolved paths and (for Telegram) chain the Keychain
wrapper so tokens never appear in the plist. They print a `launchctl
bootstrap` command; they never load the agent themselves.

Hand-editing a template: replace every `REPLACE_*` value, create
`~/Library/Logs/CyClaw`, test `ProgramArguments` by hand, then copy to
`~/Library/LaunchAgents/` and load **explicitly**.

## Related

- Dropbox sync scheduling: [`docs/SYNC_README.md`](../docs/SYNC_README.md)
- Telegram channel: [`docs/channels/TELEGRAM_DESIGN.md`](../docs/channels/TELEGRAM_DESIGN.md)
- OpenTweet X channel: [`docs/channels/OPENTWEET_DESIGN.md`](../docs/channels/OPENTWEET_DESIGN.md)
- Agentic / registry: [`agentic/README.md`](../agentic/README.md)
- Console slash commands (`/goal`, `/loop`, `/skills`, `/tools`, `/web`): [`harness/README.md`](../harness/README.md)
