#!/usr/bin/env python3
"""check_otel.py - static validation of CyClaw's telemetry-kill contract.

Usage:
    python3 .claude/skills/otel-hardening/check_otel.py [--repo-root PATH]
                                                        [--strict] [--json]
                                                        [--as-of YYYY-MM-DD]

CyClaw's security contract (issue #1135): unsolicited secondary telemetry and
analytics are disabled before every supported process or telemetry-capable
dependency initializes; intentional policy-gated feature traffic is documented
separately and never mislabeled as telemetry; and no environment variable is
sold as a general network kill switch. The runtime half of that contract is
utils/telemetry_kill.py (canonical maps + build_telemetry_safe_env) plus the
process-boundary delivery surfaces (Docker ENV, launchers, generated
plists/tasks/cron). This checker proves the STATIC half:

  * the production constants still carry EXACTLY the expected name -> value
    pairs (an INDEPENDENT oracle below -- never derived from the production
    module, so deleting or reversing a production value fails here);
  * the scrubbed credential/config names are still all removed;
  * the reference .env, the Docker surfaces, and the launchers still deliver
    the same values;
  * no code path programmatically re-enables what the env pins off;
  * every dependency, provider, executable, connector, scheduled job, and
    process launcher carries an explicit egress classification (category 1-5
    below), so a NEW component cannot land unclassified.

It cannot prove a vendor did not change its telemetry contract in a newer
release -- pin drift and stale review dates surface as WARN, which is the
prompt to re-run the live vendor-doc half (SKILL.md Step 2).

Zero third-party imports (tomllib is stdlib on 3.12) and it NEVER imports
utils.telemetry_kill (AST-parse only), so running it has zero os.environ side
effects and works in a fresh clone before any pip install.

Severity:
    FAIL  a kill-switch invariant actually broke (exit 2).
    WARN  re-verification due (pin drift, stale review date, unclassified
          component, unbounded telemetry-capable transitive) -- exit 0;
          --strict escalates every WARN to a failure.
    INFO  advisory; never affects the exit code.

Exit codes (repo convention):
    0  contract holds (warnings may be present without --strict)
    2  a FAIL check tripped (or a WARN under --strict)
    3  env/config error (a required file missing or unparseable)
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import sys
from datetime import date
from importlib import metadata
from pathlib import Path

# ---------------------------------------------------------------------------
# INDEPENDENT ORACLES -- the second copy of the contract. Never import or
# derive these from utils/telemetry_kill.py: the whole point is that a hostile
# or careless edit to the production constants (delete a key, flip
# OTEL_SDK_DISABLED to "false", change exporter "none" -> "otlp", "1" -> "0")
# disagrees with THIS copy and fails T2/T3. Update both sides in the same
# commit for a deliberate change, exactly like dep-guard's baselines.
# ---------------------------------------------------------------------------

EXPECTED_TELEMETRY_KILL: dict[str, str] = {
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

EXPECTED_UPDATE_CHECK: dict[str, str] = {
    "GH_NO_UPDATE_NOTIFIER": "1",
    "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1",
    "POWERSHELL_UPDATECHECK": "Off",
    "PIP_DISABLE_PIP_VERSION_CHECK": "1",
}

# Names REMOVED from the environment outright (popped, never blanked):
# five tracing credentials/destinations + the two declarative-OTel config
# pointers, whose presence outranks the OTEL_SDK_DISABLED/exporter values.
EXPECTED_SCRUBBED: frozenset[str] = frozenset({
    "LANGCHAIN_API_KEY", "LANGSMITH_API_KEY", "LANGCHAIN_ENDPOINT",
    "LANGSMITH_ENDPOINT", "LANGSMITH_RUNS_ENDPOINTS",
    "OTEL_CONFIG_FILE", "OTEL_EXPERIMENTAL_CONFIG_FILE",
})

# Present in the kill dict for reference-.env parity but READ BY NOTHING --
# never count these toward vendor coverage. (ORT's real controls are
# ORT_DISABLE_TELEMETRY + the runtime API in utils/onnx_telemetry.py.)
INERT_LEGACY_MARKERS: frozenset[str] = frozenset({"ORT_TELEMETRY_OPT_OUT"})

# Documented in cyclaw_telemetry_kill.env but deliberately NOT applied by
# Python, for two different reasons that must not be conflated:
#   conditional -- CyClaw sets these itself, only after confirming the
#   embedding model is already cached (retrieval/embeddings.py).
#   shell-only -- they govern programs CyClaw never launches (brew, the hf
#   CLI); an unread key in the Python dict advertises protection the process
#   cannot deliver (the ORT_TELEMETRY_OPT_OUT lesson).
CONDITIONAL_OFFLINE_PAIR: frozenset[str] = frozenset({"HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"})
SHELL_ONLY_ENV_KEYS: frozenset[str] = frozenset({"HOMEBREW_NO_ANALYTICS", "HF_HUB_DISABLE_UPDATE_CHECK"})

# Vendor pins whose telemetry surface the maps target, and the version last
# verified against that vendor's actual source (not just docs). A drifted pin
# WARNs: not a proven leak, but the prompt to re-run the live-search half.
LAST_VERIFIED_VENDOR_PINS = {
    "chromadb": "1.5.9",
    "langchain": "1.3.14",
    "langchain-core": "1.5.0",
    "langgraph": "1.2.9",
    # 0.23.0 -> 0.24.0 re-verified 2026-08-27 against the installed 0.24.0
    # source: nemoguardrails/telemetry.py:372-374 still honors
    # NEMO_GUARDRAILS_NO_USAGE_STATS and DO_NOT_TRACK (1/true); the usage
    # stats sink is https://events.telemetry.data.nvidia.com/v1.1/events/json.
    "nemoguardrails": "0.24.0",
    "sentence-transformers": "5.6.0",
    # Not a pyproject direct pin -- lives in constraints.txt (deepagents
    # transitive). Tracked here because the whole 4-name LANGSMITH_/LANGCHAIN_
    # tracing block defends against exactly this package; verified 2026-08-15
    # (precedence in utils.py:141/get_env_var) and re-confirmed 2026-08-27.
    "langsmith": "0.10.15",
}

# Transitive-only vendors (no direct pyproject pin). Best-effort INFO context
# for the live-search step; a fresh clone legitimately has none installed.
TRANSITIVE_VENDORS_TO_REPORT = ("huggingface_hub", "onnxruntime", "opentelemetry-sdk", "fastembed", "transformers")

# Telemetry-capable transitives that arrive with NO version bound anywhere in
# the manifests -- always a WARN-class review finding (issue #1135): an
# unbounded vendor can change its telemetry contract under CyClaw silently.
UNBOUNDED_TELEMETRY_CAPABLE = ("onnxruntime", "fastembed", "transformers")

# ORT_DISABLE_TELEMETRY only governs onnxruntime's non-Windows 1DS path from
# this release onward (v1.29.0, PRs #27379/#29872). A resolve below it leaves
# the env half of the ONNX control inert on macOS and Linux, so a bound that
# merely exists is not enough -- it has to clear this floor.
ORT_TELEMETRY_ENV_FLOOR = (1, 29, 0)

STALE_AFTER_DAYS = 120

_VERIFIED_DATE_RE = re.compile(r"[Vv]erified\s+(\d{4}-\d{2}-\d{2})")

# ---------------------------------------------------------------------------
# Egress classification inventory (issue #1135 workstream 5). Every component
# that can touch a network -- dependency, provider, executable, connector,
# scheduled job, launcher -- carries exactly one category:
#   1  unsolicited telemetry/analytics WITH an official control (the control
#      pairs must exist in the oracles above -- never invent one);
#   2  ancillary update/version-check egress (NOT telemetry);
#   3  intentional, policy-gated functional egress (controls stay EMPTY here:
#      the gate is CyClaw policy, not an env var, and listing one would
#      mislabel feature traffic as telemetry);
#   4  local-only observability/storage (no network sink);
#   5  absent / no mechanism found (controls stay EMPTY; evidence records the
#      negative finding and its date).
# `reviewed` is the date the evidence was last checked; T4 flags stale rows.
# ---------------------------------------------------------------------------

INVENTORY: tuple[dict[str, object], ...] = (
    {
        "name": "chromadb", "category": 1,
        "controls": {"ANONYMIZED_TELEMETRY": "False", "CHROMA_OTEL_GRANULARITY": "none",
                     "CHROMA_OTEL_COLLECTION_ENDPOINT": "", "CHROMA_OTEL_SERVICE_NAME": ""},
        "url": "https://docs.trychroma.com/docs/overview/telemetry",
        "versions": "==1.5.9 (pinned; the CHROMA_OTEL_* names are this version's "
                    "legacy surface -- current Chroma docs use different names; record by version)",
        "enforcement": "env before import (PostHog + OTel latch at import/construction); "
                       "Settings(anonymized_telemetry=False) hardcoded at both PersistentClient sites",
        "scope": "gate / mcp / indexer / clear_cache via retrieval/vector_store.py chokepoint",
        "reviewed": "2026-08-27",
        "evidence": "otel_init() early-return on granularity none confirmed in installed 1.5.9 "
                    "(telemetry/opentelemetry/__init__.py); OTEL_SDK_DISABLED is the outer layer",
    },
    {
        "name": "langchain/langsmith tracing", "category": 1,
        "controls": {"LANGSMITH_TRACING_V2": "false", "LANGCHAIN_TRACING_V2": "false",
                     "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING": "false",
                     "LANGSMITH_OTEL_ENABLED": "false"},
        "url": "https://docs.smith.langchain.com/observability/how_to_guides/trace_with_langchain",
        "versions": "langchain==1.3.14, langchain-core==1.5.0, langgraph==1.2.9, langsmith==0.10.15",
        "enforcement": "env before import (get_env_var lru_cache latches); 5 credential/destination "
                       "names popped; upload attempted even with no API key, so the pop alone is not enough",
        "scope": "every graph invocation",
        "reviewed": "2026-08-27",
        "evidence": "precedence LANGSMITH_TRACING_V2 > LANGCHAIN_TRACING_V2 > LANGSMITH_TRACING > "
                    "LANGCHAIN_TRACING confirmed in installed langsmith 0.10.15 utils.py:141",
    },
    {
        "name": "langgraph-cli analytics", "category": 1,
        "controls": {"LANGGRAPH_CLI_NO_ANALYTICS": "1"},
        "url": "https://github.com/langchain-ai/langgraph/tree/main/libs/cli",
        "versions": "langgraph==1.2.9 family", "enforcement": "env before any CLI use",
        "scope": "dev tooling only (no runtime path invokes the CLI)",
        "reviewed": "2026-08-15", "evidence": "belt-and-suspenders; runtime langgraph carries no own telemetry",
    },
    {
        "name": "huggingface-hub telemetry ping", "category": 1,
        "controls": {"HF_HUB_DISABLE_TELEMETRY": "1", "DO_NOT_TRACK": "1"},
        "url": "https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables",
        "versions": "==1.26.0",
        "enforcement": "env before import; constants.py ORs HF_HUB_DISABLE_TELEMETRY / DISABLE_TELEMETRY / "
                       "DO_NOT_TRACK. Distinct from the CONDITIONAL offline pair (first-run bootstrap fetch "
                       "is category 3, below)",
        "scope": "embeddings model handling", "reviewed": "2026-08-02",
        "evidence": "send_telemetry() HEAD request path read in pinned 1.26.0",
    },
    {
        "name": "nemoguardrails usage stats", "category": 1,
        "controls": {"NEMO_GUARDRAILS_NO_USAGE_STATS": "1", "DO_NOT_TRACK": "1"},
        "url": "https://github.com/NVIDIA-NeMo/Guardrails",
        "versions": "==0.24.0 (guardrails extra; off by default)",
        "enforcement": "env before import (guardrails/__init__.py and guardrails/integration.py both apply "
                       "ahead of the soft nemoguardrails import)",
        "scope": "optional guardrails engine", "reviewed": "2026-08-27",
        "evidence": "telemetry.py:372-374 in installed 0.24.0 honors both names; sink is "
                    "events.telemetry.data.nvidia.com",
    },
    {
        "name": "onnxruntime", "category": 1,
        "controls": {"ORT_DISABLE_TELEMETRY": "1"},
        "url": "https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md",
        "versions": "transitive (chromadb, which asks only for >=1.14.1; fastembed under the "
                    "guardrails extra) -- bounded at ==1.29.0 in constraints.txt so the env "
                    "control cannot be resolved below the release that introduced it",
        "enforcement": "env before import (process-lifetime control for the non-Windows 1DS path added in "
                       "v1.29.0, PRs #27379/#29872) + onnxruntime.disable_telemetry_events() at the load "
                       "seams (utils/onnx_telemetry.py) before session construction. Windows is "
                       "ETW/TraceLogging: collected only by an external trace session; the API cannot undo "
                       "an init-time event, and absolute suppression needs a --no_telemetry private build. "
                       "ORT_TELEMETRY_OPT_OUT is an inert legacy marker, not protection.",
        "scope": "chromadb default-EF path (never invoked -- precomputed vectors) + fastembed under live NeMo",
        "reviewed": "2026-08-27",
        "evidence": "disable_telemetry_events present in installed 1.29.0; v1.29.0 release notes + Privacy.md",
    },
    {
        "name": "opentelemetry sdk", "category": 1,
        "controls": {"OTEL_SDK_DISABLED": "true", "OTEL_TRACES_EXPORTER": "none",
                     "OTEL_METRICS_EXPORTER": "none", "OTEL_LOGS_EXPORTER": "none"},
        "url": "https://opentelemetry.io/docs/languages/sdk-configuration/general/",
        "versions": "transitive (chromadb); environment.yml floors opentelemetry-exporter-otlp-proto-grpc>=1.42",
        "enforcement": "env before import; OTEL_CONFIG_FILE / OTEL_EXPERIMENTAL_CONFIG_FILE are REMOVED "
                       "outright because declarative configuration outranks these values",
        "scope": "any OTel-instrumented dependency", "reviewed": "2026-08-27",
        "evidence": "declarative-config precedence per the OTel spec; scrub covers it",
    },
    {
        "name": "github cli (gh)", "category": 1,
        "controls": {"GH_TELEMETRY": "false"},
        "url": "https://cli.github.com/manual/gh_help_environment",
        "versions": "external binary >= 2.40 (agentic floor); telemetry shipped in 2.83",
        "enforcement": "env forced at all three spawn sites (agentic/gh_client.py x2, agentic/writer.py) via "
                       "build_telemetry_safe_env -- an ambient true/log can never reach a child",
        "scope": "agentic read/write ops", "reviewed": "2026-08-27",
        "evidence": "gh help environment documents GH_TELEMETRY true/false/log",
    },
    {
        "name": "powershell host telemetry", "category": 1,
        "controls": {"POWERSHELL_TELEMETRY_OPTOUT": "1"},
        "url": "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_telemetry",
        "versions": "pwsh 7+ (powershell.exe 5.1 sends none)",
        "enforcement": "read ONCE at pwsh startup: the parent must carry it first. Delivered by the installed "
                       "cmd shim + profile function (before powershell launches) and by generated task .cmd "
                       "set-lines; setting it inside a running host cannot undo that host's startup event",
        "scope": "CredMan task wrappers; operator launchers", "reviewed": "2026-08-27",
        "evidence": "about_Telemetry documents startup-read semantics",
    },
    {
        "name": "gh update notifiers", "category": 2,
        "controls": {"GH_NO_UPDATE_NOTIFIER": "1", "GH_NO_EXTENSION_UPDATE_NOTIFIER": "1"},
        "url": "https://cli.github.com/manual/gh_help_environment",
        "versions": "external binary", "enforcement": "same env delivery as GH_TELEMETRY",
        "scope": "agentic gh children", "reviewed": "2026-08-27",
        "evidence": "version-check egress to the release endpoint; reports nothing about usage",
    },
    {
        "name": "powershell update check", "category": 2,
        "controls": {"POWERSHELL_UPDATECHECK": "Off"},
        "url": "https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_update_notifications",
        "versions": "pwsh 7+", "enforcement": "same boundaries as POWERSHELL_TELEMETRY_OPTOUT",
        "scope": "task wrappers / launchers", "reviewed": "2026-08-27", "evidence": "documented notification check",
    },
    {
        "name": "pip version check", "category": 2,
        "controls": {"PIP_DISABLE_PIP_VERSION_CHECK": "1"},
        "url": "https://pip.pypa.io/en/stable/cli/pip/#cmdoption-disable-pip-version-check",
        "versions": "any", "enforcement": "canonical env (executor children also set PIP_NO_INDEX)",
        "scope": "agentic verifier children", "reviewed": "2026-08-27", "evidence": "documented pip option",
    },
    {
        "name": "hf cli update check", "category": 2,
        "controls": {},
        "url": "https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables",
        "versions": "hf CLI (never spawned by CyClaw)",
        "enforcement": "shell-only: HF_HUB_DISABLE_UPDATE_CHECK=1 documented in the reference .env for "
                       "operators who run the CLI by hand; a Python-side key would sit unread",
        "scope": "operator shell only", "reviewed": "2026-08-27",
        "evidence": "no CyClaw code path launches the hf CLI (repo grep)",
    },
    {
        "name": "homebrew analytics", "category": 2,
        "controls": {},
        "url": "https://docs.brew.sh/Analytics",
        "versions": "external binary",
        "enforcement": "shell-only: macos/setup-from-clone.sh exports HOMEBREW_NO_ANALYTICS=1 and runs "
                       "`brew analytics off` before its own brew calls; no other CyClaw code spawns brew",
        "scope": "macOS setup script + operator shell", "reviewed": "2026-08-27",
        "evidence": "setup-from-clone.sh:303-312 is the only brew invocation site",
    },
    {
        "name": "grok/claude cloud fallback", "category": 3, "controls": {},
        "url": "docs/THREAT_MODEL.md",
        "versions": "grok-4.5 / claude-sonnet-5 endpoints",
        "enforcement": "triple gate (mode==hybrid AND provider.enabled AND per-request user_confirmed_online); "
                       "never mislabel as telemetry, never block with the kill map",
        "scope": "graph user-gate path only", "reviewed": "2026-08-27",
        "evidence": "I3; llm/client.py; trust_env=False on httpx clients",
    },
    {
        "name": "cloud planner adapters (deepagents + langchain-openai/anthropic/google-genai/xai)",
        "category": 3, "controls": {},
        "url": "docs/agentic/AGENTIC_README.md",
        "versions": "extras: langchain-openai==1.3.5, langchain-anthropic==1.4.8, langchain-xai==1.2.2, "
                    "langchain-google-genai (constraints)",
        "enforcement": "out-of-band, --provider/--confirm-online gated; tracing killed by the same env block",
        "scope": "agentic planner only", "reviewed": "2026-08-27",
        "evidence": "policy-gated feature traffic, not telemetry",
    },
    {
        "name": "telegram channel", "category": 3, "controls": {},
        "url": "docs/channels/TELEGRAM_DESIGN.md",
        "versions": "first-party httpx client (no vendor SDK; no SDK telemetry key exists)",
        "enforcement": "shipped enabled:false; loopback POST /query only; T3 consent for online",
        "scope": "out-of-band channel", "reviewed": "2026-08-27",
        "evidence": "telegram/ imports httpx directly; intentional remote API operations",
    },
    {
        "name": "opentweet channel", "category": 3, "controls": {},
        "url": "docs/channels/OPENTWEET_DESIGN.md",
        "versions": "first-party httpx client (no vendor SDK; no SDK telemetry key exists)",
        "enforcement": "shipped enabled:false; generate-don't-load scheduling; public-write human gate",
        "scope": "out-of-band channel", "reviewed": "2026-08-27",
        "evidence": "opentweet/ imports httpx directly; intentional remote API operations",
    },
    {
        "name": "rclone/dropbox sync", "category": 3, "controls": {},
        "url": "https://rclone.org/docs/",
        "versions": "external rclone binary",
        "enforcement": "out-of-band python -m sync.cli; the corpus mirror IS the feature traffic",
        "scope": "sync/ only", "reviewed": "2026-08-27",
        "evidence": "intentional data-plane traffic; scheduler now delivers the canonical env regardless",
    },
    {
        "name": "sql connectors (pgvector/psycopg/pyodbc)", "category": 3, "controls": {},
        "url": "docs/agentic/AGENTIC_README.md",
        "versions": "pgvector/psycopg pinned; pyodbc extra",
        "enforcement": "operator-configured database endpoints; SELECT/WITH-only guard on sqlconnect",
        "scope": "vector-store backend + out-of-band sqlconnect", "reviewed": "2026-08-27",
        "evidence": "database traffic is the feature; no vendor telemetry mechanism in these drivers",
    },
    {
        "name": "harness /web fetch", "category": 3, "controls": {},
        "url": "harness/README.md",
        "versions": "allowlist-only, off by default",
        "enforcement": "config allowlist; loopback console", "scope": "harness only",
        "reviewed": "2026-08-27", "evidence": "operator-directed fetches, not telemetry",
    },
    {
        "name": "hf model bootstrap fetch", "category": 3, "controls": {},
        "url": "https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables",
        "versions": "sentence-transformers==5.6.0 / huggingface-hub==1.26.0",
        "enforcement": "one-time cache-miss download; HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE stay CONDITIONAL "
                       "(set only once the model is confirmed cached -- retrieval/embeddings.py; the real "
                       "in-process gate is local_files_only). Never made unconditional",
        "scope": "first run only", "reviewed": "2026-08-27",
        "evidence": "functional egress, not telemetry; the telemetry ping is the separate category-1 row",
    },
    {
        "name": "ollama daemon", "category": 4, "controls": {},
        "url": "https://docs.ollama.com",
        "versions": "external daemon on 127.0.0.1:11434",
        "enforcement": "CyClaw's own traffic is loopback inference only. The daemon's cloud/web features are "
                       "DAEMON policy CyClaw cannot set: local-only mode requires OLLAMA_NO_CLOUD=1 (or "
                       "disable_ollama_cloud) in the independently-running daemon's environment, then a "
                       "daemon restart -- document, don't claim",
        "scope": "local LLM path", "reviewed": "2026-08-27",
        "evidence": "no CyClaw-side switch exists for a foreign daemon; noted in SECURITY.md classification",
    },
    {
        "name": "lm studio", "category": 5, "controls": {},
        "url": "https://lmstudio.ai/docs",
        "versions": "external app (optional local provider)",
        "enforcement": "operationally separate; NO documented telemetry environment switch was found -- its "
                       "updater/model-catalog/cloud operations remain app-level policy the operator controls "
                       "in the app itself. Do not invent a control",
        "scope": "optional local inference", "reviewed": "2026-08-27",
        "evidence": "negative finding recorded per issue #1135",
    },
    {
        "name": "numbat projection (+ cel-python)", "category": 4, "controls": {},
        "url": "docs/security-philosophy/numbat_secondary_evaluator.md",
        "versions": "numbat CLI 0.2.0 (CI only); cel-python==0.5.0 optional extra, default-off",
        "enforcement": "local NDJSON file only (logs/numbat-events.ndjsonl); numbat.enabled: false disables. "
                       "Every event carries hostname/username/uid endpoint metadata -- a second sensitive "
                       "LOCAL log, not a privacy improvement. No runtime HTTP sink exists or is implicitly "
                       "configured. NEVER added to the env kill map",
        "scope": "every audit record when enabled (ships enabled: true)", "reviewed": "2026-08-27",
        "evidence": "build_endpoint() in utils/numbat_emitter.py; file sink only",
    },
    {
        "name": "netconnect passive LAN inventory", "category": 4, "controls": {},
        "url": "docs/THREAT_MODEL.md",
        "versions": "agentic/netconnect/ (stdlib-only, no new dependency)",
        "enforcement": "reads the local host + the OS's EXISTING neighbor cache only, inside explicit "
                       "RFC1918/loopback allowed_cidrs; allowed_net_ops ships (self, arp) -- no ping, port "
                       "probe, subnet sweep, or packet send exists in the code. Ships enabled: false and "
                       "refuses to run with an empty allowed_cidrs",
        "scope": "out-of-band python -m agentic.netconnect.cli only (I6: never imported by core)",
        "reviewed": "2026-09-02",
        "evidence": "scope.py CIDR gate + client.py neighbor-cache read; zero socket sends in the package",
    },
    {
        "name": "falco rules", "category": 4, "controls": {},
        "url": "docs/plans/NUMBAT_AND_ALWAYS_ON_ROADMAP.md",
        "versions": "detection-only, default-off deploy asset",
        "enforcement": "local detection; no sink configured by CyClaw", "scope": "optional deploy",
        "reviewed": "2026-08-27", "evidence": "no egress mechanism in-repo",
    },
    {
        "name": "vendored unslop scanners", "category": 5, "controls": {},
        "url": "agentic/vendor",
        "versions": "vendored", "enforcement": "offline stdlib by construction", "scope": "agentic checks",
        "reviewed": "2026-08-27", "evidence": "no network imports (vendored source)",
    },
    {
        "name": "fastembed", "category": 5, "controls": {},
        "url": "https://github.com/qdrant/fastembed",
        "versions": "transitive of nemoguardrails (guardrails extra) -- UNBOUNDED, see T13 warning",
        "enforcement": "no telemetry mechanism found; its documented first-use remote-CDN model fetch is "
                       "functional egress under the guardrails opt-in, not telemetry. Do not invent a control",
        "scope": "live NeMo only", "reviewed": "2026-08-27",
        "evidence": "negative finding; ONNX sessions it builds are covered by the onnxruntime row",
    },
    {
        "name": "uv", "category": 5, "controls": {},
        "url": "https://docs.astral.sh/uv/",
        "versions": "Docker build stage only (pinned image digest)",
        "enforcement": "no telemetry/analytics mechanism documented or found; package resolution egress is "
                       "the install feature itself", "scope": "image build",
        "reviewed": "2026-08-27", "evidence": "negative finding recorded",
    },
    {
        "name": "git", "category": 5, "controls": {},
        "url": "agentic/deepagent_github/repo_workspace.py",
        "versions": "external binary",
        "enforcement": "local-only subcommands under _GIT_ENV_ALLOWLIST; reads none of the canonical names, "
                       "so the overlay is deliberately NOT applied there (documented in-module)",
        "scope": "repo workspace ops", "reviewed": "2026-08-27",
        "evidence": "documented exclusion, not an oversight",
    },
    {
        "name": "sentence-transformers/transformers/torch stack", "category": 5, "controls": {},
        "url": "https://huggingface.co/docs/huggingface_hub/package_reference/environment_variables",
        "versions": "sentence-transformers==5.6.0; torch==2.13.0+cpu; transformers unbounded transitive",
        "enforcement": "no own telemetry mechanism; all hub traffic flows through huggingface-hub (its "
                       "category-1 ping row + category-3 bootstrap row)", "scope": "embeddings",
        "reviewed": "2026-08-27", "evidence": "negative finding; torch OSS wheels carry no telemetry",
    },
    {
        "name": "core web/runtime libs", "category": 5, "controls": {},
        "url": "pyproject.toml",
        "versions": "fastapi/starlette/uvicorn/httpx/pydantic/numpy/nltk/pyyaml/rank-bm25/pygments/"
                    "websockets/tzdata/psycopg-binary/cel-python and the dev/test tools "
                    "(pytest*/ruff/mypy/bandit/pip/python)",
        "enforcement": "no telemetry/analytics mechanism in any of them; httpx is a transport whose egress "
                       "is caller policy (all CyClaw clients set trust_env=False); nltk data downloads are "
                       "avoided by design (local Porter stemmer, no punkt)",
        "scope": "runtime + dev", "reviewed": "2026-08-27",
        "evidence": "bulk negative classification -- a NEW dependency outside every row here trips T13",
    },
)

# Maps a normalized dependency/executable name to its INVENTORY row. Every
# name any manifest declares MUST resolve here (or be an exact row name);
# a new unmapped component is the strict-mode finding the issue demands.
INVENTORY_ALIASES: dict[str, str] = {
    "chromadb": "chromadb",
    "langchain": "langchain/langsmith tracing",
    "langchain-core": "langchain/langsmith tracing",
    "langgraph": "langchain/langsmith tracing",
    "langsmith": "langchain/langsmith tracing",
    "huggingface-hub": "huggingface-hub telemetry ping",
    "nemoguardrails": "nemoguardrails usage stats",
    "onnxruntime": "onnxruntime",
    "opentelemetry-exporter-otlp-proto-grpc": "opentelemetry sdk",
    "deepagents": "cloud planner adapters (deepagents + langchain-openai/anthropic/google-genai/xai)",
    "langchain-openai": "cloud planner adapters (deepagents + langchain-openai/anthropic/google-genai/xai)",
    "langchain-anthropic": "cloud planner adapters (deepagents + langchain-openai/anthropic/google-genai/xai)",
    "langchain-google-genai": "cloud planner adapters (deepagents + langchain-openai/anthropic/google-genai/xai)",
    "langchain-xai": "cloud planner adapters (deepagents + langchain-openai/anthropic/google-genai/xai)",
    "pgvector": "sql connectors (pgvector/psycopg/pyodbc)",
    "psycopg": "sql connectors (pgvector/psycopg/pyodbc)",
    "psycopg-binary": "sql connectors (pgvector/psycopg/pyodbc)",
    "pyodbc": "sql connectors (pgvector/psycopg/pyodbc)",
    "sentence-transformers": "sentence-transformers/transformers/torch stack",
    "transformers": "sentence-transformers/transformers/torch stack",
    "torch": "sentence-transformers/transformers/torch stack",
    # conda-forge name for the same torch (environment.yml pin) -- same row
    "pytorch": "sentence-transformers/transformers/torch stack",
    "fastembed": "fastembed",
    "gh": "github cli (gh)",
    "rclone": "rclone/dropbox sync",
    "powershell": "powershell host telemetry",
    "pwsh": "powershell host telemetry",
    "brew": "homebrew analytics",
    "uv": "uv",
    "git": "git",
    "ollama": "ollama daemon",
    "openssl": "core web/runtime libs",
    "cel-python": "numbat projection (+ cel-python)",
    "netconnect": "netconnect passive LAN inventory",
    # bulk category-5 members
    "fastapi": "core web/runtime libs", "starlette": "core web/runtime libs",
    "uvicorn": "core web/runtime libs", "httpx": "core web/runtime libs",
    "pydantic": "core web/runtime libs", "numpy": "core web/runtime libs",
    "nltk": "core web/runtime libs", "pyyaml": "core web/runtime libs",
    "rank-bm25": "core web/runtime libs", "pygments": "core web/runtime libs",
    "websockets": "core web/runtime libs", "tzdata": "core web/runtime libs",
    "python-tzdata": "core web/runtime libs", "pytest": "core web/runtime libs",
    "pytest-asyncio": "core web/runtime libs", "pytest-cov": "core web/runtime libs",
    "ruff": "core web/runtime libs", "mypy": "core web/runtime libs",
    "bandit": "core web/runtime libs", "pip": "core web/runtime libs",
    "python": "core web/runtime libs",
    "pydantic-core": "core web/runtime libs",
    "pydantic-settings": "core web/runtime libs",
    "wcmatch": "core web/runtime libs",
}

# External executables/services CyClaw spawns or fronts -- swept by T13 along
# with the manifests so a NEW launcher/binary needs a classification too.
KNOWN_EXTERNAL_COMPONENTS = ("gh", "rclone", "powershell", "brew", "uv", "git", "ollama", "openssl")

_fails: list[dict[str, str]] = []
_warns: list[dict[str, str]] = []


def fail(check: str, detail: str) -> None:
    _fails.append({"check": check, "detail": detail})
    print(f"  FAIL  [{check}] {detail}")


def warn(check: str, detail: str) -> None:
    _warns.append({"check": check, "detail": detail})
    print(f"  WARN  [{check}] {detail}")


def ok(check: str, detail: str) -> None:
    print(f"  ok    [{check}] {detail}")


def info(check: str, detail: str) -> None:
    print(f"  info  [{check}] {detail}")


def _assign_targets(node: ast.stmt) -> list[ast.expr]:
    """Uniform target list for both ``x = ...`` and annotated ``x: T = ...``."""
    if isinstance(node, ast.Assign):
        return node.targets
    if isinstance(node, ast.AnnAssign) and node.value is not None:
        return [node.target]
    return []


def _load_literal(path: Path, name: str):
    """AST-parse one module-level literal without importing the module.

    Never imports utils.telemetry_kill (whose import mutates os.environ);
    handles both plain and annotated assignments.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            for tgt in _assign_targets(node):
                if isinstance(tgt, ast.Name) and tgt.id == name:
                    return ast.literal_eval(node.value)
    raise ValueError(f"{name} assignment not found in {path}")


