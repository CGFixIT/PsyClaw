"""Command-line entry point: ``python -m telegram.cli <subcommand>``.

Subcommands:

    status       Print channel config (no secrets).
    test         Run the pre-flight self-test.
    send         T1: send one outbound message to an allowlisted chat.
    poll         T2: long-poll inbound messages (requires mode=chat).
    poll-plist   Generate (never load) the macOS launchd plist for T2's
                 KeepAlive poller. Darwin-only. Injects TELEGRAM_BOT_TOKEN
                 via the Keychain wrapper -- never writes the token into
                 the plist.
    health-plist Generate (never load) the macOS launchd plist for T1's
                 periodic /health probe + notify-on-fail. Darwin-only. Same
                 Keychain-wrapper token injection as poll-plist.

Exit codes (aligned with sync/agentic):
    0    success / clean no-op when telegram.enabled is false (status/test)
    2    operation failed
    3    config / environment problem

This module never imports gate.py, graph.py, or mcp_hybrid_server.py.
"""

from __future__ import annotations

import argparse
import getpass
import platform
import shlex
import sys
import warnings
from pathlib import Path

from telegram.config import TelegramConfig, load_telegram_config
from telegram.runner import poll_forever, send_notify
from telegram.selftest import run_self_test
from utils import launchd_plist
from utils.errors import TelegramConfigError, TelegramError, TelegramRefused, TelegramRuntimeError

# Fixed Labels the generated plists own -- match the shipped static templates
# at macos/LaunchAgents/com.cgfixit.cyclaw.telegram-{health,poll}.plist (both
# write to the same well-known paths; the generators are the recommended
# path, the templates stay as hand-editable references/fallbacks).
_POLL_LAUNCHD_LABEL = "com.cgfixit.cyclaw.telegram-poll"
_HEALTH_LAUNCHD_LABEL = "com.cgfixit.cyclaw.telegram-health"
_DEFAULT_TOKEN_SERVICE = "com.cgfixit.cyclaw.telegram-bot-token"  # noqa: S105 -- Keychain service NAME, not a secret
_DEFAULT_HEALTH_INTERVAL_SEC = 300

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3
_PROMPT_HELP = "Read the bot token from a hidden interactive prompt instead of the environment."


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
    for k, v in (getattr(exc, "details", None) or {}).items():
        _err(f"   {k}: {v}")


def _disabled_notice() -> int:
    print("  telegram.enabled is false in config.yaml; nothing to do.")
    print("  Set telegram.enabled: true and non-empty allowed_chat_ids to use this layer.")
    return EXIT_OK


def _prompt_bot_token(cfg: TelegramConfig) -> None:
    if not sys.stdin.isatty():
        raise TelegramConfigError(
            "--prompt-token requires an interactive terminal",
            details={"hint": f"Set {cfg.bot_token_env} for unattended startup."},
        )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            token = getpass.getpass("Telegram bot token: ")
    except (EOFError, getpass.GetPassWarning):
        raise TelegramConfigError(
            "Unable to read the bot token without echo",
            details={"hint": f"Set {cfg.bot_token_env} instead."},
        ) from None
    except KeyboardInterrupt:
        raise TelegramConfigError("Telegram bot token prompt cancelled") from None
    cfg.set_runtime_bot_token(token)


def cmd_status(args: argparse.Namespace) -> int:
    _heading("CyClaw Telegram Channel -- Status")
    try:
        cfg = load_telegram_config(args.config)
    except TelegramConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV

    pub = cfg.to_public_dict()
    _kv("enabled", pub["enabled"])
    _kv("mode", pub["mode"])
    _kv("allowed_chat_ids", pub["allowed_chat_ids"])
    _kv("bot_token_env", pub["bot_token_env"])
    _kv("bot_token_set", pub["bot_token_set"])
    _kv("api_base", pub["api_base"])
    _kv("poll_timeout_sec", pub["poll_timeout_sec"])
    _kv("max_message_chars", pub["max_message_chars"])
    _kv("allow_hybrid_confirm", pub["allow_hybrid_confirm"])
    _kv("hybrid_confirm_ttl_sec", pub["hybrid_confirm_ttl_sec"])
    _kv("media.enabled", pub["media"]["enabled"])
    _kv("media.fsconnect_root", pub["media"]["fsconnect_root"] or "(unset)")
    _kv("media.max_download_bytes", pub["media"]["max_download_bytes"])
    _kv("query.base_url", pub["query"]["base_url"])
    _kv("query.api_key_env", pub["query"]["api_key_env"])
    _kv("query.api_key_set", pub["api_key_set"])
    _kv("query.timeout_sec", pub["query"]["timeout_sec"])
    _kv("rate_limit.max_ops", pub["rate_limit"]["max_ops"])
    _kv("rate_limit.window_s", pub["rate_limit"]["window_seconds"])
    if not cfg.enabled:
        _warn("Layer disabled (enabled: false) — CLI write paths will no-op or refuse.")
    return EXIT_OK


