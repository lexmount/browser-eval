// Synthetic DOM contract tests; no browser or provider calls.
import assert from 'node:assert/strict';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import vm from 'node:vm';

const rendered = spawnSync('uv', ['run', '--frozen', '--no-sync', 'python', '-c',
  'import json; from harness.web_extract.report_ui import JS; from tests.web_extract.sorting_fixture import generated_sorting_tables, failed_unscored_record, generated_quality_heading; print(json.dumps({"js":JS,"tables":generated_sorting_tables(),"failedUnscored":failed_unscored_record(),"qualityHeading":generated_quality_heading()}))'],
  {cwd: fileURLToPath(new URL('../..', import.meta.url)), encoding: 'utf8'});
assert.equal(rendered.status, 0, rendered.stderr);
const produced = JSON.parse(rendered.stdout);
const js = produced.js;
const payload = js.match(/const REPORT_MESSAGES = (.*);/)[1];
const catalog = JSON.parse(payload);
let focused;
class Element {
  constructor(tag, text = '') { this.tagName = tag.toUpperCase(); this.text = text; this.children = []; this.dataset = {}; this.listeners = {}; this.attributes = {}; }
  get nodeName() { return this.tagName; }
  get childNodes() { return [{nodeName: '#text', textContent: this.text}, ...this.children]; }
  querySelector(selector) { return ['small', 'small[data-reference-population]'].includes(selector) ? this.children.find(x => x.tagName === 'SMALL') || null : null; }
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
  {dataset: {method: 'b', status: 'pending', price: 'pending', outcome: 'pending', search: 'not executed'}},
];
const form = new Element('form');
form.querySelectorAll = () => controls;
form.querySelector = () => null;
const datasetQuery = new Element('input'); datasetQuery.value = '';
const datasetCount = new Element('output');
const datasetRows = [new Element('tr', '汽车 alpha page-1'), new Element('tr', '图书 beta page-2')];
const datasetForm = new Element('form');
datasetForm.querySelector = selector => selector === 'input' ? datasetQuery : datasetCount;
const datasetTable = {querySelectorAll: () => datasetRows};
const count = new Element('output');
const head = new Element('th', 'Seconds');
const populationHeading = new Element('th', produced.qualityHeading.label);
const populationNote = new Element('small', produced.qualityHeading.note);
populationNote.dataset.referencePopulation = 'price';
populationHeading.append(new Element('br'), populationNote);
const metadataHeading = new Element('th', 'Whole-page accuracy');
const metadataNote = new Element('small', 'Project supplement');
metadataHeading.append(new Element('br'), metadataNote);
const metadataTable = {tHead: {rows: [{cells: [metadataHeading]}]}, tBodies: [{rows: []}]};
const populationTable = {tHead: {rows: [{cells: [populationHeading]}]}, tBodies: [{rows: []}]};
const sorted = [9, 0.003, null, 0].map(value => {
  const cell = new Element('td', value === null ? '—Scored 0/2; excluded 2' : String(value));
  if (value === null) cell.dataset.metric = 'audited-price'; else cell.dataset.sort = String(value); return {cells: [cell]};
});
const tbody = {rows: [...sorted], append(row) { this.rows.splice(this.rows.indexOf(row), 1); this.rows.push(row); }};
const table = {tHead: {rows: [{cells: [head]}]}, tBodies: [tbody]};
const generatedTables = produced.tables.map(spec => {
  const heads = spec.rows[0].map((_, i) => new Element('th', String(i)));
  const generatedRows = spec.rows.map(row => ({cells: row.map(cell => {
    const e = new Element('td', cell.text); if (cell.sort !== null) e.dataset.sort = cell.sort; return e;
  })}));
  const generatedBody = {rows: generatedRows, append(row) {this.rows.splice(this.rows.indexOf(row), 1); this.rows.push(row);}};
  return {tHead: {rows: [{cells: heads}]}, tBodies: [generatedBody], column: spec.column};
});
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
    querySelector(selector) {
      if (selector === '[data-dataset-inventory]') return datasetTable;
      if (selector === '[data-result-controls]') return form;
      if (selector === '[data-result-count]') return count;
      return null;
    },
    querySelectorAll(selector) {
      if (selector === 'form[data-result-controls], form[data-dataset-controls]') return [form, datasetForm];
      if (selector === 'form[data-dataset-controls]') return [datasetForm];
      if (selector === 'table[data-sortable]') return [table, populationTable, metadataTable, ...generatedTables];
      if (selector === 'tr[data-status]') return rows;
      return [];
    },
  },
});
vm.runInContext(js, context);
assert.deepEqual(rows.map(x => x.hidden), [true, false, true]);
// A delivered API response can still contain wrong fields. Default quality
// filters must not equate transport success with matching the reference.
const savedControls = controls.map(item => item.value);
const deliveredWrong = {dataset: {method: 'a', status: 'pass', outcome: 'wrong',
  stage: 'fields', price: 'wrong', search: 'delivered but wrong fields'}};