def _diff_mapping(check: str, label: str, actual: dict, expected: dict) -> bool:
    """FAIL on any missing / extra / value-mismatched pair; True when exact."""
    problems: list[str] = []
    for key, want in expected.items():
        if key not in actual:
            problems.append(f"missing {key}")
        elif actual[key] != want:
            problems.append(f"{key}: expected {want!r}, production has {actual[key]!r}")
    for key in actual.keys() - expected.keys():
        problems.append(f"unexpected extra key {key} (classify it here AND in the inventory)")
    if problems:
        fail(check, f"{label} disagrees with the independent oracle -- " + "; ".join(sorted(problems)))
        return False
    return True


def check_shapes(kill, update, creds, otel_cfg) -> None:
    bad = []
    if not isinstance(kill, dict) or not kill or any(not isinstance(v, str) for v in kill.values()):
        bad.append("TELEMETRY_KILL must be a non-empty dict[str, str]")
    if not isinstance(update, dict) or any(not isinstance(v, str) for v in update.values()):
        bad.append("UPDATE_CHECK_OPT_OUT must be a dict[str, str]")
    if not isinstance(creds, tuple) or not isinstance(otel_cfg, tuple):
        bad.append("_TRACING_CREDENTIALS / _OTEL_DECLARATIVE_CONFIG must be tuples")
    if bad:
        fail("T1", "; ".join(bad))
    else:
        ok("T1", f"production structures parse ({len(kill)} telemetry, {len(update)} update-check, "
                 f"{len(creds) + len(otel_cfg)} scrubbed)")


