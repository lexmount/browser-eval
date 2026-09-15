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

const comparisonRows=[
 {dataset:{track:'web',pass:'a c',fail:'b'}},
 {dataset:{track:'document',pass:'a b c',fail:''}},
 {dataset:{track:'web',pass:'b',fail:'a c'}},
 {dataset:{track:'web',pass:'',fail:'a b c'}}
];
const comparisonControls={
 'compare-a':{value:'a'},'compare-relation':{value:'a_pass_others_fail'},'compare-track':{value:''},'compare-count':{textContent:''}
};
const compareChecks=['a','b','c'].map(value=>({value,checked:value==='b',disabled:false,closest(){return {classList:{toggle(){}}}}}));
const comparison=vm.createContext({window:{addEventListener(){}},document:{
 getElementById(id){return comparisonControls[id]},
 querySelectorAll(selector){if(selector==='#compare-rows tr')return comparisonRows;if(selector==='[data-compare-method]')return compareChecks;return []}
}});
vm.runInContext(js,comparison);
vm.runInContext('compareMethods()',comparison);
assert.deepEqual(comparisonRows.map(r=>r.hidden),[false,true,true,true]);assert.equal(comparisonControls['compare-count'].textContent,'找到 1 个任务');
assert.equal(compareChecks[0].disabled,true,'baseline must not also be a comparison method');
comparisonControls['compare-relation'].value='mixed';comparisonControls['compare-track'].value='web';
vm.runInContext('compareMethods()',comparison);
assert.deepEqual(comparisonRows.map(r=>r.hidden),[false,true,false,true]);
compareChecks[2].checked=true;comparisonControls['compare-relation'].value='all_fail';comparisonControls['compare-track'].value='';
vm.runInContext('compareMethods()',comparison);
assert.deepEqual(comparisonRows.map(r=>r.hidden),[true,true,true,false]);
compareChecks[1].checked=false;compareChecks[2].checked=false;vm.runInContext('compareMethods()',comparison);
assert.ok(comparisonRows.every(r=>r.hidden));assert.equal(comparisonControls['compare-count'].textContent,'请至少选择一个对比方法');
comparisonControls['compare-track']=null;
assert.doesNotThrow(()=>vm.runInContext('compareMethods()',comparison));
console.log('PASS: one unified method-set comparison covers asymmetric, shared, mixed and empty selections');

