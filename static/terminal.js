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
let pendingConfirmQuery = null;
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
}

async function login() {
  if (!authUserInput || !authPassInput) return;
  const username = authUserInput.value.trim();
  const password = authPassInput.value;
  authPassInput.value = '';
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
}

async function logout() {
  const headers = { 'Content-Type': 'application/json' };
  if (csrfToken) headers['X-CyClaw-CSRF'] = csrfToken;
  await fetchWithTimeout(`${API}/auth/logout`, { method: 'POST', headers }, 15000);
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

function setSoulStatus(message, tone = '') {
  soulStatus.textContent = message || '';
  soulStatus.className = `soul-status${tone ? ` ${tone}` : ''}`;
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
    statusDot.className = 'status-dot';
    statusText.textContent = `gateway ${data.status}`;
    modeBadge.textContent = data.mode || 'offline';
    const fl = document.getElementById('footerLeft');
    if (fl) {
      const ver = data.version ? ` v${data.version}` : '';
      fl.textContent = `cyclaw${ver} · ${window.location.host} · ${data.mode || 'offline'}`;
    }
    if (Number.isFinite(data.graph_timeout_sec) && data.graph_timeout_sec > 0) {
      queryDeadlineMs = (data.graph_timeout_sec + 10) * 1000;
    }
    if (data.status !== 'ok') {
      statusDot.classList.add('error');
    }
    healthBackoffMs = HEALTH_BASE_INTERVAL;
  } catch (e) {
    statusDot.className = 'status-dot offline';
    statusText.textContent = 'gateway unreachable';
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
      fetchFn: function (path, init) { return fetch(API + path, init); },
    });
  }
}

async function openAuditPanel() {
  const panel = document.getElementById('auditPanel');
  const box = document.getElementById('auditSummary');
  if (panel) panel.classList.add('open');
  if (!box) return;
  const resp = await fetchWithTimeout(`${API}/auth/audit/summary`, {}, 15000);
  if (!resp.ok) {
    box.textContent = 'audit summary unavailable (' + resp.status + ')';
    return;
  }
  box.textContent = JSON.stringify(await resp.json(), null, 2);
}

async function submitQuery(confirmedOnline = null, onlineProvider = null) {
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

  const query = confirmedOnline !== null ? pendingConfirmQuery : input.value.trim();
  if (!query) return;
  if (confirmedOnline === null && (query === '/users' || query === '/admin')) {
    input.value = '';
    openUsersPanel();
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
      addEntry('error', 'ERROR', `${resp.status}: ${extractErrorMessage(err)}`);
      return;
    }

    const data = await resp.json();
    queryCount++;

    if (data.needs_confirm) {
      pendingConfirmQuery = query;
      addConfirmEntry(
        data.confirm_message || 'Low confidence. Send online?',
        Array.isArray(data.available_providers) ? data.available_providers : []
      );
      return;
    }

    const meta = [
      { k: 'model', v: data.model_used },
      { k: 'mode', v: data.retrieval_mode },
      { k: 'hits', v: data.hit_count },
      { k: 'time', v: `${elapsed}ms` }
    ];
    const answerEl = document.getElementById(addEntry('answer', 'ANSWER', data.answer, meta));
    if (answerEl && data.sources && data.sources.length > 0) {
      addSources(answerEl, data.sources);
    }

    if (data.error) {
      addEntry('error', 'WARNING', data.error);
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
  noBtn.textContent = providerBtns.length ? 'No — Stay Offline' : 'Offline Best Effort';
  noBtn.setAttribute('aria-label', 'Decline: stay offline with best-effort local');
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
  const providerLabel = onlineProvider === 'claude' ? 'Claude' : 'Grok';
  addEntry('system', '', confirmed ? `→ Escalating to ${providerLabel}...` : '→ Staying offline (best-effort local)');
  submitQuery(confirmed, onlineProvider);
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
      throw new Error(extractErrorMessage(data, 'Failed to load soul'));
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
      throw new Error(extractErrorMessage(data, 'Failed to reload soul'));
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
      throw new Error(extractErrorMessage(data, 'Failed to create proposal'));
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
      throw new Error(extractErrorMessage(data, 'Failed to apply soul evolution'));
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
      throw new Error(extractErrorMessage(data, 'Failed to restore soul'));
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
async function callOps(path, body) {
  // 60s ceiling: /ops/* shells out to CLIs (rclone, gh) that can stall; without
  // a timeout a hung subprocess would hang the browser tab indefinitely (parity
  // with the /query, /soul/*, and /health fetches which all bound their waits).
  const resp = await fetchWithTimeout(`${API}${path}`, {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(body)
  }, 60000);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) {
    throw new Error(extractErrorMessage(data, 'Ops request failed'));
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
    const data = await callOps('/ops/sync', { action, ...opts });
    applySyncConfig(data.config);
    renderOps(syncBox, syncMeta, syncWarning, syncPreview, data);
    setSyncStatus(`[${action}] ${syncLabelMsg(data)}`, data.ok ? 'success' : 'error');
    document.getElementById('syncBadge').textContent = `last: ${action} → ${data.label}`;
    addEntry('system', '', `→ ops/sync ${action} exit ${data.exit_code} (${data.label})`);
  } catch (e) {
    setSyncStatus(e.message, 'error');
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
  if (open && !syncLoaded && apiKeyInput.value.trim()) { syncLoaded = true; await runSync('status'); }
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
  } catch (e) {
    setAgenticStatus(e.message, 'error');
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
  if (open && !agenticLoaded && apiKeyInput.value.trim()) { agenticLoaded = true; await runAgentic('status'); }
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
  } catch (e) {
    setFsStatus(e.message, 'error');
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
  if (open && !fsLoaded && apiKeyInput.value.trim()) { fsLoaded = true; await runFs('status'); }
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
  } catch (e) {
    setSqlStatus(e.message, 'error');
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
  if (open && !sqlLoaded && apiKeyInput.value.trim()) { sqlLoaded = true; await runSql('status'); }
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
function scheduleHealthCheck() {
  if (healthTimer) clearTimeout(healthTimer);
  healthTimer = setTimeout(checkHealth, healthBackoffMs);
}
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
