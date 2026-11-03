// Synthetic DOM contract tests; no browser or provider calls.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../../src/harness/web_scraper/report_ui.py', import.meta.url), 'utf8');
const js = source.match(/JS = r"""([\s\S]*?)"""/)[1];
let focused;
class Element {
  constructor(tag, text = '') { this.tagName = tag.toUpperCase(); this.text = text; this.children = []; this.dataset = {}; this.listeners = {}; this.attributes = {}; }
  get textContent() { return this.text + this.children.map(x => x.textContent).join(''); }
  set textContent(text) { this.text = text; this.children = []; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  addEventListener(name, action) { this.listeners[name] = action; }
  setAttribute(name, value) { this.attributes[name] = value; }
  removeAttribute(name) { delete this.attributes[name]; }
  getAttribute(name) { return this.attributes[name]; }
  focus() { focused = this; }
}
const controls = ['status', 'method', 'field', 'stage', 'query'].map(key => {
  const e = new Element(key === 'query' ? 'input' : 'select');
  e.dataset.resultFilter = key; e.value = key === 'status' ? 'fail' : ''; return e;
});
const control = key => controls.find(x => x.dataset.resultFilter === key);
const rows = [
  {dataset: {method: 'a', status: 'pass', price: 'correct', outcome: 'correct', search: 'alpha'}},
  {dataset: {method: 'b', status: 'fail', price: 'missing', outcome: 'wrong', stage: 'model_capacity', search: 'beta CAPACITY'}},
  {dataset: {method: 'b', status: 'pending', outcome: 'pending', search: 'not executed'}},
];
const form = {querySelectorAll() { return controls; }};
const count = new Element('output');
const head = new Element('th', 'Seconds');
const sorted = [9, 0.003, null, 0].map(value => {
  const cell = new Element('td', value === null ? '—' : String(value));
  cell.dataset.sort = value === null ? '' : String(value); return {cells: [cell]};
});
const tbody = {rows: [...sorted], append(row) { this.rows.splice(this.rows.indexOf(row), 1); this.rows.push(row); }};
const table = {tHead: {rows: [{cells: [head]}]}, tBodies: [tbody]};
const dialog = new Element('dialog');
const body = new Element('div');
const close = new Element('button', 'Close');
dialog.querySelector = selector => selector === '[data-call-log-close]' ? close : body;
dialog.showModal = () => { dialog.open = true; };
dialog.close = () => { dialog.open = false; dialog.listeners.close?.(); };
const button = new Element('button', 'Inspect');
button.dataset = {callLogOpen: 'diagnosis', src: 'public-evidence/b.json', key: 'case', field: 'price'};
button.closest = selector => selector === '[data-call-log-open]' ? button : null;
const documentListeners = {};
const classes = new Set();
let fetched = 0;
const response = {method: 'b', case_id: 'case', http_status: 422, failure_stage: 'model_capacity',
  native_error: 'Context window exceeded <script>unsafe()</script>', elapsed_seconds: 2.5,
  diagnosis: {cause: 'Complete source exceeds hosted model capacity', treatment: 'Full input retained'},
  field_results: {price: {expected: ['12.5'], actual: null, status: 'missing'}}};
const context = vm.createContext({URL, URLSearchParams, Map, Error, String, Number,
  location: {search: '?method=b&field=price&status=fail'},
  localStorage: {getItem() { return null; }, setItem() {}},
  fetch: async () => { fetched++; return {ok: true, json: async () => ({case: response})}; },
  document: {baseURI: 'http://localhost/preview/token/', documentElement: {lang: 'zh', classList: {
    toggle(key, on) { on ? classes.add(key) : classes.delete(key); }, contains(key) { return classes.has(key); }}},
    createElement: tag => new Element(tag),
    addEventListener: (name, action) => { documentListeners[name] = action; },
    getElementById: id => id === 'diagnosis' ? dialog : null,
    querySelector(selector) { return selector === '[data-result-controls]' ? form : selector === '[data-result-count]' ? count : null; },
    querySelectorAll(selector) { return selector === 'table[data-sortable]' ? [table] : selector === 'tr[data-status]' ? rows : []; },
  },
});
vm.runInContext(js, context);
assert.deepEqual(rows.map(x => x.hidden), [true, false, true]);
assert.match(count.textContent, /1/);
rows[1].dataset.price = 'correct';
control('status').listeners.change();
assert.equal(rows[1].hidden, false, 'Native call failures remain visible with a field filter');
rows[1].dataset.price = 'missing';
control('status').value = 'all'; control('status').listeners.change();
assert.deepEqual(rows.map(x => x.hidden), [true, false, false]);
control('query').value = 'CAPACITY'; control('query').listeners.input();
assert.deepEqual(rows.map(x => x.hidden), [true, false, true]);
head.children[0].listeners.click();
assert.deepEqual(tbody.rows.map(x => x.cells[0].textContent), ['0', '0.003', '9', '—']);
head.children[0].listeners.click();
assert.deepEqual(tbody.rows.map(x => x.cells[0].textContent), ['9', '0.003', '0', '—']);
assert.equal(head.getAttribute('aria-sort'), 'descending');
await documentListeners.click({target: button});
assert.equal(dialog.open, true);
assert.equal(focused, close);
assert.match(body.textContent, /Context window exceeded <script>unsafe\(\)<\/script>/);
assert.match(body.textContent, /12.5/);
assert.match(body.textContent, /Complete source exceeds hosted model capacity/);
assert.ok(body.children.some(x => x.children.some?.(c => c.tagName === 'A')));
let prevented = false;
dialog.listeners.cancel({preventDefault() { prevented = true; }});
assert.equal(prevented, true);
assert.equal(dialog.open, false);
assert.equal(focused, button);
await documentListeners.click({target: button});
assert.equal(fetched, 1, 'Repeated inspection reuses the same complete evidence');
dialog.close();
response.elapsed_seconds = null;
await documentListeners.click({target: button});
assert.match(body.textContent, /Context window exceeded/);
assert.doesNotMatch(body.textContent, /读取失败/);
console.log('PASS: method/field/query filters, pending scope, numeric sorting, exact lazy errors, text-only evidence, Escape and focus restoration');

// A slow response or rejection from a closed selection cannot replace the new selection.
for (const rejectOld of [false, true]) {
  dialog.close();
  let finishA, finishB, failA;
  context.fetch = url => new Promise((resolve, reject) => {
    if (url.includes('slow-a')) { finishA = resolve; failA = reject; }
    else finishB = resolve;
  });
  button.dataset.src = `public-evidence/slow-a-${rejectOld}.json`;
  const pendingA = documentListeners.click({target: button});
  dialog.close();
  button.dataset.src = `public-evidence/fast-b-${rejectOld}.json`;
  const pendingB = documentListeners.click({target: button});
  finishB({ok: true, json: async () => ({case: {...response, native_error: 'CURRENT B'}})});
  await pendingB;
  assert.match(body.textContent, /CURRENT B/);
  if (rejectOld) failA(new Error('OBSOLETE A ERROR'));
  else finishA({ok: true, json: async () => ({case: {...response, native_error: 'OBSOLETE A'}})});
  await pendingA;
  assert.match(body.textContent, /CURRENT B/);
  assert.doesNotMatch(body.textContent, /OBSOLETE A/);
}
console.log('PASS: stale dialog success and rejection cannot overwrite a newer selection');
