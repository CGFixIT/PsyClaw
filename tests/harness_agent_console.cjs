'use strict';

// Runtime companion to test_harness_console_contract.py. Execute shipped
// functions; stub only DOM output and HTTP responses, never the command logic.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const html = fs.readFileSync(path.join(__dirname, '..', 'static', 'harness.html'), 'utf8');

function sourceFunction(name) {
  const match = new RegExp('^(?:async )?function ' + name + '\\(', 'm').exec(html);
  assert.ok(match, 'missing shipped function: ' + name);
  const end = html.indexOf('\n}', match.index);
  assert.ok(end > match.index, 'missing function end: ' + name);
  return html.slice(match.index, end + 2);
}

function browser(respond = () => ({status: 200, body: {ok: true, parsed: {status: 'running'}}})) {
  const calls = [];
  const messages = [];
  const constants = html.match(/^const (?:MAX_AGENT_\w+|AGENT_\w+_TIMEOUT_MS) = .+;.*$/gm) || [];
  const reviewState = html.match(/^const shownAgentDiffs = .+;$/m);
  if (reviewState) constants.push(reviewState[0]);
  const context = vm.createContext({
    URL, AbortController,
    pendingAgentRun: null, inflightChat: null, apiKeyInput: null, CSRF_TOKEN: '',
    sendBtn: {disabled: false}, stream: {textContent: ''}, loopState: null,
    window: {location: {href: 'http://127.0.0.1:8790/'}},
    paintGoalLoop() {},
    sys(message) { messages.push(message); },
    table(rows) { return rows.map(row => row.join(': ')).join('\n'); },
    async fetchWithTimeout(url, options) {
      const call = {url, method: options.method, body: options.body ? JSON.parse(options.body) : undefined};
      calls.push(call);
      const response = await respond(call);
      return {
        ok: response.status >= 200 && response.status < 300,
        status: response.status,
        headers: {get() { return null; }},
        async json() { return response.body; },
      };
    },
  });
  const functions = ['api', 'agentRecord', 'showPendingAgentRun', 'isReviewableAgentDiff', 'showAgentRecord', 'runSlash',
    'isSafeRepoRelativePath', 'canonicalRepoRelativePath'].map(sourceFunction).join('\n');
  vm.runInContext(constants.join('\n') + '\n' + functions, context);
  return {context, calls, messages};
}

async function stage(context) {
  await context.runSlash('/agent run codex/runtime-test update the docs');
  assert.ok(context.pendingAgentRun, 'run must be staged');
}

async function staging() {
  const b = browser();
  await b.context.runSlash('/agent iterations 2');
  assert.equal(b.context.pendingAgentRun, null);
  assert.match(b.messages.at(-1), /nothing staged/);
  await stage(b.context);
  for (const [command, field, value] of [
    ['iterations', 'max_iterations', 2], ['pr', 'pr', 42], ['issue', 'issue', 43],
  ]) {
    await b.context.runSlash('/agent ' + command + ' ' + value);
    assert.equal(b.context.pendingAgentRun[field], value);
    assert.ok(b.messages.at(-1).includes(String(value)), 'staged value must be visible');
  }
  assert.equal(Object.hasOwn(b.context.pendingAgentRun, 'pr'), false, 'issue clears pr');
  await b.context.runSlash('/agent pr 44');
  assert.equal(Object.hasOwn(b.context.pendingAgentRun, 'issue'), false, 'pr clears issue');
  assert.equal(b.calls.length, 0, 'staging must not send requests');
  await b.context.runSlash('/agent confirm reviewed the task');
  assert.equal(b.calls.length, 1);
  assert.equal(b.calls[0].url, '/api/agent/run');
  assert.equal(b.calls[0].method, 'POST');
  assert.equal(b.calls[0].body.max_iterations, 2);
  assert.equal(b.calls[0].body.pr, 44);
  assert.equal(Object.hasOwn(b.calls[0].body, 'issue'), false);
  assert.equal(b.calls[0].body.confirm, true);
  assert.equal(b.context.pendingAgentRun, null, 'accepted run clears staging');

  const issue = browser();
  await stage(issue.context);
  await issue.context.runSlash('/agent issue 45');
  await issue.context.runSlash('/agent confirm reviewed issue');
  assert.equal(issue.calls[0].body.issue, 45);
  assert.equal(Object.hasOwn(issue.calls[0].body, 'pr'), false);
  assert.equal(Object.hasOwn(issue.calls[0].body, 'max_iterations'), false);

  const clear = browser();
  await stage(clear.context);
  for (const command of ['iterations', 'pr', 'issue']) {
    await clear.context.runSlash('/agent ' + command + ' 2');
    await clear.context.runSlash('/agent ' + command + ' clear');
  }
  await clear.context.runSlash('/agent confirm default options');
  for (const field of ['max_iterations', 'pr', 'issue']) {
    assert.equal(Object.hasOwn(clear.calls[0].body, field), false, field + ' must be omitted');
  }

  const invalid = browser();
  await stage(invalid.context);
  await invalid.context.runSlash('/agent iterations 1');
  await invalid.context.runSlash('/agent pr 9');
  const before = JSON.stringify(invalid.context.pendingAgentRun);
  for (const command of ['iterations', 'pr', 'issue']) {
    for (const value of ['', '0', '-1', '1.5', '2junk', '1e2', 'NaN', 'Infinity',
      '9007199254740992', '2 extra', 'clear extra', '../2']) {
      await invalid.context.runSlash('/agent ' + command + ' ' + value);
      assert.equal(JSON.stringify(invalid.context.pendingAgentRun), before, command + ': ' + value);
      assert.match(invalid.messages.at(-1), /usage|integer|invalid/i);
    }
  }
  await invalid.context.runSlash('/agent iterations 11');
  assert.equal(JSON.stringify(invalid.context.pendingAgentRun), before);
  await invalid.context.runSlash('/agent iterations 10');
  assert.equal(invalid.context.pendingAgentRun.max_iterations, 10);
  await invalid.context.runSlash('/agent issue 9007199254740991');
  assert.equal(invalid.context.pendingAgentRun.issue, Number.MAX_SAFE_INTEGER);
  assert.equal(invalid.calls.length, 0);
}

