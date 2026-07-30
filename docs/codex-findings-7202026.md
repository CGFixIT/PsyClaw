# Codex Findings: sync, agentic, and guardrails

Review date: 2026-07-20
Current reviewed snapshot: [origin/main at 56fa237](https://github.com/CGFixIT/CyClaw/commit/56fa2377d1447e9b325b5c0edb6875a2f3e9db61)

## Resolution status (as of 2026-07-30)

This section is a status addendum, not a rewrite of the original findings below
-- it records what a 2026-07-30 `/CyClaw-Optimize` pass confirmed by reading the
CURRENT code on `main` (per `CLAUDE.md`'s authority order, code wins over docs),
not by trusting this document's age or any PR title. Every RESOLVED line cites
the exact function and an in-code comment confirming the fix is intentional
(most reference "codex P1"/"codex P2"/"codex #593"/"codex #594" directly).

**All four P1s are resolved:**

- Windows fsconnect writes escape root -> **RESOLVED**. `agentic/fsconnect/writer.py`'s
  `_writes_refused_platform()` now hard-refuses writes on Windows, enforced in
  both `FsWriter.__init__` and `_executable` (comment cites "codex #593 P1").
  Construction on Windows, and a POSIX-constructed writer later invoked on
  Windows, are both covered.
- Fsconnect indexing retains deleted files -> **RESOLVED**. `agentic/fsconnect/indexer.py`'s
  `FsIndexer.apply()` now calls `_prune_staging()` after building the current
  manifest (comment cites "codex P1"), removing staged copies whose source
  disappeared. Pruning is ownership-bounded to the prior run's own manifest
  cache, so a file CyClaw never staged is never touched.
- Sync retries can hide corpus changes / lose the terminal audit event ->
  **RESOLVED** (both halves). `sync/runner.py`'s `_run_sync_locked()` now
  accumulates `events_by_path` across retry attempts, keyed by path with the
  final attempt's operation winning (comment cites "codex #594 P2"), so a
  transient copy-then-fail attempt is not lost by a clean retry. The
  audit-convergence gap is closed too: the `except` path now emits a
  schema-aligned `sync_failed` event preserving the harvested change evidence
  before re-raising (comment cites "codex #594 P1").
- Enabled live NeMo rails ignore advertised config -> **RESOLVED**.
  `guardrails/integration.py`'s `_apply_guardrails_config()` now overrides the
  static NeMo directory's engine/model/base_url from `GuardrailsConfig`
  (comment cites "codex P1" and names the exact LM-Studio-vs-Ollama mismatch
  this closes). The static `guardrails/config/config.yml` template itself was
  also updated to the current Ollama endpoint (`127.0.0.1:11434`) as its
  fallback default.

**Six of the eight P2 findings are resolved:**

- `/ops/sync` wall-clock budget omits the post-sync check -> **RESOLVED**.
  `utils/ops_runner.py`'s `_sync_timeout_sec()` now doubles the shim's budget
  (`multiplier = 2 if post_sync_check else 1`) to cover the full lock-held
  lifecycle.
- Sync master gate fails open for quoted booleans -> **RESOLVED**.
  `sync/config.py`'s `load_sync_config()` now raises `SyncConfigError` if any
  safety-boolean field is not a real YAML `bool`.
- Scheduler derives the wrong repo root for nested `local_path` values ->
  **RESOLVED**. `sync/scheduler.py`'s `_repo_root()` now prefers the canonical
  `cfg.repo_root` carried on the config object, falling back to the old
  depth-based derivation only when it is unavailable (comment cites "codex
  finding").
- Fsconnect safety booleans are not type-validated -> **RESOLVED**.
  `agentic/fsconnect/config.py` now validates every entry in `_BOOL_FIELDS`
  (`writes_enabled`, `allow_hard_delete`, etc.) is an actual `bool` in
  `__post_init__`, raising `FsConnectConfigError` otherwise.
- Trash deletion is not recoverably atomic -> **RESOLVED**.
  `agentic/fsconnect/writer.py`'s `_to_trash()` now writes the sidecar
  metadata BEFORE moving the payload, and rolls the sidecar back if the move
  fails -- the ordering the original finding asked for.
- MSSQL does not receive an enforced read-only session -> **RESOLVED**.
  `agentic/sqlconnect/client.py`'s `_enforce_read_only()` now fails closed:
  it tries psycopg's `read_only` property, falls back to pyodbc's
  `SQL_ATTR_ACCESS_MODE` / `SQL_MODE_READ_ONLY`, and raises
  `SqlConnectRuntimeError` if neither mechanism is available. (The
  lock-escalation hints -- `updlock`/`holdlock`/etc. -- were already blocked by
  `_FORBIDDEN_RE` at the time of the original review.)

Two P2s were not re-verified with the same rigor in this pass (spot-checked
via docstring/comment only, not full call-path tracing) -- treat as **likely
resolved, not confirmed**:

- `safe_generate` does not fully degrade on live-provider failure -- a
  catch-all `except Exception` around `generate_async` now exists in
  `guardrails/integration.py::safe_generate` and its comment cites "codex P2",
  redacting to the exception type name before degrading.
- `guardrails.cli check` is advertised as offline but calls `safe_generate` --
  `guardrails/cli.py`'s module docstring now explicitly documents that `check`
  performs a live generation when guardrails+NeMo are both enabled, otherwise
  falling back to the offline heuristic floor. This resolves it via honest
  documentation (one of the two directions the original finding suggested),
  not by renaming the subcommand.

**The three P3 items were NOT re-checked in this pass** (`sync.include_soul`
dead/misleading opt-in, `--config` partial propagation, trash entry name
collision) -- their status as of 2026-07-30 is unknown; do not assume either
way without checking the current code first.

**New finding surfaced by the same 2026-07-30 pass, adjacent to but distinct
from the MSSQL read-only fix above:** as of this addendum,
`agentic/sqlconnect/client.py`'s `_FORBIDDEN_RE`/`_FORBIDDEN_FN_RE` do not yet
guard the ad-hoc-query surface (`OPENROWSET`/`OPENQUERY`/`OPENDATASOURCE`) that
MSSQL exposes independently of the read-only-session fix above -- a single
valid read-only `SELECT` can still reach the DB host's filesystem or an
internal network address through these T-SQL row-set functions. This is
tracked as a separate, freshly opened fix rather than folded into this status
update; check `agentic/sqlconnect/client.py`'s `_FORBIDDEN_RE` directly for its
current state rather than assuming this note is still accurate.

## Scope and method

This was a source-and-test review of:

- sync/, including its CLI, scheduler, configuration, rclone runner, tests,
  docs, and the /ops/sync subprocess shim.
- agentic/, including the GitHub-context layer, Deep Agent and harness
  components, fsconnect, sqlconnect, tests, and out-of-band boundaries.
- guardrails/, its configuration, live NeMo integration, graph bridge, tests,
  docs, and direct gateway wiring.

The initial source review used commit ce07a1407319a5c04117edd76bb65a0fc44c0046.
Before publishing this report, origin/main advanced to 56fa237. The intervening
range changes only two .claude sandbox-guidance files; all reviewed runtime code,
configuration, tests, and docs are byte-identical. Findings below therefore
apply to the current snapshot.

No code was changed and no live rclone, database, LM Studio/Ollama, or NeMo
integration was executed. Priorities describe impact if the relevant
disabled-by-default optional feature is enabled; they do not claim that the
default core request path is presently exposed.

## Findings

### P1 - Windows fsconnect writes can escape the configured root

The Windows fallback validates and reparse-checks a path by name, then later
opens, writes, moves, or deletes by name. A same-host actor that can replace an
in-root path component with an NTFS junction between validation and use can
redirect an enabled write outside the configured root.

- [pathsafe.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/fsconnect/pathsafe.py#L21-L29)
  claims that Windows reasserts containment from an open handle, but
  [_win_resolve](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/fsconnect/pathsafe.py#L626-L646)
  returns a pathname and the later write paths use write_bytes, open, and
  os.replace by name.
- The project's security checklist says Windows write enablement is refused, yet
  the configuration and writer gates permit it when writes_enabled is true.
  See the [checklist](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/docs/agentic/FSCONNECT_SECURITY_REVIEW_CHECKLIST.md#L32-L37)
  and [FsWriter._executable](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/fsconnect/writer.py#L129-L139).
- The POSIX implementation is materially stronger because it retains directory
  file descriptors and descends with no-follow operations. Windows branches are
  excluded from the current test coverage.

Recommended direction: hard-refuse writes on Windows now, add a Windows-negative
test, and only enable them after a handle-based containment design is implemented
and verified.

### P1 - Fsconnect indexing retains deleted files in the retrievable corpus

FsIndexer.apply copies every current source file into the staging corpus but never
removes a staged file that disappeared from the source root. A subsequent reindex
can therefore continue serving deleted content.

- [FsIndexer.apply](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/fsconnect/indexer.py#L177-L220)
  stages current files only.
- The [retrieval indexer](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/retrieval/indexer.py#L58-L148)
  recursively ingests the staged corpus.
- The existing deletion test verifies only skip-cache pruning, not removal of the
  staged copy: [test_incremental_cache_self_prunes_deleted_file](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/tests/test_fsconnect_indexer.py#L236-L247).

Recommended direction: compare the current manifest to the staging tree and
delete obsolete staged files before reindexing; add a regression that proves a
deleted source file is absent from both staging and retrieval results.

### P1 - Sync retries can hide corpus changes and suppress reindexing

When sync_retries is greater than zero, each retry truncates the rclone log and
final corpus_changed is inferred solely from the last attempt. If a transient
attempt copied a file before failing, a successful retry can see the file already
present, emit no file event, and return exit code 0 instead of requesting a
reindex.

- The retry loop truncates the log before each attempt in
  [sync/runner.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/runner.py#L766-L789).
- Final change detection and reindex exit selection are based on the final log
  only: [sync/runner.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/runner.py#L852-L912).
- The retry test intentionally models an empty failed attempt and misses the
  partial-copy-then-clean-retry case.

Recommended direction: retain a cumulative changed flag or cumulative
change-events across retries while keeping transient error output separate.

### P1 - Enabled live NeMo rails do not use the advertised configuration or retrieval context

The live Guardrails path has two coupled activation blockers:

1. [get_cyclaw_guardrails](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/guardrails/integration.py#L88-L101)
   loads only the static NeMo directory. It does not apply GuardrailsConfig
   engine, base URL, model, rail lists, or threshold. The static
   [config.yml](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/guardrails/config/config.yml#L14-L34)
   still names the retired LM Studio endpoint at 127.0.0.1:1234, while the
   current top-level configuration targets Ollama at 127.0.0.1:11434.
2. [safe_generate](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/guardrails/integration.py#L227-L244)
   passes retrieved text as a system message, while the grounding action reads
   only context["relevant_chunks"]. The configured output rail refuses a score
   below 0.18, and the local score returns 0.0 for a non-empty answer without
   context. See [rails.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/guardrails/rails.py#L106-L124)
   and [rails.co](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/guardrails/config/rails.co#L106-L113).

NVIDIA's NeMo documentation describes relevant_chunks as action/message context,
not data inferred from an arbitrary system string:
[action parameters](https://docs.nvidia.com/nemo/guardrails/configure-guardrails/actions/action-parameters)
and [message context](https://docs.nvidia.com/nemo/guardrails/latest/run-rails/using-python-apis/check-messages.html).

Recommended direction: make one configuration source authoritative, pass
retrieved chunks through NeMo's supported context mechanism, and add a real
NeMo/Colang integration test. The current fake-engine tests do not exercise that
contract.

### P2 - Other sync correctness and operability gaps

- **The /ops/sync wall-clock budget omits an enabled post-sync check.**
  The shim allows sync_timeout_sec plus 60 seconds, while the runner can
  legitimately use one full timeout for sync and another for rclone check.
  See [utils/ops_runner.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/utils/ops_runner.py#L136-L152)
  and [the post-sync check path](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/runner.py#L368-L388).
  Derive the shim timeout from the complete lock-held lifecycle.
- **The sync master gate fails open for quoted YAML booleans or a typo.**
  bool("false") is true and unknown fields are collected but not surfaced by the
  sync command. See [sync/config.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/config.py#L372-L394).
  Strictly validate safety booleans and fail closed on unknown keys.
- **The scheduler derives its repository root from local_path.** Nested paths
  below data/corpus are valid, but the fixed two-parent calculation then resolves
  to repo/data rather than the repository. See
  [sync/config.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/config.py#L227-L238)
  and [sync/scheduler.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/scheduler.py#L64-L96).
  Carry the canonical config/repository path into scheduler generation instead.
- **Failures after sync_started do not always emit a terminal audit record.**
  Timeout, exhausted-budget, and post-check exceptions can bypass the final
  summary event. See
  [the start event](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/runner.py#L747-L755)
  and [exception/summary paths](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/runner.py#L779-L895).
  Emit a sanitized sync_failed event before re-raising.
- **sync --dry-run has support-file side effects despite documentation saying that
  nothing changes.** It creates lock/log directories, writes filters and audit
  records, and truncates the rclone log. See
  [the setup and run path](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/runner.py#L713-L789).
  Either make it non-mutating or document the narrower promise: no corpus or
  remote changes.

### P2 - Other agentic connector gaps

- **Safety-critical fsconnect booleans are not type-validated.** Quoted
  writes_enabled: "false" and allow_hard_delete: "false" are truthy and can
  enable execution or purge. See
  [fsconnect/config.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/fsconnect/config.py#L73-L119)
  and [fsconnect/writer.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/fsconnect/writer.py#L129-L139).
  Require actual booleans for every execution or deletion gate.
- **Trash deletion is not recoverably atomic.** The payload moves before the
  sidecar metadata is written; a crash or sidecar failure leaves an undiscoverable
  payload. See
  [writer.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/fsconnect/writer.py#L582-L597)
  and [trash.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/fsconnect/trash.py#L126-L154).
  Use a recoverable transaction/rollback strategy and a repair scan.
- **MSSQL does not receive an enforced read-only session.** The pyodbc path
  silently treats conn.read_only as optional, and the SQL filter permits
  lock-taking SELECT hints. This is an availability and defense-in-depth gap,
  not a demonstrated DML bypass. See
  [sqlconnect/client.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/sqlconnect/client.py#L38-L93)
  and [the connection path](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/sqlconnect/client.py#L241-L329).
  Enforce a least-privilege read-only database principal, explicitly reject
  dangerous locking hints, and test against a real pyodbc/MSSQL fixture.
  Psycopg's transaction read-only behavior does not establish an equivalent
  pyodbc/MSSQL control; see the
  [Psycopg transaction documentation](https://www.psycopg.org/psycopg3/docs/basic/transactions.html)
  and Microsoft's description of
  [ApplicationIntent](https://learn.microsoft.com/en-us/dotnet/framework/data/adonet/sql/sqlclient-support-for-high-availability-disaster-recovery).

### P2 - Other live-guardrails contract gaps

- **safe_generate does not fully degrade on live-provider failure.** Its comment
  promises graceful degradation, but it catches only dependency and rails-load
  errors, not errors raised by generate_async. See
  [guardrails/integration.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/guardrails/integration.py#L227-L241).
  Catch and redact bounded provider/runtime failures or document a fail-closed
  contract.
- **guardrails.cli check is advertised as offline but calls safe_generate.**
  When the feature and NeMo dependency are enabled, it performs a live
  generation. See
  [guardrails/cli.py](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/guardrails/cli.py#L1-L8)
  and [the command implementation](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/guardrails/cli.py#L86-L94).
  Either use the offline check_input primitive or rename and document the
  command honestly.

### P3 - Documentation and cleanup items

- sync.include_soul is a dead/misleading opt-in: the sync target is constrained
  to data/corpus, while the relaxed filter concerns relative data/personality;
  it cannot reach the real sibling soul file. See
  [path validation](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/config.py#L216-L238),
  [rclone destination selection](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/runner.py#L471-L485),
  and [the filter rule](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/filters.py#L33-L35).
- --config is only partially honoured by scheduling, auto-reindexing, and audit
  logging; use one propagated config identity for all spawned work. The
  [scheduled command](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/sync/scheduler.py#L79-L120)
  omits the alternate config path.
- Trash entry names can collide when the same path is deleted twice in the same
  second because the alleged disambiguator is deterministic. See
  [trash.entry_name](https://github.com/CGFixIT/CyClaw/blob/56fa2377d1447e9b325b5c0edb6875a2f3e9db61/agentic/fsconnect/trash.py#L46-L57).
- Several NeMo documents still describe a pre-Phase-2, LM Studio-only skeleton
  rather than the current optional bridge and Ollama configuration.

## Confirmed boundaries and non-findings

- Core request-path isolation is preserved. The gateway uses the out-of-band
  subprocess shim for sync and agentic operations; /ops/fsconnect and
  /ops/sqlconnect expose read-only operations only.
- The generic agentic GitHub writer is deliberately hard-disabled. No active
  generic GitHub write path was found.
- The POSIX fsconnect path uses held directory descriptors and no-follow descent;
  the Windows issue is a fallback-specific problem.
- The low-score user_gate bypass around the Phase-2 input rail is intentional:
  all traffic still passes the gateway sanitizer and external behavior remains
  human-gated. It should be pinned with an explicit regression test, but was not
  treated as a defect.
- The recent stale-lock work correctly expands the lock threshold to cover an
  enabled post-sync check. The separate /ops/sync timeout calculation remains
  incomplete.

## Recommended implementation order

1. Programmatically refuse Windows fsconnect writes and fix stale fsconnect
   staging before any optional write/index rollout.
2. Preserve change evidence across sync retries, fix the /ops/sync lifecycle
   budget, and restore audit convergence for exceptional exits.
3. Make Guardrails configuration and retrieved-context transport explicit and
   integration-tested with a real NeMo configuration.
4. Strictly type-check all safety gates, then harden fsconnect trash recovery and
   MSSQL read-only/locking behavior.
