# Terminal Sync + Agentic Ops Panels — Session Notes

**Date:** 2026-06-23 (re-grounded against `origin/main` @ `7a69054`, 2026-06-24)
**Branch:** `feature/terminal-update` (cut from `origin/main`) → PR into `main`
**Author:** @cgfixit via Claude Code

---

## Context — why this change

`static/terminal.html` already ships a polished **Soul Console**: a `.toolbar-btn` that
toggles a `.soul-panel`, `.soul-btn` actions, a `.soul-reason` input, a `.soul-status`
line driven by `setSoulStatus()`, and async `fetch()` handlers (`loadSoul()`,
`proposeSoulEvolution()`, `applySoulEvolution()`) that POST to `/soul/*` with an optional
`Authorization: Bearer` header from `authHeaders()`.

CyClaw's two **out-of-band** subsystems had **zero operator UI**:
- `sync/` — Dropbox corpus pull via `rclone` (`python -m sync.cli`)
- `agentic/` — governed GitHub context + skills registry (`python -m agentic.cli`)

Both could only be driven from a terminal. This change adds a **Sync Console** and an
**Agentic Console** to `terminal.html`, cloned byte-for-byte from the Soul Console pattern,
backed by two new loopback-only, rate-limited, audit-logged `/ops/*` routes in `gate.py`
that **shell out** to the CLIs via `subprocess.run([...])` — preserving the hard invariant
that `gate.py` never *imports* `sync/` or `agentic/`.

### Anchor pattern (quoted verbatim before any new UI was written)

```html
<!-- toolbar button -->
<button class="toolbar-btn" id="soulToggleBtn" onclick="toggleSoulPanel()">Soul Console</button>
<!-- action buttons -->
<button class="soul-btn" onclick="loadSoul()">Load</button>
<button class="soul-btn apply" onclick="applySoulEvolution()">Apply</button>
<!-- reason input + status line -->
<input class="soul-reason" id="soulReason" type="text" placeholder="reason for change...">
<div class="soul-status" id="soulStatus"></div>
```
```javascript
function authHeaders() {
  const key = apiKeyInput ? apiKeyInput.value.trim() : '';
  const h = { 'Content-Type': 'application/json' };
  if (key) h['Authorization'] = `Bearer ${key}`;
  return h;
}
function setSoulStatus(message, tone = '') {
  soulStatus.textContent = message || '';
  soulStatus.className = `soul-status${tone ? ` ${tone}` : ''}`;
}
// fetch idiom: const data = await resp.json().catch(() => ({}));
//             if (!resp.ok) throw new Error(extractErrorMessage(data, '...'));
```

Every new button, panel, and handler is structurally identical: same CSS classes, same
`authHeaders()` Bearer fetch, same `set<Panel>Status(message, tone)` feedback idiom.

---

## 1. Git workflow + commit plan

```bash
# (Re-grounded) cut a fresh branch from the latest origin/main:
git fetch origin main
git checkout -b feature/terminal-update origin/main
git config user.email noreply@anthropic.com
git config user.name Claude
```

