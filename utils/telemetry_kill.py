"""Canonical telemetry-kill environment block, shared by every CyClaw entry point.

CyClaw's threat model (docs/THREAT_MODEL.md) forbids telemetry outright: nothing
in this stack may phone home. The mechanism is a fixed set of environment
variables that must be in place BEFORE the libraries that read them are
imported -- LangChain/LangSmith, LangGraph, NeMo Guardrails, ChromaDB's PostHog
client, and the OpenTelemetry SDK all latch their config at import or
construction time, so setting these afterwards is too late.

This module exists because that block used to live only in ``gate.py``. Every
other process that reaches ChromaDB -- ``python -m retrieval.indexer``
(``cyclaw-index``) and ``mcp_hybrid_server.py`` -- never imports ``gate``, so
none of them applied it. They were relying entirely on the upstream defaults
staying benign, which is not a guarantee CyClaw controls: any of these names
present in the ambient environment (an operator's shell profile, a container
base image, a site-wide observability agent) would be honored.

Deliberately stdlib-only (``os``). It is imported at the very top of entry
points, ahead of anything heavy, so it must never pull in a third-party package
of its own.

NOT included here on purpose: ``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE``.
docs/security-philosophy/cyclaw_telemetry_kill.env documents both (for an
operator who wants full manual lockdown), but forcing them on unconditionally
for every process would turn retrieval/embeddings.py's documented cache-miss
bootstrap fetch into a guaranteed failure on any machine that has never run
CyClaw before -- huggingface_hub freezes HF_HUB_OFFLINE at its own import
time, so there is no way to retry past that once set. Those two are instead
applied conditionally, only once the embedding model is confirmed already on
disk, by ``retrieval/embeddings.py::_load_model`` (see
``_model_offline_eligible`` there). Do not "complete" this dict by adding them
here -- that reintroduces the first-run breakage this split was written to
avoid.

``HF_HUB_DISABLE_TELEMETRY`` / ``DO_NOT_TRACK`` are different from the pair
above and ARE included below, unconditionally. Verified 2026-07-29 by reading
huggingface_hub's own ``utils/_telemetry.py``: ``send_telemetry()`` only queues
a background HEAD request to ``{ENDPOINT}/api/telemetry/{topic}`` reporting
library/version metadata -- a separate code path from any file download or
cache lookup. ``HF_HUB_DISABLE_TELEMETRY=1`` is checked directly in that
function and suppresses only that ping; it does not touch
``is_offline_mode()``, so it carries none of ``HF_HUB_OFFLINE``'s first-run
bootstrap risk and is safe to set for every process from the start.
``DO_NOT_TRACK=1`` is confirmed (NVIDIA's own NeMo Guardrails docs) to be an
equivalent opt-out for that library specifically. Verified 2026-08-02 that
huggingface_hub honors it too, resolving the "sources disagree" note this
paragraph used to carry: ``constants.py`` computes
``HF_HUB_DISABLE_TELEMETRY`` as the OR of three env vars --
``HF_HUB_DISABLE_TELEMETRY``, ``DISABLE_TELEMETRY``, and ``DO_NOT_TRACK`` --
so setting any one of them suppresses the ping. The earlier read missed it
because it looked in ``utils/_telemetry.py``, which only consumes the
already-computed constant; the env-var parsing lives in ``constants.py``.
Checked against the pinned huggingface_hub 1.26.0. Keep the explicit HF var
set anyway: it is the vendor's own documented name and does not depend on the
cross-ecosystem convention continuing to be honored.

Applying this is an intentional process-wide side effect: it mutates
``os.environ`` for the whole interpreter. That is the point -- the libraries
read the process environment, not a config object.
"""

from __future__ import annotations

import os

