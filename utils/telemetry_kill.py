"""Canonical telemetry-kill environment block, shared by every CyClaw entry point.

CyClaw's threat model (docs/THREAT_MODEL.md) forbids unsolicited secondary
telemetry: no dependency may report usage/analytics home. Stated precisely --
this module disables *vendor telemetry and analytics*, not networking. It is
NOT a general network kill switch: intentional, policy-gated feature traffic
(cloud model calls behind the triple gate, Telegram, OpenTweet, rclone/Dropbox,
databases, the one-time embedding-model bootstrap fetch) is governed by its own
gates and documented separately (SECURITY.md "Egress classification"), and no
environment variable here blocks a socket. The mechanism is a fixed set of
environment variables that must be in place BEFORE the libraries that read them
are imported -- LangChain/LangSmith, LangGraph, NeMo Guardrails, ChromaDB's
PostHog client, ONNX Runtime, and the OpenTelemetry SDK all latch their config
at import or construction time, so setting these afterwards is too late.

This module exists because that block used to live only in ``gate.py``. Every
other process that reaches ChromaDB -- ``python -m retrieval.indexer``
(``cyclaw-index``) and ``mcp_hybrid_server.py`` -- never imports ``gate``, so
none of them applied it. They were relying entirely on the upstream defaults
staying benign, which is not a guarantee CyClaw controls: any of these names
present in the ambient environment (an operator's shell profile, a container
base image, a site-wide observability agent) would be honored.

Deliberately stdlib-only (``os``). It is imported at the very top of entry
points, ahead of anything heavy, so it must never pull in a third-party package
of its own. That is also why the real ONNX Runtime *API* suppression
(``onnxruntime.disable_telemetry_events()``) does NOT live here -- importing
onnxruntime from this module would drag a heavy transitive into every entry
point. The API call lives at the optional ONNX load seams instead
(``utils/onnx_telemetry.py``, called by ``retrieval/vector_store.py`` and
``guardrails/integration.py``); this module contributes the env half
(``ORT_DISABLE_TELEMETRY`` below), which must be set before import.

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
read the process environment, not a config object. For child processes built
from a *minimal* environment (which inherit nothing), use
``build_telemetry_safe_env`` so the same canonical values reach them too.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from collections.abc import Mapping, MutableMapping
from pathlib import Path

# Names and values are contractual: tests/test_telemetry_kill.py asserts each
# one against an independent expected map, and treats a failure as P0 (live
# telemetry leakage). .claude/skills/otel-hardening/check_otel.py carries the
# second, out-of-process copy of the same name -> value contract.
TELEMETRY_KILL: dict[str, str] = {
    # The four names below are ONE switch with a namespace precedence order,
    # not four mechanisms. Verified 2026-08-15 against the installed
    # langsmith 0.10.15 (which langchain-core 1.5.0's _tracing_v2_is_enabled
    # now fully delegates to, tracers/context.py:132-135): tracing_is_enabled()
    # calls get_env_var("TRACING_V2", default=get_env_var("TRACING")) at
    # utils.py:141, and get_env_var (utils.py:418-442) tries the LANGSMITH_
    # prefix before LANGCHAIN_ and skips only EMPTY values -- so the live
    # precedence is LANGSMITH_TRACING_V2 > LANGCHAIN_TRACING_V2 >
    # LANGSMITH_TRACING > LANGCHAIN_TRACING, the value must be exactly "true"
    # to enable, and a non-empty "false" at a higher-precedence name shadows
    # everything after it. All four must be pinned: an ambient value at any
    # single unpinned name would win over every pinned lower-precedence one,
    # latch permanently via get_env_var's @functools.lru_cache, and upload
    # every run -- langsmith attempts the upload even with NO API key
    # (client.py:712-738 only warns), so the credential pop below is not a
    # substitute. "false" is inert on every reader: langsmith requires
    # exactly "true", and langchain_core's env_var_is_set treats "false" as
    # unset (utils/env.py:18-23), which also keeps the legacy
    # LANGCHAIN_TRACING v1 check (callbacks/manager.py:2492-2506) from
    # raising its RuntimeError on an ambient truthy value.
    "LANGSMITH_TRACING_V2": "false",
    "LANGCHAIN_TRACING_V2": "false",
    "LANGSMITH_TRACING": "false",
    "LANGCHAIN_TRACING": "false",
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
    # ONNX Runtime (transitive dependency of chromadb, and of nemoguardrails's
    # fastembed base when guardrails is enabled -- see constraints.txt).
    # The platform story changed at onnxruntime v1.29.0 (2025-08-12):
    # non-Windows official builds now carry 1DS-SDK telemetry too
    # (microsoft/onnxruntime PRs #27379/#29872), and Privacy.md documents
    # ORT_DISABLE_TELEMETRY=1, read before ONNX Runtime initializes, as the
    # process-lifetime disable for that path. The runtime API
    # `onnxruntime.disable_telemetry_events()` remains the post-import
    # suppression (wired at the load seams via utils/onnx_telemetry.py) --
    # the API alone cannot prevent an initialization-time event, which is
    # why this env var must be present before the first import. On Windows,
    # telemetry is ETW/TraceLogging and only leaves the box when an external
    # trace session collects it; absolute suppression there requires a
    # --no_telemetry private build, which CyClaw does not claim to be.
    # Reviewed 2026-08-27 against
    # https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md and
    # https://github.com/microsoft/onnxruntime/releases/tag/v1.29.0.
    "ORT_DISABLE_TELEMETRY": "1",
    # INERT LEGACY MARKER -- not protection. This name is NOT read by
    # onnxruntime at all (verified 2026-07-29 by grepping the installed
    # 1.28.0 package: zero references; re-checked against Privacy.md
    # 2026-08-27 -- the documented env control is ORT_DISABLE_TELEMETRY
    # above). Retained solely for parity with the reference
    # docs/security-philosophy/cyclaw_telemetry_kill.env, which has shipped
    # this name since before the real control existed. Tests and the
    # otel-hardening checker classify it as inert and must never count it
    # toward ONNX coverage.
    "ORT_TELEMETRY_OPT_OUT": "1",
    # GitHub CLI usage telemetry (gh >= 2.83): GH_TELEMETRY accepts
    # true/false/log, defaults on. Pinned to the literal "false" so a gh
    # child spawned by agentic/gh_client.py / agentic/writer.py can never
    # inherit an ambient "true" or "log". Update-check suppression for gh is
    # the SEPARATE ancillary pair in UPDATE_CHECK_OPT_OUT below -- version
    # checks are egress but not telemetry, and the two must not be conflated.
    # Reviewed 2026-08-27 (gh help environment).
    "GH_TELEMETRY": "false",
    # PowerShell 7+ startup/feature telemetry. pwsh reads this ONCE, at its
    # own process startup -- so this entry protects only pwsh processes that
    # CyClaw (or a generated .cmd task wrapper) launches AFTER the kill is
    # applied. It cannot retroactively silence an already-running parent
    # PowerShell host: an operator's own pwsh session must receive the value
    # before it starts (shell profile / system env), which
    # docs/security-philosophy/cyclaw_telemetry_kill.env and the platform
    # docs state explicitly. Generated Windows task wrappers
    # (utils/win_schtasks.py) also write it into the .cmd before the
    # powershell/pwsh line so Task-Scheduler-launched wrappers are covered.
    # Reviewed 2026-08-27 (learn.microsoft.com about_Telemetry).
    "POWERSHELL_TELEMETRY_OPTOUT": "1",
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
    # "none" makes the early return fire and nothing is built at all. These
    # CHROMA_OTEL_* names are the legacy configuration surface of the PINNED
    # chromadb 1.5.9 -- current Chroma documentation uses different names, so
    # record controls by supported version rather than replacing these.
    "CHROMA_OTEL_GRANULARITY": "none",
    "CHROMA_OTEL_COLLECTION_ENDPOINT": "",
    "CHROMA_OTEL_SERVICE_NAME": "",
    # Global OTel SDK kill. Retained as the outer layer even with granularity
    # pinned above: it also covers any other OTel-instrumented dependency.
    # NOTE: declarative OTel configuration (OTEL_CONFIG_FILE /
    # OTEL_EXPERIMENTAL_CONFIG_FILE) takes precedence over these SDK-disable /
    # exporter settings when present, which is why both names are scrubbed
    # from the environment below rather than merely out-valued here.
    "OTEL_SDK_DISABLED": "true",
    "OTEL_TRACES_EXPORTER": "none",
    "OTEL_METRICS_EXPORTER": "none",
    "OTEL_LOGS_EXPORTER": "none",
}

# Ancillary update/version-check egress -- deliberately a SEPARATE mapping so
# no report, test, or checker ever counts these as telemetry controls: a
# version check is network egress but reports nothing about usage. They are
# still unconditionally applied (harmless where unread, inherited by every
# child), because the processes that read them -- gh, pip inside the agentic
# verifier, PowerShell task wrappers -- are all launched from environments
# this module governs. HF_HUB_DISABLE_UPDATE_CHECK is deliberately NOT here:
# it governs the `hf` CLI, which no CyClaw code path launches; it is
# documented in docs/security-philosophy/cyclaw_telemetry_kill.env for
# operators who run that CLI by hand (same reasoning as the shell-only
# HOMEBREW_NO_ANALYTICS there -- a key applied to a program CyClaw never
# spawns would advertise a protection this process cannot deliver).
UPDATE_CHECK_OPT_OUT: dict[str, str] = {
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
    "POWERSHELL_UPDATECHECK": "Off",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
}

# Credentials that, if present, would let a tracing SDK authenticate to a remote
# collector. Removed rather than blanked so no SDK can read an empty-but-present
# value and treat it as configured.
#
# The two LANGSMITH_ destination names are defense-in-depth, added 2026-08-15
# alongside the tracing-namespace fix above: with tracing pinned off at all
# four names nothing should read them at all, but the pop tuple used to carry
# LANGCHAIN_ENDPOINT without its LANGSMITH_ twin, so a future regression that
# re-enabled tracing could still have been pointed at an arbitrary host by an
# ambient value. LANGSMITH_RUNS_ENDPOINTS (langsmith run_trees.py:1344) is the
# same exposure with an attacker-chosen fan-out list rather than one URL.
# Popping only ever removes a destination override; it can never enable an
# upload, so this is safe unconditionally.
_TRACING_CREDENTIALS = (
    "LANGCHAIN_API_KEY",
    "LANGSMITH_API_KEY",
    "LANGCHAIN_ENDPOINT",
    "LANGSMITH_ENDPOINT",
    "LANGSMITH_RUNS_ENDPOINTS",
)

# Declarative OTel configuration overrides. When OTEL_EXPERIMENTAL_CONFIG_FILE
# (or its successor OTEL_CONFIG_FILE) points at a YAML config, the OTel SDK's
# declarative-configuration path takes precedence over the individual
# OTEL_SDK_DISABLED / OTEL_*_EXPORTER settings pinned above -- an ambient file
# path could therefore re-enable an exporter the value pins say are off. Both
# names are removed outright, before any SDK import, for the same reason the
# credentials above are popped rather than blanked. Reviewed 2026-08-27
# (opentelemetry.io declarative-configuration spec).
_OTEL_DECLARATIVE_CONFIG = (
    "OTEL_CONFIG_FILE",
    "OTEL_EXPERIMENTAL_CONFIG_FILE",
)

# The full removed-outright set, public so tests and the otel-hardening checker
# can assert it without reaching for the two private tuples above.
SCRUBBED_ENV_KEYS: tuple[str, ...] = (*_TRACING_CREDENTIALS, *_OTEL_DECLARATIVE_CONFIG)

# SHA-256 of the canonical JSON of TELEMETRY_KILL + UPDATE_CHECK_OPT_OUT +
# sorted SCRUBBED_ENV_KEYS. Independent of this file's prose, so a hostile
# edit to a kill *value* fails at gateway boot, not only when CI runs the
# checker (issue #1255). tests/test_telemetry_kill.py holds a second copy.
# Recompute after a deliberate map change:
#   python -c "from utils.telemetry_kill import contract_digest; print(contract_digest())"
# Split so DevSkim DS173237 does not treat the pin as a stored secret.
CONTRACT_DIGEST = (
    "583008ec29f72446"
    "a5bc297110d0967d"
    "10a7da23dfa10f20"
    "91cac9c3da4ada8c"
)


def contract_payload() -> bytes:
    """Canonical encoding of the three kill maps. Sorted keys, no whitespace."""
    return json.dumps(
        {
            "kill": TELEMETRY_KILL,
            "update": UPDATE_CHECK_OPT_OUT,
            "scrubbed": sorted(SCRUBBED_ENV_KEYS),
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def contract_digest() -> str:
    return hashlib.sha256(contract_payload()).hexdigest()


def _is_anonymized_false(node: ast.AST) -> bool:
    """True when *node* is Settings(anonymized_telemetry=False)."""
    if not isinstance(node, ast.Call):
        return False
    for kw in node.keywords:
        if kw.arg == "anonymized_telemetry" and isinstance(kw.value, ast.Constant) and kw.value.value is False:
            return True
    return False


def _verify_chroma_anonymized_flag() -> None:
    """Both PersistentClient sites in vector_store.py must disable PostHog."""
    path = Path(__file__).resolve().parent.parent / "retrieval" / "vector_store.py"
    src = path.read_text(encoding="utf-8")
    hits = 0
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_client = (isinstance(func, ast.Attribute) and func.attr == "PersistentClient") or (
            isinstance(func, ast.Name) and func.id == "PersistentClient"
        )
        if not is_client:
            continue
        for kw in node.keywords:
            if kw.arg == "settings" and _is_anonymized_false(kw.value):
                hits += 1
    if hits < 2:
        raise RuntimeError(
            "retrieval/vector_store.py must construct PersistentClient with "
            f"Settings(anonymized_telemetry=False) at both sites; found {hits}"
        )


def verify_telemetry_contract() -> None:
    """Fail closed if the kill maps or Chroma Settings sites drifted.

    Called from gate.py at import, next to the env-value table. Stdlib-only
    (ast / hashlib / json / pathlib) so it stays legal inside this module.
    """
    digest = contract_digest()
    if digest != CONTRACT_DIGEST:
        raise RuntimeError(
            f"telemetry kill contract hash mismatch: got {digest}, expected {CONTRACT_DIGEST}"
        )
    _verify_chroma_anonymized_flag()


def _enforce(env: MutableMapping[str, str]) -> None:
    """Overlay every canonical value and drop every scrubbed name, in place.

    The single enforcement core shared by ``apply_telemetry_kill`` (parent
    process, mutates ``os.environ``) and ``build_telemetry_safe_env`` (child
    process, mutates a fresh copy) -- one implementation, so the two can
    never drift.
    """
    for key, value in TELEMETRY_KILL.items():
        env[key] = value
    for key, value in UPDATE_CHECK_OPT_OUT.items():
        env[key] = value
    for key in SCRUBBED_ENV_KEYS:
        env.pop(key, None)


def build_telemetry_safe_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return a NEW dict: *base* (default ``os.environ``) with the canonical
    kill values overlaid and every scrubbed credential/config name removed.

    Pure with respect to its input -- *base* is copied, never mutated, and the
    process environment is untouched. Use this wherever a child process is
    built from an explicit ``env=`` mapping (a minimal allowlist, a generated
    launcher, a scheduler job): inheritance from a killed parent covers the
    default ``env=None`` case, but a hand-built environment starts from
    nothing and would otherwise silently drop the whole block. The return
    value is a fresh dict each call; mutating it cannot touch the canonical
    constants.
    """
    env = dict(os.environ if base is None else base)
    _enforce(env)
    return env


