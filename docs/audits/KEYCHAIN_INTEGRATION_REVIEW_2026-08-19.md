# Keychain Integration Review — CyClaw vs. mcporter, 2026-08-19

Comparative review of CyClaw's macOS Keychain secret handling against
`openclaw/mcporter`, requested after the v0.8.0 release announcement. Sources:
CyClaw at `e8bea4d`; mcporter cloned at `60dfe26` (package version `0.13.7`),
with tag `v0.8.0` fetched separately for the version the request named.

---

## Premise correction: mcporter has no OS-keychain secret storage

The comparison as posed does not have a counterpart on the mcporter side.
mcporter does not put secrets in the macOS Keychain, at `v0.8.0` or at
`0.13.7`. A case-insensitive search for `keychain`, `keytar`, `libsecret`,
`security find-generic-password`, and `wincred` across `src/`, `scripts/`, and
`docs/` returns, at `v0.8.0`, nothing at all, and at `0.13.7` only four hits,
all in release engineering:

- `scripts/codesign-native.sh` passes `--keychain-profile` / `--keychain` to
  `notarytool`.
- `docs/RELEASE.md` documents `NOTARYTOOL_KEYCHAIN_PROFILE` and the managed
  `openclaw-developer-id-release.keychain-db` used for Developer ID signing.

That is a build-time signing keychain, not a runtime secret store. The
v0.8.0 release notes are about OAuth error handling, JSON output on fallback
paths, JSONC config parsing, and daemon reliability — no credential-storage
change is listed.

## What mcporter actually does with credentials

mcporter's runtime secret store is a plain JSON file. `getOAuthVaultPath()`
(`src/oauth-vault.ts`) resolves to `<XDG_DATA_HOME>/mcporter/credentials.json`,
falling back to `~/.mcporter/credentials.json`, holding OAuth access and
refresh tokens, dynamic client registrations, PKCE code verifiers, and OAuth
state in cleartext. Confidentiality rests entirely on filesystem permissions:
`DEFAULT_ATOMIC_FILE_MODE = 0o600` in `src/fs-json.ts`, applied at
`fs.writeFile` time with flag `wx` into a same-directory temp file that is then
`rename`d over the target.

Non-OAuth secrets are not stored at all. `src/env.ts` resolves `${VAR}`,
`${VAR:-default}`, and `$env:VAR` placeholders out of `process.env` at
connection time; there is no keychain-reference syntax in the config schema.

So on the storage-backend axis, CyClaw is ahead: a Keychain item is encrypted
at rest and gated by an ACL, and a `~/.CyClaw/.env` at mode 600 is at parity
with mcporter's vault. The interesting comparison is not backend choice — it
is the handling discipline around whichever backend is chosen, and there
mcporter is doing several things CyClaw is not.

## Where mcporter's discipline is stronger

Four techniques in mcporter have no CyClaw counterpart and transfer directly.

**Write-then-verify on every secret file.** `assertSecurePath()` in
`src/chrome-devtools-relay-handoff.ts` re-`lstat`s each file and directory it
just created and throws unless it is a regular file (or directory), is not a
symlink, has `mode & 0o077 == 0`, and has `uid == process.getuid()`. Creation
uses `O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW` with the mode supplied at `open`
time, plus `fchmod` on the descriptor rather than the path. CyClaw's
equivalents (`_keychain_store_value`, `_copy_key`, `_fill_browser` in
`macos/setup-cyclaw-keys.sh`) use `mktemp` then `chmod 600`, which is nearly
equivalent on POSIX because BSD `mktemp` already uses `mkstemp(3)` at 0600 —
but nothing verifies the result, and nothing verifies the destination of the
`mv` in `_env_upsert`.

**A real Windows ACL instead of a `chmod` that cannot express the intent.**
`harness/env_keys.py`'s module docstring concedes that Windows `os.chmod`
cannot express owner-only and falls back to the inherited `%USERPROFILE%` ACL.
mcporter solves exactly that: `createWindowsHandoffDirectory()` shells to the
absolute `System32` PowerShell path (validated as a non-symlink regular file),
builds an explicit protected SDDL descriptor
(`O:<sid>G:<sid>D:P(A;OICI;FA;;;<sid>)`), creates the directory with it, then
reads the ACL back and fails closed unless it is protected, owned by the
current SID, and carries exactly one non-inherited Allow-FullControl rule for
that SID.

