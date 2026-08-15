# macOS Integration Gaps — Verification + LaunchdScheduler Plan

**Date:** 2026-08-14
**Verified against:** `origin/main` @ `8e7b540a01550a7a238af45fefd7a01ea65bf590`
**Status:** Verification complete; `LaunchdScheduler` implemented and tested
(see "Implementation" and "Testing performed" below) — full suite green in a
Linux sandbox; real-macOS `launchctl` behavior remains unverified by
construction (see "NOT run" below). All five follow-up items in "Next
integrations" below have since shipped as four separate draft PRs
(#909-#912).

**Post-merge (`origin/main` @ `e8bbc29`, 2026-08-15):** #911 shipped
`macos/cyclaw-keychain-env.sh` / `cyclaw-keychain-set.sh` — the "No Keychain
helper" row in "Verified gap" below is the original #908-era snapshot, not
current code. `uninstall-cyclaw.sh` now also best-effort bootouts the three
landed generated labels (telegram-poll / telegram-health / fsconnect-trash);
it still does not load LaunchAgents and still does not touch a gate/harness
agent.

## Method

Every claim below was checked against the actual file/line in a fresh clone of
`origin/main` (fetched at the commit pinned above), not against memory or the
docs alone. Where code and docs disagreed, code is cited as the deciding
evidence per `CLAUDE.md` §1 ("Code wins"). Nothing here is speculative —
every row has a file:line citation an operator can re-open and re-check.

## Credit: what's actually real

The macOS harness port is a working operator path, not vapor. Confirmed:

- `macos/install-cyclaw.sh` — home layout at `~/.CyClaw`, repo clone/link,
  Python 3.12 venv, dependency install, `cyclaw` shim + PATH/rc wiring.
- `macos/uninstall-cyclaw.sh` — removes the PATH/rc marker blocks atomically,
  preserves symlinked rc files, prompts before deleting `~/.CyClaw` or
  `~/CyClaw-FS`.
- `agentic/fsconnect/pathsafe.py:809,852,940,1028,1080,1194` — POSIX
  `openat`/`O_NOFOLLOW` with a held `dir_fd` on every path-descent hop; macOS
  takes the identical code path as Linux (`docs/HARNESS_MACOS.md:136-142`
  states this explicitly, and the code has no Darwin-specific branch in that
  function — the branch that exists is POSIX-vs-Windows, not
  macOS-vs-Linux).
- `macos/install-cyclaw.sh:166-194` — the Darwin branch installs plain
  `torch==2.13.0` (no `+cpu` suffix, no `--index-url` override) and strips the
  `torch==`/`--extra-index-url` lines from copies of `requirements.txt` /
  `constraints.txt` before installing the rest. `docs/HARNESS_MACOS.md:144-178`
  documents why: Apple Silicon has no separate CPU/CUDA wheel to disambiguate,
  so the `+cpu` local-version pin 404s on macOS. Confirmed independently
  against PyPI per that doc.
- `retrieval/embeddings.py:63` — `EMBED_DEVICE = "cpu"` is hardcoded (not
  platform-conditional), so macOS and Linux produce identical embeddings for
  ranking parity; `embedding_fingerprint()` (same module) detects staleness if
  that ever changes.
- `.github/workflows/ci.yml:265` and `:879` — `macos-latest` (GitHub's Apple
  Silicon arm64 runner) is in both the main test matrix and the
  `deepagents-harness` matrix. `.github/workflows/pip-audit.yml:41` also runs
  its install-verification gate across `[ubuntu-latest, windows-latest,
  macos-latest]`.
- `docs/HARNESS_MACOS.md` exists and is accurate against the code checked
  during this pass (see per-claim citations below — nothing in it was found
  to be false; the one real doc/code mismatch is in a *different* file, see
  "Honesty / I6 gap" below).
- `docs/HARNESS_MACOS.md:129-135` — Keychain access for `git push`/`publish`
  is never touched directly by CyClaw code; it only passes `HOME` through and
  lets `git` resolve whatever `credential.helper` is configured
  (`git-credential-osxkeychain` via `gh auth setup-git`, typically). Confirmed
  no direct Keychain API call anywhere in `agentic/deepagent_github/`.

## Verified gap: scheduler / process supervision

- **No launchd backend for `sync` (docs/CLI claimed it anyway).** Before this
  change, `sync/scheduler.py:394-408`'s `get_scheduler()` mapped
  `"darwin"` to `CronScheduler` — the same class used for Linux. There was no
  `LaunchdScheduler` class anywhere in the file. Yet `sync/cli.py:10` (the
  CLI's own subcommand help text) said `"Register the daily job (cron /
  launchd / Task Scheduler)."`, and `docs/SYNC_README.md:9` said the sync
  wrapper "runs as a separate process (cron / systemd timer / launchd / Task
  Scheduler)." Both true only in the sense that an operator could *manually*
  set one up outside CyClaw's own tooling — nothing in `sync/` generated or
  managed a launchd job. This is the confirmed instance of the "Honesty / I6"
  claim below.
