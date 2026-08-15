"""Contract + behavior tests for macos/setup-from-clone.sh.

Static pins keep the orchestrator from drifting off the existing macos/
scripts or reintroducing bash-4 / GNU-only / secret-in-argv patterns.
Behavioral tests cover --help / --dry-run / unknown-option / platform gate
without needing Darwin, Homebrew, Ollama, or a real venv.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "macos" / "setup-from-clone.sh"
_BASH = shutil.which("bash") or "bash"

_BASH4_ONLY = ("declare -A", "mapfile", "readarray", "local -n")
_CHAINED = (
    "macos/install-cyclaw.sh",
    "macos/setup-cyclaw-keys.sh",
    "macos/invoke-cyclaw.sh",
)
_KEY_ENVS = (
    "CYCLAW_API_KEY",
    "ANTHROPIC_API_KEY",
    "GROK_API_KEY",
    "TELEGRAM_BOT_TOKEN",
    "GH_TOKEN",
    "GITHUB_TOKEN",
)


pytestmark = pytest.mark.skipif(
    os.name == "nt", reason="requires a POSIX shell (bash)"
)


def _script_text() -> str:
    return _SCRIPT.read_text(encoding="utf-8")


def test_script_is_executable_with_shebang() -> None:
    mode = _SCRIPT.stat().st_mode
    assert mode & stat.S_IXUSR
    assert _script_text().startswith("#!/usr/bin/env bash")


def test_help_exits_zero() -> None:
    result = subprocess.run(
        [_BASH, str(_SCRIPT), "--help"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "CYCLAW_API_KEY" in result.stdout
    assert "Telegram" in result.stdout
    assert "invoke-cyclaw.sh" in result.stdout


def test_unknown_option_exits_one() -> None:
    result = subprocess.run(
        [_BASH, str(_SCRIPT), "--not-a-flag"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 1
    assert "unknown option" in result.stderr


def test_dry_run_prints_plan_and_exits_zero() -> None:
    env = os.environ.copy()
    env["CYCLAW_SETUP_FROM_CLONE_SKIP_PLATFORM"] = "1"
    result = subprocess.run(
        [_BASH, str(_SCRIPT), "--dry-run"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout
    assert "dry-run plan" in out
    assert "install-cyclaw.sh" in out
    assert "setup-cyclaw-keys.sh" in out
    assert "invoke-cyclaw.sh" in out
    assert "retrieval.indexer" in out
    assert "export CYCLAW_API_KEY=" not in out
    for key in _KEY_ENVS:
        assert f"{key}=" not in out


def test_dry_run_honors_skip_flags() -> None:
    env = os.environ.copy()
    env["CYCLAW_SETUP_FROM_CLONE_SKIP_PLATFORM"] = "1"
    result = subprocess.run(
        [
            _BASH,
            str(_SCRIPT),
            "--dry-run",
            "--skip-install",
            "--skip-keys",
            "--skip-ollama",
            "--skip-index",
            "--no-start",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert "skip installer" in result.stdout
    assert "skip keys" in result.stdout
    assert "skip Ollama" in result.stdout
    assert "skip index" in result.stdout
    assert "skip servers" in result.stdout


def test_platform_gate_rejects_non_darwin_without_skip() -> None:
    env = os.environ.copy()
    env.pop("CYCLAW_SETUP_FROM_CLONE_SKIP_PLATFORM", None)
    # Force the gate to see a non-Darwin uname by running only if we are
    # not already on Darwin/arm64. On Darwin the gate is the real one.
    if os.uname().sysname == "Darwin" and os.uname().machine == "arm64":
        pytest.skip("this host IS Apple Silicon — gate accepts, cannot reject")
    result = subprocess.run(
        [_BASH, str(_SCRIPT), "--dry-run"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )
    assert result.returncode == 1
    assert "Apple Silicon" in result.stderr or "macOS Apple Silicon" in result.stderr


def test_chains_existing_macos_scripts_does_not_reimplement_them() -> None:
    text = _script_text()
    for name in _CHAINED:
        assert name in text, f"orchestrator must invoke {name}"
    # Must not re-copy the torch/manifest-stripping block from install-cyclaw.sh
    assert "torch==2.13.0+cpu" not in text
    assert "--extra-index-url https://download.pytorch.org" not in text
    # Must not re-copy Keychain persist internals
    assert "com.cgfixit.cyclaw.api-key" not in text
    assert "add-generic-password" not in text
    # Must not run the skill harness that git-fetches origin/main
    assert "bootstrap.sh" in text  # mentioned as something we will NOT run
    assert "bash .claude/skills/cyclaw-advisor/bootstrap.sh" not in text


def test_bash32_and_bsd_userland() -> None:
    text = _script_text()
    for token in _BASH4_ONLY:
        assert token not in text
    # GNU-only grep / sed flags that break stock macOS
    assert "grep -P" not in text
    assert "sed -i " not in text


def test_never_enables_fsconnect_writes_or_indexing() -> None:
    text = _script_text()
    assert "writes_enabled: true" not in text
    assert "index_enabled: true" not in text
    assert "writes_enabled=true" not in text


def test_never_writes_secrets_to_config_yaml() -> None:
    text = _script_text()
    # The orchestrator may READ config.yaml for the shipped model tag.
    # It must never write a key into it (cyclaw-advisor).
    assert "config.yaml" in text
    for key in _KEY_ENVS:
        # No assignment-into-config pattern.
        assert not re.search(
            rf"{re.escape(key)}.{{0,40}}config\.yaml", text
        ), f"{key} appears to be written toward config.yaml"
    # Explicit: no `export KEY=value` of operator tokens in this file.
    # Comments and step() messages may name the variables; that is fine.
    assert not re.search(
        r"^export (CYCLAW_API_KEY|ANTHROPIC_API_KEY|GROK_API_KEY|TELEGRAM_BOT_TOKEN|GH_TOKEN|GITHUB_TOKEN)=",
        text,
        re.M,
    )


def test_gh_login_uses_stdin_not_argv() -> None:
    text = _script_text()
    assert "gh auth login --with-token" in text
    # The token must be piped, never `--token "$GH_TOKEN"` / argv.
    assert "--token" not in text
    assert "printf '%s\\n' \"$GH_TOKEN\" | gh auth login --with-token" in text


def test_home_dir_literal_matches_install_and_invoke() -> None:
    text = _script_text()
    match = re.search(r'HOME_DIR="\$\{CYCLAW_HOME:-\$HOME/([^}"]+)\}"', text)
    assert match, "HOME_DIR default pattern not found"
    assert match.group(1) == ".CyClaw"


def test_default_and_small_ollama_tags_are_documented() -> None:
    text = _script_text()
    assert 'DEFAULT_MODEL="qwen3.6:27b"' in text
    assert 'SMALL_DEFAULT="qwen2.5:7b"' in text
    assert "--small-model" in text
    assert "guardrails.model" in text  # C11 mismatch warning


def test_refuses_curl_pipe_sh_without_explicit_flag() -> None:
    text = _script_text()
    assert "--ollama-install-script" in text
    # The pipe exists, but only behind the flag.
    assert "curl -fsSL https://ollama.com/install.sh | sh" in text
    idx_flag = text.index("--ollama-install-script")
    idx_pipe = text.index("curl -fsSL https://ollama.com/install.sh | sh")
    assert idx_flag < idx_pipe
    assert "OLLAMA_INSTALL_SCRIPT" in text


def test_does_not_generate_or_load_launchagents() -> None:
    text = _script_text()
    assert "launchctl bootstrap" not in text
    assert "launchctl load" not in text
    assert "generate_service_plist" not in text


def test_execs_invoke_so_ctrl_c_owns_the_tree() -> None:
    text = _script_text()
    assert "exec bash" in text
    assert "invoke-cyclaw.sh" in text
    assert "--repo" in text


def test_mentions_cyclaw_advisor_privacy_contract() -> None:
    text = _script_text()
    assert "cyclaw-advisor" in text
    assert "never log secret values" in text
    assert "never write them to config.yaml" in text
    assert ".claude/skills/cyclaw-advisor/verify.sh" in text
    assert "I6" in text
    assert "xtrace" in text


def test_does_not_flip_hybrid_or_soul() -> None:
    text = _script_text()
    assert "do not flip app.mode or models.*.enabled" in text
    assert "do not touch soul.md" in text
    # No write-redirect into those files.
    assert not re.search(r">{1,2}\s*[\"']?.*config\.yaml", text)
    assert not re.search(r">{1,2}\s*[\"']?.*soul\.md", text)
