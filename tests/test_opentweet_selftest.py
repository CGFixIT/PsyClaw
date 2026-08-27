"""Unit tests for opentweet/selftest.py — the `python -m opentweet.cli test`
pre-flight.

``run_self_test`` was the one opentweet module the suite never executed. It is
self-contained (config load, loopback + https checks, and a static AST scan that
the package imports no request-path module — the I6 isolation guard for
opentweet). This exercises it directly: the fully-passing run against the repo's
shipped config, and the invalid-config short-circuit. No network, no CyClaw
server.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from opentweet.selftest import run_self_test
from utils.logger import reset_config_cache

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestRunSelfTestShippedConfig:
    def test_shipped_config_passes_all_checks(self):
        reset_config_cache()
        try:
            passed, total, lines = run_self_test(str(_REPO_ROOT / "config.yaml"))
        finally:
            reset_config_cache()

        assert total == 5
        assert passed == 5, "\n".join(lines)
        assert not any("[FAIL]" in ln for ln in lines)
        # Check 05 is the I6 static isolation guard; it must be present and green.
        assert any("05" in ln and "request-path" in ln and "[OK" in ln
                   for ln in lines), "\n".join(lines)


class TestRunSelfTestInvalidConfig:
    def test_invalid_config_fails_first_and_skips_rest(self, tmp_path):
        # A non-boolean `enabled` fails config validation on load, so check 01
        # FAILs and checks 02-05 are skipped rather than crashing the run.
        cfg = {
            "logging": {"audit_file": str(tmp_path / "audit.jsonl")},
            "opentweet": {"enabled": "yes-please"},
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        reset_config_cache()
        try:
            passed, total, lines = run_self_test(str(path))
        finally:
            reset_config_cache()

        assert total == 5
        assert "[FAIL]" in lines[0]
        assert "01" in lines[0]
        assert all("[SKIP]" in ln for ln in lines[1:])
