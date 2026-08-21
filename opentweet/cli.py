"""Command-line entry point: ``python -m opentweet.cli <subcommand>``.

Subcommands:

    status          Print channel config (no secrets).
    test            Run the pre-flight self-test.
    post            Generate one body via loopback /query and write a draft
                    (or a scheduled_date when opentweet.schedule_enabled).
    schedule-plist  Generate (never load) the macOS weekly LaunchAgent.
    schedule-task   Generate (never register) the Windows weekly task XML.

Exit codes (aligned with telegram/sync/agentic):
    0    success / clean no-op when opentweet.enabled is false (status/test/generators)
    2    operation refused or HTTP failed
    3    config / environment / platform problem

This module never imports gate.py, graph.py, or mcp_hybrid_server.py.
"""

from __future__ import annotations

import argparse
import platform
import sys
from pathlib import Path

from opentweet.config import OpenTweetConfig, load_opentweet_config
from opentweet.runner import post_once
from opentweet.selftest import run_self_test
from utils import launchd_plist, win_schtasks
from utils.errors import OpenTweetConfigError, OpenTweetError, OpenTweetRefused, OpenTweetRuntimeError
from utils.logger import audit_log

_LAUNCHD_LABEL = "com.cgfixit.cyclaw.opentweet"
_TASK_NAME = "CyClaw opentweet"
_DEFAULT_KEY_SERVICE = "com.cgfixit.cyclaw.opentweet-api-key"

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3


def _heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def _kv(key: str, value: object) -> None:
    print(f"  {key:.<28} {value}")


def _err(text: str) -> None:
    print(f"  [ERR ] {text}", file=sys.stderr)


def _ok(text: str) -> None:
    print(f"  [OK  ] {text}")


def _warn(text: str) -> None:
    print(f"  [WARN] {text}", file=sys.stderr)


def _print_typed_error(exc: object) -> None:
    _err(getattr(exc, "message", str(exc)))
    details = getattr(exc, "details", None) or {}
    for k, v in details.items():
        if k in {"received"} and isinstance(v, str) and len(v) > 80:
            continue
        _err(f"   {k}: {v}")


def _disabled_notice() -> int:
    print("  opentweet.enabled is false in config.yaml; nothing to do.")
    print("  Set opentweet.enabled: true and a non-empty topic_file to use this layer.")
    return EXIT_OK


def _audit_refused(cfg: OpenTweetConfig, exc: OpenTweetError) -> None:
    details = getattr(exc, "details", None) or {}
    audit_log(
        {
            "event": "opentweet_refused",
            "channel": "opentweet",
            "ok": False,
            "code": getattr(exc, "code", "OPENTWEET_ERROR"),
            "gate": details.get("gate"),
        },
        config_path=cfg._config_path,
    )


def cmd_status(args: argparse.Namespace) -> int:
    _heading("CyClaw OpenTweet Channel -- Status")
    try:
        cfg = load_opentweet_config(args.config)
    except OpenTweetConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV

    pub = cfg.to_public_dict()
    _kv("enabled", pub["enabled"])
    _kv("api_base", pub["api_base"])
    _kv("api_key_env", pub["api_key_env"])
    _kv("api_key_set", pub["api_key_set"])
    _kv("topic_file", pub["topic_file"] or "(unset)")
    _kv("max_topic_chars", pub["max_topic_chars"])
    _kv("max_post_chars", pub["max_post_chars"])
    _kv("schedule_enabled", pub["schedule_enabled"])
    _kv("schedule_slot", pub["schedule_slot"])
    _kv("weekday", pub["weekday"])
    _kv("fire_hour", pub["fire_hour"])
    _kv("fire_minute", pub["fire_minute"])
    _kv("query.base_url", pub["query"]["base_url"])
    _kv("query.api_key_env", pub["query"]["api_key_env"])
    _kv("query.api_key_set", pub["query_api_key_set"])
    _kv("query.timeout_sec", pub["query"]["timeout_sec"])
    if not cfg.enabled:
        _warn("Layer disabled (enabled: false) — post will refuse.")
    return EXIT_OK