**Locked read-modify-write on the credential store.** Every mutation in
`src/oauth-vault.ts` — `saveVaultEntry`, `clearVaultEntry`,
`clearVaultTokensIfMatching`, `reconcileVaultServerUrl` — runs inside
`withFileLock()`, a cross-process lockfile created with flag `wx`, with stale
lock recovery via `isProcessRunning`, a 30 s timeout, and a distinct
`FileLockTimeoutError` so callers can tell contention from I/O failure.

**A deliberate, documented split between fail-open and fail-closed on corrupt
state.** `DirectoryPersistence.readJsonOrUndefined` degrades a corrupt token or
client cache to "no usable credentials → re-authenticate", but `readState()`
deliberately does not use it, with the reason stated inline: returning
`undefined` for a corrupt OAuth state would skip the CSRF check on the
authorization callback. Caches fail open; security-critical state fails closed.

## Finding 1 — a transient Keychain read failure silently regenerates CYCLAW_API_KEY

Highest-severity item, and the direct analogue of mcporter's fail-open /
fail-closed split above.

`_keychain_get()` (`macos/setup-cyclaw-keys.sh:343`) is
`security find-generic-password -a "$ACCOUNT" -s "$1" -w 2>/dev/null || true`.
Both the exit code and stderr are discarded, so every failure mode collapses to
the empty string: item genuinely absent, keychain locked, ACL denied,
`security` missing from PATH, interaction-not-allowed in a headless launchd
context. `_keychain_has()` therefore reports "no item" for all of them.

In the `CYCLAW_API_KEY` branch (`:825-855`), when `_keychain_has` is false,
`~/.CyClaw/.env` is absent (`--no-env-file`, or the operator cleaned it), and
`$CYCLAW_API_KEY` is unset in the environment, the script generates a fresh key
and `_persist`s it — overwriting a perfectly good Keychain item that it simply
could not read. The running `gate.py` keeps the old value (`gate.py:132` reads
`os.environ`, fixed at exec), so the operator now holds a key the server
rejects, with no error explaining why.

The `--schedule-rotate` LaunchAgent makes this reachable unattended: it runs
`--rotate --skip-prompts` from launchd at 04:00, a context where a locked
keychain or an interaction-not-allowed result is exactly the expected failure.

Fix: capture `security`'s exit status and stderr, treat only the
item-not-found status as "absent", and abort with a specific message on
anything else rather than falling through to generation. The item-not-found
exit code should be confirmed on hardware before being hardcoded, per this
repo's existing convention for macOS-CLI behavior (see the VERIFY ON REAL
HARDWARE blocks in both `macos/cyclaw-keychain-*.sh`).

## Finding 2 — TMPDIR is interpolated into a Tcl script

`_keychain_store_file()` (`macos/setup-cyclaw-keys.sh:371-384`) uses an
unquoted heredoc, `/usr/bin/expect <<EXPECT_EOF`, so bash substitutes
`$secret_file`, `$ACCOUNT`, and `$service` into the Tcl source before expect
parses it. `$service` and `$ACCOUNT` are safe today — the former comes from the
hardcoded `KC_*` constants, the latter from `id -un`. `$secret_file` is
`mktemp "${TMPDIR:-/tmp}/cyclaw.kc.XXXXXX"`, and `TMPDIR` is operator-supplied
and never validated.

