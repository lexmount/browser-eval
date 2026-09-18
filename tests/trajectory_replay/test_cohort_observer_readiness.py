"""Regression controls for sequential-cohort observer failures."""

import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path("evaluation/trajectory-replay/webarena-verified/retrieval-candidates")


def test_cross_document_actions_wait_for_destination_before_continuing():
    root = ROOT.parent / "trajectory-candidates"
    calls = json.loads((root / "task-404.json").read_text())["tool_calls"]
    navigation = next(
        i
        for i, call in enumerate(calls)
        if call.get("action") == "click" and "/f/books/new" in call.get("selector", "")
    )
    assert calls[navigation + 1] == {
        "action": "wait",
        "selector": 'button[aria-label="Sort by: New"]:visible',
        "timeout": 30000,
    }
    calls = json.loads((root / "task-771.json").read_text())["tool_calls"]
    saves = [
        i
        for i, call in enumerate(calls)
        if call.get("action") == "click" and "Save Review" in call.get("selector", "")
    ]
    assert len(saves) == 2
    for index in saves:
        assert calls[index + 1] == {
            "action": "wait",
            "selector": "#reviewGrid_table:visible",
            "timeout": 30000,
        }
        assert calls[index + 2]["action"] == "navigate"


def execute(task_id, setup):
    script = json.loads((ROOT / f"task-{task_id}.json").read_text())["tool_calls"][-1]["script"]
    return subprocess.run(
        [
            "node",
            "-e",
            setup
            + "\nPromise.resolve("
            + script
            + ").then(x=>console.log(JSON.stringify(x)))"
            + ".catch(e=>{console.error(e.message);process.exitCode=1})",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )


@pytest.mark.parametrize("kind", ["metadata", "date_from", "visible_locale", "other_hidden"])
def test_pending_reviews_date_metadata_is_not_a_filter(kind):
    fields = {
        "metadata": {"type": "hidden", "name": "created_at[locale]", "value": "en_US"},
        "date_from": {"type": "text", "name": "created_at[from]", "value": "09/18/2026"},
        "visible_locale": {"type": "text", "name": "created_at[locale]", "value": "en_US"},
        "other_hidden": {"type": "hidden", "name": "status", "value": "pending"},
    }
    setup = r"""
global.location={pathname:'/admin/review/product/pending'};
const row={querySelector:()=>null,querySelectorAll:()=>[{textContent:'353'}]};
const table={querySelectorAll:s=>s.startsWith('thead')?[FIELD]:[row]};
const root={contains:()=>true,querySelector:s=>({value:s.startsWith('input')?'1':'20'})};
global.document={getElementById:id=>id.endsWith('-total-count')?{textContent:'1'}:
id.endsWith('_table')?table:root};
""".replace("FIELD", json.dumps(fields[kind]))
    result = execute(77, setup)
    if kind == "metadata":
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["answer"] == 1
    else:
        assert result.returncode != 0 and "filtered legacy grid" in result.stderr


@pytest.mark.parametrize("state", ["delayed", "failed", "never"])
def test_inventory_waits_for_completed_request_and_rejects_failed_or_stuck_grid(state):
    setup = r"""
global.location={origin:'https://test',href:'https://test/admin/catalog/product/',pathname:'/admin/catalog/product/'};
const data={totalRecords:'1',items:[{entity_id:'9',sku:'sku',name:'Name',qty:'0',
color:'50',type_id:'simple'}]};
let checks=0,clock=0;Date.now=()=>clock;
const originalTimeout=setTimeout;
global.setTimeout=(fn,ms)=>ms===100?originalTimeout(()=>{clock+=15000;fn()},0):originalTimeout(fn,ms);
const p={component:'Magento_Ui/js/grid/provider',params:{namespace:'product_listing'},
update_url:'/admin/mui/index/render/',
firstLoad:true,lastError:STATE==='failed',triggerDataReload:false,storageConfig:{name:'storage'},data};
const registry={get:key=>{if(typeof key==='function'){checks++;
if(STATE==='delayed'&&checks===2)p.firstLoad=false;key(p);return;}
return {getRequest:()=>({ids:['9'],totalRecords:1})};}};
global.requirejs=()=>{};requirejs.defined=()=>true;global.require=()=>registry;
global.fetch=async()=>{if(p.firstLoad)throw Error('read before ready');
return {ok:true,redirected:false,
headers:{get:()=> 'application/json'},json:async()=>data}};
""".replace("STATE", json.dumps(state))
    result = execute(183, setup)
    if state == "delayed":
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["total"] == 1
    else:
        assert result.returncode != 0
        assert ("grid load failed" if state == "failed" else "within 30000ms") in result.stderr


@pytest.mark.parametrize("missing", [False, True])
def test_order_details_keep_full_coverage_and_history_order_under_concurrency(missing):
    setup = r"""
global.location={origin:'http://test',href:'http://test/sales/order/history/'};
let active=0,peak=0;const seen=[];
const rows=Array.from({length:6},(_,i)=>({querySelectorAll:s=>s===':scope > td'?
[String(i+1),`3/${6-i}/23`,'$1.00','Complete'].map(textContent=>({textContent})):
[{getAttribute:()=>`/sales/order/view/order_id/${i+1}/`}]}));
const history={querySelector:s=>s==='form#login-form'?null:
{querySelector:()=>({querySelectorAll:()=>rows})},
querySelectorAll:()=>[{textContent:'6 Item(s)'}]};
global.DOMParser=class{parseFromString(body){return body==='history'?history:{querySelectorAll:()=>
MISSING&&body==='6'?[]:[{textContent:'Cornbread Muffin Mix'}]}}};
global.fetch=async url=>{const detail=/order_id\/(\d+)/.exec(url);let body='history';
if(detail){body=detail[1];seen.push(body);active++;peak=Math.max(peak,active);
if(peak>4)throw Error('unbounded detail concurrency');
await new Promise(r=>setTimeout(r,(7-Number(body))*5));active--;}
return {ok:true,redirected:false,url,headers:{get:()=> 'text/html'},text:async()=>body};};
process.on('beforeExit',()=>{if(!MISSING&&(seen.length!==6||peak<2))process.exitCode=2});
""".replace("MISSING", str(missing).lower())
    result = execute(334, setup)
    if missing:
        assert result.returncode != 0 and "order items missing" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        output = json.loads(result.stdout)
        assert output["answer"] == "2023-03-06"
        assert len(output["matched"]) == 6


@pytest.mark.parametrize("duplicate", [False, True])
def test_product_pagination_uses_unique_sort_and_still_rejects_duplicate_server_rows(duplicate):
    setup = r"""
global.location={origin:'https://test',href:'https://test/admin/catalog/product/',pathname:'/admin/catalog/product/'};
const first={totalRecords:201,items:[{entity_id:'1'}]};
const p={component:'Magento_Ui/js/grid/provider',params:{namespace:'product_listing',
sorting:{field:'qty',direction:'asc'}},update_url:'/admin/mui/index/render/',firstLoad:false,
lastError:false,triggerDataReload:false,storageConfig:{name:'storage'},data:first};
const registry={get:key=>typeof key==='function'?key(p):
{getRequest:()=>({ids:['1'],totalRecords:201})}};
global.requirejs=()=>{};requirejs.defined=()=>true;global.require=()=>registry;
global.fetch=async address=>{const u=new URL(address),
page=Number(u.searchParams.get('paging[current]'));
if(u.searchParams.get('sorting[field]')!=='entity_id'||
u.searchParams.get('sorting[direction]')!=='asc')throw Error('inherited unstable sort');
const items=Array.from({length:page===1?200:1},(_,i)=>({
entity_id:String(page===1?i+1:DUPLICATE?1:201),sku:'sku-'+(page===1?i+1:201),qty:'100'}));
return {ok:true,redirected:false,headers:{get:()=> 'application/json'},
json:async()=>({totalRecords:'201',items})};};
""".replace("DUPLICATE", str(duplicate).lower())
    result = execute(183, setup)
    if duplicate:
        assert result.returncode != 0 and "duplicate grid identities" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert len(json.loads(result.stdout)["records"]) == 201


@pytest.mark.parametrize("task_id", [110, 193, 196, 198, 204, 288])
def test_every_order_observer_waits_for_request_and_uses_unique_page_order(task_id):
    script = json.loads((ROOT / f"task-{task_id}.json").read_text())["tool_calls"][-1]["script"]
    assert "while(Date.now()<readyDeadline)" in script
    assert "sorting:{field:'entity_id',direction:'asc'}" in script
    assert "rows.sort(" in script


@pytest.mark.parametrize("task_id", [110, 193, 196, 198, 288])
@pytest.mark.parametrize("failed", [False, True])
def test_order_observers_execute_after_delayed_load_and_fail_on_grid_errors(task_id, failed):
    setup = r"""
global.location={origin:'https://test',href:'https://test/admin/sales/order/',
pathname:'/admin/sales/order/'};
const items=Array.from({length:8},(_,i)=>({entity_id:String(i+1),
created_at:`2023-01-0${i+1} 01:00:00`,status:i<4?'complete':'canceled',
order_currency_code:'USD',grand_total:'$10.00',customer_email:'customer@example.test'}));
const data={totalRecords:8,items};let polls=0;
const p={component:'Magento_Ui/js/grid/provider',params:{namespace:'sales_order_grid'},
update_url:'/admin/mui/index/render/',firstLoad:true,lastError:FAILED,
triggerDataReload:false,storageConfig:{name:'storage'},data};
const registry={get:key=>{if(typeof key==='function'){
 if(++polls===2)p.firstLoad=false;key(p);return;
}return {getRequest:()=>({ids:items.map(r=>r.entity_id),totalRecords:'8'})};}};
global.requirejs=()=>{};requirejs.defined=()=>true;global.require=()=>registry;
global.fetch=async url=>{if(p.firstLoad)throw Error('premature read');
 if(new URL(url).searchParams.get('sorting[field]')!=='entity_id')throw Error('unstable sort');
 return {ok:true,redirected:false,headers:{get:()=> 'application/json'},
 json:async()=>({...data,totalRecords:'8'})};};
""".replace("FAILED", str(failed).lower())
    result = execute(task_id, setup)
    if failed:
        assert result.returncode != 0 and "grid load failed" in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout)["complete"] is True
