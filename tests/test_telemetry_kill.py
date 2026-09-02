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

# ---------------------------------------------------------------------------
# INDEPENDENT expected maps -- the test-side copy of the contract. Deliberately
# NOT imported or derived from utils.telemetry_kill (most tests below derive,
# which catches enforcement but not a hostile edit to the constants
# themselves): if a production value is deleted or reversed, equality against
# THESE literals fails. Update both sides in the same commit for a deliberate
# change; .claude/skills/otel-hardening/check_otel.py carries the third copy.
# ---------------------------------------------------------------------------
_EXPECTED_KILL = {
    "LANGSMITH_TRACING_V2": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    "LANGCHAIN_TRACING": "false",
    "LANGSMITH_OTEL_ENABLED": "false",
    "LANGGRAPH_CLI_NO_ANALYTICS": "1",
    "NEMO_GUARDRAILS_NO_USAGE_STATS": "1",
    "ANONYMIZED_TELEMETRY": "False",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "ORT_DISABLE_TELEMETRY": "1",
    "ORT_TELEMETRY_OPT_OUT": "1",
    "GH_TELEMETRY": "false",
    "POWERSHELL_TELEMETRY_OPTOUT": "1",
    "CHROMA_OTEL_GRANULARITY": "none",
    "CHROMA_OTEL_COLLECTION_ENDPOINT": "",
    "CHROMA_OTEL_SERVICE_NAME": "",
    "OTEL_SDK_DISABLED": "true",
    "OTEL_TRACES_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
}
_EXPECTED_UPDATE = {
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
    "POWERSHELL_UPDATECHECK": "Off",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
}
_EXPECTED_SCRUBBED = frozenset({
    "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_ENDPOINT",
    "LANGSMITH_ENDPOINT", "LANGSMITH_RUNS_ENDPOINTS",
    "OTEL_CONFIG_FILE", "OTEL_EXPERIMENTAL_CONFIG_FILE",
})


def test_production_maps_match_independent_literals():
    """Deleting or reversing a production value must fail SOMETHING.

    Every other assertion in this file that iterates the production constants
    proves enforcement of whatever they currently say -- this one proves they
    still say the right thing.
    """
    from utils.telemetry_kill import (
        SCRUBBED_ENV_KEYS,
        TELEMETRY_KILL,
        UPDATE_CHECK_OPT_OUT,
    )

    assert TELEMETRY_KILL == _EXPECTED_KILL
    assert UPDATE_CHECK_OPT_OUT == _EXPECTED_UPDATE
    assert set(SCRUBBED_ENV_KEYS) == _EXPECTED_SCRUBBED


# Independent copy of utils.telemetry_kill.CONTRACT_DIGEST. A hostile edit to
# the production pin that leaves the maps themselves untouched must still fail.
# Split so DevSkim DS173237 does not treat the pin as a stored secret.
_EXPECTED_CONTRACT_DIGEST = (
    "583008ec29f72446"
    "a5bc297110d0967d"
    "10a7da23dfa10f20"
    "91cac9c3da4ada8c"
)


def test_contract_digest_matches_independent_literal():
    from utils.telemetry_kill import CONTRACT_DIGEST, contract_digest, verify_telemetry_contract

    assert CONTRACT_DIGEST == _EXPECTED_CONTRACT_DIGEST
    assert contract_digest() == _EXPECTED_CONTRACT_DIGEST
    verify_telemetry_contract()


def test_verify_telemetry_contract_rejects_a_wrong_pin(monkeypatch):
    import utils.telemetry_kill as tk

    monkeypatch.setattr(tk, "CONTRACT_DIGEST", "0" * 64)
    with pytest.raises(RuntimeError, match="contract hash mismatch"):
        tk.verify_telemetry_contract()


