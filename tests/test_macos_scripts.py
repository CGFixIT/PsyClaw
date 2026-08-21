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
from pathlib import Path

import yaml

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


def test_installer_preserves_patched_config_across_updates() -> None:
    install_text = (_REPO_ROOT / "macos" / "install-cyclaw.sh").read_text(encoding="utf-8")
    assert 'git -C "$REPO_DIR" pull --ff-only --autostash' in install_text


def test_macos_scripts_never_enable_writes_or_indexing() -> None:
    script_names = ("install-cyclaw.sh", "setup-fsconnect.sh", "setup-from-clone.sh")

    combined = "\n".join(
        (_REPO_ROOT / "macos" / name).read_text(encoding="utf-8") for name in script_names
    )
    helper = (_REPO_ROOT / "macos" / "_enable_fsconnect_readlist.py").read_text(encoding="utf-8")
    assert "writes_enabled: true" not in combined
    assert "index_enabled: true" not in combined
    assert '"writes_enabled": True' not in helper
    assert '"index_enabled": True' not in helper


def test_macos_setup_contract_and_flags_are_narrow() -> None:
    setup = (_REPO_ROOT / "macos" / "setup-fsconnect.sh").read_text(encoding="utf-8")
    installer = (_REPO_ROOT / "macos" / "install-cyclaw.sh").read_text(encoding="utf-8")
    shipped = yaml.safe_load((_REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))

    assert shipped["fsconnect"]["enabled"] is False
    assert "--prepare-only" in setup
    assert "--no-fsconnect" in installer
    assert "<<'README'" in setup
    assert "reject_shell_metachars \"$REPO_DIR\"" in installer
    for unsupported in ("declare -A", "mapfile", "readarray", "local -n"):
        assert unsupported not in setup


def test_fsconnect_trash_launchagent_is_disabled_and_secret_free() -> None:
    plist_path = _REPO_ROOT / "macos" / "LaunchAgents" / "com.cgfixit.cyclaw.fsconnect-trash.plist"
    plist_bytes = plist_path.read_bytes()
    document = plistlib.loads(plist_bytes)
    arguments = document["ProgramArguments"]

    assert document["Label"] == "com.cgfixit.cyclaw.fsconnect-trash"
    assert document["RunAtLoad"] is False
    assert "KeepAlive" not in document
    assert document["StartCalendarInterval"]["Weekday"] == 1
    assert arguments[1:3] == ["-m", "agentic.fsconnect.cli"]
    assert "trash-empty" in arguments
    assert "--root" in arguments
    assert "--reason" in arguments
    assert "--confirm" in arguments
    assert "--all" not in arguments
    assert "EnvironmentVariables" not in document
    assert any("REPLACE_" in value for value in arguments)
    assert b"mkdir -p ~/Library/Logs/CyClaw" in plist_bytes


def test_uninstaller_bootouts_landed_launchagent_labels() -> None:
    """Uninstall must name every generated CyClaw LaunchAgent label.

    Sync is handled by sync.cli unschedule; these seven are not. Bootout of
    an unloaded label is a no-op, and uninstall must not leave a KeepAlive
    or crash-restart job behind if the operator generated one.
    """
    labels = (
        "com.cgfixit.cyclaw.telegram-poll",
        "com.cgfixit.cyclaw.telegram-health",
        "com.cgfixit.cyclaw.fsconnect-trash",
        "com.cgfixit.cyclaw.gate",
        "com.cgfixit.cyclaw.harness",
        "com.cgfixit.cyclaw.keys-rotate",
        "com.cgfixit.cyclaw.opentweet",
    )
    text = (_REPO_ROOT / "macos" / "uninstall-cyclaw.sh").read_text(encoding="utf-8")
    assert "unschedule_landed_launchagents" in text
    assert "launchctl bootout" in text
    for label in labels:
        assert label in text
    # Label-domain bootout must run even when the plist file is already gone.
    assert 'bootout "gui/${uid}/${label}"' in text

    # Docs must not still claim "three landed" agents or that gate/harness
    # survive uninstall (#922 landed with #912). The brace list and the
    # "seven generated" phrasing must match the live uninstall loop.
    harness_doc = (_REPO_ROOT / "docs" / "HARNESS_MACOS.md").read_text(encoding="utf-8")
    assert "three landed generated LaunchAgents" not in harness_doc
    assert "future gate/harness agent, are left alone" not in harness_doc
    assert "five generated LaunchAgent labels" not in harness_doc
    assert "seven generated LaunchAgent labels" in harness_doc
    for label in labels:
        short = label.removeprefix("com.cgfixit.cyclaw.")
        assert short in harness_doc
    readme = (_REPO_ROOT / "macos" / "README.md").read_text(encoding="utf-8")
    assert "gate, harness" in readme
    assert "keys-rotate" in readme
    assert "opentweet" in readme


def test_all_shipped_launchagent_templates_are_well_formed_xml() -> None:
    """Every macos/LaunchAgents/*.plist must plistlib-parse.

    Regression guard: a literal "--" inside an XML comment (e.g. an
    embedded CLI flag like --chat-id or --api-key-service, easy to type
    without noticing the XML significance) makes the whole document
    invalid per the XML spec, silently breaking `launchctl load` even
    though the file looks fine to a human reader. Caught for real in this
    repo's history -- see the PR that added this test.
    """
    launch_agents_dir = _REPO_ROOT / "macos" / "LaunchAgents"
    plist_files = sorted(launch_agents_dir.glob("*.plist"))
    assert len(plist_files) >= 3  # sanity: the dir isn't empty / glob isn't broken
    for path in plist_files:
        document = plistlib.loads(path.read_bytes())
        assert document["Label"].startswith("com.cgfixit.cyclaw.")
