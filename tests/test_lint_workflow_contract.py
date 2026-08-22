"""Regression contract for changed-file handling in the lint workflow."""

from pathlib import Path

_WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "lint.yml"


def test_changed_python_paths_stay_nul_delimited_until_flake8_argv() -> None:
    text = _WORKFLOW.read_text(encoding="utf-8")
    assert "git diff --name-only -z" in text
    assert "mapfile -d ''" in text
    assert 'flake8 -- "${FILES[@]}"' in text
    assert "CHANGED_FILES" not in text
