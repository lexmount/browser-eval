"""Blind, pooled top-k preview relevance scoring for Web Search results."""

import hashlib
import json
import math

from .core import canonical_url


def _gain(grade, rank):
    return (2**grade - 1) / math.log2(rank + 2)


def score(profile, rows, plan, labels):
    """Return per-query/provider nDCG and precision after exact coverage checks."""
    cutoff = plan["cutoff"]
    if cutoff != profile["protocol"]["top_k"] or cutoff < 1:
        raise ValueError("Relevance cutoff differs from the measured result count")
    cases = {case["id"]: case for case in profile["cases"]}
    methods = [method["id"] for method in profile["methods"]]
    indexed = {(row["case_id"], row["method"]): row for row in rows}
    if len(set(plan["case_ids"])) != len(plan["case_ids"]) or set(labels) != set(plan["case_ids"]):
        raise ValueError("Relevance labels do not cover the declared sample")
    scored = []
    for case_id in plan["case_ids"]:
        case = cases[case_id]
        rankings = {}
        candidate_ids = set()
        for method in methods:
            result = indexed[case_id, method]
            ids = []
            for item in result["results"][:cutoff]:
                canonical = canonical_url(item["url"])
                candidate_id = hashlib.sha256((case_id + "\0" + canonical).encode()).hexdigest()[:12]
                ids.append(candidate_id)
                candidate_ids.add(candidate_id)
            rankings[method] = ids
        grades = labels[case_id]
        if set(grades) != candidate_ids or any(type(v) is not int or v not in range(4) for v in grades.values()):
            raise ValueError("Relevance grades do not match the pooled candidates")
        ideal = sum(_gain(g, i) for i, g in enumerate(sorted(grades.values(), reverse=True)[:cutoff]))
        for method in methods:
            ids = rankings[method]
            seen = set()
            ranked_grades = []
            for candidate_id in ids:
                ranked_grades.append(0 if candidate_id in seen else grades[candidate_id])
                seen.add(candidate_id)
            dcg = sum(_gain(grade, rank) for rank, grade in enumerate(ranked_grades))
            scored.append({"case_id": case_id, "family": case["family"], "method": method,
                           "ndcg": dcg / ideal if ideal else 0.0, "precision": sum(g >= 2 for g in ranked_grades) / cutoff,
                           "returned": len(ids)})
    return scored


def summary(scored, methods):
    return {method: {metric: sum(row[metric] for row in scoped) / len(scoped)
                     for metric in ("ndcg", "precision")}
            | {"n": len(scoped)}
            for method in methods if (scoped := [row for row in scored if row["method"] == method])}


def verify_provenance(profile, rows, evidence):
    """Make public candidate packets and rank mappings agree with measured outputs."""
    if evidence.get("judge", {}).get("kind") != "human" or not evidence["judge"].get("name", "").strip():
        raise ValueError("Relevance evaluation requires named human annotations; model judges are disabled")
    plan = evidence["plan"]
    custom = [c["id"] for c in profile["cases"] if "reference_answer" not in c]
    if plan.get("scope") != "all_custom" or plan["case_ids"] != custom:
        raise ValueError("Relevance evaluation must cover every in-house case without sampling")
    packets = evidence["blind_candidate_packets"]
    rankings = evidence["provider_rankings"]
    labels = evidence["labels"]
    if [packet["case_id"] for packet in packets] != plan["case_ids"]:
        raise ValueError("Blind packets do not cover the declared sample")
    indexed = {(row["case_id"], row["method"]): row for row in rows}
    for packet in packets:
        case_id = packet["case_id"]
        ids = {candidate["candidate_id"] for candidate in packet["candidates"]}
        if ids != set(labels[case_id]) or len(ids) != len(packet["candidates"]):
            raise ValueError("Blind packet candidates disagree with grades")
        for candidate in packet["candidates"]:
            expected = hashlib.sha256((case_id + "\0" + canonical_url(candidate["url"])).encode()).hexdigest()[:12]
            if candidate["candidate_id"] != expected:
                raise ValueError("Blind packet URL identity mismatch")
        for method in (m["id"] for m in profile["methods"]):
            actual = [hashlib.sha256((case_id + "\0" + canonical_url(item["url"])).encode()).hexdigest()[:12]
                      for item in indexed[case_id, method]["results"][:plan["cutoff"]]]
            if rankings[case_id][method] != actual:
                raise ValueError("Published rank mapping disagrees with provider results")
    crosscheck = evidence.get("crosscheck_labels", {})
    if not set(crosscheck).issubset(labels) or any(
        not set(grades).issubset(labels[case_id]) or any(type(v) is not int or v not in range(4) for v in grades.values())
        for case_id, grades in crosscheck.items()
    ):
        raise ValueError("Cross-check grades are invalid")


