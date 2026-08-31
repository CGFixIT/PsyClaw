"""Tests for agentic.vendor.unslop.suggest.apply_replacements fail-closed JSON."""

from __future__ import annotations

from pathlib import Path

from agentic.vendor.unslop.suggest import apply_replacements


def test_corrupt_replacements_file_returns_warning_without_applying(tmp_path: Path) -> None:
    suggestions = [{"span": {"start": 0, "end": 1, "text": "a"}, "suggested_replacement": None}]
    path = tmp_path / "replacements.json"
    path.write_text("{not-json", encoding="utf-8")

    warnings = apply_replacements(suggestions, str(path))

    assert warnings == ["replacements file is unreadable or not JSON"]
    assert suggestions[0]["suggested_replacement"] is None


def test_non_object_replacements_file_returns_warning(tmp_path: Path) -> None:
    suggestions = [{"span": {"start": 0, "end": 1, "text": "a"}, "suggested_replacement": None}]
    path = tmp_path / "replacements.json"
    path.write_text("[1, 2]", encoding="utf-8")

    warnings = apply_replacements(suggestions, str(path))

    assert warnings == ["replacements file must be a JSON object"]
    assert suggestions[0]["suggested_replacement"] is None


def test_well_formed_replacements_still_merge(tmp_path: Path) -> None:
    suggestions = [{"span": {"start": 0, "end": 1, "text": "a"}, "suggested_replacement": None}]
    path = tmp_path / "replacements.json"
    path.write_text(
        '{"replacements": [{"start": 0, "end": 1, "replacement": "b"}]}',
        encoding="utf-8",
    )

    warnings = apply_replacements(suggestions, str(path))

    assert warnings == []
    assert suggestions[0]["suggested_replacement"] == "b"
