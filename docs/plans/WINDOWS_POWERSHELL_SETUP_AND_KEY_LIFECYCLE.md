# Windows PowerShell setup and key-lifecycle issues

Status: open follow-up. Reviewed against `origin/main` at `c27a36cf` on 2026-08-26.
Planning only for the remaining items. No I1-I6 / topology / write-posture change.

Twin of `docs/plans/MACOS_BASH_SETUP_AND_KEY_LIFECYCLE.md`. Windows scripts live under `powershell/` + `windows/generate_service_task.py`. Do not fold remaining implementation into `#1111`-`#1113` or `#1103`.

## Implementation status

Legend: **main** = on `origin/main` (`c27a36cf`). **#1115** = coded on this branch, not yet on main. **plan** = documented, not coded. **wont** = out of scope. **missing** = no Windows twin.

| Darwin | Windows | Exists | Implementation |
| --- | --- | --- | --- |
| `macos/install-cyclaw.sh --replace-repo` | `Install-CyClaw.ps1 -ReplaceRepo` | both exist | Darwin **#1115**; Windows still silent `Remove-Item` (**plan**) |
| `uninstall-cyclaw.sh --remove-keychain` | `Uninstall-CyClaw.ps1 -RemoveCredMan` | both exist | neither deletes secrets (**plan**) |
| `invoke-cyclaw.sh` gate+harness + `#1114` dual-PID | `Invoke-CyClaw.ps1` | both exist | Windows harness-only, no CredMan inject (**plan**) |
| `cyclaw-keychain-set.sh` | `CyClaw-CredMan-Set.ps1` | both exist | Set/Env **main**; PS7 CancelKeyPress + blob wipe **#1115** |
| `cyclaw-keychain-env.sh` (`#1020`) | `CyClaw-CredMan-Env.ps1` | both exist | wrapper **main**; win32 missing-vs-unreadable **plan** |
| `setup-cyclaw-keys.sh` | `Setup-CyClaw-Keys.ps1` | Darwin only | **missing** / later PR (**plan**) |
| `setup-from-clone.sh` | (none) | Darwin only | **missing** |
| `setup-fsconnect.sh` installer `--prepare-only` | `Setup-FsConnect.ps1` | both exist | script **main**; installer does not call it (**plan**) |
| `generate_service_plist.py` | `windows/generate_service_task.py` | both exist | **main** |
| Keychain ACL / partition ID | CredMan GENERIC persist | n/a | **wont** (Appendix B) |
| This plan + appendices | this file | docs | **#1115** (docs only) |

**Already landed on this branch (#1115), not on main:** `powershell/CyClaw-CredMan-Set.ps1` flattened `try/finally` + unmanaged-blob wipe + PowerShell 7-only `[Console]::CancelKeyPress` (exit 130). Contract pin in `tests/test_powershell_windows_parity.py::test_credman_set_ps7_ctrl_c_registers_cancel_keypress`. 5.1 keeps `try/finally` only -- do not install `e.Cancel=$true` there.

There is no Windows equivalent of `~/.CyClaw/.env` + rc source block. Interactive `cyclaw` gets `CYCLAW_API_KEY` only if the parent session already has it, or the operator pastes into the console field.

## P0 -- installer still silently destroys a stale repo dir

Implementation: **plan** (Darwin twin is **#1115**).

`Install-CyClaw.ps1` on main still `Remove-Item -Recurse -Force` a stale repo dir. **Fix shape:** `-ReplaceRepo` switch; without it, throw and name the flag.

## P0 -- uninstall never deletes CredMan items

Implementation: **plan**.

`Uninstall-CyClaw.ps1` never calls `CredDelete`. Five known targets only. **Fix shape:** `-RemoveCredMan` (default off), `CredDeleteW`, never `cmdkey /delete`.

## P1 -- no key bootstrap script

Implementation: **missing** / later PR (**plan**). Do not start `Setup-CyClaw-Keys.ps1` as a drive-by on `#1115`.

## P1 -- Invoke does not start gate.py and does not load CredMan

Implementation: **plan**. Optional `-WithGate` + `try/finally` dual-PID stop (Appendix A). Optional CredRead of `com.cgfixit.cyclaw.api-key` when env is empty; fail soft if missing.

## P2 -- CredRead error taxonomy is coarser than `#1020`

Implementation: **plan**. Map win32 `1168` / `ERROR_NOT_FOUND` to missing.

## P2 -- installer does not prepare fsconnect

Implementation: script **main**; installer call **plan**.

## P2 -- `git pull --no-autostash` vs Darwin `--autostash`

Implementation: **plan** (policy pick only).

## Suggested implementation order

1. `-ReplaceRepo` on `Install-CyClaw.ps1` -- **plan**
2. Flatten Set.ps1 + wipe + PS7 CancelKeyPress -- **#1115** (done; do not redo)
3. `-RemoveCredMan` + `CredDeleteW` -- **plan**
4. CredRead win32 taxonomy in Env.ps1 -- **plan**
5. Installer calls `Setup-FsConnect.ps1 -PrepareOnly` -- **plan**
6. New `Setup-CyClaw-Keys.ps1` only after 1 and 3-5 -- **plan** / later PR

## Out of scope

- Per-app CredMan ACL (does not exist for GENERIC credentials).
- Credential Guard / VBS (does not wrap GENERIC CredWrite blobs).
- Writing secrets into task XML.
- `cmdkey.exe` as a store/read path.
- `Register-EngineEvent PowerShell.Exiting` in Set.ps1.
- CancelKeyPress on `CyClaw-CredMan-Env.ps1` unless someone asks.

---

## Appendix A -- try/finally is the Windows EXIT trap

Set.ps1 wipe + PS7 CancelKeyPress: **#1115**. Env.ps1 CredFree finally: **main**. Invoke dual-PID: **plan**.

PowerShell has no `trap EXIT` that reliably shreds a resource across Ctrl+C, `throw`, and `exit`. The right primitive is `try/finally` around unmanaged secret memory. CredMan-Set never writes a temp file, so the `#1032` / `cyclaw.kc.*` class of bug does not exist on the store path.

One function on this branch, used by both `finally` and PS7 Ctrl+C: `Invoke-CyclawCredCleanup` (idempotent). It zeros the unmanaged blob with `Marshal.WriteByte` then `FreeHGlobal`, `ZeroFreeBSTR`, and `SecureString.Dispose()`.

PowerShell 7 `CancelKeyPress` is gated on `$PSVersionTable.PSVersion.Major -ge 7` plus `UserInteractive` and not redirected. Handler sets `$eventArgs.Cancel = $true`, runs cleanup, `[Environment]::Exit(130)`. **Not installed on 5.1** (`e.Cancel=$true` can hang that host).

---

## Appendix B -- CredMan has no Keychain-style -T

**wont** as a code change. Windows GENERIC credentials do not have an access object + trusted-app list + partition IDs. Isolation is user + machine + DPAPI, not "this script."

Set.ps1 writes `CRED_TYPE_GENERIC` + `CRED_PERSIST_LOCAL_MACHINE`. Any process running as that user can `CredRead` `com.cgfixit.cyclaw.api-key`. `cmdkey` cannot retrieve the blob cleanly; the API can. Credential Guard does not wrap GENERIC blobs. Do not port `-T /usr/bin/security` thinking.