async function github() {
  const b = browser(() => ({status: 200, body: {
    ok: false, label: 'github-status', exit_code: 4, stdout: '',
    stderr: 'GITHUB_AUTH_FAILED: authenticate gh before using this repository', parsed: null,
  }}));
  await b.context.runSlash('/github');
  assert.deepEqual(b.calls, [{url: '/api/github/status', method: 'GET', body: undefined}]);
  const output = b.messages.join('\n');
  assert.match(output, /ok=false/);
  assert.match(output, /GITHUB_AUTH_FAILED: authenticate gh/);
  assert.doesNotMatch(output, /GitHub ready|tree attached|context injected|write access granted/i);
}

async function refusals() {
  const typed = (status, code, message, details = {}) => ({status, body: {detail: {code, message, details}}});
  const responses = [
    [typed(409, 'AGENT_RUN_BUSY', 'another agent run is already in progress'), /AGENT_RUN_BUSY: another agent run/],
    [typed(409, 'AGENT_RUN_BUSY', 'a local model chat turn is already running'), /AGENT_RUN_BUSY: a local model chat/],
    [typed(422, 'AGENTIC_BUDGET_EXCEEDED', 'at 3 check profile(s) the most that fits is max_iterations=2',
      {max_iterations_that_fit: 2}), /AGENTIC_BUDGET_EXCEEDED:.*max_iterations=2/],
    [typed(403, 'TOOL_DENIED', 'agentic run is not in the broker allowlist'), /TOOL_DENIED: agentic run/],
    [typed(500, 'OPS_FAILED', 'operation failed'), /confirm failed — staged run kept.*OPS_FAILED/],
    [{status: 200, body: {ok: false, label: 'agent-run', exit_code: 4,
      stderr: 'explicit confirmation required', parsed: null}}, /exit 4\).*explicit confirmation required/s],
    [{status: 200, body: {ok: true, stdout: 'agentic.enabled is false', parsed: null}}, /disabled in config.yaml — nothing ran/],
  ];
  for (const [response, expected] of responses) {
    const b = browser(() => response);
    await stage(b.context);
    await b.context.runSlash('/agent iterations 2');
    await b.context.runSlash('/agent issue 42');
    b.context.pendingAgentRun.plan = 'Reviewed plan text';
    await b.context.runSlash('/agent read README.md');
    const staged = JSON.stringify(b.context.pendingAgentRun);
    await b.context.runSlash('/agent confirm');
    assert.equal(b.calls.length, 0, 'missing reason must not dispatch');
    await b.context.runSlash('/agent confirm reviewed this plan');
    assert.equal(b.calls.length, 1, 'refusal must not retry or dispatch another action');
    assert.equal(b.calls[0].url, '/api/agent/run');
    assert.equal(b.calls[0].body.plan, 'Reviewed plan text');
    assert.equal(JSON.stringify(b.context.pendingAgentRun), staged, 'refusal preserves the whole proposal');
    assert.match(b.messages.join('\n'), expected);
    assert.equal(b.context.sendBtn.disabled, false);
  }
}