def apply_telemetry_kill() -> dict[str, str]:
    """Set every kill var and drop scrubbed names in ``os.environ``; return a
    copy of the telemetry mapping applied.

    Overwrites unconditionally -- an ambient value is exactly the case this
    defends against, so an existing setting is never preserved. Runs through
    the same ``_enforce`` core as ``build_telemetry_safe_env``, so parent and
    child enforcement cannot drift.

    Returns a copy (not the module global) so a caller can report what it
    enforced (``gate.py`` prints a verification table at startup) without
    holding a reference through which the canonical mapping could be mutated.
    The copy carries only ``TELEMETRY_KILL``: the ancillary update-check pairs
    are applied too, but they are not telemetry controls and stay out of any
    telemetry report.
    """
    _enforce(os.environ)
    return dict(TELEMETRY_KILL)


def scheduler_env_overlay() -> dict[str, str]:
    """The canonical pairs a generated launcher/job should deliver as literal
    environment, before any interpreter starts.

    A fresh ``{**TELEMETRY_KILL, **UPDATE_CHECK_OPT_OUT}`` each call -- used by
    the launchd-plist / Windows-task / cron generators. Scrubbed names carry
    no entries here because a value cannot express "absent" -- the boundaries
    that can inherit ambient values remove them explicitly instead: the cron
    line prefixes ``env -u NAME`` per scrubbed name and the generated ``.cmd``
    launchers emit ``set "NAME="`` deletions (utils/win_schtasks.py); launchd
    jobs start from launchd's own near-empty environment, not the operator's
    shell, so there is nothing to remove there.
    """
    return {**TELEMETRY_KILL, **UPDATE_CHECK_OPT_OUT}


