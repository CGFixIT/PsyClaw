"""CI smoke test for the `real-repo-run` wiring, over real sockets and real subprocesses.

``tests/test_agentic_real_repo_run_cli.py`` already covers this command's
contract exhaustively, but mocks all three of its outer boundaries in Python:
``agentic.context.run_read`` (task context), ``agentic.deepagent_github.
repo_workspace.run_read`` (the clone), and ``LocalProposerClient.invoke``
(the planner model call) are all monkeypatched directly. That is deliberate
and correct for contract tests, but it means none of them ever touch a real
socket, a real ``gh`` subprocess, or a real timeout -- and an emulated
rehearsal on 2026-08-02 found two real defects in exactly that gap (the
planner timeout never reaching the model client, and the ``ops_runner``
ceiling not scaling with it once it did). Both were invisible to the existing
suite for the same reason: every test builds ``LocalProposerClient`` with an
``httpx.MockTransport``.

This file closes that gap the same way ``tests/test_llm_client_ollama.py``
closes the equivalent one for ``LocalLLMClient``: replace the Python-level
mock with the real thing, one layer down.

- The **model** is a real ``http.server.HTTPServer`` on an ephemeral loopback
  port (mirrors ``test_llm_client_ollama.py``'s ``mock_ollama`` fixture
  exactly), not a monkeypatched ``.invoke()``.
- ``gh`` is a real executable script on ``PATH`` (mirrors nothing existing --
  every other test stubs ``gh_client.run_read`` itself, never exercising
  ``check_gh_version``'s or ``build_read_argv``'s own subprocess path), not a
  monkeypatched module reference.
- The clone target is a real ``git init --bare`` repository on local disk that
  the fake ``gh``'s ``repo clone`` handler actually ``git clone``s from, not a
  directory the test fabricates directly.

No live GitHub, no live model daemon, no torch/chromadb: ``agentic/`` never
imports ``retrieval``/``gate``/``graph`` (invariant I6), so the local-only
``real-repo-run`` path here needs nothing beyond ``httpx``/``pyyaml``/git --
verified by reading every module on this call path's own import list, not
assumed. Scoped deliberately narrow: this proves the WIRING (a real run
reaches ``pending_decision`` with the right ``changed_files``), not model
quality or realistic timing -- see ``docs/agentic/DEFERRED_WORK.md`` D1 for
the full design rationale and why a latency-emulating version was rejected
for CI (a runner's CPU says nothing about an operator's own hardware).
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

from agentic.cli import EXIT_OK, main
from agentic.gh_client import check_gh_version

_PLAN_BLOCK = "=== FILE target.txt ===\nexpected marker\n=== END FILE ===\nadd the marker"

_FAKE_GH_SCRIPT = '''#!/usr/bin/env python3
"""Fake `gh` for the real-repo-run smoke test -- handles exactly the ops
`agentic.gh_client` can issue on the --repo (local model) code path: the
version floor check, a repo overview + empty PR/issue shortlists (so
fetch_repo_context's own default allowed_read_ops sweep succeeds), and a
real `git clone` of the bare repo the test prepared, so gh_client's own
subprocess-invocation and argv-construction code both run for real."""
import json
import os
import subprocess
import sys

argv = sys.argv[1:]

if argv[:1] == ["version"]:
    print("gh version 2.60.0 (2026-01-01)")
    sys.exit(0)

if argv[:2] == ["repo", "view"]:
    print(json.dumps({
        "name": "CyClaw", "owner": {"login": "cgfixit"}, "description": "smoke fixture repo",
        "defaultBranchRef": {"name": "main"}, "isPrivate": False,
        "url": "https://github.com/cgfixit/CyClaw", "isArchived": False,
        "pushedAt": "2026-08-02T00:00:00Z", "primaryLanguage": {"name": "Python"},
        "repositoryTopics": [], "stargazerCount": 0, "licenseInfo": None,
    }))
    sys.exit(0)

if argv[:2] in (["pr", "list"], ["issue", "list"]):
    print("[]")
    sys.exit(0)

if argv[:2] == ["repo", "clone"]:
    repo, dest = argv[2], argv[3]
    bare = os.environ["CYCLAW_SMOKE_BARE_REPO"]
    subprocess.run(["git", "clone", "-q", "--depth", "1", bare, dest], check=True)
    sys.exit(0)

sys.exit(f"fake gh: unhandled invocation {argv!r}")
'''


@pytest.fixture()
def fake_gh_on_path(tmp_path, monkeypatch):
    """Put a real, executable fake `gh` at the front of PATH for this test only.

    ``check_gh_version`` is ``lru_cache``d for the life of the process (see
    its own docstring) -- cleared before and after so this test's fake `gh`
    can never leak a cached version tuple into another test file, mirroring
    ``tests/test_agentic_gh_client.py``'s own ``_temp_audit`` fixture, the
    only other place in the suite that exercises this real, non-mocked path.
    """
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir()
    gh_path = bin_dir / "gh"
    gh_path.write_text(_FAKE_GH_SCRIPT, encoding="utf-8")
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}")
    check_gh_version.cache_clear()
    yield gh_path
    check_gh_version.cache_clear()


@pytest.fixture()
def real_bare_repo(tmp_path):
    """A real `git init --bare` repository with one real commit, cloneable for real."""
    git_bin = shutil.which("git")
    assert git_bin, "git must be on PATH for this smoke test"
    scratch = tmp_path / "scratch-origin"
    scratch.mkdir()
    (scratch / "README.md").write_text("smoke fixture\n", encoding="utf-8")

    def run(*argv: str) -> None:
        subprocess.run(argv, cwd=str(scratch), check=True, capture_output=True, text=True)

    run(git_bin, "init", "-q")
    run(git_bin, "-c", "user.email=fixture@example.com", "-c", "user.name=Fixture", "add", "-A")
    run(git_bin, "-c", "user.email=fixture@example.com", "-c", "user.name=Fixture", "commit", "-q", "-m", "initial")

    bare = tmp_path / "origin.git"
    subprocess.run([git_bin, "clone", "-q", "--bare", str(scratch), str(bare)], check=True, capture_output=True)
    return bare


class _InstantAnsweringHandler(BaseHTTPRequestHandler):
    """Minimal OpenAI-compatible `/chat/completions` responder -- always the same block."""

    def log_message(self, fmt: str, *args: object) -> None:  # silence test-run noise
        pass

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)  # drain the request body; content isn't inspected here
        payload = json.dumps({"choices": [{"message": {"content": _PLAN_BLOCK}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture()
def instant_model_server():
    """A real HTTP server on an ephemeral loopback port, torn down after the test."""
    server = HTTPServer(("127.0.0.1", 0), _InstantAnsweringHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    yield f"http://127.0.0.1:{port}/v1"
    server.shutdown()
    server.server_close()


@pytest.fixture()
def smoke_config(tmp_path, monkeypatch, instant_model_server):
    """A config.yaml wired to the real mock model server, git writes enabled."""
    from agentic import config as agentic_config_module
    from utils.logger import reset_config_cache

    monkeypatch.setattr(agentic_config_module, "_repo_root", lambda: tmp_path)
    src = yaml.safe_load(Path("config.yaml").read_text(encoding="utf-8"))
    src["logging"]["audit_file"] = str(tmp_path / "audit.jsonl")
    src["agentic"]["enabled"] = True
    src["agentic"]["deepagent_github"]["enabled"] = True
    src["agentic"]["deepagent_github"]["allow_git_write_tools"] = True
    src["agentic"]["deepagent_github"]["workspace_root"] = str(tmp_path / "data" / "workspaces")
    src["agentic"]["deepagent_github"]["base_url"] = instant_model_server
    src["agentic"]["deepagent_github"]["model"] = "local-test-model"
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump(src), encoding="utf-8")
    reset_config_cache()
    yield str(path)
    reset_config_cache()


@pytest.fixture()
def checks_file(tmp_path):
    marker_check = {
        "name": "marker_check",
        "argv": [
            sys.executable, "-c",
            "import pathlib,sys; sys.exit(0 if 'expected marker' in pathlib.Path('target.txt').read_text() else 1)",
        ],
    }
    path = tmp_path / "checks.json"
    path.write_text(json.dumps([marker_check]), encoding="utf-8")
    return str(path)


def test_real_repo_run_reaches_pending_decision_over_real_socket_and_gh(
    fake_gh_on_path, real_bare_repo, smoke_config, checks_file, monkeypatch, capsys,
):
    """One full plan -> patch -> verify cycle through the real CLI.

    Every boundary this test crosses is the real thing: a real HTTP POST to
    a real socket for the planner call, a real `gh` subprocess (the fake
    script) invoked through `agentic.gh_client`'s real subprocess/version/
    argv machinery, and a real `git clone` of a real bare repository. Only
    GitHub itself and the operator's local model daemon are out of the loop.
    """
    monkeypatch.setenv("CYCLAW_SMOKE_BARE_REPO", str(real_bare_repo))

    code = main([
        "--config", smoke_config, "real-repo-run",
        "--repo", "--instruction", "add the marker",
        "--checks-file", checks_file,
        "--branch", "claude/smoke-topic", "--commit-message", "add target.txt",
        "--reason", "CI smoke test", "--confirm",
    ])

    out = capsys.readouterr().out
    assert code == EXIT_OK, capsys.readouterr().err
    record = json.loads(out)
    assert record["status"] == "pending_decision"
    assert record["branch_name"] == "claude/smoke-topic"
    assert record["changed_files"] == ["target.txt"]
    assert (Path(record["dest"]) / "target.txt").read_text(encoding="utf-8") == "expected marker"