rows.push(deliveredWrong);
for (const item of controls) item.value = item.dataset.resultFilter === 'status' ? 'fail' : '';
control('status').listeners.change();
assert.equal(deliveredWrong.hidden, false, 'Quality failure view includes successful calls with wrong fields');
assert.equal(rows[0].hidden, true, 'A matching response stays out of the quality failure view');
control('status').value = 'pass'; control('status').listeners.change();
assert.equal(deliveredWrong.hidden, true, 'Quality pass view excludes wrong fields even after successful delivery');
assert.equal(rows[0].hidden, false, 'Quality pass view retains a matching response');
assert.equal(rows[1].hidden, true, 'Quality pass view excludes transport failures');
rows.pop();
controls.forEach((item, index) => { item.value = savedControls[index]; });
control('status').listeners.change();
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
assert.deepEqual(tbody.rows.map(x => x.cells[0].textContent), ['0', '0.003', '9', '—Scored 0/2; excluded 2']);
head.children[0].listeners.click();
assert.deepEqual(tbody.rows.map(x => x.cells[0].textContent), ['9', '0.003', '0', '—Scored 0/2; excluded 2']);
assert.equal(head.getAttribute('aria-sort'), 'descending');
assert.equal(populationHeading.children[0].tagName, 'BUTTON');
assert.equal(populationHeading.children[0].textContent, produced.qualityHeading.label + ' ↕');
assert.deepEqual(populationHeading.children.map(x => x.tagName), ['BUTTON', 'SMALL']);
assert.equal(populationHeading.children[1], populationNote);
assert.equal(populationNote.textContent, produced.qualityHeading.note);
assert.equal(metadataHeading.children[0].textContent, 'Whole-page accuracy ↕');
assert.equal(metadataHeading.children[1], metadataNote);
assert.equal(metadataNote.textContent, 'Project supplement');
for (const [index, generated] of generatedTables.entries()) {
  const expected = [[2, 10], [2, 10], [4, 10, 12], [0, 0, 0, 0, 2, 10], [0, 0, 0, 0, 2, 12], [2.001, 10.001], [0, 2, 10], [0, 2, 12]][index];
  const button = generated.tHead.rows[0].cells[generated.column].children[0];
  button.listeners.click();
  const values = () => generated.tBodies[0].rows.map(row => Number(row.cells[generated.column].dataset.sort));
  assert.deepEqual(values(), expected, 'Generated numeric cells must sort numerically');
  button.listeners.click();
  assert.deepEqual(values(), [...expected].reverse());
}

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

const incomplete = structuredClone(catalog);
delete incomplete.zh.field_labels_price;
const incompleteJs = js.replace(payload, JSON.stringify(incomplete));
assert.throws(() => vm.runInNewContext(incompleteJs, {...context}), /Missing report translation: zh\/field_labels_price/);
console.log('PASS: missing client translations fail explicitly');

