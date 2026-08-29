"""Repo-tree hygiene guards that no other checker owns.

Lives in tests/ rather than a skill or a workflow step on purpose: the three
`test` legs are release gates, while `lint.yml` is advisory end-to-end
(continue-on-error on both linters AND the job) and the `verify-skills` matrix
is continue-on-error too. A guard that must actually block a merge has to run
here.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Built at runtime instead of written literally so this file does not match its
# own guard. Only the angle-bracket markers are searched: the third marker git
# writes is a bare row of seven '=', which is also how Markdown underlines a
# setext H1 -- matching it would fail on ordinary prose. A conflict cannot be
# committed without the angle-bracket pair, so these two are sufficient.
_OPEN_MARKER = "<" * 7 + " "
_CLOSE_MARKER = ">" * 7 + " "

# Reading every tracked blob is wasteful; a conflict marker only survives in
# something a human edits as text. Anything else is skipped by extension.
_TEXT_SUFFIXES = frozenset(
    {
        ".cfg", ".css", ".env", ".html", ".ini", ".js", ".json", ".jsonl",
        ".md", ".ndjson", ".ps1", ".py", ".rst", ".sh", ".toml", ".txt",
        ".yaml", ".yml",
    }
)


def _tracked_text_files() -> list[Path]:
    """Tracked files with a text suffix, via git so ignored/untracked junk is excluded."""
    # Resolved to an absolute path rather than passed as the bare name "git":
    # a bare name is looked up through PATH, which both ruff (S607) and CodeQL
    # (py/partial-path-command) flag as running whatever happens to shadow it.
    # Elsewhere in tests/ that is dodged by passing argv as a variable, but a
    # noqa only quiets ruff -- CodeQL still reports it -- so resolve it instead.
    git_exe = shutil.which("git")
    if git_exe is None:
        pytest.skip("git executable not found on PATH")
    try:
        out = subprocess.run(  # noqa: S603
            [git_exe, "ls-files", "-z"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git unavailable or this is not a git checkout")
    return [
        REPO_ROOT / rel
        for rel in out.split("\0")
        if rel and Path(rel).suffix.lower() in _TEXT_SUFFIXES
    ]


def test_no_unresolved_conflict_markers_in_tracked_text_files() -> None:
    """A committed merge conflict must fail CI rather than ship.

    Regression: setup-guide.md reached main carrying a raw conflict block --
    the shipped setup guide rendered literal markers to readers -- and every
    gate stayed green because nothing looked for them.
    """
    offenders: list[str] = []
    for path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # A mislabelled binary or an unreadable file is not this guard's
            # business; the suffix filter above already did the real narrowing.
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.startswith(_OPEN_MARKER) or line.startswith(_CLOSE_MARKER):
                rel = path.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel}:{lineno}: {line[:60]}")

    assert not offenders, "unresolved merge conflict markers found:\n" + "\n".join(offenders)
