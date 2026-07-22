"""Self-contained tests for sync.scheduler.

Runnable with ``pytest --noconftest`` (no conftest fixtures): builds an
``RcloneConfig`` directly via a tmp config.yaml + ``reset_config_cache`` and
patches the subprocess / which / platform boundary. No real crontab or schtasks
is ever invoked, and no network is touched.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sync.config import RcloneConfig, load_sync_config
from sync.scheduler import (
    TASK_TAG,
    WINDOWS_TASK_NAME,
    CronScheduler,
    ScheduleEntry,
    WindowsTaskScheduler,
    get_scheduler,
)
from utils.errors import SchedulerError
from utils.logger import reset_config_cache

# A repo-valid corpus path: config validation requires local_path to resolve
# under the repo's data/corpus tree, so derive it from this file's location.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_CORPUS = str(_REPO_ROOT / "data" / "corpus")


def _make_cfg(schedule_hour: int = 2, schedule_min: int = 0, **overrides) -> RcloneConfig:
    kwargs: dict = dict(
        local_path=_CORPUS,
        remote_name="dropbox_cyclaw",
        remote_path="CyClaw/corpus",
        schedule_hour=schedule_hour,
        schedule_min=schedule_min,
    )
    kwargs.update(overrides)
    return RcloneConfig(**kwargs)


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.stderr = stderr
    return m


# ---------------------------------------------------------------------------
# get_scheduler factory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Linux", CronScheduler),
        ("Darwin", CronScheduler),
        ("Windows", WindowsTaskScheduler),
    ],
)
def test_get_scheduler_returns_right_class(system: str, expected: type) -> None:
    cfg = _make_cfg()
    with patch("sync.scheduler.platform.system", return_value=system):
        sched = get_scheduler(cfg)
    assert isinstance(sched, expected)


def test_get_scheduler_unsupported_platform_raises() -> None:
    cfg = _make_cfg()
    with patch("sync.scheduler.platform.system", return_value="Plan9"):
        with pytest.raises(SchedulerError):
            get_scheduler(cfg)


# ---------------------------------------------------------------------------
# CronScheduler.install
# ---------------------------------------------------------------------------


def test_cron_install_appends_exactly_one_tagged_line() -> None:
    cfg = _make_cfg(schedule_hour=3, schedule_min=15)
    existing = "0 1 * * * /usr/bin/backup.sh\n"
    written: dict[str, str] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[1] == "-l":
            return _completed(stdout=existing)
        # write path: crontab -
        written["content"] = kwargs["input"]
        return _completed()

    with (
        patch("sync.scheduler.shutil.which", return_value="/usr/bin/crontab"),
        patch("sync.scheduler.subprocess.run", side_effect=fake_run),
        patch("sync.scheduler.platform.system", return_value="Linux"),
    ):
        entry = CronScheduler(cfg).install()

    content = written["content"]
    tagged = [ln for ln in content.splitlines() if TASK_TAG in ln]
    assert len(tagged) == 1
    # User's unrelated line is preserved.
    assert "/usr/bin/backup.sh" in content
    assert isinstance(entry, ScheduleEntry)
    assert entry.cron_or_time == "15 3 * * *"
    assert tagged[0].startswith("15 3 * * *")
    assert tagged[0].endswith(f"# {TASK_TAG}")
    # Command cd's into the repo root, not data/corpus.
    assert "-m sync.cli sync" in tagged[0]
    assert os.path.basename(_REPO_ROOT) in tagged[0]


def test_cron_install_replaces_prior_tagged_line() -> None:
    cfg = _make_cfg()
    # Two existing tagged lines plus an unrelated one.
    existing = (
        "0 1 * * * /usr/bin/keep.sh\n"
        f"9 9 * * * old-cmd # {TASK_TAG}\n"
        f"8 8 * * * older-cmd # {TASK_TAG}\n"
    )
    written: dict[str, str] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[1] == "-l":
            return _completed(stdout=existing)
        written["content"] = kwargs["input"]
        return _completed()

    with (
        patch("sync.scheduler.shutil.which", return_value="/usr/bin/crontab"),
        patch("sync.scheduler.subprocess.run", side_effect=fake_run),
        patch("sync.scheduler.platform.system", return_value="Linux"),
    ):
        CronScheduler(cfg).install()

    content = written["content"]
    tagged = [ln for ln in content.splitlines() if TASK_TAG in ln]
    assert len(tagged) == 1  # both old tagged lines stripped, one fresh appended
    assert "old-cmd" not in content
    assert "older-cmd" not in content
    assert "/usr/bin/keep.sh" in content


# ---------------------------------------------------------------------------
# CronScheduler.remove
# ---------------------------------------------------------------------------


def test_cron_remove_returns_false_when_no_tagged_line() -> None:
    cfg = _make_cfg()
    existing = "0 1 * * * /usr/bin/keep.sh\n"
    write_called = {"n": 0}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[1] == "-l":
            return _completed(stdout=existing)
        write_called["n"] += 1
        return _completed()

    with (
        patch("sync.scheduler.shutil.which", return_value="/usr/bin/crontab"),
        patch("sync.scheduler.subprocess.run", side_effect=fake_run),
    ):
        result = CronScheduler(cfg).remove()

    assert result is False
    assert write_called["n"] == 0  # nothing rewritten when nothing matched


def test_cron_remove_returns_true_when_tagged_line_present() -> None:
    cfg = _make_cfg()
    existing = "0 1 * * * /usr/bin/keep.sh\n" f"2 2 * * * some-cmd # {TASK_TAG}\n"
    written: dict[str, str] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        if argv[1] == "-l":
            return _completed(stdout=existing)
        written["content"] = kwargs["input"]
        return _completed()

    with (
        patch("sync.scheduler.shutil.which", return_value="/usr/bin/crontab"),
        patch("sync.scheduler.subprocess.run", side_effect=fake_run),
    ):
        result = CronScheduler(cfg).remove()

    assert result is True
    assert TASK_TAG not in written["content"]
    assert "/usr/bin/keep.sh" in written["content"]


# ---------------------------------------------------------------------------
# CronScheduler.status
# ---------------------------------------------------------------------------


def test_cron_status_parses_tagged_line() -> None:
    cfg = _make_cfg()
    existing = "0 1 * * * /usr/bin/keep.sh\n" f'30 4 * * * cd "/repo" && python -m sync.cli sync # {TASK_TAG}\n'

    with (
        patch("sync.scheduler.shutil.which", return_value="/usr/bin/crontab"),
        patch("sync.scheduler.subprocess.run", return_value=_completed(stdout=existing)),
        patch("sync.scheduler.platform.system", return_value="Linux"),
    ):
        entry = CronScheduler(cfg).status()

    assert entry is not None
    assert entry.cron_or_time == "30 4 * * *"
    assert "sync.cli sync" in entry.command
    assert TASK_TAG not in entry.command  # comment stripped from the command


def test_cron_status_returns_none_when_no_tagged_line() -> None:
    cfg = _make_cfg()
    existing = "0 1 * * * /usr/bin/keep.sh\n"
    with (
        patch("sync.scheduler.shutil.which", return_value="/usr/bin/crontab"),
        patch("sync.scheduler.subprocess.run", return_value=_completed(stdout=existing)),
    ):
        assert CronScheduler(cfg).status() is None


def test_cron_missing_crontab_binary_raises() -> None:
    cfg = _make_cfg()
    with patch("sync.scheduler.shutil.which", return_value=None):
        with pytest.raises(SchedulerError):
            CronScheduler(cfg).status()


def test_cron_uses_argv_list_never_shell() -> None:
    cfg = _make_cfg()
    seen: list[object] = []

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        seen.append(argv)
        assert isinstance(argv, list)
        assert "shell" not in kwargs or kwargs["shell"] is False
        if argv[1] == "-l":
            return _completed(stdout="")
        return _completed()

    with (
        patch("sync.scheduler.shutil.which", return_value="/usr/bin/crontab"),
        patch("sync.scheduler.subprocess.run", side_effect=fake_run),
        patch("sync.scheduler.platform.system", return_value="Linux"),
    ):
        CronScheduler(cfg).install()

    assert seen  # at least one call made
    for argv in seen:
        assert isinstance(argv, list)
        assert argv[0] == "/usr/bin/crontab"


# ---------------------------------------------------------------------------
# WindowsTaskScheduler
# ---------------------------------------------------------------------------


def test_windows_install_builds_schtasks_argv(tmp_path: Path) -> None:
    # log_dir -> tmp so the generated .bat launcher does not touch the real home.
    cfg = _make_cfg(schedule_hour=5, schedule_min=7, log_dir=str(tmp_path / "logs"))
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):  # type: ignore[no-untyped-def]
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _completed(stdout="SUCCESS")

    with (
        patch("sync.scheduler.shutil.which", return_value=r"C:\Windows\System32\schtasks.exe"),
        patch("sync.scheduler.subprocess.run", side_effect=fake_run),
        patch("sync.scheduler.platform.system", return_value="Windows"),
    ):
        entry = WindowsTaskScheduler(cfg).install()

    argv = captured["argv"]
    assert isinstance(argv, list)
    assert "shell" not in captured["kwargs"] or captured["kwargs"]["shell"] is False
    assert argv[0] == r"C:\Windows\System32\schtasks.exe"
    assert "/Create" in argv
    assert argv[argv.index("/TN") + 1] == WINDOWS_TASK_NAME
    assert argv[argv.index("/SC") + 1] == "DAILY"
    assert argv[argv.index("/ST") + 1] == "05:07"
    assert "/F" in argv
    assert argv[argv.index("/RL") + 1] == "LIMITED"
    assert entry.cron_or_time == "05:07"
    assert entry.platform_name == "windows"

    # /TR points at the generated .bat launcher (robust quoting), and the file
    # was actually written with the cd + sync invocation.
    launcher = argv[argv.index("/TR") + 1]
    assert launcher.endswith("cyclaw_sync.bat")
    assert entry.command == launcher
    bat_text = Path(launcher).read_text(encoding="utf-8")
    assert "-m sync.cli sync" in bat_text
    assert "cd /d" in bat_text


def test_windows_remove_not_found_returns_false() -> None:
    cfg = _make_cfg()
    with (
        patch("sync.scheduler.shutil.which", return_value=r"C:\Windows\System32\schtasks.exe"),
        patch(
            "sync.scheduler.subprocess.run",
            return_value=_completed(returncode=1, stderr="ERROR: The system cannot find the file specified."),
        ),
    ):
        assert WindowsTaskScheduler(cfg).remove() is False


def test_windows_remove_success_returns_true() -> None:
    cfg = _make_cfg()
    with (
        patch("sync.scheduler.shutil.which", return_value=r"C:\Windows\System32\schtasks.exe"),
        patch("sync.scheduler.subprocess.run", return_value=_completed(returncode=0, stdout="SUCCESS")),
    ):
        assert WindowsTaskScheduler(cfg).remove() is True


def test_windows_missing_schtasks_raises() -> None:
    cfg = _make_cfg()
    with patch("sync.scheduler.shutil.which", return_value=None):
        with pytest.raises(SchedulerError):
            WindowsTaskScheduler(cfg).install()


# ---------------------------------------------------------------------------
# Config-from-yaml path (Appendix D style, no conftest fixtures)
# ---------------------------------------------------------------------------


def test_scheduler_from_loaded_config(tmp_path: Path) -> None:
    cfg_yaml = tmp_path / "config.yaml"
    # Use forward slashes: YAML double-quoted strings interpret backslashes as
    # escape sequences, which breaks Windows paths like C:\Users\... in CI.
    corpus_fwd = _CORPUS.replace("\\", "/")
    cfg_yaml.write_text(
        "sync:\n"
        "  enabled: true\n"
        f'  local_path: "{corpus_fwd}"\n'
        "  remote_name: dropbox_cyclaw\n"
        "  remote_path: CyClaw/corpus\n"
        "  schedule_hour: 6\n"
        "  schedule_min: 30\n"
    )
    reset_config_cache()
    try:
        cfg = load_sync_config(str(cfg_yaml))
    finally:
        reset_config_cache()

    with patch("sync.scheduler.platform.system", return_value="Linux"):
        sched = get_scheduler(cfg)
    assert isinstance(sched, CronScheduler)
    assert sched.cfg.schedule_hour == 6
    assert sched.cfg.schedule_min == 30


# ---------------------------------------------------------------------------
# Canonical repo root + propagated config identity (codex findings)
# ---------------------------------------------------------------------------

def test_repo_root_canonical_for_nested_local_path() -> None:
    # A local_path nested below data/corpus is valid config, but the legacy
    # two-parents-up derivation would resolve to repo/data instead of the
    # repo. The canonical root carried from load time must win.
    from sync.scheduler import _repo_root

    nested = str(Path(_CORPUS) / "sub" / "vault")
    cfg = _make_cfg(local_path=nested)
    assert cfg.repo_root == str(_REPO_ROOT)
    assert _repo_root(cfg) == str(_REPO_ROOT)


def test_repo_root_legacy_fallback_only_without_canonical() -> None:
    # Without the carried attribute the depth-based fallback still works for
    # the flat .../data/corpus layout (back-compat for hand-built configs).
    from sync.scheduler import _repo_root

    cfg = _make_cfg()
    del cfg.repo_root
    assert _repo_root(cfg) == str(_REPO_ROOT)


def test_sync_command_propagates_config_identity() -> None:
    # A schedule installed via `--config /alt/cfg.yaml` must keep reading THAT
    # file: the generated command re-invokes the CLI with the same path. shlex
    # leaves a clean path unquoted, so assert the path is carried verbatim
    # rather than a specific quoting.
    from sync.scheduler import _sync_command

    cfg = _make_cfg()
    cfg._config_path = "/alt/dir/custom.yaml"
    cmd = _sync_command(cfg)
    assert "--config" in cmd
    assert "/alt/dir/custom.yaml" in cmd
    assert cmd.index("--config") < cmd.rindex("sync")  # flag before subcommand


def test_sync_command_omits_config_flag_when_unset() -> None:
    from sync.scheduler import _sync_command

    cfg = _make_cfg()  # direct RcloneConfig: loader never attached a path
    assert "--config" not in _sync_command(cfg)


def test_sync_command_shlex_escapes_hostile_paths(monkeypatch) -> None:
    # codex #592: the POSIX cron line must treat a hostile config/repo path as a
    # single inert argument -- $(), backticks, ;, & must never break out. Prove
    # it by round-tripping the generated command through shlex.split: every
    # dangerous fragment stays inside ONE quoted token.
    import shlex

    from sync import scheduler
    from sync.scheduler import _sync_command

    monkeypatch.setattr(scheduler.platform, "system", lambda: "Linux")
    cfg = _make_cfg()
    hostile = "/tmp/evil; rm -rf ~ $(id) `whoami`/cfg.yaml"
    cfg._config_path = hostile
    cmd = _sync_command(cfg)

    toks = shlex.split(cmd)  # POSIX word-splitting, honouring the quoting
    assert "--config" in toks
    assert toks[toks.index("--config") + 1] == hostile  # survives as ONE argument
    # The injection fragments are NOT standalone shell tokens.
    assert "rm" not in toks and "$(id)" not in toks and "`whoami`/cfg.yaml" not in toks
    # The only shell-active operator we emit is our own && between cd and python.
    assert toks.count("&&") == 1


def test_windows_launcher_doubles_percent_and_quotes(tmp_path) -> None:
    # codex #592: a config path containing %VAR% must be written into the .bat
    # with % doubled (so cmd.exe cannot expand it at run time) and quoted (so
    # spaces are safe). _write_windows_launcher builds the .bat on any OS.
    from sync.scheduler import _bat_quote, _write_windows_launcher

    cfg = _make_cfg(log_dir=str(tmp_path / "logs"))
    cfg._config_path = r"C:\cfg %TEMP% dir\config.yaml"
    bat = _write_windows_launcher(cfg)
    content = Path(bat).read_text(encoding="utf-8")

    assert _bat_quote(cfg._config_path) in content       # quoted + %-doubled
    assert "%%TEMP%%" in content                          # not expandable
    assert '"%TEMP%"' not in content                      # never a bare, expandable form
    assert content.startswith("@echo off")               # (read_text normalizes CRLF->LF)


def test_cron_line_escapes_percent_in_config_path(monkeypatch) -> None:
    # POSIX twin of the Windows % doubling: crontab(5) turns bare % into a
    # newline + stdin feed, truncating the scheduled command. The installed
    # line must backslash-escape every % in the command field.
    from sync.scheduler import CronScheduler, _cron_escape_command, _sync_command

    monkeypatch.setattr("sync.scheduler.platform.system", lambda: "Linux")
    cfg = _make_cfg()
    cfg._config_path = "/tmp/cfg%20dir/config.yaml"
    cfg.schedule_min = 15
    cfg.schedule_hour = 3

    raw = _sync_command(cfg)
    assert "%" in raw  # unescaped in the shell-facing string is fine
    escaped = _cron_escape_command(raw)
    assert r"\%" in escaped
    # No bare % left in the escaped command field.
    assert "%" not in escaped.replace(r"\%", "")

    line = CronScheduler(cfg)._our_line()
    assert line.startswith("15 3 * * * ")
    assert r"cfg\%20dir" in line
    # Command field (between schedule and tag) has no unescaped %.
    cmd_field = line.rsplit("#", 1)[0].split(None, 5)[5]
    assert "%" not in cmd_field.replace(r"\%", "")
