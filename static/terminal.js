const API = window.location.origin;
const apiKeyInput = document.getElementById('apiKeyInput');
const resultsEl = document.getElementById('results');
const emptyState = document.getElementById('emptyState');
const input = document.getElementById('queryInput');
const sendBtn = document.getElementById('sendBtn');
const statusDot = document.getElementById('statusDot');
const statusText = document.getElementById('statusText');
const modeBadge = document.getElementById('modeBadge');
const footerRight = document.getElementById('footerRight');
const soulPanel = document.getElementById('soulPanel');
const soulEditor = document.getElementById('soulEditor');
const soulReason = document.getElementById('soulReason');
const soulStatus = document.getElementById('soulStatus');
const soulVersion = document.getElementById('soulVersion');
const soulSource = document.getElementById('soulSource');
const proposalBox = document.getElementById('proposalBox');
const proposalMeta = document.getElementById('proposalMeta');
const proposalWarning = document.getElementById('proposalWarning');
const proposalPreview = document.getElementById('proposalPreview');
const soulToggleBtn = document.getElementById('soulToggleBtn');

// Sync + Agentic console refs (mirror the soul console ref/element naming).
const syncPanel = document.getElementById('syncPanel');
const syncStatus = document.getElementById('syncStatus');
const syncBox = document.getElementById('syncBox');
const syncMeta = document.getElementById('syncMeta');
const syncWarning = document.getElementById('syncWarning');
const syncPreview = document.getElementById('syncPreview');
const syncToggleBtn = document.getElementById('syncToggleBtn');
const agenticPanel = document.getElementById('agenticPanel');
const agenticStatus = document.getElementById('agenticStatus');
const agenticBox = document.getElementById('agenticBox');
const agenticMeta = document.getElementById('agenticMeta');
const agenticWarning = document.getElementById('agenticWarning');
const agenticPreview = document.getElementById('agenticPreview');
const agenticToggleBtn = document.getElementById('agenticToggleBtn');
const agenticConfirm = document.getElementById('agenticConfirm');
const agenticApplyBtn = document.getElementById('agenticApplyBtn');

// FS + SQL console refs (mirror sync/agentic naming).
const fsPanel = document.getElementById('fsPanel');
const fsStatus = document.getElementById('fsStatus');
const fsBox = document.getElementById('fsBox');
const fsMeta = document.getElementById('fsMeta');
const fsWarning = document.getElementById('fsWarning');
const fsPreview = document.getElementById('fsPreview');
const fsToggleBtn = document.getElementById('fsToggleBtn');
const sqlPanel = document.getElementById('sqlPanel');
const sqlStatus = document.getElementById('sqlStatus');
const sqlBox = document.getElementById('sqlBox');
const sqlMeta = document.getElementById('sqlMeta');
const sqlWarning = document.getElementById('sqlWarning');
const sqlPreview = document.getElementById('sqlPreview');
const sqlToggleBtn = document.getElementById('sqlToggleBtn');

let queryCount = 0;
// Client-side /query abort, in ms. MUST stay above the server's graph_timeout_sec
// (api.graph_timeout_sec) so the browser never aborts first — otherwise the user
// sees a generic client timeout instead of the server's truthful 504 GRAPH_TIMEOUT
// message. Synced from /health (graph_timeout_sec + 10s buffer); the default here
// already clears the 780s server default in case /health hasn't responded yet.
// That fallback is load-bearing whenever /health is slow or failing — its own
// fetch aborts at 3s while the server-side probe allows 5s, so a still-loading
// or unreachable Ollama is exactly the case where the sync never runs.
let queryDeadlineMs = 790000;
// Map<entryId, queryText>. A single global string was overwritten by each new
// low-confidence query, so approving a stale prompt submitted the wrong text.
const pendingConfirmById = new Map();
let pendingSoulProposal = null;
let entryCounter = 0;
let activeQueryController = null;
let healthBackoffMs = 15000;
const HEALTH_BASE_INTERVAL = 15000;
const HEALTH_MAX_INTERVAL = 120000;

function authHeaders() {
  const key = apiKeyInput ? apiKeyInput.value.trim() : '';
  const h = { 'Content-Type': 'application/json' };
  if (key) h['Authorization'] = `Bearer ${key}`;
  return h;
}

// /query uses the HttpOnly cyclaw_session cookie (same-origin), not the
// shared ops API key. Sending CYCLAW_API_KEY as Bearer here would be
// treated as a device token and 401 once auth.enabled is on.
function queryHeaders() {
  return { 'Content-Type': 'application/json' };
}

let csrfToken = null;
let authRole = null;
const authSetupBox = document.getElementById('authSetupBox');
const authLoginBox = document.getElementById('authLoginBox');
const authSessionBox = document.getElementById('authSessionBox');
const authUserInput = document.getElementById('authUser');
const authPassInput = document.getElementById('authPass');
const authSetupPass = document.getElementById('authSetupPass');
const authSetupPass2 = document.getElementById('authSetupPass2');
const authStatus = document.getElementById('authStatus');

function hideAuthBoxes() {
  if (authSetupBox) authSetupBox.hidden = true;
  if (authLoginBox) authLoginBox.hidden = true;
  if (authSessionBox) authSessionBox.hidden = true;
}

async function refreshAuthUi() {
  try {
    const resp = await fetchWithTimeout(`${API}/auth/whoami`, {}, 5000);
    if (resp.status === 503) {
      hideAuthBoxes();
      csrfToken = null;
      authRole = null;
      applyRoleChrome();
      return;
    }
    if (resp.ok) {
      const data = await resp.json();
      hideAuthBoxes();
      if (authSessionBox) authSessionBox.hidden = false;
      authRole = data.role || null;
      // whoami rotates cookie-session CSRF and returns the new plaintext.
      // Without this assignment a reload keeps the session cookie (looks
      // logged in) but csrfToken stays null, so logout and Users writes 403.
      csrfToken = data.csrf_token || null;
      if (authStatus) authStatus.textContent = (data.username || '') + (authRole ? ' · ' + authRole : '');
      applyRoleChrome();
      return;
    }
    csrfToken = null;
    authRole = null;
    applyRoleChrome();
    const setup = await fetchWithTimeout(`${API}/auth/setup-status`, {}, 5000);
    if (setup.ok) {
      const status = await setup.json();
      if (status.needs_password) {
        if (authSetupBox) authSetupBox.hidden = false;
        if (authLoginBox) authLoginBox.hidden = true;
        if (authSessionBox) authSessionBox.hidden = true;
        return;
      }
    }
    if (authSetupBox) authSetupBox.hidden = true;
    if (authLoginBox) authLoginBox.hidden = false;
    if (authSessionBox) authSessionBox.hidden = true;
  } catch {
    // Leave the last-known UI; /health already reports reachability.
  }
}

async function setupAdminPassword() {
  if (!authSetupPass || !authSetupPass2) return;
  const password = authSetupPass.value;
  const confirm = authSetupPass2.value;
  authSetupPass.value = '';
  authSetupPass2.value = '';
  if (password !== confirm) {
    if (authStatus) {
      if (authSessionBox) authSessionBox.hidden = false;
      authStatus.textContent = 'passwords do not match';
    }
    return;
  }
  try {
    const resp = await fetchWithTimeout(`${API}/auth/bootstrap-password`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ password }),
    }, 15000);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      if (authStatus) {
        if (authSessionBox) authSessionBox.hidden = false;
        authStatus.textContent = extractErrorMessage(err, 'could not set password');
      }
      return;
    }
    const data = await resp.json();
    csrfToken = data.csrf_token || null;
    await refreshAuthUi();
  } catch (e) {
    if (authStatus) {
      if (authSessionBox) authSessionBox.hidden = false;
      authStatus.textContent = 'network error: could not set password';
    }
  }
}

