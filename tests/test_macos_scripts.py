"""Regression test pinning macos/*.sh's duplicated CyClaw home-dir literal.

install-cyclaw.sh and invoke-cyclaw.sh each hardcode the "~/.CyClaw" home
directory independently (shell scripts can't import harness/config.py's
_default_home()). invoke-cyclaw.sh's CYCLAW_HOME fallback drifted to the
undotted "$HOME/CyClaw" for a while -- masked in the common path because the
installed `cyclaw` shim always exports CYCLAW_HOME first, but broke direct
invocation of the script without that env var pre-set. This pins the literal
so a future edit to either file fails CI instead of silently reintroducing
the drift.
"""

from __future__ import annotations

import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CANONICAL_HOME_SUFFIX = ".CyClaw"
_BASH = shutil.which("bash") or "bash"


def test_invoke_cyclaw_home_dir_matches_install_cyclaw() -> None:
    invoke_text = (_REPO_ROOT / "macos" / "invoke-cyclaw.sh").read_text(encoding="utf-8")
    install_text = (_REPO_ROOT / "macos" / "install-cyclaw.sh").read_text(encoding="utf-8")

    invoke_match = re.search(r'HOME_DIR="\$\{CYCLAW_HOME:-\$HOME/([^}"]+)\}"', invoke_text)
    install_match = re.search(r'HOME_DIR="\$HOME/([^"]+)"', install_text)

    assert invoke_match, "invoke-cyclaw.sh's HOME_DIR default pattern not found -- update this test's regex"
    assert install_match, "install-cyclaw.sh's HOME_DIR literal not found -- update this test's regex"
    assert invoke_match.group(1) == _CANONICAL_HOME_SUFFIX
    assert install_match.group(1) == _CANONICAL_HOME_SUFFIX
    assert invoke_match.group(1) == install_match.group(1)


def test_invoke_cyclaw_probes_harness_startup_and_watches_both_pids() -> None:
    """The harness must get the same startup-death probe the gateway already
    has, and the script must not block forever on one PID if the other dies."""
    text = (_REPO_ROOT / "macos" / "invoke-cyclaw.sh").read_text(encoding="utf-8")
    assert "/api/status" in text, "harness startup probe must target /api/status"
    assert "HARNESS_READY=0" in text, "harness startup readiness variable missing"
    assert "coding harness exited during startup" in text, "harness startup death message missing"
    # Dual-PID liveness loop replaces the old single wait.
    assert "while true; do" in text, "liveness watch loop missing"
    assert "kill -0 \"$HARNESS_PID\"" in text
    assert "kill -0 \"$GATE_PID\"" in text
    # macOS /bin/bash is 3.2; bash-4.3 wait-any is not portable.
    assert not any(line.strip().startswith("wait -n") for line in text.splitlines()), (
        "liveness loop must not call bash-4.3 wait-any"
    )
