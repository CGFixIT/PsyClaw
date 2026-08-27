"""Spy/fake-module tests for the ONNX Runtime API-half suppression.

The env half (ORT_DISABLE_TELEMETRY, set before import) is pinned by
test_telemetry_kill.py; these tests prove the API half:
``onnxruntime.disable_telemetry_events()`` is actually invoked, is invoked
BEFORE session/collection construction at the load seams, and is a safe no-op
whenever onnxruntime is absent, partially broken, or lacks the API. No test
here imports the real onnxruntime -- every module is a fake injected into
``sys.modules``, so the suite stays deterministic and network-free.
"""

from __future__ import annotations

import ast
import sys
import types
from pathlib import Path

import pytest

from utils.onnx_telemetry import suppress_onnx_telemetry

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _no_real_onnxruntime(monkeypatch):
    """Every test starts with no onnxruntime importable and none imported."""
    monkeypatch.delitem(sys.modules, "onnxruntime", raising=False)


def _fake_ort(record: list[str]) -> types.ModuleType:
    module = types.ModuleType("onnxruntime")
    module.disable_telemetry_events = lambda: record.append("disabled")
    return module


def test_calls_api_when_already_imported(monkeypatch):
    calls: list[str] = []
    monkeypatch.setitem(sys.modules, "onnxruntime", _fake_ort(calls))
    assert suppress_onnx_telemetry() is True
    assert calls == ["disabled"]


def test_default_does_not_force_the_import(monkeypatch):
    """Without force_import, an unimported onnxruntime stays unimported --
    the env var covers a later lazy load, and forcing a heavy dependency at a
    seam that may never construct an ONNX model would cost startup for
    nothing."""
    assert suppress_onnx_telemetry() is False
    assert "onnxruntime" not in sys.modules


def test_force_import_imports_and_calls(monkeypatch):
    calls: list[str] = []
    fake = _fake_ort(calls)

    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "onnxruntime":
            sys.modules["onnxruntime"] = fake
            return fake
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert suppress_onnx_telemetry(force_import=True) is True
    assert calls == ["disabled"]


def test_absent_package_is_safe_under_force_import(monkeypatch):
    """No importable onnxruntime: force_import swallows the ImportError.

    Simulated by failing the import rather than relying on the package being
    absent from site-packages -- environments that install chromadb carry the
    real onnxruntime transitively, and this test must pass in both worlds.
    """
    real_import = __import__

    def fake_import(name, *args, **kwargs):
        if name == "onnxruntime":
            raise ImportError("simulated absent package")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr("builtins.__import__", fake_import)
    assert suppress_onnx_telemetry(force_import=True) is False


def test_api_missing_is_safe(monkeypatch):
    module = types.ModuleType("onnxruntime")
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    assert suppress_onnx_telemetry() is False


def test_api_raising_is_safe(monkeypatch):
    module = types.ModuleType("onnxruntime")

    def boom() -> None:
        raise RuntimeError("native failure")

    module.disable_telemetry_events = boom
    monkeypatch.setitem(sys.modules, "onnxruntime", module)
    assert suppress_onnx_telemetry() is False


def test_idempotent_repeat_calls(monkeypatch):
    """Deliberately NO one-shot latch: each seam call re-disables, so a
    hostile enable_telemetry_events() between seams cannot stick."""
    calls: list[str] = []
    monkeypatch.setitem(sys.modules, "onnxruntime", _fake_ort(calls))
    assert suppress_onnx_telemetry() is True
    assert suppress_onnx_telemetry() is True
    assert calls == ["disabled", "disabled"]


# ---------------------------------------------------------------------------
# Seam ordering: the API must run BEFORE session/collection construction.
# ---------------------------------------------------------------------------


def test_vector_store_disables_before_chroma_client(monkeypatch, tmp_path):
    """Runtime spy through the real _ChromaWriter.reset: with fake chromadb
    and fake onnxruntime installed, the disable call must be recorded before
    PersistentClient construction."""
    order: list[str] = []
    monkeypatch.setitem(sys.modules, "onnxruntime", _fake_ort(order))

    fake_chromadb = types.ModuleType("chromadb")

    class _FakeCollection:
        def add(self, **kwargs):
            pass

    class _FakeClient:
        def __init__(self, path=None, settings=None):
            order.append("client")

        def delete_collection(self, name):
            raise KeyError(name)

        def create_collection(self, name, metadata=None):
            order.append("collection")
            return _FakeCollection()

    fake_chromadb.PersistentClient = _FakeClient
    fake_config = types.ModuleType("chromadb.config")
    fake_config.Settings = lambda **kwargs: None
    fake_chromadb.config = fake_config
    monkeypatch.setitem(sys.modules, "chromadb", fake_chromadb)
    monkeypatch.setitem(sys.modules, "chromadb.config", fake_config)

    from retrieval.vector_store import _ChromaWriter

    writer = _ChromaWriter({
        "indexing": {"chroma_path": str(tmp_path / "chroma"), "collection_name": "t"},
    })
    writer.reset()
    assert order[0] == "disabled", f"disable_telemetry_events must run first, got {order}"
    assert order == ["disabled", "client", "collection"]


def _call_lines(func: ast.FunctionDef, name: str) -> list[int]:
    lines = []
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            f = node.func
            called = f.id if isinstance(f, ast.Name) else getattr(f, "attr", "")
            if called == name:
                lines.append(node.lineno)
    return lines


def test_rails_factory_disables_before_llmrails_construction():
    """Source-order pin for the guardrails seam: inside get_cyclaw_guardrails,
    suppress_onnx_telemetry(force_import=True) precedes the LLMRails(...)
    construction. (Driving the real factory needs a NeMo config tree; the
    chromadb seam above proves the runtime behavior of the shared helper.)"""
    source = (REPO_ROOT / "guardrails" / "integration.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    factory = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "get_cyclaw_guardrails"
    )
    suppress_lines = _call_lines(factory, "suppress_onnx_telemetry")
    rails_lines = _call_lines(factory, "LLMRails")
    assert suppress_lines, "get_cyclaw_guardrails no longer calls suppress_onnx_telemetry"
    assert rails_lines, "get_cyclaw_guardrails no longer constructs LLMRails"
    assert min(suppress_lines) < min(rails_lines), (
        f"suppression (line {min(suppress_lines)}) must precede LLMRails "
        f"construction (line {min(rails_lines)})"
    )
    call = next(
        node for node in ast.walk(factory)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "suppress_onnx_telemetry"
    )
    kw = {k.arg: getattr(k.value, "value", None) for k in call.keywords}
    assert kw.get("force_import") is True, (
        "the guardrails seam must force the import: ONNX use is imminent there"
    )