async function login() {
  if (!authUserInput || !authPassInput) return;
  const username = authUserInput.value.trim();
  const password = authPassInput.value;
  authPassInput.value = '';
  try {
    const resp = await fetchWithTimeout(`${API}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }, 15000);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      if (authStatus) {
        if (authSessionBox) authSessionBox.hidden = false;
        authStatus.textContent = extractErrorMessage(err, 'login failed');
      }
      return;
    }
    const data = await resp.json();
    csrfToken = data.csrf_token || null;
    await refreshAuthUi();
  } catch (e) {
    if (authStatus) {
      if (authSessionBox) authSessionBox.hidden = false;
      authStatus.textContent = 'network error: login failed';
    }
  }
}

async function logout() {
  const headers = { 'Content-Type': 'application/json' };
  if (csrfToken) headers['X-CyClaw-CSRF'] = csrfToken;
  try {
    await fetchWithTimeout(`${API}/auth/logout`, { method: 'POST', headers }, 15000);
  } catch (e) {
    // Best-effort: the server may already be unreachable.
  }
  csrfToken = null;
  await refreshAuthUi();
}

async function fetchWithTimeout(url, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    window.clearTimeout(timer);
  }
}

input.addEventListener('input', () => {
  input.style.height = 'auto';
  input.style.height = Math.min(input.scrollHeight, 120) + 'px';
});

input.addEventListener('keydown', (e) => {
  // An IME (CJK, and any other composition-based input) fires Enter to commit
  // the candidate that is still being composed. Sending on that Enter submits a
  // half-typed query and eats the keystroke the operator meant for the IME.
  if (e.isComposing) return;
  // Enter sends; Shift+Enter inserts a newline. Ctrl/Cmd+Enter is kept as a
  // power-user alias for send. Shift is the only modifier that suppresses send.
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    submitQuery();
  }
});

function extractErrorMessage(err, fallback = 'Unknown error') {
  if (!err) return fallback;
  if (typeof err === 'string') return err;
  if (err.detail && typeof err.detail === 'string') return err.detail;
  if (err.detail && typeof err.detail === 'object') return err.detail.error || err.detail.message || fallback;
  return err.error || err.message || fallback;
}

// ── /query ERROR COPY ──
// Plain-language sentences for the codes that can actually reach a /query
// user: the ~10 raised as HTTPException (gate.py) plus the 4 that arrive as a
// 200 with an `error` field carrying graph.py's "{code}: {message}" stamp
// (utils/errors.py's LLMServiceError / GrokServiceError / ClaudeServiceError /
// EmbeddingServiceError). utils/errors.py declares dozens of other classes --
// soul, ops, agentic, fsconnect, sqlconnect, auth-admin -- none of which this
// console's /query path can ever surface, so they have no entry here. An
// unmapped code (or none at all) falls back to the raw server message rather
// than a translation, per describeQueryError below.
const ERROR_COPY = {
  INDEX_NOT_FOUND: "Your document library isn't built yet.",
  PROMPT_INJECTION_BLOCKED: 'That message looked like it was trying to manipulate the system, so it was blocked before reaching the assistant.',
  GRAPH_TIMEOUT: 'The local model took too long to answer -- it may be stalled.',
  GRAPH_ERROR: 'Something went wrong answering that.',
  RATE_LIMIT: 'Too many requests -- wait a moment and try again.',
  VALIDATION_ERROR: "That request wasn't formatted correctly.",
  PAYLOAD_TOO_LARGE: 'That message is too long.',
  AUTH_ROLE_DENIED: "This account can't ask questions (audit-only role).",
  CROSS_SITE_BLOCKED: "Blocked a request that didn't look like it came from this page.",
  AUTH_REQUIRED: 'Sign in to ask a question.',
  EMBEDDING_ERROR: "Couldn't search your documents right now.",
  LLM_SERVICE_ERROR: 'The local model had a problem answering.',
  GROK_SERVICE_ERROR: 'Grok had a problem answering.',
  CLAUDE_SERVICE_ERROR: 'Claude had a problem answering.'
};

// err is either a fetch-failure response body (object, code nested under
// .detail or top-level) or graph.py's stamped "{code}: {message}" string (the
// 200-with-error case) -- the two shapes extractErrorMessage already
// normalizes for the message half, but it discards the code, which is what
// this adds.
function extractErrorCode(err) {
  if (!err) return null;
  if (typeof err === 'string') {
    const m = err.match(/^([A-Z][A-Z0-9_]*): /);
    return m ? m[1] : null;
  }
  if (err.detail && typeof err.detail === 'object') return err.detail.code || null;
  return err.code || null;
}

// The stamped-string shape's message repeats the code as a prefix
// ("LLM_SERVICE_ERROR: Ollama timed out") -- strip it once the code is shown
// on its own, so it isn't printed twice.
function stripErrorCodePrefix(message, code) {
  return code && message.startsWith(`${code}: `) ? message.slice(code.length + 2) : message;
}

function describeQueryError(err) {
  const code = extractErrorCode(err);
  const message = stripErrorCodePrefix(extractErrorMessage(err, 'Unknown error'), code);
  return { text: (code && ERROR_COPY[code]) || message, code, message };
}

function describeApiKeyError(err, fallback) {
  const message = extractErrorMessage(err, fallback);
  if (message.indexOf('CYCLAW_API_KEY not set') !== -1 || message.indexOf('Soul mutation disabled') !== -1) {
    return 'The gateway was started without CYCLAW_API_KEY. Typing the key in this box only works when the server process already has the same key. Source ~/.CyClaw/.env (or set the env var) and restart, then paste it here.';
  }
  if (message === 'Invalid or missing API key' || message.indexOf('Invalid or missing API key') !== -1) {
    return 'That API key does not match the server. Check the key field, or restart the gateway after sourcing ~/.CyClaw/.env.';
  }
  return message;
}

function setSoulStatus(message, tone = '') {
  soulStatus.textContent = message || '';
  soulStatus.className = `soul-status${tone ? ` ${tone}` : ''}`;
}

// ── FIRST RUN ──
// Before this, a missing index meant POST /query answered 503 and the console
// rendered it verbatim: "ERROR 503: Index not built. Run: python -m
// retrieval.indexer" -- a CLI command, in a browser, to someone who may not
// have a terminal open. /health has always carried index_ready; the console
// just never read it. Now the empty state becomes an actionable panel instead.
const indexBuild = { state: 'idle', timer: null, misses: 0 };
const INDEX_POLL_MS = 1500;
// A dropped poll is not a failed build, so the poll retries -- but it needs a
// ceiling. Without one, a gateway that dies mid-build leaves the tab hammering
// /index/status every 1.5s forever while the panel still reads "Building your
// library", telling the operator nothing is wrong. 20 x 1.5s = ~30s of silence
// before we admit we've lost contact, which comfortably outlasts a restart.
const INDEX_POLL_MAX_MISSES = 20;

function renderFirstRun(data) {
  const emptyState = document.getElementById('emptyState');
  // The empty state is REMOVED (not hidden) on the first query, so once a user
  // has asked anything there is nothing to render into -- and by then they are
  // past first run anyway.
  if (!emptyState) return;
  let panel = document.getElementById('firstRunPanel');
  const needed = data && data.index_ready === false;

  if (!needed && indexBuild.state !== 'running' && indexBuild.state !== 'error') {
    if (panel) panel.remove();
    return;
  }
  if (!panel) {
    panel = document.createElement('div');
    panel.className = 'first-run';
    panel.id = 'firstRunPanel';
    emptyState.appendChild(panel);
  }
  paintFirstRun(panel, data);
}

function paintFirstRun(panel, data) {
  // textContent throughout, never innerHTML: corpus_path is server-supplied
  // and the build error is an exception message. Neither is trusted markup.
  panel.textContent = '';

  const title = document.createElement('div');
  title.className = 'first-run-title';
  const body = document.createElement('div');
  body.className = 'first-run-body';

  if (indexBuild.state === 'running') {
    title.textContent = 'Building your library…';
    body.textContent = indexBuild.total
      ? `${indexBuild.done} of ${indexBuild.total} sections indexed.`
      : 'Reading your documents. This can take a few minutes the first time.';
    panel.append(title, body);
    return;
  }

  if (indexBuild.state === 'error') {
    title.textContent = "That didn't work";
    body.textContent = indexBuild.error || 'The build stopped before it finished.';
    panel.append(title, body, buildButton('Try again'));
    return;
  }

  title.textContent = 'Point me at your documents';
  const folder = (data && data.corpus_path) || '';
  body.textContent = folder
    ? `Put your files in ${folder}, then build a searchable copy. `
      + 'Nothing is uploaded — the copy stays on this machine.'
    : 'Build a searchable copy of your documents. Nothing is uploaded — '
      + 'the copy stays on this machine.';
  panel.append(title, body, buildButton('Build my library'));
}

function buildButton(label) {
  const btn = document.createElement('button');
  btn.className = 'first-run-btn';
  btn.id = 'buildIndexBtn';
  btn.type = 'button';
  btn.textContent = label;
  btn.addEventListener('click', () => startIndexBuild());
  return btn;
}

async function startIndexBuild() {
  const btn = document.getElementById('buildIndexBtn');
  if (btn) btn.disabled = true;   // the server also 409s a second build
  indexBuild.state = 'running';
  indexBuild.done = 0;
  indexBuild.total = 0;
  indexBuild.error = null;
  indexBuild.misses = 0;   // Try again must not inherit the previous run's streak
  paintHealthStatus();
  renderFirstRun(lastHealth);
  try {
    // Bounded like every other call in this file. A bare fetch that never
    // settles would strand the panel: state is already 'running' above, and
    // renderFirstRun's running branch draws no button, so there would be no
    // Try again affordance and no error -- just "Building your library"
    // forever. The route only starts a thread and returns, so 15s is generous.
    const resp = await fetchWithTimeout(`${API}/index/build`, { method: 'POST' }, 15000);
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      throw new Error(extractErrorMessage(err, `build failed (${resp.status})`));
    }
    pollIndexStatus();
  } catch (e) {
    indexBuild.state = 'error';
    indexBuild.error = e.message;
    paintHealthStatus();
    renderFirstRun(lastHealth);
  }
}

function pollIndexStatus() {
  clearTimeout(indexBuild.timer);
  indexBuild.timer = setTimeout(async () => {
    try {
      const resp = await fetchWithTimeout(`${API}/index/status`, {}, 3000);
      // Mirror checkHealth's guard: a JSON-bodied non-2xx (a 429 from the
      // front-running rate limiter, a 503) must not reach the state machine --
      // s.state comes back undefined, and the assignment below reads that as
      // a FAILED build while the build keeps running server-side. Throwing
      // routes it through the miss counter instead: transient, retried, and
      // bounded by INDEX_POLL_MAX_MISSES like any other dropped poll.
      if (!resp.ok) throw new Error(`index status ${resp.status}`);
      const s = await resp.json();
      indexBuild.misses = 0;   // a reachable server clears the miss streak
      indexBuild.done = s.chunks_done || 0;
      indexBuild.total = s.chunks_total || 0;
      if (s.state === 'running') {
        paintHealthStatus();
        renderFirstRun(lastHealth);
        pollIndexStatus();
        return;
      }
      indexBuild.state = s.state === 'done' ? 'idle' : 'error';
      indexBuild.error = s.error;
      if (s.state === 'done') {
        addEntry('system', '', '→ Your library is ready. Ask a question below.');
      }
      // The server hot-inits retrieval on success, so re-reading /health both
      // clears the panel and flips the status light in one round trip.
      checkHealth();
    } catch (e) {
      // A dropped poll is not a failed build -- the build runs server-side and
      // keeps going. Retry rather than declaring failure, but stop after
      // INDEX_POLL_MAX_MISSES so a dead gateway surfaces instead of polling
      // silently forever behind a "Building your library" panel.
      indexBuild.misses += 1;
      if (indexBuild.misses >= INDEX_POLL_MAX_MISSES) {
        indexBuild.state = 'error';
        indexBuild.error = "Lost contact with CyClaw while building. The build may still be running — reload to check.";
        paintHealthStatus();
        renderFirstRun(lastHealth);
        return;
      }
      pollIndexStatus();
    }
  }, INDEX_POLL_MS);
}

// ── PLAIN-LANGUAGE HEALTH ──
// /health speaks operator. "degraded" is NORMAL without Ollama (CLAUDE.md §4
// says so explicitly) yet it painted the dot the same red as a hard failure,
// and "gateway degraded" tells someone who is not an engineer nothing about
// what to do next. The response already carries everything needed to say
// something specific -- per-service {healthy, error} plus index_ready --
// and the console was fetching all of it and discarding it.
//
// Order matters: the states are ranked by which one the reader can ACT on. No
// library is first because the fix is a button on this screen; a stopped
// engine is next because the fix is one command; anything else is
// informational.
let lastHealth = null;

function describeHealth(data) {
  const d = data || {};
  const services = (d.services && typeof d.services === 'object') ? d.services : {};
  const down = Object.keys(services).filter(
    (k) => services[k] && services[k].healthy === false
  );

  if (indexBuild.state === 'running') {
    return {
      text: 'Building your library…', tone: 'warn',
      detail: 'Reading your documents and making them searchable.'
    };
  }
  if (d.index_ready === false) {
    return {
      text: 'No library yet', tone: 'warn',
      detail: 'CyClaw has no searchable copy of your documents yet. Build one below.'
    };
  }
  if (down.includes('ollama')) {
    return {
      text: "Local AI engine isn't running", tone: 'warn',
      detail: 'Start Ollama (ollama serve) and CyClaw can write answers again. '
            + 'Your documents are still searchable in the meantime.'
    };
  }
  if (down.length) {
    return {
      text: `${down[0]} unavailable`, tone: 'warn',
      detail: (services[down[0]] && services[down[0]].error) || 'This service is not responding.'
    };
  }
  return {
    text: 'Ready', tone: 'ok',
    detail: 'Your documents are searchable and the local AI engine is running.'
  };
}

// Painting is separate from fetching because the status chip has TWO drivers:
// the 15s /health poll, and local build-state transitions that must show up
// immediately. Without this, clicking Build left the chip reading "No library
// yet" for up to 15 seconds while the panel beside it already said "Building".
function paintHealthStatus() {
  const health = describeHealth(lastHealth);
  statusDot.className = `status-dot ${health.tone}`;
  statusText.textContent = health.text;
  statusText.title = health.detail;
}

async function checkHealth() {
  try {
    const resp = await fetchWithTimeout(`${API}/health`, {}, 3000);
    // Every other fetch in this file guards on resp.ok and tolerates a
    // non-JSON body; this one did neither. A gateway answering 4xx/5xx with a
    // JSON error body (FastAPI's {"detail": ...}) flowed into the success
    // branch: data.status was undefined, so the footer rendered "gateway
    // undefined" while the dot left the offline state, a bogus
    // graph_timeout_sec could be adopted as the query deadline, and -- worst --
    // healthBackoffMs was reset to the base interval, so the console kept
    // polling a failing gateway every 15s while telling the operator it was
    // fine. Treat any non-2xx as unreachable and fall through to the catch.
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(extractErrorMessage(data, `health check failed (${resp.status})`));
    }
    lastHealth = data;
    paintHealthStatus();
    modeBadge.textContent = data.mode || 'offline';
    const fl = document.getElementById('footerLeft');
    if (fl) {
      const ver = data.version ? ` v${data.version}` : '';
      fl.textContent = `cyclaw${ver} · ${window.location.host} · ${data.mode || 'offline'}`;
    }
    if (Number.isFinite(data.graph_timeout_sec) && data.graph_timeout_sec > 0) {
      queryDeadlineMs = (data.graph_timeout_sec + 10) * 1000;
    }
    if (Number.isFinite(data.ops_sync_timeout_sec) && data.ops_sync_timeout_sec > 0) {
      opsSyncDeadlineMs = (data.ops_sync_timeout_sec + 60) * 1000;
    }
    renderFirstRun(data);
    healthBackoffMs = HEALTH_BASE_INTERVAL;
  } catch (e) {
    statusDot.className = 'status-dot offline';
    statusText.textContent = "Can't reach CyClaw";
    statusText.title = 'The gateway is not responding. Is it still running?';
    healthBackoffMs = Math.min(healthBackoffMs * 2, HEALTH_MAX_INTERVAL);
  }
  scheduleHealthCheck();
}

function applyRoleChrome() {
  const usersBtn = document.getElementById('usersToggleBtn');
  const auditBtn = document.getElementById('auditToggleBtn');
  if (usersBtn) usersBtn.hidden = !(authRole === 'admin' || authRole === 'operator');
  if (auditBtn) auditBtn.hidden = !(authRole === 'admin' || authRole === 'audit');
}

// ── ADVANCED MODE ──
// Hides the operator toolbar -- the five subsystem consoles plus the
// role-gated Users/Audit buttons -- behind one switch, so the default screen
// is a query box and a status light. See the #advancedTools comment in
// terminal.html for why this is NOT folded into applyRoleChrome().
//
// Users/Audit live INSIDE the wrapper, so hiding it hides them without
// touching their own role gate, and the two compose: a button appears only
// when its role allows it AND advanced mode is on. The /users and /audit
// slash commands keep working either way -- a hidden ancestor does not set
// the button's own .hidden, which is what openUsersPanel() checks, so
// someone who knows the command is not locked out by a display preference.
const ADVANCED_KEY = 'cyclaw.advancedMode';

function readAdvancedPref() {
  // Private windows and "block site data" throw on ACCESS, not just on write,
  // so this needs a try/catch rather than a feature check.
  try {
    return window.localStorage.getItem(ADVANCED_KEY) === '1';
  } catch (e) {
    return false;
  }
}

// Held in a variable rather than re-read per toggle: where localStorage is
// unavailable the read always returns false, so a read-modify-write toggle
// would latch on and never turn back off.
let advancedMode = readAdvancedPref();

function applyAdvancedChrome() {
  const wrap = document.getElementById('advancedTools');
  const btn = document.getElementById('advancedToggleBtn');
  if (wrap) wrap.hidden = !advancedMode;
  if (btn) {
    btn.setAttribute('aria-expanded', advancedMode ? 'true' : 'false');
    btn.textContent = advancedMode ? 'Advanced ▾' : 'Advanced ▸';
  }
}

function toggleAdvanced() {
  advancedMode = !advancedMode;
  try {
    window.localStorage.setItem(ADVANCED_KEY, advancedMode ? '1' : '0');
  } catch (e) {
    // Not persistable in this context; the toggle still works for this page
    // load, it just will not survive a reload.
  }
  applyAdvancedChrome();
}

function openUsersPanel() {
  const usersBtn = document.getElementById('usersToggleBtn');
  const panel = document.getElementById('usersPanel');
  if (!usersBtn || usersBtn.hidden) {
    addEntry('error', 'ERROR', 'Users panel is not available for this role (or auth is off).');
    return;
  }
  if (panel) panel.classList.add('open');
  if (window.CyClawAuthAdmin) {
    window.CyClawAuthAdmin.render(document.getElementById('usersPanelBody'), {
      base: '/auth',
      actorRole: authRole,
      getCsrf: function () { return csrfToken; },
      fetchFn: function (path, init) { return fetchWithTimeout(API + path, init || {}, 15000); },
      onStatus: function (msg) {
        const el = document.getElementById('usersPanelStatus');
        if (!el) return;
        if (msg) {
          el.textContent = msg;
          el.hidden = false;
        } else {
          el.textContent = '';
          el.hidden = true;
        }
      },
    });
  }
}

async function openAuditPanel() {
  const panel = document.getElementById('auditPanel');
  const box = document.getElementById('auditSummary');
  if (panel) panel.classList.add('open');
  if (!box) return;
  // Wrapped like every sibling panel (loadSoul, runSync, runOps). Without this
  // a network error -- or the 15s abort -- rejected out of an async click
  // handler as an unhandled rejection, and the box kept whatever it showed
  // before: on first open, the literal "Load the Audit panel after login."
  // placeholder, which reads exactly like a panel that loaded successfully.
  try {
    const key = apiKeyInput ? apiKeyInput.value.trim() : '';
    const resp = key
      ? await fetchWithTimeout(`${API}/audit/summary`, { headers: authHeaders() }, 15000)
      : await fetchWithTimeout(`${API}/auth/audit/summary`, {}, 15000);
    if (!resp.ok) {
      box.textContent = describeApiKeyError(
        await resp.json().catch(() => ({})),
        'audit summary unavailable (' + resp.status + ')'
      );
      return;
    }
    box.textContent = JSON.stringify(await resp.json(), null, 2);
  } catch (e) {
    box.textContent = "Couldn't load the audit summary: " + e.message;
  }
}

async function submitQuery(confirmedOnline = null, onlineProvider = null, confirmEntryId = null) {
  // sendBtn.disabled is this console's in-flight signal (set below, cleared in
  // finally). The button itself can't be clicked while disabled, but the Enter
  // key handler and the confirm-gate buttons call here directly and bypass it.
  // A second entry mid-flight overwrites the single global activeQueryController,
  // which strands the first query's abort handle, leaves the second query
  // uncancellable by Esc, and makes its deadline timer fire against a nulled
  // global. Guarding the funnel — not just the key handler — also covers the
  // case where Enter starts query #2 while an earlier confirm prompt is still
  // on screen and clickable. Returning before the input is cleared keeps the
  // operator's typed text.
  if (sendBtn.disabled) return;

  const query = confirmedOnline !== null ? pendingConfirmById.get(confirmEntryId) : input.value.trim();
  if (confirmedOnline !== null) pendingConfirmById.delete(confirmEntryId);
  if (!query) return;
  const slash = query.toLocaleLowerCase('en-US');
  if (confirmedOnline === null && (slash === '/users' || slash === '/admin' || slash === '/audit' || slash === '/help')) {
    input.value = '';
    if (slash === '/help') {
      if (emptyState) emptyState.remove();
      addEntry('system', '', 'Commands: /users, /admin, /audit, /help. Soul, Sync, Agentic, FS, and SQL are Advanced toolbar buttons. This is the RAG console — other /text is sent as a query.');
      return;
    }
    if (slash === '/audit') openAuditPanel();
    else openUsersPanel();
    return;
  }

  if (confirmedOnline === null) {
    input.value = '';
    input.style.height = 'auto';
  }

  if (emptyState) emptyState.remove();

  if (confirmedOnline === null) {
    addEntry('query', 'QUERY', query);
  }

  const loadingId = addLoadingEntry();
  sendBtn.disabled = true;
  const startTime = performance.now();

  activeQueryController = new AbortController();
  // Declared outside the try so finally can clear it on EVERY exit path —
  // cancel (Esc) and network-error included. Left armed, the stale timer
  // fires ~670s later against the global activeQueryController, which by
  // then is null (TypeError) or a NEWER query's controller (aborts it).
  let timeoutId;
  try {
    const body = { query };
    if (confirmedOnline !== null) {
      body.user_confirmed_online = confirmedOnline;
      if (onlineProvider) body.online_provider = onlineProvider;
    }

    // Bound the wait so a stalled LLM surfaces an error instead of hanging the
    // UI forever. queryDeadlineMs is synced from /health to stay just ABOVE the
    // server's graph_timeout_sec, so the server's truthful 504 GRAPH_TIMEOUT
    // message wins the race instead of being masked by a premature client abort.
    timeoutId = window.setTimeout(() => activeQueryController.abort(), queryDeadlineMs);
    const resp = await fetch(`${API}/query`, {
      method: 'POST',
      headers: queryHeaders(),
      body: JSON.stringify(body),
      signal: activeQueryController.signal
    });

    const elapsed = Math.round(performance.now() - startTime);
    removeEntry(loadingId);

    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ error: resp.statusText }));
      const { text, code, message } = describeQueryError(err);
      const meta = [{ k: 'http', v: resp.status }];
      if (code) meta.push({ k: 'code', v: code });
      if (message && message !== text) meta.push({ k: 'detail', v: message });
      addEntry('error', 'ERROR', text, meta);
      return;
    }

    const data = await resp.json();
    queryCount++;

    if (data.needs_confirm) {
      const entryId = addConfirmEntry(
        data.confirm_message || 'Low confidence. Send online?',
        Array.isArray(data.available_providers) ? data.available_providers : []
      );
      pendingConfirmById.set(entryId, query);
      return;
    }

    // model stays the ROLE ("local" / "offline-best-effort" / "grok" / "claude")
    // -- that's the one signal telling a vault hit from a best-effort guess
    // from an escalation. tag is additive: the concrete model.yaml name that
    // actually answered, so "local" doesn't read as a black box. Omitted when
    // null (the answer_model has no model identity, e.g. guardrail-blocked).
    const meta = [
      { k: 'model', v: data.model_used },
      ...(data.llm_model ? [{ k: 'tag', v: data.llm_model }] : []),
      { k: 'mode', v: data.retrieval_mode },
      { k: 'hits', v: data.hit_count },
      { k: 'time', v: `${elapsed}ms` }
    ];
    const answerEl = document.getElementById(addEntry('answer', 'ANSWER', data.answer, meta));
    if (answerEl && data.sources && data.sources.length > 0) {
      addSources(answerEl, data.sources);
    }

    if (data.error) {
      const { text, code, message } = describeQueryError(data.error);
      const meta = [];
      if (code) meta.push({ k: 'code', v: code });
      if (message && message !== text) meta.push({ k: 'detail', v: message });
      addEntry('error', 'WARNING', text, meta.length ? meta : null);
    }

    footerRight.textContent = `queries: ${queryCount} · last: ${elapsed}ms`;
  } catch (e) {
    removeEntry(loadingId);
    if (e.name === 'AbortError') {
      const elapsed = Math.round(performance.now() - startTime);
      if (elapsed >= queryDeadlineMs - 500) {
        addEntry('error', 'ERROR', `Request timed out (${Math.round(queryDeadlineMs / 1000)}s, client-side). The local LLM may be stalled — check that Ollama is running and that its context length (num_ctx) exceeds the prompt + max_tokens.`);
      } else {
        addEntry('system', 'CANCELLED', 'Query cancelled by user.');
      }
    } else {
      addEntry('error', 'ERROR', `Network error: ${e.message}`);
    }
  } finally {
    window.clearTimeout(timeoutId);
    activeQueryController = null;
    sendBtn.disabled = false;
    input.focus();
  }
}

// Builds the loading row with createElement instead of an HTML string. It is
// the only entry needing child elements (spinner + cancel hint), and giving
// addEntry a raw-HTML mode just to serve it left an innerHTML escape hatch one
// argument away from every caller that renders LLM answers, corpus filenames,
// and /ops/* subprocess output. One hardcoded caller today is exactly how the
// next caller ends up passing server text.
function addLoadingEntry() {
  const id = `entry-${entryCounter++}`;
  const el = document.createElement('div');
  el.className = 'entry system-entry';
  el.id = id;

  const textEl = document.createElement('div');
  textEl.className = 'entry-text';

  const spinner = document.createElement('span');
  spinner.className = 'spinner';
  textEl.appendChild(spinner);
  textEl.appendChild(document.createTextNode('searching vault... '));

  const hint = document.createElement('span');
  hint.className = 'cancel-hint';
  hint.textContent = '(Esc to cancel)';
  textEl.appendChild(hint);

  el.appendChild(textEl);
  resultsEl.appendChild(el);
  resultsEl.scrollTop = resultsEl.scrollHeight;
  return id;
}

function addEntry(type, label, text, meta = null) {
  const id = `entry-${entryCounter++}`;
  const el = document.createElement('div');
  el.className = `entry ${type}-entry`;
  el.id = id;

  let html = '';
  if (label) {
    // Escaped like text and meta below. Every current caller passes a hardcoded
    // literal, so this is not a live bug — but it sits in the same template as
    // two escaped interpolations, and the first caller to pass a server-supplied
    // field as a label would otherwise get stored XSS.
    html += `<div class="entry-label">${escHtml(label)}</div>`;
  }
  html += `<div class="entry-text">${escHtml(text)}</div>`;

  if (meta) {
    html += '<div class="meta-row">';
    for (const m of meta) {
      // Escape both key and value: although m.v is currently fed from
      // server-side enums (model_used / retrieval_mode), this sink writes via
      // innerHTML, so escape defensively to keep it XSS-safe if the API ever
      // surfaces attacker-influenced metadata.
      html += `<span class="meta-tag">${escHtml(String(m.k))}: <span class="val">${escHtml(String(m.v))}</span></span>`;
    }
    html += '</div>';
  }

  el.innerHTML = html;
  resultsEl.appendChild(el);
  resultsEl.scrollTop = resultsEl.scrollHeight;
  return id;
}

function removeEntry(id) {
  const el = document.getElementById(id);
  if (el) el.remove();
}

// availableProviders comes from the server's QueryResponse and lists only the
// providers the user gate would actually route to. A "Send to <provider>"
// button is rendered ONLY for those — previously both were always shown, so
// pressing one against a disabled provider silently produced an offline answer
// that looked like a cloud answer. Defaults to [] (offline-only) rather than
// both, so an older/partial response fails closed instead of re-offering the
// buttons that caused the problem.
function addConfirmEntry(message, availableProviders = []) {
  const id = `entry-${entryCounter++}`;
  const el = document.createElement('div');
  el.className = 'entry confirm-entry';
  el.id = id;
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');

  // Build via DOM + addEventListener rather than an innerHTML template with an
  // inline onclick string: textContent escapes the message natively and no
  // value is ever interpolated into an executable attribute (XSS hardening).
  const label = document.createElement('div');
  label.className = 'entry-label';
  label.textContent = 'CONFIRMATION REQUIRED';
  label.id = `${id}-label`;
  el.setAttribute('aria-labelledby', `${id}-label`);

  const text = document.createElement('div');
  text.className = 'entry-text';
  text.textContent = message;

  const buttons = document.createElement('div');
  buttons.className = 'confirm-buttons';
  buttons.setAttribute('role', 'group');
  buttons.setAttribute('aria-label', 'Confirmation actions');

  const PROVIDER_BUTTONS = {
    grok:   { text: 'Send to Grok',   aria: 'Confirm: send query to Grok online' },
    claude: { text: 'Send to Claude', aria: 'Confirm: send query to Claude API online' }
  };

  const providerBtns = [];
  for (const provider of availableProviders) {
    const spec = PROVIDER_BUTTONS[provider];
    if (!spec) continue;   // unknown provider name from the server — don't render a dead button
    const btn = document.createElement('button');
    btn.className = 'btn-confirm yes';
    btn.textContent = spec.text;
    btn.setAttribute('aria-label', spec.aria);
    btn.addEventListener('click', () => handleConfirm(true, id, provider));
    providerBtns.push(btn);
  }

  const noBtn = document.createElement('button');
  noBtn.className = 'btn-confirm no';
  // With no provider available this is the only button, so "No —" would be
  // answering a question that was never asked.
  noBtn.textContent = providerBtns.length ? 'No — Stay Offline' : 'Stay Offline';
  noBtn.setAttribute('aria-label', 'Decline: keep this answer from the local model');
  noBtn.addEventListener('click', () => handleConfirm(false, id));

  buttons.append(...providerBtns, noBtn);
  el.append(label, text, buttons);
  resultsEl.appendChild(el);
  resultsEl.scrollTop = resultsEl.scrollHeight;
  noBtn.focus();

  // Trap focus within the modal dialog (Tab cycles through the rendered buttons).
  const focusable = [...providerBtns, noBtn];
  el.addEventListener('keydown', (e) => {
    if (e.key !== 'Tab') return;
    const idx = focusable.indexOf(document.activeElement);
    if (e.shiftKey) {
      focusable[idx <= 0 ? focusable.length - 1 : idx - 1].focus();
    } else {
      focusable[(idx + 1) % focusable.length].focus();
    }
    e.preventDefault();
  });

  return id;
}

function handleConfirm(confirmed, entryId, onlineProvider = null) {
  const el = document.getElementById(entryId);
  if (el) {
    const btns = el.querySelectorAll('.btn-confirm');
    btns.forEach(b => b.disabled = true);
    el.removeAttribute('role');
    el.removeAttribute('aria-modal');
    el.removeAttribute('aria-labelledby');
  }
  const storedQuery = pendingConfirmById.get(entryId);
  if (storedQuery === undefined) {
    addEntry('system', '', '→ Confirmation expired; please resend your query.');
    input.focus();
    return;
  }
  const providerLabel = onlineProvider === 'claude' ? 'Claude' : 'Grok';
  addEntry('system', '', confirmed ? `→ Escalating to ${providerLabel}...` : '→ Staying offline (local model)');
  submitQuery(confirmed, onlineProvider, entryId);
  input.focus();
}

function fmtMaybeNumber(value, digits = 4) {
  return typeof value === 'number' ? value.toFixed(digits) : '—';
}

function fmtMaybeRank(value) {
  return typeof value === 'number' ? `#${value + 1}` : '—';
}

function addSources(parentEl, sources) {
  const toggle = document.createElement('div');
  toggle.className = 'sources-toggle';
  toggle.textContent = `▸ ${sources.length} sources`;

  const list = document.createElement('div');
  list.className = 'sources-list';

  for (const s of sources) {
    const item = document.createElement('div');
    item.className = 'source-item';
    const score = fmtMaybeNumber(s.rrf_score ?? s.score, 4);
    const path = s.source || 'unknown';
    item.innerHTML = `
      <div class="source-topline">
        <span class="source-score">rrf ${score}</span>
        <span class="source-path">${escHtml(path)}</span>
      </div>
      <div class="source-provenance">
        <span class="prov-tag">sem rank <span class="prov-val">${fmtMaybeRank(s.semantic_rank)}</span></span>
        <span class="prov-tag">sem score <span class="prov-val">${fmtMaybeNumber(s.semantic_score, 4)}</span></span>
        <span class="prov-tag">sem rrf <span class="prov-val">${fmtMaybeNumber(s.rrf_semantic_contrib, 5)}</span></span>
        <span class="prov-tag">bm25 rank <span class="prov-val">${fmtMaybeRank(s.keyword_rank)}</span></span>
        <span class="prov-tag">bm25 score <span class="prov-val">${fmtMaybeNumber(s.keyword_score, 4)}</span></span>
        <span class="prov-tag">bm25 rrf <span class="prov-val">${fmtMaybeNumber(s.rrf_keyword_contrib, 5)}</span></span>
      </div>
    `;
    list.appendChild(item);
  }

  toggle.addEventListener('click', () => {
    list.classList.toggle('open');
    toggle.textContent = list.classList.contains('open')
      ? `▾ ${sources.length} sources`
      : `▸ ${sources.length} sources`;
  });

  parentEl.appendChild(toggle);
  parentEl.appendChild(list);
}

async function toggleSoulPanel() {
  soulPanel.classList.toggle('open');
  soulToggleBtn.textContent = soulPanel.classList.contains('open') ? 'Hide Soul' : 'Soul Console';
  if (soulPanel.classList.contains('open') && !soulEditor.value.trim()) {
    await loadSoul();
  }
}

async function loadSoul() {
  setSoulStatus('Loading soul...');
  try {
    const resp = await fetchWithTimeout(`${API}/soul`, {
      headers: authHeaders(),
    }, 5000);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(describeApiKeyError(data, 'Failed to load soul'));
    }
    soulEditor.value = data.soul || '';
    soulVersion.textContent = `version: ${data.version ?? '--'}`;
    soulSource.textContent = `source: ${data.source || 'unknown'}`;
    pendingSoulProposal = null;
    proposalBox.style.display = 'none';
    setSoulStatus('Soul loaded.', 'success');
  } catch (e) {
    setSoulStatus(e.message, 'error');
  }
}

async function reloadSoul() {
  setSoulStatus('Reloading soul from disk...');
  try {
    const resp = await fetchWithTimeout(`${API}/soul/reload`, {
      method: 'POST',
      headers: authHeaders()
    }, 10000);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(describeApiKeyError(data, 'Failed to reload soul'));
    }
    addEntry('system', '', '→ Soul reloaded from disk');
    await loadSoul();
    setSoulStatus(data.message || 'Soul reloaded.', 'success');
  } catch (e) {
    setSoulStatus(e.message, 'error');
  }
}

