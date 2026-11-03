"""Bounded, registry-driven same-task comparisons for measured search results."""

import json
from functools import partial
from urllib.parse import urlsplit

from lexbench_websearch.evidence_quality import is_benchmark_page
from lexbench_websearch.relevance import query_metrics
from lexbench_websearch.scoring import (
    candidate_identity,
    evaluate,
    factual_evidence,
    in_domain,
    task_acceptance,
)

from harness.reporting.presentation import esc
from harness.reporting.privacy import WEB_SEARCH_PREVIEW
from harness.web_search.messages import report_text, zh_text
from harness.web_search.relevance import parse_judgments


def acceptance(case, row, metric="source", *, quality_excluded=False, cutoff=5, relevance_grades=None):
    return task_acceptance(case, row, metric, quality_excluded=quality_excluded, cutoff=cutoff, relevance_grades=relevance_grades)


def _url(value):
    value = str(value or "")
    parsed = urlsplit(value)
    return value if parsed.scheme in ("https", "http") and not parsed.username and not parsed.password else ""


def _preview(value, limit=None):
    # Public code examples can contain a source author's home directory.
    # Compact previews omit those paths; the immutable response remains linked.
    value = WEB_SEARCH_PREVIEW.redact(str(value or ""))
    return value if limit is None else value[:limit]


def _item_checks(case, item, *, quality_excluded=False, contamination_excluded=False):
    """Expose deterministic evidence checks per result without adding a judge."""
    safe = {"url": _url(item.get("url")), **{field: str(item.get(field) or "") for field in ("title", "snippet", "summary", "content")}}
    fields = {field: None if quality_excluded else False
              for field in ("title", "snippet", "summary", "content")}
    if quality_excluded:
        return None, fields
    if (contamination_excluded
            or ("reference_answer" in case and is_benchmark_page(safe, case=case))):
        return False, fields
    source = None
    if safe["url"] and case.get("reference_urls"):
        source = bool(factual_evidence(case, [safe])["labeled_source_hit_5"])
    elif safe["url"] and case.get("anchors"):
        source = bool(evaluate(case, [safe])["anchor_hit_5"])
    if "reference_answer" in case:
        for field in fields:
            candidate = dict(safe)
            for other in fields:
                if other != field:
                    candidate[other] = ""
            fields[field] = bool(factual_evidence(case, [candidate])["answer_in_delivered_text_5"])
    return source, fields


