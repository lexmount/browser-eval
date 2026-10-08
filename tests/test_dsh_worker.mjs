import assert from 'node:assert/strict';
import fs from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import vm from 'node:vm';

// Execute the maintained worker with synthetic upstream tool responses. Only
// external packages, stdin, stdout and timers are substituted; its loop and
// response projection execute unchanged.
const scenarios = [
  {
    name: 'declared value takes priority over presentation metadata',
    result: {content: [{type: 'text', text: 'Body'}, {type: 'image', text: 'ignored'}],
      value: {statusCode: 200, url: 'https://final.test/article'},
      meta: {statusCode: 404, url: 'https://presentation.test/'}},
    expected: {ok: true, content: 'Body', status_code: 200, final_url: 'https://final.test/article'},
  },
  {
    name: 'declared presentation projection supplies missing value',
    result: {content: [{type: 'text', text: ''}], meta: {statusCode: 201, url: 'https://final.test/created'}},
    expected: {ok: true, content: '', status_code: 201, final_url: 'https://final.test/created'},
  },
  {
    name: 'HTTP failure cannot become success from an answer header',
    result: {content: [{type: 'text', text: 'Fetched https://requested.test/ (HTTP 200)\nBody'}],
      value: {statusCode: 404, url: 'https://final.test/missing'}},
    expected: {ok: false, status_code: 404, error_type: 'http_status', error_message: 'Origin returned HTTP 404'},
  },
  {
    name: 'a content header cannot invent a missing declared status or final URL',
    result: {content: [{type: 'text', text: 'Fetched https://requested.test/ (HTTP 200)\nBody'}]},
    expected: {ok: false, error_type: 'invalid_response', error_message: 'DeepSeek Harness web_fetch returned no HTTP status'},
  },
  {
    name: 'boolean status is unavailable',
    result: {content: [{type: 'text', text: 'Body'}], value: {statusCode: true}},
    expected: {ok: false, error_type: 'invalid_response'},
  },
  {
    name: 'tool failure cannot borrow a success value',
    result: {isError: true, content: [{type: 'text', text: 'Declared tool failure'}],
      value: {statusCode: 200, url: 'https://untrusted.test/'}, error: {info: {code: 'fixture_failure'}}},
    expected: {ok: false, error_type: 'fixture_failure', error_message: 'Declared tool failure'},
  },
  {
    name: 'an exception clears its deadline and does not publish the stack',
    error: Object.assign(new Error('Synthetic exception'), {code: 'fixture_exception', stack: 'private-stack-marker'}),
    expected: {ok: false, error_type: 'fixture_exception', content: 'Synthetic exception', error_message: 'Synthetic exception'},
  },
  {
    name: 'the next call remains independent after an exception',
    result: {content: [{type: 'text', text: 'Next body'}], value: {statusCode: 200}},
    expected: {ok: true, content: 'Next body', status_code: 200},
  },
];
const requests = scenarios.map((_, index) => ({url: `https://requested.test/${index}`, timeout_seconds: 0.05}));
const calls = [];
const outputs = [];
const timers = new Set();
let next = 0;
class Context {
  tools = {execute: async (call) => {
    calls.push(call);
    const scenario = scenarios[next++];
    if (scenario.error) throw scenario.error;
    return scenario.result;
  }};
  async plugin() {}
}
const readline = {createInterface: () => ({async *[Symbol.asyncIterator]() {
  for (const request of requests) yield JSON.stringify(request);
}})};
const context = vm.createContext({
  AbortController,
  process: {stdin: {}, stdout: {write: (line) => outputs.push(JSON.parse(line))}},
  setTimeout: () => {const timer = {}; timers.add(timer); return timer;},
  clearTimeout: (timer) => timers.delete(timer),
});
const fixtures = new Map([
  ['node:readline', {default: readline}],
  ['@deepseek-ai/cordis', {Context}],
  ['@deepseek-ai/dsh-llm', {ToolCallId: (value) => value}],
  ['@deepseek-ai/dsh-web-fetch-http', {LOCAL_FETCH_PROVIDER_ID: 'synthetic'}],
]);
const source = stripTypeScriptTypes(fs.readFileSync(process.argv[2], 'utf8'));
const worker = new vm.SourceTextModule(source, {context});
await worker.link((specifier) => {
  const exports = fixtures.get(specifier) ?? {default: {}};
  return new vm.SyntheticModule(Object.keys(exports), function () {
    for (const [name, value] of Object.entries(exports)) this.setExport(name, value);
  }, {context});
});
await worker.evaluate();
assert.equal(outputs.length, scenarios.length);
assert.equal(calls.length, scenarios.length);
for (let index = 0; index < scenarios.length; index++) {
  const scenario = scenarios[index];
  const output = outputs[index];
  for (const [field, expected] of Object.entries(scenario.expected)) {
    assert.deepEqual(output[field], expected, `${scenario.name}: ${field}`);
  }
  assert.deepEqual(JSON.parse(JSON.stringify(calls[index].arguments)), {url: requests[index].url});
  assert.equal(calls[index].name, 'web_fetch');
  assert.equal(JSON.stringify(output).includes('private-stack-marker'), false);
  if (![0, 1, 2].includes(index)) assert.equal(output.final_url, undefined);
  if ([3, 4, 5, 6].includes(index)) assert.equal(output.status_code, undefined);
}
assert.equal(timers.size, 0, 'Every completed or failed call must clear its deadline');
console.log(`PASS: ${scenarios.length} actual DSH worker response and deadline scenarios`);
