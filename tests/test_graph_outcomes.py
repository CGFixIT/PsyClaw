"""Data-driven graph / gate outcome battery (issue #960, first six cases).

Each line in ``tests/fixtures/graph_outcomes.jsonl`` is one case. Graph rows
reuse the mocked ``test_graph`` stack (no embeddings, no live LLM). Gate rows
call ``check_input`` only — injection is rejected before ``retrieve`` (I1 still
holds *inside* the graph).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from graph import build_graph
from tests.conftest import (
    MOCK_EMPTY_RESULTS,
    MOCK_HIGH_SCORE_RESULTS,
    MOCK_LOW_SCORE_RESULTS,
    MockGrokClient,
    MockLocalLLM,
    MockRetriever,
    TEST_CONFIG,
)
from tests.test_graph import _make_cfg
from utils.errors import PromptInjectionError
from utils.logger import reset_config_cache
from utils.sanitizer import check_input

_REPO_ROOT = Path(__file__).resolve().parents[1]
_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "graph_outcomes.jsonl"
_SHIPPED_CONFIG = _REPO_ROOT / "config.yaml"

_MOCK_RESULTS = {
    "high": MOCK_HIGH_SCORE_RESULTS,
    "low": MOCK_LOW_SCORE_RESULTS,
    "empty": MOCK_EMPTY_RESULTS,
}


def _load_cases() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _FIXTURE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        case = json.loads(line)
        if not isinstance(case, dict) or "id" not in case:
            raise ValueError(f"graph_outcomes.jsonl: expected object with id, got {case!r}")
        rows.append(case)
    return rows


CASES = _load_cases()


@pytest.fixture(autouse=True)
def _audit_to_tmp(tmp_path, monkeypatch):
    cfg = {
        **TEST_CONFIG,
        "logging": {
            **TEST_CONFIG["logging"],
            "audit_file": str(tmp_path / "audit.jsonl"),
            "log_file": str(tmp_path / "gateway.log"),
        },
    }
    reset_config_cache()
    monkeypatch.setattr("utils.logger._get_config", lambda config_path="config.yaml": cfg)
    yield
    reset_config_cache()


def _source_names(result: dict[str, Any]) -> set[str]:
    found: set[str] = set()
    for key in ("retrieved_docs", "answer_sources"):
        for doc in result.get(key) or []:
            if isinstance(doc, dict):
                source = doc.get("source")
                if isinstance(source, str) and source:
                    found.add(source)
    return found


@pytest.mark.parametrize("case", CASES, ids=lambda c: str(c["id"]))
def test_outcome_row(case: dict[str, Any], tmp_path: Path) -> None:
    layer = case["layer"]
    if layer == "gate":
        with pytest.raises(PromptInjectionError):
            check_input(str(case["query"]), str(_SHIPPED_CONFIG))
        assert case.get("expected_error_code") == 400
        return

    if layer != "graph":
        raise AssertionError(f"unknown layer {layer!r}")

    mock_key = case["mock_results"]
    if mock_key not in _MOCK_RESULTS:
        raise AssertionError(f"unknown mock_results {mock_key!r}")

    grok_on = bool(case.get("grok_available"))
    cfg = _make_cfg(tmp_path, mode=str(case["mode"]), grok_enabled=grok_on)
    graph = build_graph(
        retriever=MockRetriever(_MOCK_RESULTS[mock_key]),
        llm=MockLocalLLM(),
        grok=MockGrokClient(response="Grok outcome-battery answer.") if grok_on else None,
        cfg=cfg,
    )
    payload: dict[str, Any] = {"query": case["query"]}
    if "user_confirmed_online" in case:
        payload["user_confirmed_online"] = case["user_confirmed_online"]
    result = graph.invoke(payload)

    got_model = result.get("answer_model") or ""
    assert got_model == (case.get("expected_answer_model") or "")
    # route_by_score sets needs_user_confirm on a low top_score and later
    # nodes do not clear it. Only assert the pause (True). An answered
    # low-score path is identified by answer_model, not by this flag.
    if case.get("expected_needs_confirm") is True:
        assert result.get("needs_user_confirm") is True

    sources = _source_names(result)
    for src in case.get("must_retrieve") or []:
        assert src in sources, f"missing source {src!r} in {sorted(sources)}"
    for src in case.get("must_not_retrieve") or []:
        assert src not in sources, f"unexpected source {src!r} in {sorted(sources)}"