- **No weekly/interval schema.** `sync/config.py:156-157` had exactly two
  scheduling fields, `schedule_hour` and `schedule_min` — a single
  daily fire time. No frequency, weekday, or day-of-month field existed.
- **No generic governed job scheduler reachable from chat.** Confirmed
  correctly absent by design: `sync/`, and now `sync/scheduler.py`'s
  `LaunchdScheduler`, are reachable only via `python -m sync.cli` (verified:
  no `import sync` anywhere under `gate.py`, `graph.py`, or
  `mcp_hybrid_server.py` — the existing I6 AST guard,
  `.claude/skills/invariant-guard/check_invariants.py`, checks this and
  passed both before and after this change).
- **No LaunchAgent for `gate.py` (`:8787`) or the harness (`:8790`).**
  Confirmed: `find macos/LaunchAgents -type f` returns exactly three files —
  `com.cgfixit.cyclaw.fsconnect-trash.plist`,
  `com.cgfixit.cyclaw.telegram-health.plist`,
  `com.cgfixit.cyclaw.telegram-poll.plist`. None starts `gate.py` or
  `harness/server.py`; `com.cgfixit.cyclaw.telegram-health.plist:15` even
  says explicitly in its own header comment: "Does NOT start gate.py." A lid
  close or reboot leaves both servers dead with no supervisor to restart
  them. Still true after this change — out of scope for this PR (see "Next
  integrations").
- **No overlap lock on the cron path.** `sync/scheduler.py:14-22`'s own
  module docstring says so directly: "The cron baseline has no built-in
  single-instance guard, so a wrapper-level lockfile (or systemd) is
  recommended if manual and scheduled runs might collide." (`sync/runner.py`
  does have its own single-instance lock for concurrent `sync` invocations
  regardless of trigger source — that part is not a gap — but the *scheduler
  transport itself* provides no overlap protection the way a systemd timer or
  launchd's own serialized dispatch would.)
- **Installer/uninstaller do not manage LaunchAgents.** Confirmed by reading
  both scripts in full: `macos/install-cyclaw.sh` never references
  `~/Library/LaunchAgents` or `launchctl`. `macos/uninstall-cyclaw.sh` only
  ever touches shell rc marker blocks, `~/.CyClaw`, and `~/CyClaw-FS` — no
  `launchctl`/`LaunchAgents` reference anywhere in the file. Any plist an
  operator hand-loaded from the shipped templates survives an uninstall.
  Still true after this change (see "Next integrations" — wiring
  `uninstall-cyclaw.sh` to call `python -m sync.cli unschedule` is a
  follow-up, not bundled here to keep this PR one reviewable concern).

## Verified gap: secrets / Apple services

- **No Keychain helper for `TELEGRAM_BOT_TOKEN` / `CYCLAW_API_KEY`.**
  Confirmed: no code anywhere imports a macOS Keychain binding (no
  `Security.framework` call, no `keyring` dependency, no `security` CLI
  invocation). `docs/channels/TELEGRAM_DESIGN.md:193` says "route
  `TELEGRAM_BOT_TOKEN` through a Keychain/runtime wrapper ... do not paste
  the token into the plist" — but no wrapper script ships. The word
  "wrapper" appears only as documentation of what an operator *could* build,
  never as a shipped artifact.
- **Token-in-plist is the opposite of the Telegram harden.** Confirmed
  directly: `macos/LaunchAgents/com.cgfixit.cyclaw.telegram-health.plist:38-42`
  and `com.cgfixit.cyclaw.telegram-poll.plist:40-46` both have an
  `EnvironmentVariables` dict with a literal
  `<key>TELEGRAM_BOT_TOKEN</key><string>REPLACE_OR_USE_KEYCHAIN_WRAPPER</string>`
  slot ready to receive a plaintext value. `docs/channels/TELEGRAM_DESIGN.md:150`
  documents "Token never persisted by CyClaw" as the design intent for the
  *server process itself* (env var or no-echo prompt only) — but the shipped
  plist template's own placeholder key structurally invites exactly the
  plaintext-secret-in-a-property-list pattern the design doc warns against
  elsewhere in the same file (`TELEGRAM_DESIGN.md:492`: "Keep the token out
  of plaintext plist values"). This tension is real and unresolved in the
  current templates — a "next integration" (see below), not something this
  PR's `sync`-scoped `LaunchdScheduler` needed to touch, since the `sync` job
  requires no secret at all (Dropbox OAuth lives in rclone's own token store
  under `~/.config/rclone`, never in a CyClaw-managed env var).
- **No `git-credential-osxkeychain` setup in the installer.** Confirmed:
  `macos/install-cyclaw.sh` never calls `gh auth setup-git` or configures
  `credential.helper`. `docs/HARNESS_MACOS.md:129-135` and
  `docs/THREAT_MODEL.md:613` both correctly document this as operator work
  (`gh auth setup-git`), not something the installer does — the doc claim and
  code agree here; there's no dishonesty gap, just a real (documented, not
  hidden) manual step.
- **No `SMAppService` / Login Items.** Confirmed: zero matches for
  `SMAppService` or `Login Item`/`LoginItem` anywhere in the repository.

## Verified gap: platform / hardware

- **Intel Macs unsupported.** `docs/HARNESS_MACOS.md:159-160` states there is
  no `x86_64` macOS wheel for `torch==2.13.0`, confirmed against PyPI per that
  doc's own text (six macOS wheels published, all `macosx_14_0_arm64`).
- **macOS 13 and older unsupported.** Same wheel-tag reasoning
  (`docs/HARNESS_MACOS.md:157-158`): the `macosx_14_0` floor means Sonoma is
  a hard floor, not a soft preference.
- **Embeddings stay CPU-only by design; do not flip to `mps`.**
  `retrieval/embeddings.py:63` hardcodes `EMBED_DEVICE = "cpu"`, and the
  module comment at line 4 explains this is deliberate — cross-platform
  determinism for the index, not an oversight. Ollama itself is unaffected
  and uses Metal normally (Ollama is a separate process CyClaw calls over
  HTTP, not something this repo's device pin touches).
- **GHCR image is `linux/amd64` only.**
  `.github/workflows/publish-ghcr.yml:14,90` — the comment says "linux/amd64
  only for now (torch+cpu multi-arch is a follow-up)" and `platforms:
  linux/amd64` is the only line in the `platforms:` key. Not a native Apple
  Silicon container image.
- **No signed `.pkg` / notarization / Homebrew cask.** Confirmed: zero
  matches anywhere in the repo for `.pkg`, `notariz`, or `brew cask`/"Homebrew
  cask". Distribution is git-clone-and-shell-script only.

## Verified gap: TCC / disk

- **No automated Files and Folders / Full Disk Access request.**
  `docs/HARNESS_MACOS.md:79-83` documents the manual step explicitly: "Grant
  the Terminal/iTerm application that launches CyClaw access under **System
  Settings > Privacy & Security > Files and Folders**. The setup does not
  install a privacy-control profile or request broader machine-wide access."
  This is an honest doc, not a false claim — but the gap (no automation) is
  real.
- **`/Volumes` refused unless explicitly reviewed.** `config.yaml:750` —
  `allow_macos_volume_roots: false  # /Volumes network/removable roots
  require a separate Darwin opt-in`. Confirmed shipped `false`.
- **No iCloud / App Sandbox story.** Confirmed correctly out of scope —
  `docs/HARNESS_MACOS.md` never claims iCloud Drive support; line 57-58
  explicitly lists iCloud Drive among the paths the installer never grants
  access to.

## Verified gap ("Honesty / I6"): docs advertised launchd, code didn't

This is the one claim in the whole list that was a genuine code/doc
mismatch rather than an honestly-documented manual step. Before this change:

- `sync/cli.py:10` (subcommand help text, shown by `python -m sync.cli
  --help`): `"schedule     Register the daily job (cron / launchd / Task
  Scheduler)."`
- `docs/SYNC_README.md:9`: describes the sync wrapper as running via "cron /
  systemd timer / launchd / Task Scheduler."
- `sync/scheduler.py`'s actual `get_scheduler()` factory
  (pre-change, `platform.system().lower() in ("linux", "darwin") ->
  CronScheduler`) had no code path that ever produced a launchd job. An
  operator following the CLI help text's implication would find nothing.

Per the task's own instruction ("Fix the words or implement the class — do
not leave both"), this PR implements the class (`LaunchdScheduler`, see
below) rather than downgrading the docs, since a real Darwin-native scheduler
backend is genuinely useful and was already implied as the intended design
(the module docstring at `sync/scheduler.py:14-22` already discusses launchd
as the macOS-native option, just never built it).

