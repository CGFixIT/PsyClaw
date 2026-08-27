"""Qwen manifest loader — no weight download."""

from __future__ import annotations

import pytest

from guardrails.errors import GuardrailsConfigError
from guardrails.qwen_registry import load_qwen_manifest, provenance_ids_for_docs


def test_shipped_manifest_loads_non_strict() -> None:
    man = load_qwen_manifest()
    assert man["tag"] == "qwen3.8:27b-mlx"
    assert man["strict"] is False
    assert man["sha256"] == ""


def test_strict_without_digest_fails(tmp_path) -> None:
    p = tmp_path / "m.yaml"
    p.write_text("tag: qwen3.8:27b-mlx\nsha256: ''\nstrict: true\n", encoding="utf-8")
    with pytest.raises(GuardrailsConfigError, match="sha256"):
        load_qwen_manifest(p)


def test_provenance_ids_omit_raw_text() -> None:
    ids = provenance_ids_for_docs(
        [{"source": "rrf.md", "chunk_id": 0, "text": "SECRET CHUNK"}]
    )
    assert ids == ("rrf.md:0",)
    assert "SECRET" not in "".join(ids)
