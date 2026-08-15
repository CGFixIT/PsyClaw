"""Read-only inventory of harness skills and whether each is actually wired.

``GET /api/skills`` (and the ``/skills`` slash command) use this view.

Wiring is honest, not "the file exists":

  - **prompt** — ``compose_system_prompt`` injects it every chat turn
    (``DISCIPLINE_SKILLS``). Wired only when the SKILL.md is readable.
  - **check** — an ``/agent checks`` profile whose argv is a
    ``.claude/skills/...`` script. Wired only when that path exists.
  - **repo** / **governed** — catalog only. Present in the sidebar registry;
    this console does not inject or execute them.

The payload is data. The ASCII diagram is a convenience for the console
reply and for ``curl``; it is not HTML.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final, TypedDict

from harness.agent_policy import skill_backed_profiles
from harness.prompts import DISCIPLINE_SKILLS
from harness.registry_view import _DESC_KEY, _NAME_KEY, list_governed_skills, list_repo_skills

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ROLE_PROMPT: Final = "prompt"
_ROLE_CHECK: Final = "check"
_ROLE_REPO: Final = "repo"
_ROLE_GOVERNED: Final = "governed"
_BOX_BAR = "─"
_DESC_CAP = 72


class SkillRecord(TypedDict):
    """One inventory row. ``wired`` is computed; the rest is the catalog."""

    name: str
    role: str
    path: str
    description: str
    source: str
    invoked: bool
    wired: bool


def _clip(text: str) -> str:
    cleaned = " ".join(text.split())
    if len(cleaned) <= _DESC_CAP:
        return cleaned
    return f"{cleaned[: _DESC_CAP - 1]}…"


def _prompt_row(entry: dict) -> SkillRecord:
    path = str(entry.get("path", "") or "")
    readable = bool(path and (_REPO_ROOT / path).is_file()) if not Path(path).is_absolute() else Path(path).is_file()
    return {
        _NAME_KEY: str(entry.get(_NAME_KEY, "") or ""),
        "role": _ROLE_PROMPT,
        "path": path,
        _DESC_KEY: _clip(str(entry.get(_DESC_KEY, "") or "")),
        "source": str(entry.get("source", "repo") or "repo"),
        "invoked": readable,
        "wired": readable,
    }


def _repo_row(entry: dict) -> SkillRecord:
    return {
        _NAME_KEY: str(entry.get(_NAME_KEY, "") or ""),
        "role": _ROLE_REPO,
        "path": str(entry.get("path", "") or ""),
        _DESC_KEY: _clip(str(entry.get(_DESC_KEY, "") or "")),
        "source": str(entry.get("source", "repo") or "repo"),
        "invoked": False,
        "wired": False,
    }


def _governed_row(entry: dict) -> SkillRecord:
    return {
        _NAME_KEY: str(entry.get(_NAME_KEY, "") or ""),
        "role": _ROLE_GOVERNED,
        "path": str(entry.get("path", "") or ""),
        _DESC_KEY: _clip(str(entry.get(_DESC_KEY, "") or "")),
        "source": "agentic-registry",
        "invoked": False,
        "wired": False,
    }


def _check_row(name: str, description: str, rel_path: str) -> SkillRecord:
    exists = (_REPO_ROOT / rel_path).is_file()
    return {
        _NAME_KEY: name,
        "role": _ROLE_CHECK,
        "path": rel_path,
        _DESC_KEY: _clip(description),
        "source": "agent-check",
        "invoked": exists,
        "wired": exists,
    }


def _tree_pair(row: SkillRecord, last: bool, indent: str) -> tuple[str, str]:
    branch = "└─" if last else "├─"
    mark = "●" if row["wired"] else "○"
    child = "  " if last else "│ "
    head = f"{indent}{branch}[{row['name']}] {mark}  {row['path']}"
    detail = f"{indent}{child} {row['description']}"
    return head, detail


def _box(row: SkillRecord) -> str:
    invoked = "yes" if row["invoked"] else "no (catalog only)"
    wired_label = "yes" if row["wired"] else "no"
    inner = (
        f" name        {row['name']}",
        f" role        {row['role']}",
        f" path        {row['path']}",
        f" source      {row['source']}",
        f" invoked     {invoked}",
        f" wired       {wired_label}",
        "",
        f" {row['description']}",
    )
    width = max(len(line) for line in inner)
    edge = _BOX_BAR * (width + 1)
    title = f" /{row['name']} "
    prefix = title if len(title) < len(edge) else ""
    rest = edge[len(prefix):]
    head = f"┌{prefix}{rest}┐"
    body = [f"│{line.ljust(width)} │" for line in inner]
    return "\n".join((head, *body, f"└{edge}┘"))


def _append_group(lines: list[str], heading: str, rows: list[SkillRecord]) -> None:
    if not rows:
        return
    lines.append(heading)
    last_index = len(rows) - 1
    for index, row in enumerate(rows):
        head, detail = _tree_pair(row, index == last_index, "")
        lines.append(head)
        lines.append(detail)


def render_skills_diagram(skills: list[SkillRecord], *, wired: int, total: int) -> str:
    """Monospaced wiring diagram for the console reply (text, never HTML)."""
    if len(skills) == 1:
        return _box(skills[0])
    lines = [f"HARNESS SKILLS — {wired} wired / {total} listed", ""]
    prompt = [row for row in skills if row["role"] == _ROLE_PROMPT]
    checks = [row for row in skills if row["role"] == _ROLE_CHECK]
    repo = [row for row in skills if row["role"] == _ROLE_REPO]
    governed = [row for row in skills if row["role"] == _ROLE_GOVERNED]
    _append_group(lines, "prompt (injected into every chat turn)", prompt)
    _append_group(lines, "agent-check (named /agent checks profiles)", checks)
    _append_group(lines, "repo catalog (present; this console does not invoke these)", repo)
    _append_group(
        lines,
        "governed registry (read-only; mutations stay behind agentic CLI)",
        governed,
    )
    if not (prompt or checks or repo or governed):
        lines.append("(none)")
    return "\n".join(lines)


def list_wired_skills() -> dict[str, object]:
    """Full inventory plus a default diagram of the wired subset."""
    repo_entries = list_repo_skills()
    prompt_names = set(DISCIPLINE_SKILLS)
    check_profiles = skill_backed_profiles()
    check_names = {name for name, _desc, _path in check_profiles}
    skills: list[SkillRecord] = []
    for entry in repo_entries:
        name = str(entry.get(_NAME_KEY, "") or "")
        if name in prompt_names:
            skills.append(_prompt_row(entry))
        elif name not in check_names:
            skills.append(_repo_row(entry))
    for name, description, rel_path in check_profiles:
        skills.append(_check_row(name, description, rel_path))
    for entry in list_governed_skills():
        skills.append(_governed_row(entry))
    wired_rows = [row for row in skills if row["wired"]]
    count = len(wired_rows)
    return {
        "skills": skills,
        "wired": count,
        "total": len(skills),
        "diagram": render_skills_diagram(wired_rows, wired=count, total=len(skills)),
    }