// A diagnosis describes the selected method's output, while page findings stay source evidence.
const savedDiagnosis = response.diagnosis;
const savedError = response.native_error;
const savedStage = response.failure_stage;
response.diagnosis = null;
response.native_error = '';
response.failure_stage = 'client_transport';
response.review = {
  source_field_review: {price: {reason: 'PAGE-LEVEL FINDING', status: 'supported', values: [], source_quote: ''}},
  reviewer_fields: {price: {reason: 'THIS METHOD MISSED THE PRICE', status: 'incorrect'}},
};
button.dataset.src = "public-evidence/method-review.json";
context.fetch = async () => ({ok: true, json: async () => ({case: response})});
await documentListeners.click({target: button});
const facts = body.children.find(element => element.tagName === 'DL');
const causeIndex = facts.children.findIndex(element => element.textContent === catalog.zh.reviewed_cause);
assert.equal(facts.children[causeIndex + 1].textContent, 'THIS METHOD MISSED THE PRICE');
assert.match(facts.textContent, new RegExp(catalog.zh.stages_client_transport));
assert.match(facts.textContent, new RegExp(catalog.zh.no_recognizable_product_fields_returned));
assert.doesNotMatch(facts.textContent, new RegExp(catalog.zh.structured_fields_returned_inspect_field_differences));
response.diagnosis = savedDiagnosis;
response.native_error = savedError;
response.failure_stage = savedStage;
delete response.review;
console.log('PASS: method-specific reviewed cause, consistent stage labels and localized empty-error fallback');

context.stockBody = new Element('div');
context.stockRow = {
  compatibility: 'structured_output', field_results: {
    price: {expected: ['1'], actual: '1', status: 'correct'},
    sku: {expected: ['sku'], actual: 'sku', status: 'correct'},
    availability: {expected: ['OutOfStock'], actual: 'OutOfStock', status: 'correct'},
    OutOfStock: {expected: ['OutOfStock'], actual: null, status: 'missing'},
  },
};
vm.runInContext('diagnosisContent(stockBody, stockRow)', context);
const descendants = element => [element, ...element.children.flatMap(descendants)];
const stockTable = descendants(context.stockBody).find(e => e.tagName === 'TABLE');
assert.equal(stockTable.children.length, 5);
const selectedStock = stockTable.children.find(e => e.className === 'selected-field');
assert.equal(selectedStock.children[0].textContent, catalog.zh.field_labels_OutOfStock);
assert.match(selectedStock.textContent, new RegExp(catalog.zh.outcomes_missing));
console.log('PASS: out-of-stock-only mismatch is displayed and selected automatically');

// A page failing another field must still appear as a pass for the selected field.
rows.push({dataset: {method: 'a', status: 'fail', stage: 'fields', price: 'correct', title: 'wrong', domain: 'book', site: 'amazon'}});
rows.push({dataset: {method: 'a', status: 'fail', stage: 'fields', engine: 'wrong', domain: 'auto', site: 'aol'}});
control('method').value = 'a';
control('query').value = '';
control('field').value = 'price';
control('status').value = 'fail'; control('status').listeners.change();
assert.equal(rows[3].hidden, true, 'A correct selected field is not a field failure');
assert.equal(rows[4].hidden, true, 'An unrelated schema must not enter the field denominator');
control('status').value = 'pass'; control('status').listeners.change();
assert.equal(rows[3].hidden, false);
const domainControl = new Element('select'); domainControl.dataset.resultFilter = 'domain'; domainControl.value = 'book'; controls.push(domainControl);
const siteControl = new Element('select'); siteControl.dataset.resultFilter = 'site'; siteControl.value = 'amazon'; controls.push(siteControl);
control('status').listeners.change();
assert.deepEqual(rows.map(x => x.hidden), [true, true, true, false, true]);
const recordBody = new Element('div');
context.recordBody = recordBody;
context.recordRow = {record_schema: true, method_label: 'Native', case_id: 'sample', http_status: 402, response: {}, native_error: '', result_href: 'zh/detail.html', field_labels: {title: '标题'}, field_results: {title: {expected: ['A', 'B'], actual: ['A'], status: 'partial'}}};
vm.runInContext('diagnosisContent(recordBody, recordRow)', context);
assert.match(recordBody.textContent, /标题/);
assert.match(recordBody.textContent, /A/);
assert.match(recordBody.textContent, /B/);
assert.ok(recordBody.children.some(x => x.tagName === 'PRE' && x.textContent === '{}'));
assert.ok(recordBody.children.some(x => x.tagName === 'A' && x.href === 'zh/detail.html'));
console.log('PASS: category/site filters, selected-field populations, multi-value record evidence and native empty-error bodies');

