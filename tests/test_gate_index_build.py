"""Tests for the first-run index-build routes (POST /index/build, GET /index/status).

A missing index has always been fail-soft -- /query answers 503
INDEX_NOT_FOUND rather than crashing -- but the only way out was a CLI command
plus a process restart. These routes let the console recover from first-run in
place, so they are the one path a brand-new operator is guaranteed to touch.

The real build cannot run here: it needs the sentence-transformers model, which
tests/conftest.py mocks away and CI has no network for. So build_index is
patched throughout and the assertions are about the STATE MACHINE and the
GATES -- which is where the risk actually is (a concurrent second build
corrupts the index; a non-loopback caller must not be able to start one).
"""

import logging
import threading
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import gate


@pytest.fixture
def idle_client():
    """A client with the build state reset to idle, restored afterwards.

    gate._index_build is module-global, so a test that leaves it "running"
    would make every later test's /index/build return 409. Save and restore.
    """
    saved = dict(gate._index_build)
    gate._index_build.update({
        "state": "idle", "started_at": None, "finished_at": None,
        "error": None, "chunks_done": 0, "chunks_total": 0,
    })
    client = TestClient(
        gate.app,
        base_url="http://localhost",  # DevSkim: ignore DS162092,DS137138 - test loopback host
        client=("127.0.0.1", 51234),  # DevSkim: ignore DS162092,DS137138
    )
    try:
        yield client
    finally:
        gate._index_build.clear()
        gate._index_build.update(saved)


