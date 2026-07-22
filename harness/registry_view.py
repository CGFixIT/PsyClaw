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
    its auth requirement.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILLS_DIR = _REPO_ROOT / ".claude" / "skills"
_AGENTIC_REGISTRY = _REPO_ROOT / "data" / "agentic" / "skills_registry.json"
_MCP_SERVER = _REPO_ROOT / "mcp_hybrid_server.py"
_UTF8 = "utf-8"

_NAME_RE = re.compile(r"^name:\s*(.+?)\s*$", re.MULTILINE)
_DESC_RE = re.compile(r"^description:\s*[>|]?-?\s*\n?\s*(.+?)\s*$", re.MULTILINE)

_BUILTIN_KIND = "built-in"
_CATALOG_KIND = "catalog"
_NAME_KEY = "name"
_DESC_KEY = "description"


@dataclass(frozen=True)
class Connector:
    """One registry connector entry (built-in or catalog)."""

    id: str
    name: str
    kind: str
    auth: str
    notes: str
    driver: str = ""


# Built-in connectors: driven by the harness today.
_BUILTIN_CONNECTORS = (
    Connector(
        id="github",
        name="GitHub (agentic layer)",
        kind=_BUILTIN_KIND,
        auth="gh CLI login (repo writes) / none needed for public reads",
        driver="python -m agentic.cli",
        notes="PR/issue context, governed skills registry; read mode by default.",
    ),
    Connector(
        id="fsconnect",
        name="Local filesystem share",
        kind=_BUILTIN_KIND,
        auth="none (scoped roots, path-safe)",
        driver="python -m agentic.fsconnect",
        notes="Gated file-share with audited refusals; read-only from the harness.",
    ),
    Connector(
        id="sqlconnect",
        name="SQL databases",
        kind=_BUILTIN_KIND,
        auth="DB credentials in agentic config",
        driver="python -m agentic.sqlconnect",
        notes="SELECT/WITH-only guard; schema + read-only queries.",
    ),
    Connector(
        id="ollama",
        name="Ollama (local models)",
        kind=_BUILTIN_KIND,
        auth="none (loopback)",
        driver="direct HTTP 127.0.0.1:11434",
        notes="Default chat backend; free, offline, no account.",
    ),
)

# Popular free / no-login connectors the operator can wire in later. Metadata
# only — the harness does NOT bundle clients for these (YAGNI until a caller
# exists); the catalog exists so the console can show what's available.
_CATALOG_CONNECTORS = (
    Connector(id="github-public", name="GitHub public API", kind=_CATALOG_KIND,
              auth="none for public data (rate-limited)",
              notes="api.github.com unauthenticated reads."),
    Connector(id="openai-compatible", name="Any OpenAI-compatible local server", kind=_CATALOG_KIND,
              auth="optional api_key",
              notes="agentic provider 'openai_compatible' (e.g. LM Studio compat)."),
    Connector(id="rss", name="RSS/Atom feeds", kind=_CATALOG_KIND,
              auth="none",
              notes="Pull release/security feeds into the corpus via sync."),
    Connector(id="dropbox", name="Dropbox corpus sync", kind=_CATALOG_KIND,
              auth="rclone remote (one-time login)",
              notes="sync/ subsystem; python -m sync.cli."),
)


def _frontmatter_field(text: str, pattern: re.Pattern[str]) -> str:
    match = pattern.search(text)
    return match.group(1).strip().strip("'\"") if match else ""


def list_repo_skills(skills_dir: Path | None = None) -> list[dict]:
    """Repo skills from ``.claude/skills/*/SKILL.md`` frontmatter."""
    root = skills_dir or _SKILLS_DIR
    skills: list[dict] = []
    for path in sorted(root.glob("*/SKILL.md")):
        try:
            text = path.read_text(encoding=_UTF8)
        except OSError:
            continue
        if path.is_relative_to(_REPO_ROOT):
            rel = str(path.relative_to(_REPO_ROOT))
        else:
            rel = str(path)
        skills.append({
            _NAME_KEY: _frontmatter_field(text, _NAME_RE) or path.parent.name,
            _DESC_KEY: _frontmatter_field(text, _DESC_RE),
            "source": "repo",
            "path": rel,
        })
    return skills


def list_governed_skills(registry_path: Path | None = None) -> list[dict]:
    """Entries in the agentic governed registry (read-only view)."""
    path = registry_path or _AGENTIC_REGISTRY
    try:
        parsed = json.loads(path.read_text(encoding=_UTF8))
    except (OSError, json.JSONDecodeError):
        return []
    entries = parsed.get("skills", parsed if isinstance(parsed, list) else [])
    governed: list[dict] = []
    for entry in entries:
        if isinstance(entry, dict) and entry.get(_NAME_KEY):
            governed.append({
                _NAME_KEY: str(entry[_NAME_KEY]),
                _DESC_KEY: str(entry.get(_DESC_KEY, "")),
                "source": "agentic-registry",
                "path": str(path),
            })
    return governed


def _literal_tools(node: ast.Assign) -> list[dict]:
    try:
        tools = ast.literal_eval(node.value)
    except (ValueError, SyntaxError):
        return []
    catalog: list[dict] = []
    for tool in tools:
        if isinstance(tool, dict):
            catalog.append({
                _NAME_KEY: tool.get(_NAME_KEY, ""),
                _DESC_KEY: tool.get(_DESC_KEY, ""),
                "source": "mcp",
            })
    return catalog


def list_mcp_tools(server_path: Path | None = None) -> list[dict]:
    """Tool catalog from ``mcp_hybrid_server.py`` — AST-parsed, never imported."""
    try:
        tree = ast.parse((server_path or _MCP_SERVER).read_text(encoding=_UTF8))
    except (OSError, SyntaxError):
        return []
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "TOOLS" for target in node.targets
        ):
            return _literal_tools(node)
    return []


def full_registry() -> dict:
    """Everything the console's /skills, /tools and /connectors panes need."""
    connectors = [asdict(conn) for conn in (*_BUILTIN_CONNECTORS, *_CATALOG_CONNECTORS)]
    return {
        "skills": list_repo_skills() + list_governed_skills(),
        "tools": list_mcp_tools(),
        "connectors": connectors,
    }
