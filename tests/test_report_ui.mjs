// Unit tests only: synthetic DOM, no browser, navigation, or network.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const source = fs.readFileSync(new URL('../harness/web_fetch/report.py', import.meta.url), 'utf8');
const js = source.match(/JS=r'''([\s\S]*?)'''/)[1];
const filters = [{dataset:{filter:'track'},value:'image'}, {dataset:{filter:'result'},value:'pass'}, {dataset:{filter:'query'},value:'CAT'}];
const rows = [
  {dataset:{track:'image',result:'pass'},textContent:'Cat photo'},
  {dataset:{track:'image',result:'fail'},textContent:'Cat failed'},
  {dataset:{track:'video',result:'pass'},textContent:'Cat video'},
  {dataset:{track:'image',result:'pass'},textContent:'Dog photo'}
];
const count={textContent:''};
const classes=()=>({values:new Set(),toggle(k,on){on?this.values.add(k):this.values.delete(k)},contains(k){return this.values.has(k)}});
const makeCell=(value)=>({dataset:{sort:String(value)},textContent:String(value),classList:classes()});
const tableRows=[100,9,-1,40].map(v=>({cells:[{textContent:'',classList:classes()},makeCell(v)]}));
const body={rows:tableRows,append(row){this.rows.splice(this.rows.indexOf(row),1);this.rows.push(row)}};
const headCells=[0,1].map(()=>({classList:classes(),removeAttribute(name){delete this[name]},setAttribute(name,value){this[name]=value}}));
const table={dataset:{},tBodies:[body],tHead:{rows:[{cells:headCells}]}};
const context=vm.createContext({window:{addEventListener(){}},document:{
  querySelectorAll(selector){return selector==='#rows tr'?rows:filters},
  querySelector(){return count},getElementById(){return table}
}});
vm.runInContext(js,context);
vm.runInContext('filterRows()',context);
assert.deepEqual(rows.map(r=>r.hidden),[false,true,true,true]);
assert.equal(count.textContent,'显示 1 条');
filters.forEach(f=>f.value='');
vm.runInContext('filterRows()',context);
assert.equal(count.textContent,'显示 4 条');
rows[1].dataset.search='未命中核对点：正文核对点-1';
filters[2].value='  正文核对点-1  ';
vm.runInContext('filterRows()',context);
assert.deepEqual(rows.map(r=>r.hidden),[true,false,true,true]);
filters[2].value='  ';
vm.runInContext('filterRows()',context);
assert.equal(count.textContent,'显示 4 条');
vm.runInContext("sortTable('test',1)",context);
assert.deepEqual(body.rows.map(r=>Number(r.cells[1].dataset.sort)),[100,40,9,-1]);
assert.equal(headCells[1]['aria-sort'],'descending');
vm.runInContext("sortTable('test',1)",context);
assert.deepEqual(body.rows.map(r=>Number(r.cells[1].dataset.sort)),[9,40,100,-1]);
assert.deepEqual(body.rows.map(r=>r.cells[0].textContent),[1,2,3,4]);
assert.equal(headCells[1]['aria-sort'],'ascending');
assert.ok(headCells[1].classList.contains('sorted'));
assert.ok(body.rows.every(r=>r.cells[1].classList.contains('sorted')&&!r.cells[0].classList.contains('sorted')));
table.dataset.col='0';table.dataset.dir='desc';
vm.runInContext('paintSort(document.getElementById("test"))',context);
assert.ok(body.rows.every(r=>r.cells[0].classList.contains('sorted')&&!r.cells[1].classList.contains('sorted')));
console.log('PASS: combined filters, search, counts, numeric sorting, missing values last, row numbers, aria-sort');
const choices=(key,values)=>({dataset:{filter:key},value:values[0],options:[...values,''].map(value=>({value,disabled:false}))});
const dependent=[choices('track',['web','image']),choices('category',['article','photo']),choices('result',['pass','fail'])];
const dependentRows=[{dataset:{track:'web',category:'article',result:'pass'},textContent:'article'}, {dataset:{track:'image',category:'photo',result:'fail'},textContent:'photo'}];
const cascaded=vm.createContext({window:{addEventListener(){}},document:{querySelectorAll(s){return s==='#rows tr'?dependentRows:dependent},querySelector(){return count}}});
vm.runInContext(js,cascaded);
vm.runInContext('cascadeFilters()',cascaded);
assert.deepEqual(dependentRows.map(r=>r.hidden),[false,true]);
dependent[0].value='image';
vm.runInContext('cascadeFilters()',cascaded);
assert.deepEqual(dependent.map(s=>s.value),['image','photo','pass']);
assert.deepEqual(dependentRows.map(r=>r.hidden),[true,true]);
dependent[2].value='fail';
vm.runInContext('cascadeFilters()',cascaded);
assert.deepEqual(dependentRows.map(r=>r.hidden),[true,false]);
dependent[0].value='web';
vm.runInContext('cascadeFilters()',cascaded);
assert.equal(dependent[2].value,'fail');
assert.deepEqual(dependentRows.map(r=>r.hidden),[true,true]);
dependent.forEach(s=>s.value='');
vm.runInContext('cascadeFilters()',cascaded);
assert.deepEqual(dependentRows.map(r=>r.hidden),[false,false]);
console.log('PASS: first-option defaults, dependent filters select available values, explicit all preserved');
const failureSelections=[choices('provider',['one','two']),choices('result',['fail','pass']),choices('failureStage',['','request','task']),choices('failureCategory',['','超时','缺少关键信息'])];
const failureRows=[
 {dataset:{provider:'one',result:'fail',failureStage:'request',failureCategory:'超时'},textContent:'request failed'},
 {dataset:{provider:'one',result:'fail',failureStage:'task',failureCategory:'缺少关键信息'},textContent:'content failed'},
 {dataset:{provider:'one',result:'pass',failureStage:'',failureCategory:''},textContent:'passed'},
 {dataset:{provider:'two',result:'fail',failureStage:'task',failureCategory:'缺少关键信息'},textContent:'another failure'}
];
const failureContext=vm.createContext({window:{addEventListener(){}},document:{querySelectorAll(s){return s==='#rows tr'?failureRows:failureSelections},querySelector(){return count}}});
vm.runInContext(js,failureContext);
vm.runInContext('cascadeFilters()',failureContext);
assert.deepEqual(failureRows.map(r=>r.hidden),[false,false,true,true]);
failureSelections[2].value='request';failureSelections[3].value='超时';
vm.runInContext('cascadeFilters()',failureContext);
assert.deepEqual(failureRows.map(r=>r.hidden),[false,true,true,true]);
failureSelections[2].value='task';
vm.runInContext('cascadeFilters()',failureContext);
assert.equal(failureSelections[3].value,'');
assert.deepEqual(failureRows.map(r=>r.hidden),[true,false,true,true]);
failureSelections[1].value='pass';
vm.runInContext('cascadeFilters()',failureContext);
assert.deepEqual(failureSelections.slice(2).map(s=>[s.value,s.disabled]),[['',true],['',true]]);
assert.deepEqual(failureRows.map(r=>r.hidden),[true,true,false,true]);
failureSelections.forEach(s=>s.value='');
vm.runInContext('cascadeFilters()',failureContext);
assert.ok(failureRows.every(r=>!r.hidden));
assert.ok(failureSelections.slice(2).every(s=>!s.disabled));
console.log('PASS: failure stage/category combinations, incompatible selection reset, pass-only state, all records');
const methodTabs={id:'method-guide',dataset:{},parentElement:null};
const capabilityPanel={dataset:{panel:'capabilities'},parentElement:methodTabs};
const capabilityDetail={tagName:'DETAILS',dataset:{},open:false,parentElement:capabilityPanel};
const adoptionPanel={dataset:{panel:'adoption'},parentElement:methodTabs};
const adoptionDetail={tagName:'DETAILS',dataset:{},open:false,parentElement:adoptionPanel};
const anchorSelections=[];
const anchors=vm.createContext({window:{addEventListener(){}},document:{getElementById(id){return {'capability-firecrawl':capabilityDetail,'evidence-firecrawl':adoptionDetail}[id]}}});
vm.runInContext(js,anchors);
anchors.chooseTab=(group,key)=>anchorSelections.push([group,key]);
vm.runInContext("applyReportAnchor('capability-firecrawl')",anchors);
assert.equal(capabilityDetail.open,true);
assert.deepEqual(anchorSelections.pop(),['method-guide','capabilities']);
vm.runInContext("applyReportAnchor('evidence-firecrawl')",anchors);
assert.equal(adoptionDetail.open,true);
assert.deepEqual(anchorSelections.pop(),['method-guide','adoption']);
console.log('PASS: official capability and adoption anchors reveal their own tab, not a hard-coded tab');