def comparison_data(profile, selected_rows, zh=False, detail_link=None, relevance=None, *, primary_factual_metric="answer"):
    """Only compact measured previews enter the page, with full evidence linked once."""
    methods = [{"id": m["id"], "label": m["label"]} for m in profile["methods"]]
    if primary_factual_metric not in ("source", "answer"):
        raise ValueError("Unsupported primary factual check")
    method_ids = {m["id"] for m in methods}
    rows = {}
    for row in selected_rows:
        if row["method"] not in method_ids:
            continue
        key = (row["case_id"], row["method"])
        if key in rows:
            raise ValueError("Comparison requires one logical result per method and task")
        rows[key] = row
    cutoff = profile.get("protocol", {}).get("top_k", 5)
    labels = (relevance or {}).get("labels", {})
    judgments = {}
    packets = {p["case_id"]: p for p in (relevance or {}).get("blind_candidate_packets", [])}
    for call in (relevance or {}).get("judge", {}).get("calls", []):
        packet = packets[call["case_id"]]
        judgments[call["case_id"]] = {x["candidate_id"]: x for x in parse_judgments(packet, call["response"])}
    cases = []
    exclusions = set(profile.get("factual_quality_exclusions", []))
    for case in profile["cases"]:
        quality_excluded = "reference_answer" in case and case["id"] in exclusions
        outputs = {}
        for method in methods:
            row = rows.get((case["id"], method["id"]))
            if row is None:
                outputs[method["id"]] = None
                continue
            items = []
            for item in row.get("results", [])[:cutoff]:
                contaminated = "reference_answer" in case and is_benchmark_page(item, case=case)
                source_match, answer_fields = _item_checks(
                    case, item, quality_excluded=quality_excluded,
                    contamination_excluded=contaminated)
                displayed = {field: _preview(item.get(field), limit)
                             for field, limit in (("title", None), ("snippet", None),
                                                  ("summary", None), ("content", 480))}
                _, visible_answers = _item_checks(
                    case, {"url": item.get("url"), **displayed},
                    quality_excluded=quality_excluded, contamination_excluded=contaminated)
                candidate_id = candidate_identity(case["id"], item["url"]) if _url(item.get("url")) else ""
                judgment = judgments.get(case["id"], {}).get(candidate_id, {})
                items.append({"url": _url(item.get("url")),
                              "grade_reason": judgment.get("reason", ""),
                              "grade_source": judgment.get("grade_source", (relevance or {}).get("judge", {}).get("kind", "unjudged")),
                              "grade_evidence": (judgment.get("evidence") or (report_text("Selected field is empty", zh) if judgment.get("evidence_empty") else "")),
                              "contamination_excluded": contaminated,
                              "source_match": source_match, "answer_fields": visible_answers,
                              "answer_outside_preview": answer_fields["content"] and not visible_answers["content"],
                              "grade": (labels[case["id"]].get(candidate_identity(case["id"], item["url"])) if case["id"] in labels and _url(item.get("url")) and not (quality_excluded or contaminated) else None),
                              "site_match": not (quality_excluded or contaminated) and any(in_domain(item.get("url", ""), d) for d in case.get("domains", [])),
                              **displayed})
            ranking = [candidate_identity(case["id"], item["url"]) for item in row.get("results", [])[:cutoff] if _url(item.get("url"))]
            query_score = query_metrics(ranking, labels[case["id"]], cutoff) if case["id"] in labels and not quality_excluded else None
            outputs[method["id"]] = {
                "score": query_score,
                "primary": acceptance(case, row, primary_factual_metric if "reference_answer" in case else "primary", cutoff=cutoff, quality_excluded=quality_excluded, relevance_grades=[x["grade"] for x in items] if case["id"] in labels else None),
                "quality": any(x["grade"] is not None and x["grade"] >= 2 for x in items) if case["id"] in labels and not quality_excluded else None,
                "source": acceptance(case, row, quality_excluded=quality_excluded, cutoff=cutoff), "answer": acceptance(case, row, "answer", quality_excluded=quality_excluded, cutoff=cutoff),
                "site": acceptance(case, row, "site", cutoff=cutoff),
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
                      "benchmark": case.get('benchmark') or case.get('cohort') or benchmark, "quality_excluded": quality_excluded, "check_types": check_types, "has_relevance": case["id"] in labels,
                      "criteria": {"answer": case.get("reference_answer"),
                                   "official_sources": (relevance or {}).get("source_requirements", {}).get(case["id"], {}),
                                   "urls": [_url(u) for u in case.get("reference_urls", case.get("anchors", [])) if _url(u)],
                                   "domains": case.get("domains", []),
                                   "aliases": case.get("identity_aliases", {}),
                                   "paper_ids": case.get("paper_ids", []),
                                   "repositories": case.get("repositories", [])},
                      "outputs": outputs})
    populations = profile.get("comparison_populations")
    if not populations:
        populations = [
            {"id": "overall", "label": {"en": "All tasks", "zh": zh_text("All tasks")}, "case_ids": [c["id"] for c in cases]},
            {"id": "factual", "label": {"en": "Public factual questions", "zh": zh_text("Public factual questions")}, "case_ids": [c["id"] for c in cases if 'factual' in c['check_types']]},
            {"id": "reference", "label": {"en": "Reference-page questions", "zh": zh_text("Reference-page questions")}, "case_ids": [c["id"] for c in profile["cases"] if c.get("anchors") and "reference_answer" not in c]},
        ]
    ids = {c["id"] for c in cases}
    if any(not set(p["case_ids"]).issubset(ids) for p in populations):
        raise ValueError("Comparison population contains unknown tasks")
    populations = [{"id": p["id"], "label": p["label"]["zh" if zh else "en"],
                    "case_ids": p["case_ids"]} for p in populations if p["case_ids"]]
    return {"methods": methods, "cases": cases, "populations": populations, "cutoff": cutoff, "primary_factual_metric": primary_factual_metric}


