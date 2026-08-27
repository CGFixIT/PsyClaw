# macOS bash setup and key-lifecycle issues

Status: open follow-up. Reviewed against `origin/main` at `c27a36cf` on 2026-08-26.
This file is planning only. It does not change runtime behavior, I1–I6, graph topology, or write posture.

**Home for this work:** stacked installer PR **#1115** (`kimi/cyclaw-optimize-installer-safety`, base `#1114` / `invoke-cyclaw.sh` liveness). Do not fold these fixes into `#1111`–`#1113` (console JS) or `#1103` (Origin header / auth).

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

## P0 — uninstall never deletes Keychain items

`macos/uninstall-cyclaw.sh` has no `security delete-generic-password` path. `--remove-home` deletes `~/.CyClaw/.env` and still leaves:

| Keychain service |
| --- |
| `com.cgfixit.cyclaw.api-key` |
| `com.cgfixit.cyclaw.telegram-bot-token` |
| `com.cgfixit.cyclaw.anthropic-api-key` |
| `com.cgfixit.cyclaw.grok-api-key` |
| `com.cgfixit.cyclaw.gh-token` |

Those items stay readable by any local process that can run `/usr/bin/security` (the ACL the wrappers already document). An operator who thinks “uninstall + delete home = secrets gone” is wrong.

**Fix shape**

- Add `--remove-keychain` (default off).
- Prompt. Delete only the five known service names for `id -un`.
- Never wildcard-delete Keychain items.
- Do not do this silently on a bare uninstall.
- Cover with the existing fake-`security` test harness (`delete-generic-password` logged, secret value never in argv or stderr).

## P0 — `_keychain_store_value` temp files have no EXIT trap

`#1032` closed the `_fill_browser` window (`$TMPDIR/cyclaw.fillkey.*`). The store path still writes the secret to `$TMPDIR/cyclaw.kc.XXXXXX`, calls expect, then `rm -f`. There is no EXIT trap. Ctrl-C / kill during the expect window leaves plaintext at a predictable temp prefix.

`_copy_key` has a shorter window of the same shape (`$TMPDIR/cyclaw.clip.*`).

**Fix shape**

- Same idiom as `#1032`: `trap 'rm -f "$tmp"' EXIT` around the store / copy, then `trap - EXIT` after the explicit `rm`.
- Pin in `tests/test_setup_cyclaw_keys.py` that the script text contains an EXIT trap covering `cyclaw.kc.` (and ideally `cyclaw.clip.`).

## P1 — `cyclaw` shim / `invoke-cyclaw.sh` do not load the dotenv

On main, `invoke-cyclaw.sh` only warns if `CYCLAW_API_KEY` is unset. The installed shim exports `CYCLAW_HOME` / `CYCLAW_REPO` and execs invoke. Neither sources `~/.CyClaw/.env`.

That matches the comment “nothing in CyClaw reads `.env` at runtime,” but it means:

- Option A (`install-cyclaw.sh` then `cyclaw` in the **same** tab) → 401 until `source ~/.CyClaw/.env` or a new login shell.
- A LaunchAgent from `generate_service_plist.py` is fine (Keychain wrap).
- A raw `cyclaw` from cron / an unloaded agent **without** the wrap is 401.

`setup-from-clone.sh` already has the safe source idiom (`xtrace` refuse + `set -a`). Reuse it in **one** place: the shim. Do not grow a second copy inside invoke.

**Fix shape**

- In the generated `$HOME/.CyClaw/bin/cyclaw` shim (and keep `install-cyclaw.sh` as the writer of that shim): if `CYCLAW_API_KEY` is empty and `$CYCLAW_HOME/.env` exists, refuse `$-` containing `x`, then `set -a; . "$CYCLAW_HOME/.env"; set +a`.
- Never print the file. Never log values.
- Contract test on the shim template inside `install-cyclaw.sh`.

## P1 — scheduled rotate vs a live gateway

