"""Status dict and HTML export builders for memory admin surfaces."""

from __future__ import annotations

import html
from collections.abc import Mapping
from typing import Any

from memory.store import count_active_facts, count_episodes, list_episodes, list_facts


def status_dict(cfg: Mapping[str, Any]) -> dict[str, Any]:
    mem = dict(cfg.get("memory") or {})
    enabled = mem.get("enabled") is True
    out: dict[str, Any] = {
        "enabled": enabled,
        "db_path": mem.get("db_path", "data/memory/cyclaw_memory.db"),
        "facts_enabled": bool((mem.get("facts") or {}).get("enabled")),
        "episodes_enabled": bool((mem.get("episodes") or {}).get("enabled")),
        "retrieval_fusion_enabled": bool((mem.get("retrieval_fusion") or {}).get("enabled")),
        "propose_apply_enabled": bool((mem.get("propose_apply") or {}).get("enabled")),
        "export_html_enabled": bool((mem.get("export_html") or {}).get("enabled")),
        "consolidation_enabled": bool((mem.get("consolidation") or {}).get("enabled")),
        "active_facts": 0,
        "episodes": 0,
    }
    if not enabled:
        return out
    try:
        out["active_facts"] = count_active_facts(cfg)
        out["episodes"] = count_episodes(cfg)
    except Exception as exc:  # noqa: BLE001 — status must never fail hard
        # Never echo raw SQLite/OS exception text: it can contain absolute
        # filesystem paths and schema details. Log the full exception for the
        # operator and return only the exception type to the API consumer.
        out["error"] = f"{type(exc).__name__}: memory store unavailable"
    return out


def export_html(cfg: Mapping[str, Any], *, max_episodes: int = 100) -> str:
    """Build a minimal offline HTML dump of recent episodes + active facts."""
    mem = dict(cfg.get("memory") or {})
    store_raw = bool((mem.get("episodes") or {}).get("store_raw_query"))
    episodes = list_episodes(cfg, limit=max_episodes)
    facts = list_facts(cfg, active_only=True, limit=200)

    rows_ep = []
    for ep in episodes:
        q_cell = html.escape(ep.raw_query) if (store_raw and ep.raw_query) else html.escape(ep.query_hash)
        rows_ep.append(
            "<tr>"
            f"<td>{ep.id}</td>"
            f"<td><code>{q_cell}</code></td>"
            f"<td>{html.escape(ep.model_used)}</td>"
            f"<td>{html.escape(ep.answer_summary[:500])}</td>"
            f"<td>{html.escape(ep.created_at)}</td>"
            "</tr>"
        )

    rows_fact = []
    for f in facts:
        rows_fact.append(
            "<tr>"
            f"<td>{f.id}</td>"
            f"<td>{html.escape(f.category)}</td>"
            f"<td>{html.escape(f.content[:1000])}</td>"
            f"<td>{html.escape(f.created_at)}</td>"
            "</tr>"
        )

    return (
        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
        "<title>CyClaw Memory Export</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;margin:1.5rem;}"
        "table{border-collapse:collapse;width:100%;margin-bottom:2rem;}"
        "th,td{border:1px solid #ccc;padding:0.4rem 0.6rem;text-align:left;vertical-align:top;}"
        "th{background:#f4f4f4;}"
        "code{font-size:0.85em;}"
        "</style></head><body>"
        "<h1>CyClaw Memory Export</h1>"
        f"<p>Episodes: {len(episodes)} (cap {max_episodes}) · "
        f"Active facts: {len(facts)}</p>"
        "<h2>Episodes</h2>"
        "<table><thead><tr>"
        "<th>ID</th><th>Query</th><th>Model</th><th>Summary</th><th>Created</th>"
        "</tr></thead><tbody>"
        + ("".join(rows_ep) or "<tr><td colspan='5'>none</td></tr>")
        + "</tbody></table>"
        "<h2>Active Facts</h2>"
        "<table><thead><tr>"
        "<th>ID</th><th>Category</th><th>Content</th><th>Created</th>"
        "</tr></thead><tbody>"
        + ("".join(rows_fact) or "<tr><td colspan='4'>none</td></tr>")
        + "</tbody></table>"
        "</body></html>"
    )