def cmd_test(args: argparse.Namespace) -> int:
    _heading("CyClaw OpenTweet Channel -- Self-test")
    try:
        passed, total, lines = run_self_test(config_path=args.config)
    except OpenTweetConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    for ln in lines:
        print(ln)
    print(f"\n{passed}/{total} passed")
    return EXIT_OK if passed == total else EXIT_FAIL


def cmd_post(args: argparse.Namespace) -> int:
    _heading("CyClaw OpenTweet Channel -- Post")
    try:
        cfg = load_opentweet_config(args.config)
    except OpenTweetConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    if not cfg.enabled:
        _err("opentweet.enabled is false; post refuses.")
        return EXIT_ENV
    if args.schedule and not cfg.schedule_enabled:
        _err("--schedule requires opentweet.schedule_enabled: true")
        return EXIT_ENV
    want_schedule = bool(cfg.schedule_enabled)
    try:
        result = post_once(
            cfg,
            topic=args.topic or None,
            topic_file=args.topic_file or None,
            schedule=want_schedule,
            dry_run=bool(args.dry_run),
        )
    except OpenTweetRefused as exc:
        _print_typed_error(exc)
        _audit_refused(cfg, exc)
        return EXIT_FAIL
    except OpenTweetRuntimeError as exc:
        _print_typed_error(exc)
        return EXIT_FAIL
    except OpenTweetConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV

    _kv("mode", result["mode"])
    _kv("text_hash", result["text_hash"])
    _kv("text_len", result["text_len"])
    _kv("dry_run", result["dry_run"])
    _kv("opentweet_id", result["opentweet_id"] or "(none)")
    return EXIT_OK


def _post_inner_argv(cfg: OpenTweetConfig, config_path: str) -> list[str]:
    topic_file = str(Path(cfg.topic_file).expanduser())
    return [
        launchd_plist.python_executable() if platform.system() != "Windows" else win_schtasks.python_executable(),
        "-m",
        "opentweet.cli",
        "--config",
        str(Path(config_path).resolve()),
        "post",
        "--topic-file",
        topic_file,
    ]


def cmd_schedule_plist(args: argparse.Namespace) -> int:
    _heading("CyClaw OpenTweet Channel -- Generate weekly launchd plist")
    if platform.system() != "Darwin":
        _err("schedule-plist is Darwin-only (writes a macOS launchd plist).")
        return EXIT_ENV
    try:
        cfg = load_opentweet_config(args.config)
    except OpenTweetConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    if not cfg.enabled:
        return _disabled_notice()

    repo_root = Path(__file__).resolve().parent.parent
    inner_argv = _post_inner_argv(cfg, args.config)
    secrets = [(args.token_service, cfg.api_key_env)]
    if args.api_key_service:
        secrets.append((args.api_key_service, cfg.query.api_key_env))
    wrapper = launchd_plist.keychain_wrapper_path(repo_root)
    program_args = launchd_plist.wrap_with_keychain_secrets(inner_argv, secrets, wrapper)

    log_path = str(launchd_plist.logs_dir() / "opentweet.log")
    weekday = args.weekday if args.weekday is not None else cfg.weekday
    hour = args.hour if args.hour is not None else cfg.fire_hour
    minute = args.minute if args.minute is not None else cfg.fire_minute
    document = {
        "Label": _LAUNCHD_LABEL,
        "WorkingDirectory": str(repo_root),
        "ProgramArguments": program_args,
        "StartCalendarInterval": {"Weekday": weekday, "Hour": hour, "Minute": minute},
        "RunAtLoad": False,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }
    path = launchd_plist.plist_path(_LAUNCHD_LABEL)
    launchd_plist.write_plist(document, path)

    _kv("plist", path)
    _kv("weekday", weekday)
    _kv("fire", f"{hour:02d}:{minute:02d}")
    _kv("token Keychain service", args.token_service)
    if args.api_key_service:
        _kv("api-key Keychain service", args.api_key_service)
    print()
    print(f"  Store the key first: macos/cyclaw-keychain-set.sh '{args.token_service}'")
    print(f"  NOT loaded. Run to activate: {launchd_plist.bootstrap_hint(path)}")
    return EXIT_OK