def check_value_oracle(kill: dict[str, str], update: dict[str, str]) -> None:
    good = _diff_mapping("T2", "TELEMETRY_KILL", kill, EXPECTED_TELEMETRY_KILL)
    good &= _diff_mapping("T2", "UPDATE_CHECK_OPT_OUT", update, EXPECTED_UPDATE_CHECK)
    if good:
        ok("T2", f"every name -> value pair matches the independent oracle "
                 f"({len(EXPECTED_TELEMETRY_KILL)} + {len(EXPECTED_UPDATE_CHECK)} pairs; "
                 f"{sorted(INERT_LEGACY_MARKERS)} counted as inert markers, not protection)")


def check_scrub_oracle(creds: tuple[str, ...], otel_cfg: tuple[str, ...]) -> None:
    actual = set(creds) | set(otel_cfg)
    missing = EXPECTED_SCRUBBED - actual
    extra = actual - EXPECTED_SCRUBBED
    if missing or extra:
        fail("T3", f"scrub set disagrees with oracle -- missing: {sorted(missing)}; extra: {sorted(extra)}")
        return
    declarative = {"OTEL_CONFIG_FILE", "OTEL_EXPERIMENTAL_CONFIG_FILE"}
    if not declarative <= set(otel_cfg):
        fail("T3", "the two declarative-OTel config names must live in _OTEL_DECLARATIVE_CONFIG "
                   f"(found {sorted(otel_cfg)}) -- they outrank the SDK-disable values")
        return
    ok("T3", f"all {len(EXPECTED_SCRUBBED)} scrubbed names present, declarative-config pair included")


