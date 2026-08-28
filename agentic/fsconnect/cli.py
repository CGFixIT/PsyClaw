"""Command-line entry point: ``python -m agentic.fsconnect.cli <subcommand>``.

Subcommands:

    status   Print fsconnect config (roots, ops, write gate, index toggle).
    list     List a directory under a read root (--root --path).
    read     Read a file under a read root (--root --path).
    stat     Stat a path under a read root.
    grep     Search a file for a pattern (--pattern [--regex]).
    glob     Find files matching a glob under a read root (--pattern [--no-recursive]).
    largest  Rank the largest files under a read root (--top [--min-bytes]).
    write    Write a file in a writable root (gated; --reason; --overwrite/--confirm).
    append   Append to a file in a writable root (gated; --reason).
    mkdir    Make a directory in a writable root (gated; --reason).
    move     Move within a writable root (gated; --reason; --confirm).
    delete        Delete a path to trash (gated; --reason; --confirm; --purge for hard delete).
    trash-empty   Purge expired (or --all) trash entries (gated; --reason; --confirm).
    trash-restore Restore a trash entry to its original path (gated; --reason; --confirm).
    quota-status  Report per-root quota usage/limits (--recompute to force a walk).
    index    Stage the file-share into the corpus (--apply [--reindex]); dry-run default.
    reveal   Open a writable root in the OS file manager (out-of-band).
    trash-empty-plist  Generate (never load) the macOS launchd plist for a
                        weekly trash-empty job, from real resolved paths.
    trash-empty-task   Generate (never register) the Windows scheduled-task
                        XML for the same weekly trash-empty job.
    test     Run the pre-flight self-test.

Write content is supplied via --body / --body-file / stdin -- the connector never
calls the LLM. Exit codes: 0 ok (and the clean no-op when disabled) / 2 op failed /
3 config-env problem / 4 write refused by the gate.

This module never imports gate.py, graph.py, or mcp_hybrid_server.py.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from agentic.fsconnect.config import FsConnectConfig, load_fsconnect_config
from utils import launchd_plist, win_schtasks
from utils.errors import (
    FsConnectConfigError,
    FsConnectError,
    FsWriteRefused,
)
from utils.logger import _get_config
from utils.telemetry_kill import scheduler_env_overlay

if TYPE_CHECKING:
    from agentic.fsconnect.writer import FsWriter

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3
EXIT_REFUSED = 4

# Fixed Label the generated plist owns -- matches the shipped static template
# at macos/LaunchAgents/com.cgfixit.cyclaw.fsconnect-trash.plist (both write
# to the same well-known path; the generator is the recommended path, the
# template stays as a hand-editable reference/fallback).
_TRASH_LAUNCHD_LABEL = "com.cgfixit.cyclaw.fsconnect-trash"
_TRASH_TASK_NAME = "CyClaw fsconnect-trash"
_DEFAULT_TRASH_REASON = "weekly launchd retention purge"
_DEFAULT_TRASH_REASON_WIN = "weekly scheduled-task retention purge"


def _heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def _kv(key: str, value: object) -> None:
    print(f"  {key:.<22} {value}")


def _err(text: str) -> None:
    print(f"  [ERR ] {text}", file=sys.stderr)


def _emit(obj: object) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _load(args: argparse.Namespace) -> FsConnectConfig | None:
    try:
        return load_fsconnect_config(args.config)
    except FsConnectConfigError as exc:
        _err(f"Config error: {exc.message}")
        for k, v in (exc.details or {}).items():
            _err(f"   {k}: {v}")
        return None


def _disabled_noop() -> int:
    _heading("Filesystem connector disabled")
    print("  fsconnect.enabled is false in config.yaml; nothing to do.")
    print("  Set fsconnect.enabled: true to use this connector.")
    return EXIT_OK


def _read_body(args: argparse.Namespace) -> bytes:
    if getattr(args, "body_file", None):
        with open(args.body_file, "rb") as f:
            return f.read()
    if getattr(args, "body", None) is not None:
        return args.body.encode("utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.buffer.read()
    return b""


# --- commands --------------------------------------------------------------

def cmd_status(args: argparse.Namespace) -> int:
    fc = _load(args)
    if fc is None:
        return EXIT_ENV
    _heading("CyClaw Filesystem Connector Status")
    _kv("enabled", getattr(fc, "enabled", False))
    _kv("allowed_roots", ", ".join(fc.allowed_roots) or "(none)")
    _kv("allowed_fs_ops", ", ".join(fc.allowed_fs_ops))
    _kv("allow_unc_roots", fc.allow_unc_roots)
    _kv("allow_macos_volume_roots", fc.allow_macos_volume_roots)
    _kv("max_file_bytes", fc.max_file_bytes)
    _kv("largest_max_entries", fc.largest_max_entries)
    _kv("writes_enabled", fc.writes_enabled)
    _kv("writable_roots", ", ".join(fc.write_root_strs) or "(none)")
    _kv("require_confirm_destructive", fc.require_confirm_destructive)
    _kv("index_enabled", fc.index_enabled)
    _kv("index_root", fc.index_root)
    return EXIT_OK


def _run_read(args: argparse.Namespace, op: str) -> int:
    fc = _load(args)
    if fc is None:
        return EXIT_ENV
    if not getattr(fc, "enabled", False):
        return _disabled_noop()
    from agentic.fsconnect import context
    cfg = _get_config(args.config)
    try:
        res = context.run_read(
            cfg, fc, op, config_path=args.config,
            target=getattr(args, "path", "") or "",
            root=getattr(args, "root", None),
            pattern=getattr(args, "pattern", None),
            regex=getattr(args, "regex", False),
            recursive=getattr(args, "recursive", True),
            top=getattr(args, "top", 20),
            min_bytes=getattr(args, "min_bytes", 0),
        )
    except FsConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL
    _emit(res)
    return EXIT_OK


def cmd_list(args: argparse.Namespace) -> int:
    return _run_read(args, "fs_list")


def cmd_read(args: argparse.Namespace) -> int:
    return _run_read(args, "fs_read")


def cmd_stat(args: argparse.Namespace) -> int:
    return _run_read(args, "fs_stat")


def cmd_grep(args: argparse.Namespace) -> int:
    return _run_read(args, "fs_grep")


def cmd_glob(args: argparse.Namespace) -> int:
    return _run_read(args, "fs_glob")


def cmd_largest(args: argparse.Namespace) -> int:
    return _run_read(args, "fs_largest")


def _run_write(args: argparse.Namespace, op: str) -> int:
    fc = _load(args)
    if fc is None:
        return EXIT_ENV
    if not getattr(fc, "enabled", False):
        return _disabled_noop()
    cfg = _get_config(args.config)
    from agentic.fsconnect.writer import FsWriter
    try:
        with FsWriter(cfg, fc, config_path=args.config) as w:
            if op == "fs_write":
                res = w.fs_write(args.path, _read_body(args), reason=args.reason or "",
                                 confirm=args.confirm, overwrite=args.overwrite, root=args.root)
            elif op == "fs_append":
                res = w.fs_append(args.path, _read_body(args), reason=args.reason or "",
                                  confirm=args.confirm, root=args.root)
            elif op == "fs_mkdir":
                res = w.fs_mkdir(args.path, reason=args.reason or "",
                                 confirm=args.confirm, root=args.root)
            else:  # fs_move
                res = w.fs_move(args.src, args.dst, reason=args.reason or "",
                                confirm=args.confirm, overwrite=args.overwrite, root=args.root)
    except FsWriteRefused as exc:
        _err(f"Write refused: {exc.message}")
        return EXIT_REFUSED
    except FsConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL
    _emit(res)
    return EXIT_OK


def cmd_write(args: argparse.Namespace) -> int:
    return _run_write(args, "fs_write")


def cmd_append(args: argparse.Namespace) -> int:
    return _run_write(args, "fs_append")


def cmd_mkdir(args: argparse.Namespace) -> int:
    return _run_write(args, "fs_mkdir")


def cmd_move(args: argparse.Namespace) -> int:
    return _run_write(args, "fs_move")


def _run_writer(args: argparse.Namespace, call: Callable[[FsWriter], dict]) -> int:
    """Load config, open an FsWriter, and run ``call(writer)`` with the exit-code map.

    Shared by the delete/trash/quota subcommands so each maps FsWriteRefused -> exit 4,
    other FsConnectError -> exit 2, and the disabled/config-error no-ops identically to
    the read/write helpers above.
    """
    fc = _load(args)
    if fc is None:
        return EXIT_ENV
    if not getattr(fc, "enabled", False):
        return _disabled_noop()
    cfg = _get_config(args.config)
    from agentic.fsconnect.writer import FsWriter
    try:
        with FsWriter(cfg, fc, config_path=args.config) as w:
            res = call(w)
    except FsWriteRefused as exc:
        _err(f"Write refused: {exc.message}")
        return EXIT_REFUSED
    except FsConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL
    _emit(res)
    return EXIT_OK


def cmd_delete(args: argparse.Namespace) -> int:
    return _run_writer(args, lambda w: w.fs_delete(
        args.path, reason=args.reason or "", confirm=args.confirm,
        purge=args.purge, root=args.root))


def cmd_trash_empty(args: argparse.Namespace) -> int:
    return _run_writer(args, lambda w: w.trash_empty(
        reason=args.reason or "", confirm=args.confirm,
        all_entries=args.all, root=args.root))


def cmd_trash_restore(args: argparse.Namespace) -> int:
    return _run_writer(args, lambda w: w.trash_restore(
        args.entry, reason=args.reason or "", confirm=args.confirm,
        overwrite=args.overwrite, root=args.root))


def cmd_quota_status(args: argparse.Namespace) -> int:
    return _run_writer(args, lambda w: w.quota_status(
        root=args.root, recompute=args.recompute))


def cmd_index(args: argparse.Namespace) -> int:
    fc = _load(args)
    if fc is None:
        return EXIT_ENV
    if not getattr(fc, "enabled", False):
        return _disabled_noop()
    if not fc.index_enabled:
        _heading("Indexing disabled")
        print("  fsconnect.index_enabled is false; nothing to do.")
        return EXIT_OK
    cfg = _get_config(args.config)
    from agentic.fsconnect.indexer import FsIndexer
    try:
        idx = FsIndexer(cfg, fc, config_path=args.config)
        res = idx.apply(staging_dir=args.staging, reindex=args.reindex) if args.apply else idx.scan()
    except FsConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL
    _emit(res)
    return EXIT_OK


def cmd_reveal(args: argparse.Namespace) -> int:
    fc = _load(args)
    if fc is None:
        return EXIT_ENV
    if not getattr(fc, "enabled", False):
        return _disabled_noop()
    from agentic.fsconnect.osutil import reveal
    target = args.root or (fc.write_root_strs[0] if fc.write_root_strs else None)
    if not target:
        _err("no root to reveal (configure writable_roots or pass --root)")
        return EXIT_FAIL
    try:
        res = reveal(target, fc.write_root_strs)
    except FsConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL
    _emit(res)
    return EXIT_OK


def cmd_trash_empty_plist(args: argparse.Namespace) -> int:
    """Generate (never load) the weekly trash-empty launchd plist.

    Darwin-only. Writes ~/Library/LaunchAgents/<label>.plist from real,
    resolved paths -- no REPLACE_* placeholders -- but never calls
    `launchctl load`/`bootstrap` itself; loading stays an explicit operator
    step (see the printed bootstrap hint). The generated command still fails
    closed at runtime if fsconnect.writes_enabled (or any other write-gate
    setting) is not true when the job actually fires -- this generator does
    not bypass or pre-satisfy that gate.
    """
    if platform.system() != "Darwin":
        _err("trash-empty-plist is Darwin-only (writes a macOS launchd plist).")
        return EXIT_ENV

    fc = _load(args)
    if fc is None:
        return EXIT_ENV
    if not getattr(fc, "enabled", False):
        return _disabled_noop()

    if not 0 <= args.weekday <= 7:
        _err("--weekday must be 0-7 (0 or 7 = Sunday, 1 = Monday)")
        return EXIT_FAIL
    if not 0 <= args.hour <= 23:
        _err("--hour must be 0-23")
        return EXIT_FAIL
    if not 0 <= args.minute <= 59:
        _err("--minute must be 0-59")
        return EXIT_FAIL

    root = args.root or (fc.write_root_strs[0] if fc.write_root_strs else None)
    if not root:
        _err("no writable root configured (set fsconnect.writable_roots, or pass --root)")
        return EXIT_FAIL

    repo_root = Path(__file__).resolve().parent.parent.parent
    program_args = [
        launchd_plist.python_executable(),
        "-m",
        "agentic.fsconnect.cli",
        "--config",
        str(Path(args.config).resolve()),
        "trash-empty",
        "--root",
        root,
        "--reason",
        args.reason,
        "--confirm",
    ]
    log_path = str(launchd_plist.logs_dir() / "fsconnect-trash.log")
    document = {
        "Label": _TRASH_LAUNCHD_LABEL,
        "WorkingDirectory": str(repo_root),
        "ProgramArguments": program_args,
        # launchd hands a job a near-empty environment; deliver the
        # canonical telemetry/update-check block before anything starts.
        # Non-secret fixed literals; secrets stay on the Keychain wrapper.
        "EnvironmentVariables": scheduler_env_overlay(),
        "StartCalendarInterval": {
            "Weekday": args.weekday,
            "Hour": args.hour,
            "Minute": args.minute,
        },
        "RunAtLoad": False,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }

    path = launchd_plist.plist_path(_TRASH_LAUNCHD_LABEL)
    launchd_plist.write_plist(document, path)

    _heading("fsconnect trash-empty launchd plist")
    _kv("plist", path)
    _kv("root", root)
    _kv("schedule", f"weekly weekday={args.weekday} {args.hour:02d}:{args.minute:02d}")
    print()
    if not fc.writes_enabled:
        print("  NOTE: fsconnect.writes_enabled is currently false -- this job will")
        print("  fail closed at runtime until write-enablement is completed. See")
        print("  docs/agentic/FSCONNECT_WRITE_ENABLEMENT_PLAYBOOK.md.")
    print(f"  NOT loaded. Run to activate: {launchd_plist.bootstrap_hint(path)}")
    return EXIT_OK


def cmd_trash_empty_task(args: argparse.Namespace) -> int:
    """Generate (never register) the weekly trash-empty Task Scheduler XML.

    Windows-only. Writes ``~/.CyClaw/tasks/`` XML + ``.cmd`` from real
    resolved paths -- no REPLACE_* placeholders -- but never calls
    ``schtasks /Create``. The generated command still fails closed at
    runtime if ``fsconnect.writes_enabled`` is not true when the job fires.
    """
    if platform.system() != "Windows":
        _err("trash-empty-task is Windows-only (writes a Task Scheduler XML).")
        return EXIT_ENV

    fc = _load(args)
    if fc is None:
        return EXIT_ENV
    if not getattr(fc, "enabled", False):
        return _disabled_noop()

    if not 0 <= args.weekday <= 7:
        _err("--weekday must be 0-7 (0 or 7 = Sunday, 1 = Monday)")
        return EXIT_FAIL
    if not 0 <= args.hour <= 23:
        _err("--hour must be 0-23")
        return EXIT_FAIL
    if not 0 <= args.minute <= 59:
        _err("--minute must be 0-59")
        return EXIT_FAIL

    root = args.root or (fc.write_root_strs[0] if fc.write_root_strs else None)
    if not root:
        _err("no writable root configured (set fsconnect.writable_roots, or pass --root)")
        return EXIT_FAIL

    repo_root = Path(__file__).resolve().parent.parent.parent
    argv = [
        win_schtasks.python_executable(),
        "-m",
        "agentic.fsconnect.cli",
        "--config",
        str(Path(args.config).resolve()),
        "trash-empty",
        "--root",
        root,
        "--reason",
        args.reason,
        "--confirm",
    ]
    path, _launcher = win_schtasks.write_generated_task(
        task_name=_TRASH_TASK_NAME,
        argv=argv,
        working_directory=str(repo_root),
        # Canonical telemetry/update-check block via the .cmd's set-lines
        # (Task Scheduler starts jobs from a near-empty environment).
        env=scheduler_env_overlay(),
        triggers=win_schtasks.weekly_calendar_trigger(args.weekday, args.hour, args.minute),
    )

    _heading("fsconnect trash-empty scheduled task")
    _kv("xml", path)
    _kv("root", root)
    _kv("schedule", f"weekly weekday={args.weekday} {args.hour:02d}:{args.minute:02d}")
    print()
    if not fc.writes_enabled:
        print("  NOTE: fsconnect.writes_enabled is currently false -- this job will")
        print("  fail closed at runtime until write-enablement is completed. See")
        print("  docs/agentic/FSCONNECT_WRITE_ENABLEMENT_PLAYBOOK.md.")
    print(f"  NOT registered. Run to activate: {win_schtasks.register_hint(_TRASH_TASK_NAME, path)}")
    return EXIT_OK


def cmd_test(args: argparse.Namespace) -> int:
    from agentic.fsconnect.selftest import run_self_test
    passed, total, lines = run_self_test(args.config)
    _heading(f"Self-test: {passed}/{total} passed")
    for line in lines:
        print(line)
    return EXIT_OK if passed == total else EXIT_FAIL


# --- parser ----------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic.fsconnect.cli",
        description="CyClaw filesystem connector -- scoped reads + gated writes, out-of-band.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print fsconnect config status.")
    p_status.set_defaults(func=cmd_status)

    for name, func, helptext in (
        ("list", cmd_list, "List a directory under a read root."),
        ("read", cmd_read, "Read a file under a read root."),
        ("stat", cmd_stat, "Stat a path under a read root."),
    ):
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--root", help="Which read root (required if multiple).")
        p.add_argument("--path", default="", help="Path relative to the root.")
        p.set_defaults(func=func)

    p_grep = sub.add_parser("grep", help="Search a file for a pattern.")
    p_grep.add_argument("--root")
    p_grep.add_argument("--path", required=True)
    p_grep.add_argument("--pattern", required=True)
    p_grep.add_argument("--regex", action="store_true", help="Treat pattern as a regex.")
    p_grep.set_defaults(func=cmd_grep)

    p_glob = sub.add_parser("glob", help="Find files matching a glob under a read root.")
    p_glob.add_argument("--root")
    p_glob.add_argument("--path", default="", help="Directory to search under (default: root).")
    p_glob.add_argument("--pattern", required=True, help="Glob pattern, e.g. '*.md' or 'sub/*.txt'.")
    p_glob.add_argument("--no-recursive", action="store_false", dest="recursive",
                        help="Search only the immediate directory, not subdirectories.")
    p_glob.set_defaults(func=cmd_glob, recursive=True)

    p_largest = sub.add_parser("largest", help="Rank the largest files under a read root.")
    p_largest.add_argument("--root", help="Which read root (required if multiple).")
    p_largest.add_argument("--path", default="", help="Directory to search under (default: root).")
    p_largest.add_argument("--top", type=int, default=20, help="Number of files to return (default: 20).")
    p_largest.add_argument(
        "--min-bytes", type=int, default=0,
        help="Ignore files smaller than this many bytes (default: 0).",
    )
    p_largest.set_defaults(func=cmd_largest)

    for name, func in (("write", cmd_write), ("append", cmd_append)):
        p = sub.add_parser(name, help=f"{name.capitalize()} a file in a writable root (gated).")
        p.add_argument("--root")
        p.add_argument("--path", required=True)
        p.add_argument("--body", help="Inline content (else --body-file or stdin).")
        p.add_argument("--body-file")
        p.add_argument("--reason", help="Human reason string (required to execute).")
        p.add_argument("--confirm", action="store_true")
        if name == "write":
            p.add_argument("--overwrite", action="store_true", help="Replace an existing file.")
        else:
            p.set_defaults(overwrite=False)
        p.set_defaults(func=func)

    p_mkdir = sub.add_parser("mkdir", help="Make a directory in a writable root (gated).")
    p_mkdir.add_argument("--root")
    p_mkdir.add_argument("--path", required=True)
    p_mkdir.add_argument("--reason")
    p_mkdir.add_argument("--confirm", action="store_true")
    p_mkdir.set_defaults(func=cmd_mkdir, overwrite=False)

    p_move = sub.add_parser("move", help="Move within a writable root (gated).")
    p_move.add_argument("--root")
    p_move.add_argument("--src", required=True)
    p_move.add_argument("--dst", required=True)
    p_move.add_argument("--reason")
    p_move.add_argument("--confirm", action="store_true")
    p_move.add_argument("--overwrite", action="store_true")
    p_move.set_defaults(func=cmd_move)

    p_delete = sub.add_parser("delete", help="Delete a path to trash (gated; --purge for hard delete).")
    p_delete.add_argument("--root")
    p_delete.add_argument("--path", required=True)
    p_delete.add_argument("--reason")
    p_delete.add_argument("--confirm", action="store_true")
    p_delete.add_argument("--purge", action="store_true",
                          help="Hard-delete (bypass trash); needs allow_hard_delete in config.")
    p_delete.set_defaults(func=cmd_delete)

    p_tempty = sub.add_parser("trash-empty", help="Purge expired (or --all) trash entries (gated).")
    p_tempty.add_argument("--root")
    p_tempty.add_argument("--reason")
    p_tempty.add_argument("--confirm", action="store_true")
    p_tempty.add_argument("--all", action="store_true", help="Purge every entry, not just expired.")
    p_tempty.set_defaults(func=cmd_trash_empty)

    p_trestore = sub.add_parser("trash-restore", help="Restore a trash entry to its original path (gated).")
    p_trestore.add_argument("--root")
    p_trestore.add_argument("--entry", required=True, help="Trash entry name (from trash listing).")
    p_trestore.add_argument("--reason")
    p_trestore.add_argument("--confirm", action="store_true")
    p_trestore.add_argument("--overwrite", action="store_true",
                            help="Replace an existing file at the original path.")
    p_trestore.set_defaults(func=cmd_trash_restore)

    p_quota = sub.add_parser("quota-status", help="Report per-root quota usage/limits.")
    p_quota.add_argument("--root")
    p_quota.add_argument("--recompute", action="store_true", help="Force a full usage walk.")
    p_quota.set_defaults(func=cmd_quota_status)

    p_index = sub.add_parser("index", help="Stage the file-share into the corpus (dry-run default).")
    p_index.add_argument("--apply", action="store_true", help="Stage files (else dry-run scan).")
    p_index.add_argument("--reindex", action="store_true", help="Trigger reindex subprocess after staging.")
    p_index.add_argument("--staging", help="Override staging dir (default data/corpus/fsconnect).")
    p_index.set_defaults(func=cmd_index)

    p_reveal = sub.add_parser("reveal", help="Open a writable root in the OS file manager.")
    p_reveal.add_argument("--root", help="Path/root to reveal (default: first writable root).")
    p_reveal.set_defaults(func=cmd_reveal)

    p_tplist = sub.add_parser(
        "trash-empty-plist",
        help="Generate (never load) the macOS launchd plist for a weekly trash-empty job. Darwin-only.",
    )
    p_tplist.add_argument("--root", help="Writable root to purge (default: first configured writable root).")
    p_tplist.add_argument("--weekday", type=int, default=1, help="0-7, 0/7=Sunday (default: 1, Monday).")
    p_tplist.add_argument("--hour", type=int, default=3, help="0-23 (default: 3).")
    p_tplist.add_argument("--minute", type=int, default=0, help="0-59 (default: 0).")
    p_tplist.add_argument("--reason", default=_DEFAULT_TRASH_REASON, help="Reason baked into the scheduled command.")
    p_tplist.set_defaults(func=cmd_trash_empty_plist)

    p_ttask = sub.add_parser(
        "trash-empty-task",
        help="Generate (never register) the Windows scheduled-task XML for a weekly trash-empty job.",
    )
    p_ttask.add_argument("--root", help="Writable root to purge (default: first configured writable root).")
    p_ttask.add_argument("--weekday", type=int, default=1, help="0-7, 0/7=Sunday (default: 1, Monday).")
    p_ttask.add_argument("--hour", type=int, default=3, help="0-23 (default: 3).")
    p_ttask.add_argument("--minute", type=int, default=0, help="0-59 (default: 0).")
    p_ttask.add_argument("--reason", default=_DEFAULT_TRASH_REASON_WIN, help="Reason baked into the scheduled command.")
    p_ttask.set_defaults(func=cmd_trash_empty_task)

    p_test = sub.add_parser("test", help="Run the pre-flight self-test.")
    p_test.set_defaults(func=cmd_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Dispatch one subcommand, mapping typed failures onto the exit-code API.

    Exit codes are an API here: utils/ops_runner.py maps the documented set to
    ok/failed/env_config/write_refused and everything else to "unknown".
    Most subcommands convert their own failures, but a handler at the dispatch
    point covers the whole class rather than the call sites known today --
    the same fix agentic/cli.py::main received in #824.

    Deliberately narrow: only the typed hierarchy is classified, so a genuine
    bug still surfaces as a traceback instead of being flattened into a tidy
    exit 2.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except FsWriteRefused as exc:
        _err(f"Write refused: {exc.message}")
        return EXIT_REFUSED
    except FsConnectConfigError as exc:
        _err(f"Config error: {exc.message}")
        return EXIT_ENV
    except FsConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
