# `windows/` — generate-only Task Scheduler supervisors

Windows twin of `macos/generate_service_plist.py`. Not request-path code
(`gate.py` / `graph.py` / `mcp_hybrid_server.py` never import this; I6).

| Script | What it does |
|---|---|
| `generate_service_task.py` | Write XML + `.cmd` for `gate.py` or `harness.server`. Refuses without `--confirm` and a non-empty `--reason`. Never calls `schtasks /Create`. |

Installer / CredMan / fsconnect jail live in [`powershell/`](../powershell/README.md).
Harness slash commands: [`harness/README.md`](../harness/README.md).
Trash and Telegram generators: `python -m agentic.fsconnect.cli trash-empty-task`,
`python -m telegram.cli poll-task` / `health-task`.
