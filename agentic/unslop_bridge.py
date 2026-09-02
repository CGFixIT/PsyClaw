"""Offline slop-detection rail for the agentic real-repo loop.

This bridge is intentionally placed inside agentic/ (not utils/) because every
consumer already lives in agentic/, so the vendored scanners never need to cross
the I6 boundary into gate.py/graph.py/mcp_hybrid_server.py.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("cyclaw.agentic.unslop_bridge")

_FILE_BLOCK_RE = re.compile(
    r"=== FILE (?P<path>[^\n]+?) ===\n(?P<body>.*?)\n=== END FILE ===",
    re.DOTALL,
)

_PROSE_FILE_SUFFIXES = {".md", ".rst", ".txt"}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _logged_phrase_fields(phrase: object) -> dict[str, Any]:
    """JSONL fields for one finding's span.

    Banned-phrase keys are short dictionary entries and stay as ``phrase``.
    Structural regexes copy operator prose into ``match.group()``; those spans
    are hashed so metrics JSONL is actually redacted (issue #1275 P1.3).
    ``BANNED_PHRASES`` is imported here, not at module import: the disabled
    probe must not load ``agentic.vendor.unslop``.
    """
    if not isinstance(phrase, str) or not phrase:
        return {}
    from agentic.vendor.unslop.banned_phrase_scan import BANNED_PHRASES

    key = phrase.casefold()
    if key in BANNED_PHRASES:
        return {"phrase": key}
    return {"phrase_sha256": _sha256(phrase), "phrase_chars": len(phrase)}


def _unslop_enabled(cfg: dict[str, Any]) -> bool:
    """True only when ``unslop.enabled`` is the literal boolean ``True``."""
    return (cfg.get("unslop") or {}).get("enabled", False) is True


def _extract_response_prose(response_text: str) -> str:
    """Return the text outside === FILE === blocks, CRLF-normalized."""
    text = response_text.replace("\r\n", "\n")
    parts = []
    cursor = 0
    for match in _FILE_BLOCK_RE.finditer(text):
        parts.append(text[cursor:match.start()])
        cursor = match.end()
    parts.append(text[cursor:])
    return "\n\n".join(part.strip() for part in parts if part.strip())


def _line_col_from_offset(text: str, offset: int) -> tuple[int, int]:
    """Return 1-based (line, column) for a character offset."""
    line = text.count("\n", 0, offset) + 1
    last_newline = text.rfind("\n", 0, offset)
    column = offset - (last_newline if last_newline != -1 else -1)
    return line, column


def _append_record(path: Path, record: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        # Observability failure must not abort the coding run.
        logger.debug("unslop metrics append failed: %s", type(exc).__name__)


def _run_scan(
    suggest_fn: Callable[[str], dict[str, Any]],
    text: str,
    *,
    surface: str,
    path: str | None,
    step: int,
    metrics_path: Path,
) -> dict[str, Any]:
    """Run ``suggest.suggest`` on one surface and append a redacted log line.

    Returns the upstream result dict, or {} on skip/error.
    """
    skipped: str | None = None
    result: dict[str, Any] = {}
    try:
        raw = suggest_fn(text)
        if raw.get("non_english"):
            skipped = "non_english"
        else:
            result = raw
    except Exception as exc:
        skipped = "scanner_error"
        _append_record(
            metrics_path,
            {
                "event": "unslop_scan",
                "step": step,
                "surface": surface,
                "path": path,
                "doc_sha256": _sha256(text),
                "chars": len(text),
                "skipped": skipped,
                "exc_type": type(exc).__name__,
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )
        return {}

    counts = result.get("counts", {}) if isinstance(result.get("counts"), dict) else {}
    suggestions = result.get("suggestions", []) if isinstance(result.get("suggestions"), list) else []

    findings = []
    for s in suggestions:
        span = s.get("span", {}) if isinstance(s.get("span"), dict) else {}
        phrase = span.get("text")
        if phrase is None:
            phrase = s.get("span")
        finding: dict[str, Any] = {
            "category": s.get("category", "unknown"),
            "severity": s.get("severity", "soft"),
        }
        finding.update(_logged_phrase_fields(phrase))
        start = span.get("start")
        if isinstance(start, int):
            line, column = _line_col_from_offset(text, start)
            finding["line"] = line
            finding["column"] = column
        findings.append({k: v for k, v in finding.items() if v is not None})

    record: dict[str, Any] = {
        "event": "unslop_scan",
        "step": step,
        "surface": surface,
        "path": path,
        "doc_sha256": _sha256(text),
        "chars": len(text),
        "counts": counts,
        "findings": findings,
        "skipped": skipped,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    structure_flags = counts.get("structure_flags", []) if isinstance(counts, dict) else []
    if structure_flags:
        record["structure_flags"] = structure_flags

    _append_record(metrics_path, record)
    return result


def build_unslop_probe(
    cfg: dict[str, Any],
) -> Callable[[str, Mapping[str, str], int], dict[str, Any]] | None:
    """Build the local-model slop-detection probe, or None when disabled.

    The returned callable expects ``(response_text, proposed_files, step)``.
    It scans response prose and prose file bodies, logs redacted findings to
    ``logs/unslop.jsonl``, and returns a nudge dict when slop is found.
    """
    if not _unslop_enabled(cfg):
        return None

    try:
        from agentic.vendor.unslop import suggest
    except Exception as exc:
        logger.debug("agentic.vendor.unslop import failed: %s", type(exc).__name__)
        return None

    def _suggest(text: str) -> dict[str, Any]:
        """Programmatic wrapper matching the upstream CLI output shape."""
        if not suggest.is_probably_english(text):
            return {
                "non_english": True,
                "document": text,
                "suggestions": [],
                "counts": {
                    "total": 0,
                    "hard": 0,
                    "soft": 0,
                    "by_category": {},
                    "structure_flags": [],
                },
            }
        suggestions = suggest.build_suggestions(text)
        struct = suggest.structure_scan(text)
        return {
            "document": text,
            "suggestions": suggestions,
            "counts": suggest.counts_block(suggestions, struct),
        }

    unslop_cfg = cfg.get("unslop") or {}
    metrics_path = Path(unslop_cfg.get("metrics_path", "logs/unslop.jsonl"))

    def _probe(
        response_text: str,
        proposed_files: Mapping[str, str],
        step: int,
    ) -> dict[str, Any]:
        total = 0
        categories: set[str] = set()

        prose = _extract_response_prose(response_text)
        if prose:
            result = _run_scan(
                _suggest,
                prose,
                surface="response_prose",
                path=None,
                step=step,
                metrics_path=metrics_path,
            )
            count = result.get("counts", {}).get("total", 0) if isinstance(result.get("counts"), dict) else 0
            total += count
            categories.update(result.get("counts", {}).get("by_category", {}).keys())

        for file_path, content in proposed_files.items():
            suffix = Path(file_path).suffix.lower()
            if suffix not in _PROSE_FILE_SUFFIXES:
                continue
            result = _run_scan(
                _suggest,
                content,
                surface="proposed_file",
                path=file_path,
                step=step,
                metrics_path=metrics_path,
            )
            count = result.get("counts", {}).get("total", 0) if isinstance(result.get("counts"), dict) else 0
            total += count
            categories.update(result.get("counts", {}).get("by_category", {}).keys())

        if total == 0:
            return {}

        nudge = (
            f"Slop-quality note: the response contained {total} AI-writing tell(s) "
            f"({', '.join(sorted(categories))}). Prefer direct, concrete prose without "
            "filler openers, listicle rhythm, or moralizing codas."
        )
        return {"nudge": nudge, "counts": {"total": total}}

    return _probe
