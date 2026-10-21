"""Bounded, registry-driven same-task comparisons for measured search results."""

import html
import hashlib
import json
import re
from urllib.parse import urlsplit

from .core import canonical_url, evaluate, factual_evidence, in_domain


def acceptance(case, row, metric="source"):
    """Return a scoped binary check, never turn an unlabelled query into failure."""
    if row is None:
        return None
    if metric == "primary":
        if "reference_answer" in case:
            metric = "answer"
        elif case.get("anchors"):
            metric = "source"
        elif case.get("domains"):
            metric = "site"
        else:
            return None
    if metric == "answer":
        key = "answer_in_delivered_text_5" if "reference_answer" in case else None
    elif metric == "source":
        key = ("labeled_source_hit_5" if case.get("reference_urls") else
               "anchor_hit_5" if case.get("anchors") else None)
    elif metric == "site":
        if not case.get("domains"):
            return None
        value = row.get("metrics", {}).get("domain_precision")
        if value is None and row.get("metrics", {}).get("result_count") == 0:
            return False
        return value > 0 if value is not None else None
    elif metric == "availability":
        return bool(row.get("results"))
    else:
        raise ValueError("Unknown comparison metric")
    value = row.get("metrics", {}).get(key) if key else None
    return bool(value) if value in (0, 1) and value is not None else None


def outcome_relation(baseline, comparisons):
    """Mixed comparison outcomes have their own bucket; missing scores stay unknown."""
    if baseline is None or not comparisons or any(x is None for x in comparisons):
        return "unknown"
    if baseline and all(comparisons):
        return "all_pass"
    if baseline and not any(comparisons):
        return "baseline_only"
    if not baseline and all(comparisons):
        return "comparison_only"
    if not baseline and not any(comparisons):
        return "all_fail"
    return "mixed"


def _url(value):
    value = str(value or "")
    parsed = urlsplit(value)
    return value if parsed.scheme in ("https", "http") and not parsed.username and not parsed.password else ""


def _preview(value, limit=None):
    # Public code examples can contain a source author's home directory.
    # Compact previews omit those paths; the immutable response remains linked.
    value = re.sub(r"/Users/[^\s\"<>]+", "[source path]", str(value or ""))
    return value if limit is None else value[:limit]


def _item_checks(case, item):
    """Expose deterministic evidence checks per result without adding a judge."""
    safe = {"url": _url(item.get("url")), **{field: str(item.get(field) or "") for field in ("title", "snippet", "summary", "content")}}
    source = None
    if safe["url"] and case.get("reference_urls"):
        source = bool(factual_evidence(case, [safe])["labeled_source_hit_5"])
    elif safe["url"] and case.get("anchors"):
        source = bool(evaluate(case, [safe])["anchor_hit_5"])
    fields = {field: False for field in ("title", "snippet", "summary", "content")}
    if "reference_answer" in case:
        for field in fields:
            candidate = dict(safe)
            for other in fields:
                if other != field:
                    candidate[other] = ""
            fields[field] = bool(factual_evidence(case, [candidate])["answer_in_delivered_text_5"])
    return source, fields


