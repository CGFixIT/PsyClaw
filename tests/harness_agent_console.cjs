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
  const functions = ['api', 'agentRecord', 'showPendingAgentRun', 'showAgentRecord', 'runSlash',
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

const scenarios = {staging};
const scenario = process.argv[2];
assert.ok(Object.hasOwn(scenarios, scenario), 'unknown runtime scenario');
scenarios[scenario]().then(() => console.log(scenario + ': passed')).catch(error => {
  console.error(error);
  process.exitCode = 1;
});
