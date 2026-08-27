"""Tests for guardrails.profiles — shipped matrix truth + loader fail-closed rules."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from guardrails.errors import GuardrailsConfigError
from guardrails.profiles import (
    CONFIGURED_UNIMPLEMENTED,
    IMPLEMENTED_RAILS,
    KNOWN_STAGES,
    load_profiles,
)

_ALL_OUT = dict.fromkeys(sorted(KNOWN_STAGES), "out-of-scope")


def _write_profiles(tmp_path: Path, profiles: list[dict]) -> Path:
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump({"profiles": profiles}), encoding="utf-8")
    return path


def _minimal_profile(name: str, **overrides: object) -> dict:
    base: dict = {
        "name": name,
        "stages": dict(_ALL_OUT),
        "rails": [],
    }
    base.update(overrides)
    return base


def test_shipped_yaml_loads():
    loaded = load_profiles()
    assert set(loaded) == {"off", "deterministic", "nemo_local", "strict_agentic"}
    assert IMPLEMENTED_RAILS == frozenset(
        {"check_injection", "check_soul_mutation", "check_grounding"}
    )
    assert CONFIGURED_UNIMPLEMENTED == frozenset({"check_jailbreak", "check_soul_leak"})


def test_off_has_all_stages_out_of_scope():
    off = load_profiles()["off"]
    assert set(off["stages"]) == KNOWN_STAGES
    assert all(v == "out-of-scope" for v in off["stages"].values())
    assert off["rails"] == []


def test_deterministic_does_not_claim_unimplemented_rails_enforced():
    det = load_profiles()["deterministic"]
    by_name = {r["name"]: r for r in det["rails"]}
    assert by_name["check_jailbreak"]["mode"] != "enforced"
    assert by_name["check_jailbreak"]["status"] == "configured-unimplemented"
    assert by_name["check_soul_leak"]["mode"] != "enforced"
    assert by_name["check_soul_leak"]["status"] == "configured-unimplemented"
    assert by_name["check_injection"]["mode"] == "enforced"
    assert by_name["check_soul_mutation"]["mode"] == "enforced"
    assert by_name["check_grounding"]["mode"] == "enforced"


def test_nemo_local_does_not_claim_colang_rails_enforced_on_query():
    nemo = load_profiles()["nemo_local"]
    by_name = {r["name"]: r for r in nemo["rails"]}
    assert by_name["check_jailbreak"]["mode"] != "enforced"
    assert by_name["self_check_facts"]["mode"] != "enforced"
    assert by_name["self_check_facts"]["status"] == "configured-unimplemented"


def test_unknown_rail_name_fails(tmp_path: Path):
    path = _write_profiles(
        tmp_path,
        [
            _minimal_profile(
                "off",
                rails=[
                    {
                        "name": "check_not_a_real_rail",
                        "status": "unknown",
                        "mode": "out-of-scope",
                    }
                ],
            )
        ],
    )
    with pytest.raises(GuardrailsConfigError, match="unknown rail name"):
        load_profiles(path)


def test_duplicate_profile_names_fail(tmp_path: Path):
    path = _write_profiles(
        tmp_path,
        [_minimal_profile("off"), _minimal_profile("off")],
    )
    with pytest.raises(GuardrailsConfigError, match="duplicate profile name"):
        load_profiles(path)


def test_empty_rail_name_fails(tmp_path: Path):
    path = _write_profiles(
        tmp_path,
        [
            _minimal_profile(
                "off",
                rails=[{"name": "", "status": "unknown", "mode": "out-of-scope"}],
            )
        ],
    )
    with pytest.raises(GuardrailsConfigError, match="rail name must be a non-empty"):
        load_profiles(path)


def test_enforced_unimplemented_rail_fails(tmp_path: Path):
    path = _write_profiles(
        tmp_path,
        [
            _minimal_profile(
                "strict_agentic",
                rails=[
                    {
                        "name": "check_jailbreak",
                        "status": "configured-unimplemented",
                        "mode": "enforced",
                        "stage": "input",
                    }
                ],
            )
        ],
    )
    with pytest.raises(GuardrailsConfigError, match="cannot be enforced"):
        load_profiles(path)


def test_unknown_stage_fails(tmp_path: Path):
    stages = dict(_ALL_OUT)
    stages["not_a_stage"] = "out-of-scope"
    path = _write_profiles(tmp_path, [_minimal_profile("off", stages=stages)])
    with pytest.raises(GuardrailsConfigError, match="unknown stage name"):
        load_profiles(path)


def test_unknown_profile_name_fails(tmp_path: Path):
    path = _write_profiles(tmp_path, [_minimal_profile("fantasy")])
    with pytest.raises(GuardrailsConfigError, match="unknown profile name"):
        load_profiles(path)
