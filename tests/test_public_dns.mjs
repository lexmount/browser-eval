import assert from 'node:assert/strict';
import { test } from 'node:test';
import { createPublicDnsLookup } from '../src/harness/web_fetch/workers/public_dns.mjs';

const response = payload => new Response(JSON.stringify(payload));
test('same resolver rule queries A and AAAA and retains private answers for native validation', async () => {
  const queries = [], traces = [];
  const lookup = createPublicDnsLookup(new AbortController().signal, traces, async (url, options) => {
    queries.push(url.href);
    assert.equal(options.redirect, 'error');
    const type = Number(url.searchParams.get('type'));
    return response({ Status: 0, Answer: [{ type, data: type === 1 ? '10.0.0.1' : '::1' }] });
  });
  assert.deepEqual(await lookup('fixture.example'), [{ address: '10.0.0.1', family: 4 }, { address: '::1', family: 6 }]);
  assert.equal(queries.length, 2);
  assert.equal(traces.length, 1);
});
test('DNS errors do not trigger a fallback or retry', async () => {
  let queries = 0;
  const lookup = createPublicDnsLookup(new AbortController().signal, [], async () => { queries++; throw new Error('unavailable'); });
  await assert.rejects(lookup('fixture.example'), /unavailable/);
  assert.equal(queries, 2); // one A and one AAAA, concurrently, never another attempt
});
test('aborted deadline performs no DNS query', async () => {
  const controller = new AbortController(); controller.abort();
  const lookup = createPublicDnsLookup(controller.signal, [], async () => assert.fail('No queries'));
  await assert.rejects(lookup('fixture.example'), { name: 'AbortError' });
});
test('malformed, empty, truncated and oversized DNS answers are rejected', async () => {
  for (const payload of [{Status:2}, {Status:0,TC:true}, {Status:0}, {Status:0,Answer:[{type:1,data:'::1'}]}]) {
    const lookup = createPublicDnsLookup(new AbortController().signal, [], async () => response(payload));
    await assert.rejects(lookup('fixture.example'));
  }
  const lookup = createPublicDnsLookup(new AbortController().signal, [], async () => new Response('x'.repeat(65537)));
  await assert.rejects(lookup('fixture.example'), /64 KiB/);
});
test('literal IPs require no DNS query and still reach native validation', async () => {
  const lookup = createPublicDnsLookup(new AbortController().signal, [], async () => assert.fail('No DNS query'));
  assert.deepEqual(await lookup('127.0.0.1'), [{address:'127.0.0.1',family:4}]);
});