def comparison_data(profile, selected_rows, zh=False, detail_link=None, relevance=None):
    """Only compact measured previews enter the page, with full evidence linked once."""
    methods = [{"id": m["id"], "label": m["label"]} for m in profile["methods"]]
    method_ids = {m["id"] for m in methods}
    rows = {}
    for row in selected_rows:
        if row["method"] not in method_ids:
            continue
        key = (row["case_id"], row["method"])
        if key in rows:
            raise ValueError("Comparison requires one logical result per method and task")
        rows[key] = row
    labels = (relevance or {}).get("labels", {})
    judgments = {}
    for call in (relevance or {}).get("judge", {}).get("calls", []):
        from .relevance import parse_judgments
        packet = json.loads(call["request"]["messages"][1]["content"])
        judgments[call["case_id"]] = {x["candidate_id"]: x for x in parse_judgments(packet, call["response"])}
    cases = []
    for case in profile["cases"]:
        outputs = {}
        for method in methods:
            row = rows.get((case["id"], method["id"]))
            if row is None:
                outputs[method["id"]] = None
                continue
            items = []
            for item in row.get("results", [])[:5]:
                source_match, answer_fields = _item_checks(case, item)
                displayed = {field: _preview(item.get(field), limit)
                             for field, limit in (("title", None), ("snippet", None),
                                                  ("summary", None), ("content", 480))}
                _, visible_answers = _item_checks(case, {"url": item.get("url"), **displayed})
                candidate_id = hashlib.sha256((case["id"] + "\0" + canonical_url(item["url"])).encode()).hexdigest()[:12] if _url(item.get("url")) else ""
                judgment = judgments.get(case["id"], {}).get(candidate_id, {})
                items.append({"url": _url(item.get("url")),
                              "grade_reason": judgment.get("reason", ""),
                              "grade_evidence": judgment.get("evidence", ""),
                              "source_match": source_match, "answer_fields": visible_answers,
                              "answer_outside_preview": answer_fields["content"] and not visible_answers["content"],
                              "grade": (labels[case["id"]].get(hashlib.sha256((case["id"] + "\0" + canonical_url(item["url"])).encode()).hexdigest()[:12]) if case["id"] in labels and _url(item.get("url")) else None),
                              "site_match": any(in_domain(item.get("url", ""), d) for d in case.get("domains", [])),
                              **displayed})
            outputs[method["id"]] = {
                "primary": (any(x["grade"] >= 2 for x in items) if case["id"] in labels else acceptance(case, row, "primary")),
                "quality": any(x["grade"] >= 2 for x in items) if case["id"] in labels else None,
                "source": acceptance(case, row), "answer": acceptance(case, row, "answer"),
                "site": acceptance(case, row, "site"),
                "availability": acceptance(case, row, "availability"),
                "items": items, "count": len(row.get("results", [])),
                "error": str(row.get("error") or "")[:200],
                "detail": detail_link(row) if detail_link else "details/" + row["id"] + ".html",
            }
        benchmark = "factual" if "reference_answer" in case else "custom"
        check_types = (["factual"] if benchmark == "factual" else
                       (["target"] if case.get("anchors") else []) +
                       (["site"] if case.get("domains") else []))
        if not check_types:
            check_types = ["availability"]
        cases.append({"id": case["id"], "query": case["query"],
                      "benchmark": benchmark, "check_types": check_types, "has_relevance": case["id"] in labels,
                      "criteria": {"answer": case.get("reference_answer"),
                                   "urls": [_url(u) for u in case.get("reference_urls", case.get("anchors", [])) if _url(u)],
                                   "domains": case.get("domains", []),
                                   "aliases": case.get("identity_aliases", {}),
                                   "paper_ids": case.get("paper_ids", []),
                                   "repositories": case.get("repositories", [])},
                      "outputs": outputs})
    populations = profile.get("comparison_populations")
    if not populations:
        populations = [
            {"id": "overall", "label": {"en": "All tasks", "zh": "总体"}, "case_ids": [c["id"] for c in cases]},
            {"id": "factual", "label": {"en": "Public factual questions", "zh": "公开事实题"}, "case_ids": [c["id"] for c in cases if c["benchmark"] == "factual"]},
            {"id": "reference", "label": {"en": "Reference-page questions", "zh": "参考网页题"}, "case_ids": [c["id"] for c in profile["cases"] if c.get("anchors") and "reference_answer" not in c]},
        ]
    ids = {c["id"] for c in cases}
    if any(not set(p["case_ids"]).issubset(ids) for p in populations):
        raise ValueError("Comparison population contains unknown tasks")
    populations = [{"id": p["id"], "label": p["label"]["zh" if zh else "en"],
                    "case_ids": p["case_ids"]} for p in populations if p["case_ids"]]
    return {"methods": methods, "cases": cases, "populations": populations}