def test_build_telemetry_safe_env_pure_and_exact():
    """The child-env builder: copies, overlays, scrubs; never mutates base or
    exposes a mutable canonical global."""
    from utils.telemetry_kill import TELEMETRY_KILL, build_telemetry_safe_env

    base = {
        "PATH": "/usr/bin",
        "GH_TELEMETRY": "log",
        "OTEL_SDK_DISABLED": "false",
        "OTEL_CONFIG_FILE": "/tmp/evil.yaml",
        "LANGSMITH_API_KEY": "leak",
    }
    before = dict(base)
    safe = build_telemetry_safe_env(base)
    assert base == before, "base mapping was mutated"
    assert safe["PATH"] == "/usr/bin", "unrelated base entries must survive"
    for key, value in {**_EXPECTED_KILL, **_EXPECTED_UPDATE}.items():
        assert safe[key] == value, f"{key} not overlaid to canonical value"
    for key in _EXPECTED_SCRUBBED:
        assert key not in safe, f"scrubbed name {key} survived"
    # Fresh dict per call; mutating it cannot reach the canonical constants.
    safe["GH_TELEMETRY"] = "tampered"
    assert build_telemetry_safe_env(base)["GH_TELEMETRY"] == "false"
    assert TELEMETRY_KILL["GH_TELEMETRY"] == "false"


def test_apply_telemetry_kill_returns_copy():
    """The historical aliasing hazard: apply used to return the module global
    itself, so a caller mutation rewrote the contract for the whole process."""
    snippet = (
        "from utils.telemetry_kill import TELEMETRY_KILL, apply_telemetry_kill\n"
        "ret = apply_telemetry_kill()\n"
        "assert ret == TELEMETRY_KILL\n"
        "assert ret is not TELEMETRY_KILL, 'apply must return a copy'\n"
        "ret['GH_TELEMETRY'] = 'tampered'\n"
        "assert TELEMETRY_KILL['GH_TELEMETRY'] == 'false'\n"
    )
    result = _run_in_subprocess(snippet)
    _assert_subprocess_ok(result, "apply_telemetry_kill_returns_copy")


def test_otel_declarative_config_files_scrubbed():
    """OTEL_CONFIG_FILE / OTEL_EXPERIMENTAL_CONFIG_FILE outrank the
    SDK-disable values, so both are removed outright before any SDK import."""
    snippet = (
        "import gate, os\n"
        "assert 'OTEL_CONFIG_FILE' not in os.environ\n"
        "assert 'OTEL_EXPERIMENTAL_CONFIG_FILE' not in os.environ\n"
    )
    result = _run_in_subprocess(
        snippet,
        extra_env={
            "OTEL_CONFIG_FILE": "/tmp/evil-otel-config.yaml",
            "OTEL_EXPERIMENTAL_CONFIG_FILE": "/tmp/evil-otel-config2.yaml",
        },
    )
    _assert_subprocess_ok(result, "otel_declarative_config_files_scrubbed")


def test_update_check_opt_outs_applied():
    """The ancillary map is applied too (visibly separate, jointly delivered)."""
    snippet = (
        "import gate, os\n"
        "assert os.environ['GH_NO_UPDATE_NOTIFIER'] == '1'\n"
        "assert os.environ['GH_NO_EXTENSION_UPDATE_NOTIFIER'] == '1'\n"
        "assert os.environ['POWERSHELL_UPDATECHECK'] == 'Off'\n"
        "assert os.environ['PIP_DISABLE_PIP_VERSION_CHECK'] == '1'\n"
    )
    result = _run_in_subprocess(
        snippet,
        extra_env={"POWERSHELL_UPDATECHECK": "Default", "PIP_DISABLE_PIP_VERSION_CHECK": "0"},
    )
    _assert_subprocess_ok(result, "update_check_opt_outs_applied")


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
    """Every name in the tracing namespace must be 'false' after import.

    All four are one switch, not four: langsmith's get_env_var resolves
    LANGSMITH_TRACING_V2 > LANGCHAIN_TRACING_V2 > LANGSMITH_TRACING >
    LANGCHAIN_TRACING and stops at the first non-empty value, so any single
    unpinned name would win over every pinned one below it.
    """
    snippet = (
        "import gate, os, sys\n"
        "for name in ('LANGSMITH_TRACING_V2', 'LANGCHAIN_TRACING_V2',\n"
        "             'LANGSMITH_TRACING', 'LANGCHAIN_TRACING'):\n"
        "    assert os.environ[name] == 'false', f'{name}={os.environ.get(name)!r}'\n"
        "assert os.environ['LANGGRAPH_CLI_NO_ANALYTICS'] == '1'\n"
    )
    result = _run_in_subprocess(snippet)
    _assert_subprocess_ok(result, "langchain_tracing_disabled")