## What NOT to build (explicit non-goal, carried into the implementation)

A chat-driven "schedule whatever weekly" button was explicitly out of scope
and remains out of scope after this change. Persistent LaunchAgents plus
`KeepAlive` plus potential token exposure is a persistence + privilege
surface; `graph.py`'s LLM-facing nodes have and will have zero path to
`sync.scheduler` (verified: I6's AST-based isolation check in
`.claude/skills/invariant-guard/check_invariants.py` covers this and passed
after the implementation below). Any launchd installation stays argv/CLI
only, scoped to the existing tagged jobs, generates plists from real install
paths (no `REPLACE_*` placeholders needing manual editing), fails closed on
unset/invalid config rather than silently defaulting, keeps tokens out of
generated plists, and requires an explicit operator `launchctl bootstrap`
step — installing the plist file never itself loads it into launchd.

## Implementation: `LaunchdScheduler` (this PR)

Added to `sync/scheduler.py`, gated to `platform.system() == "Darwin"`:

- **Selection is opt-in via config, not automatic.** `get_scheduler(cfg)`
  still returns `CronScheduler` for Darwin by default (`scheduler_backend:
  "cron"`, the pre-existing shipped default — zero behavior change for
  existing operators). Setting `sync.scheduler_backend: "launchd"` in
  `config.yaml` selects `LaunchdScheduler` instead; selecting it on
  Linux/Windows raises `SchedulerError` rather than silently falling back to
  cron/schtasks.