STYLE = """
.search-compare{margin:32px 0}.search-compare fieldset{min-width:0}.compare-controls{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:16px 0}.compare-controls label{display:grid;gap:4px;font-size:14px}.compare-checks{display:flex;flex-wrap:wrap;gap:12px}.compare-checks label{display:flex;align-items:center;gap:5px}.compare-counts{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.compare-counts span{padding:7px 12px;background:var(--panel);border:1px solid var(--line);border-radius:6px}.compare-task{border:1px solid var(--line);border-radius:10px;margin:20px 0;overflow:hidden;background:var(--panel)}.compare-criteria{padding:16px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.compare-criteria p{margin:6px 0}.compare-criteria a{display:block}.compare-task h3{margin:0;padding:16px;border-bottom:1px solid var(--line);font-size:18px}.compare-columns{display:grid;grid-template-columns:repeat(var(--method-count),minmax(240px,1fr));overflow-x:auto}.compare-method{padding:16px;min-width:0;border-right:1px solid var(--line)}.compare-method:last-child{border-right:0}.compare-method h4{margin:0 0 10px;font-size:17px}.compare-result{padding:12px 0;border-top:1px solid var(--line)}.compare-result p{font-size:14px;margin:6px 0}.compare-result a{display:block;font-size:14px;overflow-wrap:anywhere}.compare-result summary{cursor:pointer;font-size:13px}.compare-result details p{max-height:280px;overflow:auto;white-space:pre-wrap}.compare-url{color:var(--muted);font-size:12px!important}.compare-status{font-size:13px;color:var(--muted)}.compare-match{border-left:4px solid var(--win);padding-left:8px}.compare-match-note{color:var(--win);font-size:12px;font-weight:700}.compare-pager{display:flex;align-items:center;gap:14px;justify-content:center}.compare-notice{font-size:14px;color:var(--muted)}.compare-relations{height:410px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin:14px 0}.compare-relations table{width:100%;border-collapse:collapse;min-width:650px}.compare-relations th,.compare-relations td{padding:10px;border-bottom:1px solid var(--line);text-align:left}.compare-relations th{position:sticky;top:0;background:var(--panel)}.compare-relations td:first-child{min-width:240px;max-width:430px}.compare-relations td button{text-align:left;font-size:14px;display:block}.compare-relations small{color:var(--muted)}.compare-pill{display:inline-block;padding:3px 9px;border:1px solid var(--line);border-radius:20px;font-size:13px;white-space:nowrap}.compare-pill.hit{color:var(--win)}.compare-pill.miss{color:var(--fg)}@media(max-width:650px){.compare-columns{grid-template-columns:repeat(var(--method-count),minmax(260px,1fr))}.compare-controls input{width:100%}}
"""

