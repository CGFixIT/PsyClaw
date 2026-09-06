# Verification playbooks

Read the active workflow and nearest tests; do not invent a universal full-suite
requirement. Each result belongs to a tested SHA, environment, and scope.

| Changed property | Discriminating check |
|---|---|
| Endpoint trust or consent | Exercise both local answer nodes, malformed and untrusted URLs, explicit trusted host success, and provider deny/unavailable/confirm combinations with mocked HTTP. |
| Authorization or execution | Trace admission and execution-time checks, stale capabilities, alternate routes, and failure after partial work when relevant. Never enable live writes to prove a refusal. |
| Concurrency or retries | Reproduce the race/retry trigger with bounded deterministic coordination; verify state and cleanup, not merely successful return codes. |
| Telemetry | Inspect utils/telemetry_kill.py maps/scrubbing, pre-import ordering, child environments, and utils/onnx_telemetry.py load seams. Env flags are not a firewall. |
| Mac dotenv | Verify 600/400 permissions, absolute BSD stat when PATH contains GNU tools, source failure fallback, and restored allexport. Native macOS evidence remains distinct from Git Bash. |
| Docs/skills | Frontmatter, exact invocation metadata, references, code comparison, doc-sync, and any modified wrapper's argument/error behavior. |

For CyClaw, use Python 3.12 and dummy keys in isolated tests. Ruff F/B/S blocks
CI; broader Ruff/WPS are advisory and mypy is best-effort. Bare pytest does not
measure coverage: use the current workflow's explicit --cov flags for that claim.

PR reviews must inspect actual head/base and all requested findings. Confirm a
finding's trigger, impact, and validity; classify already-fixed or unsupported
reports with evidence. Apply authorized fixes to the respective remote branch.
A bot summary of a local commit is not proof that its code reached GitHub.

After publishing, verify the remote head, mergeability, and current-head checks.
Separate inherited main failures, branch regressions, and unavailable platform
checks. Never mark skipped or interrupted tests as passing. Approval of a code
change is not approval to merge or deploy it.
