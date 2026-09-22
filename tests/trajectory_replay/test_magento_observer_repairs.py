import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path("evaluation/trajectory-replay/webarena-verified/retrieval-candidates")


@pytest.mark.parametrize("rejected", [False, True])
def test_wizard_transition_requires_values_not_just_successful_click(rejected):
    candidate = json.loads(
        (ROOT.parent / "trajectory-candidates/task-547.json").read_text()
    )
    setup = r"""
let clicked=0,handlerChecks=0;
const wizard={index:0,getStep:()=>step};
const step={name:'step1',wizard,stepInitialized:true,attributesLabels:{'1':'Size','2':'Color'},
multiselect:()=>({selected:()=>['1','2']}),doSelectSavedAttributes:()=>{},notificationMessage:{}};
const box=text=>({labels:[{textContent:text}],getClientRects:()=>[{}]});
const button={textContent:'Next',disabled:false,getClientRects:()=>[{}],click:()=>{
clicked++;if(clicked!==1)throw Error('clicked more than once');
if(REJECTED)step.notificationMessage={error:true,text:'Please select attribute(s).'};
else wizard.index=1;
}};
const dialog={getClientRects:()=>[{}],querySelectorAll:s=>s==='button'?[button]:
clicked?[box('S'),box('Brown')]:[]};
global.document={querySelectorAll:()=>[dialog]};
global.getComputedStyle=()=>({visibility:'visible',display:'block'});
global.requirejs=()=>{};requirejs.defined=()=>true;
global.jQuery=()=>{};jQuery._data=()=>({click:++handlerChecks>1?[()=>{}]:[]});
global.require=name=>name==='ko'?{contextFor:()=>({$data:{wizard,selectedStep:()=>step.name}})}:{get:fn=>fn(step)};
""".replace("REJECTED", str(rejected).lower())
    result = run(candidate["tool_calls"][4]["script"], setup)
    if rejected:
        assert result.returncode and "Please select attribute(s)." in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["values_ready"] is True


