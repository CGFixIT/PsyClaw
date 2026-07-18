"""Real retriever-through-graph integration test.

CI already runs tests.ci_rag_smoke against the committed corpus. This test keeps
the distinct signal: a real ChromaDB + BM25 index flowing through graph.py.
"""

from pathlib import Path

import yaml

from graph import build_graph
from retrieval.indexer import build_index
from retrieval.hybrid_search import HybridRetriever
from tests.conftest import MockLocalLLM
from utils.logger import close_audit_handles, reset_config_cache


# Repo root, for cwd-independent reads of the shipped config.yaml. A bare
# Path("config.yaml") breaks when pytest is invoked from outside the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _fake_embedding(_text: str, _config_path: str = "config.yaml") -> list[float]:
    return [1.0, 0.0, 0.0]


def _write_isolated_config(tmp_path: Path) -> tuple[Path, dict]:
    cfg = yaml.safe_load((_REPO_ROOT / "config.yaml").read_text(encoding="utf-8"))

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "overview.md").write_text(
        "# CyClaw Retrieval\n\n"
        "CyClaw blends semantic embeddings with BM25 keyword search using "
        "Reciprocal Rank Fusion before the graph routes to the local LLM.\n",
        encoding="utf-8",
    )

    cfg["corpus"] = {"path": str(corpus), "extensions": [".md"]}
    cfg["indexing"] = {
        **cfg["indexing"],
        "chroma_path": str(tmp_path / "chroma_db"),
        "bm25_path": str(tmp_path / "bm25.json"),
        "collection_name": "rag_integration_test",
    }
    cfg["models"]["embeddings"] = {
        **cfg["models"]["embeddings"],
        "cache_dir": str(tmp_path / ".emb_cache"),
    }
    cfg["retrieval"] = {
        **cfg["retrieval"],
        "top_k_semantic": 2,
        "top_k_keyword": 2,
        "min_score": 0.028,
    }
    cfg["logging"] = {
        **cfg["logging"],
        "audit_file": str(tmp_path / "audit.jsonl"),
    }

    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return config_path, cfg


def test_real_retriever_result_flows_through_graph(tmp_path, monkeypatch) -> None:
    """Real index -> HybridRetriever -> LangGraph -> local answer + audit."""
    config_path, cfg = _write_isolated_config(tmp_path)
    reset_config_cache()
    monkeypatch.setattr(
        "retrieval.indexer.get_embeddings_batch",
        lambda texts, config_path="config.yaml": [_fake_embedding(text) for text in texts],
    )
    monkeypatch.setattr("retrieval.hybrid_search.get_embedding", _fake_embedding)
    monkeypatch.setattr("utils.logger._get_config", lambda config_path="config.yaml": cfg)

    build_index(str(config_path))
    retriever = HybridRetriever(str(config_path))
    llm = MockLocalLLM(response="local answer")

    try:
        graph = build_graph(retriever=retriever, llm=llm, grok=None, cfg=cfg)
        result = graph.invoke({
            "query": "How does CyClaw blend semantic embeddings with BM25 keyword search?"
        })
    finally:
        retriever.close()
        close_audit_handles()
        reset_config_cache()

    assert result["answer_model"] == "local"
    assert result["top_score"] >= cfg["retrieval"]["min_score"]
    assert result["answer_sources"]
    assert "overview.md" in result["answer_sources"][0]["source"]
    assert result["audit_event"]["event"] == "rag_query"
    assert result["audit_event"]["model_used"] == "local"
    assert llm.last_prompt is not None
    assert "Reciprocal Rank Fusion" in llm.last_prompt
