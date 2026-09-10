import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(
  new URL('../../harness/browser_use/report.py', import.meta.url),
  'utf8',
);
const match = source.match(/REPORT_JS = """([\s\S]*?)"""\nREPORT_STYLE/);
assert.ok(match, 'browser-use report JavaScript must remain extractable');

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
const browserControls = {
  'browser-a': {value: 'chromium', options: [{value: 'chromium'}, {value: 'moli'}], addEventListener() {}},
  'browser-relation': {value: 'mixed', options: [{value: 'mixed'}, {value: 'all_fail'}], addEventListener() {}},
  'browser-kind': {value: '', options: [{value: ''}, {value: 'RETRIEVE'}], addEventListener() {}},
  'browser-compare-count': {textContent: ''},
};
const comparisonLabel = {classList: {toggle() {}}};
const compareChecks = [
  {value: 'chromium', checked: false, disabled: false, closest() { return comparisonLabel; }, addEventListener() {}},
  {value: 'moli', checked: true, disabled: false, closest() { return comparisonLabel; }, addEventListener() {}},
];
const comparisonRows = [
  {dataset: {pass: 'chromium', fail: 'moli', kind: 'RETRIEVE'}},
  {dataset: {pass: 'chromium moli', fail: '', kind: 'NAVIGATE'}},
  {dataset: {pass: '', fail: 'chromium moli', kind: 'MUTATE'}},
];
const listeners = {};
const storage = new Map();
let replaced = '';
let scrolledTo = null;
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
    if (selector === '[data-browser-compare-control],[data-compare-engine]') return [...Object.values(browserControls).filter(x => x.addEventListener), ...compareChecks];
    if (selector === '[data-compare-engine]') return compareChecks;
    if (selector === '#browser-compare-rows tr') return comparisonRows;
    return [];
  },
  querySelector(selector) { return selector === '[data-search]' ? search : null; },
  getElementById(id) { return browserControls[id] ?? null; },
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
  scrollTo(_x, y) { scrolledTo = y; },
  scrollY: 240,
  addEventListener(name, callback) { listeners[name] = callback; },
});

vm.runInContext(match[1], context);
listeners.DOMContentLoaded();
assert.equal(search.value, 'missing');
assert.equal(controls[0].value, 'fail');
assert.deepEqual(rows.map(row => row.hidden), [false, true]);
assert.equal(count.textContent, '1 / 2');
assert.deepEqual(comparisonRows.map(row => row.hidden), [false, true, true]);
assert.equal(browserControls['browser-compare-count'].textContent, '找到 1 个任务');
assert.equal(compareChecks[0].disabled, true);

location.search = '?b_a=retired&b_relation=unknown&b_others=retired';
vm.runInContext('restoreView()', context);
assert.equal(browserControls['browser-a'].value, 'chromium');
assert.equal(browserControls['browser-relation'].value, 'mixed');
assert.equal(compareChecks[1].checked, true, 'invalid saved browsers must fall back to a valid comparison');

browserControls['browser-relation'].value = 'all_fail';
vm.runInContext('compareBrowsers()', context);
assert.deepEqual(comparisonRows.map(row => row.hidden), [true, true, false]);
assert.match(replaced, /b_relation=all_fail/);

controls[0].value = 'pass';
search.value = '';
vm.runInContext('filterRows()', context);
assert.deepEqual(rows.map(row => row.hidden), [true, false]);
assert.match(replaced, /f_result=pass/);
assert.doesNotMatch(replaced, /q=/);

scrolledTo = null;
location.search = '?f_result=fail&q=missing';
location.hash = '#same-task';
storage.set('browser-use-scroll:/analysis/?f_result=fail&q=missing', '999');
vm.runInContext('restoreView()', context);
assert.equal(scrolledTo, null, 'an explicit fragment must take precedence over saved scroll');

listeners.pagehide();
assert.equal(storage.get('browser-use-scroll:/analysis/?f_result=fail&q=missing'), '240');
console.log('PASS: browser-use filters restore from URL, update shareable state, and save scroll');
