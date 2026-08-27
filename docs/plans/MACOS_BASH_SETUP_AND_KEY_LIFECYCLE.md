# macOS bash setup and key-lifecycle issues

Status: open follow-up. Revalidated against `origin/main` at `7d2ab716` after
#1114 merged on 2026-08-27, plus #1115's branch diff.
This file is planning only except where the Implementation table says otherwise. It does not change I1-I6, graph topology, or write posture.

**Implementation provenance:** installer PR **#1115** was originally stacked on
**#1114** (`invoke-cyclaw.sh` liveness). Do not fold the remaining follow-ups
into the console JS work or **#1103** (Origin header / auth).

Twin plan: `docs/plans/WINDOWS_POWERSHELL_SETUP_AND_KEY_LIFECYCLE.md`.

## Implementation status

Legend: **main** = on `origin/main` at `7d2ab716`. **#1114** / **#1115** =
implemented by that PR. **plan** = documented, not coded. **wont** = out of
scope.

| Item | Pri | Implementation |
| --- | --- | --- |
| Persist contract (Keychain + `~/.CyClaw/.env` + rc source-only) | -- | main |
| `cyclaw-keychain-set.sh` / `cyclaw-keychain-env.sh` (bare `-w`, `-T`, `#1020`) | -- | main |
| `setup-cyclaw-keys.sh` generate / prompt / store | -- | main |
| `--fill-browser` EXIT trap (`#1032`, `cyclaw.fillkey.*`) | -- | main |
| `install-cyclaw.sh --replace-repo` | P0 | **#1115** |
| `setup-cyclaw.sh` names `--repo` / `--clone-dir` | P3 | **#1115** |
| This plan + trap/ACL appendices | -- | **#1115** (docs only) |
| `_keychain_store_value` EXIT trap (`cyclaw.kc.*`) | P0 | plan |
| `_copy_key` EXIT trap (`cyclaw.clip.*`) | P0 | plan |
| `uninstall-cyclaw.sh --remove-keychain` (five services) | P0 | plan |
| Shim sources `~/.CyClaw/.env` (xtrace refuse) | P1 | plan |
| Rotate job refuses if gateway is up | P1 | plan |
| README triple-copy / `setup-cyclaw.sh` identity | P2 | plan |
| Keychain ACL is any `security` reader (`apple-tool:`) | P2 | wont (document only; Appendix B) |
| `invoke-cyclaw.sh` dual-PID wait | -- | **#1114** (merged; do not re-implement here) |
| Signed Keychain helper / `set-generic-password-partition-list` | -- | wont |

## What is already correct

Do not rip up the persist contract. It is coherent:

```
setup-from-clone.sh                 Option C orchestrator
  install-cyclaw.sh                 home / venv / shim / PATH  (no secrets)
  setup-cyclaw-keys.sh              generate + persist
    Keychain                        official launchd path
    ~/.CyClaw/.env                  chmod 600 dotenv
    <repo>/.env                     optional, gitignored
  source ~/.CyClaw/.env             this process only; refuse if xtrace is on
  exec invoke-cyclaw.sh             servers inherit the env
```

