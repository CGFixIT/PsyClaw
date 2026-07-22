"""Command-line entry point: ``python -m sync.cli <subcommand>``.

Subcommands:

    setup        Verify rclone, load+show config, write filters, print the
                 Dropbox OAuth hint, optionally schedule the daily job.
    sync         Run one sync now. ``--dry-run`` previews; ``--resync`` rebuilds
                 the bisync baseline.
    test         Run the pre-flight self-test.
    schedule     Register the daily job (cron / launchd / Task Scheduler).
    unschedule   Remove the daily job.
    status       Print current sync + schedule status.

Exit codes (see §7 of the implementation plan):
    0    success, no corpus change
    10   success and data/corpus/** changed -- run `python -m retrieval.indexer`
    1    aborted by a safety fuse (--max-delete / --max-transfer)
    2    sync failed (other)
    3    config / environment problem (rclone missing or too old, config invalid)

This module never imports gate.py, graph.py, or mcp_hybrid_server.py. The
scheduler is imported LAZILY inside the schedule/unschedule/setup handlers so
this module imports even while the scheduler is still in development, and so
tests can patch ``sync.cli.get_scheduler`` (re-exported below) cleanly.
"""

from __future__ import annotations

import argparse
import platform
import subprocess  # noqa: S404 -- fixed-argv indexer invocation only; never shell=True
import sys
import textwrap
from typing import Any

from sync.config import load_sync_config
from sync.filters import filter_summary, write_filter_file
from sync.runner import check_rclone_version, reindex_exit_code_for, run_sync
from utils.errors import (
    RcloneNotInstalledError,
    RcloneTimeoutError,
    RcloneVersionError,
    SchedulerError,
    SyncConfigError,
    SyncError,
)

EXIT_OK = 0
EXIT_SAFETY = 1
EXIT_FAIL = 2
EXIT_ENV = 3
EXIT_REINDEX = 10


def get_scheduler(cfg: Any) -> Any:
    """Lazy proxy to ``sync.scheduler.get_scheduler``.

    Imported inside the function so ``sync.cli`` imports even if the scheduler
    module is absent / in flight, and so the import is deferred until a
    scheduling subcommand actually runs. Tests patch this name directly.
    """
    from sync.scheduler import get_scheduler as _get_scheduler

    return _get_scheduler(cfg)


# ---------------------------------------------------------------------------
# Pretty-printing helpers (stdlib only).
# ---------------------------------------------------------------------------

def _heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def _kv(key: str, value: object) -> None:
    print(f"  {key:.<24} {value}")


def _warn(text: str) -> None:
    print(f"  [WARN] {text}", file=sys.stderr)


def _err(text: str) -> None:
    print(f"  [ERR ] {text}", file=sys.stderr)


def _ok(text: str) -> None:
    print(f"  [OK  ] {text}")


def _print_typed_error(exc: object) -> None:
    """Print a typed error's ``message`` plus any actionable ``details`` keys.

    Only ``cmd_setup`` previously surfaced ``exc.details`` (e.g. the
    ``corpus_root`` an invalid ``local_path`` must live under); ``cmd_sync`` and
    the schedule/status handlers dropped them, so operators on the *common*
    paths got a less actionable error than those running setup. Centralised so
    every command surfaces the same detail. ``getattr`` keeps it safe for typed
    errors without a ``details`` attribute (e.g. ``SchedulerError``).
    """
    _err(getattr(exc, "message", str(exc)))
    for k, v in (getattr(exc, "details", None) or {}).items():
        _err(f"   {k}: {v}")


