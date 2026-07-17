"""Security-focused tests: BM25 pickle rejection, API key auth, and async endpoints."""

import copy
import json
import os
import pickle
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

from tests.conftest import TEST_CONFIG


# ---------------------------------------------------------------------------
# Task 1: BM25 pickle RCE rejection
# ---------------------------------------------------------------------------

class TestBM25PickleRejection:
    """Verify that the retriever rejects pickle files and only loads JSON."""

    def test_rejects_malicious_pickle(self, tmp_path):
        """A crafted pickle payload must not execute — JSON loader raises instead."""

        class Evil:
            def __reduce__(self):
                return (os.system, ("echo PWNED > /tmp/pwned.txt",))

        pkl_path = tmp_path / "evil.pkl"
        with open(pkl_path, "wb") as f:
            pickle.dump({"bm25": Evil(), "chunks": [], "metadata": []}, f)

        cfg = copy.deepcopy(TEST_CONFIG)
        cfg["indexing"]["bm25_path"] = str(pkl_path)
        cfg["indexing"]["chroma_path"] = str(tmp_path / "chroma_db")
        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(cfg, f)

        (tmp_path / "chroma_db").mkdir()

        # Stub the semantic vector backend so init reaches the BM25 loader (the
        # path under test) without a real ChromaDB/pgvector store.
        with patch("retrieval.hybrid_search.get_vector_reader") as mock_reader:
            mock_reader.return_value = MagicMock()

            from retrieval.hybrid_search import HybridRetriever
            with pytest.raises((json.JSONDecodeError, ValueError, UnicodeDecodeError)):
                HybridRetriever(config_path=str(config_file))

        assert not Path("/tmp/pwned.txt").exists(), "Malicious pickle payload was executed!"

    def test_loads_json_index_successfully(self, tmp_path):
        """A valid JSON BM25 index loads and rebuilds BM25Okapi."""
        chunks = ["hello world test", "another test doc"]
        tokenized = [c.split() for c in chunks]
        metadata = [{"source": f"doc{i}.md", "chunk_id": i, "stem_tags": "[]"} for i in range(len(chunks))]

        bm25_path = tmp_path / "bm25.json"
        with open(bm25_path, "w") as f:
            json.dump({"tokenized_corpus": tokenized, "chunks": chunks, "metadata": metadata}, f)

        cfg = copy.deepcopy(TEST_CONFIG)
        cfg["indexing"]["bm25_path"] = str(bm25_path)
        cfg["indexing"]["chroma_path"] = str(tmp_path / "chroma_db")
        config_file = tmp_path / "config.yaml"
        with open(config_file, "w") as f:
            yaml.dump(cfg, f)

        (tmp_path / "chroma_db").mkdir()

        # Stub the semantic vector backend so init reaches the BM25 loader (the
        # path under test) without a real ChromaDB/pgvector store.
        with patch("retrieval.hybrid_search.get_vector_reader") as mock_reader:
            mock_reader.return_value = MagicMock()

            from retrieval.hybrid_search import HybridRetriever
            retriever = HybridRetriever(config_path=str(config_file))
            assert len(retriever.bm25_chunks) == 2
            assert retriever.bm25 is not None


# ---------------------------------------------------------------------------
# Task 2: API key auth on soul mutation endpoints
# ---------------------------------------------------------------------------

class TestAPIKeyAuth:
    """Bearer token auth on soul mutation endpoints via Depends()."""

    @pytest.fixture
    def client_with_auth(self, tmp_path):
        """Create a test client with API key enforcement enabled."""
        os.environ["CYCLAW_API_KEY"] = "test-secret-key-12345"
        try:
            from unittest.mock import patch as _patch
            with _patch.dict(os.environ, {"CYCLAW_API_KEY": "test-secret-key-12345"}):
                from gate import require_api_key
                from fastapi.testclient import TestClient
                from fastapi import FastAPI, Depends, HTTPException

                test_app = FastAPI()

                @test_app.get("/open")
                def open_endpoint():
                    return {"status": "ok"}

                @test_app.post("/protected", dependencies=[Depends(require_api_key)])
                def protected_endpoint():
                    return {"status": "ok"}

                yield TestClient(test_app)
        finally:
            os.environ.pop("CYCLAW_API_KEY", None)

    def test_unprotected_endpoint_no_key(self, client_with_auth):
        resp = client_with_auth.get("/open")
        assert resp.status_code == 200

    def test_protected_rejects_no_key(self, client_with_auth):
        resp = client_with_auth.post("/protected")
        assert resp.status_code == 401

    def test_protected_rejects_wrong_key(self, client_with_auth):
        resp = client_with_auth.post("/protected", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401

    def test_protected_accepts_correct_key(self, client_with_auth):
        resp = client_with_auth.post(
            "/protected",
            headers={"Authorization": "Bearer test-secret-key-12345"}
        )
        assert resp.status_code == 200

    def test_fail_closed_when_env_var_unset(self, tmp_path):
        """PR #99 #4 (Option B, fail-closed): with CYCLAW_API_KEY unset, /soul/* is
        NO LONGER open — the endpoint is refused (401), not accepted. No key is
        generated, logged, or stored."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CYCLAW_API_KEY", None)
            from gate import require_api_key
            from fastapi.testclient import TestClient
            from fastapi import FastAPI, Depends

            test_app = FastAPI()

            @test_app.post("/protected", dependencies=[Depends(require_api_key)])
            def protected_endpoint():
                return {"status": "ok"}

            client = TestClient(test_app)
            resp = client.post("/protected")
            assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Task 4: General logging setup
# ---------------------------------------------------------------------------

class TestLoggingSetup:
    """Verify that setup_logging creates file + console handlers."""

    def test_setup_logging_creates_log_file(self, tmp_path):
        import logging
        from utils.logger import setup_logging, _logging_initialized
        import utils.logger as logger_mod

        logger_mod._logging_initialized = False
        log_file = str(tmp_path / "test.log")
        cfg = {"logging": {"level": "DEBUG", "log_file": log_file, "audit_file": str(tmp_path / "audit.jsonl"),
                            "audit_fields": {}}}

        setup_logging(cfg)
        test_logger = logging.getLogger("cyclaw.test_setup")
        test_logger.info("test log message")

        assert Path(log_file).exists()
        content = Path(log_file).read_text()
        assert "test log message" in content

        logger_mod._logging_initialized = False
        root = logging.getLogger("cyclaw")
        root.handlers.clear()
