"""Optional Qwen/Ollama tag manifest. Strict mode default-off. No weight fetch."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from guardrails.errors import GuardrailsConfigError

DEFAULT_PATH = Path(__file__).resolve().parent / "qwen_manifest.yaml"


def load_qwen_manifest(path: Path | None = None, *, strict: bool | None = None) -> dict[str, Any]:
    target = path or DEFAULT_PATH
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise GuardrailsConfigError(f"qwen manifest unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise GuardrailsConfigError("qwen manifest must be a mapping")
    tag = str(raw.get("tag") or "").strip()
    if not tag:
        raise GuardrailsConfigError("qwen manifest missing tag")
    digest = str(raw.get("sha256") or "").strip()
    enforce = raw.get("strict", False) if strict is None else strict
    if enforce and not digest:
        raise GuardrailsConfigError("strict qwen manifest requires sha256")
    return {
        "tag": tag,
        "sha256": digest,
        "source_url": str(raw.get("source_url") or ""),
        "strict": bool(enforce),
    }


def provenance_ids_for_docs(docs: list[dict[str, Any]]) -> tuple[str, ...]:
    """Untrusted retrieval ids for GuardrailDecision.provenance_ids (no raw text)."""
    out: list[str] = []
    for doc in docs:
        source = str(doc.get("source") or "unknown")
        chunk = doc.get("chunk_id", "")
        out.append(f"{source}:{chunk}")
    return tuple(out)