STYLE = """
.search-compare{margin:32px 0}.search-compare fieldset{min-width:0}.compare-controls{display:flex;gap:12px;flex-wrap:wrap;align-items:end;margin:16px 0}.compare-controls label{display:grid;gap:4px;font-size:14px}.compare-checks{display:flex;flex-wrap:wrap;gap:12px}.compare-checks label{display:flex;align-items:center;gap:5px}.compare-counts{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}.compare-counts span{padding:7px 12px;background:var(--panel);border:1px solid var(--line);border-radius:6px}.compare-task{border:1px solid var(--line);border-radius:10px;margin:20px 0;overflow:hidden;background:var(--panel)}.compare-criteria{padding:16px;border-bottom:1px solid var(--line);overflow-wrap:anywhere}.compare-criteria p{margin:6px 0}.compare-criteria a{display:block}.compare-task h3{margin:0;padding:16px;border-bottom:1px solid var(--line);font-size:18px}.compare-columns{display:grid;grid-template-columns:repeat(var(--method-count),minmax(240px,1fr));overflow-x:auto}.compare-method{padding:16px;min-width:0;border-right:1px solid var(--line)}.compare-method:last-child{border-right:0}.compare-method h4{margin:0 0 10px;font-size:17px}.compare-result{padding:12px 0;border-top:1px solid var(--line)}.compare-result p{font-size:14px;margin:6px 0}.compare-result a{display:block;font-size:14px;overflow-wrap:anywhere}.compare-result summary{cursor:pointer;font-size:13px}.compare-result details p{max-height:280px;overflow:auto;white-space:pre-wrap}.compare-url{color:var(--muted);font-size:12px!important}.compare-status{font-size:13px;color:var(--muted)}.compare-match{border-left:4px solid var(--win);padding-left:8px}.compare-match-note{color:var(--win);font-size:12px;font-weight:700}.query-score{padding:12px 0;display:grid;gap:7px}.query-score>strong{font-size:19px}.query-score small,.query-score>span{font-size:12px;color:var(--muted)}.query-score details table{font-size:12px;width:100%}.query-score details p{font-size:12px;overflow-wrap:anywhere}.grade-badge{display:inline-block;padding:3px 7px;background:var(--bg);border-radius:4px}.grade-2,.grade-3{color:var(--win);font-weight:700}.compare-pager{display:flex;align-items:center;gap:14px;justify-content:center}.compare-notice{font-size:14px;color:var(--muted)}.compare-relations{height:410px;overflow:auto;border:1px solid var(--line);border-radius:8px;margin:14px 0}.compare-relations table{width:100%;border-collapse:collapse;min-width:650px}.compare-relations th,.compare-relations td{padding:10px;border-bottom:1px solid var(--line);text-align:left}.compare-relations th{position:sticky;top:0;background:var(--panel)}.compare-relations td:first-child{min-width:240px;max-width:430px}.compare-relations td button{text-align:left;font-size:14px;display:block}.compare-relations small{color:var(--muted)}.compare-pill{display:inline-block;padding:3px 9px;border:1px solid var(--line);border-radius:20px;font-size:13px;white-space:nowrap}.compare-pill.hit{color:var(--win)}.compare-pill.miss{color:var(--fg)}@media(max-width:650px){.compare-columns{grid-template-columns:repeat(var(--method-count),minmax(260px,1fr))}.compare-controls input{width:100%}}
"""