def test_ambient_langsmith_tracing_v2_cannot_re_enable_upload():
    """The highest-precedence tracing name must not survive a hostile ambient set.

    LANGSMITH_TRACING_V2 is the FIRST name langsmith's tracing_is_enabled()
    consults, and it is the name LangSmith's own docs now recommend -- so it is
    the likeliest stray var to find in an operator's shell profile or a
    container base image. It was also the one name the kill dict originally
    omitted: an ambient 'true' there beat the pinned LANGCHAIN_TRACING_V2
    'false', latched permanently in get_env_var's lru_cache, attached
    LangChainTracer to every graph invocation, and uploaded every run to
    api.smith.langchain.com. No API key was needed -- langsmith's
    missing-key check only warns -- so the credential scrub did not prevent
    egress. This pins the full namespace against exactly that environment.
    """
    hostile = {
        "LANGSMITH_TRACING_V2": "true",
        "LANGCHAIN_TRACING_V2": "true",
        "LANGSMITH_TRACING": "true",
        "LANGCHAIN_TRACING": "true",
    }
    snippet = (
        "import gate, os\n"
        "for name in ('LANGSMITH_TRACING_V2', 'LANGCHAIN_TRACING_V2',\n"
        "             'LANGSMITH_TRACING', 'LANGCHAIN_TRACING'):\n"
        "    assert os.environ[name] == 'false', f'{name}={os.environ.get(name)!r}'\n"
        # The behavioural assertion, not just the env one: ask langsmith itself
        # whether it considers tracing on. Skipped rather than failed when the
        # package is absent, so this stays green in a minimal environment.
        "try:\n"
        "    from langsmith.utils import tracing_is_enabled\n"
        "except ImportError:\n"
        "    pass\n"
        "else:\n"
        "    assert tracing_is_enabled() is False, 'langsmith still reports tracing enabled'\n"
    )
    result = _run_in_subprocess(snippet, extra_env=hostile)
    _assert_subprocess_ok(result, "ambient_langsmith_tracing_v2_cannot_re_enable_upload")


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
    """Pre-seed every name in _TRACING_CREDENTIALS, then importing gate must
    scrub all of them from os.environ.

    Driven off the module's own tuple rather than a hardcoded list, so a name
    added to the scrub set is covered here automatically instead of silently
    going untested (which is how the LANGSMITH_ endpoint twins stayed absent).

    Runs in a subprocess so we never have to reload gate (which would
    re-fire FastAPI app construction and the ChromaDB client init).
    """
    from utils.telemetry_kill import _TRACING_CREDENTIALS

    snippet = (
        "import gate, os\n"
        "from utils.telemetry_kill import _TRACING_CREDENTIALS\n"
        "leaked = [k for k in _TRACING_CREDENTIALS if k in os.environ]\n"
        "assert not leaked, f'tracing credentials survived: {leaked}'\n"
    )
    result = _run_in_subprocess(
        snippet,
        # A non-empty hostile value for every scrubbed name, whatever the tuple
        # currently holds -- a blank would pass a mere `not in os.environ` check
        # for the wrong reason.
        extra_env=dict.fromkeys(_TRACING_CREDENTIALS, "hostile-value-should-be-removed"),
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
    "OTEL_METRICS_EXPORTER": "otlp",
    "OTEL_LOGS_EXPORTER": "otlp",
    # Declarative config outranks the SDK-disable values -- the most dangerous
    # ambient pair after LANGSMITH_TRACING_V2. Must be REMOVED, not out-valued.
    "OTEL_CONFIG_FILE": "/tmp/evil-otel.yaml",
    "OTEL_EXPERIMENTAL_CONFIG_FILE": "/tmp/evil-otel2.yaml",
    "ANONYMIZED_TELEMETRY": "True",
    # Every name in the tracing namespace, not just the two originally listed:
    # LANGSMITH_TRACING_V2 outranks all of them, so an ambient 'true' there is
    # the single most dangerous value this dict can carry.
    "LANGSMITH_TRACING_V2": "true",
    "LANGCHAIN_TRACING_V2": "true",
    "LANGSMITH_TRACING": "true",
    "LANGCHAIN_TRACING": "true",
    "LANGSMITH_OTEL_ENABLED": "true",
    "LANGCHAIN_API_KEY": "leaked-key-should-be-removed",
    "LANGSMITH_API_KEY": "leaked-key-should-be-removed",
    "LANGCHAIN_ENDPOINT": "https://collector.example.invalid",
    "LANGSMITH_ENDPOINT": "https://collector.example.invalid",
    "LANGSMITH_RUNS_ENDPOINTS": '{"https://collector.example.invalid": "k"}',
    "HF_HUB_DISABLE_TELEMETRY": "0",
    "DO_NOT_TRACK": "0",
    "LANGGRAPH_CLI_NO_ANALYTICS": "0",
    "NEMO_GUARDRAILS_NO_USAGE_STATS": "0",
    "ORT_DISABLE_TELEMETRY": "0",
    "ORT_TELEMETRY_OPT_OUT": "0",
    "GH_TELEMETRY": "true",
    "POWERSHELL_TELEMETRY_OPTOUT": "0",
    "GH_NO_UPDATE_NOTIFIER": "0",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "0",
    "POWERSHELL_UPDATECHECK": "Default",
    "PIP_DISABLE_PIP_VERSION_CHECK": "0",
}