async function proposeSoulEvolution() {
  const newSoul = soulEditor.value.trim();
  const reason = soulReason.value.trim() || 'user-requested';
  if (!newSoul) {
    setSoulStatus('Soul text is empty.', 'error');
    return;
  }

  // Hide any stale proposal from a prior attempt so an error here can't leave a
  // previous proposal preview on screen, mismatched with the new status message.
  proposalBox.style.display = 'none';
  setSoulStatus('Creating proposal...');
  try {
    const resp = await fetchWithTimeout(`${API}/soul/propose`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ new_soul: newSoul, reason })
    }, 10000);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(describeApiKeyError(data, 'Failed to create proposal'));
    }

    pendingSoulProposal = {
      new_soul: data.proposed_soul || newSoul,
      reason: data.reason || reason
    };
    proposalMeta.textContent = `current: ${data.current_sha || 'n/a'} · proposed: ${data.proposed_sha || 'n/a'} · reason: ${pendingSoulProposal.reason}`;
    proposalWarning.textContent = data.warning || 'Review before applying.';
    proposalPreview.textContent = pendingSoulProposal.new_soul;
    proposalBox.style.display = 'block';
    setSoulStatus('Proposal created. Review it, then apply if correct.', 'success');
    addEntry('system', '', '→ Soul evolution proposed and awaiting apply');
  } catch (e) {
    setSoulStatus(e.message, 'error');
  }
}

