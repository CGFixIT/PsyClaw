"""Contract for the hand-editable macOS LaunchAgent templates + the launcher.

These templates are the documented fallback for operators who do not use the
generators (``telegram.cli poll-plist`` / ``health-plist`` /
``fsconnect.cli trash-empty-plist``), which resolve a real interpreter via
``utils.launchd_plist.python_executable()``. The templates cannot resolve
anything, so every interpreter they name must be an obvious placeholder --
launchd hands a job a minimal PATH and macOS has shipped no ``/usr/bin/python``
since 12.3, so a bare ``python`` in ProgramArguments is a silent no-op.

Deliberately a separate module from tests/test_macos_scripts.py: that file is
being edited concurrently (PR #928) and these assertions are about a different
contract -- the templates' *executability*, not the uninstaller's label list.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LAUNCHAGENTS = _REPO_ROOT / "macos" / "LaunchAgents"
_INVOKE = _REPO_ROOT / "macos" / "invoke-cyclaw.sh"

# A token that is a bare interpreter name rather than an absolute path or a
# REPLACE_* placeholder. Anchored so "/usr/bin/python3" and
# "REPLACE_WITH_VENV_OR_SYSTEM_PYTHON" are both accepted.
_BARE_PYTHON_RE = re.compile(r"(?:^|\s|\|\|\s*|&&\s*|;\s*)(python3?)(?=\s|$)")


def _templates() -> list[Path]:
    found = sorted(_LAUNCHAGENTS.glob("*.plist"))
    assert found, f"no LaunchAgent templates found under {_LAUNCHAGENTS}"
    return found


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_program_arguments_never_invoke_a_bare_python(template: Path) -> None:
    """No ProgramArguments entry may rely on a `python` that PATH must resolve.

    Scoped to ProgramArguments on purpose: the header comments legitimately say
    "Recommended path: `python -m telegram.cli health-plist`", which is an
    instruction the operator runs in their own interactive shell, not something
    launchd executes.
    """
    with template.open("rb") as fh:
        parsed = plistlib.load(fh)
    argv = parsed.get("ProgramArguments", [])
    assert argv, f"{template.name} declares no ProgramArguments"
    for arg in argv:
        match = _BARE_PYTHON_RE.search(str(arg))
        assert match is None, (
            f"{template.name} ProgramArguments invokes a bare {match.group(1)!r}: {arg!r}. "
            "launchd gives the job a minimal PATH and macOS ships no /usr/bin/python, "
            "so this silently fails at run time. Use a REPLACE_*_PYTHON placeholder "
            "(or an absolute interpreter path) instead."
        )


@pytest.mark.parametrize("template", _templates(), ids=lambda p: p.name)
def test_every_template_names_an_interpreter_placeholder(template: Path) -> None:
    """Each template must carry a REPLACE_*_PYTHON token to substitute."""
    placeholders = set(re.findall(r"REPLACE_[A-Z_]*PYTHON", template.read_text(encoding="utf-8")))
    assert placeholders, (
        f"{template.name} names no interpreter placeholder, so whatever it invokes "
        "is whatever launchd's minimal PATH happens to resolve."
    )


def test_health_template_documents_its_interpreter_placeholder() -> None:
    """The health template's edit checklist must name the token it contains.

    This is the specific regression: the checklist said to edit
    "ProgramArguments python -m" while the body carried a bare `python` and no
    placeholder at all, so there was nothing there for an operator to find.
    """
    text = (_LAUNCHAGENTS / "com.cgfixit.cyclaw.telegram-health.plist").read_text(encoding="utf-8")
    header = text.split("<plist", 1)[0]
    assert "REPLACE_WITH_VENV_OR_SYSTEM_PYTHON" in header, (
        "the health template's header checklist does not name the interpreter "
        "placeholder its ProgramArguments actually uses"
    )
    assert "ProgramArguments python -m" not in header, (
        "the header still points at a placeholder that does not exist in the body"
    )


def test_health_template_documents_the_hardcoded_gate_port() -> None:
    """The probe URL is a literal; the header must say so.

    The generator derives the URL from telegram.query.base_url, but this
    template cannot -- an operator running gate.py on a non-default port needs
    to be told to edit it, or the agent reports failure every interval forever.
    """
    template = _LAUNCHAGENTS / "com.cgfixit.cyclaw.telegram-health.plist"
    text = template.read_text(encoding="utf-8")
    header = text.split("<plist", 1)[0]
    assert "127.0.0.1:8787/health" in text, "health template no longer probes the default port"
    assert "8787" in header, (
        "the health template hardcodes the gate port but its header checklist "
        "never tells the operator to change it for a non-default api.port"
    )


def test_invoke_launcher_validates_ports_before_use() -> None:
    """--port/--gate-port and their env defaults must be checked as integers."""
    text = _INVOKE.read_text(encoding="utf-8")
    assert "require_port()" in text, "invoke-cyclaw.sh lost its port validator"
    # Both the flag-supplied and the environment-supplied values must be checked.
    assert 'require_port "harness port' in text
    assert 'require_port "gate port' in text
    assert "65535" in text, "port validator no longer bounds the upper range"


def test_invoke_launcher_detects_a_gate_that_died_on_startup() -> None:
    """The readiness loop must check liveness, not only the socket.

    gate.py exits fast on a missing index, a bound port, or an invalid config.
    Polling /health alone let that fall through silently: the harness still
    started, a browser still opened on a dead port, and the final
    `wait "$HARNESS_PID"` blocked forever with no diagnostic.
    """
    text = _INVOKE.read_text(encoding="utf-8")
    assert 'kill -0 "$GATE_PID"' in text, (
        "invoke-cyclaw.sh no longer checks whether the gate process survived startup"
    )
    assert "RAG gateway exited during startup" in text, "the startup-failure diagnostic was removed"
    # The pid must be cleared before exiting so the EXIT trap's cleanup() does
    # not try to signal an already-reaped process. Split on the branch's own
    # `exit 1` rather than on "fi" -- prose in the surrounding comments contains
    # that substring (e.g. "first").
    gate_death_block = text.split('if ! kill -0 "$GATE_PID"', 1)[1].split("exit 1", 1)[0]
    assert 'GATE_PID=""' in gate_death_block, (
        "the gate-death branch must clear GATE_PID before exit so cleanup() skips it"
    )
