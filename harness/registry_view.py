"""Merged read-only view of the harness skill/tool/connector registry.

Three catalogues, one endpoint payload:

  - **skills** — repo ``.claude/skills/*/SKILL.md`` (name + description from
    YAML frontmatter, parsed by hand so PyYAML stays out of this path) merged
    with the governed ``agentic`` skills registry
    (``data/agentic/skills_registry.json``, read-only — mutations stay behind
    ``agentic.cli propose-skill/apply-skill`` and its human-reason gate).
  - **tools** — the MCP server's tool catalog, AST-parsed from
    ``mcp_hybrid_server.py`` so this module never imports the server (I6).
  - **connectors** — the built-in out-of-band connectors plus a catalog of
    popular free / no-login integration points, each honestly labelled with
    its auth requirement and availability probe.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _REPO_ROOT / ".claude" / "skills"
_AGENTIC_REGISTRY = _REPO_ROOT / "data" / "agentic" / "skills_registry.json"
_MCP_SERVER = _REPO_ROOT / "mcp_hybrid_server.py"

_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
_DESC_RE = re.compile(r"^description:\s*[>|]?-?\s*\n?\s*(.+?)\s*$", re.MULTILINE)

# Built-in connectors: (id, display name, auth model, how the harness drives it).
_BUILTIN_CONNECTORS = [
    {
        "id": "github",
        "name": "GitHub (agentic layer)",
        "kind": "built-in",
        "auth": "gh CLI login (repo writes) / none needed for public reads",
        "driver": "python -m agentic.cli",
        "notes": "PR/issue context, governed skills registry; read mode by default.",
    },
    {
        "id": "fsconnect",
        "name": "Local filesystem share",
        "kind": "built-in",
        "auth": "none (scoped roots, path-safe)",
        "driver": "python -m agentic.fsconnect",
        "notes": "Gated file-share with audited refusals; read-only from the harness.",
    },
    {
        "id": "sqlconnect",
        "name": "SQL databases",
        "kind": "built-in",
        "auth": "DB credentials in agentic config",
        "driver": "python -m agentic.sqlconnect",
        "notes": "SELECT/WITH-only guard; schema + read-only queries.",
    },
    {
        "id": "ollama",
        "name": "Ollama (local models)",
        "kind": "built-in",
        "auth": "none (loopback)",
        "driver": "direct HTTP 127.0.0.1:11434",
        "notes": "Default chat backend; free, offline, no account.",
    },
]

# Popular free / no-login connectors the operator can wire in later. Metadata
# only — the harness does NOT bundle clients for these (YAGNI until a caller
# exists); the catalog exists so the console can show what's available.
_CATALOG_CONNECTORS = [
    {"id": "github-public", "name": "GitHub public API", "kind": "catalog",
     "auth": "none for public data (rate-limited)", "notes": "api.github.com unauthenticated reads."},
    {"id": "openai-compatible", "name": "Any OpenAI-compatible local server", "kind": "catalog",
     "auth": "optional api_key", "notes": "agentic provider 'openai_compatible' (e.g. LM Studio compat)."},
    {"id": "rss", "name": "RSS/Atom feeds", "kind": "catalog",
     "auth": "none", "notes": "Pull release/security feeds into the corpus via sync."},
    {"id": "dropbox", "name": "Dropbox corpus sync", "kind": "catalog",
     "auth": "rclone remote (one-time login)", "notes": "sync/ subsystem; python -m sync.cli."},
]


def _frontmatter_field(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text)
    return match.group(1).strip().strip("'\"") if match else ""


def list_repo_skills(skills_dir: Path | None = None) -> list[dict]:
    """Repo skills from ``.claude/skills/*/SKILL.md`` frontmatter."""
    root = skills_dir or _SKILLS_DIR
    skills: list[dict] = []
    for path in sorted(root.glob("*/SKILL.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        skills.append({
            "name": _frontmatter_field(text, _NAME_RE) or path.parent.name,
            "description": _frontmatter_field(text, _DESC_RE),
            "source": "repo",
            "path": str(path.relative_to(_REPO_ROOT)) if path.is_relative_to(_REPO_ROOT) else str(path),
        })
    return skills


def list_governed_skills(registry_path: Path | None = None) -> list[dict]:
    """Entries in the agentic governed registry (read-only view)."""
    path = registry_path or _AGENTIC_REGISTRY
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    entries = data.get("skills", data if isinstance(data, list) else [])
    out: list[dict] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get("name"):
            out.append({
                "name": str(entry["name"]),
                "description": str(entry.get("description", "")),
                "source": "agentic-registry",
                "path": str(path),
            })
    return out


def list_mcp_tools(server_path: Path | None = None) -> list[dict]:
    """Tool catalog from ``mcp_hybrid_server.py`` — AST-parsed, never imported."""
    path = server_path or _MCP_SERVER
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "TOOLS" for t in node.targets
        ):
            try:
                tools = ast.literal_eval(node.value)
            except (ValueError, SyntaxError):
                return []
            return [
                {"name": t.get("name", ""), "description": t.get("description", ""), "source": "mcp"}
                for t in tools
                if isinstance(t, dict)
            ]
    return []


def list_connectors() -> list[dict]:
    """Built-in connectors first, then the free/no-login catalog."""
    return [dict(c) for c in _BUILTIN_CONNECTORS] + [dict(c) for c in _CATALOG_CONNECTORS]


def full_registry() -> dict:
    """Everything the console's /skills, /tools and /connectors panes need."""
    return {
        "skills": list_repo_skills() + list_governed_skills(),
        "tools": list_mcp_tools(),
        "connectors": list_connectors(),
    }