class TestIndexStatus:
    def test_status_is_always_200_even_with_no_index(self, idle_client):
        """Never 404/503: the console polls this to decide what to render, so an
        error status would be indistinguishable from 'no index yet'."""
        resp = idle_client.get("/index/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["state"] == "idle"
        assert body["error"] is None
        assert "index_ready" in body

    def test_status_reports_progress_from_the_running_build(self, idle_client):
        gate._index_build.update({
            "state": "running", "started_at": 100.0, "finished_at": None,
            "chunks_done": 40, "chunks_total": 120,
        })
        body = idle_client.get("/index/status").json()
        assert body["state"] == "running"
        assert (body["chunks_done"], body["chunks_total"]) == (40, 120)
        assert body["elapsed_sec"] is not None


class TestIndexBuildGates:
    def test_non_loopback_peer_is_refused(self):
        """The gate is the SOCKET peer, which a Host or Origin header cannot
        forge. Deliberately not the API key: on a genuine first run
        CYCLAW_API_KEY may be unset, and an unset key fails CLOSED, which would
        brick the exact flow this route exists to unblock."""
        remote = TestClient(
            gate.app,
            base_url="http://localhost",  # DevSkim: ignore DS162092,DS137138 - test loopback host
            client=("203.0.113.7", 51234),  # DevSkim: ignore DS162092,DS137138 - TEST-NET-3
        )
        resp = remote.post("/index/build")
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "INDEX_BUILD_LOOPBACK_ONLY"

    def test_cross_site_request_is_refused(self, idle_client):
        """A bodyless cross-origin POST is a 'simple request': no preflight, so
        it REACHES the handler and its side effect happens. Same reasoning as
        _looks_cross_site's own docstring."""
        resp = idle_client.post("/index/build", headers={"Origin": "https://evil.example"})
        assert resp.status_code == 403
        assert resp.json()["detail"]["code"] == "CROSS_SITE_BLOCKED"

    def test_second_concurrent_build_is_refused_with_409(self, idle_client):
        """Two builds would write the same ChromaDB collection and the same
        bm25.json, so the loser corrupts the winner."""
        gate._index_build["state"] = "running"
        resp = idle_client.post("/index/build")
        assert resp.status_code == 409
        assert resp.json()["detail"]["code"] == "INDEX_BUILD_IN_PROGRESS"

    def test_start_marks_running_and_spawns_a_worker(self, idle_client):
        """Patch the WORKER, never threading.Thread.

        gate.threading is the real threading module, so patching Thread on it
        replaces it process-wide -- including for pytest's own machinery, which
        deadlocks the run rather than failing it. The route resolves
        _run_index_build from the module namespace when it builds the thread,
        so patching that name is both sufficient and contained.
        """
        ran = threading.Event()
        with patch.object(gate, "_run_index_build", side_effect=ran.set):
            resp = idle_client.post("/index/build")
            assert resp.status_code == 200
            assert resp.json()["state"] == "running"
            # Real daemon thread, so wait for it rather than assuming it ran.
            assert ran.wait(timeout=5), "the worker thread never started"


class TestIndexBuildWorker:
    """gate._run_index_build is the body the thread runs; call it directly."""

    def test_success_hot_inits_retrieval_and_reports_done(self, idle_client):
        """The point of the whole feature: after a build the process must serve
        the new index WITHOUT a restart, because /query resolves compiled_graph
        at call time rather than at import."""
        saved = gate.compiled_graph
        try:
            def _fake_init(*, boot=False):
                gate.compiled_graph = object()  # stand-in for a live graph

            with patch("retrieval.indexer.build_index") as mock_build, \
                 patch.object(gate, "_init_retrieval", side_effect=_fake_init) as mock_init:
                gate._run_index_build()

            mock_build.assert_called_once()
            mock_init.assert_called_once()
            assert gate._index_build["state"] == "done"
            assert gate._index_build["error"] is None
        finally:
            gate.compiled_graph = saved

    def test_build_that_produces_no_index_is_an_error_not_a_success(self, idle_client):
        """build_index returning None is not proof of success -- it always
        returns None. The real check is whether a graph now exists."""
        saved = gate.compiled_graph
        try:
            gate.compiled_graph = None
            with patch("retrieval.indexer.build_index"), \
                 patch.object(gate, "_init_retrieval"):
                gate._run_index_build()
            assert gate._index_build["state"] == "error"
            assert "no index" in gate._index_build["error"].lower()
        finally:
            gate.compiled_graph = saved

    def test_failure_is_sanitized_and_never_raises(self, idle_client):
        """Runs on a daemon thread with no caller to catch it, and the message
        reaches a browser -- so it must be sanitized, not a raw exception."""
        with patch("retrieval.indexer.build_index", side_effect=RuntimeError("/secret/path exploded")), \
             patch.object(gate, "_init_retrieval"):
            gate._run_index_build()  # must not raise
        assert gate._index_build["state"] == "error"
        assert gate._index_build["error"]
        assert "/secret/path" not in gate._index_build["error"]

    def test_progress_handler_is_removed_even_on_failure(self, idle_client):
        """A leaked handler would keep firing on every later indexer log line
        and slowly accumulate one handler per failed build."""
        idx_logger = logging.getLogger("retrieval.indexer")
        before = len(idx_logger.handlers)
        with patch("retrieval.indexer.build_index", side_effect=RuntimeError("boom")), \
             patch.object(gate, "_init_retrieval"):
            gate._run_index_build()
        assert len(idx_logger.handlers) == before


class TestIndexProgressHandler:
    """Progress is read from the indexer's own log records.

    build_index takes no callback, so there is nothing to subscribe to. The
    handler matches on record.msg -- the FORMAT STRING, not the rendered text
    -- so it needs no string parsing and survives a wording change that keeps
    the same literal.
    """

    def test_reads_counts_from_the_indexer_progress_record(self):
        gate._index_build.update({"chunks_done": 0, "chunks_total": 0})
        handler = gate._IndexProgressHandler()
        handler.emit(logging.LogRecord(
            "retrieval.indexer", logging.INFO, __file__, 1,
            "Indexed %d/%d chunks", (50, 200), None,
        ))
        assert (gate._index_build["chunks_done"], gate._index_build["chunks_total"]) == (50, 200)

    def test_ignores_unrelated_records(self):
        gate._index_build.update({"chunks_done": 7, "chunks_total": 9})
        handler = gate._IndexProgressHandler()
        handler.emit(logging.LogRecord(
            "retrieval.indexer", logging.INFO, __file__, 1,
            "Done. Semantic backend: %s, BM25: %s", ("chroma", "x.json"), None,
        ))
        assert (gate._index_build["chunks_done"], gate._index_build["chunks_total"]) == (7, 9)

    def test_a_malformed_record_does_not_break_the_build(self):
        """Progress is best-effort: the handler runs inside the indexer's own
        logging call, so raising here would abort a working build."""
        handler = gate._IndexProgressHandler()
        handler.emit(logging.LogRecord(
            "retrieval.indexer", logging.INFO, __file__, 1,
            "Indexed %d/%d chunks", ("not-a-number",), None,
        ))  # must not raise
