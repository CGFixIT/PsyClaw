# `powershell/` — Windows install and launch glue

Windows-native installer / launcher / uninstaller for the CyClaw coding
harness. Not request-path code: `gate.py`, `graph.py`, and
`mcp_hybrid_server.py` never import anything here (I6). The macOS/Linux
sibling is `macos/` — two OS-native script trees on purpose, because the
harness Python code itself carries no platform branch.

Target platforms: Windows 10/11 and Server 2019/2022, Windows PowerShell 5.1
(also works on PowerShell 7+). CI exercises the 5.1 path on `windows-2022`.

After install, typing `cyclaw` in any PowerShell window starts the harness
console on `127.0.0.1:8790`. Everything mutable lives under
`%USERPROFILE%\.CyClaw` (sessions, venv, repo clone).

## Scripts

| Script | What it does |
|---|---|
| `Install-CyClaw.ps1` | Creates the `%USERPROFILE%\.CyClaw` home layout and venv, installs the `cyclaw` profile function and PATH entry. |
| `Invoke-CyClaw.ps1` | Starts the harness control plane on `127.0.0.1:8790` (loopback only) from the per-user venv, opens the browser console. Ctrl+C stops it. |
| `Uninstall-CyClaw.ps1` | Removes the profile function and PATH entry. Keeps `%USERPROFILE%\.CyClaw` unless `-RemoveHome` (prompts first). |

Each script carries full comment-based help — `Get-Help .\Install-CyClaw.ps1
-Full` is the per-flag reference; this README stays a map.

## Related

- Full harness walkthrough (Windows): [`docs/HARNESS_POWERSHELL.md`](../docs/HARNESS_POWERSHELL.md)
- Console package: [`harness/README.md`](../harness/README.md)
- macOS/Linux equivalent: [`macos/README.md`](../macos/README.md)