# ---------------------------------------------------------------------------
# Subcommand handlers
# ---------------------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> int:
    """First-time setup: config, version, filters, OAuth hint, optional schedule."""
    _heading("CyClaw Dropbox Sync -- Setup")

    try:
        cfg = load_sync_config(args.config)
    except SyncConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    _ok(f"Loaded sync config from {args.config}")
    _kv("local_path", cfg.local_path)
    _kv("remote", cfg.remote)
    _kv("direction", cfg.direction)
    _kv("include_soul", cfg.include_soul)

    if cfg.include_soul:
        # Honest note, not an alarm: include_soul is a deprecated no-op. The
        # sync root is confined to data/corpus (sync.config), which can never
        # contain data/personality/, and the soul filter rule is unconditional
        # -- so soul data cannot be mirrored regardless of this flag.
        _warn("include_soul=true has no effect: soul data is never mirrored.")
        _warn("sync.local_path is confined to data/corpus; data/personality/ is outside the sync root.")

    try:
        v = check_rclone_version()
        _ok(f"rclone {v[0]}.{v[1]}.{v[2]} installed")
    except (RcloneNotInstalledError, RcloneTimeoutError, RcloneVersionError) as exc:
        _print_typed_error(exc)
        return EXIT_ENV

    try:
        path = write_filter_file(cfg)
        _ok(f"Wrote filter file: {path}")
    except OSError as exc:
        _err(f"Could not write filter file: {exc}")
        return EXIT_ENV

    _heading("Next: configure the Dropbox remote")
    print(textwrap.dedent(f"""
        1. Run: rclone config
        2. Choose:  n (new remote)
        3. Name:    {cfg.remote_name}
        4. Storage: dropbox
        5. Accept defaults; complete the browser OAuth flow.
        6. Verify:  rclone lsd {cfg.remote_name}:
    """).strip())

    if args.schedule:
        try:
            entry = get_scheduler(cfg).install()
            _ok(f"Scheduled daily sync ({entry.cron_or_time})")
        except SchedulerError as exc:
            _err(f"Scheduling failed: {exc.message}")
            return EXIT_ENV

    return EXIT_OK


def _run_auto_reindex(cfg: Any) -> int:
    """Rebuild the index as a child process after a corpus-changing sync.

    Reached only when ``sync.auto_reindex`` is true and the run signalled a
    corpus change (the exit-10 condition). Returns ``EXIT_OK`` if the rebuild
    succeeded, ``EXIT_FAIL`` otherwise -- a failed rebuild leaves the index stale
    relative to the freshly-synced corpus, which the operator must see rather
    than have masked behind a success code.

    argv is a fixed list (``sys.executable -m retrieval.indexer [--config PATH]``);
    never ``shell=True``. The loaded config's identity is propagated via
    ``--config`` so a sync started with ``--config /alt/cfg.yaml`` rebuilds the
    index from THAT config, not the default ``config.yaml`` (codex #592). The
    recorded path is repo-root-anchored absolute, so it resolves regardless of
    the child's working directory.
    """
    _heading("Corpus changed -- auto-reindexing")
    argv = [sys.executable, "-m", "retrieval.indexer"]
    cfg_path = getattr(cfg, "_config_path", None)
    if cfg_path:
        argv += ["--config", cfg_path]
    try:
        completed = subprocess.run(argv, check=False)  # noqa: S603 -- fixed argv, no shell
    except OSError as exc:
        _err(f"Could not launch the indexer: {exc}")
        return EXIT_FAIL
    if completed.returncode == 0:
        _ok("Index rebuilt; corpus and index are back in sync.")
        return EXIT_OK
    _err(f"Indexer exited {completed.returncode}; the index may be stale.")
    return EXIT_FAIL


def cmd_sync(args: argparse.Namespace) -> int:
    """Run one sync now."""
    try:
        cfg = load_sync_config(args.config)
    except SyncConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV

    # Honour the config toggle: `sync.enabled: false` is an intentional "off",
    # not an error -- a scheduled run should no-op cleanly (exit 0), never fail.
    if not getattr(cfg, "enabled", True):
        _heading("Sync disabled")
        print("  sync.enabled is false in config.yaml; nothing to do.")
        print("  Set sync.enabled: true (or remove the key) to run sync.")
        return EXIT_OK

    try:
        result = run_sync(cfg, dry_run=args.dry_run, resync=args.resync)
    except (RcloneNotInstalledError, RcloneTimeoutError, RcloneVersionError) as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    except SyncError as exc:
        _err(f"Sync error: {exc.message}")
        return EXIT_FAIL

    counts = result.event_counts()
    _heading("Sync complete" if result.success else "Sync FAILED")
    _kv("direction", result.direction)
    _kv("exit_code", result.rclone_exit_code)
    _kv("duration_sec", f"{result.duration_sec:.2f}")
    _kv("added", counts["added"])
    _kv("modified", counts["modified"])
    _kv("deleted", counts["deleted"])
    _kv("corpus_changed", result.corpus_changed)
    if result.errors:
        _kv("errors_n", len(result.errors))
        for line in result.errors[:5]:
            _err(line[:200])

    if result.check_result is not None:
        cr = result.check_result
        _kv("check_ok", cr.ok)
        if not cr.ok:
            _kv("check_differences", cr.differences)
            _kv("check_missing_local", cr.missing_local)
            _kv("check_missing_remote", cr.missing_remote)
            for e in cr.errors[:3]:
                _warn(f"check: {e}")

    code = reindex_exit_code_for(result, cfg)
    # auto_reindex turns the exit-10 "caller should reindex" signal into an
    # in-CLI rebuild, so a scheduled sync keeps the index fresh with no second
    # cron entry. Only fires on the corpus-changed path; every other exit code
    # (no change, safety abort, failure) passes straight through unchanged.
    if code == EXIT_REINDEX and getattr(cfg, "auto_reindex", False):
        return _run_auto_reindex(cfg)
    return code