- Interactive shells inherit keys because `setup-cyclaw-keys.sh` appends a **source-only** marker block to `~/.zshrc` (or the bash login file). Secrets are never inlined into an rc file.
- LaunchAgents never read `.env`. They wrap the binary with `macos/cyclaw-keychain-env.sh`.
- `cyclaw-keychain-set.sh` uses a bare `-w` so `security` does the TTY `readpassphrase(3)` itself. The secret is never a shell variable and never an argv token. `-T` pins the ACL to `/usr/bin/security`. Non-TTY is refused.
- `cyclaw-keychain-env.sh` fails closed and distinguishes missing (`security` 44) vs unreadable vs empty vs tool error (#1020). `VAR_NAME` is allowlisted before any Keychain call. Wrappers compose via `exec`.
- `setup-cyclaw-keys.sh` generates `CYCLAW_API_KEY` with `openssl rand -hex 20`. Operator tokens are prompt-or-skip. Store path is a 0600 temp file plus `/usr/bin/expect` (or the test stdin path), not `-w "$SECRET"`. An unreadable or empty existing API-key item refuses to mint a replacement.
- `--fill-browser` is loopback-only and memory-only. `#1032` already traps `cyclaw.fillkey.*` on EXIT.
- `--schedule-rotate` writes a plist and does **not** load it. No secret in the plist.
- Tests in `tests/test_cyclaw_keychain_scripts.py` and `tests/test_setup_cyclaw_keys.py` run against a fake `security` without Darwin.
- I3 / I5 / I6 stay intact: no `config.yaml` writes, no soul touch, no core imports.

The remaining work is **lifecycle**, not generation hygiene.

## P0 -- uninstall never deletes Keychain items

Implementation: **plan**.

`macos/uninstall-cyclaw.sh` has no `security delete-generic-password` path. `--remove-home` deletes `~/.CyClaw/.env` and still leaves:

| Keychain service | Implementation |
| --- | --- |
| `com.cgfixit.cyclaw.api-key` | plan (delete on `--remove-keychain`) |
| `com.cgfixit.cyclaw.telegram-bot-token` | plan |
| `com.cgfixit.cyclaw.anthropic-api-key` | plan |
| `com.cgfixit.cyclaw.grok-api-key` | plan |
| `com.cgfixit.cyclaw.gh-token` | plan |

Those items stay readable by any local process that can run `/usr/bin/security` (the ACL the wrappers already document). An operator who thinks "uninstall + delete home = secrets gone" is wrong.

**Fix shape**

- Add `--remove-keychain` (default off).
- Prompt. Delete only the five known service names for `id -un`.
- Never wildcard-delete Keychain items.
- Do not do this silently on a bare uninstall.
- Cover with the existing fake-`security` test harness (`delete-generic-password` logged, secret value never in argv or stderr).

## P0 -- `_keychain_store_value` temp files have no EXIT trap

Implementation: **plan**. `#1032` (`cyclaw.fillkey.*`) is **main**.

The store path still writes the secret to `$TMPDIR/cyclaw.kc.XXXXXX`, calls expect, then `rm -f`. There is no EXIT trap. Ctrl-C / kill during the expect window leaves plaintext at a predictable temp prefix.

`_copy_key` has a shorter window of the same shape (`$TMPDIR/cyclaw.clip.*`). Also **plan**.

**Fix shape:** copy the `#1032` idiom in Appendix A onto those two functions. Do not invent a new trap style.

## P1 -- `cyclaw` shim / `invoke-cyclaw.sh` do not load the dotenv

Implementation: **plan**.

On main, `invoke-cyclaw.sh` only warns if `CYCLAW_API_KEY` is unset. The installed shim exports `CYCLAW_HOME` / `CYCLAW_REPO` and execs invoke. Neither sources `~/.CyClaw/.env`.

`setup-from-clone.sh` already has the safe source idiom (`xtrace` refuse + `set -a`). Reuse it in **one** place: the shim. Do not grow a second copy inside invoke.

## P1 -- scheduled rotate vs a live gateway

Implementation: **plan**.

`--schedule-rotate monthly|weekly` writes a 04:00 LaunchAgent that mints a new `CYCLAW_API_KEY` and does not restart gate/harness. Prefer fail-closed if `/health` succeeds. Do not auto-kill `gate.py` without `--confirm` / `--reason`.

## P2 -- triple plaintext copies

Implementation: **plan** (README sentence only). Do not silently flip `--repo-env` default.

## P2 -- Keychain ACL is "anyone who can run `security`"

Implementation: **wont** as a code change. Documented in wrapper headers + Appendix B. P0 uninstall-delete is what stops leftover items from being immortal.

## P3 -- smaller Darwin / bash nits

- `expect` heredoc / Tcl metacharacters in username: **plan** if that function is touched.
- `macos/setup-cyclaw.sh` collision messaging: **#1115**.
- `invoke-cyclaw.sh` dual-PID wait: **#1114** (merged; do not re-implement here; stay on bash 3.2, never `wait -n`).

## Suggested implementation order

1. EXIT trap on `_keychain_store_value` / `_copy_key` temps (same pattern as `#1032`). Lowest risk. **plan**
2. `uninstall-cyclaw.sh --remove-keychain` for the five known service names. Highest operator value. **plan**
3. Shim-side `source ~/.CyClaw/.env` with the existing xtrace-refuse guard. **plan**
4. Rotate job refuses if the gateway is up. **plan**
5. README sentences for the triple-copy and `setup-cyclaw.sh` identity. **plan**

## Out of scope

- Signed Keychain helper binary.
- Changing Keychain service names (launchd generators and tests pin them).
- Flipping `auth.enabled` / `api.tls.enabled`.
- Loading LaunchAgents from setup scripts.
- Anything in `static/terminal.js` / `static/harness.html` / `gate.py`.
- Custom keychain files / `set-generic-password-partition-list` (see ACL appendix).

## Verify when code lands

```bash
bash -n macos/install-cyclaw.sh macos/setup-cyclaw-keys.sh macos/setup-from-clone.sh \
        macos/uninstall-cyclaw.sh macos/cyclaw-keychain-set.sh macos/cyclaw-keychain-env.sh \
        macos/invoke-cyclaw.sh macos/setup-cyclaw.sh
GROK_API_KEY=dummy python -m pytest \
  tests/test_macos_scripts.py \
  tests/test_cyclaw_keychain_scripts.py \
  tests/test_setup_cyclaw_keys.py \
  tests/test_setup_from_clone.py -q --tb=short
```

No Darwin required for the tests above. Real-hardware checks that still need a Mac (do not claim they passed in CI):

- bare `-w` actually prompts on this `security` version
- `ps -ww` during the prompt shows no secret in argv
- `-T /usr/bin/security` suppresses the later read dialog for launchd
- `--remove-keychain` deletes only the five named items for this account
- `security dump-keychain -a` on the login keychain shows trusted app `/usr/bin/security` and usually partition `apple-tool:,apple:`

---

## Appendix A -- bash EXIT trap idiom (`#1032` and the missing store trap)

`#1032` `_fill_browser` is **main**. Store/copy traps are **plan**.

`_fill_browser` on main is the pattern to copy. Cleartext lives in `$TMPDIR/cyclaw.fillkey.*` only while `osascript` runs.

```bash
_fill_browser() {
  local secret_file scpt
  secret_file="$(mktemp "${TMPDIR:-/tmp}/cyclaw.fillkey.XXXXXX")"
  scpt="$(mktemp "${TMPDIR:-/tmp}/cyclaw.fill.XXXXXX")"
  trap 'rm -f "$secret_file" "$scpt"' EXIT
  umask 077
  printf '%s' "$_api_value" > "$secret_file"
  chmod 600 "$secret_file"
  # ... write AppleScript, osascript "$scpt" "$secret_file" ...
  rm -f "$secret_file" "$scpt"
  trap - EXIT
}
```

Why that shape:

| Choice | Why |
| --- | --- |
| `trap ... EXIT` | bash runs EXIT on normal return, `exit`, `set -e` death, and after INT/TERM. One trap covers the function. |
| Single-quoted body `trap 'rm -f "$tmp"' EXIT` | The text is stored literally. `$tmp` expands when EXIT **fires**, which is the mktemp path assigned two lines earlier. |
| Register **after** mktemp, **before** the first write | If mktemp fails there is nothing to shred. If printf fails the empty 0600 file still goes away. |
| `trap - EXIT` after the explicit `rm` | bash has one handler per signal. Leaving the function-local trap installed lets the next `_prompt_secret` clobber it. |
| `rm -f` never `rm -rf` | Names are files from `mktemp`. No glob. |

Wrong forms:

```bash
trap "rm -f $secret_file $scpt" EXIT   # expands NOW -- empty if set before mktemp
trap 'rm -f $secret_file' EXIT         # unquoted -> word-split if TMPDIR has spaces
```

### Drop-in for `_keychain_store_value` (still missing on main -- plan)

```bash
_keychain_store_value() {
  local service="$1" value="$2" tmp rc old_umask
  old_umask="$(umask)"
  umask 077
  tmp="$(mktemp "${TMPDIR:-/tmp}/cyclaw.kc.XXXXXX")"
  trap 'rm -f "$tmp"' EXIT
  printf '%s' "$value" > "$tmp"
  chmod 600 "$tmp"
  umask "$old_umask"
  rc=0
  _keychain_store_file "$service" "$tmp" || rc=$?
  rm -f "$tmp"
  trap - EXIT
  return "$rc"
}
```

Same for `_copy_key` (`cyclaw.clip.*`). Do **not** extend that trap across the clipboard TTL background job.

Contract pin for `tests/test_setup_cyclaw_keys.py` (string pin, no Darwin) when the store trap lands.

---

## Appendix B -- macOS Keychain ACL layers

Documentation only (**wont** as a code change on this stack). CyClaw does not talk to Security.framework. Store and read exec `/usr/bin/security`.

Three checks: (1) access object / `-T` trusted apps, (2) partition ID `apple-tool:` on the login keychain, (3) keychain lock + GUI session. `-T /usr/bin/security` does not mean "only CyClaw." Any local process that can exec `/usr/bin/security` and knows `(account, service)` can `find-generic-password -w`. Unsigned shell scripts cannot be ACL subjects. Leave the login-keychain model; pair it with uninstall-delete.
