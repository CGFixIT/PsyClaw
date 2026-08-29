# `static/` — browser consoles

Static HTML/JS served by the two local servers. No build step, no framework,
no external CDN — everything ships in this directory and is served from
loopback only.

| File | Served by | What it is |
|---|---|---|
| `terminal.html` + `terminal.js` | `gate.py` at `GET /` (plus the `/static` mount) on `127.0.0.1:8787` | The CyClaw Terminal — the operator console for `/query` and the authenticated soul/ops/memory endpoints. |
| `harness.html` | `harness/server.py` at `GET /` (plus the `/static` mount) on `127.0.0.1:8790` | The coding-harness console (slash-command UI: `/goal`, `/loop`, `/skills`, `/tools`, `/web`, `/agent`, …). |
| `auth_admin.js` | both `/static` mounts — `gate.py` on `127.0.0.1:8787` and `harness/server.py` on `127.0.0.1:8790`; referenced by both `terminal.html` and `harness.html` | Shared Users panel (`/auth/users` list/create/role/disable/enable) — one script, no inline script. |

Both servers mount this directory (`app.mount("/static", ...)`), so
`harness.html`'s `<script src="/static/auth_admin.js">` resolves on either
console and the shared Users panel works on both. The harness route table is
`GET /` plus `/static/*` plus `/api/*`; it still reads `harness.html` off disk
once per app instance and returns it as an HTML response rather than serving
that copy through the mount, because the response is templated (see below).

Two placeholders are substituted into `harness.html` at serve time and are
always literal in the file on disk: `__CYCLAW_CSRF_TOKEN__` (one value per
process) and `__CYCLAW_CSP_NONCE__` (a fresh value per response). The harness
console's CSP names that nonce as the only source for its inline `<style>` and
`<script>`, so **any inline block added here needs `nonce="__CYCLAW_CSP_NONCE__"`
or the browser silently refuses to run it**. Served through `gate.py`'s mount
instead, the raw file keeps both literals — gate's own CSP has no nonce source,
so its inline script stays blocked there, which is why
`tests/test_gate.py::test_no_static_page_relies_on_inline_script` exempts
`harness.html` by name.

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
