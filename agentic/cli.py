"""Command-line entry point: ``python -m agentic.cli <subcommand>``.

Subcommands:

    status         Print agentic config + gh availability + registry summary.
    context        Fetch read-only GitHub context (--pr N | --issue N | --repo).
    propose-skill  Preview a skills-registry change (never writes).
    apply-skill    Apply a skills-registry change (governed; needs --reason).
    test           Run the pre-flight self-test.

Exit codes:
    0    success (also the clean no-op when agentic.enabled is false)
    2    operation failed (gh error, registry error)
    3    config / environment problem (gh missing, config invalid)
    4    a write was refused by the gate

This module never imports gate.py, graph.py, or mcp_hybrid_server.py.
"""

from __future__ import annotations

import argparse
import json
import sys

from agentic.config import AgenticConfig, load_agentic_config
from utils.errors import (
    AgenticConfigError,
    AgenticError,
    GhNotInstalledError,
    GhVersionError,
    PromptInjectionError,
    SkillRegistryError,
)
from utils.logger import _get_config

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3
EXIT_REFUSED = 4


def _heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def _kv(key: str, value: object) -> None:
    print(f"  {key:.<22} {value}")


def _err(text: str) -> None:
    print(f"  [ERR ] {text}", file=sys.stderr)


def _ok(text: str) -> None:
    print(f"  [OK  ] {text}")


def _load(args: argparse.Namespace) -> AgenticConfig | None:
    try:
        return load_agentic_config(args.config)
    except AgenticConfigError as exc:
        _err(f"Config error: {exc.message}")
        for k, v in (exc.details or {}).items():
            _err(f"   {k}: {v}")
        return None


def _disabled_noop() -> int:
    _heading("Agentic layer disabled")
    print("  agentic.enabled is false in config.yaml; nothing to do.")
    print("  Set agentic.enabled: true to use this layer.")
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV

    _heading("CyClaw Agentic Status")
    _kv("enabled", getattr(cfg, "enabled", False))
    _kv("repo", cfg.repo)
    _kv("mode", cfg.mode)
    _kv("writes_enabled", cfg.writes_enabled)
    _kv("gh_min_version", cfg.gh_min_version)
    _kv("registry_path", cfg.registry_path)
    _kv("allowed_read_ops", ", ".join(cfg.allowed_read_ops))

    from agentic.gh_client import check_gh_version
    try:
        v = check_gh_version(min_version=cfg.gh_min_tuple)
        _ok(f"gh {v[0]}.{v[1]}.{v[2]}")
    except (GhNotInstalledError, GhVersionError) as exc:
        _err(exc.message)

    try:
        from agentic.registry import SkillRegistry
        reg = SkillRegistry(_get_config(args.config), cfg)
        _kv("registry_version", reg.version())
        _kv("skills", ", ".join(reg.list_skills()) or "(none)")
    except SkillRegistryError as exc:
        _err(f"Registry: {exc.message}")
    return EXIT_OK