async function applySoulEvolution() {
  if (!pendingSoulProposal) {
    setSoulStatus('Create a proposal first.', 'error');
    return;
  }

  setSoulStatus('Applying soul evolution...');
  try {
    const resp = await fetchWithTimeout(`${API}/soul/apply`, {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify(pendingSoulProposal)
    }, 10000);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(describeApiKeyError(data, 'Failed to apply soul evolution'));
    }

    addEntry('system', '', '→ Soul updated and active for future queries');
    pendingSoulProposal = null;
    proposalBox.style.display = 'none';
    await loadSoul();
    setSoulStatus(data.message || 'Soul updated.', 'success');
  } catch (e) {
    setSoulStatus(e.message, 'error');
  }
}

async function restoreSoul() {
  setSoulStatus('Restoring from .bak...');
  try {
    const resp = await fetchWithTimeout(`${API}/soul/restore`, {
      method: 'POST',
      headers: authHeaders()
    }, 10000);
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) {
      throw new Error(describeApiKeyError(data, 'Failed to restore soul'));
    }
    addEntry('system', '', '→ Soul restored from .bak');
    await loadSoul();
    setSoulStatus('Soul restored from backup.', 'success');
  } catch (e) {
    setSoulStatus(e.message, 'error');
  }
}

// ============================================================================
// SYNC + AGENTIC OPS CONSOLES
// Same async/fetch/status idiom as the Soul Console. Both POST to /ops/* with
// the API key from authHeaders(); both render the exit-code envelope so failure
// states (safety-fuse abort, env/config error, write refused) are explicit.
// ============================================================================