SCRIPT = r"""
(()=>{
const root=document.querySelector('[data-search-comparison]');if(!root)return;
const data=JSON.parse(root.querySelector('[data-comparison-data]').textContent),L=JSON.parse(root.dataset.labels),$=s=>root.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let page=0,relationPage=0,focusedId=null;const size=3,relationSize=10;
const selected=()=>[...root.querySelectorAll('[data-compare-method]:checked')].map(x=>x.value).filter(x=>x!==$('[data-compare-baseline]').value);
const relation=(base,others)=>{if(base==null||!others.length||others.some(v=>v==null))return 'unknown';if(base&&others.every(Boolean))return 'all_pass';if(base&&others.every(v=>!v))return 'baseline_only';if(!base&&others.every(Boolean))return 'comparison_only';if(!base&&others.every(v=>!v))return 'all_fail';return 'mixed'};
const effectiveMetric=(c,m)=>m==='primary'?(c.has_relevance?'quality':c.benchmark==='factual'?'answer':c.check_types.includes('target')?'source':c.check_types.includes('site')?'site':'unscored'):m;
const verdict=(c,o,m)=>{const k=effectiveMetric(c,m),v=o?.[m];return v==null?L.unknown:L.verdicts[k][v?0:1]};
const criteria=c=>{const x=c.criteria;return '<div class="compare-criteria" data-task-criteria><strong>'+esc(L.criteria)+'</strong>'+(x.answer!=null?'<p>'+esc(L.referenceAnswer)+': <b>'+esc(x.answer)+'</b></p>':'')+(x.urls.length?'<p>'+esc(L.referenceUrls)+'</p>'+x.urls.map(u=>'<a href="'+esc(u)+'" target="_blank" rel="noopener noreferrer">'+esc(u)+'</a>').join(''):'')+(x.domains.length?'<p>'+esc(L.requestedSites)+': '+x.domains.map(esc).join(', ')+'</p>':'')+((x.paper_ids.length||x.repositories.length||Object.keys(x.aliases).length)?'<details><summary>'+esc(L.equivalents)+'</summary><pre>'+esc(JSON.stringify({aliases:x.aliases,papers:x.paper_ids,repositories:x.repositories},null,2))+'</pre></details>':'')+(!x.urls.length&&x.answer==null&&!x.domains.length?'<p>'+esc(L.unannotated)+'</p>':'')+'</div>'};
function matching(){let q=$('[data-output-query]').value.toLowerCase(),benchmark=$('[data-output-benchmark]').value,check=$('[data-output-check]').value;return data.cases.filter(c=>(benchmark==='all'||c.benchmark===benchmark)&&(check==='all'||c.check_types.includes(check))&&(!focusedId||c.id===focusedId)&&(!q||c.query.toLowerCase().includes(q)||c.id.toLowerCase().includes(q)))}
function render(){root.querySelectorAll('[data-compare-method]').forEach(x=>x.disabled=x.value===$('[data-compare-baseline]').value);let cases=matching(),metric=$('[data-compare-metric]').value,base=$('[data-compare-baseline]').value,others=selected(),counts={},matches=[];for(let c of cases){let r=relation(c.outputs[base]?.[metric],others.map(m=>c.outputs[m]?.[metric]));counts[r]=(counts[r]||0)+1;if($('[data-compare-relation]').value==='all'||r===$('[data-compare-relation]').value)matches.push(c)}
$('[data-comparison-counts]').innerHTML=Object.keys(L.relations).map(k=>'<span>'+esc(L.relations[k])+': <strong>'+(counts[k]||0)+'</strong></span>').join('');
relationPage=Math.min(relationPage,Math.max(0,Math.ceil(matches.length/relationSize)-1));
const compared=data.methods.filter(m=>m.id===base||others.includes(m.id));
$('[data-relation-tasks]').innerHTML=matches.length?'<table><thead><tr><th>'+esc(L.query)+'</th>'+compared.map(m=>'<th>'+esc(m.label)+'</th>').join('')+'</tr></thead><tbody>'+matches.slice(relationPage*relationSize,(relationPage+1)*relationSize).map(c=>'<tr><td><small>'+esc(c.id)+'</small><button data-show-query="'+esc(c.id)+'">'+esc(c.query)+'</button></td>'+compared.map(m=>{const o=c.outputs[m.id],v=o?.[metric],label=verdict(c,o,metric);return '<td>'+(o?'<a class="compare-pill '+(v==null?'unknown':v?'hit':'miss')+'" href="'+esc(o.detail)+'">'+esc(label)+'</a>':esc(L.missing))+'</td>'}).join('')+'</tr>').join('')+'</tbody></table>':'<p>'+esc(L.none)+'</p>';
$('[data-relation-page]').textContent=(matches.length?relationPage+1:0)+' / '+Math.ceil(matches.length/relationSize)+' · '+matches.length+' '+L.tasks;
$('[data-relation-prev]').disabled=relationPage===0;$('[data-relation-next]').disabled=(relationPage+1)*relationSize>=matches.length;
root.querySelectorAll('[data-show-query]').forEach(b=>b.onclick=()=>{focusedId=b.dataset.showQuery;$('[data-output-query]').value=data.cases.find(c=>c.id===focusedId).query;page=0;relationPage=0;render();$('[data-output-pages]').scrollIntoView({block:'start',behavior:'smooth'})});
    page=Math.min(page,Math.max(0,Math.ceil(matches.length/size)-1));
    const field=(x,f,body)=>'<'+(f==='title'?'div':'p')+' class="'+(x.answer_fields?.[f]?'compare-match':'')+'">'+body+(x.answer_fields?.[f]?'<span class="compare-match-note"> · '+esc(L.answerField)+'</span>':'')+'</'+(f==='title'?'div':'p')+'>';
    $('[data-output-pages]').innerHTML=matches.slice(page*size,(page+1)*size).map(c=>'<article class="compare-task"><h3>'+esc(c.query)+'</h3>'+criteria(c)+'<div class="compare-columns" style="--method-count:'+data.methods.length+'">'+data.methods.map(m=>{let o=c.outputs[m.id];return '<section class="compare-method"><h4>'+esc(m.label)+'</h4>'+(o?'<p class="compare-status">'+esc(L.results)+': '+o.count+' · '+esc(verdict(c,o,metric))+'</p><a href="'+esc(o.detail)+'">'+esc(L.full)+'</a>'+(!o.items.length?'<p>'+esc(o.error||L.empty)+'</p>':o.items.map((x,i)=>'<div class="compare-result '+(x.source_match||x.site_match||x.grade>=2?'compare-match':'')+'">'+(x.grade!=null?'<p>'+esc(L.grade)+': '+x.grade+' / 3</p>':'')+(x.grade_reason?'<details><summary>'+esc(L.gradeReason)+'</summary><p>'+esc(x.grade_reason)+'</p><blockquote>'+esc(x.grade_evidence)+'</blockquote></details>':'')+(x.source_match?'<span class="compare-match-note">'+esc(L.sourceItem)+'</span>':'')+(x.site_match?'<span class="compare-match-note">'+esc(L.siteItem)+'</span>':'')+field(x,'title','<a href="'+esc(x.url)+'" target="_blank" rel="noopener noreferrer">'+(i+1)+'. '+esc(x.title||x.url||L.untitled)+'</a>')+'<p class="compare-url">'+esc(x.url)+'</p>'+field(x,'snippet',esc(x.snippet))+['summary','content'].filter(f=>x[f]).map(f=>'<details><summary>'+esc(L[f])+'</summary>'+field(x,f,esc(x[f]))+(f==='content'&&x.answer_outside_preview?'<p>'+esc(L.outsidePreview)+'</p>':'')+'</details>').join('')+'</div>').join('')):'<p>'+esc(L.missing)+'</p>')+'</section>'}).join('')+'</div></article>').join('')||'<p>'+esc(L.none)+'</p>';
    $('[data-output-page]').textContent=(matches.length?page+1:0)+' / '+Math.ceil(matches.length/size)+' · '+matches.length+' '+L.tasks;
    $('[data-output-prev]').disabled=page===0;$('[data-output-next]').disabled=(page+1)*size>=matches.length;
}
root.querySelectorAll('select,input').forEach(x=>x.addEventListener(x.matches('[data-output-query]')?'input':'change',()=>{page=0;relationPage=0;focusedId=null;render()}));
$('[data-relation-prev]').onclick=()=>{relationPage--;render()};$('[data-relation-next]').onclick=()=>{relationPage++;render()};
$('[data-output-prev]').onclick=()=>{page--;render()};$('[data-output-next]').onclick=()=>{page++;render()};render();
})();
"""


