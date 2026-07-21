"""Command-line entry point: ``python -m guardrails.cli <subcommand>``.

Subcommands:

    status   Print guardrails config + NeMo-config presence + dep availability.
    check    Probe the guardrails path over a query string. With guardrails
             enabled AND nemoguardrails installed this performs a LIVE
             generation through the configured local LLM; otherwise it runs
             the offline heuristic floor (no LLM, no NeMo needed).
    metrics  Summarize the guardrail metrics stream (logs/guardrails.jsonl).
    test     Run the pre-flight self-test.

Exit codes:
    0    success (also the clean no-op when guardrails.enabled is false)
    2    operation failed
    3    config / environment problem (config invalid)

This module never imports gate.py, graph.py, or mcp_hybrid_server.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from guardrails.config import GuardrailsConfig, load_guardrails_config
from guardrails.errors import GuardrailsConfigError

EXIT_OK = 0
EXIT_FAIL = 2
EXIT_ENV = 3


def _heading(text: str) -> None:
    print(f"\n{text}\n{'-' * len(text)}")


def _kv(key: str, value: object) -> None:
    print(f"  {key:.<26} {value}")


def _err(text: str) -> None:
    print(f"  [ERR ] {text}", file=sys.stderr)


def _ok(text: str) -> None:
    print(f"  [OK  ] {text}")


def _load(args: argparse.Namespace) -> GuardrailsConfig | None:
    try:
        return load_guardrails_config(args.config)
    except GuardrailsConfigError as exc:
        _err(f"Config error: {exc.message}")
        for k, v in (exc.details or {}).items():
            _err(f"   {k}: {v}")
        return None


def cmd_status(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    from guardrails.integration import NEMO_AVAILABLE

    _heading("CyClaw Guardrails Status")
    _kv("enabled", cfg.enabled)
    _kv("engine", cfg.engine)
    _kv("base_url", cfg.base_url)
    _kv("model", cfg.model)
    _kv("nemo_config_dir", cfg.nemo_config_dir)
    _kv("nemo_config_present", cfg.nemo_config_present)
    _kv("metrics_path", cfg.metrics_path)
    _kv("hallucination_threshold", cfg.hallucination_threshold)
    _kv("input_rails", ", ".join(cfg.input_rails))
    _kv("output_rails", ", ".join(cfg.output_rails))
    _kv("topical_rails", ", ".join(cfg.topical_rails))
    if NEMO_AVAILABLE:
        _ok("nemoguardrails installed (live rails available)")
    else:
        _err("nemoguardrails NOT installed (skeleton degrades to offline heuristics)")
    if getattr(cfg, "_unknown_keys", None):
        _err(f"unknown guardrails keys (typos?): {cfg._unknown_keys}")
    return EXIT_OK


def cmd_check(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    from guardrails.integration import safe_generate

    result = asyncio.run(safe_generate(args.query, context=args.context or "", cfg=cfg))
    print(json.dumps(result, indent=2, default=str))
    return EXIT_OK


def cmd_metrics(args: argparse.Namespace) -> int:
    cfg = _load(args)
    if cfg is None:
        return EXIT_ENV
    from guardrails.metrics import print_metrics

    print_metrics(cfg.metrics_path)
    return EXIT_OK


def cmd_test(args: argparse.Namespace) -> int:
    from guardrails.selftest import run_self_test

    passed, total, lines = run_self_test(args.config)
    _heading(f"Self-test: {passed}/{total} passed")
    for line in lines:
        print(line)
    return EXIT_OK if passed == total else EXIT_FAIL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m guardrails.cli",
        description="CyClaw NeMo guardrails layer -- out-of-band, opt-in, soul-aware rails.",
    )
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml (default: %(default)s)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="Print guardrails config + dependency status.")
    p_status.set_defaults(func=cmd_status)

    p_check = sub.add_parser(
        "check",
        help="Probe the guardrails path over a query. Live generation when guardrails "
        "+ nemoguardrails are enabled; offline heuristic floor otherwise.",
    )
    p_check.add_argument("query", help="The user query to evaluate.")
    p_check.add_argument("--context", help="Optional retrieved-context string to ground against.")
    p_check.set_defaults(func=cmd_check)

    p_metrics = sub.add_parser("metrics", help="Summarize the guardrail metrics stream.")
    p_metrics.set_defaults(func=cmd_metrics)

    p_test = sub.add_parser("test", help="Run the pre-flight self-test.")
    p_test.set_defaults(func=cmd_test)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
