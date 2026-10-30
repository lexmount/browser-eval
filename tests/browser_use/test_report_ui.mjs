import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(
  new URL('../../src/harness/browser_use/report.py', import.meta.url),
  'utf8',
);
const match = source.match(/REPORT_JS = """([\s\S]*?)"""\nREPORT_STYLE/);
assert.ok(match, 'browser-use report JavaScript must remain extractable');

const controls = [
  {dataset: {filter: 'result'}, value: '', addEventListener() {}},
  {dataset: {filter: 'model'}, value: '', addEventListener() {}},
  {dataset: {filter: 'engine'}, value: '', addEventListener() {}},
];
const search = {value: '', addEventListener() {}};
const rows = [
  {dataset: {tier: 'hard', result: 'fail', model: 'model-a', engine: 'moli'}, textContent: 'missing item'},
  {dataset: {tier: 'easy', result: 'pass', model: 'model-a', engine: 'chromium'}, textContent: 'complete'},
];
const count = {textContent: ''};
const matrixControls = [
  {dataset: {matrixFilter: 'relation'}, value: 'mixed', addEventListener() {}},
  {dataset: {matrixFilter: 'kind'}, value: '', addEventListener() {}},
];
const comparisonRows = [
  {dataset: {tier: 'hard', relation: 'mixed', kind: 'RETRIEVE'}},
  {dataset: {tier: 'easy', relation: 'all_pass', kind: 'NAVIGATE'}},
  {dataset: {tier: 'medium', relation: 'all_fail', kind: 'MUTATE'}},
];
const matrixCount = {textContent: ''};
const matrixRoot = {
  querySelectorAll(selector) {
    if (selector === '[data-matrix-filter]') return matrixControls;
    if (selector === 'tbody tr[data-tier]') return comparisonRows;
    return [];
  },
  querySelector(selector) { return selector === '[data-matrix-count]' ? matrixCount : null; },
};
const diagnosisDialog = {
  open: false,
  showModal() { this.open = true; },
  close() { this.open = false; },
  addEventListener() {},
};
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
    if (selector === 'tbody tr[data-tier]:not([data-relation])') return rows;
    if (selector === '[data-visible-count]') return [count];
    if (selector === '[data-matrix-filter]') return matrixControls;
    if (selector === 'dialog') return [diagnosisDialog];
    return [];
  },
  querySelector(selector) { return selector === '[data-search]' ? search : null; },
  getElementById(id) {
    if (id === 'configuration-matrix') return matrixRoot;
    if (id === 'diagnosis-example') return diagnosisDialog;
    return null;
  },
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
assert.equal(matrixCount.textContent, '1 / 3');

matrixControls[0].value = 'all_fail';
vm.runInContext('filterMatrix()', context);
assert.deepEqual(comparisonRows.map(row => row.hidden), [true, true, false]);

vm.runInContext("openDiagnosis('diagnosis-example')", context);
assert.equal(diagnosisDialog.open, true);
vm.runInContext("closeDiagnosis('diagnosis-example')", context);
assert.equal(diagnosisDialog.open, false);

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
