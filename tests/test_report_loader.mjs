// Pure synthetic DOM test: no browser or network.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
const source=fs.readFileSync(new URL('../scripts/package_artifact_hub.py',import.meta.url),'utf8');
const js=source.match(/LOADER=r'''([\s\S]*?)'''/)[1];
let fields=[{dataset:{filter:'provider'},value:'lexmount-global'},{dataset:{filter:'query'},value:'正文核对点-1'}],calls=0;
const context=vm.createContext({TextDecoder,URL,fetch:async()=>({ok:true,json:async()=>({})}),
 window:{scrollX:0,scrollY:650,addEventListener(){}},
 document:{querySelector:()=>({}),querySelectorAll:()=>fields,addEventListener(){}},
 cascadeFilters(){calls++}
});
vm.runInContext(js,context);
vm.runInContext("currentPage='analysis/index.html';savePageState()",context);
fields.forEach(f=>f.value='');
const saved=vm.runInContext("restorePageState('analysis/index.html')",context);
assert.deepEqual(fields.map(f=>f.value),['lexmount-global','正文核对点-1']);
assert.equal(saved.y,650);assert.equal(calls,1);
assert.equal(vm.runInContext("restorePageState('missing.html')",context),null);
console.log('PASS: packaged report restores per-page filters, query and scroll position');
const anchors=[false,true].map(download=>({dataset:{},getAttribute:()=> 'article.html',hasAttribute:n=>n==='download'&&download}));
context.linkRoot={querySelectorAll:()=>anchors};
vm.runInContext("fixLinks(linkRoot,'guide/index.html',{files:{'guide/article.html':{}}})",context);
assert.equal(anchors[0].href,'#page=guide%2Farticle.html');
assert.equal(anchors[1].dataset.download,'guide/article.html');
console.log('PASS: HTML export downloads while ordinary HTML links navigate');
