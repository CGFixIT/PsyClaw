# `static/` — browser consoles

Static HTML/JS served by the two local servers. No build step, no framework,
no external CDN — everything ships in this directory and is served from
loopback only.

| File | Served by | What it is |
|---|---|---|
| `terminal.html` + `terminal.js` | `gate.py` at `GET /` (plus the `/static` mount) on `127.0.0.1:8787` | The CyClaw Terminal — the operator console for `/query` and the authenticated soul/ops/memory endpoints. |
| `harness.html` | `harness/server.py` on `127.0.0.1:8790` | The coding-harness console (slash-command UI: `/goal`, `/loop`, `/skills`, `/tools`, `/web`, `/agent`, …). |
| `auth_admin.js` | served only by `gate.py`'s `/static` mount on `127.0.0.1:8787`; referenced by both `terminal.html` and `harness.html` | Shared Users panel (`/auth/users` list/create/role/disable/enable) — one script, no inline script. |

`gate.py` carries the only `/static` mount in the repo (`gate.py`'s
`app.mount("/static", ...)`). `harness/server.py` mounts nothing: it reads
`harness.html` off disk and returns it as an HTML response, and its route table
is `GET /` plus `/api/*`. So `harness.html`'s `<script src="/static/auth_admin.js">`
tag resolves only when the markup is served from the gateway — loaded from the
harness console on `127.0.0.1:8790` that request has no route and the Users
panel script does not load. Treat the shared Users panel as a gateway-console
feature until the harness grows a mount of its own.

## The console contract

`tests/test_terminal_contract.py` extracts the routes `terminal.html`
actually calls and compares them against `gate.py`'s route table — any new
state-changing POST endpoint must be added to that test's `_POST_PATHS`, and
any route the console calls must really exist. Treat `terminal.html` as a
tested artifact, not free-form UI.

`tests/test_harness_console_contract.py` does the same for `harness.html`:
every `api(...)` path (including concatenations like
`/api/sessions/{}/goal`) must exist on `harness/server.py` with the method
the console uses. Slash-command contracts for `/goal`, `/loop`, `/skills`,
`/tools`, and `/web` live there too. `/web` and `/loop` must never call
`/api/agent/*`.

Security posture for anything added here: same-origin only, no third-party
script/font/CDN references (the servers are loopback-bound and offline-first
— an external reference would both leak and break), and any
`target="_blank"` link needs `rel="noopener noreferrer"`.

## Related

- Route table and auth requirements per endpoint: `CLAUDE.md` §2 "All HTTP routes"
- Harness walkthroughs: `docs/HARNESS_POWERSHELL.md`, `docs/HARNESS_MACOS.md`
- Harness slash-command usage: [`../harness/README.md`](../harness/README.md)
