"""Stdlib-only macOS launchd plist helpers, shared by every out-of-band
package (``sync``, ``agentic.fsconnect``, ``telegram``, ``harness``) that
generates a LaunchAgent plist from real, resolved install paths instead of a
hand-edited ``REPLACE_*`` template.

Never imported by ``gate.py``/``graph.py``/``mcp_hybrid_server.py`` (I6) --
those never touch launchd, and this module has no reason to reach them either
(pure stdlib: ``os``, ``plistlib``, ``shutil``, ``subprocess``, ``pathlib``).

Design contract every caller follows (see
``docs/work/MACOS_LAUNCHD_INTEGRATION_PLAN.md``):

  - Darwin-only. Callers gate on ``platform.system() == "Darwin"`` themselves
    -- this module has no opinion on platform and will happily write a plist
    anywhere if asked, so the caller's own guard is load-bearing.
  - Generate, don't auto-load. Nothing here ever calls ``launchctl
    load``/``bootstrap`` -- :func:`bootstrap_hint` returns the command an
    operator must run by hand.
  - No secrets in the file. Whether a generated plist's
    ``EnvironmentVariables`` (if any) stays secret-free is the caller's
    responsibility -- this module only writes/removes/probes whatever
    document dict it is given. Use :func:`wrap_with_keychain_secrets` to
    inject a runtime secret via the macOS Keychain instead of ever writing
    one into ``EnvironmentVariables``.

This module intentionally does NOT depend on ``sync.scheduler``'s
``LaunchdScheduler`` (and vice versa): both implement a small, similar
plist-write/bootout/probe pattern independently rather than sharing code
across the two, so each stays a self-contained, independently reviewable
change. See the PR that introduced this module for the reasoning.
"""

from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

# Repo-relative path to the Keychain env-injection wrapper -- see
# macos/cyclaw-keychain-env.sh's own header for the full contract.
KEYCHAIN_WRAPPER_RELATIVE_PATH = "macos/cyclaw-keychain-env.sh"


def python_executable() -> str:
    """Best-guess python interpreter to invoke from a generated plist.

    Mirrors ``sync/scheduler.py``'s own ``_python_executable()`` -- kept here
    independently too, per this module's documented decision not to couple
    with ``sync.scheduler`` (see the module docstring).
    """
    candidate = sys.executable or "python"
    if candidate and os.path.isfile(candidate):
        return candidate
    found = shutil.which("python3") or shutil.which("python")
    return found or "python"


def current_uid() -> int:
    """POSIX uid, or 0 as an inert placeholder on a non-POSIX Python.

    Every caller of this module is Darwin-only in production, where
    ``os.getuid`` always exists. The fallback exists solely so tests that
    mock ``platform.system()`` to "Darwin" but still run on whatever real
    interpreter CI is don't crash with ``AttributeError`` -- CPython's ``os``
    module omits ``getuid`` entirely on Windows, independent of what
    ``platform.system()`` is mocked to return.
    """
    return os.getuid() if hasattr(os, "getuid") else 0


def agents_dir() -> Path:
    """``~/Library/LaunchAgents`` for the real invoking user."""
    return Path.home() / "Library" / "LaunchAgents"


def logs_dir() -> Path:
    """``~/Library/Logs/CyClaw`` -- the shared log directory convention
    every shipped CyClaw plist (generated or template) already uses."""
    return Path.home() / "Library" / "Logs" / "CyClaw"


def plist_path(label: str) -> Path:
    """The on-disk path a plist with this launchd ``Label`` would occupy."""
    return agents_dir() / f"{label}.plist"


def write_plist(document: dict, path: Path) -> None:
    """Atomically write *document* as an XML plist to *path*.

    Creates the parent directory if missing. Writes to a same-directory temp
    file first and ``os.replace()``s it into place, so a crash mid-write
    never leaves a partial or corrupt plist on disk, and a re-run is a clean
    overwrite (idempotent).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "wb") as f:
        plistlib.dump(document, f, fmt=plistlib.FMT_XML)
    os.replace(tmp_path, path)


def bootstrap_hint(path: Path) -> str:
    """The ``launchctl`` command an operator must run by hand to load *path*.

    Never executed by this module -- see the module docstring's
    generate-don't-auto-load contract.
    """
    return f"launchctl bootstrap gui/{current_uid()} {path}"


def launchctl_bin() -> str | None:
    """Best-effort ``launchctl`` lookup; ``None`` (not an error) when absent."""
    return shutil.which("launchctl")


def bootout(path: Path) -> None:
    """Best-effort unload of the agent at *path*.

    Tolerates "not loaded" (bootout on a plist that was written but never
    bootstrapped is an expected no-op) and a missing ``launchctl`` binary --
    callers should still delete the plist file themselves afterward
    regardless of whether this actually unloaded anything.
    """
    launchctl = launchctl_bin()
    if not launchctl:
        return
    subprocess.run(  # noqa: S603 -- argv list, launchctl resolved via shutil.which
        [launchctl, "bootout", f"gui/{current_uid()}", str(path)],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def is_loaded(label: str) -> bool | None:
    """Best-effort load-state probe for the agent with this ``Label``.

    Returns ``True``/``False`` when ``launchctl`` answered, or ``None`` when
    ``launchctl`` itself is unavailable (never raises).
    """
    launchctl = launchctl_bin()
    if not launchctl:
        return None
    probe = subprocess.run(  # noqa: S603 -- argv list, launchctl resolved via shutil.which
        [launchctl, "print", f"gui/{current_uid()}/{label}"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    return probe.returncode == 0


def keychain_wrapper_path(repo_root: str | Path) -> str:
    """Absolute path to ``cyclaw-keychain-env.sh`` within *repo_root*."""
    return str(Path(repo_root) / KEYCHAIN_WRAPPER_RELATIVE_PATH)


def wrap_with_keychain_secrets(
    argv: list[str], secrets: list[tuple[str, str]], wrapper_path: str
) -> list[str]:
    """Prepend one Keychain-wrapper layer per ``(service, env_var_name)`` pair.

    Each layer is ``<wrapper_path> <service> <env_var_name> --``; the
    innermost layer's ``exec "$@"`` finally reaches *argv*. Composable: N
    secrets means N nested wrapper invocations chained via ``exec`` inside
    ``cyclaw-keychain-env.sh``, each exporting one more variable into the
    inherited environment before re-exec'ing into the next layer (or the
    real command on the last one). No secret is ever a literal value in the
    plist this argv gets embedded into -- each wrapper layer resolves its
    one secret from the Keychain at process-start time, not generation time.

    An empty *secrets* list returns *argv* unchanged.
    """
    result = list(argv)
    for service, var_name in reversed(secrets):
        result = [wrapper_path, service, var_name, "--", *result]
    return result