def cmd_schedule_task(args: argparse.Namespace) -> int:
    _heading("CyClaw OpenTweet Channel -- Generate weekly scheduled task")
    if platform.system() != "Windows":
        _err("schedule-task is Windows-only (writes a Task Scheduler XML).")
        return EXIT_ENV
    try:
        cfg = load_opentweet_config(args.config)
    except OpenTweetConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    if not cfg.enabled:
        return _disabled_notice()

    repo_root = Path(__file__).resolve().parent.parent
    inner_argv = _post_inner_argv(cfg, args.config)
    secrets = [(args.token_service, cfg.api_key_env)]
    if args.api_key_service:
        secrets.append((args.api_key_service, cfg.query.api_key_env))
    wrapper = win_schtasks.credman_wrapper_path(repo_root)
    argv = win_schtasks.wrap_with_credman_secrets(inner_argv, secrets, wrapper)
    weekday = args.weekday if args.weekday is not None else cfg.weekday
    hour = args.hour if args.hour is not None else cfg.fire_hour
    minute = args.minute if args.minute is not None else cfg.fire_minute
    path, _launcher = win_schtasks.write_generated_task(
        task_name=_TASK_NAME,
        argv=argv,
        working_directory=str(repo_root),
        triggers=win_schtasks.weekly_calendar_trigger(weekday, hour, minute),
        restart_interval=None,
        restart_count=0,
        execution_time_limit="PT30M",
    )
    _kv("xml", path)
    _kv("weekday", weekday)
    _kv("fire", f"{hour:02d}:{minute:02d}")
    print()
    print(f"  NOT registered. Run to activate: {win_schtasks.register_hint(_TASK_NAME, path)}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m opentweet.cli",
        description="CyClaw OpenTweet X channel -- out-of-band, default-off, audit-logged.",
    )
    parser.add_argument(
        "--config",
        default="config.yaml",
        help="Path to CyClaw config.yaml (default: %(default)s)",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print channel status (no secrets).")
    p_status.set_defaults(func=cmd_status)

    p_test = sub.add_parser("test", help="Run the pre-flight self-test.")
    p_test.set_defaults(func=cmd_test)

    p_post = sub.add_parser("post", help="Generate one post via /query and write a draft (or schedule).")
    p_post.add_argument("--topic", default="", help="Inline topic (overrides --topic-file).")
    p_post.add_argument("--topic-file", default="", help="Topic file (default: opentweet.topic_file).")
    p_post.add_argument(
        "--schedule",
        action="store_true",
        help="Require opentweet.schedule_enabled; job scheduling is config-driven.",
    )
    p_post.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate generation; do not call OpenTweet write.",
    )
    p_post.set_defaults(func=cmd_post)

    def _sched_flags(p: argparse.ArgumentParser) -> None:
        p.add_argument("--token-service", default=_DEFAULT_KEY_SERVICE, help="Keychain/CredMan name for OPENTWEET_API_KEY.")
        p.add_argument("--api-key-service", default="", help="Optional Keychain/CredMan name for CYCLAW_API_KEY.")
        p.add_argument("--weekday", type=int, default=None, help="0/7 Sunday … 6 Saturday (default: config).")
        p.add_argument("--hour", type=int, default=None, help="Fire hour 0-23 (default: config fire_hour).")
        p.add_argument("--minute", type=int, default=None, help="Fire minute 0-59 (default: config fire_minute).")

    p_plist = sub.add_parser("schedule-plist", help="Generate (never load) the macOS weekly LaunchAgent.")
    _sched_flags(p_plist)
    p_plist.set_defaults(func=cmd_schedule_plist)

    p_task = sub.add_parser("schedule-task", help="Generate (never register) the Windows weekly task XML.")
    _sched_flags(p_task)
    p_task.set_defaults(func=cmd_schedule_task)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    return int(func(args))


if __name__ == "__main__":
    sys.exit(main())