def run(script, setup):
    result = subprocess.run(
        [
            "node",
            "-e",
            setup
            + "\nPromise.resolve("
            + script
            + ").then(v=>console.log(JSON.stringify(v)))"
            + ".catch(e=>{console.error(e.message);process.exitCode=1})",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result


@pytest.mark.parametrize("bad", [False, True])
def test_legacy_grid_not_notification_provider(bad):
    value = json.loads((ROOT / "task-41.json").read_text())
    script = value["tool_calls"][-1]["script"]
    setup = r"""
global.location={pathname:'/admin/search/term/report/',href:'https://test/admin/search/term/report/'};
const values={query_id:'1',query_text:'demo',num_results:'2',popularity:'7'};
const row={querySelector:()=>null,querySelectorAll:s=>[{textContent:values[s.slice(7)]}]};
const table={querySelectorAll:s=>s.startsWith('thead')?[]:[row]};
const root={contains:t=>t===table,querySelector:s=>({value:s.startsWith('input')?'1':'20'})};
global.document={getElementById:id=>id.endsWith('-total-count')?
{textContent:TOTAL}:id.endsWith('_table')?table:root};
""".replace("TOTAL", json.dumps("2" if bad else "1"))
    result = run(script, setup)
    if bad:
        assert result.returncode and "incomplete legacy page" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["answer"] == ["demo"]


@pytest.mark.parametrize("bad", [False, True])
@pytest.mark.parametrize("label", ["1 Item(s)", "Items 1 to 1 of 1 total", "Items 1-1 of 1"])
def test_single_page_history_and_inconsistent_total(bad, label):
    value = json.loads((ROOT / "task-50.json").read_text())
    script = value["tool_calls"][-1]["script"]
    setup = r"""
global.location={origin:'http://test',href:'http://test/sales/order/history/?limit=50'};
const row={querySelectorAll:s=>s===':scope > td'?
['1','3/1/23','$12.00','Complete'].map(textContent=>({textContent})):
[{getAttribute:()=>'/sales/order/view/order_id/1/'}]};
const body={querySelectorAll:()=>[row]},table={querySelector:()=>body};
const doc={querySelector:s=>s==='form#login-form'?null:table,
querySelectorAll:()=>[{textContent:TOTAL}]};
global.DOMParser=class{parseFromString(){return doc}};
global.fetch=async u=>({ok:true,redirected:false,url:u,
headers:{get:()=> 'text/html'},text:async()=>''});
""".replace("TOTAL", json.dumps("2 Item(s)" if bad else label))
    result = run(script, setup)
    if bad:
        assert result.returncode and "history pagination bounds inconsistent" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["answer"] == {"order_count": 1, "amount": 12}


@pytest.mark.parametrize("task_id", [183, 184])
@pytest.mark.parametrize("bad", [False, True])
def test_inventory_current_records_and_runtime_color_labels(task_id, bad):
    value = json.loads((ROOT / f"task-{task_id}.json").read_text())
    setup = r"""
global.location={origin:'https://test',href:'https://test/admin/catalog/product/',
pathname:'/admin/catalog/product/'};
const rows=[{entity_id:'9',sku:'sku-blue',name:'Example Blue',qty:'0',color:'50',type_id:'simple'}];
const data={totalRecords:'1',items:rows};
const p={component:'Magento_Ui/js/grid/provider',params:{namespace:'product_listing',
filters:{status:'disabled'},search:'old search',paging:{current:1,pageSize:20}},
update_url:'/admin/mui/index/render/',firstLoad:false,lastError:false,
triggerDataReload:false,storageConfig:{name:'storage'},data};
const registry={get:key=>typeof key==='function'?key(p):key==='storage'?
{getRequest:()=>({ids:['9'],totalRecords:'1'})}:{options:[{value:'50',label:'Blue'}]}};
global.requirejs=()=>{};requirejs.defined=()=>true;global.require=()=>registry;
global.fetch=async address=>{const u=new URL(address);
if(u.searchParams.has('filters[status]')||u.searchParams.get('search')!=='')
throw Error('leaked saved filters');
if(TASK===184&&(u.searchParams.get('filters[type_id]')!=='simple'||
u.searchParams.get('filters[qty][to]')!=='0'))throw Error('wrong stock scope');
return {ok:true,redirected:false,headers:{get:()=> 'application/json'},
json:async()=>({...data,items:BAD?[{...rows[0],qty:null}]:rows})}};
""".replace("TASK", str(task_id)).replace("BAD", str(bad).lower())
    result = run(value["tool_calls"][-1]["script"], setup)
    if bad:
        assert result.returncode and "missing product quantity" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        if task_id == 184:
            assert output["answer"] == [{"name": "Example Blue", "color": "Blue"}]
        else:
            assert output["answer"] == []
            assert output["records"] == [{"id": "9", "sku": "sku-blue", "quantity": 0}]
            assert output["coverage_pages"][0]["ids"] == ["9"]
            assert output["coverage_pages"][0]["has_next"] is False


@pytest.mark.parametrize("bad", [False, True])
@pytest.mark.parametrize("string_total", [False, True])
def test_modern_grid_ignores_notification_and_clears_saved_filters(bad, string_total):
    value = json.loads((ROOT / "task-94.json").read_text())
    script = value["tool_calls"][-1]["script"]
    setup = r"""
global.location={origin:'https://test',href:'https://test/admin/sales/invoice/',
pathname:'/admin/sales/invoice/'};
const data={totalRecords:1,items:[{entity_id:'1',increment_id:'000000001',grand_total:'$12.00'}]};
const p={component:'Magento_Ui/js/grid/provider',params:{namespace:'sales_order_invoice_grid',
filters:{status:'pending'},paging:{pageSize:20,current:1}},update_url:'/admin/mui/index/render/',
firstLoad:false,lastError:false,triggerDataReload:false,storageConfig:{name:'storage'},data};
const notification={...p,params:{namespace:'notification_area'}};
const registry={get:key=>typeof key==='function'?([notification,p].forEach(key)):
{getRequest:()=>({ids:['1'],totalRecords:1})}};
global.requirejs=()=>{};requirejs.defined=()=>true;global.require=()=>registry;
global.fetch=async address=>{const url=new URL(address);
if(url.searchParams.has('filters[status]')||
url.searchParams.get('namespace')!=='sales_order_invoice_grid')
throw Error('leaked saved filters or notification');
return {ok:true,redirected:false,headers:{get:()=> 'application/json'},
json:async()=>({...data,totalRecords:TOTAL})}};
""".replace("TOTAL", json.dumps(str(2 if bad else 1) if string_total else (2 if bad else 1)))
    result = run(script, setup)
    if bad:
        assert result.returncode and "incomplete or changed page" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["answer"] == 12
