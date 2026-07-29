"""Contract tests for the committed github_coding_repo fixture and its runner.

The fixture (`tests/fixtures/github_coding_repo/`) is the deterministic,
no-network substrate for `GitHubCodingRunner` (phase 7 of
docs/agentic/GITHUB_DEEP_AGENT_HARNESS_OPTIMIZER_PLAN.md). These tests cover
the runner's security checks the phase 6-9 happy-path tests do not: path-safety
rejection, fixture-case validation, governance findings for missing/undeclared
surfaces and undeclared files, overlays into new subdirectories, holdout cases
on files the candidate never touched, and whole-tree fixture immutability.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from agentic.harness_optimizer import Experiment, Surface, SurfaceType, Variant
from agentic.harness_optimizer.proposer import build_proposer_workspace
from agentic.harness_optimizer.runners.github_coding_runner import (
    FixtureCase,
    GitHubCodingRunner,
    _safe_child,
)
from utils.errors import AgenticError
from utils.logger import close_audit_handles

FIXTURE = Path(__file__).parent / "fixtures" / "github_coding_repo"


@pytest.fixture(autouse=True)
def _close_audit_handles():
    yield
    close_audit_handles()


def _audit_cfg(tmp_path: Path) -> dict:
    return {"logging": {"audit_file": str(tmp_path / "audit.jsonl"), "audit_fields": {}}, "policy": {"privacy": {}}}


def _experiment(surfaces: tuple[Surface, ...] | None = None) -> Experiment:
    return Experiment(
        experiment_id="fixture_repo_expansion",
        target_workspace="data/agentic/workspaces/fixture_repo_expansion",
        surfaces=surfaces
        or (
            Surface("planner", SurfaceType.GITHUB_CODING_PROMPT, "planner.py"),
            Surface("scheduler", SurfaceType.GITHUB_CODING_PROMPT, "scheduler.py"),
        ),
        train_visible=("case-visible",),
        holdout_hidden=("case-hidden",),
    )


def _workspace(tmp_path: Path, experiment: Experiment | None = None):
    cfg = _audit_cfg(tmp_path)
    workspace = build_proposer_workspace(tmp_path / "runs", experiment or _experiment(), "candidate", cfg=cfg)
    return workspace, cfg


def _runner(workspace, cfg: dict, cases: tuple[FixtureCase, ...] | None = None) -> GitHubCodingRunner:
    return GitHubCodingRunner(
        fixture_repo=FIXTURE,
        workspace=workspace,
        cases=cases
        or (
            FixtureCase("case-visible", "train_visible", "planner.py", "def render"),
            FixtureCase("case-hidden", "holdout_hidden", "scheduler.py", "plan_window"),
        ),
        cfg=cfg,
    )


def _propose(workspace, surface_path: str, content: str) -> None:
    target = workspace.current_dir / surface_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    workspace.proposal_path.write_text("# Proposal\n\nGeneral fix.", encoding="utf-8")


# -- path safety --------------------------------------------------------------------

def test_safe_child_rejects_escape_shapes(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    for bad in (
        "/etc/passwd",              # POSIX absolute
        "C:\\Windows\\system.ini",  # Windows absolute
        "../escape.py",             # parent climb
        "a/../../escape.py",        # embedded climb
        "drive:x.py",               # drive-letter shape
        "",                         # empty
        ".",                        # resolves to the root itself
        "\x00bad",                  # NUL byte
    ):
        with pytest.raises(AgenticError):
            _safe_child(root, bad)


def test_safe_child_rejects_root_relative_shapes_on_every_host(tmp_path: Path) -> None:
    # Regression for a HOST-DEPENDENT hole in the guard. `Path(...)` takes the
    # running platform's flavour, so each of these slipped through on exactly
    # one OS and was then re-split into a path *inside* the root:
    #   "/etc/passwd"           -> passed on Windows  (WindowsPath and
    #                              PureWindowsPath both call a drive-less root
    #                              relative), landing as root/etc/passwd
    #   "\\Windows\\system.ini" -> passed on POSIX    (one filename to
    #                              PurePosixPath, driveless to PureWindowsPath),
    #                              landing as root/Windows/system.ini
    # Asserting both on every platform is the point: a one-OS check is what let
    # this survive, and the CI matrix only runs the Windows leg on one lane.
    root = tmp_path / "root"
    root.mkdir()
    for bad in (
        "/etc/passwd",              # POSIX root
        "/",                        # bare root
        "\\Windows\\system.ini",    # Windows drive-relative root
        "\\\\server\\share\\x",     # UNC
        "C:/Windows/system.ini",    # drive with forward slashes
    ):
        with pytest.raises(AgenticError):
            _safe_child(root, bad)


def test_safe_child_allows_nested_relative_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    (root / "docs").mkdir(parents=True)
    assert _safe_child(root, "docs/usage.md") == (root / "docs" / "usage.md").resolve()
    assert _safe_child(root, "docs\\usage.md") == (root / "docs" / "usage.md").resolve()


def test_fixture_case_validation() -> None:
    with pytest.raises(AgenticError):
        FixtureCase("", "train_visible", "planner.py", "x")
    with pytest.raises(AgenticError):
        FixtureCase("   ", "train_visible", "planner.py", "x")
    with pytest.raises(AgenticError):
        FixtureCase("c", "not_a_split", "planner.py", "x")
    with pytest.raises(AgenticError):
        FixtureCase("c", "train_visible", "../escape.py", "x")
    with pytest.raises(AgenticError):
        FixtureCase("c", "train_visible", "planner.py", "")


def test_runner_requires_fixture_dir_and_cases(tmp_path: Path) -> None:
    workspace, cfg = _workspace(tmp_path)
    case = FixtureCase("case-visible", "train_visible", "planner.py", "x")
    with pytest.raises(AgenticError, match="fixture repository"):
        GitHubCodingRunner(fixture_repo=tmp_path / "missing", workspace=workspace, cases=(case,), cfg=cfg)
    with pytest.raises(AgenticError, match="at least one fixture case"):
        GitHubCodingRunner(fixture_repo=FIXTURE, workspace=workspace, cases=(), cfg=cfg)


# -- experiment/case declaration mismatches -----------------------------------------

def test_undeclared_train_case_raises(tmp_path: Path) -> None:
    workspace, cfg = _workspace(tmp_path)
    runner = _runner(workspace, cfg, cases=(FixtureCase("case-other", "train_visible", "planner.py", "x"),))
    with pytest.raises(AgenticError, match="not declared"):
        runner.run(_experiment(), Variant("baseline", (), "proposal.md", str(workspace.root)))


def test_undeclared_holdout_case_raises(tmp_path: Path) -> None:
    workspace, cfg = _workspace(tmp_path)
    runner = _runner(workspace, cfg, cases=(FixtureCase("case-other", "holdout_hidden", "planner.py", "x"),))
    with pytest.raises(AgenticError, match="not declared"):
        runner.run(_experiment(), Variant("baseline", (), "proposal.md", str(workspace.root)))


# -- governance findings -------------------------------------------------------------

def test_missing_declared_surface_is_a_critical_finding(tmp_path: Path) -> None:
    workspace, cfg = _workspace(tmp_path)
    # Variant declares the scheduler surface but current/ holds no scheduler.py.
    workspace.proposal_path.write_text("# Proposal\n\nGeneral fix.", encoding="utf-8")
    report = _runner(workspace, cfg).run(_experiment(), Variant("candidate", ("scheduler",), "proposal.md", str(workspace.root)))
    assert any(f.startswith("critical: missing_candidate_file") for f in report.governance_findings)


def test_undeclared_surface_change_is_a_critical_finding(tmp_path: Path) -> None:
    workspace, cfg = _workspace(tmp_path)
    _propose(workspace, "planner.py", 'def render() -> str:\n    return "fixed"\n')
    report = _runner(workspace, cfg).run(_experiment(), Variant("candidate", ("ghost",), "proposal.md", str(workspace.root)))
    assert any(f.startswith("critical: unallowed_surface") for f in report.governance_findings)


def test_undeclared_file_in_current_is_a_critical_finding(tmp_path: Path) -> None:
    workspace, cfg = _workspace(tmp_path)
    _propose(workspace, "planner.py", 'def render() -> str:\n    return "fixed"\n')
    (workspace.current_dir / "sneaky.py").write_text("x = 1\n", encoding="utf-8")
    report = _runner(workspace, cfg).run(_experiment(), Variant("candidate", ("planner",), "proposal.md", str(workspace.root)))
    assert any(f.startswith("critical: unallowed_candidate_file") for f in report.governance_findings)


# -- the expansion itself: shapes the one-file fixture could not express -------------

def test_holdout_case_watches_a_file_the_candidate_never_touched(tmp_path: Path) -> None:
    # The canonical anti-overfitting shape: the visible case scores the surface
    # the candidate changed, while the holdout case scores an untouched file.
    # With only planner.py the fixture could not express this -- both cases had
    # to read the same file.
    workspace, cfg = _workspace(tmp_path)
    _propose(workspace, "scheduler.py", 'def plan_window() -> str:\n    return "optimized"\n')
    runner = GitHubCodingRunner(
        fixture_repo=FIXTURE,
        workspace=workspace,
        cases=(
            FixtureCase("case-visible", "train_visible", "scheduler.py", "optimized"),
            FixtureCase("case-hidden", "holdout_hidden", "planner.py", "def render"),
        ),
        cfg=cfg,
    )
    baseline = runner.run(_experiment(), Variant("baseline", (), "proposal.md", str(workspace.root)))
    candidate = runner.run(_experiment(), Variant("candidate", ("scheduler",), "proposal.md", str(workspace.root)))
    assert baseline.score == 0.5  # visible fails (scheduler still "baseline"), holdout passes
    assert candidate.score == 1.0


def test_overlay_creates_a_new_subdirectory_file(tmp_path: Path) -> None:
    # A surface path whose parent directory does not exist in the fixture
    # exercises the overlay's mkdir(parents=True) branch -- and must not leak
    # the new directory back into the committed fixture.
    experiment = _experiment((Surface("added", SurfaceType.GITHUB_CODING_PROMPT, "src/added.py"),))
    workspace, cfg = _workspace(tmp_path, experiment)
    _propose(workspace, "src/added.py", 'def added() -> str:\n    return "new"\n')
    runner = GitHubCodingRunner(
        fixture_repo=FIXTURE,
        workspace=workspace,
        cases=(
            FixtureCase("case-visible", "train_visible", "src/added.py", "new"),
            FixtureCase("case-hidden", "holdout_hidden", "planner.py", "def render"),
        ),
        cfg=cfg,
    )
    report = runner.run(experiment, Variant("candidate", ("added",), "proposal.md", str(workspace.root)))
    assert report.score == 1.0
    assert not (FIXTURE / "src").exists()


def test_nested_fixture_file_is_a_valid_case_target(tmp_path: Path) -> None:
    workspace, cfg = _workspace(tmp_path)
    runner = _runner(
        workspace,
        cfg,
        cases=(FixtureCase("case-visible", "train_visible", "docs/usage.md", "GitHubCodingRunner"),),
    )
    report = runner.run(_experiment(), Variant("baseline", (), "proposal.md", str(workspace.root)))
    assert report.train_passed is True


# -- committed-fixture integrity ------------------------------------------------------

def _tree_digest(root: Path) -> dict[str, str]:
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_committed_fixture_tree_is_never_mutated(tmp_path: Path) -> None:
    before = _tree_digest(FIXTURE)
    workspace, cfg = _workspace(tmp_path)
    _propose(workspace, "planner.py", 'def render() -> str:\n    return "mutated"\n')
    (workspace.current_dir / "scheduler.py").write_text('def plan_window() -> str:\n    return "mutated"\n', encoding="utf-8")
    _runner(workspace, cfg).run(
        _experiment(), Variant("candidate", ("planner", "scheduler"), "proposal.md", str(workspace.root))
    )
    assert _tree_digest(FIXTURE) == before


def test_fixture_python_files_compile_and_docs_exist() -> None:
    sources = sorted(FIXTURE.rglob("*.py"))
    assert sources, "fixture must contain at least one Python module"
    for source in sources:
        compile(source.read_text(encoding="utf-8"), str(source), "exec")
    assert (FIXTURE / "README.md").is_file()
    assert (FIXTURE / "docs" / "usage.md").is_file()
    assert (FIXTURE / "scheduler.py").is_file()