# All three halves are derived from the module's own constants rather than a
# hardcoded list, so any name added to any of them is enforced here without a
# matching edit -- the drift that let the LANGSMITH_ tracing/endpoint names go
# unpinned in the first place. (Whether the constants still SAY the right
# thing is test_production_maps_match_independent_literals' job.)
_ASSERT_KILLED = (
    "from utils.telemetry_kill import (\n"
    "    SCRUBBED_ENV_KEYS, TELEMETRY_KILL, UPDATE_CHECK_OPT_OUT,\n"
    ")\n"
    "bad = [f'{k}: expected={v!r} actual={os.environ.get(k)!r}'\n"
    "       for m in (TELEMETRY_KILL, UPDATE_CHECK_OPT_OUT)\n"
    "       for k, v in m.items() if os.environ.get(k) != v]\n"
    "assert not bad, 'kill vars not enforced: ' + '; '.join(bad)\n"
    "leaked = [k for k in SCRUBBED_ENV_KEYS if k in os.environ]\n"
    "assert not leaked, f'scrubbed names survived: {leaked}'\n"
)


@pytest.mark.parametrize(
    "entry_import",
    [
        "import mcp_hybrid_server",
        "import retrieval.indexer",
        "import retrieval.vector_store",
        "import agentic",
        "import guardrails",
        "import harness.server",
        "import metrics",
        "import retrieval.clear_cache",
        # issue #1135 additions: the remaining out-of-band packages, the two
        # console-script CLI modules, the direct guardrails seam, and the
        # retrieval module whose own imports (yaml, rank_bm25) precede its
        # transitive kill. With gate covered by the tests above, every
        # [project.scripts] target module is now subprocess-pinned.
        "import telegram",
        "import opentweet",
        "import sync",
        "import utils.gen_cert",
        "import utils.authn_cli",
        "import guardrails.integration",
        "import retrieval.hybrid_search",
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


def test_hf_hub_and_do_not_track_disabled():
    """HF_HUB_DISABLE_TELEMETRY and DO_NOT_TRACK must be '1' after import.

    Distinct from HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE (which stay conditional,
    see retrieval/embeddings.py): these two only suppress telemetry pings, not
    downloads, so they are unconditional in the shared kill dict.
    """
    snippet = (
        "import gate, os\n"
        "assert os.environ['HF_HUB_DISABLE_TELEMETRY'] == '1'\n"
        "assert os.environ['DO_NOT_TRACK'] == '1'\n"
    )
    result = _run_in_subprocess(snippet, extra_env={"HF_HUB_DISABLE_TELEMETRY": "0", "DO_NOT_TRACK": "0"})
    _assert_subprocess_ok(result, "hf_hub_and_do_not_track_disabled")


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
