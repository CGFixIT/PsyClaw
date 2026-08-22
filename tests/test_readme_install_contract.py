"""Regression contract for unprivileged virtual-environment setup."""

from pathlib import Path

_README = Path(__file__).resolve().parents[1] / "README.md"


def test_readme_does_not_require_elevation_to_create_venv() -> None:
    text = _README.read_text(encoding="utf-8")
    assert "sudo python3.12 -m venv" not in text
    assert "python3.12 -m venv .venv" in text