async function review() {
  const initial = {run_id: 'run-1', status: 'pending_decision', diff: '-old\n+new'};
  let current = {...initial};
  const b = browser(call => ({status: 200, body: {ok: true, parsed:
    call.method === 'GET' ? {...current} : {...current, status: 'approved'}}}));
  const approve = () => b.context.runSlash('/agent approve run-1');
  await approve();
  assert.deepEqual(b.calls.map(c => c.method), ['GET'], 'unseen diff must not be approved');
  assert.match(b.messages.join('\n'), /candidate diff\n-old\n\+new/);
  await approve();
  assert.deepEqual(b.calls.map(c => c.method), ['GET', 'GET', 'POST']);
  assert.equal(b.calls.at(-1).url, '/api/agent/runs/run-1/decision');
  assert.deepEqual(b.calls.at(-1).body, {decision: 'approve'});

  await b.context.runSlash('/agent status run-1');
  current.diff = '-old\n+changed';
  b.calls.length = 0;
  await approve();
  assert.equal(b.calls.length, 1, 'changed diff requires another explicit command');
  assert.match(b.messages.join('\n'), /candidate diff\n-old\n\+changed/);
  await approve();
  assert.equal(b.calls.at(-1).method, 'POST');

  await b.context.runSlash('/agent status run-1');
  await b.context.runSlash('/clear');
  b.calls.length = 0;
  await approve();
  assert.equal(b.calls.length, 1, 'clear invalidates the displayed diff');

  for (const record of [
    {...initial, diff: ''}, {...initial, diff: null}, {...initial, diff: {text: 'not a diff'}},
    {...initial, diff: '   '}, {...initial, diff: '[diff unavailable: clone inaccessible]'},
    {...initial, diff: '[no diff to show -- the candidate reported changed files, but none were tracked or new]'},
    {...initial, diff: '-old\n+partial\n... [diff truncated at 20000 chars]'},
    {...initial, status: 'running'}, {...initial, run_id: 'different-run'},
  ]) {
    current = {...initial};
    await b.context.runSlash('/agent status run-1');
    current = record;
    b.calls.length = 0;
    await approve();
    assert.equal(b.calls.length, 1, 'invalid or unready run must not reach decision');
    assert.equal(b.calls[0].method, 'GET');
    assert.equal(b.context.sendBtn.disabled, false);
  }

  for (const response of [
    {status: 200, body: {ok: false, label: 'refused', exit_code: 4, stderr: 'status refused'}},
    {status: 200, body: {ok: true, parsed: null, stdout: 'disabled'}},
    {status: 500, body: {detail: {code: 'OPS_FAILED', message: 'status failed'}}},
  ]) {
    const failedStatus = browser(() => response);
    failedStatus.context.showAgentRecord(initial);
    await failedStatus.context.runSlash('/agent approve run-1');
    assert.equal(failedStatus.calls.length, 1, 'failed status cannot reuse a previous displayed diff');
    assert.equal(failedStatus.calls[0].method, 'GET');
    assert.equal(failedStatus.context.sendBtn.disabled, false);
  }

  const refused = browser(call => ({status: call.method === 'GET' ? 200 : 403,
    body: call.method === 'GET' ? {ok: true, parsed: initial} :
      {detail: {code: 'TOOL_DENIED', message: 'write denied'}}}));
  await stage(refused.context);
  const staged = JSON.stringify(refused.context.pendingAgentRun);
  await refused.context.runSlash('/agent status run-1');
  await refused.context.runSlash('/agent approve run-1');
  assert.equal(JSON.stringify(refused.context.pendingAgentRun), staged);
  assert.match(refused.messages.join('\n'), /TOOL_DENIED: write denied/);
  assert.equal(refused.context.sendBtn.disabled, false);
  refused.calls.length = 0;
  for (const command of ['reject', 'push', 'publish', 'discard']) {
    if (command === 'publish') {
      await refused.context.runSlash('/agent publish run-1');
      assert.equal(refused.calls.length, 2, 'publish needs its own reason');
    }
    await refused.context.runSlash('/agent ' + command + ' run-1' + (command === 'publish' ? ' reviewed publication' : ''));
    assert.equal(JSON.stringify(refused.context.pendingAgentRun), staged);
  }
  assert.deepEqual(refused.calls.map(c => c.url), [
    '/api/agent/runs/run-1/decision', '/api/agent/runs/run-1/push',
    '/api/agent/runs/run-1/publish', '/api/agent/runs/run-1/discard',
  ]);
  assert.deepEqual(refused.calls.map(c => c.body), [
    {decision: 'reject'}, {}, {reason: 'reviewed publication', confirm: true}, {},
  ]);
}

const scenarios = {staging, github, refusals, review};
const scenario = process.argv[2];
assert.ok(Object.hasOwn(scenarios, scenario), 'unknown runtime scenario');
scenarios[scenario]().then(() => console.log(scenario + ': passed')).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