// Shared POST helper. The route returns HTTP 200 even when the CLI exits
// non-zero (the exit code lives in the JSON envelope); only gateway-level
// problems (401/422/400/429/500) trip the !resp.ok branch.
// Server-side budgets these calls must outlive (utils/ops_runner.py):
// /ops/{agentic,fsconnect,sqlconnect} subprocesses are killed at 120s
// (_TIMEOUT_SEC), and /ops/sync action=sync at sync_timeout_sec*2 + 60
// = up to 7260s with post_sync_check. The old 60s client ceiling aborted
// the tab while the CLI kept running under its single-instance lock and
// threw away the exit-code envelope; each deadline now sits just above
// its server budget so the envelope (or the gateway's typed error) always
// arrives. A hung subprocess is still bounded — by the server's kill.
const OPS_CLI_TIMEOUT_MS = 130000;    // 120s ops_runner._TIMEOUT_SEC + 10s margin
// Re-synced from /health (ops_sync_timeout_sec + 60s margin) because
// sync.sync_timeout_sec has no upper bound -- no constant here can cover every
// valid server configuration, so the server is asked rather than guessed at.
// This default covers the shipped 3600s config (x2 for post_sync_check, +60)
// and carries the wait until the first successful /health, exactly like
// queryDeadlineMs above.
let opsSyncDeadlineMs = 7320000;
async function callOps(path, body, timeoutMs = OPS_CLI_TIMEOUT_MS) {
  const resp = await fetchWithTimeout(`${API}${path}`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body)
  }, timeoutMs);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(describeApiKeyError(data, 'Ops request failed'));
  }
  return data;
}

