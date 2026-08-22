"""LangGraph-ready memory nodes for CyClaw agentic layer.

Extracted from the monolithic orchestrate.py so the mechanical memory lifecycle
(extract, consolidate, title, next-action) can be used as first-class nodes
in LangGraph while preserving 100% backward compatibility for the existing
CLI + Claude Code hooks (PreCompact, SessionEnd, timer).

Design principles (cyclaw-advisor invariants):
- Nodes are pure functions: take state + cfg, return state updates.
- No autonomous self-modification of soul or topology.
- All writes that were atomic in the CLI remain atomic.
- Governance (propose/apply + human reason) is enforced at the registry
  boundary when these nodes are exposed as skills.
- Deterministic consolidation and path stamping logic is preserved exactly.

Usage in a LangGraph:
    from .memory_nodes import extract_node, consolidate_node, ...
    graph.add_node("memory_extract", partial(extract_node, cfg=cfg))

The original orchestrate.py CLI remains the source of truth for hooks and
manual /memory commands.

Small polish (June 2026): optional registry wiring for governance_score
so nodes can surface how well-governed suggested memory actions/skills are.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# Paths resolved relative to this file (same as original orchestrate.py)
SKILL_DIR = Path(__file__).resolve().parent
REPO_ROOT = SKILL_DIR.parents[2]
MEM_DIR = REPO_ROOT / "docs" / "memories"
INDEX_FILE = MEM_DIR / "INDEX.md"
CONSOLIDATED_FILE = MEM_DIR / "CONSOLIDATED.md"
STATE_FILE = MEM_DIR / ".orchestrator-state.json"

CONSOLIDATION_INTERVAL_HOURS = 12
RESERVED = {INDEX_FILE.name, CONSOLIDATED_FILE.name, "SESSION_NOTES.md"}
SNAPSHOT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}\.md$")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(**updates) -> None:
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    state = _load_state()
    state.update(updates)
    STATE_FILE.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")


def _stamp() -> str:
    return _now().strftime("%Y-%m-%d_%H%M%S")


def _timestamped_path() -> Path:
    return MEM_DIR / f"{_stamp()}.md"


def _snapshots() -> list[Path]:
    if not MEM_DIR.exists():
        return []
    return sorted(
        p for p in MEM_DIR.glob("*.md")
        if p.name not in RESERVED and SNAPSHOT_RE.match(p.name)
    )


def _split_sections(text: str) -> dict[str, list[str]]:
    """Group bullet/line content under their nearest '## ' heading."""
    sections: dict[str, list[str]] = {}
    current = "Uncategorized"
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
        elif line.startswith("# "):
            continue
        elif line.strip():
            sections.setdefault(current, []).append(line.rstrip())
    return sections


def _refresh_index() -> None:
    snaps = _snapshots()
    lines = ["# Memory index", ""]
    state = _load_state()
    lc = state.get("last_consolidation", "never")
    le = state.get("last_extraction", "never")
    lines += [
        f"- Last extraction: `{le}`",
        f"- Last consolidation: `{lc}`",
        f"- Snapshots: {len(snaps)}",
        "",
    ]
    if CONSOLIDATED_FILE.exists():
        lines.append(f"- Working set: [`{CONSOLIDATED_FILE.name}`]({CONSOLIDATED_FILE.name})")
        lines.append("")
    lines.append("## Snapshots (newest first)")
    lines.append("")
    for snap in reversed(snaps):
        lines.append(f"- [`{snap.name}`]({snap.name})")
    INDEX_FILE.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


# =============================================================================
# LangGraph Node Functions
# =============================================================================

def extract_node(state: Dict[str, Any], cfg: dict | None = None, registry=None) -> Dict[str, Any]:
    """LangGraph node: persist a memory snapshot.

    Optional `registry` (SkillRegistry) can be passed to include governance_score
    for memory-related skills in the returned state.
    """
    content = state.get("content", "").strip()
    content_file = state.get("content_file")

    if content_file:
        content = Path(content_file).read_text(encoding="utf-8").strip()

    MEM_DIR.mkdir(parents=True, exist_ok=True)
    target = _timestamped_path()

    if not content:
        content = (
            f"# Memory snapshot — {_stamp()}\n\n"
            "<!-- Filled by the memory-extraction skill. Sections below are "
            "the standard memory categories; delete any that are empty. -->\n\n"
            "## User preferences\n\n"
            "## Project patterns\n\n"
            "## Error corrections\n\n"
            "## Workflow notes\n"
        )
    elif not content.startswith("#"):
        content = f"# Memory snapshot — {_stamp()}\n\n{content}\n"

    target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    _save_state(last_extraction=_now().isoformat())
    _refresh_index()

    result = {
        "memory_extracted_path": str(target.relative_to(REPO_ROOT)),
        "last_extraction": _now().isoformat(),
        "memory_action": "extract",
    }

    if registry is not None:
        try:
            result["governance_score_memory_extract"] = registry.governance_score("memory-extraction")
        except Exception:
            pass
    return result


def consolidate_node(state: Dict[str, Any], cfg: dict | None = None, registry=None) -> Dict[str, Any]:
    """LangGraph node: deterministic merge/dedupe of memory snapshots.

    Optional `registry` for governance_score on consolidation-related skills.
    """
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    snaps = _snapshots()
    merged: dict[str, list[str]] = {}
    seen: dict[str, set[str]] = {}

    for snap in snaps:
        for section, lines in _split_sections(snap.read_text(encoding="utf-8")).items():
            bucket = merged.setdefault(section, [])
            seenset = seen.setdefault(section, set())
            for line in lines:
                key = re.sub(r"\s+", " ", line.strip().lower())
                if key and key not in seenset:
                    seenset.add(key)
                    bucket.append(line)

    total_lines = sum(len(v) for v in merged.values())
    out = [f"# Consolidated memory — {_stamp()}", ""]
    out.append(
        f"_Structural merge of {len(snaps)} snapshot(s); "
        f"{total_lines} unique line(s) across {len(merged)} section(s). "
        "Run the memory-consolidation skill for semantic merge._"
    )
    out.append("")
    for section in sorted(merged):
        if not merged[section]:
            continue
        out.append(f"## {section}")
        out.append("")
        out.extend(merged[section])
        out.append("")

    CONSOLIDATED_FILE.write_text("\n".join(out).rstrip() + "\n", encoding="utf-8")
    _save_state(last_consolidation=_now().isoformat())
    _refresh_index()

    result = {
        "consolidated_path": str(CONSOLIDATED_FILE.relative_to(REPO_ROOT)),
        "last_consolidation": _now().isoformat(),
        "memory_action": "consolidate",
        "snapshot_count": len(snaps),
        "unique_lines": total_lines,
    }

    if registry is not None:
        try:
            result["governance_score_memory_consolidate"] = registry.governance_score("memory-consolidation")
        except Exception:
            pass
    return result


# Fixed fallback title used when no meaningful title can be derived from content
# and the context is not linked to anything else. Per project owner (CG) directive.
_DEFAULT_TITLE = "CyClaw - Created By CG"


def _resolve_title(state: Dict[str, Any]) -> str:
    """Derive a title from state, falling back to the fixed default.

    Returns the first non-empty of: an explicit ``state['title']``, the first
    Markdown ``# `` heading in ``state['content']``, otherwise ``_DEFAULT_TITLE``.
    """
    explicit = (state.get("title") or "").strip()
    if explicit:
        return explicit

    content = state.get("content") or ""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            heading = stripped[2:].strip()
            if heading:
                return heading

    return _DEFAULT_TITLE


def title_node(state: Dict[str, Any], cfg: dict | None = None, registry=None) -> Dict[str, Any]:
    """LangGraph node: suggest a concise title for the current memory context.

    Title resolution order:
      1. An explicit ``title`` already present in ``state`` (caller-provided).
      2. The first Markdown ``# `` heading found in ``state['content']``.
      3. The fixed fallback ``"CyClaw - Created By CG"`` when nothing is linked
         or derivable (per project owner directive).

    If ``registry`` is provided, includes governance_score for title-related
    memory skills.
    """
    suggested_title = _resolve_title(state)

    result = {
        "suggested_title": suggested_title,
        "memory_action": "title",
    }

    if registry is not None:
        try:
            result["governance_score_memory_title"] = registry.governance_score("memory-title")
        except Exception:
            pass
    return result


def next_action_node(state: Dict[str, Any], cfg: dict | None = None, registry=None) -> Dict[str, Any]:
    """LangGraph node: suggest the next memory-related action.

    Looks at last_consolidation / last_extraction and timer-check logic.
    If `registry` is provided, includes governance_score for the suggested action.
    """
    state_data = _load_state()
    last = state_data.get("last_consolidation")
    due = True
    if last:
        try:
            elapsed = _now() - datetime.fromisoformat(last)
            due = elapsed.total_seconds() >= CONSOLIDATION_INTERVAL_HOURS * 3600
        except ValueError:
            due = True

    if due:
        suggestion = "Run consolidate then extract (12h interval reached)"
        action = "consolidate+extract"
        suggested_skill = "memory-consolidation"
    else:
        suggestion = "No consolidation due. Consider targeted extraction if important context appeared."
        action = "extract"
        suggested_skill = "memory-extraction"

    result = {
        "next_memory_action": action,
        "suggestion": suggestion,
        "consolidation_due": due,
        "memory_action": "next_action",
    }

    if registry is not None:
        try:
            result["governance_score_suggested_skill"] = registry.governance_score(suggested_skill)
            result["suggested_skill"] = suggested_skill
        except Exception:
            pass

    return result


__all__ = [
    "extract_node",
    "consolidate_node",
    "title_node",
    "next_action_node",
]
