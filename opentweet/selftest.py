"""Operator-facing pre-flight self-test for ``python -m opentweet.cli test``.

Does NOT contact OpenTweet or CyClaw. Validates config load, loopback URL,
and that the package stays free of request-path imports (static).
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import cast

from opentweet.config import load_opentweet_config
from utils.errors import OpenTweetConfigError
from utils.selftest import fail, finalize, ok, skip

REPO_ROOT = Path(__file__).resolve().parent.parent
PKG_ROOT = Path(__file__).resolve().parent


def run_self_test(config_path: str = "config.yaml") -> tuple[int, int, list[str]]:
    results: list[tuple[bool, str]] = []

    try:
        cfg = load_opentweet_config(config_path)
        results.append(ok("01. Config loads and validates"))
    except OpenTweetConfigError as exc:
        results.append(fail("01. Config loads and validates", exc.message))
        for n in range(2, 6):
            results.append(skip(f"{n:02d}. (skipped -- no config)", "config invalid"))
        return cast(tuple[int, int, list[str]], finalize(results))

    host_ok = any(h in cfg.query.base_url for h in ("127.0.0.1", "localhost", "[::1]"))
    results.append(
        ok("02. query.base_url is loopback")
        if host_ok
        else fail("02. query.base_url is loopback", cfg.query.base_url)
    )

    if not cfg.enabled:
        results.append(ok("03. disabled layer may have empty topic_file"))
    elif cfg.topic_file:
        results.append(ok("03. enabled with topic_file set"))
    else:
        results.append(fail("03. enabled requires topic_file", "empty topic_file"))

    if cfg.api_base.startswith("https://"):
        results.append(ok("04. api_base is https"))
    else:
        results.append(fail("04. api_base is https", cfg.api_base))

    forbidden = {"gate", "gate_ops", "gate_auth", "gate_memory", "graph", "mcp_hybrid_server"}
    leaked_files: list[str] = []
    for py in PKG_ROOT.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    names.add(alias.name.split(".", 1)[0])
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names.add(node.module.split(".", 1)[0])
        hit = forbidden & names
        if hit:
            leaked_files.append(f"{py.relative_to(REPO_ROOT)}:{sorted(hit)}")
    results.append(
        ok("05. package does not import request-path modules")
        if not leaked_files
        else fail("05. package does not import request-path modules", "; ".join(leaked_files))
    )

    return cast(tuple[int, int, list[str]], finalize(results))
