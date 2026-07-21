"""Cross-platform scheduler abstraction for the CyClaw Dropbox corpus sync.

Linux/macOS: crontab manipulation via ``crontab -l`` and ``crontab -`` piping
             (never ``crontab -e`` -- no interactive editor in an unattended
             flow). A single tagged line is added/replaced/removed.
Windows:     ``schtasks`` for Task Scheduler create/delete/query.

We intentionally avoid third-party deps (python-crontab, pywin32) so this plugs
into CyClaw's offline-first / minimal-deps philosophy. The trade-off is
platform-specific subprocess calls -- kept tight, argv-list only, never
``shell=True``, and binaries resolved via ``shutil.which`` so no partial-path
taint reaches the kernel.

systemd note (Linux): the more robust Linux option (per the implementation plan
section 5.1) is a systemd ``--user`` ``Type=oneshot`` service driven by a timer
unit: it gives inherent overlap protection, journald logging, and
``Persistent=true`` catch-up after downtime. We do not auto-generate the unit
here; **cron is the implemented portable baseline** (works on macOS/WSL/BSD
too). Operators who prefer systemd should run the sync via a ``--user`` timer
calling ``python -m sync.cli sync`` and skip ``schedule``/``unschedule``. The
cron baseline has no built-in single-instance guard, so a wrapper-level lockfile
(or systemd) is recommended if manual and scheduled runs might collide.

Scheduler identity: every task we register is tagged with ``TASK_TAG`` (a
trailing comment on Linux/macOS, the task name on Windows) so install/remove
only ever touch our own entry and never anything the user added by hand.
"""

from __future__ import annotations

import os
import platform
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass

from sync.config import RcloneConfig
from utils.errors import SchedulerError

TASK_TAG = "CYCLAW_DROPBOX_SYNC"
WINDOWS_TASK_NAME = "CyClaw Dropbox Sync"


@dataclass
class ScheduleEntry:
    """Description of a scheduled job, in platform-neutral form."""

    platform_name: str  # "linux", "darwin", "windows"
    command: str  # the actual command line that will be run
    cron_or_time: str  # cron expression OR HH:MM
    raw: str  # the raw line / schtasks output for debugging


def _python_executable() -> str:
    """Best guess at the python interpreter to invoke from the scheduler."""
    candidate = sys.executable or "python"
    if candidate and os.path.isfile(candidate):
        return candidate
    found = shutil.which("python3") or shutil.which("python")
    return found or "python"


def _repo_root(cfg: RcloneConfig) -> str:
    """Directory the scheduled command should cd into before running sync.

    Prefers the canonical root carried on the config object (derived from the
    code location at load time). The legacy fallback derives it from
    ``cfg.local_path`` -- correct ONLY for the flat ``.../data/corpus`` layout:
    a local_path nested below data/corpus would resolve to ``repo/data``
    instead of the repo (codex finding), so the depth-based derivation is
    never used when the canonical root is available.
    """
    canonical = getattr(cfg, "repo_root", None)
    if canonical:
        return str(canonical)
    corpus = os.path.abspath(cfg.local_path)
    repo_root = os.path.dirname(os.path.dirname(corpus))  # .../data/corpus -> repo
    return repo_root


def _bat_quote(s: str) -> str:
    """Quote a path for safe literal use inside a cmd.exe ``.bat`` line.

    Wraps in double quotes (so spaces, ``&``, ``(``, ``)`` are inert) and doubles
    every ``%`` so a segment like ``%TEMP%`` is not expanded as an environment
    variable when the scheduled task runs (codex #592: a naive ``f'"{path}"'``
    let ``%VAR%`` expansion and unbalanced quoting through). Windows filenames
    cannot contain a literal ``"``, so no inner-quote escaping is needed; our
    ``.bat`` leaves delayed expansion off, so ``!`` stays literal too.
    """
    return '"' + s.replace("%", "%%") + '"'


def _sync_command(cfg: RcloneConfig) -> str:
    """The actual command the scheduler will invoke.

    cd into the repo root (so ``config.yaml`` resolves correctly), then run
    ``python -m sync.cli sync`` as a separate process, carrying the loaded
    config's identity via ``--config`` so a schedule installed with a custom
    config keeps reading THAT file.

    POSIX: this string IS the cron line, so every operator-influenced token is
    ``shlex.quote``-d -- a repo path or config path containing spaces or shell
    metacharacters (``$()``, backticks, ``;``, ``&``) becomes a single inert
    argument that cannot break out of the command (codex #592).

    Windows: the scheduler does NOT register this string -- see
    ``_write_windows_launcher`` and ``WindowsTaskScheduler.install``. A full
    ``cmd /c`` string through ``schtasks /TR`` is quote-fragile, so a ``.bat``
    launcher is used instead; this string is kept only for status output.
    """
    py = _python_executable()
    root = _repo_root(cfg)
    cfg_path = getattr(cfg, "_config_path", None)
    if platform.system() == "Windows":
        config_arg = f"--config {_bat_quote(cfg_path)} " if cfg_path else ""
        return f'cmd /c "cd /d {_bat_quote(root)} && {_bat_quote(py)} -m sync.cli {config_arg}sync"'
    tokens = ["cd", shlex.quote(root), "&&", shlex.quote(py), "-m", "sync.cli"]
    if cfg_path:
        tokens += ["--config", shlex.quote(cfg_path)]
    tokens.append("sync")
    return " ".join(tokens)


