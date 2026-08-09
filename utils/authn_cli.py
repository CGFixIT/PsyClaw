"""``cyclaw-user``: local-only account / session-adjacent / device-token admin.

Covers every account after the first (docs/AUTHENTICATION_DESIGN.md §10.4's
bootstrap decision -- an auto-generated one-time password on first boot --
creates only the first, "admin"). There is no HTTP route for any of this: the
"local-only" requirement in §10.4 is satisfied by construction, not by a
runtime check, because this is a plain argparse CLI with no server component
to reach it through.

Usage::

    python -m utils.authn_cli add <username>
    python -m utils.authn_cli list
    python -m utils.authn_cli disable <username>
    python -m utils.authn_cli enable <username>
    python -m utils.authn_cli passwd <username>
    python -m utils.authn_cli token create <username> <label>
    python -m utils.authn_cli token list <username>
    python -m utils.authn_cli token revoke <username> <label>

Or, once installed, the same subcommands via the ``cyclaw-user`` console
script. Every subcommand that takes a password accepts ``--password`` for
scripting, but omitting it and answering the interactive prompt (``getpass``,
not echoed, never in shell history or `ps`) is the recommended path.

Exit codes::

    0   success
    2   operation failed (duplicate/unknown user, password policy violation)
    3   config / environment problem (config unreadable, store unopenable)
"""

from __future__ import annotations

# Same reasoning as every other CyClaw entry point: this must precede the
# heavy imports below so no telemetry-emitting library latches config first.
from utils.telemetry_kill import apply_telemetry_kill

apply_telemetry_kill()

import argparse  # noqa: E402 - must follow the telemetry kill above
import getpass  # noqa: E402 - must follow the telemetry kill above
import sys  # noqa: E402 - must follow the telemetry kill above
from pathlib import Path  # noqa: E402 - must follow the telemetry kill above

import yaml  # noqa: E402 - must follow the telemetry kill above

from utils.authn import PasswordPolicyError  # noqa: E402 - must follow the telemetry kill above
from utils.authn_manager import AuthManager  # noqa: E402 - must follow the telemetry kill above
from utils.errors import AuthConfigError, AuthError  # noqa: E402 - must follow the telemetry kill above

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3

# This file lives at utils/authn_cli.py, one level below the repo root --
# same anchoring as retrieval/clear_cache.py / metrics.py, and for the same
# reason: a bare relative "config.yaml" default must not depend on cwd.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _resolve_config_path(config_path: str) -> Path:
    path = Path(config_path).expanduser()
    return path if path.is_absolute() else _REPO_ROOT / path


