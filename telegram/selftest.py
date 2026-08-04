"""Operator-facing pre-flight self-test for ``python -m telegram.cli test``.

Does NOT contact Telegram or CyClaw by default. Validates config load, gates,
URL hygiene, and that the package stays free of request-path imports (static).
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import cast

from telegram.config import load_telegram_config
from utils.errors import TelegramConfigError
from utils.selftest import fail, finalize, ok, skip

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_ROOT = Path(__file__).resolve().parent


def run_self_test(config_path: str = "config.yaml") -> tuple[int, int, list[str]]:
    results: list[tuple[bool, str]] = []

    # 01. Config loads.
    try:
        cfg = load_telegram_config(config_path)
        results.append(ok("01. Config loads and validates"))
    except TelegramConfigError as exc:
        results.append(fail("01. Config loads and validates", exc.message))
        for n in range(2, 8):
            results.append(skip(f"{n:02d}. (skipped -- no config)", "config invalid"))
        return cast(tuple[int, int, list[str]], finalize(results))

    # 02. Loopback query URL.
    host_ok = any(h in cfg.query.base_url for h in ("127.0.0.1", "localhost", "[::1]"))
    if host_ok:
        results.append(ok("02. query.base_url is loopback"))
    else:
        results.append(fail("02. query.base_url is loopback", cfg.query.base_url))

    # 03. Mode is known.
    if cfg.mode in ("notify", "chat"):
        results.append(ok(f"03. mode is valid ({cfg.mode})"))
    else:
        results.append(fail("03. mode is valid", cfg.mode))

    # 04. Enabled implies non-empty allowlist (enforced in config; re-check).
    if not cfg.enabled:
        results.append(ok("04. disabled layer may have empty allowlist"))
    elif cfg.allowed_chat_ids:
        results.append(ok(f"04. enabled with {len(cfg.allowed_chat_ids)} allowlisted chat(s)"))
    else:
        results.append(fail("04. enabled requires allowlist", "empty allowed_chat_ids"))

    # 05. Disabled mode needs no secret; enabled pre-flight requires the
    # configured environment variable to contain a non-empty token.
    if not cfg.enabled:
        results.append(ok("05. bot token not required while disabled"))
    elif os.environ.get(cfg.bot_token_env, "").strip():
        results.append(ok(f"05. bot token is set via {cfg.bot_token_env}"))
    else:
        results.append(fail("05. bot token is set", f"{cfg.bot_token_env} is unset or empty"))

    # 06. Package files do not import gate/graph/mcp (static AST).
    forbidden = {"gate", "gate_ops", "graph", "mcp_hybrid_server"}
    leaked_files: list[str] = []
    for py in PKG_ROOT.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".")[0])
        hit = forbidden & names
        if hit:
            leaked_files.append(f"{py.relative_to(REPO_ROOT)}:{sorted(hit)}")
    if not leaked_files:
        results.append(ok("06. telegram/ does not import request-path modules"))
    else:
        results.append(fail("06. telegram/ does not import request-path modules", "; ".join(leaked_files)))

    # 07. Hybrid auto-confirm is off by default.
    if cfg.allow_hybrid_confirm is False:
        results.append(ok("07. allow_hybrid_confirm is false (T3 not armed)"))
    else:
        results.append(
            skip(
                "07. allow_hybrid_confirm is false (T3 not armed)",
                "operator set true — T3 UX still not implemented in skeleton",
            )
        )

    return cast(tuple[int, int, list[str]], finalize(results))


if __name__ == "__main__":
    p, t, out = run_self_test()
    for ln in out:
        print(ln)
    print(f"\n{p}/{t} passed")
    raise SystemExit(0 if p == t else 1)
