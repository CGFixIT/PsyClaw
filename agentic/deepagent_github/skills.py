"""Governed virtual skill files for the optional Deep Agents integration."""

from __future__ import annotations

import json

from agentic.registry import SkillRegistry
from utils.errors import AgenticError

_VIRTUAL_SKILLS_ROOT = "/skills"


def governed_skill_files(registry: SkillRegistry) -> dict[str, str]:
    """Project applied registry entries into virtual Deep Agents skill files."""

    files: dict[str, str] = {}
    for name in registry.list_skills():
        skill = registry.get_skill(name)
        if not skill or not all(isinstance(skill.get(field), str) and skill[field].strip()
                                for field in ("name", "description", "body")):
            raise AgenticError("registry contains an invalid governed skill", details={"name": name})
        files[f"{_VIRTUAL_SKILLS_ROOT}/{name}/SKILL.md"] = "\n".join(
            (
                "---",
                f"name: {json.dumps(skill['name'])}",
                f"description: {json.dumps(skill['description'])}",
                "---",
                "",
                skill["body"].rstrip(),
                "",
            )
        )
    return files
