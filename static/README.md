# `static/` — browser consoles

Static HTML/JS served by the two local servers. No build step, no framework,
no external CDN — everything ships in this directory and is served from
loopback only.

| File | Served by | What it is |
|---|---|---|
| `terminal.html` + `terminal.js` | `gate.py` at `GET /` (plus the `/static` mount) on `127.0.0.1:8787` | The CyClaw Terminal — the operator console for `/query` and the authenticated soul/ops/memory endpoints. |
| `harness.html` | `harness/server.py` on `127.0.0.1:8790` | The coding-harness console (grok-build-style slash-command UI). |
| `extractor.html` + `extractor.js` | reachable at `/static/extractor.html` via the static mount, but nothing links to it — also works opened directly from disk | Standalone keyword-insight extractor utility; calls no CyClaw endpoint. |

## The console contract

`tests/test_terminal_contract.py` extracts the routes `terminal.html`
actually calls and compares them against `gate.py`'s route table — any new
state-changing POST endpoint must be added to that test's `_POST_PATHS`, and
any route the console calls must really exist. Treat `terminal.html` as a
tested artifact, not free-form UI.

Security posture for anything added here: same-origin only, no third-party
script/font/CDN references (the servers are loopback-bound and offline-first
— an external reference would both leak and break), and any
`target="_blank"` link needs `rel="noopener noreferrer"`.

## Related

- Route table and auth requirements per endpoint: `CLAUDE.md` §2 "All HTTP routes"
- Harness walkthroughs: `docs/HARNESS_POWERSHELL.md`, `docs/HARNESS_MACOS.md`