SCRIPT = r"""
(()=>{
const root=document.querySelector('[data-search-comparison]');if(!root)return;
const data=JSON.parse(root.querySelector('[data-comparison-data]').textContent),L=JSON.parse(root.dataset.labels),$=s=>root.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let page=0,relationPage=0,focusedId=null;const size=1,relationSize=10;
const selected=()=>[...root.querySelectorAll('[data-compare-method]:checked')].map(x=>x.value).filter(x=>x!==$('[data-compare-baseline]').value);
const relation=(base,others)=>{if(base==null||!others.length||others.some(v=>v==null))return 'unknown';if(base&&others.every(Boolean))return 'all_pass';if(base&&others.every(v=>!v))return 'baseline_only';if(!base&&others.every(Boolean))return 'comparison_only';if(!base&&others.every(v=>!v))return 'all_fail';return 'mixed'};
const effectiveMetric=(c,m)=>m==='primary'?(c.has_relevance?'quality':c.benchmark==='factual'?data.primary_factual_metric:c.check_types.includes('target')?'source':c.check_types.includes('site')?'site':'unscored'):m;
const verdict=(c,o,m)=>{if(c.quality_excluded&&['primary','source','answer','quality'].includes(m))return L.quarantined;const k=effectiveMetric(c,m),v=o?.[m];return v==null?L.unknown:L.verdicts[k][v?0:1]};
const scoreSummary=o=>!o.score?'':'<div class="query-score" data-query-score><strong>'+esc(L.qualityScore)+' '+(o.score.ndcg*100).toFixed(1)+'<small> / 100</small></strong><span>'+esc(L.usefulCount)+' '+o.score.useful+'/'+data.cutoff+' · '+esc(o.score.success?L.direct:L.noDirect)+'</span><details><summary>'+esc(L.calculation)+'</summary><table><thead><tr><th>'+esc(L.rank)+'</th><th>'+esc(L.grade)+'</th><th>'+esc(L.weightedGain)+'</th></tr></thead><tbody>'+Array.from({length:data.cutoff},(_,i)=>'<tr><td>'+(i+1)+'</td><td>'+(i<o.score.ranked_grades.length?o.score.ranked_grades[i]:esc(L.notReturned))+(o.score.duplicates[i]?' · '+esc(L.duplicate):'')+'</td><td>'+(o.score.contributions[i]||0).toFixed(4)+'</td></tr>').join('')+'</tbody></table><p>'+esc(L.idealGrades)+': '+o.score.ideal_grades.join(', ')+'</p><p>'+esc(L.actual)+' '+o.score.dcg.toFixed(4)+' ÷ '+esc(L.ideal)+' '+o.score.idcg.toFixed(4)+' × 100 = '+(o.score.ndcg*100).toFixed(1)+'</p></details></div>';
const criteria=c=>{const x=c.criteria;return '<div class="compare-criteria" data-task-criteria><strong>'+esc(L.criteria)+'</strong>'+(c.quality_excluded?'<p data-quality-excluded>'+esc(L.quarantined)+'</p>':'')+(x.answer!=null?'<p>'+esc(L.referenceAnswer)+': <b>'+esc(x.answer)+'</b></p>':'')+(x.urls.length?'<p>'+esc(L.referenceUrls)+'</p>'+x.urls.map(u=>'<a href="'+esc(u)+'" target="_blank" rel="noopener noreferrer">'+esc(u)+'</a>').join(''):'')+(x.official_sources&&Object.keys(x.official_sources).length?'<details><summary>'+esc(L.sourceRules)+'</summary><p>'+[...(x.official_sources.domains||[]),...(x.official_sources.url_prefixes||[])].map(esc).join('<br>')+'</p>'+x.official_sources.sources.map(u=>'<a href="'+esc(u)+'" target="_blank" rel="noopener noreferrer">'+esc(u)+'</a>').join('')+'</details>':'')+(x.domains.length?'<p>'+esc(L.requestedSites)+': '+x.domains.map(esc).join(', ')+'</p>':'')+((x.paper_ids.length||x.repositories.length||Object.keys(x.aliases).length)?'<details><summary>'+esc(L.equivalents)+'</summary><pre>'+esc(JSON.stringify({aliases:x.aliases,papers:x.paper_ids,repositories:x.repositories},null,2))+'</pre></details>':'')+(!x.urls.length&&x.answer==null&&!x.domains.length?'<p>'+esc(L.unannotated)+'</p>':'')+'</div>'};
function matching(){let q=$('[data-output-query]').value.toLowerCase(),benchmark=$('[data-output-benchmark]').value,check=$('[data-output-check]').value;return data.cases.filter(c=>(benchmark==='all'||(benchmark==='excluded'?c.quality_excluded:c.benchmark===benchmark&&!c.quality_excluded))&&(check==='all'||c.check_types.includes(check))&&(!focusedId||c.id===focusedId)&&(!q||c.query.toLowerCase().includes(q)||c.id.toLowerCase().includes(q)))}
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
    $('[data-output-pages]').innerHTML=matches.slice(page*size,(page+1)*size).map(c=>'<article class="compare-task"><h3>'+esc(c.query)+'</h3>'+criteria(c)+'<div class="compare-columns" style="--method-count:'+data.methods.length+'">'+data.methods.map(m=>{let o=c.outputs[m.id];return '<section class="compare-method"><h4>'+esc(m.label)+'</h4>'+(o?scoreSummary(o)+'<p class="compare-status">'+esc(L.results)+': '+o.count+' · '+esc(verdict(c,o,metric))+'</p><a href="'+esc(o.detail)+'">'+esc(L.full)+'</a>'+(!o.items.length?'<p>'+esc(o.error||L.empty)+'</p>':o.items.map((x,i)=>'<div class="compare-result '+(c.quality_excluded||x.contamination_excluded?'':x.grade!=null?(x.grade>=2?'compare-match':''):(x.source_match||x.site_match?'compare-match':''))+'">'+(x.grade!=null?'<p class="grade-badge grade-'+x.grade+'">'+esc(L.gradeNames[x.grade])+' · '+esc(L.gradeSources[x.grade_source]||x.grade_source)+'</p>':'')+(x.grade_reason?'<details><summary>'+esc(L.gradeReason)+'</summary><p>'+esc(x.grade_reason)+'</p><blockquote>'+esc(x.grade_evidence)+'</blockquote></details>':'')+(x.contamination_excluded?'<p class="compare-status">'+esc(L.contaminated)+'</p>':'')+(x.source_match?'<span class="compare-match-note">'+esc(L.sourceItem)+'</span>':'')+(x.site_match?'<span class="compare-match-note">'+esc(L.siteItem)+'</span>':'')+field(x,'title','<a href="'+esc(x.url)+'" target="_blank" rel="noopener noreferrer">'+(i+1)+'. '+esc(x.title||x.url||L.untitled)+'</a>')+'<p class="compare-url">'+esc(x.url)+'</p>'+field(x,'snippet',esc(x.snippet))+['summary','content'].filter(f=>x[f]).map(f=>'<details><summary>'+esc(L[f])+'</summary>'+field(x,f,esc(x[f]))+(f==='content'&&x.answer_outside_preview?'<p>'+esc(L.outsidePreview)+'</p>':'')+'</details>').join('')+'</div>').join('')):'<p>'+esc(L.missing)+'</p>')+'</section>'}).join('')+'</div></article>').join('')||'<p>'+esc(L.none)+'</p>';
    $('[data-output-page]').textContent=(matches.length?page+1:0)+' / '+Math.ceil(matches.length/size)+' · '+matches.length+' '+L.tasks;
    $('[data-output-prev]').disabled=page===0;$('[data-output-next]').disabled=(page+1)*size>=matches.length;
}
root.querySelectorAll('select,input').forEach(x=>x.addEventListener(x.matches('[data-output-query]')?'input':'change',()=>{page=0;relationPage=0;focusedId=null;render()}));
$('[data-relation-prev]').onclick=()=>{relationPage--;render()};$('[data-relation-next]').onclick=()=>{relationPage++;render()};
$('[data-output-prev]').onclick=()=>{page--;render()};$('[data-output-next]').onclick=()=>{page++;render()};render();
})();
"""