def render_comparison(profile, selected_rows, zh, detail_link=None, relevance=None):
    """Render both results-analysis modules; embed once per results page."""
    t = lambda en, cn: cn if zh else en
    data = comparison_data(profile, selected_rows, zh, detail_link, relevance)
    e = lambda value: html.escape(str(value), quote=True)
    labels = {"relations": dict(zip(
        ("all_pass", "baseline_only", "comparison_only", "all_fail", "mixed", "unknown"),
        (t("All selected methods hit", "所选方法均命中"), t("Only baseline hits", "仅基准命中"),
         t("Only comparison methods hit", "仅对比方法命中"), t("None hit", "所选方法均未命中"),
         t("Comparison methods disagree", "对比方法结果不同"), t("No comparable score", "无可比评分")), strict=True)),
        "query": t("Task / query", "题目 / 查询"), "none": t("No matching tasks", "没有符合条件的题目"), "unknown": t("Unscored", "未设评分"),
        "hit": t("Found", "命中"), "miss": t("Not found", "未命中"),
        "criteria": t("What this question checks", "本题判断依据"),
        "referenceAnswer": t("Dataset answer", "题库参考答案"),
        "referenceUrls": t("Reference URLs: compare the first five result URLs", "参考网址：核对前 5 条结果的网址"),
        "requestedSites": t("Required websites", "查询限定的网站"),
        "equivalents": t("Accepted equivalent URLs and identifiers", "允许匹配的等价网址与标识"),
        "unannotated": t("Relevance annotation is missing; a nonempty response only establishes availability.", "本题尚缺相关性标注；返回非空只说明接口可用。"),
        "siteItem": t("Matches required website", "来自限定网站"),
        "grade": t("Relevance grade", "相关性评分"),
        "gradeReason": t("Scoring reason and quoted evidence", "评分理由与引用依据"),
        "verdicts": {"quality": [t("Relevant result found", "找到相关结果（评分至少 2 分）"), t("No relevant result in top 5", "前 5 条没有达到 2 分的结果")], "source": [t("Reference URL found", "找到参考网址"), t("Reference URL absent from top 5", "前 5 条未找到参考网址")],
                     "answer": [t("Answer text found", "文字包含参考答案"), t("Answer text absent", "文字未包含参考答案")],
                     "site": [t("Required website found", "找到限定网站的结果"), t("Required website absent", "未找到限定网站的结果")],
                     "availability": [t("Nonempty response", "返回非空"), t("Empty response", "返回为空")]},
        "results": t("Returned results", "返回条数"), "full": t("Open full response and evidence", "查看完整响应与证据"),
        "empty": t("No results returned", "没有返回结果"), "missing": t("No measured result", "没有实测结果"),
        "untitled": t("Untitled result", "无标题结果"), "summary": t("Full page summary", "完整长摘要"),
        "content": t("Page body excerpt", "网页正文节选"), "sourceItem": t("Reference page", "命中参考网页"),
        "outsidePreview": t("The answer text occurs later in the full body; use the full response link above.", "答案线索位于完整正文的后部；请通过上方完整响应链接查看。"),
        "answerField": t("contains answer cue", "包含答案线索"), "tasks": t("tasks", "题")}
    options = lambda values: ''.join(f'<option value="{e(k)}">{e(v)}</option>' for k, v in values)
    methods = [(m["id"], m["label"]) for m in data["methods"]]
    custom_count = sum(c["benchmark"] == "custom" for c in data["cases"])
    factual_count = sum(c["benchmark"] == "factual" for c in data["cases"])
    benchmark_options = [
        ("all", t(f"Both benchmarks · {len(data['cases'])}", f"两套评测 · {len(data['cases'])}")),
        ("custom", t(f"In-house scenarios · {custom_count}", f"自建场景集 · {custom_count}")),
        ("factual", t(f"SimpleQA retrieval diagnostic · {factual_count}", f"SimpleQA 检索诊断 · {factual_count}")),
    ]
    return ('<style>' + STYLE + '</style><section class="search-compare" data-search-comparison data-labels="' + e(json.dumps(labels, ensure_ascii=False)) + '">'
        + '<div class="compare-controls"><label>' + t('Find a query', '搜索题目') + '<input type="search" data-output-query></label><label>' + t('Benchmark', '评测集') + '<select data-output-benchmark>' + options(benchmark_options) + '</select></label><label>' + t('Task check', '题型与检查') + '<select data-output-check>' + options([('all', t('All task types', '全部题型')), ('target', t('Reference-page discovery', '目标网页发现')), ('site', t('Requested-site constraint', '指定网站约束')), ('availability', t('Open discovery availability', '开放探索可用性')), ('factual', t('Factual evidence', '事实证据'))]) + '</select></label></div>'
        + '<h2>' + t('Compare methods on the same tasks', '同一道题，各方法是否命中') + '</h2><p class="compare-notice">'
        + t('Source checks use independently identified reference pages or published supporting sources. Answer checks use all delivered text before benchmark-mirror exclusion, including explicitly accepted answer variants; they do not judge factual correctness. Unlabelled tasks remain unscored.', '来源检查核对独立参考网页或题库标注来源；答案检查使用全部交付文本及明确允许的答案变体，尚未排除题库镜像，不判断事实正确性。没有标注的题目不计为失败。')
        + '</p><div class="compare-controls"><label>' + t('Check', '检查项目') + '<select data-compare-metric>' + options([('primary', t('Primary check for each task type', '按题型使用主要检查')), ('quality', t('Relevance · grade at least 2', '相关性：至少 2 分')), ('source', t('Reference target / published source', '目标网页／标注来源')), ('answer', t('Answer cue in delivered text · raw', '返回文字中的答案线索')), ('site', t('Requested-site constraint', '指定网站约束')), ('availability', t('Nonempty response', '非空响应'))]) + '</select></label><label>' + t('Baseline', '基准方法') + '<select data-compare-baseline>' + options(methods) + '</select></label></div><fieldset><legend>' + t('Compare with', '对比方法') + '</legend><div class="compare-checks">'
        + ''.join('<label><input type="checkbox" data-compare-method checked value="' + e(k) + '">' + e(v) + '</label>' for k,v in methods)
        + '</div></fieldset><div class="compare-counts" data-comparison-counts aria-live="polite"></div><label>' + t('Show tasks where', '查看题目') + '<select data-compare-relation>' + options([('all', t('All outcome relations', '全部结果'))] + list(labels['relations'].items())) + '</select></label><div class="compare-relations" data-relation-tasks></div><div class="compare-pager"><button data-relation-prev>' + t('Previous', '上一页') + '</button><span data-relation-page aria-live="polite"></span><button data-relation-next>' + t('Next', '下一页') + '</button></div>'
        + '<h2>' + t('Actual outputs, side by side', '逐题查看实际输出') + '</h2><p class="compare-notice">' + t('The dataset, query, check, providers and outcome filters above also control this view. Each column shows the first five results. Page summaries are complete; page bodies remain compact previews. Green marks identify the exact reference-page or answer-cue check, not a semantic correctness judgment.', '上方的题库、题目、检查项目、方法和命中状态会同步筛选这里。每列展示前 5 条结果，长摘要完整显示，网页正文保留节选。绿色只表示该条命中参考网页或该字段含答案线索，不代表整条结果语义正确。') + '</p><div data-output-pages></div><div class="compare-pager"><button data-output-prev>' + t('Previous', '上一页') + '</button><span data-output-page aria-live="polite"></span><button data-output-next>' + t('Next', '下一页') + '</button></div>'
        + '<script type="application/json" data-comparison-data>' + json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026') + '</script></section><script>' + SCRIPT + '</script>')
