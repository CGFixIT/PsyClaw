"""Unit tests for sync/selftest.py — the `python -m sync.cli test` pre-flight.

`run_self_test` is the function the CLI's ``test`` subcommand depends on, but it
was the only sync module never executed by the suite — it was merely *mocked*
(``patch("sync.selftest.run_self_test", ...)``) in test_sync_cli.py. This
exercises it directly: the invalid-config short-circuit and a fully-passing
run. rclone is mocked, so no binary and no network are required.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from sync.config import RcloneConfig
from sync.selftest import run_self_test
from utils.errors import SyncConfigError

_CORPUS = str(Path(__file__).resolve().parent.parent / "data" / "corpus")


def _cfg(tmp_path) -> RcloneConfig:
    # local_path must resolve inside the repo's data/corpus tree (config guard);
    # filter_file / log_dir are redirected into tmp so the test writes nothing
    # outside its sandbox.
    return RcloneConfig(
        local_path=_CORPUS,
        filter_file=str(tmp_path / "cyclaw_filters.txt"),
        log_dir=str(tmp_path / "logs"),
    )


class TestRunSelfTestInvalidConfig:
    def test_invalid_config_fails_first_and_skips_rest(self):
        with patch("sync.selftest.load_sync_config",
                   side_effect=SyncConfigError("bad config")):
            passed, total, lines = run_self_test("ignored.yaml")

        # 1 hard FAIL (check 01) + 7 environment-conditional SKIPs (counted as
        # pass) = 7/8. The run must not crash on an unloadable config.
        assert total == 8
        assert passed == 7
        assert "[FAIL]" in lines[0]
        assert "01" in lines[0]
        assert all("[SKIP]" in ln for ln in lines[1:])


class TestRunSelfTestHappyPath:
    def test_all_checks_pass_with_rclone_mocked(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch("sync.selftest.load_sync_config", return_value=cfg), \
             patch("sync.selftest.check_rclone_version", return_value=(1, 68, 2)):
            passed, total, lines = run_self_test("ignored.yaml")

        assert total == 8
        assert passed == 8, "\n".join(lines)
        # No check should have FAILED.
        assert not any("[FAIL]" in ln for ln in lines)
        # The mocked rclone version is surfaced in the report.
        assert any("rclone 1.68.2" in ln for ln in lines)
        # The default-excluded soul directory is asserted by check 04.
        assert any("data/personality" in ln for ln in lines)

    def test_filter_file_is_written(self, tmp_path):
        cfg = _cfg(tmp_path)
        with patch("sync.selftest.load_sync_config", return_value=cfg), \
             patch("sync.selftest.check_rclone_version", return_value=(1, 68, 2)):
            run_self_test("ignored.yaml")
        # Check 05 writes the rclone filter file to disk.
        assert Path(cfg.filter_file).is_file()

    def test_all_checks_pass_with_include_soul_true(self, tmp_path):
        # include_soul is a deprecated no-op: check 04 must still find the soul
        # exclusion present, so a config with include_soul=true passes cleanly.
        cfg = _cfg(tmp_path)
        cfg.include_soul = True
        with patch("sync.selftest.load_sync_config", return_value=cfg), \
             patch("sync.selftest.check_rclone_version", return_value=(1, 68, 2)):
            passed, total, lines = run_self_test("ignored.yaml")

        assert total == 8
        assert passed == 8, "\n".join(lines)
        assert not any("[FAIL]" in ln for ln in lines)
        assert any("04" in ln and "data/personality" in ln for ln in lines)
