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
from pathlib import Path

from agentic.config import AgenticConfig
from agentic.registry import SkillRegistry

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
    data = _load()
    history = data["history"]
    assert data["version"] == len(history)
    assert [row["version"] for row in history] == list(range(1, len(history) + 1))


def test_skill_entries_match_the_governed_schema_and_hashes() -> None:
    for name, skill in _load()["skills"].items():
        assert _NAME_RE.match(name), name
        assert set(skill) == SKILL_KEYS
        assert skill["name"] == name
        assert all(skill[field].strip() for field in ("description", "body", "reason"))
        # apply_skill stores sha256 of the canonical "name\ndescription\nbody".
        canonical = f"{skill['name']}\n{skill['description']}\n{skill['body']}"
        assert skill["sha256"] == hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_history_rows_reference_applied_skills() -> None:
    data = _load()
    for row in data["history"]:
        assert set(row) == HISTORY_KEYS
        assert _NAME_RE.match(row["name"]), row["name"]
        # The governed path has no delete operation, so every name ever applied
        # is still present.
        assert row["name"] in data["skills"]
        assert row["reason"].strip()
        assert re.fullmatch(r"[0-9a-f]{64}", row["sha256"])


def test_committed_registry_loads_through_the_real_registry_class() -> None:
    data = _load()
    registry = SkillRegistry(
        {"logging": {"audit_fields": {}}, "policy": {"prompt_filter": {}, "privacy": {}}},
        AgenticConfig(registry_path="data/agentic/skills_registry.json", mode="read", writes_enabled=False),
    )
    assert registry.version() == data["version"]
    assert registry.list_skills() == sorted(data["skills"])
