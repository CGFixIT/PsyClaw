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

import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

import pytest
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
    assert "CHILD_EXIT_STATUS=$?" in text, "liveness watch must preserve a failed child's status"
    assert 'exit "$CHILD_EXIT_STATUS"' in text, "launcher must propagate the child exit status"
    # macOS /bin/bash is 3.2; bash-4.3 wait-any is not portable.
    assert not any(line.strip().startswith("wait -n") for line in text.splitlines()), (
        "liveness loop must not call bash-4.3 wait-any"
    )


@pytest.mark.skipif(os.name == "nt", reason="requires POSIX child-process exit semantics")
def test_invoke_cyclaw_propagates_a_post_start_child_failure(tmp_path: Path) -> None:
    """A harness that dies after the startup probe must not become exit 0."""
    home = tmp_path / "home"
    fake_python = home / "venv" / "bin" / "python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text("#!/bin/sh\nsleep 4\nexit 37\n", encoding="utf-8")
    fake_python.chmod(0o755)

    repo = tmp_path / "repo"
    harness = repo / "harness"
    harness.mkdir(parents=True)
    (harness / "server.py").write_text("# launcher probe\n", encoding="utf-8")

    env = os.environ.copy()
    env["CYCLAW_HOME"] = str(home)
    result = subprocess.run(
        [
            _BASH,
            str(_REPO_ROOT / "macos" / "invoke-cyclaw.sh"),
            "--repo",
            str(repo),
            "--no-gate",
            "--no-browser",
        ],
        cwd=_REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 37, result.stdout + result.stderr
    assert "harness process" in result.stderr


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


def test_setup_cyclaw_keys_disowns_clipboard_clear_job() -> None:
    """The pasteboard TTL clearer must survive script exit.

    Without `disown`, the background subshell receives SIGHUP when the
    parent script exits and may die before clearing the key from the
    pasteboard. The clear job must also be silent (no job-control noise).
    """
    setup = (_REPO_ROOT / "macos" / "setup-cyclaw-keys.sh").read_text(encoding="utf-8")
    # Locate the block that forks the TTL clearer.
    match = re.search(
        r"sleep \"\$CLIP_TTL\".*?\) >/dev/null 2>&1 &",
        setup,
        re.DOTALL,
    )
    assert match, "clipboard TTL background job not found"
    # disown must immediately follow the fork so the job is detached.
    after_fork = setup[match.end():match.end() + 200]
    assert "disown" in after_fork, "clipboard clear job is not disowned"


def test_setup_cyclaw_keys_warns_when_installed_copy_drifted() -> None:
    """An installed LaunchAgent copy that differs from the running script
    must warn the operator to re-run --schedule-rotate."""
    setup = (_REPO_ROOT / "macos" / "setup-cyclaw-keys.sh").read_text(encoding="utf-8")
    assert "_warn_if_installed_copy_drifted" in setup
    assert "cmp -s" in setup
    assert "differs from this script" in setup


# --- setup-cyclaw.sh: single-entry onboarding wrapper (#1053) --------------


def test_setup_cyclaw_syntax_is_valid() -> None:
    result = subprocess.run(
        [_BASH, "-n", str(_REPO_ROOT / "macos" / "setup-cyclaw.sh")],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_setup_cyclaw_looks_like_repo_matches_the_files_it_requires() -> None:
    """The one-script onboarding wrapper's checkout-detection gate must name
    files that actually exist in this repo, or every invocation falls
    through to "clone a fresh copy" even when run inside a real checkout."""
    setup = (_REPO_ROOT / "macos" / "setup-cyclaw.sh").read_text(encoding="utf-8")
    required = re.findall(r'\[ -f "\$1/([^"]+)" \]', setup)
    assert required, "looks_like_repo's required-file list not found -- update this test's regex"
    for relative in required:
        assert (_REPO_ROOT / relative).is_file(), f"looks_like_repo requires {relative}, which is missing"


def test_setup_cyclaw_clipboard_clear_job_is_disowned() -> None:
    """Regression guard mirroring test_setup_cyclaw_keys_disowns_clipboard_clear_job.

    setup-cyclaw.sh's copy_key_fallback forks the same kind of background
    pasteboard-clear job as setup-cyclaw-keys.sh. Without `disown` it is a
    job of this script's own shell and is killed by SIGHUP the moment the
    script exits (Ctrl+C, or simply finishing), before the sleep completes --
    leaving CYCLAW_API_KEY sitting in the pasteboard indefinitely.
    """
    setup = (_REPO_ROOT / "macos" / "setup-cyclaw.sh").read_text(encoding="utf-8")
    match = re.search(r"sleep \"\$ttl\".*?\) >/dev/null 2>&1 &", setup, re.DOTALL)
    assert match, "clipboard TTL background job not found in setup-cyclaw.sh"
    after_fork = setup[match.end():match.end() + 500]
    assert "disown" in after_fork, "clipboard clear job in setup-cyclaw.sh is not disowned"
    # disown must be the next STATEMENT after the fork -- only comments and
    # blank lines may sit between them, so it isn't just attached to a later,
    # unrelated job by coincidence of a wide search window.
    lines_before_disown = after_fork.split("disown", 1)[0].splitlines()
    for line in lines_before_disown:
        stripped = line.strip()
        assert stripped == "" or stripped.startswith("#"), f"unexpected statement before disown: {line!r}"


def test_setup_cyclaw_cleans_up_browser_fill_temp_dir_on_interrupt() -> None:
    """fill_browser_key stages CYCLAW_API_KEY in a 0600 temp file for osascript
    to read. An interrupt between mktemp and the function's own closing
    `rm -rf` must not leave that secret-bearing file behind -- same failure
    class harness/env_keys.py's _write_temp_file guards against for the
    harness console's key writer."""
    setup = (_REPO_ROOT / "macos" / "setup-cyclaw.sh").read_text(encoding="utf-8")
    assert "FILL_KEY_TMP_DIR=" in setup
    cleanup_match = re.search(r"cleanup_runner\(\) \{.*?\n\}", setup, re.DOTALL)
    assert cleanup_match, "cleanup_runner function not found"
    assert "FILL_KEY_TMP_DIR" in cleanup_match.group(0)
    assert 'rm -rf "$FILL_KEY_TMP_DIR"' in cleanup_match.group(0)


def test_setup_cyclaw_never_passes_the_api_key_as_argv() -> None:
    """Secret values must reach osascript only via the 0600 temp file path,
    never as a literal argument -- matches the script's own documented
    security contract ("Secret values are never printed or passed as
    child-process arguments")."""
    setup = (_REPO_ROOT / "macos" / "setup-cyclaw.sh").read_text(encoding="utf-8")
    assert 'osascript "$script_file" "$secret_file"' in setup
    # Every use of the variable must be a redirect (>), a pipe (|), or a
    # string comparison (=) -- never a bare word handed to a command as an
    # argv entry, which would put the key in `ps`/process-list output.
    allowed = (
        "printf '%s' \"$CYCLAW_API_KEY\" | pbcopy",
        'printf \'%s\' "$CYCLAW_API_KEY" > "$secret_file"',
        'if [ "$current" = "$CYCLAW_API_KEY" ]',
    )
    for line in setup.splitlines():
        if '"$CYCLAW_API_KEY"' not in line:
            continue
        assert any(pattern in line for pattern in allowed), f"unexpected use of the key: {line!r}"


def test_setup_cyclaw_dry_run_takes_no_action(tmp_path: Path) -> None:
    """--dry-run must plan without cloning, installing, or starting anything."""
    result = subprocess.run(
        [
            _BASH,
            str(_REPO_ROOT / "macos" / "setup-cyclaw.sh"),
            "--dry-run",
            "--repo",
            str(_REPO_ROOT),
            "--skip-prompts",
            "--start",
            "--browser",
            "--autofill-api-key",
        ],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        env={"CYCLAW_ONBOARDING_SKIP_PLATFORM": "1", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert "dry-run only; no writes or network actions will occur" in result.stdout
    assert list(tmp_path.iterdir()) == []


def test_setup_cyclaw_help_and_unknown_option() -> None:
    help_result = subprocess.run(
        [_BASH, str(_REPO_ROOT / "macos" / "setup-cyclaw.sh"), "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert help_result.returncode == 0
    assert "Usage:" in help_result.stdout

    bad_result = subprocess.run(
        [_BASH, str(_REPO_ROOT / "macos" / "setup-cyclaw.sh"), "--not-a-real-flag"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert bad_result.returncode == 1
    assert "unknown option" in bad_result.stderr