// Render the exit-code envelope into a soul-style proposal box.
function renderOps(box, meta, warning, preview, data) {
  meta.textContent = `action: ${data.action} · exit: ${data.exit_code} (${data.label}) · ${data.ok ? 'OK' : 'FAILED'}`;
  const err = (data.stderr || '').trim();
  const errTruncated = err.length > 600 ? ' …[truncated]' : '';
  warning.textContent = err ? `stderr: ${err.slice(0, 600)}${errTruncated}` : '';
  let bodyText = '';
  if (data.parsed) {
    bodyText = JSON.stringify(data.parsed, null, 2);
  } else {
    bodyText = (data.stdout || '').trim();
  }
  preview.textContent = bodyText || '(no output)';
  box.style.display = 'block';
}

// ---- Sync Console ----------------------------------------------------------

function setSyncStatus(message, tone = '') {
  syncStatus.textContent = message || '';
  syncStatus.className = `soul-status${tone ? ` ${tone}` : ''}`;
}

function syncLabelMsg(data) {
  switch (data.exit_code) {
    case 0:  return 'OK';
    case 10: return 'OK — corpus changed; reindex needed (python -m retrieval.indexer)';
    case 1:  return 'SAFETY FUSE TRIPPED (max-delete / max-transfer abort)';
    case 2:  return 'operation failed';
    case 3:  return 'env/config error (rclone missing/too old, or config invalid)';
    default: return `exit ${data.exit_code}`;
  }
}

function applySyncConfig(config) {
  if (!config) return;
  document.getElementById('syncEnabled').textContent = `enabled: ${config.enabled}`;
  document.getElementById('syncDirection').textContent = `direction: ${config.direction}`;
  document.getElementById('syncSchedule').textContent = `schedule: ${config.schedule}`;
}

async function runSync(action, opts = {}) {
  setSyncStatus(`Running sync ${action}...`);
  try {
    const data = await callOps('/ops/sync', { action, ...opts },
      action === 'sync' ? opsSyncDeadlineMs : OPS_CLI_TIMEOUT_MS);
    applySyncConfig(data.config);
    renderOps(syncBox, syncMeta, syncWarning, syncPreview, data);
    setSyncStatus(`[${action}] ${syncLabelMsg(data)}`, data.ok ? 'success' : 'error');
    document.getElementById('syncBadge').textContent = `last: ${action} → ${data.label}`;
    addEntry('system', '', `→ ops/sync ${action} exit ${data.exit_code} (${data.label})`);
    return true;
  } catch (e) {
    setSyncStatus(e.message, 'error');
    return false;
  }
}

function syncStatusCmd()  { return runSync('status'); }
function syncDryRun()     { return runSync('sync', { dry_run: true }); }
function syncPull()       { return runSync('sync', { dry_run: false }); }
function syncScheduleOn() { return runSync('schedule'); }
function syncScheduleOff(){ return runSync('unschedule'); }

let syncLoaded = false;
async function toggleSyncPanel() {
  syncPanel.classList.toggle('open');
  const open = syncPanel.classList.contains('open');
  syncToggleBtn.textContent = open ? 'Hide Sync' : 'Sync Console';
  // Lazy first status read only when a key is already present (all ops need auth).
  if (open && !syncLoaded) {
    if (!apiKeyInput.value.trim()) {
      setSyncStatus('Enter an API key above to load sync status.', 'error');
    } else {
      syncLoaded = await runSync('status');
    }
  }
}

// ---- Agentic Console -------------------------------------------------------

let agenticConfig = { enabled: false, mode: 'read', writes_enabled: false };

function setAgenticStatus(message, tone = '') {
  agenticStatus.textContent = message || '';
  agenticStatus.className = `soul-status${tone ? ` ${tone}` : ''}`;
}

function agenticLabelMsg(data) {
  switch (data.exit_code) {
    case 0:  return 'OK';
    case 2:  return 'operation failed';
    case 3:  return 'env/config error (gh missing/too old, or config invalid)';
    case 4:  return 'WRITE REFUSED by gate';
    default: return `exit ${data.exit_code}`;
  }
}