The task §2 named `feature/terminal-sync-agentic-redesign-2026-06-23`; the actual working
branch is **`feature/terminal-update`** per the follow-up instruction ("clone origin/main
into a branch called feature/terminal-update").

**Commit order (shim-first, so each layer is testable in isolation):**
1. `utils/ops_runner.py` + `tests/test_ops_runner.py` — subprocess shim + 25 unit tests.
2. `schemas/api.py` — `OpsSyncRequest` / `OpsAgenticRequest` typed contract.
3. `gate.py` — `POST /ops/sync` + `POST /ops/agentic` wiring the shim.
4. `static/terminal.html` — Sync + Agentic panels (HTML + CSS + JS handlers).
5. This session-notes deliverable.

Then: `git push -u origin feature/terminal-update` (retry w/ backoff) → open **draft** PR into `main`.

---

## 2. `static/terminal.html` — Sync panel (HTML + JS)

The Sync panel is a `.soul-panel` clone with a status badge, five `.soul-btn` actions
(Status / Dry-Run / Pull Now / Schedule On / Schedule Off), a `syncStatus`-element +
`setSyncStatus()` feedback function byte-identical to `setSoulStatus()`, and a
`.proposal-box`-style readout that renders **exit code + stderr** for every failure state:

| Exit | Rendered message |
|---|---|
| 0 | OK |
| 10 | OK — corpus changed; reindex needed |
| 1 | **SAFETY FUSE TRIPPED (max-delete / max-transfer abort)** |
| 2 | operation failed |
| 3 | env/config error (rclone missing/too old, or config invalid) |

The full terminal.html diff (CSS + toolbar + both panels + DOM refs + JS handlers) is in
§3 below as one unified diff — the Sync hunks are the `#syncPanel` block and the
`---- Sync Console ----` JS section.

---

## 3. `static/terminal.html` — full unified diff (Sync + Agentic panels)

> One file, one diff. Sync panel = `#syncPanel` + `Sync Console` JS section.
> Agentic panel = `#agenticPanel` + 4-gate checklist + `Agentic Console` JS section.

```diff
diff --git a/static/terminal.html b/static/terminal.html
index fbc9dd6..70e1dfc 100644
--- a/static/terminal.html
+++ b/static/terminal.html
@@ -577,6 +577,39 @@
     max-height: 220px;
     overflow: auto;
   }
+  /* ── AGENTIC 4-GATE CHECKLIST (Sync/Agentic panels reuse all soul classes) ── */
+  .gate-checklist {
+    display: flex;
+    flex-direction: column;
+    gap: 3px;
+    font-family: var(--mono);
+    font-size: 10px;
+    color: var(--text-muted);
+    border-left: 2px solid var(--border);
+    padding: 4px 0 4px 10px;
+  }
+  .gate-row.ok { color: var(--accent-green); }
+  .gate-row.bad { color: var(--accent-amber); }
+  .gate-confirm-label {
+    font-family: var(--mono);
+    font-size: 11px;
+    color: var(--text-secondary);
+    display: flex;
+    align-items: center;
+    gap: 5px;
+    cursor: pointer;
+  }
+  .soul-btn:disabled {
+    opacity: 0.45;
+    cursor: not-allowed;
+    border-color: var(--border);
+    color: var(--text-muted);
+  }
+  .soul-btn:disabled:hover {
+    color: var(--text-muted);
+    border-color: var(--border);
+    background: transparent;
+  }
 </style>
 </head>
 <body>
@@ -602,7 +635,9 @@
 <div class="main">
   <div class="soul-toolbar">
     <button class="toolbar-btn" id="soulToggleBtn" onclick="toggleSoulPanel()">Soul Console</button>
-    <span class="toolbar-hint">view, reload, propose, and apply the personality layer</span>
+    <button class="toolbar-btn" id="syncToggleBtn" onclick="toggleSyncPanel()">Sync Console</button>
+    <button class="toolbar-btn" id="agenticToggleBtn" onclick="toggleAgenticPanel()">Agentic Console</button>
+    <span class="toolbar-hint">soul · corpus sync · agentic ops — all loopback, audited, API-key gated</span>
     <input id="apiKeyInput" type="password" placeholder="API key (optional)"
            style="margin-left:auto;background:var(--bg-input);border:1px solid var(--border);
            border-radius:var(--radius);padding:4px 8px;font-family:var(--mono);font-size:10px;
@@ -634,6 +669,79 @@
     </div>
   </div>
 
+  <!-- SYNC CONSOLE — drives the out-of-band sync/ CLI via POST /ops/sync -->
+  <div class="soul-panel" id="syncPanel">
+    <div class="soul-header">
+      <div class="soul-title">Sync Console</div>
+      <div class="soul-meta">
+        <span id="syncEnabled">enabled: --</span>
+        <span id="syncDirection">direction: --</span>
+        <span id="syncSchedule">schedule: --</span>
+        <span id="syncBadge">last: never</span>
+      </div>
+    </div>
+    <div class="soul-actions">
+      <button class="soul-btn" onclick="syncStatusCmd()">Status</button>
+      <button class="soul-btn" onclick="syncDryRun()">Dry-Run</button>
+      <button class="soul-btn apply" onclick="syncPull()">Pull Now</button>
+      <button class="soul-btn" onclick="syncScheduleOn()">Schedule On</button>
+      <button class="soul-btn" onclick="syncScheduleOff()">Schedule Off</button>
+    </div>
+    <div class="soul-status" id="syncStatus"></div>
+    <div class="proposal-box" id="syncBox">
+      <div class="proposal-meta" id="syncMeta"></div>
+      <div class="proposal-warning" id="syncWarning"></div>
+      <pre class="proposal-preview" id="syncPreview"></pre>
+    </div>
+  </div>
+
+  <!-- AGENTIC CONSOLE — drives the out-of-band agentic/ CLI via POST /ops/agentic -->
+  <div class="soul-panel" id="agenticPanel">
+    <div class="soul-header">
+      <div class="soul-title">Agentic Console</div>
+      <div class="soul-meta">
+        <span id="agenticEnabled">enabled: --</span>
+        <span id="agenticMode">mode: --</span>
+        <span id="agenticWrites">writes_enabled: --</span>
+      </div>
+    </div>
+    <div class="soul-actions">
+      <input class="soul-reason" id="agenticNum" type="text" inputmode="numeric"
+             placeholder="PR / Issue # (for Fetch Context)" style="max-width:260px;">
+      <button class="soul-btn" onclick="agenticContextPR()">Fetch PR</button>
+      <button class="soul-btn" onclick="agenticContextIssue()">Fetch Issue</button>
+      <button class="soul-btn" onclick="agenticStatusCmd()">Status</button>
+      <button class="soul-btn" onclick="agenticRegistryHealth()">Registry Health</button>
+    </div>
+    <input class="soul-reason" id="agenticName" type="text" placeholder="skill name (for propose / apply)">
+    <input class="soul-reason" id="agenticDesc" type="text" placeholder="skill description">
+    <textarea class="soul-editor" id="agenticBody" placeholder="skill body (markdown)..." style="min-height:120px;"></textarea>
+    <input class="soul-reason" id="agenticReason" type="text" oninput="refreshAgenticGates()"
+           placeholder="reason for change (required to apply a skill)">
+    <div class="soul-actions">
+      <button class="soul-btn" onclick="agenticPropose()">Propose Skill</button>
+      <label class="gate-confirm-label"><input type="checkbox" id="agenticConfirm" onchange="refreshAgenticGates()"> --confirm</label>
+      <button class="soul-btn apply" id="agenticApplyBtn" onclick="agenticApply()" disabled>Apply Skill (writes disabled)</button>
+    </div>
+    <!-- 4-gate checklist: Apply stays disabled until all four show ✓. Gates 1-2
+         (mode=write, writes_enabled) come from the route's config block; gates 3-4
+         (reason, --confirm) from the inputs above. This is a UI governance overlay:
+         with the shipped defaults (writes_enabled:false) the Apply button is disabled,
+         so no skills-registry write can be triggered from the console at all. -->
+    <div class="gate-checklist" id="agenticGates">
+      <div class="gate-row bad" id="gateMode">✗ mode = write</div>
+      <div class="gate-row bad" id="gateWrites">✗ writes_enabled = true</div>
+      <div class="gate-row bad" id="gateReason">✗ reason non-empty</div>
+      <div class="gate-row bad" id="gateConfirm">✗ --confirm checked</div>
+    </div>
+    <div class="soul-status" id="agenticStatus"></div>
+    <div class="proposal-box" id="agenticBox">
+      <div class="proposal-meta" id="agenticMeta"></div>
+      <div class="proposal-warning" id="agenticWarning"></div>
+      <pre class="proposal-preview" id="agenticPreview"></pre>
+    </div>
+  </div>
+
   <div class="results" id="results">
     <div class="empty-state" id="emptyState">
       <div class="logo-large">CyClaw</div>
@@ -688,6 +796,24 @@ const proposalWarning = document.getElementById('proposalWarning');
 const proposalPreview = document.getElementById('proposalPreview');
 const soulToggleBtn = document.getElementById('soulToggleBtn');
 
+// Sync + Agentic console refs (mirror the soul console ref/element naming).
+const syncPanel = document.getElementById('syncPanel');
+const syncStatus = document.getElementById('syncStatus');
+const syncBox = document.getElementById('syncBox');
+const syncMeta = document.getElementById('syncMeta');
+const syncWarning = document.getElementById('syncWarning');
+const syncPreview = document.getElementById('syncPreview');
+const syncToggleBtn = document.getElementById('syncToggleBtn');
+const agenticPanel = document.getElementById('agenticPanel');
+const agenticStatus = document.getElementById('agenticStatus');
+const agenticBox = document.getElementById('agenticBox');
+const agenticMeta = document.getElementById('agenticMeta');
+const agenticWarning = document.getElementById('agenticWarning');
+const agenticPreview = document.getElementById('agenticPreview');
+const agenticToggleBtn = document.getElementById('agenticToggleBtn');
+const agenticConfirm = document.getElementById('agenticConfirm');
+const agenticApplyBtn = document.getElementById('agenticApplyBtn');
+
 let queryCount = 0;
 let pendingConfirmQuery = null;
 let pendingSoulProposal = null;
@@ -1065,6 +1191,211 @@ async function restoreSoul() {
   }
 }
 
+// ============================================================================
+// SYNC + AGENTIC OPS CONSOLES
+// Same async/fetch/status idiom as the Soul Console. Both POST to /ops/* with
+// the API key from authHeaders(); both render the exit-code envelope so failure
+// states (safety-fuse abort, env/config error, write refused) are explicit.
+// ============================================================================
+
+// Shared POST helper. The route returns HTTP 200 even when the CLI exits
+// non-zero (the exit code lives in the JSON envelope); only gateway-level
+// problems (401/422/400/429/500) trip the !resp.ok branch.
+async function callOps(path, body) {
+  const resp = await fetch(`${API}${path}`, {
+    method: 'POST',
+    headers: authHeaders(),
+    body: JSON.stringify(body)
+  });
+  const data = await resp.json().catch(() => ({}));
+  if (!resp.ok) {
+    throw new Error(extractErrorMessage(data, 'Ops request failed'));
+  }
+  return data;
+}
+
+// Render the exit-code envelope into a soul-style proposal box.
+function renderOps(box, meta, warning, preview, data) {
+  meta.textContent = `action: ${data.action} · exit: ${data.exit_code} (${data.label}) · ${data.ok ? 'OK' : 'FAILED'}`;
+  const err = (data.stderr || '').trim();
+  warning.textContent = err ? `stderr: ${err.slice(0, 600)}` : '';
+  let bodyText = '';
+  if (data.parsed) {
+    bodyText = JSON.stringify(data.parsed, null, 2);
+  } else {
+    bodyText = (data.stdout || '').trim();
+  }
+  preview.textContent = bodyText || '(no output)';
+  box.style.display = 'block';
+}
+
+// ---- Sync Console ----------------------------------------------------------
+
+function setSyncStatus(message, tone = '') {
+  syncStatus.textContent = message || '';
+  syncStatus.className = `soul-status${tone ? ` ${tone}` : ''}`;
+}
+
+function syncLabelMsg(data) {
+  switch (data.exit_code) {
+    case 0:  return 'OK';
+    case 10: return 'OK — corpus changed; reindex needed (python -m retrieval.indexer)';
+    case 1:  return 'SAFETY FUSE TRIPPED (max-delete / max-transfer abort)';
+    case 2:  return 'operation failed';
+    case 3:  return 'env/config error (rclone missing/too old, or config invalid)';
+    default: return `exit ${data.exit_code}`;
+  }
+}
+
+function applySyncConfig(config) {
+  if (!config) return;
+  document.getElementById('syncEnabled').textContent = `enabled: ${config.enabled}`;
+  document.getElementById('syncDirection').textContent = `direction: ${config.direction}`;
+  document.getElementById('syncSchedule').textContent = `schedule: ${config.schedule}`;
+}
+
+async function runSync(action, opts = {}) {
+  setSyncStatus(`Running sync ${action}...`);
+  try {
+    const data = await callOps('/ops/sync', { action, ...opts });
+    applySyncConfig(data.config);
+    renderOps(syncBox, syncMeta, syncWarning, syncPreview, data);
+    setSyncStatus(`[${action}] ${syncLabelMsg(data)}`, data.ok ? 'success' : 'error');
+    document.getElementById('syncBadge').textContent = `last: ${action} → ${data.label}`;
+    addEntry('system', '', `→ ops/sync ${action} exit ${data.exit_code} (${data.label})`);
+  } catch (e) {
+    setSyncStatus(e.message, 'error');
+  }
+}
+
+function syncStatusCmd()  { return runSync('status'); }
+function syncDryRun()     { return runSync('sync', { dry_run: true }); }
+function syncPull()       { return runSync('sync', { dry_run: false }); }
+function syncScheduleOn() { return runSync('schedule'); }
+function syncScheduleOff(){ return runSync('unschedule'); }
+
+let syncLoaded = false;
+async function toggleSyncPanel() {
+  syncPanel.classList.toggle('open');
+  const open = syncPanel.classList.contains('open');
+  syncToggleBtn.textContent = open ? 'Hide Sync' : 'Sync Console';
+  // Lazy first status read only when a key is already present (all ops need auth).
+  if (open && !syncLoaded && apiKeyInput.value.trim()) { syncLoaded = true; await runSync('status'); }
+}
+
+// ---- Agentic Console -------------------------------------------------------
+
+let agenticConfig = { enabled: false, mode: 'read', writes_enabled: false };
+
+function setAgenticStatus(message, tone = '') {
+  agenticStatus.textContent = message || '';
+  agenticStatus.className = `soul-status${tone ? ` ${tone}` : ''}`;
+}
+
+function agenticLabelMsg(data) {
+  switch (data.exit_code) {
+    case 0:  return 'OK';
+    case 2:  return 'operation failed';
+    case 3:  return 'env/config error (gh missing/too old, or config invalid)';
+    case 4:  return 'WRITE REFUSED by gate';
+    default: return `exit ${data.exit_code}`;
+  }
+}
+
+function setGate(id, ok, label) {
+  const el = document.getElementById(id);
+  el.textContent = `${ok ? '✓' : '✗'} ${label}`;
+  el.className = `gate-row ${ok ? 'ok' : 'bad'}`;
+}
+
+// The 4-gate checklist. Apply stays disabled until all four pass. Gates 1-2 are
+// config-driven (mode=write + writes_enabled), so with the shipped defaults
+// (mode=read, writes_enabled=false) the Apply button is disabled and cannot fire a
+// skills-registry write from the console. This is a UI governance overlay on top of
+// the registry's own gate (reason + injection scan + --confirm) — strictly stricter,
+// never weaker. (GitHub writes are separate and stay stubbed in agentic/writer.py.)
+function refreshAgenticGates() {
+  const reasonOk  = document.getElementById('agenticReason').value.trim().length > 0;
+  const confirmOk = agenticConfirm.checked;
+  const modeOk    = agenticConfig.mode === 'write';
+  const writesOk  = agenticConfig.writes_enabled === true;
+  setGate('gateMode', modeOk, 'mode = write');
+  setGate('gateWrites', writesOk, 'writes_enabled = true');
+  setGate('gateReason', reasonOk, 'reason non-empty');
+  setGate('gateConfirm', confirmOk, '--confirm checked');
+  const allOk = modeOk && writesOk && reasonOk && confirmOk;
+  agenticApplyBtn.disabled = !allOk;
+  agenticApplyBtn.textContent = allOk
+    ? 'Apply Skill (confirm write)'
+    : (writesOk ? 'Apply Skill' : 'Apply Skill (writes disabled)');
+}
+
+function applyAgenticConfig(config) {
+  if (!config) return;
+  agenticConfig = config;
+  document.getElementById('agenticEnabled').textContent = `enabled: ${config.enabled}`;
+  document.getElementById('agenticMode').textContent = `mode: ${config.mode}`;
+  document.getElementById('agenticWrites').textContent = `writes_enabled: ${config.writes_enabled}`;
+  refreshAgenticGates();
+}
+
+async function runAgentic(action, opts = {}) {
+  setAgenticStatus(`Running agentic ${action}...`);
+  try {
+    const data = await callOps('/ops/agentic', { action, ...opts });
+    applyAgenticConfig(data.config);
+    renderOps(agenticBox, agenticMeta, agenticWarning, agenticPreview, data);
+    let extra = '';
+    if (data.parsed && typeof data.parsed.governance_score === 'number') {
+      extra = ` · governance_score: ${data.parsed.governance_score}/100`;
+    }
+    setAgenticStatus(`[${action}] ${agenticLabelMsg(data)}${extra}`, data.ok ? 'success' : 'error');
+    addEntry('system', '', `→ ops/agentic ${action} exit ${data.exit_code} (${data.label})`);
+  } catch (e) {
+    setAgenticStatus(e.message, 'error');
+  }
+}
+
+function agenticStatusCmd()    { return runAgentic('status'); }
+function agenticRegistryHealth(){ return runAgentic('status'); }  // status carries registry_version + skills
+
+function agenticContextPR() {
+  const n = parseInt(document.getElementById('agenticNum').value, 10);
+  if (Number.isNaN(n)) { setAgenticStatus('Enter a PR number first.', 'error'); return; }
+  return runAgentic('context', { pr: n });
+}
+function agenticContextIssue() {
+  const n = parseInt(document.getElementById('agenticNum').value, 10);
+  if (Number.isNaN(n)) { setAgenticStatus('Enter an issue number first.', 'error'); return; }
+  return runAgentic('context', { issue: n });
+}
+function agenticPropose() {
+  const name = document.getElementById('agenticName').value.trim();
+  const desc = document.getElementById('agenticDesc').value.trim();
+  const body = document.getElementById('agenticBody').value;
+  const reason = document.getElementById('agenticReason').value.trim();
+  if (!name || !desc) { setAgenticStatus('Skill name and description are required.', 'error'); return; }
+  return runAgentic('propose-skill', { name, desc, body: body || null, reason: reason || null });
+}
+function agenticApply() {
+  const name = document.getElementById('agenticName').value.trim();
+  const desc = document.getElementById('agenticDesc').value.trim();
+  const body = document.getElementById('agenticBody').value;
+  const reason = document.getElementById('agenticReason').value.trim();
+  if (!name || !desc) { setAgenticStatus('Skill name and description are required.', 'error'); return; }
+  if (!reason) { setAgenticStatus('A non-empty reason is required to apply.', 'error'); return; }
+  return runAgentic('apply-skill', { name, desc, body: body || null, reason, confirm: agenticConfirm.checked });
+}
+
+let agenticLoaded = false;
+async function toggleAgenticPanel() {
+  agenticPanel.classList.toggle('open');
+  const open = agenticPanel.classList.contains('open');
+  agenticToggleBtn.textContent = open ? 'Hide Agentic' : 'Agentic Console';
+  if (open) refreshAgenticGates();
+  if (open && !agenticLoaded && apiKeyInput.value.trim()) { agenticLoaded = true; await runAgentic('status'); }
+}
+
 function escHtml(str) {
   const d = document.createElement('div');
   d.textContent = str;
```

### Agentic 4-gate checklist (constraint 8 made visible)

The Apply button stays `disabled` until **all four** gates show ✓:

```
✗ mode = write          ← from route config block (cfg["agentic"]["mode"])
✗ writes_enabled = true  ← from route config block (cfg["agentic"]["writes_enabled"])
✗ reason non-empty       ← from the reason input
✗ --confirm checked      ← from the confirm checkbox
```

With the shipped defaults (`mode: read`, `writes_enabled: false`), gates 1–2 are ✗, so
Apply is **impossible via the UI → dry-run only**. The button label reflects state:
`Apply Skill (writes disabled)` → `Apply Skill` → `Apply Skill (confirm write)`.
Failure states render agentic exit **4 = WRITE REFUSED by gate**, and `governance_score`
(0–100) is surfaced from the `propose-skill` JSON.

---

## 4. `gate.py` additions — the two `/ops/*` routes

**Routes ARE required** (not optional): a browser cannot spawn a subprocess, so the panel
buttons need an HTTP endpoint. They remain *subprocess shims* — `gate.py` never imports
`sync/`/`agentic/`. Two routes total (under the 3-route cap), both loopback-only,
rate-limited (shared `_rate_limiter`), `require_api_key`-gated, and audited.

```diff
diff --git a/gate.py b/gate.py
index 1d31b60..bbe1ebd 100644
--- a/gate.py
+++ b/gate.py
@@ -72,7 +72,10 @@ from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
 from graph import build_graph, GraphState
 from retrieval.hybrid_search import HybridRetriever
 from llm.client import LocalLLMClient, GrokClient
-from schemas.api import QueryRequest, QueryResponse, SourceInfo, HealthResponse, SoulEvolutionRequest
+from schemas.api import (
+    QueryRequest, QueryResponse, SourceInfo, HealthResponse, SoulEvolutionRequest,
+    OpsSyncRequest, OpsAgenticRequest,
+)
 from utils.logger import audit_log, setup_logging
 from fastapi.middleware.cors import CORSMiddleware
 from fastapi.staticfiles import StaticFiles
@@ -82,6 +85,10 @@ from utils.errors import (
 )
 from utils.health import check_all
 from utils.personality import PersonalityManager
+# Subprocess shim for the out-of-band sync/ + agentic/ control surface. This is a
+# subprocess wrapper ONLY — it never imports sync/ or agentic/, so gate.py's
+# out-of-band isolation invariant is preserved (see utils/ops_runner.py).
+from utils.ops_runner import run_sync_op, run_agentic_op, OpsError
 from metrics import load_events, compute_metrics
 
 _bearer_scheme = HTTPBearer(auto_error=False)
@@ -425,6 +432,105 @@ async def audit_summary():
     return await asyncio.to_thread(compute_metrics, events)
 
 
+# =============================================================================
+# Ops endpoints — out-of-band sync/ + agentic/ control surface (terminal panels)
+# =============================================================================
+# These back the Soul Console's Sync + Agentic panels. A browser cannot spawn a
+# subprocess, so the gateway does — via utils/ops_runner, which is a pure
+# subprocess shim. gate.py NEVER imports sync/ or agentic/, so out-of-band
+# isolation (and the five security invariants that rest on it) is preserved.
+#
+# Every action is: loopback-only (inherited 127.0.0.1 bind + TrustedHost
+# allow-list), rate-limited (shared _rate_limiter), API-key-gated
+# (require_api_key — uniform with /soul/* mutations; subprocess execution is more
+# sensitive than a /soul GET), and audited. A CLI that exits non-zero is reported
+# inside the JSON envelope (HTTP 200) so the UI can render exit codes / stderr;
+# only gateway-level problems (bad action -> 400, rate limit -> 429, launch
+# failure -> 500) raise HTTP errors.
+#
+# The "config" block is read from the already-parsed cfg dict (NOT an import of
+# sync/ or agentic/) so the UI can surface enabled/mode/writes_enabled — the two
+# config-driven gates of the agentic apply checklist — authoritatively.
+
+def _ops_sync_config() -> dict:
+    s = cfg.get("sync", {}) or {}
+    return {
+        "enabled": bool(s.get("enabled", False)),
+        "direction": s.get("direction", "pull"),
+        "max_delete": s.get("max_delete"),
+        "max_transfer": s.get("max_transfer"),
+        "schedule": f"{int(s.get('schedule_hour', 2)):02d}:{int(s.get('schedule_min', 0)):02d}",
+    }
+
+
+def _ops_agentic_config() -> dict:
+    a = cfg.get("agentic", {}) or {}
+    return {
+        "enabled": bool(a.get("enabled", False)),
+        "mode": a.get("mode", "read"),
+        "writes_enabled": bool(a.get("writes_enabled", False)),
+        "repo": a.get("repo", ""),
+    }
+
+
+@app.post("/ops/sync", dependencies=[Depends(require_api_key)])
+async def ops_sync(request: Request, req: OpsSyncRequest):
+    client_ip = request.client.host if request.client else "unknown"
+    if not check_rate_limit(client_ip):
+        audit_log({"event": "rate_limit_exceeded", "ip": client_ip})
+        raise HTTPException(
+            status_code=429,
+            detail={"error": "Rate limit exceeded (60/min)", "code": "RATE_LIMIT"},
+        )
+    try:
+        result = await asyncio.to_thread(run_sync_op, req.action, dry_run=req.dry_run)
+    except OpsError as e:
+        audit_log({"event": "ops_sync_rejected", "action": req.action, "error": str(e)})
+        raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
+    except Exception as e:
+        safe_msg = _sanitize_error(e)
+        audit_log({"event": "ops_sync_error", "action": req.action, "error": safe_msg})
+        raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
+    audit_log({
+        "event": "ops_sync_executed", "action": req.action, "dry_run": req.dry_run,
+        "exit_code": result.exit_code, "label": result.label,
+    })
+    payload = result.to_dict()
+    payload["config"] = _ops_sync_config()
+    return payload
+
+
+@app.post("/ops/agentic", dependencies=[Depends(require_api_key)])
+async def ops_agentic(request: Request, req: OpsAgenticRequest):
+    client_ip = request.client.host if request.client else "unknown"
+    if not check_rate_limit(client_ip):
+        audit_log({"event": "rate_limit_exceeded", "ip": client_ip})
+        raise HTTPException(
+            status_code=429,
+            detail={"error": "Rate limit exceeded (60/min)", "code": "RATE_LIMIT"},
+        )
+    try:
+        result = await asyncio.to_thread(
+            run_agentic_op, req.action,
+            pr=req.pr, issue=req.issue, no_diff=req.no_diff,
+            name=req.name, desc=req.desc, body=req.body, reason=req.reason, confirm=req.confirm,
+        )
+    except OpsError as e:
+        audit_log({"event": "ops_agentic_rejected", "action": req.action, "error": str(e)})
+        raise HTTPException(status_code=400, detail={"error": str(e), "code": "OPS_BAD_ACTION"}) from e
+    except Exception as e:
+        safe_msg = _sanitize_error(e)
+        audit_log({"event": "ops_agentic_error", "action": req.action, "error": safe_msg})
+        raise HTTPException(status_code=500, detail={"error": safe_msg, "code": "OPS_ERROR"}) from e
+    audit_log({
+        "event": "ops_agentic_executed", "action": req.action,
+        "exit_code": result.exit_code, "label": result.label,
+    })
+    payload = result.to_dict()
+    payload["config"] = _ops_agentic_config()
+    return payload
+
+
 def _is_port_in_use(host: str, port: int) -> bool:
     """Return True if a TCP listener already holds ``host:port``.
```

### `schemas/api.py` — typed request contract

```diff
diff --git a/schemas/api.py b/schemas/api.py
index 9a0a03c..df9df5d 100644
--- a/schemas/api.py
+++ b/schemas/api.py
@@ -6,7 +6,7 @@ Hardened in feature/CyClaw-Agent: strict=True + extra='forbid' on all models
 (prevents silent data injection or unexpected fields in agentic flows).
 """
 
-from typing import List, Optional
+from typing import List, Literal, Optional
 
 from pydantic import BaseModel, ConfigDict, Field
 
@@ -54,3 +54,31 @@ class SoulEvolutionRequest(BaseModel):
     model_config = ConfigDict(extra='forbid', strict=True)
     new_soul: str = Field(min_length=1, max_length=65536)
     reason: str = Field(min_length=1)
+
+
+# --- Ops console request models -------------------------------------------------
+# These back the terminal console's Sync + Agentic panels (/ops/sync, /ops/agentic).
+# action is a closed Literal so an unknown verb is rejected at the schema boundary
+# (HTTP 422) before any subprocess is spawned; extra='forbid' + strict=True block
+# silent field injection. The gateway never imports sync/ or agentic/ — it shells
+# out via utils.ops_runner — so these models are the only typed contract crossing
+# the out-of-band boundary.
+class OpsSyncRequest(BaseModel):
+    model_config = ConfigDict(extra='forbid', strict=True)
+    action: Literal["status", "test", "sync", "schedule", "unschedule"]
+    dry_run: bool = False
+
+
+class OpsAgenticRequest(BaseModel):
+    model_config = ConfigDict(extra='forbid', strict=True)
+    action: Literal["status", "test", "context", "propose-skill", "apply-skill"]
+    # context selectors
+    pr: Optional[int] = Field(default=None, ge=1)
+    issue: Optional[int] = Field(default=None, ge=1)
+    no_diff: bool = False
+    # skills-registry fields (propose-skill / apply-skill)
+    name: Optional[str] = Field(default=None, max_length=128)
+    desc: Optional[str] = Field(default=None, max_length=512)
+    body: Optional[str] = Field(default=None, max_length=65536)
+    reason: Optional[str] = Field(default=None, max_length=4096)
+    confirm: bool = False
```

### `utils/ops_runner.py` — NEW subprocess shim (never imports sync/agentic)

```diff
diff --git a/utils/ops_runner.py b/utils/ops_runner.py
new file mode 100644
index 0000000..48b3d3a
--- /dev/null
+++ b/utils/ops_runner.py
@@ -0,0 +1,214 @@
+"""utils/ops_runner.py – subprocess shim for the out-of-band ``sync`` / ``agentic`` CLIs.
+
+The FastAPI gateway exposes ``POST /ops/sync`` and ``POST /ops/agentic`` so the
+browser Soul Console can drive the two out-of-band subsystems. A browser cannot
+spawn a subprocess, so the gateway must — but the gateway must NOT *import* those
+packages, because architectural isolation of ``sync/`` and ``agentic/`` from
+``gate.py`` / ``graph.py`` / ``mcp_hybrid_server.py`` is a hard CyClaw invariant.
+
+This module is that boundary, and nothing more:
+
+* It NEVER imports ``sync`` or ``agentic``. It only builds an argv list and runs
+  it with ``subprocess.run([...])`` (list form, no shell) as
+  ``python -m sync.cli`` / ``python -m agentic.cli``.
+* It accepts only a whitelisted set of actions per subsystem. An unknown action
+  raises :class:`OpsError`, which the route maps to HTTP 400 — a caller can never
+  smuggle an arbitrary subcommand or flag through.
+* User-supplied skill bodies are written to a ``NamedTemporaryFile`` and passed
+  via ``--body-file``, never interpolated into argv.
+
+Exit codes are translated to operator-meaningful labels (see the per-subsystem
+maps below) so the UI can render failure states — a tripped ``--max-delete`` /
+``--max-transfer`` safety fuse, an env/config error, or a refused write — without
+re-deriving the meaning of each code.
+
+Exit-code contract (mirrors the docstrings in ``sync/cli.py`` / ``agentic/cli.py``):
+
+    sync:     0 ok · 10 ok+reindex-needed · 1 safety-abort · 2 failed · 3 env/config
+    agentic:  0 ok · 2 failed · 3 env/config · 4 write-refused
+"""
+
+from __future__ import annotations
+
+import json
+import subprocess  # nosec B404 - list-form only, no shell, fixed interpreter + whitelisted argv
+import sys
+import tempfile
+from dataclasses import dataclass
+from pathlib import Path
+from typing import Any
+
+# Repo root = parent of utils/. The CLIs run as ``python -m sync.cli`` /
+# ``agentic.cli``; running with cwd=repo-root puts the ``sync`` / ``agentic``
+# packages on the import path without mutating PYTHONPATH for the gateway process.
+_REPO_ROOT = Path(__file__).resolve().parent.parent
+_CONFIG_PATH = _REPO_ROOT / "config.yaml"
+_TIMEOUT_SEC = 120
+
+# action whitelists — the ONLY subcommands a caller may reach.
+_SYNC_ACTIONS = frozenset({"status", "test", "sync", "schedule", "unschedule"})
+_AGENTIC_ACTIONS = frozenset({"status", "test", "context", "propose-skill", "apply-skill"})
+# agentic subcommands that emit JSON on stdout (vs. human text).
+_AGENTIC_JSON_ACTIONS = frozenset({"context", "propose-skill", "apply-skill"})
+
+# exit code -> (ok, label)
+_SYNC_LABELS: dict[int, tuple[bool, str]] = {
+    0: (True, "ok"),
+    10: (True, "ok_reindex_needed"),
+    1: (False, "safety_abort"),
+    2: (False, "failed"),
+    3: (False, "env_config"),
+}
+_AGENTIC_LABELS: dict[int, tuple[bool, str]] = {
+    0: (True, "ok"),
+    2: (False, "failed"),
+    3: (False, "env_config"),
+    4: (False, "write_refused"),
+}
+
+
+class OpsError(ValueError):
+    """A disallowed action or malformed request. The route maps this to HTTP 400."""
+
+
+@dataclass
+class OpsResult:
+    """Normalized result of one CLI invocation, JSON-serializable for the route."""
+
+    subsystem: str
+    action: str
+    exit_code: int
+    ok: bool
+    label: str
+    stdout: str
+    stderr: str
+    parsed: Any = None
+
+    def to_dict(self) -> dict[str, Any]:
+        return {
+            "subsystem": self.subsystem,
+            "action": self.action,
+            "exit_code": self.exit_code,
+            "ok": self.ok,
+            "label": self.label,
+            "stdout": self.stdout,
+            "stderr": self.stderr,
+            "parsed": self.parsed,
+        }
+
+
+def _run(argv: list[str]) -> subprocess.CompletedProcess[str]:
+    """Run a fully-formed, whitelisted argv list. No shell, fixed interpreter."""
+    return subprocess.run(  # noqa: S603  # nosec B603 - list-form, no shell, fixed interpreter + whitelisted argv
+        argv,
+        cwd=str(_REPO_ROOT),
+        capture_output=True,
+        text=True,
+        timeout=_TIMEOUT_SEC,
+        check=False,
+    )
+
+
+def _maybe_json(text: str) -> Any:
+    """Parse JSON if the text is JSON, else return None (status/text output)."""
+    try:
+        return json.loads(text)
+    except (json.JSONDecodeError, ValueError):
+        return None
+
+
+def _write_body(body: str) -> str:
+    """Persist a skill body to a temp file so it is passed via --body-file, never argv."""
+    handle = tempfile.NamedTemporaryFile(
+        mode="w", suffix=".md", prefix="cyclaw_skill_", delete=False, encoding="utf-8"
+    )
+    try:
+        handle.write(body)
+    finally:
+        handle.close()
+    return handle.name
+
+
+def run_sync_op(action: str, *, dry_run: bool = False) -> OpsResult:
+    """Invoke ``python -m sync.cli <action>`` and normalize the result.
+
+    Only ``dry_run`` is honored, and only for the ``sync`` action (it maps to
+    ``--dry-run``). Every other action takes no caller-controlled arguments, so
+    there is no surface for argument injection.
+    """
+    if action not in _SYNC_ACTIONS:
+        raise OpsError(f"Unknown sync action: {action!r}")
+
+    argv = [sys.executable, "-m", "sync.cli", "--config", str(_CONFIG_PATH), action]
+    if action == "sync" and dry_run:
+        argv.append("--dry-run")
+
+    proc = _run(argv)
+    ok, label = _SYNC_LABELS.get(proc.returncode, (False, "unknown"))
+    return OpsResult("sync", action, proc.returncode, ok, label, proc.stdout, proc.stderr)
+
+
+def run_agentic_op(
+    action: str,
+    *,
+    pr: int | None = None,
+    issue: int | None = None,
+    no_diff: bool = False,
+    name: str | None = None,
+    desc: str | None = None,
+    body: str | None = None,
+    reason: str | None = None,
+    confirm: bool = False,
+) -> OpsResult:
+    """Invoke ``python -m agentic.cli <action>`` and normalize the result.
+
+    ``context`` takes an optional ``--pr`` / ``--issue`` selector (defaults to
+    ``--repo``). ``propose-skill`` / ``apply-skill`` require ``name`` + ``desc``;
+    ``apply-skill`` additionally requires a non-empty ``reason`` (the registry
+    governance gate) and only adds ``--confirm`` when the caller set it — calling
+    apply without confirm reaches the CLI's own refusal path (exit 4), which is
+    surfaced verbatim rather than masked.
+
+    Validation raises happen before the subprocess launch. All ``proc`` usage
+    lives INSIDE the try so there is no post-``finally`` reference to an unbound
+    name: if ``_run`` raises (e.g. ``subprocess.TimeoutExpired``), the ``finally``
+    cleans up the temp body-file and the exception propagates before any result is
+    read. The body-file is unlinked on every exit path (return or raise).
+    """
+    if action not in _AGENTIC_ACTIONS:
+        raise OpsError(f"Unknown agentic action: {action!r}")
+    if action in {"propose-skill", "apply-skill"} and (not name or not desc):
+        raise OpsError(f"{action} requires both name and desc")
+    if action == "apply-skill" and not (reason and reason.strip()):
+        raise OpsError("apply-skill requires a non-empty reason")
+
+    argv = [sys.executable, "-m", "agentic.cli", "--config", str(_CONFIG_PATH), action]
+    body_file: str | None = None
+    try:
+        if action == "context":
+            if pr is not None:
+                argv += ["--pr", str(pr)]
+            elif issue is not None:
+                argv += ["--issue", str(issue)]
+            else:
+                argv.append("--repo")
+            if no_diff:
+                argv.append("--no-diff")
+        elif action in {"propose-skill", "apply-skill"}:
+            # name/desc validated above; both are required, so they are non-None here.
+            argv += ["--name", str(name), "--desc", str(desc)]
+            if body:
+                body_file = _write_body(body)
+                argv += ["--body-file", body_file]
+            if reason:
+                argv += ["--reason", reason]
+            if action == "apply-skill" and confirm:
+                argv.append("--confirm")
+
+        proc = _run(argv)
+        ok, label = _AGENTIC_LABELS.get(proc.returncode, (False, "unknown"))
+        parsed = _maybe_json(proc.stdout) if (ok and action in _AGENTIC_JSON_ACTIONS) else None
+        return OpsResult("agentic", action, proc.returncode, ok, label, proc.stdout, proc.stderr, parsed)
+    finally:
+        if body_file:
+            Path(body_file).unlink(missing_ok=True)
```

### `tests/test_ops_runner.py` — NEW (25 tests; includes a hermetic isolation proof)

```diff
diff --git a/tests/test_ops_runner.py b/tests/test_ops_runner.py
new file mode 100644
index 0000000..7813dab
--- /dev/null
+++ b/tests/test_ops_runner.py
@@ -0,0 +1,190 @@
+"""Tests for utils.ops_runner — the subprocess shim behind /ops/sync and /ops/agentic.
+
+These never spawn the real CLIs: ``_run`` is monkeypatched to capture the argv the
+shim builds and to return a synthetic ``CompletedProcess``. The one exception is the
+isolation test, which spins a clean interpreter to prove importing the shim never
+imports the out-of-band packages.
+"""
+
+from __future__ import annotations
+
+import subprocess
+import sys
+
+import pytest
+
+from utils import ops_runner
+from utils.ops_runner import OpsError, run_agentic_op, run_sync_op
+
+
+def _fake_run(returncode: int = 0, stdout: str = "", stderr: str = ""):
+    """Return a (_run replacement, captured-argv list) pair."""
+    captured: list[list[str]] = []
+
+    def _runner(argv: list[str]) -> subprocess.CompletedProcess[str]:
+        captured.append(argv)
+        return subprocess.CompletedProcess(args=argv, returncode=returncode, stdout=stdout, stderr=stderr)
+
+    return _runner, captured
+
+
+# --------------------------------------------------------------------------- sync
+
+
+def test_sync_unknown_action_raises() -> None:
+    with pytest.raises(OpsError):
+        run_sync_op("rm-rf")
+
+
+def test_sync_status_argv(monkeypatch: pytest.MonkeyPatch) -> None:
+    runner, captured = _fake_run(returncode=0, stdout="ok")
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    res = run_sync_op("status")
+    argv = captured[0]
+    assert argv[0] == sys.executable
+    assert argv[1:3] == ["-m", "sync.cli"]
+    assert "--config" in argv and argv[-1] == "status"
+    assert res.ok is True and res.label == "ok" and res.exit_code == 0
+
+
+def test_sync_dry_run_appends_flag(monkeypatch: pytest.MonkeyPatch) -> None:
+    runner, captured = _fake_run()
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    run_sync_op("sync", dry_run=True)
+    assert captured[0][-1] == "--dry-run"
+    assert captured[0][-2] == "sync"
+
+
+def test_sync_dry_run_ignored_for_non_sync(monkeypatch: pytest.MonkeyPatch) -> None:
+    runner, captured = _fake_run()
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    run_sync_op("status", dry_run=True)  # dry_run must NOT leak onto status
+    assert "--dry-run" not in captured[0]
+
+
+@pytest.mark.parametrize(
+    "code,ok,label",
+    [(0, True, "ok"), (10, True, "ok_reindex_needed"), (1, False, "safety_abort"),
+     (2, False, "failed"), (3, False, "env_config"), (99, False, "unknown")],
+)
+def test_sync_exit_code_labels(monkeypatch: pytest.MonkeyPatch, code: int, ok: bool, label: str) -> None:
+    runner, _ = _fake_run(returncode=code, stderr="boom" if code else "")
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    res = run_sync_op("sync")
+    assert res.ok is ok and res.label == label and res.exit_code == code
+
+
+# ------------------------------------------------------------------------- agentic
+
+
+def test_agentic_unknown_action_raises() -> None:
+    with pytest.raises(OpsError):
+        run_agentic_op("delete-repo")
+
+
+def test_agentic_context_repo_default(monkeypatch: pytest.MonkeyPatch) -> None:
+    runner, captured = _fake_run(returncode=0, stdout='{"repo": "x"}')
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    res = run_agentic_op("context")
+    assert "--repo" in captured[0]
+    assert res.parsed == {"repo": "x"}  # JSON action parsed on success
+
+
+def test_agentic_context_pr_and_issue(monkeypatch: pytest.MonkeyPatch) -> None:
+    runner, captured = _fake_run(returncode=0, stdout="{}")
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    run_agentic_op("context", pr=42)
+    assert captured[0][-2:] == ["--pr", "42"]
+    captured.clear()
+    run_agentic_op("context", issue=7, no_diff=True)
+    assert "--issue" in captured[0] and "7" in captured[0] and "--no-diff" in captured[0]
+
+
+def test_agentic_propose_requires_name_desc() -> None:
+    with pytest.raises(OpsError):
+        run_agentic_op("propose-skill", name="x")  # missing desc
+
+
+def test_agentic_apply_requires_reason() -> None:
+    with pytest.raises(OpsError):
+        run_agentic_op("apply-skill", name="x", desc="y", reason="   ")
+
+
+def test_agentic_apply_confirm_and_body_file(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
+    runner, captured = _fake_run(returncode=0, stdout='{"status": "applied"}')
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    res = run_agentic_op(
+        "apply-skill", name="demo", desc="a demo skill", body="# body\ncontent",
+        reason="adding demo", confirm=True,
+    )
+    argv = captured[0]
+    assert "--name" in argv and "demo" in argv
+    assert "--confirm" in argv
+    # body routed through a temp --body-file, never inlined as an argv token
+    assert "--body-file" in argv
+    body_idx = argv.index("--body-file") + 1
+    assert "# body" not in argv  # the literal body is not on the command line
+    # the temp file is cleaned up after the run
+    from pathlib import Path
+    assert not Path(argv[body_idx]).exists()
+    assert res.parsed == {"status": "applied"}
+
+
+def test_agentic_apply_no_confirm_omits_flag(monkeypatch: pytest.MonkeyPatch) -> None:
+    runner, captured = _fake_run(returncode=4, stderr="apply-skill requires --confirm")
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    res = run_agentic_op("apply-skill", name="x", desc="y", reason="r", confirm=False)
+    assert "--confirm" not in captured[0]
+    assert res.exit_code == 4 and res.label == "write_refused" and res.ok is False
+
+
+@pytest.mark.parametrize(
+    "code,ok,label",
+    [(0, True, "ok"), (2, False, "failed"), (3, False, "env_config"),
+     (4, False, "write_refused"), (77, False, "unknown")],
+)
+def test_agentic_exit_code_labels(monkeypatch: pytest.MonkeyPatch, code: int, ok: bool, label: str) -> None:
+    runner, _ = _fake_run(returncode=code, stdout="{}" if code == 0 else "")
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    res = run_agentic_op("status")
+    assert res.ok is ok and res.label == label
+
+
+def test_agentic_text_action_not_parsed(monkeypatch: pytest.MonkeyPatch) -> None:
+    # status emits human text, not JSON — parsed must stay None even on success.
+    runner, _ = _fake_run(returncode=0, stdout="  enabled... False")
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    res = run_agentic_op("status")
+    assert res.parsed is None
+
+
+def test_to_dict_shape(monkeypatch: pytest.MonkeyPatch) -> None:
+    runner, _ = _fake_run(returncode=0, stdout="ok")
+    monkeypatch.setattr(ops_runner, "_run", runner)
+    d = run_sync_op("status").to_dict()
+    assert set(d) == {"subsystem", "action", "exit_code", "ok", "label", "stdout", "stderr", "parsed"}
+
+
+# ----------------------------------------------------------------------- isolation
+
+
+def test_importing_shim_does_not_import_out_of_band_packages() -> None:
+    """Hard invariant: importing the shim must not import sync/ or agentic/.
+
+    Run in a clean interpreter so prior test imports cannot mask a regression.
+    """
+    code = (
+        "import sys; import utils.ops_runner; "
+        "assert 'sync' not in sys.modules, 'ops_runner imported sync'; "
+        "assert 'agentic' not in sys.modules, 'ops_runner imported agentic'; "
+        "print('ISOLATED_OK')"
+    )
+    proc = subprocess.run(
+        [sys.executable, "-c", code],
+        cwd=str(ops_runner._REPO_ROOT),
+        capture_output=True,
+        text=True,
+        check=False,
+    )
+    assert proc.returncode == 0, proc.stderr
+    assert "ISOLATED_OK" in proc.stdout
```

---

## 5. Rationale — how this preserves all 9 constraints + improves operator UX

This integration adds an operator control surface for the two out-of-band subsystems
**without weakening a single invariant**: the new `/ops/sync` and `/ops/agentic` routes are
*thin subprocess shims* (`utils/ops_runner.py`) that build a **whitelisted argv** and call
`subprocess.run([...])` in list form (no `shell=True`, skill bodies via a temp
`--body-file`), so `gate.py` never imports `sync/` or `agentic/` (#6) — proven at runtime
(`sync`/`agentic` absent from `sys.modules` after `import gate`) and statically (no import
lines); the change touches no graph node, so RAG-First (#1), Topology=Policy (#2), and the
triple-gated Grok path (#3) are untouched; every route emits an `audit.jsonl` entry
(`ops_sync_executed` / `ops_agentic_executed` / `ops_*_rejected`) before returning, keeping
audit convergence intact (#4, #9); no soul write path is added (#5); the agentic GitHub-write
scaffold stays dry-run (`writer.py` `EXECUTION_ENABLED` hard-False, never reached by the CLI)
(#7); and the UI **visibly** reflects `writes_enabled:false` by hard-disabling Apply behind a
4-gate checklist driven by the authoritative config block (#8). Operator UX improves because
sync/agentic ops are now one click away with explicit, color-coded exit-code feedback
(safety-fuse abort, env/config error, write refused) and a governance score on every skill
proposal — all gated behind the same API key as soul mutations.

> **⚠️ Design tension flagged (honest disclosure):** The task's 4-gate checklist
> (`mode=write`, `writes_enabled`, `reason`, `--confirm`) is the `writer.py::plan_write()`
> gate for **GitHub writes** — which the CLI never reaches. The CLI's `apply-skill` writes
> the **local skills registry** (`registry.apply_skill()`), governed by reason + injection
> scan + `--confirm` only (it does *not* check `mode`/`writes_enabled`). My resolution
> **overlays the writer.py 4-gate as a UI governance layer** on top of the registry's own
> governance — strictly *stricter* than the backend, never weaker. Net effect: with the
> shipped config the UI cannot apply at all (dry-run only), exactly the safe posture
> constraints 7–8 intend. An operator who deliberately sets `mode: write` +
> `writes_enabled: true` and restarts unlocks a real, governed *registry* write (not a
> GitHub write — that remains stubbed).

---

## 6. Next 3 concrete steps after applying

### Step 1 — run the test + lint + type gates (Python 3.12)
```bash
python3.12 -m venv .venv && . .venv/bin/activate     # if not already
pip install torch==2.6.0+cpu --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.txt --ignore-installed PyYAML
GROK_API_KEY=dummy pytest tests/test_ops_runner.py tests/test_gate.py -q --tb=short
GROK_API_KEY=dummy pytest tests/ -q                  # full suite (expect all green)
ruff check utils/ops_runner.py schemas/api.py tests/test_ops_runner.py
mypy --strict --python-version 3.12 utils/ops_runner.py
```

### Step 2 — verify the live endpoints + audit trail
```bash
export CYCLAW_API_KEY=$(python -c "import secrets;print(secrets.token_urlsafe(24))")
python -m retrieval.indexer            # build the index if needed
cyclaw-server &                        # or: python gate.py
# In the browser at http://127.0.0.1:8787 : paste the key into the API-key field,
# toggle Sync Console -> Status, Agentic Console -> Status/Propose Skill.
curl -s -X POST 127.0.0.1:8787/ops/sync   -H "Authorization: Bearer $CYCLAW_API_KEY" \
     -H 'Content-Type: application/json' -d '{"action":"status"}' | jq
tail -5 logs/audit.jsonl | jq           # confirm ops_sync_executed / ops_agentic_executed
```

### Step 3 — exercise the failure + governance paths
```bash
# agentic apply WITHOUT confirm -> exit 4 WRITE REFUSED rendered in the panel:
curl -s -X POST 127.0.0.1:8787/ops/agentic -H "Authorization: Bearer $CYCLAW_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"action":"apply-skill","name":"x","desc":"a demo skill","reason":"r","confirm":false}' | jq '.exit_code,.label'
# propose-skill -> governance_score in .parsed.governance_score
```

### Things to verify
- `logs/audit.jsonl` gains an entry **per** ops click.
- Agentic Apply stays **disabled** until `mode=write` + `writes_enabled=true` (config) AND
  reason + confirm (UI) — i.e. it cannot fire under the shipped config.
- `import gate` does **not** import `sync`/`agentic` (the test asserts this).

### Top 2 pitfalls to watch
1. **Isolation regression.** Anyone "simplifying" `ops_runner` by importing `sync.cli` /
   `agentic.cli` directly (instead of subprocess) silently breaks constraint #6 and the five
   invariants that rest on it. The guard is `tests/test_ops_runner.py::
   test_importing_shim_does_not_import_out_of_band_packages` — keep it green.
2. **Subprocess argv injection.** The shim must stay **list-form** `subprocess.run([...])`
   with no `shell=True`; skill bodies must keep flowing through the temp `--body-file`, never
   interpolated into argv. The action whitelist + Pydantic `Literal` are the two gates that
   keep an arbitrary subcommand/flag from ever reaching `subprocess`.

---

## Verification performed (Python 3.12.3)

| Check | Result |
|---|---|
| `py_compile` gate/schemas/ops_runner/tests | ✅ |
| `ruff check` (ops_runner + tests) | ✅ clean |
| `mypy --strict` ops_runner | ✅ no issues |
| Full `pytest tests/` | ✅ **450 passed** |
| `test_ops_runner.py` | ✅ 25 passed |
| Endpoint emulation (`/`, `/health`, `/query` vault-hit, `/ops/sync`, `/ops/agentic`, `/audit/summary`) | ✅ all 200 |
| Audit entries (`ops_sync_executed`, `ops_agentic_executed`, `ops_agentic_rejected`) | ✅ written |
| Isolation (`sync`/`agentic` not in `sys.modules` after `import gate`) | ✅ proven |
| `governance_score` surfaced via propose-skill | ✅ 100/100 |
| JS syntax (`node --check`) + handler/id static cross-check | ✅ clean |

---

## origin/main drift since the original plan (assessed before implementing)

`static/terminal.html`, `gate.py`, `config.yaml`, `sync/cli.py`, `agentic/cli.py`,
`agentic/writer.py` were **all unchanged** — the anchor pattern, route patterns, CLI surface,
and exit codes held. Favorable drift incorporated:
- `agentic/registry.py`: `propose_skill` now scores the **proposed** spec → governance_score
  is meaningful even for new skills (surfaced in the panel).
- `schemas/api.py`: `SoulEvolutionRequest` gained `Field(min_length=...)` → mirrored in the
  new Ops models.
- `pyproject.toml`: `--cov` dropped from `addopts` → targeted test runs don't fail on global
  coverage.

---

## Adversarial review (4 reviewers + verify phase)

A workflow ran four independent reviewers (invariants, security, UI-fidelity, correctness);
each finding was then adversarially verified. **Result: zero real, in-scope defects.**

**Confirmed-good (sampling of the positive findings):** architectural isolation preserved
(no `sync`/`agentic` import into `gate.py`); subprocess strictly list-form with body via
temp `--body-file`; action whitelist enforced at both schema (`Literal`) and function
boundaries; `require_api_key` + rate-limit on both routes; `pr`/`issue` validated `ge=1`;
XSS-safe (`textContent` for CLI output); errors sanitized; audit logging comprehensive;
`strict=True` + `extra='forbid'` block field injection.

**Two non-low findings raised — both addressed and then verified `real=False`:**
1. *(medium)* "HTML comment misleads about dry-run behavior." → Comment reworded to state
   the gate is a **UI overlay** that disables Apply under shipped defaults (verifier then
   judged the corrected text not-an-issue).
2. *(high ×2)* "Unbound `proc` if `subprocess.run` raises." → Not a runtime bug (the
   exception propagates through `finally` before any read; mypy --strict agreed), but
   `run_agentic_op` was restructured to move all `proc` usage **inside** the `try` so the
   pattern is unambiguously correct. Verifier judged the original `real=False` as well.
