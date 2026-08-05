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

import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CANONICAL_HOME_SUFFIX = ".CyClaw"


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