# Names and values are contractual: tests/test_telemetry_kill.py asserts each
# one, and treats a failure as P0 (live telemetry leakage).
TELEMETRY_KILL: dict[str, str] = {
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    # LangSmith's newer OTel-based trace route (langsmith[otel] +
    # LANGSMITH_OTEL_ENABLED=true), separate from the LANGSMITH_TRACING flag
    # above. OTEL_SDK_DISABLED below already neuters the OTel SDK generally,
    # so this is belt-and-suspenders for that specific route, not a distinct
    # mechanism -- kept explicit so a future OTel_SDK_DISABLED removal doesn't
    # silently re-open this one too.
    "LANGSMITH_OTEL_ENABLED": "false",
    "LANGGRAPH_CLI_NO_ANALYTICS": "1",
    "NEMO_GUARDRAILS_NO_USAGE_STATS": "1",
    "ANONYMIZED_TELEMETRY": "False",
    # Suppresses only huggingface_hub's background telemetry HEAD request (see
    # module docstring) -- unlike HF_HUB_OFFLINE, this never blocks a real
    # download or cache-miss fetch, so it is safe unconditionally.
    "HF_HUB_DISABLE_TELEMETRY": "1",
    # Cross-ecosystem opt-out convention; confirmed effective for NeMo
    # Guardrails AND (as of 2026-08-02, read from constants.py in the pinned
    # 1.26.0) for huggingface_hub -- see module docstring. Harmless where
    # unread.
    "DO_NOT_TRACK": "1",
    # ONNX Runtime, a transitive dependency of chromadb (and of nemoguardrails's
    # fastembed base, when guardrails is enabled) -- see constraints.txt. Kept
    # for parity with docs/security-philosophy/cyclaw_telemetry_kill.env, which
    # documents this as one of the vars "gate.py also sets... at import time"
    # (it previously did not). Stated precisely: this specific env var is NOT
    # read by onnxruntime -- verified 2026-07-29 by grepping the installed
    # 1.28.0 package for the name; zero references. ORT's own Privacy.md
    # confirms telemetry is implemented ONLY for official Windows builds (ETW/
    # TraceLogging), off by construction on Linux/macOS, and the real opt-out
    # is the runtime API `onnxruntime.disable_telemetry_events()` -- not an env
    # var. Retained as documented, harmless (unread) belt-and-suspenders rather
    # than silently dropped; wiring the real API call is a separate, deliberate
    # change this module does not make.
    "ORT_TELEMETRY_OPT_OUT": "1",
    # ChromaDB OpenTelemetry. `chroma_otel_granularity` is the actual on/off
    # switch: chromadb's otel_init() returns immediately when it is "none", and
    # only builds a TracerProvider + BatchSpanProcessor + OTLPSpanExporter when
    # it is anything else (chromadb/telemetry/opentelemetry/__init__.py).
    # Blanking the endpoint/service name alone does NOT stop that construction,
    # and note that Settings(anonymized_telemetry=False) governs the separate
    # PostHog product-telemetry path, not this one. Verified 2026-07-29 against
    # chromadb 1.5.9: with granularity left unset and an ambient
    # CHROMA_OTEL_GRANULARITY=all, the OTLP exporter IS constructed and only
    # OTEL_SDK_DISABLED downgrades the tracer to a NoOp; pinning granularity to
    # "none" makes the early return fire and nothing is built at all.
    "CHROMA_OTEL_GRANULARITY": "none",
    "CHROMA_OTEL_COLLECTION_ENDPOINT": "",
    "CHROMA_OTEL_SERVICE_NAME": "",
    # Global OTel SDK kill. Retained as the outer layer even with granularity
    # pinned above: it also covers any other OTel-instrumented dependency.
    "OTEL_SDK_DISABLED": "true",
    "OTEL_TRACES_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
}

# Credentials that, if present, would let a tracing SDK authenticate to a remote
# collector. Removed rather than blanked so no SDK can read an empty-but-present
# value and treat it as configured.
_TRACING_CREDENTIALS = ("LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_ENDPOINT")


def apply_telemetry_kill() -> dict[str, str]:
    """Set every kill var and drop tracing credentials; return the mapping applied.

    Overwrites unconditionally -- an ambient value is exactly the case this
    defends against, so an existing setting is never preserved.

    Returns the mapping so a caller can report what it enforced (``gate.py``
    prints a verification table at startup) without re-importing the constant.
    """
    for key, value in TELEMETRY_KILL.items():
        os.environ[key] = value
    for key in _TRACING_CREDENTIALS:
        os.environ.pop(key, None)
    return TELEMETRY_KILL