Tcl performs command substitution on `[...]` inside double-quoted strings, so
`set fh [open "$secret_file" r]` executes anything a `TMPDIR` containing square
brackets carries. `reject_shell_metachars()` is applied to `HOME_DIR`,
`REPO_DIR`, and the installed self-copy, but never to `TMPDIR` — and it screens
for `"`, backtick, `$`, and `\`, not `[` or `]`, so extending it to `TMPDIR`
alone would not close this.

Severity is low: it is local, self-inflicted, and requires an unusual `TMPDIR`.
The fix removes the class rather than filtering for it — quote the heredoc
(`<<'EXPECT_EOF'`) and pass the values through the environment, reading them in
Tcl as `$env(CYCLAW_KC_SECRET_FILE)` and friends. No interpolation, nothing to
screen.

## Finding 3 — unlocked read-modify-write on the dotenv files

`_env_upsert()` (`:279-301`) is a whole-file read-modify-write:
`grep -v '^KEY=' "$file" > "$tmp"`, append the new assignment, `mv` over the
target. It holds no lock. Two concurrent runs each read the pre-state and the
later `mv` wins outright, dropping every key the other run wrote — not just the
contended one.

Concurrency is realistic precisely because of `--schedule-rotate`: the
LaunchAgent fires at 04:00 on the 1st (or Sunday) while nothing prevents an
operator from running `setup-cyclaw-keys.sh` at that moment. `_persist` calls
`_env_upsert` up to four times per secret across two files, widening the window.

mcporter's answer is `withFileLock` around every vault mutation. A shell
equivalent is a `mkdir`-based lock (atomic on POSIX, no `flock(1)` on macOS)
held across the whole `_persist` sequence, with a stale-lock timeout so a
killed run cannot wedge the next one.

## Finding 4 — the multi-store write has no read-back and no divergence detection

`_persist()` (`:418-460`) writes each secret to as many as four places:
Keychain, `$CYCLAW_HOME/.env`, the checkout `.env`, and the current process
environment. When the Keychain write fails it emits `warn` and proceeds to
write the dotenvs anyway (`:430`). The stores then disagree permanently, and
nothing detects it: on the next run `_prompt_secret` reports "already set via
.env" while launchd, which reads only the Keychain (per
`utils/launchd_plist.wrap_with_keychain_secrets`), still resolves the old value
or fails closed.

mcporter's `CompositePersistence` treats this as the central problem —
ordered fallback across stores, a per-store snapshot map, and
`clearRejectedCredentials` clearing the specific rejected generation from every
store that holds it, so no store can resurrect a value another store has
retired.

Two cheap steps close most of the gap without importing that machinery:

1. After `_keychain_store_value` returns success, read the item back with
   `_keychain_get` and compare to the intended value. This costs one extra
   `security` exec (single-digit milliseconds) and catches the failure modes
   the exit code misses — notably an `-U` update that did not take.
2. Add a `--verify` (or `doctor`) mode that reports, for each of the five
   `KC_*` services, whether the Keychain, `$CYCLAW_HOME/.env`, and the checkout
   `.env` agree — comparing SHA-256 digests, never values, consistent with the
   audit-log convention in `utils/logger.py`.

## Finding 5 — `-U` may not replace the ACL on a pre-existing item

`cyclaw-keychain-set.sh:57` runs
`security add-generic-password -a "$ACCOUNT" -s "$SERVICE" -T /usr/bin/security -U -w`.
The script's own header already flags the open question: whether `-U` on an
item created before `-T` was introduced replaces that item's trusted-application
list or leaves the original ACL in place. If it leaves it, operators who stored
secrets with an earlier version of this script still have the default
creator-trust-plus-interactive-prompt behavior that `-T` was added to replace,
and nothing in the current flow tells them so.

The deterministic alternative is `security delete-generic-password` followed by
a fresh `add-generic-password` without `-U`, which always produces a known ACL —
at the cost of a window where the secret exists nowhere if the add fails. Given
that every value here is either regenerable (`CYCLAW_API_KEY`) or
re-pasteable, that tradeoff is probably acceptable, but it is a behavior change
and should be the operator's call.

Lower-cost first step: have `--verify` from Finding 4 also dump the stored
item's ACL (`security dump-keychain -a` is not needed; `security
find-generic-password -g` plus Keychain Access inspection suffices) so drift is
at least visible.

## Finding 6 — the keychain search list is not pinned

Both `cyclaw-keychain-env.sh:74` and `_keychain_get` call
`security find-generic-password -a "$ACCOUNT" -s "$SERVICE" -w` with no
keychain argument, so the lookup walks the user's default keychain search list.
An item with a matching service and account in any keychain earlier in that
list shadows the intended one. The `com.cgfixit.cyclaw.*` reverse-DNS service
names make an accidental collision very unlikely, but a deliberate one is not
defended against at all.

Passing the login keychain explicitly as the trailing positional argument
(`... -w "$HOME/Library/Keychains/login.keychain-db"`) removes the ambiguity.
This is a one-token change with no latency cost, and it is the closest
CyClaw analogue to mcporter's `vaultKeyForDefinition()` — a SHA-256 over name,
URL, and command that makes a credential key collision-resistant and
explicitly bound to the identity it was issued for.

## Finding 7 — no timeout on either `security` invocation

Neither `cyclaw-keychain-env.sh` nor the `expect` block in
`setup-cyclaw-keys.sh` bounds how long `security` may take. The expect script
sets `set timeout 30`, but its `expect` block has no `timeout` branch, so on
expiry control falls through to `catch wait result`, which blocks until the
child exits — indefinitely if `security` is sitting on a prompt nothing will
answer. In the `--schedule-rotate` LaunchAgent that wedges the job rather than
failing it.

macOS ships no `timeout(1)`, so the shell fix is a background watchdog, which
is more machinery than the risk warrants for the read path. The expect path is
worth fixing directly: add a `timeout { catch { exec kill [exp_pid] }; exit 1 }`
branch. Also note `exit [lindex $result 3]` is only an exit status when the
child exited normally; on an error result that index holds an errno instead.

## Finding 8 — minor secret-hygiene items

- `_copy_key` (`:631`) and `_fill_browser` (`:675`) set `umask 077` after
  `mktemp` and never restore it, unlike `_env_upsert` and
  `_keychain_store_value`, which save and restore. Harmless today because both
  run near the end, but it is an inconsistency that will bite whoever adds code
  after them.
- The `--clipboard-ttl` watchdog (`:640-650`) backgrounds a subshell that holds
  `$_api_value` in memory for the full TTL (default 90 s) in a process detached
  from the script. Small window, worth a comment noting it is deliberate.
- `_fill_browser` writes the AppleScript to a temp file and passes the secret's
  *path*, not its value — the same pattern mcporter uses for its relay handoff.
  That is the strongest single piece of secret handling in the script and
  should be called out as the house pattern rather than left as an
  implementation detail.

## What CyClaw already does better than mcporter

Worth recording so none of it is "simplified" away later.

- **The secret never reaches argv.** `cyclaw-keychain-set.sh`'s bare `-w`
  makes `security` prompt via `readpassphrase(3)`, and `_keychain_store_file`
  routes generated values through a 0600 file into expect's spawn rather than a
  command line. mcporter never needed this — its tokens arrive over HTTP — but
  it is the correct discipline and it is well documented.
- **Encrypted at rest with an ACL.** A Keychain item beats mcporter's
  mode-0600 JSON on any threat model that includes offline disk access.
- **Fail-closed launch wrapper.** `cyclaw-keychain-env.sh` exits 1 without
  `exec`ing when the item is missing or empty, so a job never starts with the
  variable silently unset. It also validates the env-var name against
  `^[A-Za-z_][A-Za-z0-9_]*$` before touching the Keychain.
- **Honest documentation of an unverified claim.** The ACL-attribution comment
  in `cyclaw-keychain-env.sh:28-55` records that the trust grant goes to
  `/usr/bin/security` rather than the script, states that a narrow ACL is not
  achievable without a signed helper binary, and marks the whole thing VERIFY
  ON REAL HARDWARE. That is better epistemic practice than most of what is in
  mcporter's comments.
- **Composable single-purpose wrapper.** One secret per layer, chained via
  `exec`, is simpler and easier to audit than a persistence-store class
  hierarchy, and it fits the threat model.

## Non-finding: latency

There is no meaningful latency problem to fix. The read path costs one
`security` exec per secret per process start — single-digit milliseconds, paid
once at launchd job start, never in a request path. Chaining N secrets costs N
bash plus N `security` processes, still well under a tenth of a second for any
realistic N. mcporter's `Promise.all` fan-out across stores and its cached
per-call vault snapshot exist because it reads credentials on every MCP
connection, which CyClaw does not. Do not add caching here; it would trade a
measurable nothing for a stale-secret failure mode.

## Recommended order

Findings 1, 3, and 4 are the ones that can cost an operator a working
deployment; 2, 6, and 7 are cheap and remove whole classes rather than
instances; 5 needs a decision before any code changes.

| # | Finding | Tier (CLAUDE.md §7) | Notes |
|---|---|---|---|
| 1 | Transient read failure regenerates the API key | High | Touches secret handling; needs approval |
| 3 | Unlocked dotenv read-modify-write | High | Same |
| 4 | No read-back / divergence detection | High | Same |
| 2 | TMPDIR interpolated into Tcl | High | Same |
| 6 | Keychain search list not pinned | High | Same |
| 7 | No timeout on the expect path | Medium | Robustness only |
| 5 | `-U` ACL replacement semantics | High | Blocked on hardware verification |
| 8 | Hygiene / comments | Low | Safe to batch with any of the above |

Every code-touching item lands in `macos/setup-cyclaw-keys.sh` and
`macos/cyclaw-keychain-*.sh`, which are secret-handling paths and therefore
High tier under CLAUDE.md §7 — explicit approval first. Findings 1 and 5 both
need on-hardware verification of `security(1)` exit codes and `-U` ACL
behavior before anything is hardcoded, matching the VERIFY ON REAL HARDWARE
convention already established in those files.

Existing coverage to extend rather than duplicate:
`tests/test_cyclaw_keychain_scripts.py` (12 tests, with a fake `security` on
PATH) and `tests/test_setup_cyclaw_keys.py` (20 tests). The fake-`security`
fixture is the natural place to add exit-code cases for Finding 1.