function setGate(id, ok, label) {
  const el = document.getElementById(id);
  el.textContent = `${ok ? '✓' : '✗'} ${label}`;
  el.className = `gate-row ${ok ? 'ok' : 'bad'}`;
}

// The 4-gate checklist. Apply stays disabled until all four pass. Gates 1-2 are
// config-driven (mode=write + writes_enabled), so with the shipped defaults
// (mode=read, writes_enabled=false) the Apply button is disabled and cannot fire a
// skills-registry write from the console. This is a UI governance overlay on top of
// the registry's own gate (reason + injection scan + --confirm) — strictly stricter,
// never weaker. (GitHub writes are separate and stay stubbed in agentic/writer.py.)
function refreshAgenticGates() {
  const reasonOk  = document.getElementById('agenticReason').value.trim().length > 0;
  const confirmOk = agenticConfirm.checked;
  const modeOk    = agenticConfig.mode === 'write';
  const writesOk  = agenticConfig.writes_enabled === true;
  setGate('gateMode', modeOk, 'mode = write');
  setGate('gateWrites', writesOk, 'writes_enabled = true');
  setGate('gateReason', reasonOk, 'reason non-empty');
  setGate('gateConfirm', confirmOk, '--confirm checked');
  const allOk = modeOk && writesOk && reasonOk && confirmOk;
  agenticApplyBtn.disabled = !allOk;
  agenticApplyBtn.textContent = allOk
    ? 'Apply Skill (confirm write)'
    : (writesOk ? 'Apply Skill' : 'Apply Skill (writes disabled)');
}

function applyAgenticConfig(config) {
  if (!config) return;
  agenticConfig = config;
  document.getElementById('agenticEnabled').textContent = `enabled: ${config.enabled}`;
  document.getElementById('agenticMode').textContent = `mode: ${config.mode}`;
  document.getElementById('agenticWrites').textContent = `writes_enabled: ${config.writes_enabled}`;
  refreshAgenticGates();
}

async function runAgentic(action, opts = {}) {
  setAgenticStatus(`Running agentic ${action}...`);
  try {
    const data = await callOps('/ops/agentic', { action, ...opts });
    applyAgenticConfig(data.config);
    renderOps(agenticBox, agenticMeta, agenticWarning, agenticPreview, data);
    let extra = '';
    if (data.parsed && typeof data.parsed.governance_score === 'number') {
      extra = ` · governance_score: ${data.parsed.governance_score}/100`;
    }
    setAgenticStatus(`[${action}] ${agenticLabelMsg(data)}${extra}`, data.ok ? 'success' : 'error');
    addEntry('system', '', `→ ops/agentic ${action} exit ${data.exit_code} (${data.label})`);
    return true;
  } catch (e) {
    setAgenticStatus(e.message, 'error');
    return false;
  }
}

function agenticStatusCmd()    { return runAgentic('status'); }
function agenticRegistryHealth(){ return runAgentic('status'); }  // status carries registry_version + skills

function agenticContextPR() {
  const n = parseInt(document.getElementById('agenticNum').value, 10);
  if (Number.isNaN(n)) { setAgenticStatus('Enter a PR number first.', 'error'); return; }
  return runAgentic('context', { pr: n });
}
function agenticContextIssue() {
  const n = parseInt(document.getElementById('agenticNum').value, 10);
  if (Number.isNaN(n)) { setAgenticStatus('Enter an issue number first.', 'error'); return; }
  return runAgentic('context', { issue: n });
}
function agenticPropose() {
  const name = document.getElementById('agenticName').value.trim();
  const desc = document.getElementById('agenticDesc').value.trim();
  const body = document.getElementById('agenticBody').value;
  const reason = document.getElementById('agenticReason').value.trim();
  if (!name || !desc) { setAgenticStatus('Skill name and description are required.', 'error'); return; }
  return runAgentic('propose-skill', { name, desc, body: body || null, reason: reason || null });
}
function agenticApply() {
  const name = document.getElementById('agenticName').value.trim();
  const desc = document.getElementById('agenticDesc').value.trim();
  const body = document.getElementById('agenticBody').value;
  const reason = document.getElementById('agenticReason').value.trim();
  if (!name || !desc) { setAgenticStatus('Skill name and description are required.', 'error'); return; }
  if (!reason) { setAgenticStatus('A non-empty reason is required to apply.', 'error'); return; }
  return runAgentic('apply-skill', { name, desc, body: body || null, reason, confirm: agenticConfirm.checked });
}

let agenticLoaded = false;
async function toggleAgenticPanel() {
  agenticPanel.classList.toggle('open');
  const open = agenticPanel.classList.contains('open');
  agenticToggleBtn.textContent = open ? 'Hide Agentic' : 'Agentic Console';
  if (open) refreshAgenticGates();
  if (open && !agenticLoaded) {
    if (!apiKeyInput.value.trim()) {
      setAgenticStatus('Enter an API key above to load agentic status.', 'error');
    } else {
      agenticLoaded = await runAgentic('status');
    }
  }
}

// ---- FS Console --------------------------------------------------------------

function setFsStatus(message, tone = '') {
  fsStatus.textContent = message || '';
  fsStatus.className = `soul-status${tone ? ` ${tone}` : ''}`;
}

function fsLabelMsg(data) {
  switch (data.exit_code) {
    case 0:  return 'OK';
    case 2:  return 'operation failed';
    case 3:  return 'env/config error';
    case 4:  return 'WRITE REFUSED by gate';
    default: return `exit ${data.exit_code}`;
  }
}

function applyFsConfig(config) {
  if (!config) return;
  document.getElementById('fsEnabled').textContent = `enabled: ${config.enabled}`;
  document.getElementById('fsWritesEnabled').textContent = `writes: ${config.writes_enabled}`;
}

async function runFs(action, opts = {}) {
  setFsStatus(`Running fsconnect ${action}...`);
  try {
    const data = await callOps('/ops/fsconnect', { action, ...opts });
    applyFsConfig(data.config);
    renderOps(fsBox, fsMeta, fsWarning, fsPreview, data);
    setFsStatus(`[${action}] ${fsLabelMsg(data)}`, data.ok ? 'success' : 'error');
    addEntry('system', '', `→ ops/fsconnect ${action} exit ${data.exit_code} (${data.label})`);
    return true;
  } catch (e) {
    setFsStatus(e.message, 'error');
    return false;
  }
}

function fsStatusCmd() { return runFs('status'); }
function fsListCmd() {
  return runFs('list', {
    root: document.getElementById('fsRoot').value.trim() || null,
    path: document.getElementById('fsPath').value.trim() || null,
  });
}
function fsReadCmd() {
  return runFs('read', {
    root: document.getElementById('fsRoot').value.trim() || null,
    path: document.getElementById('fsPath').value.trim() || null,
  });
}
function fsStatCmd() {
  return runFs('stat', {
    root: document.getElementById('fsRoot').value.trim() || null,
    path: document.getElementById('fsPath').value.trim() || null,
  });
}
function fsGrepCmd() {
  const pattern = document.getElementById('fsPattern').value.trim();
  if (!pattern) { setFsStatus('Enter a grep pattern first.', 'error'); return; }
  return runFs('grep', {
    root: document.getElementById('fsRoot').value.trim() || null,
    path: document.getElementById('fsPath').value.trim() || null,
    pattern,
    regex: false,
  });
}
function fsGlobCmd() {
  const pattern = document.getElementById('fsPattern').value.trim();
  if (!pattern) { setFsStatus('Enter a glob pattern first.', 'error'); return; }
  return runFs('glob', {
    root: document.getElementById('fsRoot').value.trim() || null,
    pattern,
  });
}

let fsLoaded = false;
async function toggleFsPanel() {
  fsPanel.classList.toggle('open');
  const open = fsPanel.classList.contains('open');
  fsToggleBtn.textContent = open ? 'Hide FS' : 'FS Console';
  if (open && !fsLoaded) {
    if (!apiKeyInput.value.trim()) {
      setFsStatus('Enter an API key above to load fsconnect status.', 'error');
    } else {
      fsLoaded = await runFs('status');
    }
  }
}

// ---- SQL Console -------------------------------------------------------------

function setSqlStatus(message, tone = '') {
  sqlStatus.textContent = message || '';
  sqlStatus.className = `soul-status${tone ? ` ${tone}` : ''}`;
}

function sqlLabelMsg(data) {
  switch (data.exit_code) {
    case 0:  return 'OK';
    case 2:  return 'operation failed';
    case 3:  return 'env/config error (driver missing, DSN unset, or config invalid)';
    default: return `exit ${data.exit_code}`;
  }
}

function applySqlConfig(config) {
  if (!config) return;
  document.getElementById('sqlEnabled').textContent = `enabled: ${config.enabled}`;
  document.getElementById('sqlDriver').textContent = `driver: ${config.driver}`;
  document.getElementById('sqlReadOnly').textContent = `read_only: ${config.read_only}`;
}