def _write_windows_launcher(cfg: RcloneConfig) -> str:
    """Write a ``.bat`` launcher for the scheduled sync and return its path.

    Registering a path to a one-line batch file via ``schtasks /TR`` avoids the
    fragile quoting of embedding a full ``cmd /c`` command string. Every path in
    the file is ``_bat_quote``-d: quoted against spaces and ``%``-doubled so no
    path segment is reinterpreted as an environment variable at run time
    (codex #592).
    """
    root = _repo_root(cfg)
    py = _python_executable()
    bat_dir = cfg.log_dir or root
    os.makedirs(bat_dir, exist_ok=True)
    bat_path = os.path.join(bat_dir, "cyclaw_sync.bat")
    cfg_path = getattr(cfg, "_config_path", None)
    config_arg = f"--config {_bat_quote(cfg_path)} " if cfg_path else ""
    # CRLF line endings + _bat_quote so paths with spaces or % are safe.
    content = (
        "@echo off\r\n"
        f"cd /d {_bat_quote(root)}\r\n"
        f"{_bat_quote(py)} -m sync.cli {config_arg}sync\r\n"
    )
    with open(bat_path, "w", encoding="utf-8", newline="") as f:
        f.write(content)
    return bat_path


# ---------------------------------------------------------------------------
# Linux / macOS -- cron
# ---------------------------------------------------------------------------


