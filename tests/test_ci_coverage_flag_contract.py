"""Lock the per-module --cov= enumeration in both pytest CI lanes.

dep-guard's D10 check already asserts that every ``[tool.coverage.run] source``
entry is measured by every lane, but it matches at *package* granularity: the
source list names ``utils`` wholesale while ci.yml and python-package-conda.yml
enumerate ``--cov=utils.<module>`` one module at a time. A single missing
submodule therefore satisfies D10 (the ``utils`` package is still "covered" by
its 20 siblings) while its real coverage is measured and then discarded, so no
regression in it can ever move the ``fail_under = 80`` gate.

That is exactly how ``utils.win_schtasks`` drifted: it shipped with a dedicated
tests/test_win_schtasks.py and was never added to either lane. This test closes
the blind spot by asserting the enumeration is complete for both lanes.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]

# The two lanes that actually run pytest with coverage. Kept in sync with
# dep-guard's _CI_COV_FILES.
_COV_LANES = (
    ".github/workflows/ci.yml",
    ".github/workflows/python-package-conda.yml",
)

_COV_FLAG_RE = re.compile(r"--cov=([A-Za-z0-9_.]+)")

# Packages the lanes deliberately enumerate module-by-module rather than
# passing wholesale. Only these need the completeness check -- a package listed
# as a bare `--cov=<pkg>` (agentic, guardrails, harness, telegram, opentweet, memory) is
# already measured in full by coverage.py itself.
_ENUMERATED_PACKAGES = ("utils", "retrieval", "sync")


def _cov_flags(rel: str) -> set[str]:
    return set(_COV_FLAG_RE.findall((_REPO_ROOT / rel).read_text(encoding="utf-8")))


def _package_modules(package: str) -> set[str]:
    return {p.stem for p in (_REPO_ROOT / package).glob("*.py") if p.stem != "__init__"}


@pytest.mark.parametrize("lane", _COV_LANES)
@pytest.mark.parametrize("package", _ENUMERATED_PACKAGES)
def test_enumerated_package_is_fully_covered_by_lane(lane: str, package: str) -> None:
    flags = _cov_flags(lane)
    # Only enforce completeness when the lane really does enumerate submodules.
    # If a future edit switches to a bare `--cov=<package>`, coverage.py walks
    # the package itself and there is nothing left to enumerate.
    if package in flags:
        return
    missing = sorted(m for m in _package_modules(package) if f"{package}.{m}" not in flags)
    assert not missing, (
        f"{lane} enumerates {package}.* module-by-module but omits: "
        f"{', '.join(missing)}. Coverage for these is measured and discarded, so a "
        f"regression in them cannot move the fail_under gate. Add a --cov= flag for each."
    )


@pytest.mark.parametrize("package", _ENUMERATED_PACKAGES)
def test_cov_lanes_agree_with_each_other(package: str) -> None:
    """Both lanes must measure the same set -- D10's original drift class.

    The conda lane silently lost --cov=gate_ops once (2026-07-19), understating
    its total and blinding it to a regression the ubuntu lane would have caught.
    """
    per_lane = {lane: {f for f in _cov_flags(lane) if f.startswith(f"{package}.")} for lane in _COV_LANES}
    first, second = _COV_LANES
    assert per_lane[first] == per_lane[second], (
        f"{package}.* --cov flags differ between lanes: "
        f"only in {first}: {sorted(per_lane[first] - per_lane[second])}; "
        f"only in {second}: {sorted(per_lane[second] - per_lane[first])}"
    )


def test_win_schtasks_is_measured() -> None:
    """Regression pin for the specific module this contract was added for."""
    assert (_REPO_ROOT / "utils" / "win_schtasks.py").exists()
    for lane in _COV_LANES:
        assert "utils.win_schtasks" in _cov_flags(lane), f"{lane} does not measure utils.win_schtasks"


def test_endpoint_trust_is_measured() -> None:
    """Regression pin for the module that broke CI on 2026-08-27."""
    assert (_REPO_ROOT / "utils" / "endpoint_trust.py").exists()
    for lane in _COV_LANES:
        assert "utils.endpoint_trust" in _cov_flags(lane), f"{lane} does not measure utils.endpoint_trust"


def test_tool_broker_is_measured() -> None:
    """Regression pin for the module that broke CI on 2026-08-27 (#1157)."""
    assert (_REPO_ROOT / "utils" / "tool_broker.py").exists()
    for lane in _COV_LANES:
        assert "utils.tool_broker" in _cov_flags(lane), f"{lane} does not measure utils.tool_broker"