def cmd_context(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()

    from agentic import context
    # Pass the full config so the injection scan over GitHub-sourced text sees the
    # operator's banned_patterns, not just the OWASP baseline.
    app_cfg = _get_config(args.config)
    try:
        if args.pr is not None:
            bundle = context.fetch_pr_context(cfg, args.pr, include_diff=not args.no_diff, app_cfg=app_cfg)
        elif args.issue is not None:
            bundle = context.fetch_issue_context(cfg, args.issue, app_cfg=app_cfg)
        else:
            bundle = context.fetch_repo_context(cfg, app_cfg=app_cfg)
    except (GhNotInstalledError, GhVersionError) as exc:
        _err(exc.message)
        return EXIT_ENV
    except AgenticError as exc:
        _err(exc.message)
        return EXIT_FAIL

    print(json.dumps(bundle, indent=2, default=str))
    return EXIT_OK


def _read_body(args: argparse.Namespace) -> str:
    if args.body_file:
        with open(args.body_file, encoding="utf-8") as f:
            return f.read()
    return args.body or ""


def cmd_propose_skill(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    # Honor the master switch: agentic.enabled=false means "the layer is off", so a
    # skills-registry op must not run while the operator believes it is inert
    # (matching cmd_context). propose is read-only, but gating it keeps the switch
    # consistent across all registry operations.
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()
    spec = {"name": args.name, "description": args.desc, "body": _read_body(args)}
    from agentic.registry import SkillRegistry
    try:
        reg = SkillRegistry(_get_config(args.config), cfg)
        proposal = reg.propose_skill(spec, reason=args.reason or "")
    except SkillRegistryError as exc:
        _err(exc.message)
        return EXIT_FAIL
    print(json.dumps(proposal, indent=2))
    return EXIT_OK


def cmd_apply_skill(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    # Master switch first: a registry WRITE must never run while agentic.enabled is
    # false. Previously apply-skill ignored the flag, so a confirmed write reached
    # the registry JSON even with the layer "disabled" (also reachable via the
    # API-key-gated POST /ops/agentic console) — a leaky off-switch. The per-write
    # governance (reason + confirm + injection gate) still applies once enabled.
    if not getattr(cfg, "enabled", False):
        return _disabled_noop()
    if not args.confirm:
        _err("apply-skill requires --confirm")
        return EXIT_REFUSED
    spec = {"name": args.name, "description": args.desc, "body": _read_body(args)}
    from agentic.registry import SkillRegistry
    try:
        reg = SkillRegistry(_get_config(args.config), cfg)
        result = reg.apply_skill(spec, reason=args.reason or "")
    except PromptInjectionError as exc:
        _err(f"Injection blocked: {exc.message}")
        return EXIT_REFUSED
    except SkillRegistryError as exc:
        _err(exc.message)
        return EXIT_FAIL
    print(json.dumps(result, indent=2))
    return EXIT_OK


def cmd_test(args: argparse.Namespace) -> int:
    from agentic.selftest import run_self_test
    passed, total, lines = run_self_test(args.config)
    _heading(f"Self-test: {passed}/{total} passed")
    for line in lines:
        print(line)
    return EXIT_OK if passed == total else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m agentic.cli",
        description="CyClaw agentic layer -- read-only GitHub context + governed skills, out-of-band.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print agentic config + gh + registry status.")
    p_status.set_defaults(func=cmd_status)

    p_ctx = sub.add_parser("context", help="Fetch read-only GitHub context.")
    g = p_ctx.add_mutually_exclusive_group()
    g.add_argument("--pr", type=int, help="Fetch a PR's metadata + diff.")
    g.add_argument("--issue", type=int, help="Fetch an issue's metadata.")
    g.add_argument("--repo", action="store_true", help="Fetch a repo overview (default).")
    p_ctx.add_argument("--no-diff", action="store_true", help="Omit the PR diff.")
    p_ctx.set_defaults(func=cmd_context)

    p_prop = sub.add_parser("propose-skill", help="Preview a skills-registry change (no write).")
    p_prop.add_argument("--name", required=True)
    p_prop.add_argument("--desc", required=True)
    p_prop.add_argument("--body")
    p_prop.add_argument("--body-file")
    p_prop.add_argument("--reason", help="Advisory; required at apply time.")
    p_prop.set_defaults(func=cmd_propose_skill)

    p_apply = sub.add_parser("apply-skill", help="Apply a skills-registry change (governed).")
    p_apply.add_argument("--name", required=True)
    p_apply.add_argument("--desc", required=True)
    p_apply.add_argument("--body")
    p_apply.add_argument("--body-file")
    p_apply.add_argument("--reason", required=True, help="Human reason string (required).")
    p_apply.add_argument("--confirm", action="store_true", help="Required to actually write.")
    p_apply.set_defaults(func=cmd_apply_skill)

    p_test = sub.add_parser("test", help="Run the pre-flight self-test.")
    p_test.set_defaults(func=cmd_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