def cmd_test(args: argparse.Namespace) -> int:
    """Run the pre-flight self-test."""
    from sync.selftest import run_self_test

    passed, total, lines = run_self_test(args.config, dry_run=True)
    _heading(f"Self-test: {passed}/{total} passed")
    for line in lines:
        print(line)
    return EXIT_OK if passed == total else EXIT_FAIL


def cmd_schedule(args: argparse.Namespace) -> int:
    try:
        cfg = load_sync_config(args.config)
        entry = get_scheduler(cfg).install()
    except (SyncConfigError, SchedulerError) as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    _ok(f"Scheduled: {entry.cron_or_time} on {entry.platform_name}")
    return EXIT_OK


def cmd_unschedule(args: argparse.Namespace) -> int:
    try:
        cfg = load_sync_config(args.config)
        removed = get_scheduler(cfg).remove()
    except (SyncConfigError, SchedulerError) as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    if removed:
        _ok("Scheduled job removed.")
    else:
        _ok("No CyClaw scheduled job was registered.")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    try:
        cfg = load_sync_config(args.config)
    except SyncConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV

    _heading("CyClaw Sync Status")
    _kv("enabled", getattr(cfg, "enabled", True))
    _kv("local_path", cfg.local_path)
    _kv("remote", cfg.remote)
    _kv("direction", cfg.direction)
    _kv("include_soul", f"{cfg.include_soul} (deprecated no-op -- soul is never mirrored)")
    _kv("schedule", f"{cfg.schedule_hour:02d}:{cfg.schedule_min:02d}")
    _kv("filter_file", cfg.filter_file)
    _kv("log_dir", cfg.log_dir)
    _kv("platform", platform.system())

    try:
        v = check_rclone_version()
        _ok(f"rclone {v[0]}.{v[1]}.{v[2]}")
    except (RcloneNotInstalledError, RcloneTimeoutError, RcloneVersionError) as exc:
        _print_typed_error(exc)

    try:
        entry = get_scheduler(cfg).status()
        if entry:
            _ok(f"Scheduled: {entry.cron_or_time}")
        else:
            print("  [-] Not scheduled.")
    except SchedulerError as exc:
        _warn(f"Could not read scheduler state: {exc.message}")
    except ImportError:
        # Scheduler module not available in this environment -- non-fatal for status.
        _warn("Scheduler module unavailable; schedule state not read.")

    fsummary = filter_summary(cfg)
    print()
    print("  Filter summary:")
    print(f"    soul excluded:  {fsummary['soul_excluded']}")
    print(f"    total rules:    {fsummary['total_rules']}")
    print(f"    extra excludes: {len(fsummary['extra_excludes'])}")

    return EXIT_OK


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m sync.cli",
        description="CyClaw Dropbox sync -- rclone-based, audit-logged, out-of-band.",
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to CyClaw config.yaml (default: %(default)s)",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_setup = sub.add_parser("setup", help="Bootstrap: verify env, write filters, optional schedule.")
    p_setup.add_argument("--schedule", action="store_true", help="Also register the daily scheduled job.")
    p_setup.set_defaults(func=cmd_setup)

    p_sync = sub.add_parser("sync", help="Run one sync now.")
    p_sync.add_argument("--dry-run", action="store_true", help="Preview only; modify nothing.")
    p_sync.add_argument("--resync", action="store_true", help="bisync only: rebuild baseline state.")
    p_sync.set_defaults(func=cmd_sync)

    p_test = sub.add_parser("test", help="Run the pre-flight self-test.")
    p_test.set_defaults(func=cmd_test)

    p_sched = sub.add_parser("schedule", help="Register the daily scheduled job.")
    p_sched.set_defaults(func=cmd_schedule)

    p_unsched = sub.add_parser("unschedule", help="Remove the daily scheduled job.")
    p_unsched.set_defaults(func=cmd_unschedule)

    p_status = sub.add_parser("status", help="Print sync + schedule status.")
    p_status.set_defaults(func=cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    return int(func(args))


if __name__ == "__main__":
    sys.exit(main())
