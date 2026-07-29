"""Privacy/security regression guards for CyClaw's telemetry kill switch.

CyClaw v1.3.0 sets a fixed set of env vars at the top of gate.py BEFORE any
langchain / chromadb / OpenTelemetry imports run, and hard-removes any
LangChain / LangSmith API keys that might have leaked into the process
environment. A failure in any of these tests means telemetry leakage is live
in production — treat as P0.

The actual env mutations happen as a side effect of importing gate. Because
importing gate at the top of this test module would trigger the full FastAPI
app + ChromaDB client + HybridRetriever init, every test in this file runs
in a fresh Python subprocess. That keeps the tests hermetic and fast (no
test depends on side effects from previous tests), and it lets the
LANGCHAIN_API_KEY hard-remove test exercise a real re-entry into the kill
switch without needing importlib.reload.

Run with:

    pytest tests/test_telemetry_kill.py -v
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest


# Repo root = parent of tests/. We exec gate from there so its sibling
# imports (graph, retrieval, etc.) resolve when the subprocess imports it.
REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_in_subprocess(snippet: str, extra_env: dict | None = None) -> subprocess.CompletedProcess:
    """Run a Python snippet in a fresh subprocess with gate importable.

    The snippet must be self-contained: import os and gate as needed.
    Any AssertionError surfaces as a non-zero exit code.
    """
    env = os.environ.copy()
    # Make sure the repo root is on sys.path so `import gate` works.
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [sys.executable, "-c", snippet],
        env=env,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )


def _assert_subprocess_ok(result: subprocess.CompletedProcess, label: str) -> None:
    """Surface stdout / stderr on failure so debugging is one read away."""
    assert result.returncode == 0, (
        f"{label} subprocess failed (exit={result.returncode})\n"
        f"--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# 1. LangChain / LangSmith tracing disabled
# ---------------------------------------------------------------------------

def test_langchain_tracing_disabled():
    """LANGCHAIN_TRACING_V2 and LANGSMITH_TRACING must be 'false' after import."""
    snippet = (
        "import gate, os, sys\n"
        "assert os.environ['LANGCHAIN_TRACING_V2'] == 'false', "
        "  f\"LANGCHAIN_TRACING_V2={os.environ.get('LANGCHAIN_TRACING_V2')!r}\"\n"
        "assert os.environ['LANGSMITH_TRACING'] == 'false', "
        "  f\"LANGSMITH_TRACING={os.environ.get('LANGSMITH_TRACING')!r}\"\n"
        "assert os.environ['LANGGRAPH_CLI_NO_ANALYTICS'] == '1'\n"
    )
    result = _run_in_subprocess(snippet)
    _assert_subprocess_ok(result, "langchain_tracing_disabled")


# ---------------------------------------------------------------------------
# 2. OpenTelemetry SDK disabled
# ---------------------------------------------------------------------------

def test_otel_sdk_disabled():
    """OTEL_SDK_DISABLED + all three exporters must be set to silence OTel."""
    snippet = (
        "import gate, os\n"
        "assert os.environ['OTEL_SDK_DISABLED'] == 'true'\n"
        "assert os.environ['OTEL_TRACES_EXPORTER'] == 'none'\n"
        "assert os.environ['OTEL_METRICS_EXPORTER'] == 'none'\n"
        "assert os.environ['OTEL_LOGS_EXPORTER'] == 'none'\n"
    )
    result = _run_in_subprocess(snippet)
    _assert_subprocess_ok(result, "otel_sdk_disabled")


# ---------------------------------------------------------------------------
# 3. ChromaDB / PostHog telemetry disabled
# ---------------------------------------------------------------------------

def test_chroma_telemetry_disabled():
    """ANONYMIZED_TELEMETRY=False + Chroma OTel endpoint/service blanked."""
    snippet = (
        "import gate, os\n"
        "assert os.environ['ANONYMIZED_TELEMETRY'] == 'False'\n"
        "assert os.environ['CHROMA_OTEL_COLLECTION_ENDPOINT'] == ''\n"
        "assert os.environ['CHROMA_OTEL_SERVICE_NAME'] == ''\n"
    )
    result = _run_in_subprocess(snippet)
    _assert_subprocess_ok(result, "chroma_telemetry_disabled")


# ---------------------------------------------------------------------------
# 4. NeMo Guardrails telemetry disabled
# ---------------------------------------------------------------------------

def test_nemo_usage_stats_disabled_by_gate():
    """Gateway startup must opt out before an optional NeMo import can occur."""
    snippet = (
        "import gate, os\n"
        "assert os.environ['NEMO_GUARDRAILS_NO_USAGE_STATS'] == '1'\n"
    )
    result = _run_in_subprocess(
        snippet,
        extra_env={"NEMO_GUARDRAILS_NO_USAGE_STATS": "0"},
    )
    _assert_subprocess_ok(result, "nemo_usage_stats_disabled_by_gate")


def test_nemo_usage_stats_disabled_by_standalone_guardrails_package():
    """The standalone guardrails CLI/import path does not pass through gate."""
    snippet = (
        "import guardrails.rails, os\n"
        "assert os.environ['NEMO_GUARDRAILS_NO_USAGE_STATS'] == '1'\n"
    )
    result = _run_in_subprocess(
        snippet,
        extra_env={"NEMO_GUARDRAILS_NO_USAGE_STATS": "0"},
    )
    _assert_subprocess_ok(result, "nemo_usage_stats_disabled_by_standalone_package")


# ---------------------------------------------------------------------------
# 5. API keys hard-removed
# ---------------------------------------------------------------------------

def test_api_keys_hard_removed():
    """Pre-seed LANGCHAIN_API_KEY / LANGSMITH_API_KEY / LANGCHAIN_ENDPOINT,
    then importing gate must scrub all three from os.environ.

    Runs in a subprocess so we never have to reload gate (which would
    re-fire FastAPI app construction and the ChromaDB client init).
    """
    snippet = (
        "import gate, os\n"
        "assert 'LANGCHAIN_API_KEY'  not in os.environ, "
        "  f\"LANGCHAIN_API_KEY still set: {os.environ.get('LANGCHAIN_API_KEY')!r}\"\n"
        "assert 'LANGSMITH_API_KEY'  not in os.environ, "
        "  f\"LANGSMITH_API_KEY still set: {os.environ.get('LANGSMITH_API_KEY')!r}\"\n"
        "assert 'LANGCHAIN_ENDPOINT' not in os.environ, "
        "  f\"LANGCHAIN_ENDPOINT still set: {os.environ.get('LANGCHAIN_ENDPOINT')!r}\"\n"
    )
    result = _run_in_subprocess(
        snippet,
        extra_env={
            "LANGCHAIN_API_KEY": "sk-test123",
            "LANGSMITH_API_KEY": "sk-lssmith",
            "LANGCHAIN_ENDPOINT": "https://evil.example.com",
        },
    )
    _assert_subprocess_ok(result, "api_keys_hard_removed")


# ---------------------------------------------------------------------------
# 6. Every kill-switch key is present with the expected value
# ---------------------------------------------------------------------------

def test_all_kill_keys_present():
    """Regression guard: every key in _TELEMETRY_KILL is set to its declared value.

    If anyone removes an entry from _TELEMETRY_KILL or mutates a value, this
    test fails immediately. The check is intentionally exhaustive — partial
    coverage in tests 1-3 is not enough.
    """
    snippet = (
        "import gate, os\n"
        "from gate import _TELEMETRY_KILL\n"
        "missing = []\n"
        "for key, expected in _TELEMETRY_KILL.items():\n"
        "    actual = os.environ.get(key)\n"
        "    if actual != expected:\n"
        "        missing.append(f'{key}: expected={expected!r} actual={actual!r}')\n"
        "assert not missing, 'Kill-switch keys diverged: ' + '; '.join(missing)\n"
    )
    result = _run_in_subprocess(snippet)
    _assert_subprocess_ok(result, "all_kill_keys_present")


# ---------------------------------------------------------------------------
# 7. The kill switch reaches the entry points that never import gate
# ---------------------------------------------------------------------------
# gate.py is not the only process that loads ChromaDB. `python -m retrieval.indexer`
# (cyclaw-index) and mcp_hybrid_server.py both reach it without importing gate, so
# before utils/telemetry_kill.py existed they applied nothing and inherited whatever
# the ambient environment carried. These tests pin that each entry point enforces the
# block on its own, under an environment that actively tries to enable telemetry.

# Values a hostile / careless ambient environment might carry. CHROMA_OTEL_GRANULARITY
# is the one that matters most: it is ChromaDB's actual on/off switch (otel_init returns
# immediately only when it is "none"), and gate.py's original block never set it.
_HOSTILE_ENV = {
    "CHROMA_OTEL_GRANULARITY": "all",
    "CHROMA_OTEL_COLLECTION_ENDPOINT": "https://collector.example.invalid",
    "CHROMA_OTEL_SERVICE_NAME": "leaky",
    "OTEL_SDK_DISABLED": "false",
    "OTEL_TRACES_EXPORTER": "otlp",
    "ANONYMIZED_TELEMETRY": "True",
    "LANGCHAIN_TRACING_V2": "true",
    "LANGCHAIN_API_KEY": "leaked-key-should-be-removed",
    "LANGSMITH_API_KEY": "leaked-key-should-be-removed",
}

_ASSERT_KILLED = (
    "from utils.telemetry_kill import TELEMETRY_KILL\n"
    "bad = [f'{k}: expected={v!r} actual={os.environ.get(k)!r}'\n"
    "       for k, v in TELEMETRY_KILL.items() if os.environ.get(k) != v]\n"
    "assert not bad, 'kill vars not enforced: ' + '; '.join(bad)\n"
    "leaked = [k for k in ('LANGCHAIN_API_KEY', 'LANGSMITH_API_KEY', 'LANGCHAIN_ENDPOINT')\n"
    "          if k in os.environ]\n"
    "assert not leaked, f'tracing credentials survived: {leaked}'\n"
)


@pytest.mark.parametrize(
    "entry_import",
    [
        "import mcp_hybrid_server",
        "import retrieval.indexer",
        "import retrieval.vector_store",
    ],
)
def test_kill_switch_applied_without_importing_gate(entry_import: str) -> None:
    """Each non-gate entry point enforces the block over a hostile ambient env."""
    snippet = (
        "import os\n"
        f"{entry_import}\n"
        "assert 'gate' not in __import__('sys').modules, 'this path must not import gate'\n"
        + _ASSERT_KILLED
    )
    result = _run_in_subprocess(snippet, extra_env=dict(_HOSTILE_ENV))
    _assert_subprocess_ok(result, f"kill_switch_via_{entry_import}")


def test_chroma_otel_granularity_is_pinned_none() -> None:
    """The switch that actually stops ChromaDB building an OTLP exporter.

    ChromaDB's otel_init() early-returns only on granularity "none"; for any other
    value it constructs a TracerProvider + BatchSpanProcessor + OTLPSpanExporter,
    and only OTEL_SDK_DISABLED then downgrades the tracer to a NoOp. Pinning
    granularity means nothing is constructed at all. Settings(anonymized_telemetry=
    False) does not cover this -- that governs the separate PostHog path.
    """
    from utils.telemetry_kill import TELEMETRY_KILL

    assert TELEMETRY_KILL["CHROMA_OTEL_GRANULARITY"] == "none"


def test_gate_and_shared_module_agree() -> None:
    """gate._TELEMETRY_KILL is the shared mapping, not a drifting copy."""
    snippet = (
        "import os, gate\n"
        "from utils.telemetry_kill import TELEMETRY_KILL\n"
        "assert gate._TELEMETRY_KILL == TELEMETRY_KILL, 'gate copy diverged from shared mapping'\n"
        + _ASSERT_KILLED
    )
    result = _run_in_subprocess(snippet, extra_env=dict(_HOSTILE_ENV))
    _assert_subprocess_ok(result, "gate_and_shared_module_agree")