def cmd_test(args: argparse.Namespace) -> int:
    _heading("CyClaw Telegram Channel -- Self-test")
    try:
        cfg = load_telegram_config(args.config)
        if cfg.enabled:
            cfg.resolve_bot_token()
        passed, total, lines = run_self_test(config_path=args.config)
    except TelegramConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    for ln in lines:
        print(ln)
    print(f"\n{passed}/{total} passed")
    return EXIT_OK if passed == total else EXIT_FAIL


def cmd_send(args: argparse.Namespace) -> int:
    _heading("CyClaw Telegram Channel -- Send (T1)")
    try:
        cfg = load_telegram_config(args.config)
    except TelegramConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    if not cfg.enabled:
        return _disabled_notice()

    text = args.text
    if args.body_file:
        try:
            with open(args.body_file, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            _err(f"Could not read --body-file: {exc}")
            return EXIT_ENV
    if not text or not str(text).strip():
        _err("Provide --text or --body-file with non-empty content.")
        return EXIT_ENV

    if args.dry_run:
        if not cfg.is_chat_allowed(args.chat_id):
            _err(f"chat_id {args.chat_id} not in allowed_chat_ids (dry-run)")
            return EXIT_FAIL
        preview = str(text).strip()
        if len(preview) > cfg.max_message_chars:
            preview = preview[: cfg.max_message_chars - 20] + "\n…[truncated]"
        _ok(f"dry-run: would send {len(preview)} chars to chat_id={args.chat_id}")
        print(f"  preview: {preview[:200]!r}{'…' if len(preview) > 200 else ''}")
        return EXIT_OK

    try:
        if args.prompt_token:
            _prompt_bot_token(cfg)
        result = send_notify(cfg, chat_id=args.chat_id, text=str(text))
    except TelegramConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    except TelegramRefused as exc:
        _print_typed_error(exc)
        return EXIT_FAIL
    except TelegramRuntimeError as exc:
        _print_typed_error(exc)
        return EXIT_FAIL
    except TelegramError as exc:
        _print_typed_error(exc)
        return EXIT_FAIL

    msg_id = None
    try:
        msg_id = result.get("result", {}).get("message_id")
    except AttributeError:
        pass
    _ok(f"Sent to chat_id={args.chat_id}" + (f" message_id={msg_id}" if msg_id else ""))
    return EXIT_OK


def cmd_poll(args: argparse.Namespace) -> int:
    _heading("CyClaw Telegram Channel -- Poll (T2)")
    try:
        cfg = load_telegram_config(args.config)
    except TelegramConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    if not cfg.enabled:
        return _disabled_notice()
    if cfg.mode != "chat":
        _err("poll requires telegram.mode: chat (current mode refuses inbound).")
        return EXIT_ENV
    try:
        if args.prompt_token:
            _prompt_bot_token(cfg)
        cfg.resolve_bot_token()
    except TelegramConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV

    max_iter = args.max_iterations if args.max_iterations and args.max_iterations > 0 else None
    _ok(
        f"Long-polling as bot (timeout={cfg.poll_timeout_sec}s); "
        f"allowlisted chats={cfg.allowed_chat_ids}; Ctrl-C to stop."
    )
    try:
        poll_forever(cfg, max_iterations=max_iter)
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        return EXIT_OK
    except TelegramRefused as exc:
        _print_typed_error(exc)
        return EXIT_FAIL
    except TelegramRuntimeError as exc:
        _print_typed_error(exc)
        return EXIT_FAIL
    except TelegramConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    return EXIT_OK


def cmd_poll_plist(args: argparse.Namespace) -> int:
    """Generate (never load) the T2 KeepAlive poller launchd plist.

    Darwin-only. TELEGRAM_BOT_TOKEN (and, optionally, CYCLAW_API_KEY) are
    injected at process-start time via macos/cyclaw-keychain-env.sh -- never
    written into the plist itself. See that script's header for how to store
    the secret first (macos/cyclaw-keychain-set.sh).
    """
    _heading("CyClaw Telegram Channel -- Generate poll (T2) launchd plist")
    if platform.system() != "Darwin":
        _err("poll-plist is Darwin-only (writes a macOS launchd plist).")
        return EXIT_ENV
    try:
        cfg = load_telegram_config(args.config)
    except TelegramConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    if not cfg.enabled:
        return _disabled_notice()
    if cfg.mode != "chat":
        _err("poll-plist requires telegram.mode: chat (current mode refuses inbound).")
        return EXIT_ENV

    repo_root = Path(__file__).resolve().parent.parent
    inner_argv = [
        launchd_plist.python_executable(),
        "-m",
        "telegram.cli",
        "--config",
        str(Path(args.config).resolve()),
        "poll",
    ]
    secrets = [(args.token_service, "TELEGRAM_BOT_TOKEN")]
    if args.api_key_service:
        secrets.append((args.api_key_service, "CYCLAW_API_KEY"))
    wrapper = launchd_plist.keychain_wrapper_path(repo_root)
    program_args = launchd_plist.wrap_with_keychain_secrets(inner_argv, secrets, wrapper)

    log_path = str(launchd_plist.logs_dir() / "telegram-poll.log")
    document = {
        "Label": _POLL_LAUNCHD_LABEL,
        "WorkingDirectory": str(repo_root),
        "ProgramArguments": program_args,
        "KeepAlive": True,
        "ThrottleInterval": 10,
        "RunAtLoad": False,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }

    path = launchd_plist.plist_path(_POLL_LAUNCHD_LABEL)
    launchd_plist.write_plist(document, path)

    _kv("plist", path)
    _kv("token Keychain service", args.token_service)
    if args.api_key_service:
        _kv("api-key Keychain service", args.api_key_service)
    print()
    print(f"  Store the token first: macos/cyclaw-keychain-set.sh '{args.token_service}'")
    print("  Run exactly one poller per bot token (T2's own operator checklist).")
    print(f"  NOT loaded. Run to activate: {launchd_plist.bootstrap_hint(path)}")
    print()
    print("  IMPORTANT ORDER: this agent is KeepAlive, throttled to 10s (launchd's")
    print("  own default). Loading it before the token is stored means the wrapper")
    print("  exits 1 on every start, so launchd relaunches it every 10s indefinitely")
    print("  -- a tight, log-spamming crash loop, not a security issue.")
    print("  Store the token FIRST, then bootstrap.")
    return EXIT_OK


def cmd_health_plist(args: argparse.Namespace) -> int:
    """Generate (never load) the T1 periodic /health-probe launchd plist.

    Darwin-only. Does NOT start gate.py -- probes the already-running
    server's loopback /health via curl and sends a Telegram notify only on
    failure. TELEGRAM_BOT_TOKEN is injected the same Keychain-wrapper way as
    poll-plist.
    """
    _heading("CyClaw Telegram Channel -- Generate health (T1) launchd plist")
    if platform.system() != "Darwin":
        _err("health-plist is Darwin-only (writes a macOS launchd plist).")
        return EXIT_ENV
    try:
        cfg = load_telegram_config(args.config)
    except TelegramConfigError as exc:
        _print_typed_error(exc)
        return EXIT_ENV
    if not cfg.enabled:
        return _disabled_notice()

    chat_id = args.chat_id or cfg.allowed_chat_ids[0]
    if not cfg.is_chat_allowed(chat_id):
        _err(f"chat_id {chat_id} is not in telegram.allowed_chat_ids")
        return EXIT_FAIL
    if args.interval_sec <= 0:
        _err("--interval-sec must be > 0")
        return EXIT_FAIL

    repo_root = Path(__file__).resolve().parent.parent
    py = launchd_plist.python_executable()
    config_arg = shlex.quote(str(Path(args.config).resolve()))
    health_url = shlex.quote(f"{cfg.query.base_url}/health")
    health_cmd = (
        f"curl -sf --max-time 5 {health_url} >/dev/null || "
        f"{shlex.quote(py)} -m telegram.cli --config {config_arg} send "
        f"--chat-id {shlex.quote(chat_id)} "
        '--text "CyClaw /health failed $(date -u +%Y-%m-%dT%H:%MZ)"'
    )
    inner_argv = ["/bin/bash", "-lc", health_cmd]
    wrapper = launchd_plist.keychain_wrapper_path(repo_root)
    program_args = launchd_plist.wrap_with_keychain_secrets(
        inner_argv, [(args.token_service, "TELEGRAM_BOT_TOKEN")], wrapper
    )

    log_path = str(launchd_plist.logs_dir() / "telegram-health.log")
    document = {
        "Label": _HEALTH_LAUNCHD_LABEL,
        "WorkingDirectory": str(repo_root),
        "ProgramArguments": program_args,
        "StartInterval": args.interval_sec,
        "RunAtLoad": False,
        "StandardOutPath": log_path,
        "StandardErrorPath": log_path,
    }

    path = launchd_plist.plist_path(_HEALTH_LAUNCHD_LABEL)
    launchd_plist.write_plist(document, path)

    _kv("plist", path)
    _kv("chat_id", chat_id)
    _kv("interval_sec", args.interval_sec)
    _kv("token Keychain service", args.token_service)
    print()
    print(f"  Store the token first: macos/cyclaw-keychain-set.sh '{args.token_service}'")
    print(f"  NOT loaded. Run to activate: {launchd_plist.bootstrap_hint(path)}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m telegram.cli",
        description="CyClaw Telegram channel -- out-of-band, allowlisted, audit-logged.",
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

    p_send = sub.add_parser("send", help="T1: send one outbound message.")
    p_send.add_argument("--chat-id", required=True, help="Telegram chat id (must be allowlisted).")
    p_send.add_argument("--text", default="", help="Message text.")
    p_send.add_argument("--body-file", default="", help="Read message text from file (overrides --text).")
    p_send.add_argument("--prompt-token", action="store_true", help=_PROMPT_HELP)
    p_send.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate allowlist and print preview; do not call Telegram.",
    )
    p_send.set_defaults(func=cmd_send)

    p_poll = sub.add_parser("poll", help="T2: long-poll inbound (mode=chat only).")
    p_poll.add_argument("--prompt-token", action="store_true", help=_PROMPT_HELP)
    p_poll.add_argument(
        "--max-iterations",
        type=int,
        default=0,
        help="Stop after N getUpdates batches (0 = forever). For tests.",
    )
    p_poll.set_defaults(func=cmd_poll)

    p_poll_plist = sub.add_parser(
        "poll-plist",
        help="Generate (never load) the macOS launchd plist for the T2 poller. Darwin-only.",
    )
    p_poll_plist.add_argument(
        "--token-service", default=_DEFAULT_TOKEN_SERVICE,
        help="Keychain service name holding the bot token (default: %(default)s).",
    )
    p_poll_plist.add_argument(
        "--api-key-service", default="",
        help="Optional Keychain service name holding CYCLAW_API_KEY (unset: not injected).",
    )
    p_poll_plist.set_defaults(func=cmd_poll_plist)

    p_health_plist = sub.add_parser(
        "health-plist",
        help="Generate (never load) the macOS launchd plist for the T1 health probe. Darwin-only.",
    )
    p_health_plist.add_argument("--chat-id", default="", help="Notify target (default: first allowed_chat_ids entry).")
    p_health_plist.add_argument(
        "--interval-sec", type=int, default=_DEFAULT_HEALTH_INTERVAL_SEC,
        help="Seconds between probes (default: %(default)s).",
    )
    p_health_plist.add_argument(
        "--token-service", default=_DEFAULT_TOKEN_SERVICE,
        help="Keychain service name holding the bot token (default: %(default)s).",
    )
    p_health_plist.set_defaults(func=cmd_health_plist)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    func = args.func
    return int(func(args))


if __name__ == "__main__":
    sys.exit(main())