- **Frequency: daily / weekly / monthly**, via new `sync.schedule_frequency`
  config key (`"daily"` default, matching the pre-existing single-fire-time
  behavior). `schedule_weekday` (0-7, launchd's own Sunday-is-0-or-7
  convention, default `1` = Monday, matching the existing
  `fsconnect-trash.plist` template's own convention) and `schedule_day`
  (1-31, default `1`) are new fields, each validated in
  `RcloneConfig.__post_init__` and each ignored unless the matching
  frequency is selected. All four fields (`schedule_hour`, `schedule_min`,
  `schedule_weekday`, `schedule_day`) build a `StartCalendarInterval` dict
  written via `plistlib` — no hand-built XML string, so no injection surface
  in the plist body itself.
- **Generated, not templated.** The plist's `ProgramArguments` uses the same
  `_python_executable()` / `_repo_root(cfg)` helpers `CronScheduler` already
  uses — real, resolved paths at generation time, not `REPLACE_*`
  placeholders. `WorkingDirectory` is the real repo root. Log paths are
  `~/Library/Logs/CyClaw/sync.log` under the real invoking user's home
  (`Path.home()`), matching the shipped templates' log-path convention.
  `ProgramArguments` is an argv array launchd execs directly — no shell
  involved, so none of `CronScheduler`'s `shlex`/`%`-escaping is needed or
  present (a structurally simpler, more injection-resistant transport than
  the cron string it complements).
- **No secrets in the file.** The `sync` job needs no token (rclone owns its
  own OAuth state under `~/.config/rclone`, untouched by CyClaw), so the
  generated plist carries no `EnvironmentVariables` key at all — trivially
  satisfying "no token in the file" for this first integration. This was a
  deliberate reason to pick `sync` as the first job to wire, ahead of the
  token-bearing `telegram-health`/`telegram-poll` jobs (see "Next
  integrations").
- **`install()` never calls `launchctl load`/`bootstrap`.** It writes the
  plist to `~/Library/LaunchAgents/com.cgfixit.cyclaw.sync.plist` (creating
  the directory and the log directory if absent) and returns a
  `ScheduleEntry` whose new `note` field carries the exact
  `launchctl bootstrap gui/<uid> <path>` command the operator must run
  themselves. This satisfies the task's explicit "require an explicit
  operator launchctl bootstrap" requirement — the write is reversible and
  inert until a human loads it.
- **`remove()`** best-effort unloads (`launchctl bootout gui/<uid> <path>`,
  tolerating "not loaded" as success) then deletes the plist file. Returns
  `False` with no subprocess call at all when no plist is present.
- **`status()`** returns `None` when no plist file exists; otherwise parses
  the on-disk plist via `plistlib` and best-effort probes `launchctl print`
  for live-loaded state, never raising if `launchctl` itself is unavailable
  (keeps `sync.cli status` non-fatal on a box without launchd, mirroring the
  existing `ImportError` tolerance in `sync/cli.py:362-364`).
- **`sync/cli.py`** prints `entry.note` (when non-empty) after `schedule`
  and `setup --schedule`, so the operator sees the required manual
  `launchctl bootstrap` command directly in the CLI output rather than
  needing to read source or docs to find it.

## Testing performed

This container is Linux (`platform.system() == "Linux"`), not macOS — **no
live `launchctl` call was or could be exercised in this environment.** Per
this repo's own epistemic standard (state what's tested vs. assumed, don't
imply verification that didn't happen), here is the honest split:

**Actually run, in this sandbox, and passing** (Python 3.12.3, a venv built
outside the repo tree per `CLAUDE.md` §4's documented trap-avoidance —
`download.pytorch.org` is blocked by this session's network policy, so
`torch==2.13.0` was installed from default PyPI instead of the `+cpu`-pinned
index for this local verification run only; `requirements.txt` /
`constraints.txt` in the repo are untouched):

- `GROK_API_KEY=dummy pytest tests/test_sync_scheduler.py tests/test_launchd_scheduler.py -q --tb=short`
  — 65 passed. Every pre-existing `CronScheduler`/`WindowsTaskScheduler` test
  passes unmodified (zero behavior change for the default path), plus 37 new
  `LaunchdScheduler` tests covering: plist structure (`plistlib.loads()`
  round-trip), correct `StartCalendarInterval` shape for each of
  daily/weekly/monthly, absence of any `EnvironmentVariables` key or secret
  substring, `RunAtLoad: False`, the `note` field's bootstrap-command text,
  idempotent overwrite (no leftover temp file), `get_scheduler()` backend
  selection and its platform guard (`launchd` on non-Darwin raises
  `SchedulerError`), `remove()`/`status()`/`install()` on non-Darwin raising
  before touching any path, `remove()` on a missing plist doing zero
  subprocess calls, `install()` doing zero subprocess calls (confirming the
  "never auto-loads" requirement in code, not just in a docstring), and
  `status()` reading the schedule from the on-disk plist rather than a
  possibly-drifted in-memory config. All `subprocess.run` /
  `platform.system` / `Path.home` boundaries are mocked — no real
  `launchctl`, no real `~/Library/LaunchAgents` touched, matching
  `tests/test_sync_scheduler.py`'s own existing pattern
  (`--noconftest`-runnable, no live service).
- `ruff check --select E,F,I,B,C4,UP,S .` (whole repo, not just the touched
  files) — clean, zero findings.
- `python3 .claude/skills/invariant-guard/check_invariants.py` — 35/35
  checks pass (all six invariants + five supporting guards); I6 (module
  isolation) specifically re-checked since this touches `sync/`.
- `python3 .claude/skills/config-guard/check_config.py` — 0 failures (the one
  pre-existing warning, C9 on `app.mode: hybrid` + armed providers, is
  unrelated to this change — see `CLAUDE.md`'s load-bearing-numbers table).
- `python3 .claude/skills/doc-sync/doc_sync.py` — 0 drift items found.
- Full suite: `GROK_API_KEY=dummy pytest tests/ -q --tb=short` — exit code 0,
  every test file passed with zero failures/errors; the only non-pass
  results were pre-existing, environment-gated `SKIPPED`s unrelated to this
  change (Windows-only branches on this Linux host, `test_fsconnect_macos_real.py`'s
  real-Darwin-hardware checks, optional `langchain_xai`/`deepagents` extras
  not installed, `CYCLAW_DB_URL` not pointed at a live Postgres).
- CI-style coverage scoped to the three touched modules
  (`pytest tests/ --cov=sync.config --cov=sync.scheduler --cov=sync.cli
  --cov-report=term-missing`, mirroring `ci.yml`'s existing `--cov=sync.scheduler`
  /`--cov=sync.config`/`--cov=sync.cli` flags): **90.14% combined** (well
  above the 80% `pyproject.toml` gate) — `sync/config.py` 97%, `sync/scheduler.py`
  89%, `sync/cli.py` 85%. Every line `coverage` reported missing in
  `sync/scheduler.py` is pre-existing `WindowsTaskScheduler`/fallback-branch
  code outside this change (verified by line-range: `LaunchdScheduler` spans
  lines 368-526; every reported miss falls in `CronScheduler`'s
  rarely-hit fallback or `WindowsTaskScheduler`, both untouched here) — the
  new `LaunchdScheduler` class itself has no uncovered line.

**NOT run — genuinely untestable outside real macOS, stated plainly rather
than implied:**
- An actual `launchctl bootstrap gui/<uid> <plist>` load, confirming the job
  fires at the scheduled `StartCalendarInterval` and that `launchctl print`
  reports it loaded.
- `launchctl bootout` actually unloading a live-loaded job (the `remove()`
  code path was exercised only against a mocked `subprocess.run`).
- Real Full Disk Access / TCC prompt behavior when the generated plist's
  `ProgramArguments` invoke a Python process from a LaunchAgent context
  (launchd-invoked processes can have different TCC prompting behavior than
  an interactive Terminal session — unverified here).
- Log rotation / `StandardOutPath` append behavior across multiple real
  firings.

An operator with real macOS hardware should verify the four items above
before relying on this in production; they are exactly the reason `install()`
does not auto-bootstrap.

## Next integrations (suggested, in rough priority order)

All five items below shipped as four follow-up draft PRs, each cut
independently from `origin/main` (not stacked on this PR or on each other,
and each duplicating the shared `utils/launchd_plist.py` helper + Keychain
scripts rather than branching from one another) to keep them
independently reviewable and mergeable with zero cross-branch conflict
risk. Every one of them is still Darwin-only, still only *generates* a
plist (never calls `launchctl load`/`bootstrap` itself), and was verified
in the same Linux sandbox limitation this PR documents — real-macOS
`launchctl` behavior remains unverified by construction for all four.

1. **`uninstall-cyclaw.sh` wired to `python -m sync.cli unschedule`** —
   shipped in
   [PR #909](https://github.com/cgfixit/CyClaw/pull/909)
   (`claude/macos-uninstall-unschedule`). Adds a best-effort
   `unschedule_sync_job()` step, run first (before any `--remove-home`
   deletion), that calls `sync.cli unschedule` against
   `~/.CyClaw/repo/config.yaml` when present; any failure prints a
   `WARNING` and uninstall proceeds rather than aborting.
2. **`fsconnect-trash` migrated off its static template** — shipped in
   [PR #910](https://github.com/cgfixit/CyClaw/pull/910)
   (`claude/macos-fsconnect-trash-launchd`). Adds
   `python -m agentic.fsconnect.cli trash-empty-plist`, which writes the
   weekly `StartCalendarInterval` plist from real resolved paths (no
   `REPLACE_*` placeholders). Also introduces the shared
   `utils/launchd_plist.py` helper (write/bootout/probe a plist atomically)
   that PRs #911 and #912 below both reuse.
3. **A Keychain runtime wrapper** for `TELEGRAM_BOT_TOKEN` /
   `CYCLAW_API_KEY`, plus **`telegram-health` / `telegram-poll` on the same
   generated-plist mechanism** — shipped together in
   [PR #911](https://github.com/cgfixit/CyClaw/pull/911)
   (`claude/macos-telegram-launchd-keychain`), exactly as this doc
   recommended (secrets wrapper first, token-bearing jobs second, not the
   reverse). `macos/cyclaw-keychain-env.sh` fetches one secret via
   `security find-generic-password` and `exec`s the wrapped command,
   composable for chaining more than one secret; `cyclaw-keychain-set.sh`
   stores one with a no-echo prompt. `python -m telegram.cli poll-plist`
   (`KeepAlive`) and `health-plist` (`StartInterval`) generate plists that
   inject the token through the wrapper — no plaintext token ever reaches
   a plist. Replaces the `REPLACE_OR_USE_KEYCHAIN_WRAPPER` placeholder
   tension in the shipped static templates with something that actually
   ships.
4. **A supervised LaunchAgent for `gate.py`/`harness/server.py`** — shipped
   in [PR #912](https://github.com/cgfixit/CyClaw/pull/912)
   (`claude/macos-gate-harness-launchagent`), deliberately gated more
   tightly than #909-#911 given the risk framing below: `macos/generate_service_plist.py`
   refuses to write anything without both `--confirm` and a non-empty
   `--reason` (mirroring `utils/personality.py`'s soul-mutation gate),
   uses `KeepAlive: {"SuccessfulExit": false}` (restart only on crash, never
   after a clean `launchctl stop`/`bootout` — verified against both
   `gate.py`'s and `harness/server.py`'s actual `uvicorn.run()` shutdown
   behavior before choosing this, not assumed), and defaults
   `ThrottleInterval` to 30s. This was and remains the highest-value but
   highest-risk item: an always-running network listener restarted by
   launchd on crash/reboot is a materially different security posture than
   "runs only while a terminal is open." The PR itself flags it as the one
   integration in this series most worth a design read (not just a diff
   read) before merging.