`--schedule-rotate monthly|weekly` writes a 04:00 LaunchAgent that mints a new `CYCLAW_API_KEY` and does not restart gate/harness. Live servers keep the old key until someone restarts them. A loaded agent will quietly 401 Soul / ops / harness at 4am.

Documented today. Still the wrong default once someone actually `launchctl bootstrap`s the plist.

**Fix shape (pick one, prefer the first)**

1. Rotate job fails closed if `curl -sf --max-time 2 http://127.0.0.1:${CYCLAW_GATE_PORT:-8787}/health` succeeds. Print “gateway is up; bounce it, then re-run --rotate.”
2. Or stop recommending load unless a supervised gate/harness LaunchAgent is also in play and the rotate job is allowed to bounce it (that path needs `--confirm` + reason; do not sneak it in).

Do not auto-kill `gate.py` from a calendar agent without the same `--confirm` / `--reason` gate `generate_service_plist.py` already uses.

## P2 — triple plaintext copies

By design the same secret lands in Keychain + `~/.CyClaw/.env` + optional `<checkout>/.env`. The repo copy is the weakest: gitignored, but it sits in a working tree next to `git add`. `--no-repo-env` already exists; default-on is the aggressive choice.

**Fix shape**

- One sentence in `macos/README.md`: Keychain is source of truth for launchd; `~/.CyClaw/.env` is for interactive shells; the checkout `.env` is a convenience duplicate — delete it if you do not need it.
- Do not change the default in this pass unless the operator asks. Default-off for `--repo-env` is a product decision, not a silent flip.

## P2 — Keychain ACL is “anyone who can run `security`”

Already documented in the wrapper headers. Not fixable in shell. A signed helper binary is the only narrow ACL. Leave it. Item P0 (delete on uninstall) is what stops leftover items from being immortal.

## P3 — smaller Darwin / bash nits

- `expect` heredoc is unquoted, so `$ACCOUNT` / `$service` expand in bash before Tcl. Service names are constants. A username containing Tcl metacharacters (`[]`, `$`) can break the spawn line. Rare; quote for Tcl if this is touched.
- `expect` always spawns `/usr/bin/security`, not `$_SECURITY_BIN`. Correct on a real Mac. Tests already use the stdin-store branch.
- `_prompt_secret` uses `eval "$outvar=..."`. Call sites pass fixed identifiers. Never pass user input as `outvar`.
- `macos/setup-cyclaw.sh` still sits next to Option A (`install-cyclaw.sh`) and Option C (`setup-from-clone.sh`). Label the legacy twin in `macos/README.md` so nobody runs the wrong script and thinks keys were installed. `#1115` already improves its collision messaging; keep that work on this branch.
- `invoke-cyclaw.sh` on **main** still `wait`s only the harness PID, so a gate that dies after ready is silent until Ctrl+C. That is **#1114**, already stacked under this PR. Do not re-implement it here. `#1114` must stay on stock bash 3.2 (`wait "$PID"` after `kill -0`, never `wait -n`).

## Suggested implementation order

Keep this as installer-stack work. Separate commits, same branch or a follow-up on top of `#1115` after `#1114` lands.

1. EXIT trap on `_keychain_store_value` / `_copy_key` temps (same pattern as `#1032`). Lowest risk.
2. `uninstall-cyclaw.sh --remove-keychain` for the five known service names. Highest operator value.
3. Shim-side `source ~/.CyClaw/.env` with the existing xtrace-refuse guard.
4. Rotate job refuses if the gateway is up.
5. README sentences for the triple-copy and `setup-cyclaw.sh` identity.

## Out of scope

- Signed Keychain helper binary.
- Changing Keychain service names (launchd generators and tests pin them).
- Flipping `auth.enabled` / `api.tls.enabled`.
- Loading LaunchAgents from setup scripts.
- Anything in `static/terminal.js` / `static/harness.html` / `gate.py`.

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