// Following a same-document result fragment must dismiss its modal first.
const fullRecordLink = recordBody.children.find(x => x.tagName === 'A');
fullRecordLink.closest = selector => selector === 'dialog' ? dialog : null;
dialog.showModal();
fullRecordLink.listeners.click();
assert.equal(dialog.open, false, 'Full result navigation closes an enclosing modal');

// Wholly unannotated reference groups are visible in All, neither pass nor fail.
for (const item of controls) item.value = '';
const unannotated = {dataset: {method: 'a', status: 'pass', stage: 'fields', title: 'unavailable', author: 'correct'}};
rows.push(unannotated);
control('field').value = 'title';
for (const [status, hidden] of [['all', false], ['pass', true], ['fail', true]]) {
  control('status').value = status; control('status').listeners.change();
  assert.equal(unannotated.hidden, hidden, 'Unannotated field selection: ' + status);
}
control('field').value = 'author';control('status').value = 'pass';control('status').listeners.change();
assert.equal(unannotated.hidden, false, 'Scored fields on the same record remain selectable');
console.log('PASS: same-document modal navigation and unannotated-field quality subsets');
const pendingBook = {dataset: {method: 'a', status: 'pending', outcome: 'pending', author: 'pending'}};
const pendingCar = {dataset: {method: 'a', status: 'pending', outcome: 'pending', model: 'pending'}};
rows.push(pendingBook, pendingCar);
control('field').value = 'author';control('status').value = 'all';control('status').listeners.change();
assert.equal(pendingBook.hidden, false);
assert.equal(pendingCar.hidden, true, 'Pending pages must request the selected field');

for (const filterForm of [form, datasetForm]) {
  let prevented = false;
  filterForm.listeners.submit({preventDefault() { prevented = true; }});
  assert.equal(prevented, true, 'Filter forms never navigate on Enter');
}
datasetQuery.value = 'beta'; datasetForm.listeners.input();
assert.deepEqual(datasetRows.map(row => row.hidden), [true, false]);
assert.equal(datasetCount.textContent, '1 / 2');
datasetQuery.value = '汽车'; datasetForm.listeners.input();
assert.deepEqual(datasetRows.map(row => row.hidden), [false, true]);
console.log('PASS: dataset page/site/category search stays separate from result filters, and Enter preserves controls');

// Compare three or more methods on the same page, without selecting a shared-pass subset.
const comparisonRows = [];
for (const [id, outcomes] of Object.entries({
  shared: [true, true, true], baseline: [true, false, false],
  mixed: [false, true, false], failed: [false, false, false],
})) for (const [i, method] of ['a', 'b', 'c'].entries()) comparisonRows.push({dataset: {
  case: id, method, status: outcomes[i] ? 'pass' : 'fail', outcome: outcomes[i] ? 'correct' : 'wrong',
  price: outcomes[i] ? 'correct' : 'wrong', stage: 'fields',
}});
comparisonRows.push({dataset: {case: 'pending', method: 'a', status: 'pass'}});
comparisonRows.push({dataset: {case: 'pending', method: 'b', status: 'pending'}});
comparisonRows.push({dataset: {case: 'pending', method: 'c', status: 'fail'}});
context.comparisonRows = comparisonRows;
const matching = relation => Array.from(vm.runInContext(`matchingCaseIds(comparisonRows, 'a', ['b','c'], '${relation}', '')`, context));
assert.deepEqual(matching('baseline_only'), ['baseline']);
assert.deepEqual(matching('comparison_only'), ['mixed']);
assert.deepEqual(matching('different'), ['baseline', 'mixed']);
assert.deepEqual(matching('all_pass'), ['shared']);
assert.deepEqual(matching('all_fail'), ['failed']);
comparisonRows.find(row => row.dataset.case === 'shared' && row.dataset.method === 'c').dataset.price = 'unavailable';
assert.deepEqual(Array.from(vm.runInContext("matchingCaseIds(comparisonRows, 'a', ['b','c'], 'all_pass', 'price')", context)), []);
assert.deepEqual(Array.from(vm.runInContext("matchingCaseIds(comparisonRows, 'b', ['a'], 'comparison_only', '')", context)), ['baseline']);
console.log('PASS: baseline, multiple comparison methods, explicit outcome relations, pending and unannotated coverage');

