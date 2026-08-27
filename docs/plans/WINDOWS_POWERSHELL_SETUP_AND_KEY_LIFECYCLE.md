# Windows PowerShell setup and key-lifecycle issues

Status: open follow-up. Reviewed against `origin/main` at `c27a36cf` on 2026-08-26.
Planning only. No runtime change, no I1–I6 / topology / write-posture change.

Twin of `docs/plans/MACOS_BASH_SETUP_AND_KEY_LIFECYCLE.md`. Windows scripts live under `powershell/` + `windows/generate_service_task.py`. Do not fold implementation into `#1111`–`#1113` or `#1103`.

## What exists (and what does not)

| Darwin | Windows | Status |
| --- | --- | --- |
| `macos/install-cyclaw.sh` | `powershell/Install-CyClaw.ps1` | exists; Windows still silently `Remove-Item -Recurse` a stale repo dir (`#1115` fixed Darwin only) |
| `macos/uninstall-cyclaw.sh` | `powershell/Uninstall-CyClaw.ps1` | exists; no CredDelete |
| `macos/invoke-cyclaw.sh` | `powershell/Invoke-CyClaw.ps1` | exists; Windows starts **harness only**, not gate.py |
| `macos/cyclaw-keychain-set.sh` | `powershell/CyClaw-CredMan-Set.ps1` | exists; TTY SecureString + CredWriteW; never cmdkey |
| `macos/cyclaw-keychain-env.sh` | `powershell/CyClaw-CredMan-Env.ps1` | exists; CredReadW + call-operator wrap |
| `macos/setup-cyclaw-keys.sh` | **missing** | no autogen `CYCLAW_API_KEY`, no dotenv, no rotate task |
| `macos/setup-from-clone.sh` | **missing** | no Option C orchestrator |
| `macos/setup-fsconnect.sh` | `powershell/Setup-FsConnect.ps1` | exists; installer does **not** call it |
| `macos/generate_service_plist.py` | `windows/generate_service_task.py` | exists; `--confirm` + `--reason`; CredMan wrap via `utils.win_schtasks` |

There is no Windows equivalent of `~/.CyClaw/.env` + rc source block. Interactive `cyclaw` gets `CYCLAW_API_KEY` only if the parent session already has it, or the operator pastes into the console field.

## P0 — installer still silently destroys a stale repo dir

`Install-CyClaw.ps1` on main:

```powershell
elseif (-not (Test-Path (Join-Path $Repo "harness\server.py"))) {
    if (Test-Path $Repo) { Remove-Item -Recurse -Force $Repo }
```

`#1115` replaced the Darwin twin with `--replace-repo`. Windows did not get the flag. Same data-loss primitive.

**Fix shape:** `-ReplaceRepo` switch; without it, throw and name the flag. Pin in `tests/test_powershell_windows_parity.py`.

## P0 — uninstall never deletes CredMan items

`Uninstall-CyClaw.ps1` unschedules a fixed task list and optionally deletes `%USERPROFILE%\.CyClaw`. It never calls `CredDelete`. Known targets that generators and a future keys script will use:

- `com.cgfixit.cyclaw.api-key`
- `com.cgfixit.cyclaw.telegram-bot-token`
- `com.cgfixit.cyclaw.anthropic-api-key`
- `com.cgfixit.cyclaw.grok-api-key`
- `com.cgfixit.cyclaw.gh-token`

Any process running as this user can `CredRead` them after `-RemoveHome`.

**Fix shape:** `-RemoveCredMan` (default off), prompt, `CredDeleteW` only those five `CRED_TYPE_GENERIC` targets. Never `cmdkey /delete` (argv + enumeration). Missing target is a no-op. Cover with a fake P/Invoke test or a text pin plus a small C# stub if the suite already hosts one.

Also add `CyClaw keys-rotate` to `$KnownTaskNames` if/when a rotate task exists. Today it does not.

## P1 — no key bootstrap script

Darwin `setup-cyclaw-keys.sh` autogenerates `CYCLAW_API_KEY`, prompts for the four operator tokens, persists to Keychain + dotenv, and can schedule rotate. Windows has only the raw Set/Env pair. Operators must invent a target name and a generation method themselves.

**Fix shape (later PR, not a drive-by):** `powershell/Setup-CyClaw-Keys.ps1` mirroring the Darwin flags that matter (`-Rotate`, `-SkipPrompts`, `-GrokDummy`, `-NoCredMan` is not useful — CredMan is the only persist path). Generate with `RandomNumberGenerator.GetBytes(20)` hex, not `openssl`. Store via the same CredWrite path as Set.ps1 (no temp file on disk). Do not write a checkout `.env` unless someone asks; Windows has no rc-source convention that matches Darwin.

## P1 — Invoke does not start gate.py and does not load CredMan

`Invoke-CyClaw.ps1` runs only `python -m harness.server`. Darwin invoke starts gate `:8787` + harness `:8790` and has a dual-PID cleanup trap (`#1114`).

It also never calls `CyClaw-CredMan-Env.ps1`. Comment is explicit: key is inherited or pasted. Same-tab 401 after install is the default.

**Fix shape:**

- Optional `-WithGate` (or just start both, matching Darwin). If both, add a `try/finally` that stops both processes — see trap section in the review notes.
- Optional: if `$env:CYCLAW_API_KEY` is empty, `CredRead` target `com.cgfixit.cyclaw.api-key` and inject. Fail soft (warn) if missing so a paste-only operator is not blocked.

## P2 — CredRead error taxonomy is coarser than `#1020`

`CyClaw-CredMan-Env.ps1` treats every `CredRead` failure as “no item” and prints `store it first`, including `ERROR_INVALID_PARAMETER` / access-denied. Darwin keychain-env distinguishes missing (44) vs unreadable vs empty vs tool error.

**Fix shape:** map `1168` / `ERROR_NOT_FOUND` to missing; anything else to “could not read (win32=N)” and do not suggest Set.ps1.

## P2 — installer does not prepare fsconnect

Darwin `install-cyclaw.sh` always runs `setup-fsconnect.sh --prepare-only`. Windows `Install-CyClaw.ps1` never calls `Setup-FsConnect.ps1`. Jail + ACL exist only if the operator remembers the extra script.

**Fix shape:** call `Setup-FsConnect.ps1 -PrepareOnly` from the installer after the repo is in place. Keep enablement opt-in.

## Out of scope

- Per-app CredMan ACL (does not exist for GENERIC credentials).
- Credential Guard / VBS (does not wrap GENERIC CredWrite blobs).
- Writing secrets into task XML.
- `cmdkey.exe` as a store/read path.
