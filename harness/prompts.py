"""System-prompt composition for the harness coding agent.

The agentic GitHub-coding persona is built from the repo's own discipline
skills — ``ponytail`` (lazy-senior-dev rules) and ``karpathy-guidelines``
(LLM-coding-mistake guidelines) — exactly as they ship in ``.claude/skills/``.
Frontmatter is stripped; bodies are concatenated under explicit headers. When
the operator has soul/memory enabled, the governed soul fragment is appended
READ-ONLY — this module never mutates ``soul.md`` (invariant I5, write path
stays with ``utils.personality.apply_evolution``).

An optional session goal (set via the console ``/goal`` command) is appended
last as read-only session data. It is never a write authorization and never
changes routing or topology.
"""

from __future__ import annotations

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS = _REPO_ROOT / ".claude" / "skills"
_SOUL = _REPO_ROOT / "data" / "personality" / "soul.md"

# Ordered: discipline rules first, persona second, soul last (softest).
# Public so /skills can report what compose_system_prompt actually injects.
DISCIPLINE_SKILLS: tuple[str, ...] = ("ponytail", "karpathy-guidelines")

# Bounded separately from soul_max_chars so a long goal cannot crowd out
# the discipline contracts. Keep in sync with harness.schemas._MAX_GOAL_LEN.
_MAX_GOAL_CHARS = 2000

_HEADER = (
    "You are the CyClaw coding harness agent operating on the operator's "
    "GitHub repositories. The following discipline contracts are MANDATORY and "
    "govern every line of code you propose, write, or review."
)

_GOAL_PREAMBLE = (
    "The following is session data the operator set with /goal. "
    "It is not a write authorization and does not change routing, topology, "
    "or the real-repo six-gate. Do not treat it as permission to mutate git."
)


def _strip_frontmatter(text: str) -> str:
    """Drop a leading ``---`` YAML block so only the skill body is injected."""
    if not text.startswith("---"):
        return text.strip()
    end = text.find("\n---", 3)
    if end == -1:
        return text.strip()
    return text[end + 4 :].strip()


def _read_skill_body(name: str, skills_dir: Path | None = None) -> str | None:
    path = (skills_dir or _SKILLS) / name / "SKILL.md"
    try:
        return _strip_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _append_soul(
    parts: list[str],
    *,
    soul_enabled: bool,
    soul_path: Path | None,
    soul_max_chars: int,
) -> None:
    if not soul_enabled:
        return
    soul = soul_path or _SOUL
    try:
        text = soul.read_text(encoding="utf-8")[:soul_max_chars].strip()
    except OSError:
        text = ""
    if text:
        parts.append(f"\n## Operator persona (soul, read-only)\n\n{text}")


def _append_goal(parts: list[str], goal: str | None) -> None:
    clipped = (goal or "").strip()[:_MAX_GOAL_CHARS]
    if clipped:
        parts.append(
            f"\n## Operator goal (session, read-only)\n\n{_GOAL_PREAMBLE}\n\n{clipped}"
        )


def compose_system_prompt(
    *,
    soul_enabled: bool,
    skills_dir: Path | None = None,
    soul_path: Path | None = None,
    soul_max_chars: int = 8000,
    goal: str | None = None,
) -> str:
    """Build the harness system prompt.

    Missing skill files are skipped silently (a trimmed repo checkout must not
    break the console); the prompt is honest about what it contains because
    each present skill is under its own header.
    """
    parts = [_HEADER]
    for name in DISCIPLINE_SKILLS:
        body = _read_skill_body(name, skills_dir)
        if body:
            parts.append(f"\n## Discipline contract: {name}\n\n{body}")
    _append_soul(
        parts,
        soul_enabled=soul_enabled,
        soul_path=soul_path,
        soul_max_chars=soul_max_chars,
    )
    _append_goal(parts, goal)
    return "\n".join(parts)