def candidate_packet(profile, rows):
    """Pool every in-house query without method names, rankings or generated grades."""
    case_ids = [c["id"] for c in profile["cases"] if "reference_answer" not in c]
    cutoff = profile["protocol"]["top_k"]
    indexed = {(r["case_id"], r["method"]): r for r in rows}
    if len(indexed) != len(rows):
        raise ValueError("Expected one final result per method and case")
    packets, rankings = [], {}
    for case in profile["cases"]:
        cid = case["id"]
        if cid not in case_ids:
            continue
        pool = {}
        rankings[cid] = {}
        for method in profile["methods"]:
            if (cid, method["id"]) not in indexed:
                raise ValueError("Incomplete method-case matrix")
            ranking = []
            for item in indexed[cid, method["id"]]["results"][:cutoff]:
                url = canonical_url(item["url"])
                key = hashlib.sha256((cid + "\0" + url).encode()).hexdigest()[:12]
                ranking.append(key)
                candidate = pool.setdefault(key, {"candidate_id": key, "url": url, "previews": []})
                preview = {f: item.get(f) or "" for f in ("title", "snippet")}
                if preview not in candidate["previews"]:
                    candidate["previews"].append(preview)
            rankings[cid][method["id"]] = ranking
        for candidate in pool.values():
            candidate["previews"].sort(key=lambda x: (x["title"], x["snippet"]))
        packets.append({"case_id": cid, "query": case["query"],
                        "required_domains": case.get("domains", []),
                        "candidates": [pool[k] for k in sorted(pool)]})
    blind = {"schema": "web-search-human-annotation/1", "plan": {
        "scope": "all_custom", "cutoff": cutoff, "case_ids": case_ids},
        "blind_candidate_packets": packets}
    blind["packet_sha256"] = hashlib.sha256(json.dumps(blind, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return blind, rankings


def import_annotations(profile, rows, submitted):
    """Validate complete frozen human input, then restore private rank mappings."""
    packet, rankings = candidate_packet(profile, rows)
    if submitted.get("packet_sha256") != packet["packet_sha256"]:
        raise ValueError("Annotation packet does not match the current frozen results")
    if set(submitted.get("reviewed_case_ids", [])) != set(packet["plan"]["case_ids"]):
        raise ValueError("Every case requires explicit review, including empty pools")
    evidence = {**packet, "labels": submitted["labels"], "judge": submitted["judge"],
                "provider_rankings": rankings}
    verify_provenance(profile, rows, evidence)
    score(profile, rows, evidence["plan"], evidence["labels"])
    return evidence


def export_annotations(profile, rows, destination):
    """Write a self-contained human annotation form; no evaluator API is called."""
    packet, _ = candidate_packet(profile, rows)
    destination.mkdir(parents=True, exist_ok=False)
    (destination / "packet.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2))
    payload = json.dumps(packet, ensure_ascii=False).replace("<", "\\u003c")
    page = ANNOTATION_HTML.replace("__PACKET__", payload)
    (destination / "index.html").write_text(page, encoding="utf-8")
    return destination / "index.html"


ANNOTATION_HTML = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>自建搜索题库 · 全量相关性标注</title><style>
body{font:16px/1.6 system-ui;color:#183039;background:#f4f7f8;margin:auto;max-width:1000px;padding:28px}header,article{background:white;padding:24px;border:1px solid #d8e1e5;border-radius:10px;margin:16px 0}button,select,input{font:inherit;padding:8px}a{overflow-wrap:anywhere;color:#065c78}article p{white-space:pre-wrap}nav{display:flex;gap:12px;flex-wrap:wrap;align-items:center}#progress{font-weight:bold}summary{cursor:pointer}label{display:block;margin:10px 0}.error{color:#a32121}small{color:#52676f}</style>
<header><h1>自建搜索题库 · 全量相关性标注</h1><p>按题意判断每个网页是否有用。候选网页来自三家接口的前 5 条结果，按网址去重，隐藏接口名称和原始排名。所有题目都使用同一套标准。</p>
<p><b>0 分</b>：无关，或违反题目明确的网站、日期、语言等要求。<b>1 分</b>：话题相关，但没有可用于回答的信息。<b>2 分</b>：提供部分有效信息。<b>3 分</b>：直接满足主要需求。</p>
<p>先看查询和预览，信息不足时打开原网页核实。技术问题允许多种正确方法；开放问题允许多种相关来源。接口没有返回网页的题目也要确认。分数由人工填写；页面只保存和校验，不会调用模型。</p>
<label>标注者姓名 <input id="reviewer" autocomplete="name"></label><p id="progress"></p><nav><button id="prev">上一题</button><select id="cases"></select><button id="next">下一题</button><button id="unfinished">下一道未完成</button><button id="export">导出完整标注</button></nav><p id="message" role="status"></p></header>
<h2 id="query"></h2><main id="candidates"></main><label><input type="checkbox" id="reviewed">我已核对本题所有候选网页</label>
<script type="application/json" id="packet">__PACKET__</script><script>
const packet=JSON.parse(document.querySelector('#packet').textContent),items=packet.blind_candidate_packets,$=s=>document.querySelector(s),key='search-labels-'+packet.packet_sha256;
const esc=x=>String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let state;try{state=JSON.parse(localStorage.getItem(key))}catch{};state=state||{labels:{},reviewed:[],name:''};let at=0;
const done=c=>state.reviewed.includes(c.case_id)&&c.candidates.every(x=>Number.isInteger(state.labels[c.case_id]?.[x.candidate_id]));
function save(){state.name=$('#reviewer').value;localStorage.setItem(key,JSON.stringify(state));$('#progress').textContent=items.filter(done).length+' / '+items.length+' 题完成；候选网页 '+items.reduce((n,c)=>n+c.candidates.length,0)+' 条'}
function render(){let c=items[at];$('#cases').value=String(at);$('#query').textContent=c.query+(c.required_domains?.length?'（限定网站：'+c.required_domains.join(', ')+'）':'');$('#reviewed').checked=state.reviewed.includes(c.case_id);$('#prev').disabled=at===0;$('#next').disabled=at===items.length-1;
$('#candidates').innerHTML=c.candidates.map(x=>'<article><a href="'+esc(x.url)+'" target="_blank" rel="noopener noreferrer">'+esc(x.url)+'</a>'+x.previews.map(p=>'<h3>'+esc(p.title)+'</h3><p>'+esc(p.snippet)+'</p>').join('')+'<label>相关性 <select data-grade="'+esc(x.candidate_id)+'"><option value="">请选择</option>'+[0,1,2,3].map(g=>'<option value="'+g+'" '+(state.labels[c.case_id]?.[x.candidate_id]===g?'selected':'')+'>'+g+' 分</option>').join('')+'</select></label></article>').join('')||'<article>三家均未返回候选网页。请确认本题，评分时保留这道题。</article>';
document.querySelectorAll('[data-grade]').forEach(el=>el.onchange=()=>{state.labels[c.case_id]??={};if(el.value==='')delete state.labels[c.case_id][el.dataset.grade];else state.labels[c.case_id][el.dataset.grade]=Number(el.value);save()});save()}
$('#cases').innerHTML=items.map((c,i)=>'<option value="'+i+'">'+(i+1)+'. '+esc(c.query)+'</option>').join('');$('#reviewer').value=state.name;$('#reviewer').oninput=save;
$('#cases').onchange=()=>{at=Number($('#cases').value);render()};$('#prev').onclick=()=>{at--;render()};$('#next').onclick=()=>{at++;render()};$('#unfinished').onclick=()=>{let i=items.findIndex(c=>!done(c));if(i>=0){at=i;render()}};
$('#reviewed').onchange=()=>{let cid=items[at].case_id;state.reviewed=state.reviewed.filter(x=>x!==cid);if($('#reviewed').checked)state.reviewed.push(cid);save()};
$('#export').onclick=()=>{if(!state.name.trim()||!items.every(done)){$('#message').textContent='请填写标注者姓名，并完成全部题目后导出。';return}const result={packet_sha256:packet.packet_sha256,judge:{kind:'human',name:state.name.trim()},labels:Object.fromEntries(items.map(c=>[c.case_id,state.labels[c.case_id]||{}])),reviewed_case_ids:items.map(c=>c.case_id)};const u=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:'application/json'}));const a=document.createElement('a');a.href=u;a.download='search-human-annotations.json';a.click();setTimeout(()=>URL.revokeObjectURL(u),1000);$('#message').textContent='完整标注已导出。'};render();
</script></html>'''