const wmbRows=[
 {dataset:{method:'moli',language:'en',level:'hard',request:'failed',failure:'timeout',text_edit:''},textContent:'case-a Moli timeout',cells:[{textContent:''}]},
 {dataset:{method:'moli',language:'en',level:'hard',request:'success',text_edit:'0.18'},textContent:'case-b Moli content',cells:[{textContent:''}]},
 {dataset:{method:'kitesurf',language:'zh',level:'mid',request:'success',text_edit:'0.82'},textContent:'case-c Kitesurf content',cells:[{textContent:''}]}
];
const wmbTable={tBodies:[{rows:wmbRows}],closest(){return {scrollIntoView(){wmbTable.scrolled=true}}}};
const wmbCount={textContent:''};
const wmbControl=(key,value,options=[])=>({dataset:{wmbFilter:key,wmbDefault:''},value,options:options.map(v=>({value:v,disabled:false})),disabled:false});
const wmbSelections=[wmbControl('method','',['','moli','kitesurf']),wmbControl('axis','',['','language','level']),wmbControl('category','',['','en','zh','hard','mid']),wmbControl('request','',['','failed','success']),wmbControl('failure','',['','timeout']),wmbControl('metric','',['','text_edit']),wmbControl('band','',['','0:0.25','unavailable']),wmbControl('query','')];
const wmbContext=vm.createContext({window:{addEventListener(){}},URLSearchParams,document:{
 querySelector(s){return s==='[data-wmb-table]'?wmbTable:s==='[data-wmb-count]'?wmbCount:null},
 querySelectorAll(s){return s==='[data-wmb-filter]'?wmbSelections:[]}
}});
vm.runInContext(js,wmbContext);
vm.runInContext('wmbFilters()',wmbContext);
assert.deepEqual(wmbRows.map(r=>r.hidden),[false,false,false]);
assert.equal(wmbCount.textContent,'显示 3 / 3 条');
wmbSelections.find(c=>c.dataset.wmbFilter==='request').value='failed';
wmbSelections.find(c=>c.dataset.wmbFilter==='failure').value='timeout';
vm.runInContext('wmbFilters()',wmbContext);
assert.deepEqual(wmbRows.map(r=>r.hidden),[false,true,true]);
vm.runInContext("wmbInspect('inspect?method=moli&axis=language&category=en&request=success&metric=text_edit&band=0%3A0.25')",wmbContext);
assert.deepEqual(wmbRows.map(r=>r.hidden),[true,false,true]);
assert.equal(wmbSelections.find(c=>c.dataset.wmbFilter==='failure').value,'');
assert.equal(wmbCount.textContent,'显示 1 / 3 条');
assert.equal(wmbTable.scrolled,true);
wmbSelections.find(c=>c.dataset.wmbFilter==='band').value='unavailable';
vm.runInContext('wmbFilters()',wmbContext);
assert.ok(wmbRows.every(r=>r.hidden));
vm.runInContext('wmbReset()',wmbContext);
assert.deepEqual(wmbRows.map(r=>r.hidden),[false,false,false]);
console.log('PASS: WebMainBench all-default, method, category, request, metric-band, unavailable and deep-link filters');

const wmbCompareRows=[
 {dataset:{delivered:'a c',failed:'b',language:'en',level:'hard'},cells:[{textContent:''}]},
 {dataset:{delivered:'a b c',failed:'',language:'zh',level:'easy'},cells:[{textContent:''}]},
 {dataset:{delivered:'b',failed:'a c',language:'en',level:'hard'},cells:[{textContent:''}]}
];
const wmbCompareControls={
 'wmb-compare-a':{value:'a'},'wmb-compare-relation':{value:'mixed'},
 'wmb-compare-axis':{value:''},'wmb-compare-category':{value:'',options:['','en','zh','hard','easy'].map(value=>({value,disabled:false})),disabled:false},
 'wmb-compare-count':{textContent:''}
};
const wmbCompareChecks=['a','b','c'].map(value=>({value,checked:value==='b',disabled:false,closest(){return {classList:{toggle(){}}}}}));
const wmbCompareContext=vm.createContext({window:{addEventListener(){}},document:{
 getElementById(id){return wmbCompareControls[id]},
 querySelectorAll(selector){if(selector==='#wmb-compare-rows tr')return wmbCompareRows;if(selector==='[data-wmb-compare-method]')return wmbCompareChecks;return []}
}});
vm.runInContext(js,wmbCompareContext);
vm.runInContext('wmbCompare()',wmbCompareContext);
assert.deepEqual(wmbCompareRows.map(r=>r.hidden),[false,true,false]);
assert.equal(wmbCompareControls['wmb-compare-count'].textContent,'显示 2 / 3 页');
wmbCompareControls['wmb-compare-relation'].value='a_success_others_fail';
vm.runInContext('wmbCompare()',wmbCompareContext);
assert.deepEqual(wmbCompareRows.map(r=>r.hidden),[false,true,true]);
wmbCompareControls['wmb-compare-axis'].value='language';
wmbCompareControls['wmb-compare-category'].value='zh';
wmbCompareControls['wmb-compare-relation'].value='all_success';
vm.runInContext('wmbCompare()',wmbCompareContext);
assert.deepEqual(wmbCompareRows.map(r=>r.hidden),[true,false,true]);
assert.equal(wmbCompareChecks[0].disabled,true);
console.log('PASS: WebMainBench same-page group comparison combines methods, delivery pattern and official categories');
