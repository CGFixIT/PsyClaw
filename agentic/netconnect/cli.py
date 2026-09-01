"""CLI for passive, explicitly scoped LAN inventory.

Subcommands: ``status``, ``self``, ``arp``, and ``test``.
"""

from __future__ import annotations

import argparse
import json
import sys

from agentic.netconnect.config import NetConnectConfig, load_netconnect_config
from utils.errors import (
    NetCommandNotInstalledError,
    NetConnectConfigError,
    NetConnectError,
)
from utils.logger import _get_config, setup_logging

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3


def _heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def _kv(key: str, value: object) -> None:
    print(f"  {key:.<22} {value}")


def _err(text: str) -> None:
    print(f"  [ERR ] {text}", file=sys.stderr)


def _load(args: argparse.Namespace) -> NetConnectConfig | None:
    try:
        return load_netconnect_config(args.config)
    except NetConnectConfigError as exc:
        _err(f"Config error: {exc.message}")
        return None


def _disabled_noop() -> int:
    _heading("Network connector disabled")
    print("  netconnect.enabled is false in config.yaml; nothing to do.")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    net_cfg = _load(args)
    if net_cfg is None:
        return EXIT_ENV
    _heading("CyClaw Passive Network Connector Status")
    _kv("enabled", net_cfg.enabled)
    _kv("allowed_cidrs", ", ".join(net_cfg.allowed_cidrs) or "(none)")
    _kv("allowed_net_ops", ", ".join(net_cfg.allowed_net_ops))
    _kv("command_timeout_sec", net_cfg.command_timeout_sec)
    _kv("max_neighbors", net_cfg.max_neighbors)
    _kv("active_scanning", False)
    # Same operator-facing typo surfacing as guardrails/cli.py's cmd_status:
    # load_netconnect_config() drops unrecognized keys onto _unknown_keys, and
    # without this line a typo'd key silently runs on the default.
    if getattr(net_cfg, "_unknown_keys", None):
        _err(f"unknown netconnect keys (typos?): {net_cfg._unknown_keys}")
    return EXIT_OK


def _run(args: argparse.Namespace, op: str) -> int:
    net_cfg = _load(args)
    if net_cfg is None:
        return EXIT_ENV
    if not net_cfg.enabled:
        return _disabled_noop()
    from agentic.netconnect import context

    try:
        result = context.run_op(
            _get_config(args.config),
            net_cfg,
            op,
            config_path=args.config,
        )
    except NetCommandNotInstalledError as exc:
        _err(exc.message)
        return EXIT_ENV
    except NetConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL
    print(json.dumps(result, indent=2, default=str))
    return EXIT_OK


def cmd_self(args: argparse.Namespace) -> int:
    return _run(args, "self")


def cmd_arp(args: argparse.Namespace) -> int:
    return _run(args, "arp")


def cmd_test(args: argparse.Namespace) -> int:
    from agentic.netconnect.selftest import run_self_test

    passed, total, lines = run_self_test(args.config)
    _heading(f"Self-test: {passed}/{total} passed")
    for line in lines:
        print(line)
    return EXIT_OK if passed == total else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic.netconnect.cli",
        description="CyClaw passive LAN inventory -- out-of-band, disabled by default.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_text, func in (
        ("status", "Print passive network connector status.", cmd_status),
        ("self", "Show best-effort local host inventory without probing.", cmd_self),
        ("arp", "Read and scope-filter the existing neighbor cache.", cmd_arp),
        ("test", "Run the offline pre-flight self-test.", cmd_test),
    ):
        command = sub.add_parser(name, help=help_text)
        command.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Every agentic.* logger (this module's own included) has propagated
    # only to stderr's last-resort handler until this call: none of these
    # entrypoints ran setup_logging, so nothing durable ever saw them,
    # regardless of config.yaml's logging.log_file. setup_logging attaches
    # the shared file handler that captures every non-cyclaw.* logger too
    # (utils/logger.py's _capture_third_party) -- this call is what makes
    # that reach agentic.* records, one process at a time.
    setup_logging(_get_config(args.config))
    try:
        return int(args.func(args))
    except NetConnectConfigError as exc:
        _err(f"Config error: {exc.message}")
        return EXIT_ENV
    except NetCommandNotInstalledError as exc:
        _err(exc.message)
        return EXIT_ENV
    except NetConnectError as exc:
        _err(exc.message)
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