// A page with no reference coverage is neither a quality pass nor a failure.
const unavailablePage = {dataset: {case: 'unscored', method: 'a', status: 'unavailable', outcome: 'unavailable', title: 'unavailable', stage: 'fields'}};
rows.push(unavailablePage);
control('field').value = '';control('status').value = 'all';control('status').listeners.change();
assert.equal(unavailablePage.hidden, false);
for (const state of ['pass', 'fail']) {
  control('status').value = state;control('status').listeners.change();
  assert.equal(unavailablePage.hidden, true);
}
comparisonRows.push(unavailablePage, {dataset: {case: 'unscored', method: 'b', status: 'fail', outcome: 'wrong'}}, {dataset: {case: 'unscored', method: 'c', status: 'fail', outcome: 'wrong'}});
assert.equal(matching('different').includes('unscored'), false);
assert.equal(matching('all_fail').includes('unscored'), false);
console.log('PASS: wholly unannotated pages stay in All and outside whole-page quality relations');


assert.equal(produced.failedUnscored.status, 'fail');
assert.equal(produced.failedUnscored.scoredFields, '0');
for (const method of ['a', 'b', 'c']) comparisonRows.push({dataset: {...produced.failedUnscored, method}});
assert.equal(matching('all_fail').includes('unscored-failure'), false);
assert.equal(matching('different').includes('unscored-failure'), false);
console.log('PASS: failed calls without reference coverage retain diagnosis and stay outside quality comparisons');

for (const field of ["Price", "price", "Display Name", "status", "x.y"]) {
  context.arbitraryField = field;
  assert.equal(vm.runInContext(`qualityOutcome({dataset: {status: 'pass', fieldOutcomes: JSON.stringify({[arbitraryField]: 'correct'})}}, arbitraryField)`, context), true);
  assert.equal(vm.runInContext(`qualityOutcome({dataset: {status: 'pass', fieldOutcomes: JSON.stringify({[arbitraryField]: 'missing'})}}, arbitraryField)`, context), false);
}

for (const field of ["constructor", "toString", "hasOwnProperty", "__proto__"]) {
  context.arbitraryField = field;
  assert.equal(vm.runInContext(`qualityOutcome({dataset: {status: 'pass', fieldOutcomes: JSON.stringify({other: 'correct'})}}, arbitraryField)`, context), null);
  assert.equal(vm.runInContext(`qualityOutcome({dataset: {status: 'pass', fieldOutcomes: JSON.stringify({[arbitraryField]: 'correct'})}}, arbitraryField)`, context), true);
  assert.equal(vm.runInContext(`qualityOutcome({dataset: {status: 'pass'}}, arbitraryField)`, context), null);
}

// A metric link must reveal its definition even while the guide is collapsed.
const metricGuide = {open: false};
let metricScrolled = false;
const metricTarget = {
  matches: () => false,
  closest: selector => selector === 'details[data-metric-guide]' ? metricGuide : null,
  scrollIntoView: () => { metricScrolled = true; },
};
context.location.hash = '#metric-completion-page_exact';
context.document.getElementById = id => id === 'metric-completion-page_exact' ? metricTarget : null;
vm.runInContext('revealResultPanel()', context);
assert.equal(metricGuide.open, true);
assert.equal(metricScrolled, true);