def render_comparison(profile, selected_rows, zh, detail_link=None, relevance=None, *, primary_factual_metric="answer"):
    """Render both results-analysis modules; embed once per results page."""
    t = partial(report_text, zh=zh)
    data = comparison_data(profile, selected_rows, zh, detail_link, relevance, primary_factual_metric=primary_factual_metric)
    cutoff = data["cutoff"]
    e = partial(esc, none="None")
    items = [item for case in data["cases"] for output in case["outputs"].values()
             if output for item in output["items"]]
    delivered_fields = t("Each column shows the first five results with their returned titles and snippets.")
    if any(item.get("summary") for item in items):
        delivered_fields += t(" Page summaries are shown in full.")
    if any(item.get("content") for item in items):
        delivered_fields += t(" Page bodies are shown as excerpts.")
    labels = {"relations": dict(zip(
        ("all_pass", "baseline_only", "comparison_only", "all_fail", "mixed", "unknown"),
        (t("All selected methods hit"), t("Only baseline hits"),
         t("Only comparison methods hit"), t("None hit"),
         t("Comparison methods disagree"), t("No comparable score")), strict=True)),
        "query": t("Task / query"), "none": t("No matching tasks"), "unknown": t("Unscored"),
        "hit": t("Found"), "miss": t("Not found"),
        "criteria": t("What this question checks"),
        "sourceRules": t("Allowed official sources and verification links"),
        "quarantined": t("Reference quarantined; excluded from quality"),
        "contaminated": t("Benchmark record; excluded from quality"),
        "referenceAnswer": t("Dataset answer"),
        "referenceUrls": t("Reference URLs: compare the first {cutoff} result URLs", cutoff=cutoff),
        "requestedSites": t("Required websites"),
        "equivalents": t("Accepted equivalent URLs and identifiers"),
        "unannotated": t("Relevance annotation is missing; a nonempty response only establishes availability."),
        "siteItem": t("Matches required website"),
        "grade": t("Relevance grade"),
        "gradeNames": [t("0 · Irrelevant"), t("1 · Topic only"), t("2 · Partly useful"), t("3 · Directly useful")],
        "qualityScore": t("Ranking quality"), "usefulCount": t("Useful results"),
        "calculation": t("Inspect score calculation"),
        "notReturned": t("Not returned"), "duplicate": t("Duplicate: no extra credit"),
        "zeroPool": t("No relevant candidate in the pool: score zero."),
        "rank": t("Rank", context="original"), "weightedGain": t("Rank contribution"),
        "actual": t("Actual weighted sum"), "ideal": t("Pooled ideal weighted sum"),
        "idealGrades": t("Ideal top-{cutoff} grades", cutoff=cutoff),
        "direct": t("Direct match found"), "noDirect": t("No direct match"),
        "gradeSources": {"human": t("Human annotation"), "model": t("Model grading"), "source-rule": t("Source requirement"), "source_policy": t("Source requirement")},
        "gradeReason": t("Scoring reason and quoted evidence"),
        "verdicts": {"quality": [t("Relevant result found"), t("No relevant result in top {cutoff}", cutoff=cutoff)], "source": [t("Reference URL found"), t("Reference URL absent from top {cutoff}", cutoff=cutoff)],
                     "answer": [t("Answer text found"), t("Answer text absent")],
                     "site": [t("Required website found"), t("Required website absent")],
                     "availability": [t("Search results returned"), t("Empty response")]},
        "results": t("Returned results", context="count"), "full": t("Open full response and evidence"),
        "empty": t("No results returned"), "missing": t("No measured result"),
        "untitled": t("Untitled result"), "summary": t("Full page summary"),
        "content": t("Page body excerpt"), "sourceItem": t("Reference page"),
        "outsidePreview": t("The answer text occurs later in the full body; use the full response link above."),
        "answerField": t("contains answer cue"), "tasks": t("tasks")}
    options = lambda values: ''.join(f'<option value="{e(k)}">{e(v)}</option>' for k, v in values)
    methods = [(m["id"], m["label"]) for m in data["methods"]]
    populations = sorted({case["benchmark"] for case in data["cases"]})
    names = {"custom": t("In-house scenarios"), "factual": t("Public factual questions")}
    for group in profile.get('report_cohorts', []):
        label = group.get('label', group['id'])
        names[group['id']] = label.get('zh' if zh else 'en', group['id']) if isinstance(label, dict) else label
    default_group = next((case['benchmark'] for case in data['cases'] if 'factual' not in case['check_types']), 'all')
    benchmark_options = [("all", t("All saved responses · {cases}", cases=len(data['cases'])))] + [
        (key, names.get(key, key) + " · " + str(sum(c["benchmark"] == key and not c["quality_excluded"] for c in data["cases"]))) for key in populations
    ]
    excluded_count = sum(c["quality_excluded"] for c in data["cases"])
    if excluded_count:
        benchmark_options.append(("excluded", t("Reference issues · {excluded_count}", excluded_count=excluded_count)))
    checks = {k for case in data["cases"] for k in case["check_types"]}
    available_metrics = {'primary', 'availability'}
    descriptions = []
    if any(case['has_relevance'] for case in data['cases']):
        available_metrics.add('quality')
        descriptions.append(t("Relevance uses the frozen 0–3 labels."))
    if any(case['criteria']['urls'] for case in data['cases']):
        available_metrics.add('source')
        descriptions.append(t("Source checks match the supplied reference URLs."))
    if 'factual' in checks:
        available_metrics.add('answer')
        descriptions.append(t("Answer checks match published answer text and exclude benchmark records."))
    if any(case['criteria']['domains'] for case in data['cases']):
        available_metrics.add('site')
    if not descriptions:
        descriptions.append(t("These tasks have no relevance or answer labels; only response availability is measured."))
    metric_options = [
        ('primary', t("Primary check for each task type")),
        ('quality', t("Relevance · grade at least 2")),
        ('source', t("Reference target / published source")),
        ('answer', t("Answer cue in delivered text · contamination excluded")),
        ('site', t("Requested-site constraint")),
        ('availability', t("Nonempty response")),
    ]
    check_options = [("all", t("All task types"))] + [(key, label) for key, label in (("target", t("Reference-page discovery")), ("site", t("Requested-site constraint")), ("availability", t("Open discovery availability")), ("factual", t("Factual evidence"))) if key in checks]
    return ('<style>' + STYLE + '</style><section class="search-compare" data-search-comparison data-labels="' + e(json.dumps(labels, ensure_ascii=False)) + '">'
        + '<div class="compare-controls"><label>' + t("Find a query") + '<input type="search" data-output-query></label><label>' + t("Benchmark") + '<select data-output-benchmark>' + options(benchmark_options).replace('<option value="' + e(default_group) + '">', '<option value="' + e(default_group) + '" selected>') + '</select></label><label>' + t("Task check") + '<select data-output-check>' + options(check_options) + '</select></label></div>'
        + '<h2>' + t("Compare methods on the same tasks") + '</h2><p class="compare-notice">'
        + ' '.join(descriptions)
        + '</p><div class="compare-controls"><label>' + t("Check", context="item") + '<select data-compare-metric>' + options([(key, label) for key, label in metric_options if key in available_metrics]) + '</select></label><label>' + t("Baseline") + '<select data-compare-baseline>' + options(methods) + '</select></label></div><fieldset><legend>' + t("Compare with") + '</legend><div class="compare-checks">'
        + ''.join('<label><input type="checkbox" data-compare-method checked value="' + e(k) + '">' + e(v) + '</label>' for k,v in methods)
        + '</div></fieldset><div class="compare-counts" data-comparison-counts aria-live="polite"></div><label>' + t("Show tasks where") + '<select data-compare-relation>' + options([('all', t("All outcome relations"))] + list(labels['relations'].items())) + '</select></label><div class="compare-relations" data-relation-tasks></div><div class="compare-pager"><button data-relation-prev>' + t("Previous") + '</button><span data-relation-page aria-live="polite"></span><button data-relation-next>' + t("Next") + '</button></div>'
        + '<h2>' + t("Actual outputs, side by side") + '</h2><p class="compare-notice">' + t("The filters above also control this view. ") + delivered_fields + t(" Green marks identify the matched fields or useful results. Relevance grades and expandable score calculations are shown where available.") + '</p><div data-output-pages></div><div class="compare-pager"><button data-output-prev>' + t("Previous") + '</button><span data-output-page aria-live="polite"></span><button data-output-next>' + t("Next") + '</button></div>'
        + '<script type="application/json" data-comparison-data>' + json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026') + '</script></section><script>' + SCRIPT + '</script>')