class CronScheduler:
    """Manage a single CyClaw cron entry via ``crontab -l`` / ``crontab -``."""

    def __init__(self, cfg: RcloneConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _crontab_bin() -> str:
        path = shutil.which("crontab")
        if not path:
            raise SchedulerError(
                "crontab not available on this system",
                details={"hint": "Install cron, or schedule via a systemd --user timer manually."},
            )
        return path

    # crontab interactions: avoid -e (editor); use stdin piping.
    def _read_crontab(self) -> str:
        crontab = self._crontab_bin()
        try:
            result = subprocess.run(  # noqa: S603  # argv list, crontab resolved via shutil.which
                [crontab, "-l"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SchedulerError(
                "crontab not available on this system",
                details={"hint": "Install cron or schedule via a systemd --user timer manually."},
            ) from exc
        # `crontab -l` returns 1 when the user has no crontab -- not an error.
        if result.returncode not in (0, 1):
            raise SchedulerError(
                f"crontab -l failed (rc={result.returncode}): {result.stderr.strip()}",
                details={"stderr": result.stderr[:500]},
            )
        return result.stdout or ""

    def _write_crontab(self, content: str) -> None:
        crontab = self._crontab_bin()
        try:
            proc = subprocess.run(  # noqa: S603  # argv list, crontab resolved via shutil.which
                [crontab, "-"],
                input=content,
                text=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SchedulerError("crontab binary not available") from exc
        if proc.returncode != 0:
            raise SchedulerError(
                f"crontab write failed (rc={proc.returncode}): {proc.stderr.strip()}",
                details={"stderr": proc.stderr[:500]},
            )

    def _our_line(self) -> str:
        """The single tagged cron line we want active."""
        cmd = _sync_command(self.cfg)
        return f"{self.cfg.schedule_min} {self.cfg.schedule_hour} * * * {cmd} # {TASK_TAG}"

    def install(self) -> ScheduleEntry:
        """Add or replace the CyClaw cron entry (idempotent)."""
        current = self._read_crontab().splitlines()
        # Strip any existing CyClaw entries (tagged with TASK_TAG), then append
        # exactly one fresh tagged line.
        filtered = [ln for ln in current if TASK_TAG not in ln]
        line = self._our_line()
        filtered.append(line)
        new_content = "\n".join(filtered) + "\n"
        self._write_crontab(new_content)
        return ScheduleEntry(
            platform_name=platform.system().lower(),
            command=_sync_command(self.cfg),
            cron_or_time=f"{self.cfg.schedule_min} {self.cfg.schedule_hour} * * *",
            raw=line,
        )

    def remove(self) -> bool:
        """Remove any CyClaw cron entries. Returns True if anything was removed."""
        current = self._read_crontab().splitlines()
        filtered = [ln for ln in current if TASK_TAG not in ln]
        if len(filtered) == len(current):
            return False
        new_content = "\n".join(filtered) + ("\n" if filtered else "")
        self._write_crontab(new_content)
        return True

    def status(self) -> ScheduleEntry | None:
        """Return the active entry if installed, else None."""
        for ln in self._read_crontab().splitlines():
            if TASK_TAG in ln:
                # Expected shape: "MIN HOUR * * * cmd # TAG"
                parts = ln.split(maxsplit=5)
                if len(parts) >= 6:
                    cron_expr = " ".join(parts[:5])
                    return ScheduleEntry(
                        platform_name=platform.system().lower(),
                        command=parts[5].rsplit("#", 1)[0].strip(),
                        cron_or_time=cron_expr,
                        raw=ln,
                    )
        return None


# ---------------------------------------------------------------------------
# Windows -- schtasks
# ---------------------------------------------------------------------------


class WindowsTaskScheduler:
    """Manage a single CyClaw task via schtasks.exe."""

    def __init__(self, cfg: RcloneConfig) -> None:
        self.cfg = cfg

    @staticmethod
    def _schtasks() -> str:
        path = shutil.which("schtasks")
        if not path:
            raise SchedulerError(
                "schtasks.exe not available on PATH",
                details={"hint": "schtasks is a built-in Windows tool. Run on Windows, not WSL."},
            )
        return path

    def install(self) -> ScheduleEntry:
        # Register a .bat launcher path (robust) rather than an inline cmd /c
        # string (quote-fragile through schtasks /TR for paths with spaces).
        launcher = _write_windows_launcher(self.cfg)
        time_str = f"{self.cfg.schedule_hour:02d}:{self.cfg.schedule_min:02d}"
        argv = [
            self._schtasks(),
            "/Create",
            "/TN",
            WINDOWS_TASK_NAME,
            "/TR",
            launcher,
            "/SC",
            "DAILY",
            "/ST",
            time_str,
            "/F",  # force overwrite of an existing task with the same name
            "/RL",
            "LIMITED",
        ]
        try:
            proc = subprocess.run(  # noqa: S603  # argv list, schtasks resolved via shutil.which
                argv, capture_output=True, text=True, timeout=15, check=False
            )
        except subprocess.SubprocessError as exc:
            raise SchedulerError(f"schtasks /Create failed: {exc}") from exc
        if proc.returncode != 0:
            raise SchedulerError(
                f"schtasks /Create failed (rc={proc.returncode}): {proc.stderr.strip()}",
                details={"stderr": proc.stderr[:500]},
            )
        return ScheduleEntry(
            platform_name="windows",
            command=launcher,
            cron_or_time=time_str,
            raw=proc.stdout.strip(),
        )

    def remove(self) -> bool:
        argv = [self._schtasks(), "/Delete", "/TN", WINDOWS_TASK_NAME, "/F"]
        try:
            proc = subprocess.run(  # noqa: S603  # argv list, schtasks resolved via shutil.which
                argv, capture_output=True, text=True, timeout=15, check=False
            )
        except subprocess.SubprocessError as exc:
            raise SchedulerError(f"schtasks /Delete failed: {exc}") from exc
        if proc.returncode == 0:
            return True
        # schtasks /Delete returns nonzero when the task didn't exist -- treat
        # "not found" as a no-op (False), never an error.
        combined = proc.stdout + proc.stderr
        if "cannot find the file specified" in combined or "does not exist" in combined.lower():
            return False
        if proc.returncode == 1:
            return False
        raise SchedulerError(
            f"schtasks /Delete failed (rc={proc.returncode}): {proc.stderr.strip()}",
            details={"stderr": proc.stderr[:500]},
        )

    def status(self) -> ScheduleEntry | None:
        argv = [self._schtasks(), "/Query", "/TN", WINDOWS_TASK_NAME, "/FO", "LIST"]
        try:
            proc = subprocess.run(  # noqa: S603  # argv list, schtasks resolved via shutil.which
                argv, capture_output=True, text=True, timeout=15, check=False
            )
        except subprocess.SubprocessError:
            return None
        if proc.returncode != 0:
            return None
        return ScheduleEntry(
            platform_name="windows",
            command=_sync_command(self.cfg),
            cron_or_time=f"{self.cfg.schedule_hour:02d}:{self.cfg.schedule_min:02d}",
            raw=proc.stdout.strip(),
        )


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def get_scheduler(cfg: RcloneConfig) -> CronScheduler | WindowsTaskScheduler:
    """Return the right scheduler for the current OS.

    linux/darwin -> CronScheduler; windows -> WindowsTaskScheduler; anything
    else raises SchedulerError.
    """
    sys_name = platform.system().lower()
    if sys_name == "windows":
        return WindowsTaskScheduler(cfg)
    if sys_name in ("linux", "darwin"):
        return CronScheduler(cfg)
    raise SchedulerError(
        f"Unsupported platform for scheduling: {sys_name}",
        details={"platform": sys_name, "supported": ["linux", "darwin", "windows"]},
    )
