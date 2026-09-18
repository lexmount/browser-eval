import json
import subprocess
from pathlib import Path

import pytest


@pytest.mark.parametrize("defect", ["none", "duplicate", "incomplete", "currency"])
def test_sony_catalog_observer_keeps_distinct_skus_and_complete_scope(defect):
    candidate = json.loads(
        Path(
            "evaluation/trajectory-replay/webarena-verified/retrieval-candidates/task-279.json"
        ).read_text()
    )
    setup = r"""
global.location={origin:'http://shop.test',pathname:'/catalogsearch/result/',href:'http://shop.test/catalogsearch/result/?q=Sony'};
global.DOMParser=class {parseFromString(s){return {querySelectorAll:()=>[],body:{textContent:s}}}};
const product=(sku,name,price)=>({sku,name,url_key:'same-shared-url',
description:{html:'Bluetooth'},rating_summary:80,
price_range:{minimum_price:{final_price:{value:price,currency:'USD'}}}});
const rows=[product('a','Sony Bluetooth Headphones',10),
product('b','Sony WI-1000XM2 Headphones',20)];
rows[1].description.html='Wireless neckband';
if(DEFECT==='duplicate')rows[1].sku='a';
if(DEFECT==='currency')rows[0].price_range.minimum_price.final_price.currency='EUR';
global.fetch=async u=>{if(new URL(u).pathname!=='/graphql')throw Error('wrong endpoint');
return {ok:true,redirected:false,headers:{get:()=> 'application/json'},
json:async()=>({data:{products:{total_count:DEFECT==='incomplete'?3:2,
page_info:{current_page:1,total_pages:1},items:rows}}})}};
""".replace("DEFECT", json.dumps(defect))
    result = subprocess.run(
        [
            "node",
            "-e",
            setup
            + "\nPromise.resolve("
            + candidate["tool_calls"][-1]["script"]
            + ").then(v=>console.log(JSON.stringify(v)))"
            + ".catch(e=>{console.error(e.message);process.exitCode=1})",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if defect == "none":
        assert result.returncode == 0, result.stderr
        answer = json.loads(result.stdout)["answer"]
        assert len(answer["names"]) == 2
        assert answer["min"] == 10 and answer["max"] == 20
    else:
        assert result.returncode != 0
        assert {
            "duplicate": "identity missing or duplicated",
            "incomplete": "incomplete or changed catalog",
            "currency": "invalid USD catalog price",
        }[defect] in result.stderr
