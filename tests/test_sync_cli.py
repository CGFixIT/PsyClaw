"""Self-contained unit tests for sync.cli (no network, no real rclone/scheduler).

Runnable with ``pytest --noconftest tests/test_sync_cli.py``. The runner and the
lazily-imported scheduler are patched on the ``sync.cli`` module so no subprocess
or real scheduler is touched. Asserts the documented exit-code contract (§7).
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sync.cli import (
    EXIT_ENV,
    EXIT_FAIL,
    EXIT_OK,
    EXIT_REINDEX,
    EXIT_SAFETY,
    main,
)
from sync.config import RcloneConfig
from utils.errors import RcloneNotInstalledError, SchedulerError, SyncConfigError
from utils.logger import reset_config_cache


@pytest.fixture(autouse=True)
def _reset_cache():
    reset_config_cache()
    yield
    reset_config_cache()


def _cfg() -> RcloneConfig:
    corpus = __import__("pathlib").Path(__file__).resolve().parent.parent / "data" / "corpus"
    return RcloneConfig(local_path=str(corpus))


def _result(success=True, corpus_changed=False, aborted=False, exit_code=0):
    r = MagicMock()
    r.success = success
    r.corpus_changed = corpus_changed
    r.aborted_for_safety = aborted
    r.rclone_exit_code = exit_code
    r.direction = "pull"
    r.duration_sec = 0.1
    r.errors = []
    r.event_counts.return_value = {"added": 0, "modified": 0, "deleted": 0}
    return r


# ---------------------------------------------------------------------------
# sync subcommand -- exit codes 0 / 10 / 1 / 2 / 3
# ---------------------------------------------------------------------------

def test_sync_ok_exit_0():
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.run_sync", return_value=_result()), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_OK):
        assert main(["sync"]) == EXIT_OK


def test_sync_corpus_changed_exit_10():
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.run_sync", return_value=_result(corpus_changed=True)), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_REINDEX):
        assert main(["sync"]) == EXIT_REINDEX


def test_sync_safety_abort_exit_1():
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.run_sync", return_value=_result(success=False, aborted=True)), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_SAFETY):
        assert main(["sync"]) == EXIT_SAFETY


def test_sync_other_failure_exit_2():
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.run_sync", return_value=_result(success=False)), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_FAIL):
        assert main(["sync"]) == EXIT_FAIL


def test_sync_rclone_missing_exit_3():
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.run_sync", side_effect=RcloneNotInstalledError("nope")):
        assert main(["sync"]) == EXIT_ENV


def test_sync_bad_config_exit_3():
    with patch("sync.cli.load_sync_config", side_effect=SyncConfigError("bad")):
        assert main(["sync"]) == EXIT_ENV


def test_sync_disabled_noops_exit_0_without_running():
    # sync.enabled: false is an intentional off, not an error: cmd_sync must
    # return EXIT_OK and never invoke run_sync.
    cfg = _cfg()
    cfg.enabled = False  # set by load_sync_config in production
    with patch("sync.cli.load_sync_config", return_value=cfg), \
         patch("sync.cli.run_sync") as mrun:
        assert main(["sync"]) == EXIT_OK
        mrun.assert_not_called()


def test_sync_enabled_true_runs():
    cfg = _cfg()
    cfg.enabled = True
    with patch("sync.cli.load_sync_config", return_value=cfg), \
         patch("sync.cli.run_sync", return_value=_result()) as mrun, \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_OK):
        assert main(["sync"]) == EXIT_OK
        mrun.assert_called_once()


def test_sync_dry_run_passes_flag():
    captured = {}

    def fake_run(cfg, dry_run=False, resync=False):
        captured["dry_run"] = dry_run
        return _result()

    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.run_sync", side_effect=fake_run), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_OK):
        assert main(["sync", "--dry-run"]) == EXIT_OK
    assert captured["dry_run"] is True


# ---------------------------------------------------------------------------
# auto_reindex -- exit-10 corpus change is rebuilt in-CLI when enabled
# ---------------------------------------------------------------------------

def test_auto_reindex_runs_indexer_and_returns_0_on_change():
    cfg = _cfg()
    cfg.auto_reindex = True
    captured = {}

    def fake_run(argv, check=False):
        captured["argv"] = argv
        return MagicMock(returncode=0)

    with patch("sync.cli.load_sync_config", return_value=cfg), \
         patch("sync.cli.run_sync", return_value=_result(corpus_changed=True)), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_REINDEX), \
         patch("sync.cli.subprocess.run", side_effect=fake_run):
        # A successful auto-reindex collapses exit 10 -> 0 (work is done).
        assert main(["sync"]) == EXIT_OK
    # Fixed argv, module form, never shell=True.
    assert captured["argv"][1:] == ["-m", "retrieval.indexer"]


def test_auto_reindex_propagates_custom_config_identity():
    # codex #592: a sync started with `--config /alt/cfg.yaml` must rebuild the
    # index from THAT config, not the default. The loader records the resolved
    # path on the cfg; the reindex subprocess must carry it as `--config`.
    cfg = _cfg()
    cfg.auto_reindex = True
    cfg._config_path = "/alt/dir/custom.yaml"
    captured = {}

    def fake_run(argv, check=False):
        captured["argv"] = argv
        return MagicMock(returncode=0)

    with patch("sync.cli.load_sync_config", return_value=cfg), \
         patch("sync.cli.run_sync", return_value=_result(corpus_changed=True)), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_REINDEX), \
         patch("sync.cli.subprocess.run", side_effect=fake_run):
        assert main(["sync"]) == EXIT_OK
    assert captured["argv"][1:] == ["-m", "retrieval.indexer", "--config", "/alt/dir/custom.yaml"]


def test_auto_reindex_off_passes_exit_10_through():
    cfg = _cfg()
    cfg.auto_reindex = False
    with patch("sync.cli.load_sync_config", return_value=cfg), \
         patch("sync.cli.run_sync", return_value=_result(corpus_changed=True)), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_REINDEX), \
         patch("sync.cli.subprocess.run") as mrun:
        assert main(["sync"]) == EXIT_REINDEX
        mrun.assert_not_called()  # default behaviour: signal, don't rebuild


def test_auto_reindex_failure_returns_exit_2():
    cfg = _cfg()
    cfg.auto_reindex = True
    with patch("sync.cli.load_sync_config", return_value=cfg), \
         patch("sync.cli.run_sync", return_value=_result(corpus_changed=True)), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_REINDEX), \
         patch("sync.cli.subprocess.run", return_value=MagicMock(returncode=3)):
        # A failed rebuild must surface as a failure, not be masked as success.
        assert main(["sync"]) == EXIT_FAIL


def test_auto_reindex_not_triggered_without_corpus_change():
    cfg = _cfg()
    cfg.auto_reindex = True
    with patch("sync.cli.load_sync_config", return_value=cfg), \
         patch("sync.cli.run_sync", return_value=_result()), \
         patch("sync.cli.reindex_exit_code_for", return_value=EXIT_OK), \
         patch("sync.cli.subprocess.run") as mrun:
        assert main(["sync"]) == EXIT_OK
        mrun.assert_not_called()  # no exit-10 -> indexer never launched


# ---------------------------------------------------------------------------
# status / test subcommands
# ---------------------------------------------------------------------------

def test_status_ok_with_rclone_present():
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.check_rclone_version", return_value=(1, 70, 0)), \
         patch("sync.cli.get_scheduler") as mgs:
        mgs.return_value.status.return_value = None
        assert main(["status"]) == EXIT_OK


def test_status_rclone_missing_still_exit_0():
    # status reports rclone-missing but does not itself fail (env reporting view).
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.check_rclone_version", side_effect=RcloneNotInstalledError("nope")), \
         patch("sync.cli.get_scheduler") as mgs:
        mgs.return_value.status.return_value = None
        assert main(["status"]) == EXIT_OK


def test_status_bad_config_exit_3():
    with patch("sync.cli.load_sync_config", side_effect=SyncConfigError("bad")):
        assert main(["status"]) == EXIT_ENV


def test_test_subcommand_all_pass_exit_0():
    with patch("sync.selftest.run_self_test", return_value=(3, 3, ["ok"])):
        assert main(["test"]) == EXIT_OK


def test_test_subcommand_some_fail_exit_2():
    with patch("sync.selftest.run_self_test", return_value=(2, 3, ["x"])):
        assert main(["test"]) == EXIT_FAIL


# ---------------------------------------------------------------------------
# schedule / unschedule -- lazily-imported get_scheduler is patched on sync.cli
# ---------------------------------------------------------------------------

def test_schedule_ok():
    entry = MagicMock(cron_or_time="0 2 * * *", platform_name="cron")
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.get_scheduler") as mgs:
        mgs.return_value.install.return_value = entry
        assert main(["schedule"]) == EXIT_OK


def test_schedule_failure_exit_3():
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.get_scheduler") as mgs:
        mgs.return_value.install.side_effect = SchedulerError("cron unavailable")
        assert main(["schedule"]) == EXIT_ENV


def test_unschedule_ok():
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.get_scheduler") as mgs:
        mgs.return_value.remove.return_value = True
        assert main(["unschedule"]) == EXIT_OK


def test_setup_schedule_uses_lazy_scheduler():
    entry = MagicMock(cron_or_time="0 2 * * *", platform_name="cron")
    with patch("sync.cli.load_sync_config", return_value=_cfg()), \
         patch("sync.cli.check_rclone_version", return_value=(1, 70, 0)), \
         patch("sync.cli.write_filter_file", return_value="/tmp/filters.txt"), \
         patch("sync.cli.get_scheduler") as mgs:
        mgs.return_value.install.return_value = entry
        assert main(["setup", "--schedule"]) == EXIT_OK
        mgs.return_value.install.assert_called_once()


# ---------------------------------------------------------------------------
# Module imports without the scheduler present (B<->C decoupling)
# ---------------------------------------------------------------------------

def test_cli_imports_without_scheduler():
    import importlib

    import sync.cli as cli_mod

    importlib.reload(cli_mod)
    assert callable(cli_mod.main)