def check_staleness(source: str, today: date) -> None:
    dates = sorted({date.fromisoformat(d) for d in _VERIFIED_DATE_RE.findall(source)})
    if not dates:
        warn("T4", "no 'Verified YYYY-MM-DD' stamp found in utils/telemetry_kill.py")
    else:
        oldest = dates[0]
        age = (today - oldest).days
        if age > STALE_AFTER_DAYS:
            warn("T4", f"oldest 'Verified' stamp {oldest.isoformat()} is {age}d old "
                       f"(threshold {STALE_AFTER_DAYS}d) -- re-run the live vendor sweep")
        else:
            ok("T4", f"oldest 'Verified' stamp {oldest.isoformat()} ({age}d old, within {STALE_AFTER_DAYS}d)")
    stale_rows = []
    for row in INVENTORY:
        reviewed = date.fromisoformat(str(row["reviewed"]))
        if (today - reviewed).days > STALE_AFTER_DAYS:
            stale_rows.append(f"{row['name']} ({row['reviewed']})")
    if stale_rows:
        warn("T4", f"inventory row(s) past the {STALE_AFTER_DAYS}d review window: {', '.join(stale_rows)}")
    else:
        ok("T4", f"all {len(INVENTORY)} inventory rows reviewed within {STALE_AFTER_DAYS}d")


