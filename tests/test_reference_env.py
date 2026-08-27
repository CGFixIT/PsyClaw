"""The reference .env is contractual, not documentation.

docs/security-philosophy/cyclaw_telemetry_kill.env promises an operator that
sourcing it delivers the canonical block to every child process. That promise
held only on paper before issue #1135: the file used bare KEY=value lines,
which set shell-local variables a child never inherits. These tests source
the real file in a real shell, launch a real child Python, and assert the
exact inherited values -- plus the format rules (export-form, no duplicates,
three sections) that keep the promise enforceable. The value-agreement half
against the production maps is the otel-hardening checker's T8; the
independent expected values here are deliberately a THIRD copy for the
handful of highest-consequence names.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENV_FILE = REPO_ROOT / "docs" / "security-philosophy" / "cyclaw_telemetry_kill.env"

_EXPORT_RE = re.compile(r"^export ([A-Z][A-Z0-9_]*)=(.*)$")

# Independent spot-check values (not derived from any production module).
_MUST_INHERIT = {
    "OTEL_SDK_DISABLED": "true",
    "CHROMA_OTEL_GRANULARITY": "none",
    "CHROMA_OTEL_COLLECTION_ENDPOINT": "",
    "GH_TELEMETRY": "false",
    "ORT_DISABLE_TELEMETRY": "1",
    "POWERSHELL_TELEMETRY_OPTOUT": "1",
    "POWERSHELL_UPDATECHECK": "Off",
    "HOMEBREW_NO_ANALYTICS": "1",
    "DO_NOT_TRACK": "1",
}

# Shipped commented out: sourcing the file must NOT deliver these (an active
# pair breaks a fresh install's one-time bootstrap download); the file only
# documents the uncomment for operators with a pre-seeded cache.
_MUST_STAY_COMMENTED = ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE")


def _parsed_lines() -> list[tuple[int, str, str]]:
    out = []
    for lineno, raw in enumerate(ENV_FILE.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _EXPORT_RE.match(stripped)
        assert m, f"line {lineno} is not `export NAME=value`: {raw!r}"
        out.append((lineno, m.group(1), m.group(2)))
    return out


def test_every_assignment_uses_export_form():
    lines = _parsed_lines()
    assert lines, "reference env has no assignments at all"


def test_no_duplicate_names():
    names = [name for _, name, _ in _parsed_lines()]
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate exports: {sorted(dupes)}"


def test_three_sections_present_in_order():
    text = ENV_FILE.read_text(encoding="utf-8")
    markers = ["--- 1. Unconditional telemetry kill",
               "--- 2. Ancillary update-check opt-outs",
               "--- 3. Conditional strict-offline"]
    positions = [text.find(m) for m in markers]
    assert all(p >= 0 for p in positions), f"missing section marker(s): {list(zip(markers, positions, strict=False))}"
    assert positions == sorted(positions), "section markers out of order"


def test_spot_values_match_independent_expectations():
    documented = {name: value for _, name, value in _parsed_lines()}
    for name, value in _MUST_INHERIT.items():
        assert documented.get(name) == value, (
            f"{name}: file has {documented.get(name)!r}, independent expectation {value!r}"
        )


def test_strict_offline_pair_ships_commented():
    text = ENV_FILE.read_text(encoding="utf-8")
    documented = {name for _, name, _ in _parsed_lines()}
    for name in _MUST_STAY_COMMENTED:
        assert f"# export {name}=1" in text, f"{name} must be documented in commented form"
        assert name not in documented, (
            f"{name} is actively exported -- the strict-offline pair must stay "
            "commented; source cannot pick sections and an active export breaks "
            "first-run bootstrap"
        )


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell sourcing semantics")
def test_sourcing_delivers_exact_values_to_a_child_python(tmp_path):
    """The actual promise: source the file, exec a child Python, read the
    child's os.environ. A bare KEY=value file passes every static test above
    except the regex -- this one fails it behaviorally too."""
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json, os, sys\n"
        f"names = {sorted(_MUST_INHERIT)!r}\n"
        "print(json.dumps({n: os.environ.get(n) for n in names}))\n",
        encoding="utf-8",
    )
    script = f'set -e\nsource "{ENV_FILE}"\nexec "{sys.executable}" "{probe}"\n'
    completed = subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        # A minimal parent environment: inheritance must come from the
        # sourced file, not from this test process's own (killed) env.
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    assert completed.returncode == 0, completed.stderr
    inherited = json.loads(completed.stdout.strip())
    assert inherited == _MUST_INHERIT
    # And the commented strict-offline pair must NOT have been delivered.
    probe2 = tmp_path / "probe2.py"
    probe2.write_text(
        "import os\n"
        "assert 'HF_HUB_OFFLINE' not in os.environ\n"
        "assert 'TRANSFORMERS_OFFLINE' not in os.environ\n",
        encoding="utf-8",
    )
    completed2 = subprocess.run(
        ["/bin/bash", "-c", f'set -e\nsource "{ENV_FILE}"\nexec "{sys.executable}" "{probe2}"\n'],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": os.environ.get("PATH", "/usr/bin:/bin")},
    )
    assert completed2.returncode == 0, completed2.stderr


@pytest.mark.skipif(os.name == "nt", reason="POSIX shell sourcing semantics")
def test_sourcing_overwrites_hostile_ambient_values(tmp_path):
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import os\n"
        "assert os.environ['GH_TELEMETRY'] == 'false', os.environ['GH_TELEMETRY']\n"
        "assert os.environ['OTEL_SDK_DISABLED'] == 'true'\n",
        encoding="utf-8",
    )
    script = f'set -e\nsource "{ENV_FILE}"\nexec "{sys.executable}" "{probe}"\n'
    completed = subprocess.run(
        ["/bin/bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "GH_TELEMETRY": "true",
            "OTEL_SDK_DISABLED": "false",
        },
    )
    assert completed.returncode == 0, completed.stderr
