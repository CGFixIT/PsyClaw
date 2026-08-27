# Windows PowerShell setup and key-lifecycle issues

Status: open follow-up. Reviewed against `origin/main` at `c27a36cf` on 2026-08-26.
Planning only for the remaining items. No I1-I6 / topology / write-posture change.

Twin of `docs/plans/MACOS_BASH_SETUP_AND_KEY_LIFECYCLE.md`. Windows scripts live under `powershell/` + `windows/generate_service_task.py`. Do not fold remaining implementation into `#1111`-`#1113` or `#1103`.

**Already landed on this branch (#1115), not on main:** `powershell/CyClaw-CredMan-Set.ps1` flattened `try/finally` + unmanaged-blob wipe + PowerShell 7-only `[Console]::CancelKeyPress` (exit 130). Contract pin in `tests/test_powershell_windows_parity.py::test_credman_set_ps7_ctrl_c_registers_cancel_keypress`. 5.1 keeps `try/finally` only -- do not install `e.Cancel=$true` there.

## What exists (and what does not)

| Darwin | Windows | Status |
| --- | --- | --- |
| `macos/install-cyclaw.sh` | `powershell/Install-CyClaw.ps1` | exists; Windows still silently `Remove-Item -Recurse` a stale repo dir (`#1115` fixed Darwin only) |
| `macos/uninstall-cyclaw.sh` | `powershell/Uninstall-CyClaw.ps1` | exists; no CredDelete |
| `macos/invoke-cyclaw.sh` | `powershell/Invoke-CyClaw.ps1` | exists; Windows starts **harness only**, not gate.py |
| `macos/cyclaw-keychain-set.sh` | `powershell/CyClaw-CredMan-Set.ps1` | exists; TTY SecureString + CredWriteW; never cmdkey; **PS7 Ctrl+C wipe on this branch** |
| `macos/cyclaw-keychain-env.sh` | `powershell/CyClaw-CredMan-Env.ps1` | exists; CredReadW + call-operator wrap |
| `macos/setup-cyclaw-keys.sh` | **missing** | no autogen `CYCLAW_API_KEY`, no dotenv, no rotate task |
| `macos/setup-from-clone.sh` | **missing** | no Option C orchestrator |
| `macos/setup-fsconnect.sh` | `powershell/Setup-FsConnect.ps1` | exists; installer does **not** call it |
| `macos/generate_service_plist.py` | `windows/generate_service_task.py` | exists; `--confirm` + `--reason`; CredMan wrap via `utils.win_schtasks` |

There is no Windows equivalent of `~/.CyClaw/.env` + rc source block. Interactive `cyclaw` gets `CYCLAW_API_KEY` only if the parent session already has it, or the operator pastes into the console field.

## P0 -- installer still silently destroys a stale repo dir

`Install-CyClaw.ps1` on main still `Remove-Item -Recurse -Force` a stale `~\.CyClaw\repo`. `#1115` replaced the Darwin twin with `--replace-repo`. Windows did not get the flag.

**Fix shape:** `-ReplaceRepo` switch; without it, throw and name the flag. Pin in `tests/test_powershell_windows_parity.py`.

## P0 -- uninstall never deletes CredMan items

`Uninstall-CyClaw.ps1` never calls `CredDelete`. Known targets:

- `com.cgfixit.cyclaw.api-key`
- `com.cgfixit.cyclaw.telegram-bot-token`
- `com.cgfixit.cyclaw.anthropic-api-key`
- `com.cgfixit.cyclaw.grok-api-key`
- `com.cgfixit.cyclaw.gh-token`

Any process running as this user can `CredRead` them after `-RemoveHome`.

**Fix shape:** `-RemoveCredMan` (default off), prompt, `CredDeleteW` only those five `CRED_TYPE_GENERIC` targets. Never `cmdkey /delete`. Missing target is a no-op. Add `CyClaw keys-rotate` to `$KnownTaskNames` only if/when a rotate task exists.

## P1 -- no key bootstrap script

Windows has only the raw Set/Env pair. Later PR: `powershell/Setup-CyClaw-Keys.ps1` with `-Rotate` / `-SkipPrompts` / `-GrokDummy`. Generate with `RandomNumberGenerator.GetBytes(20)` hex. Store via the same CredWrite path as Set.ps1 (no temp file). No checkout `.env` unless asked.

## P1 -- Invoke does not start gate.py and does not load CredMan

`Invoke-CyClaw.ps1` runs only `python -m harness.server`. Optional `-WithGate` + `try/finally` dual-PID stop (Appendix A). Optional CredRead of `com.cgfixit.cyclaw.api-key` when `$env:CYCLAW_API_KEY` is empty; fail soft if missing.

## P2 -- CredRead error taxonomy is coarser than `#1020`

Map win32 `1168` / `ERROR_NOT_FOUND` to missing. Anything else is "could not read (win32=N)" -- do not suggest Set.ps1.

## P2 -- installer does not prepare fsconnect

Call `Setup-FsConnect.ps1 -PrepareOnly` from the installer after the repo is in place. Keep enablement opt-in. That script is the one place Windows does real NTFS ACLs (`SetAccessRuleProtection` + current user FullControl). File-system ACL, not CredMan ACL.

## P2 -- `git pull --no-autostash` vs Darwin `--autostash`

Pick one policy across installers. Do not silently flip either side in this pass.

## Suggested implementation order

1. `-ReplaceRepo` on `Install-CyClaw.ps1` (parity with `#1115` Darwin).
2. Flatten Set.ps1 + wipe + PS7 CancelKeyPress. **Done on this branch.** Do not redo it.
3. `-RemoveCredMan` + `CredDeleteW` on the five targets.
4. CredRead win32 taxonomy in Env.ps1.
5. Installer calls `Setup-FsConnect.ps1 -PrepareOnly`.
6. New `Setup-CyClaw-Keys.ps1` only after 1 and 3-5.

## Out of scope

- Per-app CredMan ACL (does not exist for GENERIC credentials).
- Credential Guard / VBS (does not wrap GENERIC CredWrite blobs).
- Writing secrets into task XML.
- `cmdkey.exe` as a store/read path.
- `Register-EngineEvent PowerShell.Exiting` in Set.ps1.
- CancelKeyPress on `CyClaw-CredMan-Env.ps1` unless someone asks.

---

## Appendix A -- try/finally is the Windows EXIT trap

PowerShell has no `trap EXIT` that reliably shreds a resource across Ctrl+C, `throw`, and `exit`. The right primitive is `try/finally` around unmanaged secret memory. CredMan-Set never writes a temp file, so the `#1032` / `cyclaw.kc.*` class of bug does not exist on the store path.

### What Set.ps1 now does on this branch

One function, used by both `finally` and PS7 Ctrl+C: `Invoke-CyclawCredCleanup` (idempotent). It zeros the unmanaged blob with `Marshal.WriteByte` then `FreeHGlobal`, `ZeroFreeBSTR`, and `SecureString.Dispose()`. `$plain` is only nulled -- .NET strings are immutable. Empty secret and CredWrite failure `throw` instead of `exit 1` inside the try so finally runs.

### PowerShell 7 Ctrl+C (shipped on this branch)

Windows PowerShell 5.1 often skips `finally` on Ctrl+C. PowerShell 7 `CancelKeyPress` runs before the process is torn down.

Gated on `$PSVersionTable.PSVersion.Major -ge 7` plus `UserInteractive` and not redirected. Handler sets `$eventArgs.Cancel = $true`, runs cleanup, `[Environment]::Exit(130)`. Removed in `finally` on the normal path.

**Not installed on 5.1.** That host can hang if Ctrl+C is treated as cancelable input. 5.1 still gets flattened try/finally + wipe.

Do not ship `Register-EngineEvent -SourceIdentifier PowerShell.Exiting` in Set.ps1.

### CredMan-Env

`CredRead` in try, `CredFree` in finally, inject into `Env:$EnvVar`, null `$secret`, then `& $exe @exeArgs` (call operator, not Start-Process). After inject the secret lives in this process environment until exit. That is the point. Do not add a disk file.

### Invoke dual-PID (not shipped; only if Invoke grows a gate)

`Start-Process -PassThru` for gate + harness, `Wait-Process` on harness, `finally` `Stop-Process` both. Do not use `wait -n`. Do not use bash. Uninstall already uses try/finally around `sync.cli unschedule`.

---

## Appendix B -- CredMan has no Keychain-style -T

Windows GENERIC credentials do not have an access object + trusted-app list + partition IDs. Isolation is user + machine + DPAPI, not "this script."

Set.ps1 writes `CRED_TYPE_GENERIC` + `CRED_PERSIST_LOCAL_MACHINE` (survives logoff; visible to other logon sessions of this same user on this same computer; not visible on other computers). `SESSION` dies at logoff (useless for Task Scheduler). `ENTERPRISE` roams -- do not use.

Who can CredRead:

- Any process running as that user. There is no `-T powershell.exe` and no "only CyClaw-CredMan-Env.ps1."
- Other users on the box: no, not via CredRead in their session.
- SYSTEM / another admin: not via the same user's CredRead, but they can take the DPAPI master key offline (`%APPDATA%\Microsoft\Protect\<SID>\` + `%APPDATA%\Microsoft\Credentials\`) and unwrap the blob. Solved red-team path, not an ACL miss in CyClaw.
- `SeTrustedCredmanAccessPrivilege` is for LSASS backup APIs, not GENERIC CredRead from a user script.
- Credential Guard / LSAIso protects domain creds and NTLM/TGT. It does not wrap `CRED_TYPE_GENERIC` blobs CyClaw stores. Do not claim VBS coverage.

Honest Windows sentence, parallel to Darwin "any process that can run `/usr/bin/security`":

Any process running as this user on this machine can `CredRead` `com.cgfixit.cyclaw.api-key`. `cmdkey` cannot retrieve the blob cleanly; the API can. That is why Env.ps1 uses `CredReadW` and why Set.ps1 refuses `cmdkey /add`.

No signed helper narrows that. The only tighter persist is `CRED_PERSIST_SESSION`, which breaks launch-at-logon tasks. Do not port `-T /usr/bin/security` thinking. The control you actually have is: no argv, wipe unmanaged memory, delete on uninstall, and "any process as this user can read."