def _parse_requirement_names(lines: list[str]) -> dict[str, str]:
    req_re = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(?:\[[^\]]*\])?\s*==\s*([^\s;,#]+)")
    out: dict[str, str] = {}
    for line in lines:
        m = req_re.match(line)
        if m:
            out[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    return out


def _load_pyproject_deps(pyproject_path: Path) -> dict[str, str]:
    import tomllib

    with pyproject_path.open("rb") as fh:
        data = tomllib.load(fh)
    groups: list[list[str]] = [data.get("project", {}).get("dependencies", [])]
    groups.extend(data.get("project", {}).get("optional-dependencies", {}).values())
    return _parse_requirement_names([e for g in groups for e in g])


def check_vendor_pin_drift(root: Path) -> None:
    pyproject = root / "pyproject.toml"
    constraints = root / "constraints.txt"
    if not pyproject.exists():
        warn("T5", f"{pyproject} not found -- skipped")
        return
    try:
        current = _load_pyproject_deps(pyproject)
    except Exception as exc:  # noqa: BLE001 -- report, don't crash the checker
        warn("T5", f"could not parse {pyproject}: {exc}")
        return
    if constraints.exists():
        # constraints-only pins (e.g. langsmith, a deepagents transitive) are
        # part of the verified surface too -- pyproject alone missed the one
        # package the whole tracing block defends against.
        for name, ver in _parse_requirement_names(constraints.read_text(encoding="utf-8").splitlines()).items():
            current.setdefault(name, ver)
    drifted = []
    for vendor, baseline in LAST_VERIFIED_VENDOR_PINS.items():
        pin = current.get(vendor)
        if pin is None:
            info("T5", f"{vendor} not pinned in pyproject/constraints (transitive or removed) -- skipped")
        elif pin != baseline:
            drifted.append(f"{vendor}: verified {baseline} -> now pinned {pin}")
    if drifted:
        for d in drifted:
            warn("T5", f"vendor pin drifted since last verification -- {d}; re-check its telemetry docs")
    else:
        ok("T5", f"all {len(LAST_VERIFIED_VENDOR_PINS)} verified vendors unchanged")


def report_transitive_vendor_versions() -> None:
    for name in TRANSITIVE_VENDORS_TO_REPORT:
        try:
            version = metadata.version(name)
        except metadata.PackageNotFoundError:
            info("T6", f"{name} not installed here -- skipped (normal in a fresh clone)")
            continue
        info("T6", f"{name} installed version: {version} -- cross-check against the last live-search notes")


def check_embeddings_conditional_wiring(embeddings_path: Path) -> None:
    if not embeddings_path.exists():
        fail("T7", f"{embeddings_path} not found")
        return
    source = embeddings_path.read_text(encoding="utf-8")
    needed = ("_model_offline_eligible", "HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE", "try_to_load_from_cache")
    missing = [n for n in needed if n not in source]
    if missing:
        fail("T7", f"retrieval/embeddings.py lost conditional-offline symbol(s): {missing}")
        return
    ok("T7", "retrieval/embeddings.py still wires the conditional HF Hub offline check")


_EXPORT_LINE_RE = re.compile(r"^export ([A-Z][A-Z0-9_]*)=(.*)$")
_SECTION_MARKERS = ("--- 1. Unconditional telemetry kill",
                    "--- 2. Ancillary update-check opt-outs",
                    "--- 3. Conditional strict-offline")


def check_reference_env_file(env_path: Path) -> None:
    """Format + value contract for the reference .env -- FAIL class, not WARN.

    Every non-comment line must be exactly ``export NAME=value`` (no bare
    KEY=value -- a sourced bare assignment never reaches a child process);
    duplicates and malformed lines are rejected; the three section markers
    must exist; and every documented value must match the oracles exactly.
    """
    if not env_path.exists():
        fail("T8", f"{env_path} not found")
        return
    text = env_path.read_text(encoding="utf-8")
    for marker in _SECTION_MARKERS:
        if marker not in text:
            fail("T8", f"section marker missing from {env_path.name}: {marker!r}")
            return
    documented: dict[str, str] = {}
    problems: list[str] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _EXPORT_LINE_RE.match(line)
        if not m:
            problems.append(f"line {lineno} is not `export NAME=value`: {raw!r}")
            continue
        name, value = m.group(1), m.group(2)
        if name in documented:
            problems.append(f"duplicate export of {name} (line {lineno})")
            continue
        documented[name] = value
    if problems:
        fail("T8", f"{env_path.name} format violations -- " + "; ".join(problems))
        return
    # The shell-only names come from their named set above -- the set IS the
    # contract for section 2's tail, not documentation beside it. Both
    # shell-only names are "1"-valued by their vendors' conventions. The
    # conditional strict-offline pair is different: it must appear COMMENTED
    # ("# export NAME=1"), never active -- `source` cannot pick sections, so
    # an active pair would break a fresh install's one-time bootstrap
    # download (Codex P2).
    expected_values = {
        **EXPECTED_TELEMETRY_KILL,
        **EXPECTED_UPDATE_CHECK,
        **dict.fromkeys(sorted(SHELL_ONLY_ENV_KEYS), "1"),
    }
    for name in sorted(CONDITIONAL_OFFLINE_PAIR):
        if f"# export {name}=1" not in text:
            fail("T8", f"{env_path.name} must ship the strict-offline pair commented "
                       f"('# export {name}=1'); an active export breaks first-run bootstrap")
            return
        if name in documented:
            fail("T8", f"{env_path.name} exports {name} actively -- the strict-offline "
                       "pair must stay commented (source cannot pick sections)")
            return
    mismatched = [f"{k}: doc has {documented[k]!r}, oracle says {v!r}"
                  for k, v in expected_values.items() if k in documented and documented[k] != v]
    missing = sorted(set(expected_values) - set(documented))
    extra = sorted(set(documented) - set(expected_values))
    if mismatched or missing or extra:
        fail("T8", f"{env_path.name} drifted -- missing: {missing}; extra: {extra}; " + "; ".join(mismatched))
        return
    ok("T8", f"{env_path.name}: export-format, no duplicates, all {len(expected_values)} values exact")


def check_helper_wiring(kill_source: str) -> None:
    """The pure builder exists and both enforcement paths share one core."""
    tree = ast.parse(kill_source)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    missing = [n for n in ("build_telemetry_safe_env", "apply_telemetry_kill",
                           "scheduler_env_overlay", "_enforce") if n not in funcs]
    if missing:
        fail("T9", f"utils/telemetry_kill.py is missing function(s): {missing}")
        return
    def calls_enforce(fn: ast.FunctionDef) -> bool:
        return any(isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "_enforce"
                   for n in ast.walk(fn))
    drifting = [n for n in ("build_telemetry_safe_env", "apply_telemetry_kill")
                if not calls_enforce(funcs[n])]
    if drifting:
        fail("T9", f"parent/child enforcement can drift: {drifting} no longer call the shared _enforce core")
        return
    ok("T9", "build_telemetry_safe_env + apply_telemetry_kill share the _enforce core; "
             "scheduler_env_overlay present")


def _parse_dockerfile_env(text: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    # join line continuations, then pull KEY=value / KEY="value" tokens from ENV lines
    joined = text.replace("\\\n", " ")
    for line in joined.splitlines():
        line = line.strip()
        if not line.startswith("ENV "):
            continue
        for m in re.finditer(r'([A-Za-z_][A-Za-z0-9_]*)=("(?:[^"]*)"|\S*)', line[4:]):
            value = m.group(2)
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            pairs[m.group(1)] = value
    return pairs


def _parse_compose_environment(text: str) -> dict[str, str]:
    pairs: dict[str, str] = {}
    in_env = False
    base_indent = None
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("environment:"):
            in_env = True
            base_indent = len(raw) - len(raw.lstrip())
            continue
        if in_env:
            indent = len(raw) - len(raw.lstrip())
            if stripped and indent <= (base_indent or 0):
                in_env = False
                continue
            if stripped.startswith("- ") and "=" in stripped:
                name, _, value = stripped[2:].partition("=")
                pairs[name.strip()] = value
    return pairs


def check_docker_delivery(root: Path) -> None:
    for label, path, parser in (("Dockerfile", root / "Dockerfile", _parse_dockerfile_env),
                                ("docker-compose.yml", root / "docker-compose.yml", _parse_compose_environment)):
        if not path.exists():
            warn("T10", f"{label} not found -- skipped")
            continue
        pairs = parser(path.read_text(encoding="utf-8"))
        if "CYCLAW_TELEMETRY_KILL" in pairs:
            fail("T10", f"{label} still sets CYCLAW_TELEMETRY_KILL -- read by no code, "
                        "removed by issue #1135; the real canonical values replace it")
            continue
        wrong = [f"{k}: {label} has {pairs.get(k)!r}, oracle says {v!r}"
                 for k, v in {**EXPECTED_TELEMETRY_KILL, **EXPECTED_UPDATE_CHECK}.items()
                 if pairs.get(k) != v]
        if wrong:
            fail("T10", f"{label} does not deliver the canonical env -- " + "; ".join(sorted(wrong)[:6])
                 + (f" (+{len(wrong) - 6} more)" if len(wrong) > 6 else ""))
        else:
            ok("T10", f"{label} delivers every canonical pair before interpreter start")


def check_launcher_delivery(root: Path) -> None:
    # The -S -E flags are part of the contract: the helper interpreter must
    # not run site init (a venv sitecustomize/.pth hook would fire before the
    # module emits the safe values) and must ignore ambient PYTHONPATH.
    expectations = (
        ("macos/invoke-cyclaw.sh", ("-S -E -m utils.telemetry_kill --export shell",)),
        ("powershell/Invoke-CyClaw.ps1", ("-S -E -m utils.telemetry_kill --export powershell",)),
        ("powershell/Install-CyClaw.ps1", ('set "POWERSHELL_TELEMETRY_OPTOUT=1"',
                                           'set "POWERSHELL_UPDATECHECK=Off"')),
    )
    all_ok = True
    for rel, needles in expectations:
        path = root / rel
        if not path.exists():
            warn("T11", f"{rel} not found -- skipped")
            all_ok = False
            continue
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in text:
                fail("T11", f"{rel} no longer carries {needle!r} -- launcher env delivery regressed")
                all_ok = False
    if all_ok:
        ok("T11", "launchers export the canonical block; cmd shim sets the pwsh opt-outs pre-launch")


# Source patterns that programmatically re-enable what the env pins off.
# Swept over first-party runtime code only -- tests, docs, skills, and the
# vendored tree legitimately mention these names.
_BYPASS_PATTERNS = (
    "enable_telemetry_events",
    "set_tracer_provider(",
    "OTLPSpanExporter(",
    "TracerProvider(",
    "LangChainTracer(",
)
_BYPASS_SKIP_PARTS = ("tests", ".claude", ".codex", "docs", "vendor", "__pycache__")
# Files that must keep referencing the safe-env builders -- the "subprocess
# env= that drops the canonical map" half of the sweep, expressed positively
# so it cannot false-positive on unrelated env= usage.
_MUST_REFERENCE = (
    ("agentic/executor/runner.py", "build_telemetry_safe_env"),
    ("agentic/gh_client.py", "build_telemetry_safe_env"),
    ("agentic/writer.py", "build_telemetry_safe_env"),
    ("sync/cli.py", "build_telemetry_safe_env"),
    ("sync/scheduler.py", "scheduler_env_overlay"),
    ("telegram/cli.py", "scheduler_env_overlay"),
    ("opentweet/cli.py", "scheduler_env_overlay"),
    ("agentic/fsconnect/cli.py", "scheduler_env_overlay"),
    ("macos/generate_service_plist.py", "scheduler_env_overlay"),
    ("windows/generate_service_task.py", "scheduler_env_overlay"),
)


def check_programmatic_bypasses(root: Path) -> None:
    hits: list[str] = []
    for path in sorted(root.rglob("*.py")):
        rel = path.relative_to(root)
        if any(part in _BYPASS_SKIP_PARTS for part in rel.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        for pattern in _BYPASS_PATTERNS:
            for lineno, line in enumerate(text.splitlines(), start=1):
                stripped = line.strip()
                if pattern in stripped and not stripped.startswith("#"):
                    hits.append(f"{rel}:{lineno} {pattern}")
    if hits:
        fail("T12", "programmatic telemetry re-enable found in runtime code: " + "; ".join(hits))
    else:
        ok("T12", f"no programmatic bypass patterns in runtime code ({len(_BYPASS_PATTERNS)} patterns swept)")
    dropped = []
    for rel, symbol in _MUST_REFERENCE:
        path = root / rel
        if path.exists() and symbol not in path.read_text(encoding="utf-8"):
            dropped.append(f"{rel} no longer references {symbol}")
    if dropped:
        fail("T12", "a child-env builder dropped the canonical map: " + "; ".join(dropped))
    else:
        ok("T12", f"all {len(_MUST_REFERENCE)} child-env builders still route through the canonical helpers")


def _parse_environment_yml_names(path: Path) -> set[str]:
    names: set[str] = set()
    if not path.exists():
        return names
    req_re = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
    for raw in path.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s.startswith("- ") and not s.startswith("- pip:") and "=" in s:
            # channel entries ("- conda-forge") carry no version spec -- skip
            m = req_re.match(s[2:])
            if m:
                names.add(re.sub(r"[-_.]+", "-", m.group(1)).lower())
    return names


def check_classification_inventory(root: Path, strict: bool) -> None:
    # -- schema validation -------------------------------------------------
    schema_problems: list[str] = []
    known_controls = {**EXPECTED_TELEMETRY_KILL, **EXPECTED_UPDATE_CHECK}
    names = set()
    for row in INVENTORY:
        name = str(row.get("name"))
        names.add(name)
        cat = row.get("category")
        if cat not in (1, 2, 3, 4, 5):
            schema_problems.append(f"{name}: category must be 1-5, got {cat!r}")
            continue
        controls = row.get("controls")
        if not isinstance(controls, dict):
            schema_problems.append(f"{name}: controls must be a dict")
            continue
        if cat in (3, 4, 5) and controls:
            schema_problems.append(f"{name}: category-{cat} rows must not invent controls, got {sorted(controls)}")
        if cat in (1, 2):
            for key, value in controls.items():
                if key in INERT_LEGACY_MARKERS:
                    schema_problems.append(f"{name}: {key} is an inert legacy marker and cannot "
                                           "be counted as a control")
                elif known_controls.get(key) != value:
                    schema_problems.append(f"{name}: control {key}={value!r} is not in the canonical oracles")
        for field in ("url", "versions", "enforcement", "scope", "reviewed", "evidence"):
            if not str(row.get(field, "")).strip():
                schema_problems.append(f"{name}: missing field {field}")
    # category-2 rows with EMPTY controls are the documented shell-only pair;
    # they must say so in their enforcement text.
    for row in INVENTORY:
        if row["category"] == 2 and not row["controls"] and "shell-only" not in str(row["enforcement"]):
            schema_problems.append(f"{row['name']}: control-less category-2 row must document shell-only delivery")
    if schema_problems:
        fail("T13", "inventory schema violations -- " + "; ".join(schema_problems))
    else:
        by_cat = {c: sum(1 for r in INVENTORY if r["category"] == c) for c in (1, 2, 3, 4, 5)}
        ok("T13", f"inventory schema valid: {len(INVENTORY)} rows "
                  f"(cat1={by_cat[1]} cat2={by_cat[2]} cat3={by_cat[3]} cat4={by_cat[4]} cat5={by_cat[5]})")

    # -- surface sweep: every declared component must resolve to a row -----
    components: set[str] = set()
    try:
        components |= set(_load_pyproject_deps(root / "pyproject.toml"))
    except Exception as exc:  # noqa: BLE001
        warn("T13", f"could not parse pyproject.toml for the sweep: {exc}")
    for manifest in ("requirements.txt", "constraints.txt"):
        path = root / manifest
        if path.exists():
            components |= set(_parse_requirement_names(path.read_text(encoding="utf-8").splitlines()))
    components |= _parse_environment_yml_names(root / "environment.yml")
    components |= set(KNOWN_EXTERNAL_COMPONENTS)
    components.discard("cyclaw")
    unclassified = sorted(
        c for c in components
        if c not in INVENTORY_ALIASES and INVENTORY_ALIASES.get(c) not in names and c not in names
    )
    if unclassified:
        msg = (f"unclassified component(s) -- every dependency/executable needs an egress category: "
               f"{unclassified}")
        if strict:
            fail("T13", msg)
        else:
            warn("T13", msg)
    else:
        ok("T13", f"all {len(components)} declared components resolve to a classification row")
    for name in UNBOUNDED_TELEMETRY_CAPABLE:
        # Recorded review findings (issue #1135): bounding these is a
        # dependency decision, not a checker fix, so an unbound one is
        # reported every run rather than failing it. Consulting the manifests
        # instead of asserting the list is what keeps this honest in both
        # directions -- a name that gains a bound stops being reported, and
        # one that loses it starts again. A NEW unclassified name still trips
        # the sweep above.
        if name == "onnxruntime":
            _check_ort_floor(root)
            continue
        _report_transitive_bound(name, _pins_by_surface(root, name))


# Install surfaces with independent resolvers. A pin on one says nothing about
# the others, so every pin question is asked per surface. REQUIRED surfaces are
# the ones CyClaw ships and CI exercises; conda is tracked but its gap is a
# standing acknowledgement rather than a per-run failure.
_PIP_SURFACE = "pip (requirements/constraints)"
_WHEEL_SURFACE = "wheel / `pip install -e .` (pyproject.toml metadata)"
_CONDA_SURFACE = "conda (environment.yml)"
_REQUIRED_PIN_SURFACES = (_PIP_SURFACE, _WHEEL_SURFACE)


def _unconditional_pin(entries: list[str], name: str) -> str | None:
    """Exact pin for ``name`` in ``entries``, or None if absent OR marker-scoped.

    The single place that answers "does this entry actually bind the version
    everywhere we care about?". It exists because the marker hole was fixed for
    pyproject.toml first and left open on the pip manifests -- constraints and
    requirements files use requirement-specifier syntax too, so they carry
    markers just the same. Two copies of this rule meant only one of them got
    fixed.

    ``_parse_requirement_names`` stops the version at the ``;`` and discards
    the rest, so ``onnxruntime==1.29.0; sys_platform == 'win32'`` looks global
    while pip skips it entirely on macOS and Linux -- the platforms the ONNX
    floor exists to protect. Markers are not evaluated (this checker is
    deliberately stdlib-only and runs before install); any marker at all
    disqualifies the pin as unconditional coverage.
    """
    for entry in entries:
        if not isinstance(entry, str):
            continue
        head, _, marker = entry.partition(";")
        found = _parse_requirement_names([head]).get(name)
        if found is None:
            continue
        return None if marker.strip() else found
    return None


def _base_dependency_pin(pyproject: Path, name: str) -> str | None:
    """Exact, UNCONDITIONAL pin for ``name`` in ``project.dependencies``.

    Two things a naive lookup gets wrong, both of which read as coverage the
    default wheel install does not have:

    * Optional-dependency groups are not installed by a bare ``pip install``,
      so a pin that lives only in an extra is not wheel coverage.
    * An environment marker scopes the pin. ``_parse_requirement_names`` stops
      the version at the ``;`` and discards the rest, so
      ``onnxruntime==1.29.0; sys_platform == 'win32'`` looked global -- while
      macOS and Linux, the platforms this floor exists to protect, would get
      only chromadb's ``>=1.14.1``. Markers are not evaluated here (this
      checker is deliberately stdlib-only and runs before install); any marker
      at all disqualifies the pin as unconditional coverage.
    """
    try:
        import tomllib

        with pyproject.open("rb") as fh:
            data = tomllib.load(fh)
    except Exception:  # noqa: BLE001 - an unparseable manifest is not a bound
        return None
    entries = data.get("project", {}).get("dependencies", [])
    if not isinstance(entries, list):
        return None
    return _unconditional_pin(entries, name)


def _pins_by_surface(root: Path, name: str) -> dict[str, str | None]:
    """Exact pinned version of ``name`` on each install surface, independently.

    Returning the first match found anywhere is what the ONNX path was split
    up to avoid: a pin on one surface then vouches for surfaces its resolver
    never touches. Membership is not a bound either -- the conda parser admits
    any line containing an "=", so only a version anchored on a digit counts.
    """
    pip_pin: str | None = None
    for manifest in ("constraints.txt", "requirements.txt"):
        path = root / manifest
        if path.exists() and pip_pin is None:
            # Same rule as the wheel surface: constraints and requirements
            # files use requirement-specifier syntax, so a marker-scoped pin
            # here binds nothing on the platforms this floor protects.
            pip_pin = _unconditional_pin(path.read_text(encoding="utf-8").splitlines(), name)

    wheel_pin: str | None = None
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        # BASE dependencies only. _load_pyproject_deps merges project.dependencies
        # with every optional-dependencies group, so a pin living only in an extra
        # (say `guardrails`) read as wheel coverage -- but a default wheel install
        # selects no extras, leaving chromadb's loose >=1.14.1 authoritative.
        wheel_pin = _base_dependency_pin(pyproject, name)

    conda_pin: str | None = None
    env_path = root / "environment.yml"
    if env_path.exists():
        match = re.search(
            rf"^\s*-\s*{re.escape(name)}\s*=\s*([0-9][^\s#]*)",
            env_path.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        conda_pin = match.group(1) if match else None

    return {_PIP_SURFACE: pip_pin, _WHEEL_SURFACE: wheel_pin, _CONDA_SURFACE: conda_pin}


def _ort_floor_verdict(surface: str, pinned: str | None) -> None:
    """Report one install surface against the ONNX telemetry-control floor.

    A missing pin on a REQUIRED surface fails: this floor is a security
    control, and reporting its removal as info() left the checker silent while
    both shipped surfaces could resolve below it. The conda gap stays info --
    it is acknowledged and unbounded upstream, not a regression to catch.
    """
    floor = ".".join(str(part) for part in ORT_TELEMETRY_ENV_FLOOR)
    if pinned is None:
        message = (f"{surface}: no onnxruntime floor -- chromadb asks only for >=1.14.1, so this "
                   f"surface can resolve below {floor} where ORT_DISABLE_TELEMETRY does not yet "
                   "govern the non-Windows 1DS path")
        if surface in _REQUIRED_PIN_SURFACES:
            fail("T13", message)
        else:
            info("T13", message)
        return
    try:
        parts = [int(part) for part in pinned.split(".")[:3]]
        # Pad to three components: a two-part pin like "1.29" would otherwise
        # compare as (1, 29) < (1, 29, 0) and fail a version that clears the floor.
        parsed = tuple(parts + [0] * (3 - len(parts)))
    except ValueError:
        warn("T13", f"{surface}: onnxruntime pin {pinned!r} is not a parseable version")
        return
    if parsed < ORT_TELEMETRY_ENV_FLOOR:
        fail("T13", f"{surface}: onnxruntime pinned {pinned}, below the {floor} floor where "
                    "ORT_DISABLE_TELEMETRY starts governing the non-Windows 1DS path -- "
                    "the env half of the ONNX control is inert on macOS/Linux at this pin")
        return
    ok("T13", f"{surface}: onnxruntime pinned {pinned} (>= {floor})")


def _check_ort_floor(root: Path) -> None:
    """Verify the ONNX floor on EACH install surface independently."""
    for surface, pinned in _pins_by_surface(root, "onnxruntime").items():
        _ort_floor_verdict(surface, pinned)


def _report_transitive_bound(name: str, pins: dict[str, str | None]) -> None:
    """Report a telemetry-capable transitive's bound, per surface.

    A pin on one surface must not read as coverage everywhere: naming the
    surfaces that remain unbounded is the difference between "pinned" and
    "pinned where it happens to be looked for".
    """
    unbounded = [surface for surface, pinned in pins.items() if pinned is None]
    if len(unbounded) == len(pins):
        info("T13", f"telemetry-capable transitive {name!r} has no version bound in any manifest -- "
                    "standing review finding (its telemetry contract can change under CyClaw silently)")
        return
    pinned_desc = ", ".join(f"{s}={v}" for s, v in pins.items() if v is not None)
    if unbounded:
        info("T13", f"telemetry-capable transitive {name!r} is pinned on some surfaces only "
                    f"({pinned_desc}); still unbounded on: {', '.join(unbounded)}")
        return
    ok("T13", f"telemetry-capable transitive {name!r} pinned on every surface ({pinned_desc})")


def check_onnx_seams(root: Path) -> None:
    helper = root / "utils" / "onnx_telemetry.py"
    if not helper.exists():
        fail("T14", "utils/onnx_telemetry.py missing -- the ONNX API half of the suppression is gone")
        return
    text = helper.read_text(encoding="utf-8")
    if "disable_telemetry_events" not in text or "getattr" not in text:
        fail("T14", "utils/onnx_telemetry.py no longer performs the getattr-guarded "
                    "disable_telemetry_events call")
        return
    missing = [rel for rel in ("retrieval/vector_store.py", "guardrails/integration.py")
               if "suppress_onnx_telemetry" not in (root / rel).read_text(encoding="utf-8")]
    if missing:
        fail("T14", f"ONNX load seam(s) no longer call suppress_onnx_telemetry: {missing}")
        return
    ok("T14", "ONNX env+API suppression wired at both load seams")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--repo-root", type=Path, default=None)
    p.add_argument("--strict", action="store_true",
                   help="treat WARN as failure (exit 2 on any warning)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--as-of", type=date.fromisoformat, default=None, metavar="YYYY-MM-DD",
                   help="staleness anchor (default: today); lets tests pin a deterministic date")
    args = p.parse_args(argv)
    today = args.as_of or date.today()

    root = args.repo_root or Path(__file__).resolve().parents[3]
    kill_path = root / "utils" / "telemetry_kill.py"
    if not kill_path.exists():
        print(f"env error: not found: {kill_path}", file=sys.stderr)
        return 3

    try:
        source = kill_path.read_text(encoding="utf-8")
        kill = _load_literal(kill_path, "TELEMETRY_KILL")
        update = _load_literal(kill_path, "UPDATE_CHECK_OPT_OUT")
        creds = tuple(_load_literal(kill_path, "_TRACING_CREDENTIALS"))
        otel_cfg = tuple(_load_literal(kill_path, "_OTEL_DECLARATIVE_CONFIG"))
    except (OSError, SyntaxError, ValueError) as exc:
        print(f"env error: could not parse {kill_path}: {exc}", file=sys.stderr)
        return 3

    print(f"== otel-hardening: telemetry-kill contract (as of {today.isoformat()}) ==")
    check_shapes(kill, update, creds, otel_cfg)
    check_value_oracle(kill, update)
    check_scrub_oracle(creds, otel_cfg)
    check_staleness(source, today)
    check_vendor_pin_drift(root)
    report_transitive_vendor_versions()
    check_embeddings_conditional_wiring(root / "retrieval" / "embeddings.py")
    check_reference_env_file(root / "docs" / "security-philosophy" / "cyclaw_telemetry_kill.env")
    check_helper_wiring(source)
    check_docker_delivery(root)
    check_launcher_delivery(root)
    check_programmatic_bypasses(root)
    check_classification_inventory(root, strict=args.strict)
    check_onnx_seams(root)

    strict_fail = args.strict and _warns
    print(f"\n{len(_fails)} failure(s), {len(_warns)} warning(s)"
          + (" (--strict: warnings count as failures)" if args.strict else ""))
    if args.json:
        print(json.dumps({"fails": _fails, "warns": _warns, "strict": args.strict,
                          "as_of": today.isoformat()}, indent=2))
    return 2 if (_fails or strict_fail) else 0


if __name__ == "__main__":
    sys.exit(main())
