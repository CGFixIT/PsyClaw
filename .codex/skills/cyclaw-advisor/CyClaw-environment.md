# CyClaw environment notes (advisor sibling)

Routing aid for hardware / install / harness-path / dep facts the advisor may cite.
Re-verify against live `origin/main`. Recorded with cyclaw-advisor at
`d9b0f8cdb7b59923371da692ab0be18ed116e9df` (2026-08-27).

Code and `config.yaml` win. This file is not an installer.

## Operator box (docs after 7564f508)

Tracked docs name the operator SKU as **MacBook Pro M5 Pro 48GB**, not M5 Max.
See `setup-guide.md` and the M5 48GB local-coder expectations for `real-repo-run`.
Do not invent RAM/model pairings beyond what those docs currently say.

## Hard sandbox (executor, not the core graph)

Production `run_verification` must not call unconstrained `subprocess.run`.
`agentic/executor/hard_sandbox.py` `production_sandbox()`:

| Platform | Backend | Fail closed when |
|---|---|---|
| Windows | Job Object (`KILL_ON_JOB_CLOSE`, 32-process cap) | Create/assign failure |
| Darwin | `sandbox-exec` Seatbelt (deny network; write only under cwd + TMPDIR) | `sandbox-exec` missing |
| Linux | `unshare --net` after a `/bin/true` probe | `unshare` missing or probe EPERM |

`ArgvListSandbox` is tests-only. POSIX timeout kills the process group
(`os.killpg`), not just the `sandbox-exec` / `unshare` wrapper.
Windows Job Object is a process-tree kill boundary, not a network namespace —
sockets still work on that backend.

## Telemetry kill (#1149)

`utils/telemetry_kill.py` is the canonical map. It is applied at import and
delivered into child/scheduler/Docker/launcher environments. Real ONNX
suppression uses `ORT_DISABLE_TELEMETRY=1` plus `disable_telemetry_events()`
at load seams.

This is **not** a general network kill switch. Class-3 policy-gated traffic
(Grok/Claude, Telegram, OpenTweet, rclone/Dropbox, SQL, `/web`, model
bootstrap) stays behind existing gates. Decorative `CYCLAW_TELEMETRY_KILL=1`
is gone from Docker.

## Harness / I6 path

Harness ToolBroker imports `utils.tool_broker`, never `guardrails`.
`mcp_hybrid_server.py` must not import brokers.
`real-repo-run` is CLI via `ops_runner` / `agentic.cli`, not an MCP tool.

## Deps / pins that matter to the advisor

- Optional `nemoguardrails` pin is 0.24.0. Shipped `guardrails.enabled` is
  boolean `false`. CI may overlay `enabled: true` in a temp file only (#1162).
- `pygments==2.21.0` is on the `pyproject.toml` test extra.
- `httpx==0.28.1` and `websockets==15.0.1` remain the live pins
  (`remaining_work.md`); do not advise a silent major bump.

## Terminal / launch surfaces

No `terminal.html` path change was in the high-signal set after `7564f508`.
If a later commit moves the harness console, re-read `harness/` and
`docs/HARNESS_*.md` before citing a URL or file path.