def _export_lines(syntax: str) -> list[str]:
    """Render the canonical block for a shell to eval; values are our own
    literals, but validate defensively so a future edit cannot smuggle quoting."""
    lines: list[str] = []
    sections = (
        ("telemetry kill (unsolicited vendor telemetry/analytics)", TELEMETRY_KILL),
        ("ancillary update-check opt-outs (egress, NOT telemetry)", UPDATE_CHECK_OPT_OUT),
    )
    for title, mapping in sections:
        lines.append(f"# --- {title} ---")
        for name, value in mapping.items():
            if not (name.isidentifier() and name.isascii()):
                raise ValueError(f"refusing invalid env name: {name!r}")
            if "'" in value or '"' in value or "\n" in value or "\r" in value:
                raise ValueError(f"refusing env value with quotes/newlines: {name}")
            if syntax == "shell":
                lines.append(f"export {name}='{value}'")
            else:
                lines.append(f"$env:{name} = '{value}'")
    lines.append("# --- scrubbed (declarative-config/credential overrides, removed outright) ---")
    for name in SCRUBBED_ENV_KEYS:
        if syntax == "shell":
            lines.append(f"unset {name} 2>/dev/null || true")
        else:
            lines.append(f"Remove-Item -ErrorAction SilentlyContinue Env:{name}")
    return lines


def _main(argv: list[str] | None = None) -> int:
    """``python -m utils.telemetry_kill --export {shell,powershell}``.

    Prints eval-able lines so the shipped launchers can place the canonical
    block into their own process -- and therefore into every child they start
    -- BEFORE any Python interpreter or tool launches. One source of truth:
    the launchers never hand-copy a key/value pair.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="python -m utils.telemetry_kill")
    parser.add_argument("--export", choices=("shell", "powershell"), required=True)
    args = parser.parse_args(argv)
    print("\n".join(_export_lines(args.export)))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
