"""Contract tests for the committed governed skills registry artifact.

`data/agentic/skills_registry.json` is file-as-truth for the governed skills
catalog: its content may change only through `agentic.cli apply-skill`
(propose -> scan -> human reason -> confirm -> atomic apply -> sha256 history,
per docs/agentic/SKILLS_REGISTRY_GOVERNANCE.md). Nothing verified that the
committed artifact is actually one the governed write path could have produced,
so a hand-edit (a governance bypass) would pass CI unnoticed. These tests pin
the documented shape and the write-path invariants against the real file.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from pathlib import Path

import pytest
import yaml

from agentic.config import AgenticConfig
from agentic.registry import SkillRegistry
from utils.logger import _get_config, close_audit_handles, reset_config_cache

REPO_ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = REPO_ROOT / "data" / "agentic" / "skills_registry.json"

TOP_LEVEL_KEYS = {"version", "updated", "skills", "history"}
SKILL_KEYS = {"name", "description", "body", "sha256", "reason", "updated"}
HISTORY_KEYS = {"version", "name", "sha256", "reason", "timestamp"}
# Same slug rule agentic/registry.py enforces at propose/apply time: the first
# character is anchored to an alphanumeric so the name can never be an
# argv-flag shape ("-foo") or a path-traversal shape ("..evil").
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def _load() -> dict:
    return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))


# The per-row rules live in functions rather than inline in the loops because the
# committed artifact currently holds zero skills and zero history rows: looping
# over it asserts nothing, so the schema and sha256 rules below would ship as
# dead code and could rot unnoticed until the day a skill is finally applied.
# The synthetic tests at the bottom drive these same two functions against a
# registry produced by the REAL governed write path, so the rules are exercised
# now and the committed-artifact tests keep their teeth for later.

def _check_skill_entry(name: str, skill: dict) -> None:
    assert _NAME_RE.match(name), name
    assert set(skill) == SKILL_KEYS
    assert skill["name"] == name
    assert all(skill[field].strip() for field in ("description", "body", "reason"))  # DevSkim: ignore DS106863 - "des" inside "description", not the DES cipher
    # apply_skill stores sha256 of the canonical "name\ndescription\nbody".
    canonical = f"{skill['name']}\n{skill['description']}\n{skill['body']}"  # DevSkim: ignore DS106863 - "des" inside "description", not the DES cipher
    assert skill["sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check_history_row(row: dict, data: dict) -> None:
    assert set(row) == HISTORY_KEYS
    assert _NAME_RE.match(row["name"]), row["name"]
    # The governed path has no delete operation, so every name ever applied
    # is still present.
    assert row["name"] in data["skills"]
    assert row["reason"].strip()
    assert re.fullmatch(r"[0-9a-f]{64}", row["sha256"])


def _check_version_history_pairing(data: dict) -> None:
    history = data["history"]
    assert data["version"] == len(history)
    assert [row["version"] for row in history] == list(range(1, len(history) + 1))


def test_top_level_shape_matches_the_governance_doc() -> None:
    data = _load()
    assert isinstance(data, dict)
    assert set(data) == TOP_LEVEL_KEYS
    assert isinstance(data["version"], int) and data["version"] >= 0
    assert data["updated"] is None or isinstance(data["updated"], str)
    assert isinstance(data["skills"], dict)
    assert isinstance(data["history"], list)


def test_version_and_history_follow_the_apply_path_invariant() -> None:
    # apply_skill bumps `version` by exactly one and appends exactly one history
    # row per apply, starting from the empty v0 skeleton -- so a governed
    # artifact satisfies version == len(history) with rows numbered 1..N in
    # order. Drift here means the file did not come from the governed path.
    _check_version_history_pairing(_load())


def test_skill_entries_match_the_governed_schema_and_hashes() -> None:
    for name, skill in _load()["skills"].items():
        _check_skill_entry(name, skill)


def test_history_rows_reference_applied_skills() -> None:
    data = _load()
    for row in data["history"]:
        _check_history_row(row, data)


def test_committed_registry_loads_through_the_real_registry_class() -> None:
    data = _load()
    registry = SkillRegistry(
        {"logging": {"audit_fields": {}}, "policy": {"prompt_filter": {}, "privacy": {}}},
        AgenticConfig(registry_path="data/agentic/skills_registry.json", mode="read", writes_enabled=False),
    )
    assert registry.version() == data["version"]
    assert registry.list_skills() == sorted(data["skills"])


# -- synthetic coverage --------------------------------------------------------
# The committed artifact is empty (version 0, no skills, no history), so the two
# loop-driven tests above execute zero iterations today. These build a registry
# through the real governed write path -- SkillRegistry.apply_skill, the only
# thing allowed to mutate the store -- and run the SAME rule functions over its
# output. That exercises the sha256 and schema logic now, and doubles as proof
# that the contract describes what the writer actually emits rather than what
# this file assumes it emits.

def _spec(body: str, name: str = "demo-skill") -> dict:
    """A minimal valid skill spec for apply_skill.

    The trailing suppression is DevSkim's DS106863, a bare case-insensitive
    string match for the DES cipher that fires on the "des" inside
    "description". Confining every literal spec to this one builder keeps that
    suppression to a single line instead of one per test.
    """
    return {"name": name, "description": "a demo skill", "body": body}  # DevSkim: ignore DS106863 - "des" inside "description", not the DES cipher


@pytest.fixture
def governed_registry(tmp_path: Path):
    rel = f"data/agentic/_pytest_contract_{uuid.uuid4().hex}.json"
    target = (REPO_ROOT / rel).resolve()
    cfg_doc = {
        "logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}},
        "policy": {"prompt_filter": {"banned_patterns": ["update your soul"]}, "privacy": {}},
    }
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(cfg_doc), encoding="utf-8")
    reset_config_cache()
    registry = SkillRegistry(
        _get_config(str(cfg_path)),
        AgenticConfig(registry_path=rel, mode="write", writes_enabled=True),
    )
    yield registry, target
    close_audit_handles()
    reset_config_cache()
    target.unlink(missing_ok=True)
    lock = target.with_suffix(target.suffix + ".lock.d")
    if lock.exists():
        lock.rmdir()


def test_governed_writes_satisfy_the_committed_artifact_contract(governed_registry) -> None:
    registry, target = governed_registry
    registry.apply_skill(
        _spec("Do the safe thing."),
        reason="add the demo skill",
    )
    registry.apply_skill(
        _spec("Do the safe thing, twice."),
        reason="revise the demo body",
    )
    registry.apply_skill(
        _spec("More safe text.", name="second.skill-2"),
        reason="add a second skill",
    )

    data = json.loads(target.read_text(encoding="utf-8"))
    # Every rule the committed-artifact tests apply, now over non-empty data.
    assert set(data) == TOP_LEVEL_KEYS
    _check_version_history_pairing(data)
    assert data["version"] == 3 and len(data["skills"]) == 2  # 3 applies, 1 an update
    for name, skill in data["skills"].items():
        _check_skill_entry(name, skill)
    for row in data["history"]:
        _check_history_row(row, data)
    # The update kept one entry but recorded its own history row and a new hash.
    demo_rows = [row for row in data["history"] if row["name"] == "demo-skill"]
    assert len(demo_rows) == 2
    assert demo_rows[0]["sha256"] != demo_rows[1]["sha256"]
    assert demo_rows[1]["sha256"] == data["skills"]["demo-skill"]["sha256"]


def test_sha256_rule_detects_a_hand_edited_body(governed_registry) -> None:
    # The whole point of the hash check: a body edited in place without going
    # through apply_skill (a governance bypass) must not still validate.
    registry, target = governed_registry
    registry.apply_skill(
        _spec("Original body."),
        reason="add the demo skill",
    )
    data = json.loads(target.read_text(encoding="utf-8"))
    _check_skill_entry("demo-skill", data["skills"]["demo-skill"])  # green before tampering

    data["skills"]["demo-skill"]["body"] = "Body swapped by hand."
    with pytest.raises(AssertionError):
        _check_skill_entry("demo-skill", data["skills"]["demo-skill"])


def test_history_rule_detects_a_row_with_no_surviving_skill(governed_registry) -> None:
    registry, target = governed_registry
    registry.apply_skill(
        _spec("Original body."),
        reason="add the demo skill",
    )
    data = json.loads(target.read_text(encoding="utf-8"))
    _check_history_row(data["history"][0], data)  # green before tampering

    del data["skills"]["demo-skill"]  # the governed path has no delete
    with pytest.raises(AssertionError):
        _check_history_row(data["history"][0], data)


def test_version_pairing_rule_detects_a_dropped_history_row(governed_registry) -> None:
    registry, target = governed_registry
    registry.apply_skill(
        _spec("Original body."),
        reason="add the demo skill",
    )
    data = json.loads(target.read_text(encoding="utf-8"))
    _check_version_history_pairing(data)  # green before tampering

    data["history"] = []  # version stays 1, history now empty
    with pytest.raises(AssertionError):
        _check_version_history_pairing(data)