async function runSql(action, opts = {}) {
  setSqlStatus(`Running sqlconnect ${action}...`);
  try {
    const data = await callOps('/ops/sqlconnect', { action, ...opts });
    applySqlConfig(data.config);
    renderOps(sqlBox, sqlMeta, sqlWarning, sqlPreview, data);
    setSqlStatus(`[${action}] ${sqlLabelMsg(data)}`, data.ok ? 'success' : 'error');
    addEntry('system', '', `→ ops/sqlconnect ${action} exit ${data.exit_code} (${data.label})`);
    return true;
  } catch (e) {
    setSqlStatus(e.message, 'error');
    return false;
  }
}

function sqlStatusCmd()  { return runSql('status'); }
function sqlSchemaCmd()  { return runSql('schema'); }
function sqlPreviewCmd() {
  const table = document.getElementById('sqlTable').value.trim();
  if (!table) { setSqlStatus('Enter a table name first.', 'error'); return; }
  return runSql('query', { table });
}
function sqlCountCmd() {
  const table = document.getElementById('sqlTable').value.trim();
  if (!table) { setSqlStatus('Enter a table name first.', 'error'); return; }
  return runSql('query', { table, count: true });
}
function sqlRunCmd() {
  const sql = document.getElementById('sqlQuery').value.trim();
  if (!sql) { setSqlStatus('Enter a SQL query first.', 'error'); return; }
  return runSql('query', { sql, fmt: document.getElementById('sqlFmt').value });
}
function sqlExplainCmd() {
  const sql = document.getElementById('sqlQuery').value.trim();
  if (!sql) { setSqlStatus('Enter a SQL query first.', 'error'); return; }
  return runSql('query', { sql, explain: true });
}

let sqlLoaded = false;
async function toggleSqlPanel() {
  sqlPanel.classList.toggle('open');
  const open = sqlPanel.classList.contains('open');
  sqlToggleBtn.textContent = open ? 'Hide SQL' : 'SQL Console';
  if (open && !sqlLoaded) {
    if (!apiKeyInput.value.trim()) {
      setSqlStatus('Enter an API key above to load sqlconnect status.', 'error');
    } else {
      sqlLoaded = await runSql('status');
    }
  }
}

// TEXT CONTEXTS ONLY -- not attribute-safe. Serializing a text node escapes
// &, < and >, but leaves " and ' intact, which is correct and sufficient for
// every current use (all of them element text). Interpolating this into a
// quoted attribute -- title="${escHtml(x)}" -- would NOT be safe: a corpus
// filename like  " onmouseover=alert(1) x="  breaks straight out. Use
// setAttribute/textContent for attributes instead of extending this.
const _escDiv = document.createElement('div');
function escHtml(str) {
  _escDiv.textContent = str;
  return _escDiv.innerHTML;
}

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') {
    if (activeQueryController) {
      activeQueryController.abort();
      return;
    }
    document.querySelectorAll('.soul-panel.open').forEach(function(panel) {
      panel.classList.remove('open');
    });
    soulToggleBtn.textContent = 'Soul Console';
    syncToggleBtn.textContent = 'Sync Console';
    agenticToggleBtn.textContent = 'Agentic Console';
    fsToggleBtn.textContent = 'FS Console';
    sqlToggleBtn.textContent = 'SQL Console';
  }
});

let healthTimer = null;
let healthVisible = !document.hidden;
function scheduleHealthCheck() {
  if (healthTimer) clearTimeout(healthTimer);
  if (healthVisible) {
    healthTimer = setTimeout(checkHealth, healthBackoffMs);
  }
}
document.addEventListener('visibilitychange', () => {
  healthVisible = !document.hidden;
  if (healthVisible) {
    // The tab is visible again: refresh immediately so the status dot is
    // current, then resume the normal polling schedule.
    checkHealth();
  } else if (healthTimer) {
    clearTimeout(healthTimer);
    healthTimer = null;
  }
});
checkHealth();
input.focus();

// Toolbar/panel/send button wiring — addEventListener instead of an inline
// event-handler attribute, so gate.py's CSP can drop 'unsafe-inline' from
// script-src (see _SecurityHeadersMiddleware). Every element below already
// existed with a static id or gained one for this; none carry interpolated
// data, so this is a mechanical move, not a behavior change.
document.getElementById('soulToggleBtn').addEventListener('click', toggleSoulPanel);
document.getElementById('syncToggleBtn').addEventListener('click', toggleSyncPanel);
document.getElementById('agenticToggleBtn').addEventListener('click', toggleAgenticPanel);
document.getElementById('fsToggleBtn').addEventListener('click', toggleFsPanel);
document.getElementById('sqlToggleBtn').addEventListener('click', toggleSqlPanel);
const usersToggleBtn = document.getElementById('usersToggleBtn');
if (usersToggleBtn) usersToggleBtn.addEventListener('click', openUsersPanel);
const auditToggleBtn = document.getElementById('auditToggleBtn');
if (auditToggleBtn) auditToggleBtn.addEventListener('click', openAuditPanel);
document.getElementById('loadSoulBtn').addEventListener('click', loadSoul);
document.getElementById('reloadSoulBtn').addEventListener('click', reloadSoul);
document.getElementById('restoreSoulBtn').addEventListener('click', restoreSoul);
document.getElementById('proposeSoulEvolutionBtn').addEventListener('click', proposeSoulEvolution);
document.getElementById('applySoulEvolutionBtn').addEventListener('click', applySoulEvolution);
document.getElementById('syncStatusBtn').addEventListener('click', syncStatusCmd);
document.getElementById('syncDryRunBtn').addEventListener('click', syncDryRun);
document.getElementById('syncPullBtn').addEventListener('click', syncPull);
document.getElementById('syncScheduleOnBtn').addEventListener('click', syncScheduleOn);
document.getElementById('syncScheduleOffBtn').addEventListener('click', syncScheduleOff);
document.getElementById('agenticContextPRBtn').addEventListener('click', agenticContextPR);
document.getElementById('agenticContextIssueBtn').addEventListener('click', agenticContextIssue);
document.getElementById('agenticStatusBtn').addEventListener('click', agenticStatusCmd);
document.getElementById('agenticRegistryHealthBtn').addEventListener('click', agenticRegistryHealth);
document.getElementById('agenticProposeBtn').addEventListener('click', agenticPropose);
document.getElementById('agenticApplyBtn').addEventListener('click', agenticApply);
document.getElementById('fsStatusBtn').addEventListener('click', fsStatusCmd);
document.getElementById('fsListBtn').addEventListener('click', fsListCmd);
document.getElementById('fsReadBtn').addEventListener('click', fsReadCmd);
document.getElementById('fsStatBtn').addEventListener('click', fsStatCmd);
document.getElementById('fsGrepBtn').addEventListener('click', fsGrepCmd);
document.getElementById('fsGlobBtn').addEventListener('click', fsGlobCmd);
document.getElementById('sqlStatusBtn').addEventListener('click', sqlStatusCmd);
document.getElementById('sqlSchemaBtn').addEventListener('click', sqlSchemaCmd);
document.getElementById('sqlPreviewBtn').addEventListener('click', sqlPreviewCmd);
document.getElementById('sqlCountBtn').addEventListener('click', sqlCountCmd);
document.getElementById('sqlRunBtn').addEventListener('click', sqlRunCmd);
document.getElementById('sqlExplainBtn').addEventListener('click', sqlExplainCmd);
// submitQuery(confirmedOnline, onlineProvider) treats any non-null first
// argument as "user already confirmed" -- addEventListener passes the click's
// PointerEvent as that argument, so a bare `submitQuery` reference here would
// silently take the wrong branch and never send a plain query. Call with no
// arguments, matching the original onclick="submitQuery()" contract.
document.getElementById('sendBtn').addEventListener('click', () => submitQuery());
document.getElementById('agenticReason').addEventListener('input', refreshAgenticGates);
if (document.getElementById('authLoginBtn')) {
  document.getElementById('authLoginBtn').addEventListener('click', () => login());
}
if (document.getElementById('authSetupBtn')) {
  document.getElementById('authSetupBtn').addEventListener('click', () => setupAdminPassword());
}
if (document.getElementById('authLogoutBtn')) {
  document.getElementById('authLogoutBtn').addEventListener('click', () => logout());
}
if (authPassInput) {
  authPassInput.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      login();
    }
  });
}
if (authSetupPass2) {
  authSetupPass2.addEventListener('keydown', function(e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      setupAdminPassword();
    }
  });
}
if (document.getElementById('advancedToggleBtn')) {
  document.getElementById('advancedToggleBtn').addEventListener('click', () => toggleAdvanced());
}
applyAdvancedChrome();
refreshAuthUi();
document.getElementById('agenticConfirm').addEventListener('change', refreshAgenticGates);
