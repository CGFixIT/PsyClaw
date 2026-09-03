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


class TestRunSelfTestFailBranches:
    def test_enabled_with_topic_file_ok_branch(self, tmp_path, monkeypatch):
        from opentweet.config import load_opentweet_config

        topic = tmp_path / "topics.md"
        topic.write_text("# topics\n", encoding="utf-8")
        cfg = {
            "logging": {"audit_file": str(tmp_path / "audit.jsonl")},
            "opentweet": {"enabled": False},
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        def _enabled(config_path: str = "config.yaml"):
            loaded = load_opentweet_config(config_path)
            loaded.enabled = True
            loaded.topic_file = str(topic)
            return loaded

        monkeypatch.setattr("opentweet.selftest.load_opentweet_config", _enabled)
        reset_config_cache()
        try:
            _p, _t, lines = run_self_test(str(path))
        finally:
            reset_config_cache()
        assert any("03. enabled with topic_file set" in ln and "[OK" in ln for ln in lines)

    def test_topic_api_base_and_import_leak_fail(self, tmp_path, monkeypatch):
        from opentweet.config import load_opentweet_config

        cfg = {
            "logging": {"audit_file": str(tmp_path / "audit.jsonl")},
            "opentweet": {"enabled": False},
        }
        path = tmp_path / "config.yaml"
        path.write_text(yaml.safe_dump(cfg), encoding="utf-8")

        def _enabled_no_topic(config_path: str = "config.yaml"):
            loaded = load_opentweet_config(config_path)
            loaded.enabled = True
            loaded.topic_file = ""
            return loaded

        monkeypatch.setattr("opentweet.selftest.load_opentweet_config", _enabled_no_topic)
        reset_config_cache()
        try:
            _p, _t, lines = run_self_test(str(path))
        finally:
            reset_config_cache()
        assert any("03. enabled requires topic_file" in ln and "[FAIL]" in ln for ln in lines)

        def _bad_api(config_path: str = "config.yaml"):
            loaded = load_opentweet_config(config_path)
            loaded.api_base = "http://opentweet.io"
            return loaded

        monkeypatch.setattr("opentweet.selftest.load_opentweet_config", _bad_api)
        reset_config_cache()
        try:
            _p, _t, lines = run_self_test(str(path))
        finally:
            reset_config_cache()
        assert any("04. api_base is https" in ln and "[FAIL]" in ln for ln in lines)

        fake_pkg = tmp_path / "fake_opentweet_pkg"
        fake_pkg.mkdir()
        (fake_pkg / "leaky.py").write_text("import gate\n", encoding="utf-8")
        monkeypatch.setattr("opentweet.selftest.load_opentweet_config", load_opentweet_config)
        monkeypatch.setattr("opentweet.selftest.PKG_ROOT", fake_pkg)
        monkeypatch.setattr("opentweet.selftest.REPO_ROOT", tmp_path)
        reset_config_cache()
        try:
            _p, _t, lines = run_self_test(str(path))
        finally:
            reset_config_cache()
        assert any(
            "05. package does not import request-path modules" in ln and "[FAIL]" in ln
            for ln in lines
        )
