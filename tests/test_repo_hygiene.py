"""Repo-tree hygiene guards that no other checker owns.

Lives in tests/ rather than a skill or a workflow step on purpose: the three
`test` legs are release gates, while `lint.yml` is advisory end-to-end
(continue-on-error on both linters AND the job) and the `verify-skills` matrix
is continue-on-error too. A guard that must actually block a merge has to run
here.
"""

from __future__ import annotations

import re
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
        raise
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
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if line.startswith(_OPEN_MARKER) or line.startswith(_CLOSE_MARKER):
                rel = path.relative_to(REPO_ROOT).as_posix()
                offenders.append(f"{rel}:{lineno}: {line[:60]}")

    assert not offenders, "unresolved merge conflict markers found:\n" + "\n".join(offenders)


# Characters Windows forbids in a path component, plus the reserved device
# names. A tracked path containing any of these cannot be checked out on a
# Windows runner at all: `git checkout` aborts with "error: invalid path" and
# exit 128, so all three Windows legs die before a single test body runs.
_WINDOWS_FORBIDDEN_CHARS = frozenset('<>:"|?*')
_WINDOWS_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{n}" for n in range(1, 10)}
    | {f"lpt{n}" for n in range(1, 10)}
)


def _tracked_paths() -> list[str]:
    git_exe = shutil.which("git")
    if git_exe is None:
        pytest.skip("git executable not found on PATH")
    out = subprocess.run(  # noqa: S603
        [git_exe, "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [rel for rel in out.split("\0") if rel]


def test_no_tracked_path_is_uncheckoutable_on_windows() -> None:
    """A path Windows cannot create must never reach a tracked tree.

    Regression: a test fixture passed sqlite's ":memory:" sentinel as a
    db_path, but memory.store.connect treats db_path as a filesystem path and
    creates it -- so a zero-byte file literally named ":memory:" appeared in the
    repo root and a `git add -A` committed it. Linux and macOS did not care;
    all three Windows jobs failed identically in `git checkout` with
    "error: invalid path ':memory:'", ten seconds in, before any test ran.

    Nothing caught it locally, because the whole suite is green on a machine
    whose filesystem accepts the name. This guard is the missing check: it runs
    on every platform and fails on the tracked path list alone.
    """
    offenders: list[str] = []
    for rel in _tracked_paths():
        for component in rel.split("/"):
            bad = sorted(_WINDOWS_FORBIDDEN_CHARS & set(component))
            if bad:
                offenders.append(f"{rel}  (forbidden on Windows: {''.join(bad)})")
                break
            # "NUL", "nul.txt" and "NUL.tar.gz" are all reserved; the check is
            # on the first dot-separated segment, case-insensitively.
            if component.split(".")[0].lower() in _WINDOWS_RESERVED_STEMS:
                offenders.append(f"{rel}  (reserved Windows device name)")
                break

    assert not offenders, (
        "tracked paths that Windows cannot check out:\n" + "\n".join(offenders)
    )


_README_TEST_COUNT = re.compile(r"\((\d+)\s+`test_\*\.py` files")


def test_tests_readme_test_file_count_matches_tree() -> None:
    """tests/README.md suite-count integer must match find tests -name 'test_*.py'.

    Regression: the lede was bumped 181→183, then 194→209 on #1214, then left
    stale again after later test files (including tests/nemo_runtime/) landed.
    `ls tests/test_*.py` misses nested files that pytest testpaths=["tests"]
    still collects. Count recursively; put new guards in this file so the
    integer does not move just because the pin exists.
    """
    readme = (REPO_ROOT / "tests" / "README.md").read_text(encoding="utf-8")
    match = _README_TEST_COUNT.search(readme)
    assert match, (
        "tests/README.md must state '(N `test_*.py` files' in the lede so the "
        "count can be pinned"
    )
    claimed = int(match.group(1))
    actual = sum(
        1
        for path in (REPO_ROOT / "tests").rglob("test_*.py")
        if path.is_file() and "__pycache__" not in path.parts
    )
    assert claimed == actual, (
        f"tests/README.md claims {claimed} test_*.py files; "
        f"find tests -name 'test_*.py' is {actual}. "
        f"Count recursively (including tests/nemo_runtime/), not ls tests/test_*.py."
    )