def load_config(config_path: str) -> dict:
    """Read and parse ``config_path``. Raises AuthConfigError on any failure."""
    resolved = _resolve_config_path(config_path)
    try:
        with open(resolved, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
    except OSError as exc:
        raise AuthConfigError(f"could not read config {config_path!r}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise AuthConfigError(f"invalid YAML in {config_path!r}: {exc}") from exc
    if not isinstance(cfg, dict):
        raise AuthConfigError(f"config {config_path!r} did not parse to a mapping")
    return cfg


def _prompt_password(*, confirm: bool = True) -> str:
    password = getpass.getpass("Password: ")
    if confirm:
        again = getpass.getpass("Confirm password: ")
        if password != again:
            raise PasswordPolicyError("passwords did not match")
    return password


def cmd_add(manager: AuthManager, args: argparse.Namespace) -> int:
    password = args.password or _prompt_password()
    username = manager.create_user(args.username, password)
    print(f"created user: {username}")
    return EXIT_OK


def cmd_list(manager: AuthManager, _args: argparse.Namespace) -> int:
    users = manager.list_users()
    if not users:
        print("(no users)")
        return EXIT_OK
    for u in users:
        state = "disabled" if u.disabled else "enabled"
        locked = " LOCKED" if u.locked_until_ts else ""
        print(f"{u.username}\t{state}{locked}\tfailed_count={u.failed_count}")
    return EXIT_OK


def cmd_disable(manager: AuthManager, args: argparse.Namespace) -> int:
    manager.disable_user(args.username)
    print(f"disabled: {args.username}")
    return EXIT_OK


def cmd_enable(manager: AuthManager, args: argparse.Namespace) -> int:
    manager.enable_user(args.username)
    print(f"enabled: {args.username}")
    return EXIT_OK


def cmd_passwd(manager: AuthManager, args: argparse.Namespace) -> int:
    password = args.password or _prompt_password()
    manager.set_password(args.username, password)
    print(f"password updated: {args.username}")
    return EXIT_OK


def cmd_token_create(manager: AuthManager, args: argparse.Namespace) -> int:
    token = manager.create_device_token(args.username, args.label)
    print("Save this token now -- it will not be shown again:")
    print(token)
    return EXIT_OK


def cmd_token_list(manager: AuthManager, args: argparse.Namespace) -> int:
    tokens = manager.list_device_tokens(args.username)
    if not tokens:
        print("(no tokens)")
        return EXIT_OK
    for t in tokens:
        state = "revoked" if t.revoked else "active"
        print(f"{t.label}\t{state}")
    return EXIT_OK


def cmd_token_revoke(manager: AuthManager, args: argparse.Namespace) -> int:
    revoked = manager.revoke_device_token(args.username, args.label)
    if not revoked:
        print(f"no active token found: {args.username}/{args.label}", file=sys.stderr)
        return EXIT_FAIL
    print(f"revoked: {args.username}/{args.label}")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cyclaw-user",
        description="Manage CyClaw per-user accounts, local-only (docs/AUTHENTICATION_DESIGN.md).",
    )
    parser.add_argument(
        "--config", default="config.yaml",
        help="Path to CyClaw config.yaml (default: %(default)s)",
    )

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add", help="Create a new account.")
    p_add.add_argument("username")
    p_add.add_argument("--password", help="Omit to be prompted (recommended).")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="List accounts.")
    p_list.set_defaults(func=cmd_list)

    p_disable = sub.add_parser("disable", help="Disable an account.")
    p_disable.add_argument("username")
    p_disable.set_defaults(func=cmd_disable)

    p_enable = sub.add_parser("enable", help="Re-enable a disabled account.")
    p_enable.add_argument("username")
    p_enable.set_defaults(func=cmd_enable)

    p_passwd = sub.add_parser("passwd", help="Change an account's password.")
    p_passwd.add_argument("username")
    p_passwd.add_argument("--password", help="Omit to be prompted.")
    p_passwd.set_defaults(func=cmd_passwd)

    p_token = sub.add_parser("token", help="Manage per-device bearer tokens.")
    token_sub = p_token.add_subparsers(dest="token_cmd", required=True)

    p_token_create = token_sub.add_parser("create", help="Mint a new device token.")
    p_token_create.add_argument("username")
    p_token_create.add_argument("label")
    p_token_create.set_defaults(func=cmd_token_create)

    p_token_list = token_sub.add_parser("list", help="List an account's device tokens.")
    p_token_list.add_argument("username")
    p_token_list.set_defaults(func=cmd_token_list)

    p_token_revoke = token_sub.add_parser("revoke", help="Revoke a device token by label.")
    p_token_revoke.add_argument("username")
    p_token_revoke.add_argument("label")
    p_token_revoke.set_defaults(func=cmd_token_revoke)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except AuthConfigError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return EXIT_ENV

    try:
        manager = AuthManager(cfg)
    except Exception as exc:
        print(f"Error: could not open the auth store: {exc}", file=sys.stderr)
        return EXIT_ENV

    try:
        return int(args.func(manager, args))
    except PasswordPolicyError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return EXIT_FAIL
    except AuthError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return EXIT_FAIL
    finally:
        manager.close()


if __name__ == "__main__":
    sys.exit(main())
