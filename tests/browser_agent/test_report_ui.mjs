import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(
  new URL('../../harness/browser_agent/report.py', import.meta.url),
  'utf8',
);
const match = source.match(/REPORT_JS = """([\s\S]*?)"""\nREPORT_STYLE/);
assert.ok(match, 'browser-agent report JavaScript must remain extractable');

const controls = [
  {dataset: {filter: 'result'}, value: '', addEventListener() {}},
  {dataset: {filter: 'engine'}, value: '', addEventListener() {}},
];
const search = {value: '', addEventListener() {}};
const rows = [
  {dataset: {tier: 'hard', result: 'fail', engine: 'moli'}, textContent: 'missing item'},
  {dataset: {tier: 'easy', result: 'pass', engine: 'chromium'}, textContent: 'complete'},
];
const count = {textContent: ''};
const pairControls = [
  {dataset: {pairFilter: 'relation'}, value: 'all_fail', addEventListener() {}},
];
const pairedRows = [
  {dataset: {relation: 'all_fail'}},
  {dataset: {relation: 'all_pass'}},
  {dataset: {relation: 'mixed'}},
];
const pairCount = {textContent: ''};
const listeners = {};
const storage = new Map();
let replaced = '';
const location = {
  href: 'https://example.test/analysis/?f_result=fail&q=missing',
  pathname: '/analysis/',
  search: '?f_result=fail&q=missing',
  hash: '',
};
const document = {
  documentElement: {dataset: {}},
  addEventListener(name, callback) { listeners[name] = callback; },
  querySelectorAll(selector) {
    if (selector === '[data-filter]') return controls;
    if (selector === '[data-search]') return [search];
    if (selector === 'tbody tr[data-tier]') return rows;
    if (selector === '[data-visible-count]') return [count];
    if (selector === '[data-pair-filter]') return pairControls;
    if (selector === '#pair-rows tr') return pairedRows;
    if (selector === '[data-pair-count]') return [pairCount];
    return [];
  },
  querySelector(selector) { return selector === '[data-search]' ? search : null; },
  getElementById() { return null; },
};
const context = vm.createContext({
  URL,
  URLSearchParams,
  document,
  location,
  history: {replaceState(_state, _title, url) { replaced = url; }},
  localStorage: {getItem() { return null; }, setItem() {}},
  sessionStorage: {
    getItem(key) { return storage.get(key) ?? null; },
    setItem(key, value) { storage.set(key, value); },
  },
  requestAnimationFrame(callback) { callback(); },
  scrollTo() {},
  scrollY: 240,
  addEventListener(name, callback) { listeners[name] = callback; },
});

vm.runInContext(match[1], context);
listeners.DOMContentLoaded();
assert.equal(search.value, 'missing');
assert.equal(controls[0].value, 'fail');
assert.deepEqual(rows.map(row => row.hidden), [false, true]);
assert.equal(count.textContent, '1 / 2');
assert.deepEqual(pairedRows.map(row => row.hidden), [false, true, true]);
assert.equal(pairCount.textContent, '找到 1 个任务');

pairControls[0].value = 'mixed';
vm.runInContext('pairRows()', context);
assert.deepEqual(pairedRows.map(row => row.hidden), [true, true, false]);
assert.match(replaced, /p_relation=mixed/);

controls[0].value = 'pass';
search.value = '';
vm.runInContext('filterRows()', context);
assert.deepEqual(rows.map(row => row.hidden), [true, false]);
assert.match(replaced, /f_result=pass/);
assert.doesNotMatch(replaced, /q=/);

listeners.pagehide();
assert.equal(storage.get('browser-agent-scroll:/analysis/?f_result=fail&q=missing'), '240');
console.log('PASS: browser-agent filters restore from URL, update shareable state, and save scroll');
