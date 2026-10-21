"""Small bilingual Web Search report built from frozen calls, never invented scores."""

import argparse
import copy
import html
import json
import posixpath
import re
from collections import Counter
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from harness.web_fetch.config import ROOT

from .core import (
    aggregate,
    canonical_url,
    classify,
    digest,
    evaluate,
    in_domain,
    normalize,
    request_cost,
    validate_profile,
    verify_bundle,
    write_json,
)
from .evidence_quality import factual_sensitivity, CONTAMINATION_RULE
from .diagnostics import operational
from .runner import reference_available, reference_changes
from .scoring import apply_identity_correction
from .relevance import score as score_relevance, summary as relevance_summary, verify_provenance

STYLE = """
:root{color-scheme:light dark;--bg:#f7f8fa;--fg:#18252e;--panel:white;--line:#d9e0e5;--muted:#53646d;--win:#08704d}
@media(prefers-color-scheme:dark){:root{--bg:#11191f;--fg:#e4edf3;--panel:#19252e;--line:#34454f;--muted:#acbec9;--win:#79dbaf}}
body.dark{--bg:#11191f;--fg:#e4edf3;--panel:#19252e;--line:#34454f;--muted:#acbec9;--win:#79dbaf}
body.light{--bg:#f7f8fa;--fg:#18252e;--panel:white;--line:#d9e0e5;--muted:#53646d;--win:#08704d}
*{box-sizing:border-box}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.65 system-ui,sans-serif}
main,nav{max-width:1180px;margin:auto;padding:20px}main{overflow-wrap:anywhere}nav{display:flex;gap:22px;flex-wrap:wrap;border-bottom:1px solid var(--line)}
a{color:inherit;text-underline-offset:3px}h1{font-size:clamp(28px,4vw,36px);line-height:1.2}h2{margin-top:32px;font-size:24px}
select,input{max-width:100%}
.notice{border-left:4px solid #b77c1c;padding:14px 20px;background:var(--panel)}.muted{color:var(--muted)}
.scroll{overflow:auto;border:1px solid var(--line);border-radius:8px;margin:20px 0}.inventory{max-height:520px}
table{border-collapse:collapse;min-width:720px;width:100%;background:var(--panel)}th,td{padding:12px 16px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}th{white-space:nowrap;position:sticky;top:0;background:var(--panel)}
table.compact{min-width:0}table.compact td{min-width:70px}td{min-width:110px;max-width:420px;overflow-wrap:anywhere}.win{color:var(--win);font-weight:700}button,input,select{font:inherit;padding:7px 12px;background:var(--panel);color:var(--fg);border:1px solid var(--line);border-radius:5px}
[data-row][data-state="pass"] td:nth-child(4){color:var(--win);font-weight:700}
pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--panel);padding:20px;border:1px solid var(--line)}dialog{max-width:min(900px,95vw);max-height:85vh;background:var(--bg);color:var(--fg);border:1px solid var(--line)}
.specimen{border:1px solid var(--line);padding:20px;border-radius:8px;background:var(--panel)}.flow{display:flex;gap:12px;flex-wrap:wrap}.flow span{padding:15px;background:var(--panel);border:1px solid var(--line)}
.bar{height:22px;background:#648caf;border-radius:3px;min-width:2px}.timeline{position:relative;height:24px;background:var(--bg);min-width:220px}.timeline i{position:absolute;height:18px;top:3px;background:#648caf;border-radius:3px}.timeline i.good{background:#29976d}.chapter{border-top:1px solid var(--line);margin-top:36px;padding-top:10px}.eyebrow{color:var(--muted)}.lede{font-size:19px;max-width:900px}
.process{padding:18px;background:var(--panel);border:1px solid var(--line);border-radius:10px}.process .mermaid{background:white;color:#18252e;overflow:auto}.process svg{max-width:100%;height:auto}.process .flow-fallback{display:flex;gap:10px;flex-wrap:wrap}.process .flow-fallback span{padding:14px;border:1px solid var(--line);flex:1;min-width:150px}.specimen blockquote{margin:10px 0;padding-left:16px;border-left:3px solid var(--line)}.chapter p{max-width:960px}.chapter h3{margin-top:24px}.field-value{white-space:pre-wrap;max-height:250px;overflow:auto}
.capmap{display:grid;grid-template-columns:minmax(135px,1.2fr) repeat(5,minmax(90px,1fr));gap:6px;margin:18px 0}.capmap>*{padding:12px 8px;border-radius:6px;background:var(--panel);text-align:center}.capmap .yes{background:#d7f0e5;color:#124b36;font-weight:700}.capmap .no{background:#eceff3;color:#52606c}.capmap .name{text-align:left;font-weight:700}.capmap .head{font-size:13px;font-weight:700}@media(max-width:700px){.capmap{grid-template-columns:minmax(110px,1.2fr) repeat(5,minmax(70px,1fr));font-size:12px;overflow:auto}.capmap>*{padding:8px 4px}}
.metric-bars{display:grid;gap:12px;margin:18px 0;max-width:850px}.metric-bars .item{display:grid;grid-template-columns:150px minmax(120px,1fr) 195px;gap:12px;align-items:center}.metric-bars .track{height:22px;border-radius:4px;background:var(--line);overflow:hidden}.metric-bars .fill{display:block;height:100%;background:#2c8767}.metric-bars small{color:var(--muted)}@media(max-width:700px){.metric-bars .item{grid-template-columns:1fr 1fr}.metric-bars .track{grid-column:1/-1;grid-row:2}.metric-bars small{text-align:right}}
.chapter-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:28px 0}.chapter-link{display:grid;gap:8px;padding:22px;border:1px solid var(--line);border-radius:12px;background:var(--panel);text-decoration:none}.chapter-link span{color:var(--muted)}[data-population]{margin:28px 0;padding:20px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}[data-population] h3{margin:0}[data-population] .scroll{margin-bottom:0}.population-note{max-width:850px;color:var(--muted)}
@media(max-width:600px){.chapter-links{grid-template-columns:1fr}[data-population]{padding:12px}main,nav{padding:16px}nav{gap:14px}td,th{padding:10px}.flow{flex-direction:column}}
"""
SCRIPT = """
document.querySelector('[data-theme]').onclick=()=>{document.body.className=document.body.className==='dark'?'light':'dark'};
document.querySelectorAll('[data-sort-column]').forEach(b=>b.onclick=()=>{let t=b.closest('table'),i=+b.dataset.sortColumn,asc=b.parentElement.getAttribute('aria-sort')!=='ascending';t.querySelectorAll('th').forEach(x=>x.removeAttribute('aria-sort'));b.parentElement.setAttribute('aria-sort',asc?'ascending':'descending');let rows=[...t.tBodies[0].rows];rows.sort((a,b)=>{let x=a.cells[i].dataset.value,y=b.cells[i].dataset.value;if(x==='')return y===''?0:1;if(y==='')return -1;return (Number(x)-Number(y))*(asc?1:-1)});rows.forEach(r=>t.tBodies[0].append(r))});
let pf=document.querySelector('[data-population-filter]');let f=document.querySelector('[data-filter]'),q=document.querySelector('[data-query]'),g=document.querySelector('[data-cohort-filter]'),o=document.querySelector('[data-origin-filter]'),m=document.querySelector('[data-method-filter]');function filter(){document.querySelectorAll('[data-row]').forEach(r=>{r.hidden=(pf&&pf.value!=='all'&&!JSON.parse(pf.selectedOptions[0].dataset.caseIds||'[]').includes(r.dataset.caseId))||(f&&f.value!=='all'&&r.dataset.state!==f.value)||(g&&g.value!=='all'&&r.dataset.cohort!==g.value)||(o&&o.value!=='all'&&r.dataset.origin!==o.value)||(m&&m.value!=='all'&&r.dataset.method!==m.value)||(q&&!r.textContent.toLowerCase().includes(q.value.toLowerCase()))})}if(pf)pf.onchange=filter;if(f)f.onchange=filter;if(q)q.oninput=filter;if(g)g.onchange=filter;if(o)o.onchange=filter;if(m)m.onchange=filter;filter();
document.querySelectorAll('[data-evidence]').forEach(b=>b.onclick=async()=>{let d=document.querySelector('dialog');d.querySelector('a').hidden=true;d.querySelector('pre').textContent='…';d.showModal();try{let r=await fetch(b.dataset.evidence);if(!r.ok)throw Error('HTTP '+r.status);let text=await r.text();try{let v=JSON.parse(text);if(b.dataset.result!==undefined)v=v.results[Number(b.dataset.result)];text=JSON.stringify(v,null,2)}catch{}d.querySelector('pre').textContent=text}catch(e){d.querySelector('pre').textContent=String(e)}});
document.querySelectorAll('[data-diagnosis]').forEach(b=>b.onclick=()=>{let d=document.querySelector('dialog');d.querySelector('pre').textContent=b.dataset.diagnosis;let a=d.querySelector('a');a.hidden=false;a.href=b.dataset.detail;d.showModal()});
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());
if(document.querySelector('.mermaid')&&innerWidth>700){let s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js';s.integrity='sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I';s.crossOrigin='anonymous';s.onload=async()=>{mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'neutral'});for(let [i,n] of [...document.querySelectorAll('.mermaid')].entries()){try{let r=await mermaid.render('search-flow-'+i,n.textContent);n.innerHTML=r.svg;n.hidden=false;n.previousElementSibling.hidden=true}catch{}}};document.head.appendChild(s)}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    return "—" if value is None else f"{value:,.2f}"


def table(headers, rows, sortable=False):
    head = "".join(
        f'<th><button data-sort-column="{i}">{esc(h)} ↕</button></th>'
        if sortable and i
        else f"<th>{esc(h)}</th>"
        for i, h in enumerate(headers)
    )
    return (
        '<div class="scroll" tabindex="0"><table'
        + (' class="compact"' if len(headers) == 2 else "")
        + "><thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def cells(values):
    return "<tr>" + "".join("<td>" + v + "</td>" for v in values) + "</tr>"


def public_calls(profile, rows, source, sources):
    """Keep measured outcomes; redact URL credentials and audit billing corrections."""
    rows = copy.deepcopy(rows)
    methods = {m["id"]: m for m in profile["methods"]}
    files, redactions, corrections = {}, [], []
    for row in rows:
        attempts = row.get("attempts", [])
        final_path = row["evidence"]["path"]
        for attempt in attempts or [row]:
            binding = attempt["evidence"]
            raw = (sources.get(row.get("cohort"), source) / binding["path"]).read_bytes()
            public = re.sub(rb'(https?://)[^/\s"<>@]+@', rb'\1REDACTED:REDACTED@', raw)
            if public != raw:
                redactions.append({"path": binding["path"], "original_sha256": digest(raw),
                                   "public_sha256": digest(public), "rule": "URL userinfo redacted; outcome unchanged"})
                binding["sha256"] = digest(public)
            files[binding["path"]] = public
            try:
                payload = json.loads(raw)
            except ValueError:
                payload = None
            cost = request_cost(methods[row["method"]], attempt["http_status"], attempt.get("transport_exit", 0), payload)
            if cost != attempt["cost"]:
                corrections.append({"path": binding["path"], "original_cost": attempt["cost"], "cost": cost,
                                    "rule": "Provider nonempty billing does not depend on local URL validation"})
                attempt["cost"] = cost
        if attempts:
            row["evidence"] = copy.deepcopy(attempts[-1]["evidence"])
            assert row["evidence"]["path"] == final_path
            row["cost"] = sum(a["cost"] for a in attempts) if all(a["cost"] is not None for a in attempts) else None
    return rows, files, redactions, corrections


def load_run(source):
    profile = json.loads((source / "profile.json").read_text())
    validate_profile(profile)
    receipt = json.loads((source / "receipt.json").read_text())
    if digest((source / "profile.json").read_bytes()) != receipt["profile_sha256"]:
        raise ValueError("Profile hash mismatch")
    raw = (source / "results.jsonl").read_bytes()
    if digest(raw) != receipt["results_sha256"]:
        raise ValueError("Results hash mismatch")
    rows = [json.loads(line) for line in raw.splitlines()]
    expected = {
        (c["id"], m["id"], r)
        for c in profile["cases"]
        for m in profile["methods"]
        for r in range(profile["protocol"]["rounds"])
    }
    actual = [(r["case_id"], r["method"], r["round"]) for r in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("Incomplete or duplicate call matrix")
    if not receipt.get("complete") or receipt["call_count"] != len(rows):
        raise ValueError("Incomplete receipt")
    if "physical_call_count" in receipt and receipt["physical_call_count"] != sum(
        len(r.get("attempts", [r["evidence"]])) for r in rows
    ):
        raise ValueError("Physical call count mismatch")
    verify_bundle(
        source,
        [r["evidence"] for r in rows]
        + [a["evidence"] for r in rows for a in r.get("attempts", [])]
        + receipt.get("bindings", []),
    )
    # Preserve the frozen receipt, but expose incomplete historical reference transfers.
    receipt = dict(receipt)
    receipt["reference_capture_issues"] = []
    for phase in ("before", "after"):
        reference_file = source / f"references-{phase}.json"
        if reference_file.exists():
            for url, capture in json.loads(reference_file.read_text()).items():
                if profile["protocol"].get("reference_scope") == "identity":
                    actual_available = reference_available(
                        profile, url, capture, (source / capture["path"]).read_bytes()
                    )
                    if actual_available != capture["available"]:
                        raise ValueError("Reference identity qualification mismatch")
                if capture.get("transport_exit") or capture.get("http_status") != 200:
                    receipt["reference_capture_issues"].append(
                        {
                            "phase": phase,
                            "url": url,
                            "transport_exit": capture.get("transport_exit"),
                        }
                    )
    if receipt.get("certified") and receipt["reference_capture_issues"]:
        raise ValueError("Certified source capture is incomplete")
    if receipt.get("certified") and profile["protocol"].get("reference_scope") == "identity":
        before = json.loads((source / "references-before.json").read_text())
        after = json.loads((source / "references-after.json").read_text())
        expected_refs = {u for c in profile["cases"] for u in c.get("anchors", [])}
        expected_refs.update(profile.get("reference_artifacts", {}))
        if (
            set(before) != expected_refs
            or set(after) != expected_refs
            or reference_changes(profile, before, after)
        ):
            raise ValueError("Incomplete or failed identity reference scope")
    bycase = {c["id"]: c for c in profile["cases"]}
    for r in rows:
        if r["id"] != f"{r['case_id']}-{r['method']}-r{r['round']}":
            raise ValueError("Unsafe call identity")
        attempts = r.get("attempts")
        if attempts is not None:
            if not 1 <= len(attempts) <= 1 + int(bool(profile["protocol"].get("retry"))):
                raise ValueError("Invalid retry count")
            if attempts[-1]["evidence"] != r["evidence"]:
                raise ValueError("Final response must determine the result")
            if r["elapsed_ms"] + 1 < sum(a["elapsed_ms"] for a in attempts):
                raise ValueError("User-visible latency excludes a physical call")
            if all(a["cost"] is not None for a in attempts):
                if abs(r["cost"] - sum(a["cost"] for a in attempts)) > 1e-8:
                    raise ValueError("Retry cost mismatch")
        if not r["error"] and r["http_status"] == 200:
            normalized = normalize(json.loads((source / r["evidence"]["path"]).read_bytes()))
            if normalized != r["results"]:
                raise ValueError("Normalized response mismatch")
        if evaluate(bycase[r["case_id"]], r["results"]) != r["metrics"]:
            raise ValueError("Stored score mismatch")
    return profile, rows, receipt


def _execution_contract(protocol):
    """Fields that must match for same-budget cross-cohort comparisons."""
    return {key: protocol.get(key) for key in (
        "top_k", "rounds", "score_round", "concurrency", "server_timeout_ms",
        "client_timeout_s", "interval_s", "retry", "order", "as_of",
    )}


def load_report_runs(source, cohorts, labels, method_ids=None):
    """Compose separately verified runs; never pretend they were one collection."""
    inputs = [("primary", source), *cohorts]
    if len({name for name, _ in inputs}) != len(inputs):
        raise ValueError("Duplicate cohort name")
    profile = None
    rows, receipts, groups, paths = [], [], [], {}
    seen_cases, seen_evidence = set(), set()
    for name, path in inputs:
        if not name.replace("-", "").isalnum():
            raise ValueError("Unsafe cohort name")
        current, calls, receipt = load_run(path)
        if method_ids is not None:
            methods = {method["id"]: method for method in current["methods"]}
            if not method_ids or not set(method_ids).issubset(methods):
                raise ValueError("Cohort does not contain every comparison participant")
            current = copy.deepcopy(current)
            current["methods"] = [methods[method_id] for method_id in method_ids]
            calls = [row for row in calls if row["method"] in method_ids]
        if profile is None:
            profile = copy.deepcopy(current)
            profile["cases"] = []
        elif current["methods"] != profile["methods"] or _execution_contract(current["protocol"]) != _execution_contract(profile["protocol"]):
            raise ValueError("Cohort participants or execution protocols differ")
        case_ids = {c["id"] for c in current["cases"]}
        evidence = {r["evidence"]["path"] for r in calls}
        if seen_cases & case_ids or seen_evidence & evidence:
            raise ValueError("Overlapping cohort case or evidence identities")
        seen_cases.update(case_ids)
        seen_evidence.update(evidence)
        profile["cases"].extend(dict(c, cohort=name) for c in current["cases"])
        profile.setdefault("reference_checks", {}).update(current.get("reference_checks", {}))
        profile.setdefault("reference_artifacts", {}).update(current.get("reference_artifacts", {}))
        rows.extend(dict(r, cohort=name) for r in calls)
        groups.append(
            {
                "id": name,
                "label": labels.get(name, {"en": name, "zh": name}),
                "case_ids": sorted(case_ids),
                "protocol": current["protocol"],
            }
        )
        receipts.append(
            {
                "cohort": name,
                "profile_sha256": digest((path / "profile.json").read_bytes()),
                "results_sha256": digest((path / "results.jsonl").read_bytes()),
                "receipt_sha256": digest((path / "receipt.json").read_bytes()),
                "collection_receipt": receipt,
            }
        )
        paths[name] = path
    profile["report_cohorts"] = groups
    if any(group["protocol"].get("preserve_publisher_queries") for group in groups):
        profile["protocol"]["preserve_publisher_queries"] = True
    # A batch-specific prose description is not the combined report's population.
    # Frozen source profiles remain bound separately by their original hashes.
    profile["protocol"].pop("comparison", None)
    validate_profile(profile)
    return (
        profile,
        rows,
        {
            "certified": all(r["collection_receipt"].get("certified") for r in receipts),
            "synthetic": all(r["collection_receipt"].get("synthetic") for r in receipts),
            "complete": True,
            "call_count": len(rows),
            "separate_collections": receipts,
        },
        paths,
    )


def build(
    output,
    *,
    profile_path=None,
    source=None,
    content=None,
    language="zh",
    include_english=False,
    correction=None,
    relevance=None,
    site_prefix="",
    cohorts=(),
):
    if site_prefix and ("/" in site_prefix or not site_prefix.replace("-", "").isalnum()):
        raise ValueError("Site prefix must be one safe directory name")
    authored = json.loads(content.read_text()) if content else {"sections": []}
    reading_sections = authored.get("reading_sections", [])
    if len({p["key"] for p in reading_sections}) != len(reading_sections) or any(
        not p["key"].replace("-", "").isalnum() for p in reading_sections
    ):
        raise ValueError("Unsafe or overlapping reading section")
    sources = {}
    if cohorts:
        if not source:
            raise ValueError("Additional cohorts require a primary source")
        profile, rows, receipt, sources = load_report_runs(
            source, cohorts, authored.get("cohort_labels", {}), authored.get("comparison_methods")
        )
    elif source:
        profile, rows, receipt = load_run(source)
    else:
        profile = json.loads(profile_path.read_text())
        validate_profile(profile)
        rows = []
        receipt = {"certified": False}
    collection_profile = profile
    rows, public_files, redactions, billing_corrections = public_calls(profile, rows, source, sources)
    scoring_revision = None
    if correction:
        revision = json.loads(correction.read_text())
        profile, rows, changes = apply_identity_correction(profile, rows, revision)
        scoring_revision = {
            "contract": revision,
            "changes": changes,
            "sha256": digest(correction.read_bytes()),
        }
    assessment_revision = None
    if authored.get("assessment_revision"):
        from .scoring import apply_report_revision
        profile, rows, changes = apply_report_revision(profile, rows, authored["assessment_revision"])
        assessment_revision = {"contract": authored["assessment_revision"], "changes": changes, "implementation_sha256": {name: digest((Path(__file__).parent / name).read_bytes()) for name in ("scoring.py", "core.py", "answer_rules.py")}}
    profile, rows = project_comparison(profile, rows, authored)
    materialized_paths = {e["path"] for r in rows for e in [r["evidence"], *[a["evidence"] for a in r.get("attempts", [])]]}
    redactions = [r for r in redactions if r["path"] in materialized_paths]
    billing_corrections = [r for r in billing_corrections if r["path"] in materialized_paths]
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    languages = ["zh", "en"] if include_english or language == "en" else ["zh"]
    routes = []
    selected = [r for r in rows if r["round"] == profile["protocol"]["score_round"]]
    relevance_data = json.loads(relevance.read_text()) if relevance else None
    if relevance_data:
        verify_provenance(profile, selected, relevance_data)
    relevance_scores = (
        score_relevance(profile, selected, relevance_data["plan"], relevance_data["labels"])
        if relevance_data else None
    )
    overview_methods = scoped_overview_methods(profile, authored)
    overview_rows = scoped_overview_rows(profile, rows, authored)
    summary = aggregate([r for r in overview_rows if r["round"] == profile["protocol"]["score_round"]])
    physical = aggregate(overview_rows)
    payloads = {}
    for r in rows:
        try:
            payloads[r["id"]] = json.loads(
                public_files[r["evidence"]["path"]]
            )
        except ValueError:
            payloads[r["id"]] = {}
    diagnostics = operational(rows, payloads)
    bundle = []
    factual_cases = {c["id"]: c for c in profile["cases"] if "reference_answer" in c}
    if factual_cases:
        sensitivity = {r["id"]: factual_sensitivity(factual_cases[r["case_id"]], r["results"]) for r in selected if r["case_id"] in factual_cases}
        write_json(output / "factual-sensitivity.json", {"schema": "factual-sensitivity/1", "timing": "post-hoc diagnostic; no recollection or rank compaction", "rule": CONTAMINATION_RULE, "results": sensitivity})
        bundle.append({"path": "factual-sensitivity.json", "sha256": digest((output / "factual-sensitivity.json").read_bytes())})
    if relevance_data:
        target = output / "relevance-judgments.json"
        target.write_bytes(relevance.read_bytes())
        bundle.append({"path": target.name, "sha256": digest(target.read_bytes())})
    notice = ROOT / "evaluation/web-search/NOTICE.txt"
    notice_target = output / "query-catalog-license.txt"
    notice_target.write_bytes(notice.read_bytes())
    bundle.append({"path": notice_target.name, "sha256": digest(notice.read_bytes())})
    for asset in authored.get("assets", []):
        verify_bundle(content.parent, [{"path": asset["source"], "sha256": asset["sha256"]}])
        target = output / asset["path"]
        if not target.resolve().is_relative_to(output):
            raise ValueError("Unsafe report asset destination")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((content.parent / asset["source"]).read_bytes())
        bundle.append({"path": asset["path"], "sha256": asset["sha256"]})
    if (output / "manifest.json").exists():
        old_manifest = json.loads((output / "manifest.json").read_text())
        for route in old_manifest["routes"]:
            old = (output / route).resolve()
            if old.is_relative_to(output) and old.is_file():
                old.unlink()
    for r in rows:
        for evidence in [r["evidence"], *[a["evidence"] for a in r.get("attempts", [])]]:
            if evidence in bundle:
                continue
            target = output / evidence["path"]
            target.parent.mkdir(exist_ok=True)
            target.write_bytes(public_files[evidence["path"]])
            bundle.append(evidence)
    for lang in languages:
        zh = lang == "zh"
        prefix = "" if lang == language else lang + "/"

        def t(en, cn, zh=zh):
            return cn if zh else en

        nav = [
            ("index.html", t("Overview", "概览")),
            ("dataset.html", t("Queries", "评测集")),
            ("results.html", t("Results", "结果")),
            ("interpretation.html", t("Findings", "结果解读")),
            ("performance.html", t("Latency diagnostics", "性能诊断")),
            ("research.html", t("Method & market", "方法与竞品")),
        ]
        if reading_sections and authored.get("presentation") != "comparison-report":
            nav = (
                [("index.html", t("Overview", "总览"))]
                + [("index.html#" + p["key"], p["nav"][lang]) for p in reading_sections]
                + [("interpretation.html", t("Report interpretation", "报告解读"))]
                + [entry for entry in nav[1:] if entry[0] != "interpretation.html"]
            )
        if authored.get("presentation") == "comparison-report":
            nav = [("index.html", t("Overview", "总览")),
                   ("capabilities.html", t("Interfaces", "接口")),
                   ("dataset.html", t("Two benchmarks", "两套评测")),
                   ("results.html", t("Results", "逐题结果")),
                   ("performance.html", t("Performance and cost", "性能与成本")),
                   ("research.html", t("Methods and FAQ", "方法与问答")),
                   ("interpretation.html", t("Decisions", "结论与决策"))]
        state = (
            t(
                "Measured API evaluation · automated target checks, no independent relevance review.",
                "API 实测 · 目标匹配由规则核验，未做独立相关性标注。",
            )
            if rows
            else t(
                "Evaluation design preview. No API performance ranking.",
                "评测设计预览，尚无 API 性能排名。",
            )
        )
        if rows and len(profile["methods"]) == 1:
            state = t(
                "Single-provider preflight; no cross-provider comparison.",
                "单接口预检；不构成两家接口对比。",
            )
        if receipt.get("synthetic"):
            state = t(
                "Synthetic fixture — not API measurements.",
                "合成示例，所有数字均为测试夹具，不是 API 实测。",
            )
        if rows and not receipt["certified"]:
            state += t(" Diagnostic evidence.", "诊断性证据。")
        if authored.get("reading_model") and rows and receipt.get("certified"):
            state = t(
                "Measured Search API responses and documented capabilities · each metric states its evidence scope.",
                "Search API 实测与公开规格核对 · 每项指标单独说明证据范围。",
            )

        def page(route, title, body, prefix=prefix, lang=lang, nav=nav, state=state, t=t):
            path = output / prefix / route
            path.parent.mkdir(parents=True, exist_ok=True)
            back = "../" if route.startswith("details/") else ""
            depth = "../" * (len(Path(prefix + route).parts) - 1 + bool(site_prefix))
            navigation = "".join(f'<a href="{back}{p}">{label}</a>' for p, label in nav)
            if len(languages) > 1:
                other = "zh" if lang == "en" else "en"
                otherprefix = "" if other == language else other + "/"
                language_depth = "../" * (len(Path(prefix + route).parts) - 1)
                navigation += f'<a href="{language_depth}{otherprefix}{route}">{"中文" if other == "zh" else "English"}</a>'
            navigation += f'<button data-theme>{t("Theme", "切换主题")}</button>'
            if site_prefix:
                navigation += f'<a href="{depth}index.html">{t("Main comparison", "主评测")}</a>'
            notice_html = f'<p class="notice" data-state>{state}</p>'
            if authored.get("presentation") == "comparison-report" and receipt.get("certified") and not receipt.get("synthetic"):
                notice_html = ''
            document = BeautifulSoup(
                f'<!doctype html><html lang="{lang}"><head><base href="{depth or "./"}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · {t("Web Search", "网页搜索")}</title><style>{STYLE}</style></head><body><nav>{navigation}</nav><main><p class="muted">{t("Browser Eval / Web Search", "浏览器评测 / 网页搜索")}</p><h1>{esc(title)}</h1>{notice_html}{body}</main><dialog aria-label="{t("Response evidence", "响应证据")}"><button data-close>{t("Close", "关闭")}</button><pre></pre><a hidden>{t("Full result", "完整结果")}</a></dialog><script>{SCRIPT}</script></body></html>',
                "html.parser",
            )
            # Artifact Site injects a root base on every nested route. Local pages use
            # the matching base above; every artifact-owned link names the root path.
            for node in document.select("[href],[data-evidence],[data-detail]"):
                if node.name == "base":
                    continue
                for attr in ("href", "data-evidence", "data-detail"):
                    value = node.get(attr)
                    if value and not value.startswith(("https://", "http://", "#")):
                        asset_paths = {a["path"] for a in authored.get("assets", [])}
                        if relevance_data:
                            asset_paths.add("relevance-judgments.json")
                        node[attr] = posixpath.normpath(
                            posixpath.join(
                                site_prefix,
                                "" if value in asset_paths else posixpath.dirname(prefix + route),
                                value,
                            )
                        )
            path.write_text(str(document))
            routes.append(prefix + route)

        hit_key = "anchor_hit_5" if profile["protocol"]["top_k"] == 5 else "anchor_hit_10"
        cutoff = profile["protocol"]["top_k"]
        headers = [
            t("Method", "方法"),
            t("Nonempty / queries", "非空／题数"),
            t("Mean ms", "平均 ms"),
            "P50 ms",
            "P95 ms",
            t(f"Known page@{cutoff}", f"目标页进前{cutoff}条"),
            t("Price / 1k", "千次价格"),
        ]
        summary = aggregate([r for r in overview_rows if r["round"] == profile["protocol"]["score_round"]])
        physical = aggregate(overview_rows)
        entries = []
        for m in overview_methods:
            s = summary.get(m["id"])
            p = physical.get(m["id"])
            a = p["all_latency"] if p else {}
            hit = s["metrics"][hit_key] if s else {"mean": None, "n": 0}
            entries.append(
                [
                    m["label"],
                    p["nonempty"] if p else None,
                    a.get("mean"),
                    a.get("p50"),
                    a.get("p95"),
                    hit["mean"],
                    m["price_per_1000"],
                ]
            )
        winners = {}
        for i in (2, 3, 4, 5):
            values = [e[i] for e in entries]
            if len(values) > 1 and all(v is not None for v in values):
                best = (max if i == 5 else min)(values)
                if values.count(best) == 1:
                    winners[i] = best
        core = []
        for entry, m in zip(entries, overview_methods, strict=True):
            td = []
            for i, value in enumerate(entry):
                label = esc(value) if i == 0 else fmt(value)
                if i == 1 and value is not None:
                    label = f"{value}/{physical[m['id']]['n']}"
                if i == 6:
                    label += " " + m["currency"]
                if i == 5 and value is not None:
                    label = f"{value * 100:.1f}% ({summary[m['id']]['metrics'][hit_key]['n']}题)" if zh else f"{value * 100:.1f}% ({summary[m['id']]['metrics'][hit_key]['n']} queries)"
                win = i in winners and value == winners[i]
                td.append(
                    f'<td data-value="{value if i and value is not None else ""}"'
                    + (' class="win"' if win else "")
                    + ">"
                    + ("★ " if win else "")
                    + label
                    + "</td>"
                )
            core.append("<tr>" + "".join(td) + "</tr>")
        summary = aggregate(selected)
        physical = aggregate(rows)
        body = table(headers, core, True)
        body += cohort_summary(profile, rows, zh)
        body += authored_sections(authored, "summary", lang)
        if receipt.get("reference_capture_issues"):
            body += (
                '<p class="notice">'
                + t(
                    f"{len(receipt['reference_capture_issues'])} reference captures have HTTP or transfer failures. This cohort remains diagnostic and cannot certify source-text quality.",
                    f"{len(receipt['reference_capture_issues'])} 条参考抓取记录存在 HTTP 或传输失败。本批仅供诊断，不能认证参考正文质量。",
                )
                + "</p>"
            )

        body += (
            "<p>"
            + t(
                "All-call latency includes failures and every declared round. P50 is the median; P95 uses nearest rank. Quality uses only the preselected first round. A known-page miss is not proof of irrelevant results.",
                "耗时包含失败和预定的所有轮次。P50 为中位数，P95 取最近秩百分位数。命中指标仅使用预定的第一轮；没命中已知目标页，不等于其他结果不相关。",
            )
            + "</p>"
        )
        body += (
            "<p>"
            + t(
                "Prices checked 2026-10-20. Lexmount pricing is unset. DeepSeek: CNY 5 per 1,000 nonempty successful requests. API charges exclude downstream model tokens and additional fetches.",
                "价格核对日期：2026-10-20。Lexmount 尚未确定价格。DeepSeek 每千次成功且非空请求 5 元。接口费用不包含下游模型 token 和额外抓取费用。",
            )
            + "</p>"
        )
        body += comparison_tables(profile, selected, rows, zh)
        body += (
            "<p>"
            + t(
                f"Collected methods: {len(profile['methods'])}. Declared rounds: {profile['protocol']['rounds']}.",
                f"本批接口数量：{len(profile['methods'])}；预定轮次：{profile['protocol']['rounds']}。",
            )
            + "</p>"
        )
        body += (
            "<h2>"
            + t("What is compared", "比较范围")
            + '</h2><div class="flow">'
            + "".join(
                "<span>" + v + "</span>"
                for v in [
                    t(
                        f"{len(profile['cases'])} frozen queries",
                        f"{len(profile['cases'])} 条固定查询",
                    ),
                    t(f"Same top-{cutoff} budget", f"统一前 {cutoff} 条预算"),
                    t("Serial API calls", "串行调用"),
                    t("Saved responses & metrics", "保留响应与指标"),
                ]
            )
            + "</div>"
        )
        body += (
            "<p>"
            + t(
                "Web, site-restricted and native vertical queries remain separate populations. DeepSeek receives site operators for vertical tasks; Lexmount receives its native source parameter. Both return their native text, without a generation model.",
                "通用网页、站点限定和垂类查询分别统计。垂类任务中，DeepSeek 使用 site 算子，Lexmount 使用原生 source 参数。保留各家原生文本，不接入回答模型。",
            )
            + "</p>"
        )
        if rows:
            body += latency_rounds(profile, rows, zh)
            body += "<h2>" + t("Successful-call latency", "非空成功响应耗时") + "</h2>"
            body += table(
                [t("Method", "方法"), "N", t("Mean ms", "平均耗时 ms"), "P50 ms", "P95 ms"],
                [
                    cells(
                        [
                            esc(m["label"]),
                            str(physical[m["id"]]["success_latency"]["n"]),
                            *[
                                fmt(physical[m["id"]]["success_latency"][k])
                                for k in ("mean", "p50", "p95")
                            ],
                        ]
                    )
                    for m in profile["methods"]
                ],
            )
        if authored.get("reading_model"):
            body = authored_sections(authored, "lead", lang, rows=selected)
            body += (
                "<h2>" + t("Measured baseline", "实测概况") + "</h2>"
                + "<p>" + esc(cohort_label(profile, authored.get("overview_cohort"), zh)) + "</p>"
            )
            overview_case_ids = {r["case_id"] for r in overview_rows}
            target_count = sum(bool(c.get("anchors") or c.get("paper_ids") or c.get("repositories"))
                               for c in profile["cases"] if c["id"] in overview_case_ids)
            body += '<p class="muted">' + t(
                f"Nonempty counts queries with at least one result. Mean, P50 (median) and P95 cover all {len(overview_case_ids)} queries in this table; 95% finish within the P95 time. Known page@{cutoff} checks whether a target URL appears in the first {cutoff} results, across {target_count} predeclared target-page queries.",
                f"“非空”指至少返回一条结果的题数。平均耗时、P50（中位数）和 P95 均统计本表全部 {len(overview_case_ids)} 题；95% 的题在 P95 所示时间内结束。“目标页进前{cutoff}条”只看事先指定了目标网页的 {target_count} 题，目标网址出现在前{cutoff}条即算命中。",
            ) + '</p>'
            body += table(headers, core, True)
            if profile["protocol"].get("retry"):
                body += '<p class="lede">' + t(
                    f"The same {len(profile['cases'])} queries went to every provider. Each query has one final result; transport, HTTP or invalid-response failures receive one retry. Latency covers the complete user-visible request, including any retry. Empty valid results are not retried.",
                    f"每家使用相同的 {len(profile['cases'])} 道题，每题只计一个最终结果。传输、HTTP 或响应格式失败时重试一次；耗时包含整次请求和可能的重试。正常返回空结果不重试。",
                ) + '</p>'
            body += authored_sections(authored, "summary", lang, rows=selected)
            for chapter in reading_sections:
                body += (
                    '<section id="'
                    + esc(chapter["key"])
                    + '" data-reading="'
                    + esc(chapter["key"])
                    + '"><h2>'
                    + esc(chapter["title"][lang])
                    + "</h2>"
                    + authored_sections(authored, chapter["key"] + "-lead", lang, rows=selected, heading_level=3)
                    + (capability_map(dict(profile, methods=overview_methods), selected, zh) if chapter["key"] == "capabilities" else "")
                    + (relevance_panel(profile, relevance_scores, relevance_data, zh) if chapter["key"] == "quality" and relevance_scores else "")
                    + (known_page_chart(dict(profile, methods=overview_methods), selected, zh) if chapter["key"] == "quality" else "")
                    + (factual_panel(profile, selected, zh) if chapter["key"] == "quality" else "")
                    + (mode_panel(profile, selected, zh) if chapter["key"] == "speed" else "")
                    + authored_sections(
                        authored, chapter["key"], lang, rows=selected, heading_level=3
                    )
                    + "</section>"
                )
        if authored.get("presentation") == "comparison-report":
            body = authored_sections(authored, "lead", lang, rows=selected)
            body += population_summary(profile, selected, authored, zh)
            body += authored_sections(authored, "summary", lang, rows=selected)
            body += '<div class="chapter-links">' + ''.join(
                '<a class="chapter-link" href="' + route + '"><strong>' + title + '</strong><span>' + text + '</span></a>'
                for route, title, text in [
                    ("capabilities.html", t("What each API returns", "各家交付什么"), t("Requests, fields and actual examples", "请求参数、返回字段与实际样例")),
                    ("quality.html", t("How useful are the results?", "结果有没有用"), t("Evidence checks with explicit populations", "分题组看证据命中与适用范围")),
                    ("results.html", t("Compare every response", "逐题并排比较"), t("Outcome comparison and paginated outputs", "通过情况对比与分页输出")),
                    ("research.html", t("How search is evaluated", "行业怎么评测搜索"), t("Benchmarks, labels and metric definitions", "公开基准、标注与指标解释")),
                ]) + '</div>'
            for chapter_key, chapter_title in [("capabilities", t("Interfaces and delivered content", "接口与实际返回内容")), ("quality", t("Search quality", "搜索质量")), ("cost", t("API cost", "接口成本"))]:
                chapter_body = authored_sections(authored, chapter_key + "-lead", lang, rows=selected)
                if chapter_key == "capabilities":
                    chapter_body += capability_map(profile, selected, zh)
                if chapter_key == "quality":
                    chapter_body += factual_panel(profile, selected, zh)
                    chapter_body += scenario_panel(profile, selected, authored, zh)
                chapter_body += authored_sections(authored, chapter_key, lang, rows=selected)
                page(chapter_key + ".html", chapter_title, chapter_body)
        page(
            "index.html",
            authored.get("title", {}).get(lang)
            or (
                t(
                    f"Web Search: {len(profile['cases'])}-query comparison",
                    f"Web Search：{len(profile['cases'])} 题接口评测",
                )
                if profile.get("report_cohorts")
                else t("Search quality, speed and cost", "搜索质量、速度与成本")
            ),
            body,
        )
        ds = []
        for c in profile["cases"]:
            anchors = "<br>".join(
                f'<a href="{esc(u)}" title="{esc(u)}">{t("Target page", "目标页面")} · {esc(u.split("://", 1)[-1].split("/", 1)[0])}</a>'
                for u in c.get("anchors", [])
            ) or t("No target URL declared", "未设目标网址")
            if "reference_answer" in c:
                anchors = esc(c["reference_answer"]) + "<br>" + "<br>".join('<a href="' + esc(u) + '">' + t("Labeled source", "标注来源") + "</a>" for u in c.get("reference_urls", []))
            if c.get("paper_ids") or c.get("repositories"):
                anchors += "<br>" + esc(
                    ", ".join(c.get("paper_ids", []) + c.get("repositories", []))
                )
            if c.get("identity_aliases"):
                anchors += "<br>" + t("Verified equivalent URLs: ", "核验为同页的网址：")
                anchors += "<br>".join(esc(u) for u in c["identity_aliases"])
            ds.append(
                cells(
                    [
                        esc(c["id"]),
                        esc(
                            cohort_label(profile, c.get("cohort"), zh) + " / " + c["family"]
                            if profile.get("report_cohorts")
                            else search_category(c["family"], zh)
                        ),
                        esc(c["language"]),
                        esc(search_category(c.get("category", c["family"]), zh)),
                        esc(provenance_label(c.get("provenance_kind", "curated"), zh)),
                        esc(c["query"]),
                        anchors,
                        esc(source_note(c, zh))
                        + "<br>"
                        + (
                            f'<a href="{esc(c["origin"])}">' + t("Source", "来源") + "</a>"
                            if c["origin"].startswith("https://")
                            else ""
                        ),
                    ]
                )
            )
        page(
            "dataset.html",
            t("Query set", "评测查询集"),
            dataset_context(profile, authored, zh)
            + provenance_table(profile, zh)
            + f'<p><a href="{"../" if prefix else ""}query-catalog-license.txt">'
            + t("Public query catalog attribution and license", "公开查询集署名与许可")
            + "</a></p>"
            + origin_filter(profile, zh)
            + cohort_filter(profile, zh)
            + population_filter(profile, authored, zh)
            + "<label>"
            + t("Search queries, categories or sources", "搜索查询、类别或来源")
            + " <input data-query></label><p>"
            + t(
                "Social posts inspire scenarios; original queries not disclosed by their authors are explicitly new queries. Anchor URLs are a limited known-page set.",
                "社交帖子提供场景线索；原作者没有披露的查询，明确标为新编查询。目标 URL 只是有限的已知页面集合。",
            )
            + "</p>"
            + "<p>"
            + t(
                "Target URLs test known-page discovery, not overall relevance. Original targets retain their collection-time evidence; later additions retain dated independent source observations. Public factual answers and source URLs come from the published dataset, not newly captured reference pages.",
                "目标网址用于检查指定页面发现，不代表整体相关性。原有目标保留采集时证据，后补目标保留独立来源的核验日期与内容摘录。公开事实题的答案和来源取自原题库，不表示本轮重新抓取了全部参考网页。",
            )
            + "</p>"
            + table(
                [
                    "ID",
                    t("Search route", "搜索方式"),
                    t("Language", "语言"),
                    t("Category", "类别"),
                    t("Provenance", "来源类型"),
                    t("Query", "查询"),
                    t("Reference page / answer", "参考页面／答案"),
                    t("Provenance", "来源"),
                ],
                [
                    s.replace(
                        "<tr>",
                        f'<tr data-row data-state="all" data-cohort="{esc(c.get("cohort", ""))}" data-origin="{esc(origin_group(c))}" data-case-id="{esc(c["id"])}">',
                        1,
                    )
                    for s, c in zip(ds, profile["cases"], strict=True)
                ],
            ),
        )
        inventory = []
        bycase = {c["id"]: c for c in profile["cases"]}
        method_labels = {method["id"]: method["label"] for method in profile["methods"]}
        for r in selected:
            cid = r["case_id"]
            c = bycase[cid]
            status, reason = classify(c, r)
            reason_label = {
                "api_error": t("API or transport error", "接口或传输错误"),
                "empty": t("Empty result list", "返回空列表"),
                "domain_mismatch": t(
                    "Results outside the requested domain", "返回了指定域名以外的结果"
                ),
                "target_miss": t(
                    f"Reference target absent from top {cutoff}", f"前 {cutoff} 条未匹配参考目标"
                ),
                "target_hit": t("Reference target found", "找到参考目标"),
                "source_hit": t("Published supporting source found", "找到题库标注来源"),
                "source_miss": t("Published supporting source not found", "未找到题库标注来源"),
                "probe": t("Unjudged query", "未标注相关性"),
            }[reason]
            target = "details/" + r["id"] + ".html"
            state_label = {
                "fail": t("Miss / error", "未命中／错误"),
                "pass": t("Target hit", "目标命中"),
                "probe": t("Unjudged query", "未标注相关性"),
            }[status]
            diagnostic_text = "\n".join(
                [
                    c["query"],
                    reason_label,
                    f"HTTP {r['http_status']}; {len(r['results'])} " + t("results", "条结果"),
                    r["error"]
                    or (
                        t("Empty results array", "结果数组为空")
                        if not r["results"]
                        else t(
                            "Compare returned URLs with the independent reference target in the full result.",
                            "完整结果中可对照返回网址与独立参考目标。",
                        )
                    ),
                ]
            )
            inventory.append(
                f'<tr data-row data-state="{status}" data-method="{esc(r["method"])}" data-cohort="{esc(c.get("cohort", ""))}" data-origin="{esc(origin_group(c))}"><td>{esc(cid)}<br>{esc(cohort_label(profile, c.get("cohort"), zh))}</td><td>{esc(c["query"])}</td><td>{esc(method_labels[r["method"]])}</td><td>{esc(state_label)}</td><td>{fmt(r["elapsed_ms"])}</td><td><button data-diagnosis="{esc(diagnostic_text)}" data-detail="{target}">{t("Diagnosis", "诊断")}</button> <a href="{target}">{t("Details", "详情")}</a></td></tr>'
            )
            # Relative path from details/ to shared evidence at output root.
            ev = ("../../" if prefix else "../") + r["evidence"]["path"]
            detail = f"<section data-summary><p>{esc(c['query'])}</p><p>HTTP {r['http_status']} · {fmt(r['elapsed_ms'])} ms · {len(r['results'])} {t('results', '条结果')}</p></section>"
            detail += (
                "<h2>"
                + t("Rule and evidence", "规则与证据")
                + "</h2><p>"
                + esc(c.get("anchors") or c.get("domains") or [])
                + "</p>"
            )
            if c.get("reference_annotation"):
                detail += annotation_detail(c["reference_annotation"], zh)
            elif c.get("anchors"):
                detail += (
                    "<p>"
                    + t(
                        "These are predeclared target URLs, not full reference answers. Before/after HTTP captures verify page identity. Finding other useful sources requires separate relevance and answer-evidence judgments.",
                        "以上是预先指定的目标网址，不是完整标准答案。采集前后的 HTTP 留档用于核验页面身份；其他来源是否有用、能否支持答案，需要另作相关性与证据判断。",
                    )
                    + "</p>"
                )
            if "reference_answer" in c:
                detail += "<h3>" + t("Published answer and sources", "公开标准答案与来源") + "</h3>"
                detail += "<p>" + esc(c["reference_answer"]) + "</p>"
                detail += "<ul>" + "".join(
                    '<li><a href="' + esc(u) + '">' + esc(u) + "</a></li>"
                    for u in c.get("reference_urls", [])
                ) + "</ul>"
                detail += "<p>" + t(
                    "Checks compare the first five URLs with the published sources and look for the normalized answer phrase in individual returned fields. A phrase match is not a factual correctness or entailment judgment.",
                    "检查前五条网址是否属于公开标注来源，以及单个返回字段是否出现规范化后的答案词组。文字匹配不判定上下文是否正确，也不等于答案准确率。",
                ) + "</p>"
            if c.get("paper_ids") or c.get("repositories"):
                detail += (
                    "<p>"
                    + esc(", ".join(c.get("paper_ids", []) + c.get("repositories", [])))
                    + "</p>"
                )
            if c.get("identity_aliases"):
                detail += "<p>" + t("Verified equivalent URLs: ", "核验为同页的网址：")
                detail += esc(", ".join(c["identity_aliases"])) + "</p>"
            diagnosis = reason_label
            detail += "<p>" + diagnosis + "</p>"
            detail += (
                "<h2>"
                + t("Actual request parameters", "实际请求参数")
                + "</h2><pre data-request>"
                + esc(json.dumps(r.get("request", {}), ensure_ascii=False, indent=2))
                + "</pre>"
            )
            metric_labels = [
                (hit_key, t(f"Known target@{cutoff}", f"已知目标@{cutoff}")),
                ("reciprocal_rank", t("Reciprocal rank", "目标排名倒数")),
                ("domain_precision", t("Requested-domain fraction", "指定域名占比")),
                ("duplicate_rate", t("Duplicate-document fraction", "重复文档占比")),
                ("content_coverage", t("Results containing content", "含正文的结果占比")),
            ]
            if "reference_answer" in c:
                metric_labels += [
                    ("labeled_source_hit_5", t("Published source in top 5", "前五条找到标注来源")),
                    ("answer_in_preview_5", t("Answer phrase in title/snippet", "标题／短摘要出现答案")),
                    ("answer_in_delivered_text_5", t("Answer phrase in delivered text", "全部交付文字出现答案")),
                ]
            if "reference_answer" in c:
                sensitivity = factual_sensitivity(c, r["results"])
                detail += '<p>' + t("Post-hoc benchmark-mirror exclusion; blanked ranks: ", "事后题库镜像排除；留空的位置：") + esc(str(sensitivity["excluded_ranks"])) + '</p>'
                detail += table([t("After mirror exclusion", "排除镜像后"), t("Value", "数值")], [cells([t("Answer in preview", "短摘要／标题出现答案"), str(sensitivity["metrics"]["answer_in_preview_5"])]), cells([t("Answer in all text", "全部文字出现答案"), str(sensitivity["metrics"]["answer_in_delivered_text_5"])])])
            detail += table(
                [t("Metric", "指标"), t("Value", "数值")],
                [cells([label, fmt(r["metrics"][key])]) for key, label in metric_labels],
            )
            detail += (
                "<h2>"
                + t("Exact error", "原始错误")
                + "</h2><pre data-error>"
                + esc(
                    r["error"]
                    or t(
                        "No API error reported; inspect the returned response for retrieval diagnostics.",
                        "接口没有报错；检索结果见原始响应。",
                    )
                )
                + "</pre>"
            )
            detail += f'<button data-evidence="{ev}">{t("Open response", "打开响应")}</button> <a href="{ev}">JSON</a>'
            repeats = [x for x in rows if x["case_id"] == cid and x["method"] == r["method"]]
            if len(repeats) > 1:
                repeat_rows = []
                for call in repeats:
                    call_path = ("../../" if prefix else "../") + call["evidence"]["path"]
                    repeat_rows.append(
                        cells(
                            [
                                str(call["round"] + 1),
                                str(call["http_status"]),
                                fmt(call["elapsed_ms"]),
                                str(len(call["results"])),
                                f'<button data-evidence="{call_path}">{t("Open response", "打开响应")}</button> <a href="{call_path}">JSON</a>',
                            ]
                        )
                    )
                detail += (
                    "<h2>"
                    + t("All declared physical calls", "全部预定调用")
                    + "</h2>"
                    + table(
                        [
                            t("Round", "轮次"),
                            "HTTP",
                            "ms",
                            t("Results", "结果数"),
                            t("Evidence", "证据"),
                        ],
                        repeat_rows,
                    )
                )
            peers = [x for x in selected if x["case_id"] == cid]
            detail += same_task(peers, profile, zh)
            detail += "<h2>" + t("Returned results", "返回结果") + "</h2>"
            for item_index, item in enumerate(r["results"]):
                detail += f'<article class="specimen"><a href="{esc(item["url"])}">{esc(item["title"])}</a><p>{esc(item["snippet"])}</p><button data-evidence="{ev}" data-result="{item_index}">{t("Summary and content", "查看摘要与正文")}</button></article>'
            page(target, cid + " / " + method_labels[r["method"]], detail)
        options = [
            ("fail", t("Target miss / error", "目标未命中／错误")),
            ("pass", t("Declared evidence found", "找到预定证据")),
            ("probe", t("Unjudged queries", "未标注相关性的查询")),
            ("all", t("All", "全部")),
        ]
        default = "fail" if any('data-state="fail"' in r for r in inventory) else "all"
        controls = (
            "<label>"
            + t("Filter", "筛选")
            + " <select data-filter>"
            + "".join(
                f'<option value="{v}"'
                + (" selected" if v == default else "")
                + ">"
                + label
                + "</option>"
                for v, label in options
            )
            + "</select></label> <label>"
            + t("Provider", "服务")
            + ' <select data-method-filter><option value="all">'
            + t("All", "全部")
            + "</option>"
            + "".join(
                f'<option value="{esc(method["id"])}">{esc(method["label"])}</option>'
                for method in profile["methods"]
            )
            + "</select></label> <label>"
            + t("Find", "查找")
            + " <input data-query></label>"
        )
        page(
            "results.html",
            t("Saved result inventory", "逐项结果"),
            (render_result_comparison(profile, selected, zh, prefix=prefix, site_prefix=site_prefix, content=authored) if authored.get("presentation") == "comparison-report" else "")
            + origin_filter(profile, zh)
            + cohort_filter(profile, zh)
            + controls
            + table(
                [
                    "ID",
                    t("Query", "查询"),
                    t("Method", "方法"),
                    t("Outcome", "状态"),
                    "ms",
                    t("Evidence", "证据"),
                ],
                inventory,
            ).replace('class="scroll"', 'class="scroll inventory"')
            + (
                ""
                if rows
                else "<p>"
                + t(
                    "No authenticated cohort has been collected. Missing credentials are not provider failures.",
                    "尚未采集经过鉴权的对比批次。缺少密钥不计为服务失败。",
                )
                + "</p>"
            )
            + (
                comparison_tables(profile, selected, rows, zh)
                if authored.get("reading_model")
                else ""
            ),
        )
        research = (
            authored_sections(authored, "method", lang, rows=selected)
            + (cohort_summary(profile, rows, zh) if authored.get("reading_model") else "")
            + fairness_table(profile, rows, zh)
        )
        page(
            "research.html", t("Evaluation method and competitors", "评测方法与主要对手"), research
        )
        page(
            "interpretation.html",
            t("What the results mean", "结果说明了什么"),
            authored_sections(authored, "interpretation-lead", lang, rows=selected)
            + interpretation_summary_table(profile, selected, zh, authored)
            + ("" if authored.get("presentation") == "comparison-report" else factual_panel(profile, selected, zh))
            + authored_sections(authored, "interpretation", lang, rows=selected)
            + ("" if authored.get("presentation") == "comparison-report" else outcome_table(profile, selected, zh) + domain_discovery_table(profile, selected, zh)),
        )
        page(
            "performance.html",
            t("Performance and cost", "性能与成本"),
            authored_sections(authored, "performance", lang, rows=selected)
            + performance_view(profile, diagnostics, zh, evidence_prefix="../" if prefix else "")
            + ('<p class="lede"><a href="cost.html">' + t("Open the complete cost calculation", "查看完整成本计算") + "</a></p>" if authored.get("presentation") == "comparison-report" else ""),
        )
    write_json(
        output / "manifest.json",
        dict(
            schema="web-search-report/1",
            template="web-search/1",
            languages=languages,
            primary_language=language,
            site_prefix=site_prefix,
            routes=routes,
            profile=profile,
            collection_profile=collection_profile,
            scoring_revision=scoring_revision,
            assessment_revision=assessment_revision,
            evidence_redactions=redactions,
            billing_corrections=billing_corrections,
            relevance_sha256=digest(relevance.read_bytes()) if relevance else None,
            result_storage="raw-response",
            rows=[
                {
                    **{k: v for k, v in r.items() if k != "results"},
                    "result_count": len(r["results"]),
                    "normalized_results_sha256": digest(
                        json.dumps(r["results"], ensure_ascii=False, sort_keys=True).encode()
                    ),
                }
                for r in rows
            ],
            receipt=receipt,
            bundle=bundle,
            content_sha256=digest(content.read_bytes()) if content else None,
            diagnostics=diagnostics,
            authored=authored if authored.get("reading_model") or authored.get("presentation") else None,
            reading_sections=reading_sections,
            renderer_sha256=digest(Path(__file__).read_bytes()),
        ),
    )
    check(output)
    return output / "index.html"


def check(output):
    manifest = json.loads((output / "manifest.json").read_text())
    if manifest.get("schema") == "web-search-reference/1":
        return check_reference(output)
    primary = manifest["primary_language"]
    editions = [
        {r.removeprefix(lang + "/") for r in manifest["routes"] if r.startswith(lang + "/")}
        if lang != primary
        else {r for r in manifest["routes"] if not r.startswith(("en/", "zh/"))}
        for lang in manifest["languages"]
    ]
    if not editions or any(e != editions[0] for e in editions):
        raise ValueError("Language route inventories disagree")
    verify_bundle(output, manifest["bundle"])
    for item in manifest["bundle"]:
        if item["path"].endswith((".json", ".txt", ".md")):
            raw = (output / item["path"]).read_bytes()
            if any(value != b"REDACTED:REDACTED" for value in re.findall(rb'https?://([^/\s"<>@]+)@', raw)):
                raise ValueError("Public evidence contains URL credentials")
    for item in manifest.get("evidence_redactions", []):
        if digest((output / item["path"]).read_bytes()) != item["public_sha256"]:
            raise ValueError("Public redaction binding changed")
    relevance_data = None
    if manifest.get("relevance_sha256"):
        relevance_path = output / "relevance-judgments.json"
        if digest(relevance_path.read_bytes()) != manifest["relevance_sha256"]:
            raise ValueError("Relevance judgments hash mismatch")
        relevance_data = json.loads(relevance_path.read_text())
    payloads = {}
    for row in manifest["rows"]:
        try:
            payloads[row["id"]] = json.loads((output / row["evidence"]["path"]).read_bytes())
        except ValueError:
            payloads[row["id"]] = {}
        if manifest.get("result_storage") == "raw-response":
            results = (
                normalize(payloads[row["id"]])
                if row["http_status"] == 200 and not row["error"]
                else []
            )
            if (
                len(results) != row["result_count"]
                or digest(json.dumps(results, ensure_ascii=False, sort_keys=True).encode())
                != row["normalized_results_sha256"]
            ):
                raise ValueError("Result receipt disagrees with raw response")
            row["results"] = results
    factual_cases = {
        case["id"]: case for case in manifest["profile"]["cases"]
        if case.get("reference_answer")
    }
    if factual_cases:
        sensitivity_path = output / "factual-sensitivity.json"
        expected_sensitivity = {
            row["id"]: factual_sensitivity(factual_cases[row["case_id"]], row["results"])
            for row in manifest["rows"] if row["case_id"] in factual_cases
        }
        sensitivity = json.loads(sensitivity_path.read_text())
        if (
            sensitivity.get("rule") != CONTAMINATION_RULE
            or sensitivity.get("results") != expected_sensitivity
        ):
            raise ValueError("Factual sensitivity disagrees with preserved responses")
    groups = manifest["profile"].get("report_cohorts", [])
    if groups:
        declared = [(g["id"], cid) for g in groups for cid in g["case_ids"]]
        actual_cases = [(c.get("cohort"), c["id"]) for c in manifest["profile"]["cases"]]
        if len(declared) != len(set(cid for _, cid in declared)) or set(declared) != set(
            actual_cases
        ):
            raise ValueError("Cohort population does not match the combined query set")
        by_id = dict((cid, group) for group, cid in declared)
        if any(r.get("cohort") != by_id.get(r["case_id"]) for r in manifest["rows"]):
            raise ValueError("Result belongs to the wrong cohort")
    for lang in manifest["languages"]:
        prefix = "" if lang == primary else lang + "/"
        soup = BeautifulSoup((output / (prefix + "research.html")).read_text(), "html.parser")
        expected = BeautifulSoup(
            fairness_table(manifest["profile"], manifest["rows"], lang == "zh"), "html.parser"
        )
        actual = soup.select_one("[data-fairness]")
        if actual is None or actual.get_text() != expected.select_one("[data-fairness]").get_text():
            raise ValueError("Fairness table disagrees with physical calls")
        if groups:
            summary_route = (
                "research.html"
                if (manifest.get("authored") or {}).get("reading_model")
                else "index.html"
            )
            home = BeautifulSoup((output / (prefix + summary_route)).read_text(), "html.parser")
            section = home.select_one("[data-cohort-summary]")
            expected_section = BeautifulSoup(
                cohort_summary(manifest["profile"], manifest["rows"], lang == "zh"), "html.parser"
            )
            if section is None or section.get_text() != expected_section.get_text():
                raise ValueError("Missing or incorrect cohort summary")
            for route in ("dataset.html", "results.html"):
                inventory = BeautifulSoup((output / (prefix + route)).read_text(), "html.parser")
                options = inventory.select("[data-cohort-filter] option")
                if {o["value"] for o in options} != {"all", *(g["id"] for g in groups)}:
                    raise ValueError("Incomplete cohort filter")
        for route in ("dataset.html", "results.html"):
            inventory = BeautifulSoup((output / (prefix + route)).read_text(), "html.parser")
            expected_groups = {origin_group(c) for c in manifest["profile"]["cases"]}
            if {o["value"] for o in inventory.select("[data-origin-filter] option")} != {
                "all",
                *expected_groups,
            }:
                raise ValueError("Incomplete query-source filter")
            expected_origins = [
                origin_group(c)
                for c in manifest["profile"]["cases"]
                for _ in range(
                    1 if route == "dataset.html" else (
                        len(manifest["profile"]["methods"]) if manifest["rows"] else 0
                    )
                )
            ]
            if Counter(r.get("data-origin") for r in inventory.select("[data-row]")) != Counter(
                expected_origins
            ):
                raise ValueError("Query-source population mismatch")
            if route == "results.html":
                method_ids = {method["id"] for method in manifest["profile"]["methods"]}
                if {option["value"] for option in inventory.select("[data-method-filter] option")} != {"all", *method_ids}:
                    raise ValueError("Incomplete provider filter")
                if Counter(row.get("data-method") for row in inventory.select("[data-row]")) != Counter(
                    result["method"] for result in manifest["rows"]
                ):
                    raise ValueError("Provider filter population mismatch")
    expected_diagnostics = operational(manifest["rows"], payloads)
    cases = {c["id"]: c for c in manifest["profile"]["cases"]}
    for row in manifest["rows"]:
        if row["http_status"] == 200 and not row["error"]:
            if normalize(payloads[row["id"]]) != row["results"]:
                raise ValueError("Published results disagree with raw response")
        if row["metrics"] != evaluate(cases[row["case_id"]], row["results"]):
            raise ValueError("Published scores disagree with the declared rules")
    if relevance_data:
        selected = [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]]
        verify_provenance(manifest["profile"], selected, relevance_data)
    if manifest.get("scoring_revision"):
        revision = manifest["scoring_revision"]
        collected = [
            dict(
                r,
                metrics=evaluate(
                    next(
                        c
                        for c in manifest["collection_profile"]["cases"]
                        if c["id"] == r["case_id"]
                    ),
                    r["results"],
                ),
            )
            for r in manifest["rows"]
        ]
        revised, _, changes = apply_identity_correction(
            manifest["collection_profile"], collected, revision["contract"]
        )
        expected_changes = [c for c in revision["changes"] if c["id"] in {r["id"] for r in collected}]
        if changes != expected_changes:
            raise ValueError("Scoring revision provenance mismatch")
        if manifest.get("assessment_revision"):
            from .scoring import apply_report_revision
            revised, _, _ = apply_report_revision(revised, [], manifest["assessment_revision"]["contract"])
        revised, _ = project_comparison(revised, [], manifest.get("authored") or {})
        if revised != manifest["profile"]:
            raise ValueError("Scoring revision profile mismatch")
    elif manifest.get("assessment_revision"):
        from .scoring import apply_report_revision
        revised, _, _ = apply_report_revision(manifest["collection_profile"], [], manifest["assessment_revision"]["contract"])
        revised, _ = project_comparison(revised, [], manifest.get("authored") or {})
        if revised != manifest["profile"]:
            raise ValueError("Assessment revision profile mismatch")
    if manifest.get("diagnostics") != expected_diagnostics:
        raise ValueError("Performance explanation disagrees with raw evidence")
    for route in manifest["routes"]:
        path = output / route
        soup = BeautifulSoup(path.read_text(), "html.parser")
        authored = manifest.get("authored") or {}
        if authored.get("reading_model") and "details/" not in route:
            lang = soup.html["lang"]
            name = Path(route).name
            key = {
                "index.html": "summary",
                "research.html": "method",
                "interpretation.html": "interpretation",
                "performance.html": "performance",
                "capabilities.html": "capabilities",
                "quality.html": "quality",
                "cost.html": "cost",
            }.get(name)
            if key:
                score_rows = [
                    r
                    for r in manifest["rows"]
                    if r["round"] == manifest["profile"]["protocol"]["score_round"]
                ]
                expected = (
                    authored_sections(authored, "lead", lang, rows=score_rows)
                    if name == "index.html"
                    else ""
                ) + (
                    authored_sections(authored, "interpretation-lead", lang, rows=score_rows)
                    if name == "interpretation.html"
                    else ""
                ) + (
                    authored_sections(authored, key + "-lead", lang, rows=score_rows)
                    if name in ("capabilities.html", "quality.html", "cost.html") else ""
                ) + authored_sections(authored, key, lang, rows=score_rows)
                if name == "index.html" and authored.get("presentation") != "comparison-report":
                    chapters = manifest["reading_sections"]
                    if [s["data-reading"] for s in soup.select("[data-reading]")] != [
                        c["key"] for c in chapters
                    ]:
                        raise ValueError("Missing reader chapter")
                    expected += "".join(
                        authored_sections(authored, c["key"] + "-lead", lang, rows=score_rows, heading_level=3) + authored_sections(
                            authored, c["key"], lang, rows=score_rows, heading_level=3
                        )
                        for c in chapters
                    )
                expected_soup = BeautifulSoup(expected, "html.parser")
                if [s.get_text() for s in soup.select("[data-authored]")] != [
                    s.get_text() for s in expected_soup.select("[data-authored]")
                ]:
                    raise ValueError(
                        "Reader explanation or measured specimen differs from its bound content"
                    )
        quiet_certified = authored.get("presentation") == "comparison-report" and manifest["receipt"].get("certified") and not manifest["receipt"].get("synthetic")
        if len(soup.select("p[data-state]")) != (0 if quiet_certified else 1):
            raise ValueError("Missing evidence state")
        if soup.head is None:
            raise ValueError("Missing HTML head for artifact hosting")
        if route.endswith("performance.html") and manifest["rows"]:
            trs = soup.select("table")[0].select("tbody tr")
            for tr, method in zip(trs, manifest["profile"]["methods"], strict=True):
                stats = expected_diagnostics["methods"][method["id"]]
                keys = (
                    "client_ms",
                    "server_ms",
                    "outside_server_ms",
                    "ttfb_ms",
                    "body_ms",
                    "bytes",
                )
                for cell, key in zip(tr.select("td")[1:], keys, strict=True):
                    expected = fmt(stats[key]["mean"]) + f" (n={stats[key]['n']})"
                    if cell.get_text() != expected:
                        raise ValueError("Visible performance decomposition mismatch")
            candidates = [
                c for c in expected_diagnostics["calls"] if c["after_first_ms"] is not None
            ]
            if candidates:
                chosen = max(candidates, key=lambda c: c["after_first_ms"])
                specimen = soup.select_one("[data-timing-specimen]")
                if specimen is None or specimen["data-timing-specimen"] != chosen["id"]:
                    raise ValueError("Missing or wrong timing specimen")
        if route.endswith("quality.html") and (manifest.get("authored") or {}).get("presentation") == "comparison-report":
            score_rows = [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]]
            for selector, expected in (("[data-factual]", factual_panel(manifest["profile"], score_rows, soup.html["lang"] == "zh")), ("[data-scenario]", scenario_panel(manifest["profile"], score_rows, manifest["authored"], soup.html["lang"] == "zh"))):
                expected_node = BeautifulSoup(expected, "html.parser").select_one(selector)
                if str(soup.select_one(selector)) != str(expected_node):
                    raise ValueError("Quality population differs from measured inputs")
        if route.endswith("interpretation.html"):
            selected = [
                r for r in manifest["rows"]
                if r["round"] == manifest["profile"]["protocol"]["score_round"]
            ]
            expected_table = BeautifulSoup(
                interpretation_summary_table(manifest["profile"], selected, soup.html["lang"] == "zh", manifest.get("authored") or {}),
                "html.parser",
            ).select_one("table")
            actual_table = soup.select_one("table")
            if actual_table is None or str(actual_table) != str(expected_table):
                raise ValueError("Interpretation core comparison differs from evidence")
        comparison_home = route.endswith("index.html") and (manifest.get("authored") or {}).get("presentation") == "comparison-report"
        if comparison_home:
            expected = BeautifulSoup(population_summary(manifest["profile"], [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]], manifest["authored"], soup.html["lang"] == "zh"), "html.parser")
            if str(soup.select_one('[data-populations]')) != str(expected.select_one('[data-populations]')):
                raise ValueError("Population summary differs from measured inputs")
        if route.endswith("index.html") and not comparison_home:
            for selector, renderer in (
                ("[data-factual]", factual_panel),
                ("[data-modes]", mode_panel),
            ):
                score_rows = [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]]
                expected_panel = BeautifulSoup(renderer(manifest["profile"], score_rows, soup.html["lang"] == "zh"), "html.parser").select_one(selector)
                if expected_panel and str(soup.select_one(selector)) != str(expected_panel):
                    raise ValueError("Deterministic evidence comparison differs from measured inputs")
            if relevance_data:
                selected = [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]]
                scores = score_relevance(manifest["profile"], selected, relevance_data["plan"], relevance_data["labels"])
                expected_panel = BeautifulSoup(relevance_panel(manifest["profile"], scores, relevance_data, soup.html["lang"] == "zh"), "html.parser").select_one("[data-relevance]")
                actual_panel = soup.select_one("[data-relevance]")
                if actual_panel is None or str(actual_panel) != str(expected_panel):
                    raise ValueError("Preview relevance panel disagrees with judgments")
            if not soup.select("table"):
                raise ValueError("Missing core metrics")
            table_rows = soup.select("table")[0].select("tbody tr")
            overview_methods = scoped_overview_methods(manifest["profile"], manifest.get("authored") or {})
            if len(table_rows) != len(overview_methods):
                raise ValueError("Missing participant")
            overview_rows = scoped_overview_rows(manifest["profile"], manifest["rows"], manifest.get("authored") or {})
            physical = aggregate(overview_rows)
            cutoff = manifest["profile"]["protocol"]["top_k"]
            hit_key = "anchor_hit_5" if cutoff == 5 else "anchor_hit_10"
            quality = aggregate(
                [
                    r
                    for r in overview_rows
                    if r["round"] == manifest["profile"]["protocol"]["score_round"]
                ]
            )
            for tr, method in zip(table_rows, overview_methods, strict=True):
                stats = physical.get(method["id"])
                if not stats:
                    continue
                for column, key in ((2, "mean"), (3, "p50"), (4, "p95")):
                    cell = tr.select("td")[column]
                    value = stats["all_latency"][key]
                    if float(cell["data-value"]) != value or cell.get_text().replace(
                        "★", ""
                    ).strip() != fmt(value):
                        raise ValueError("Visible latency disagrees with evidence")
                cell = tr.select("td")[5]
                hit = quality[method["id"]]["metrics"][hit_key]
                expected = (
                    "—" if hit["mean"] is None else (
                        f"{hit['mean'] * 100:.1f}% ({hit['n']}题)"
                        if soup.html["lang"] == "zh" else f"{hit['mean'] * 100:.1f}% ({hit['n']} queries)"
                    )
                )
                if cell.get_text().replace("★", "").strip() != expected:
                    raise ValueError("Visible target hit disagrees with evidence")
            for i in (2, 3, 4, 5):
                cells_at = [r.select("td")[i] for r in table_rows]
                values = [float(c["data-value"]) if c["data-value"] else None for c in cells_at]
                best = None
                if len(values) > 1 and all(v is not None for v in values):
                    candidate = (max if i == 5 else min)(values)
                    if values.count(candidate) == 1:
                        best = candidate
                for cell, value in zip(cells_at, values, strict=True):
                    if ("win" in cell.get("class", [])) != (best is not None and value == best):
                        raise ValueError("Missing or incorrect unique winner marker")
        if "details/" in route and (
            len(soup.select("[data-summary]")) != 1
            or not soup.select("[data-error]")
            or not soup.select("[data-evidence]")
        ):
            raise ValueError("Missing detail summary, error or evidence")
        for a in soup.select("[href],[data-evidence],[data-detail]"):
            if a.name == "base":
                continue
            href = a.get("href", a.get("data-evidence", a.get("data-detail", "")))
            if href.startswith(("https://", "http://", "#")):
                continue
            href = href.partition("#")[0]
            site_root = output.parent if manifest.get("site_prefix") else output
            target = (site_root / href).resolve()
            if not target.is_relative_to(site_root.resolve()) or not target.is_file():
                raise ValueError("Broken evidence/navigation target: " + href)
        if "/Users/" in str(soup):
            raise ValueError("Private data in report")
    return True


def build_reference(output, content_path, language="en"):
    """Render documented API capabilities separately from measured comparisons."""
    data = json.loads(content_path.read_text())
    if data.get("schema") != "web-search-reference/1":
        raise ValueError("Unsupported reference comparison schema")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    artifacts = data.get("artifacts", [])
    verify_bundle(content_path.parent, artifacts)
    for item in artifacts:
        target = output / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((content_path.parent / item["path"]).read_bytes())
    sources = {s["id"]: s for s in data["sources"]}
    routes = []
    for lang in (language, "zh" if language == "en" else "en"):
        zh = lang == "zh"
        route = "index.html" if lang == language else lang + "/index.html"
        other = "zh" if lang == "en" else "en"
        switch = other + "/index.html" if lang == language else "../index.html"

        def refs(ids):
            return " ".join(
                f'<a class="citation" href="#source-{esc(i)}">[{esc(i)}]</a>' for i in ids
            )

        body = f'<p class="eyebrow">API REFERENCE / {esc(data["checked"])}</p><h1>{esc(data["title"][lang])}</h1>'
        body += '<p class="notice" data-state>' + esc(data["scope"][lang]) + "</p>"
        body += '<p class="lede">' + esc(data["lead"][lang]) + "</p>"
        for section in data["sections"]:
            body += f'<section id="{esc(section["id"])}"><h2>{esc(section["title"][lang])}</h2>'
            if section.get("headers"):
                body += table(
                    section["headers"][lang],
                    [cells([esc(v) for v in row[lang]]) for row in section["rows"]],
                )
            for paragraph in section.get("paragraphs", []):
                body += (
                    "<p>" + esc(paragraph[lang]) + " " + refs(paragraph.get("sources", [])) + "</p>"
                )
            for sample in section.get("samples", []):
                body += (
                    ("<details open>" if sample.get("open") else "<details>")
                    + "<summary>"
                    + esc(sample["label"][lang])
                    + "</summary>"
                )
                body += (
                    '<p class="muted">'
                    + esc(sample["note"][lang])
                    + "</p><pre><code>"
                    + esc(sample["code"])
                    + "</code></pre></details>"
                )
            body += "</section>"
        body += (
            '<section id="sources"><h2>'
            + ("来源与核对范围" if zh else "Sources and scope")
            + "</h2>"
        )
        for identity, source in sources.items():
            if not source["url"].startswith("https://"):
                raise ValueError("Sources must use HTTPS")
            body += f'<p id="source-{esc(identity)}">[{esc(identity)}] <a href="{esc(source["url"])}">{esc(source["label"][lang])}</a><br><span class="muted">{esc(source["note"][lang])}</span></p>'
        for item in artifacts:
            target = ("../" if lang != language else "") + item["path"]
            body += f'<p><a href="{esc(target)}">{esc(item["label"][lang])}</a></p>'
        body += "</section>"
        navigation = "".join(
            f'<a href="#{esc(s["id"])}">{esc(s["nav"][lang])}</a>' for s in data["sections"]
        )
        document = f'''<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(data["title"][lang])}</title><style>{STYLE}
main{{max-width:1120px}}section{{border-top:1px solid var(--line);margin-top:36px;padding-top:8px}}details{{border:1px solid var(--line);border-radius:8px;padding:16px;margin:16px 0;background:var(--panel)}}summary{{cursor:pointer;font-weight:600}}code{{font:14px/1.65 ui-monospace,monospace}}.citation{{font-size:13px;color:var(--win)}}a:focus-visible,summary:focus-visible,button:focus-visible{{outline:3px solid #648caf;outline-offset:4px}}@media print{{nav,button{{display:none}}.scroll{{overflow:visible}}table{{min-width:0}}body{{font-size:11px}}}}
</style></head><body><nav>{navigation}<a href="{switch}">{"English" if zh else "中文"}</a><button data-theme>{"切换主题" if zh else "Theme"}</button></nav><main>{body}</main><script>document.querySelector('[data-theme]').onclick=()=>{{let dark=getComputedStyle(document.body).backgroundColor==='rgb(17, 25, 31)';document.body.className=dark?'light':'dark'}};</script></body></html>'''
        path = output / route
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document)
        routes.append(route)
    (output / "reference-input.json").write_bytes(content_path.read_bytes())
    write_json(
        output / "manifest.json",
        {
            "schema": "web-search-reference/1",
            "template": "web-search/reference/1",
            "primary_language": language,
            "languages": ["en", "zh"],
            "routes": routes,
            "input_sha256": digest(content_path.read_bytes()),
            "bundle": artifacts,
        },
    )
    check_reference(output)
    return output / "index.html"


def check_reference(output):
    """Reject altered facts, missing sources and broken specimen/navigation links."""
    manifest = json.loads((output / "manifest.json").read_text())
    raw = (output / "reference-input.json").read_bytes()
    if digest(raw) != manifest["input_sha256"]:
        raise ValueError("Reference input hash mismatch")
    data = json.loads(raw)
    if manifest.get("bundle", []) != data.get("artifacts", []):
        raise ValueError("Reference artifact bindings disagree")
    verify_bundle(output, manifest.get("bundle", []))
    expected = {
        "index.html",
        ("zh" if manifest["primary_language"] == "en" else "en") + "/index.html",
    }
    if set(manifest["routes"]) != expected:
        raise ValueError("Missing language edition")
    for route in expected:
        soup = BeautifulSoup((output / route).read_text(), "html.parser")
        lang = soup.html["lang"]
        if (
            soup.h1.get_text() != data["title"][lang]
            or soup.select_one("[data-state]").get_text() != data["scope"][lang]
        ):
            raise ValueError("Reference scope mismatch")
        for section in data["sections"]:
            node = soup.find(id=section["id"])
            if node is None or node.h2.get_text() != section["title"][lang]:
                raise ValueError("Missing reference section")
            actual = [[td.get_text() for td in row.select("td")] for row in node.select("tbody tr")]
            if actual != [row[lang] for row in section.get("rows", [])]:
                raise ValueError("Capability table mismatch")
            for p in section.get("paragraphs", []):
                if p[lang] not in node.get_text():
                    raise ValueError("Missing factual qualification")
            if [x.get_text() for x in node.select("pre code")] != [
                s["code"] for s in section.get("samples", [])
            ]:
                raise ValueError("Specimen mismatch")
            if [x.has_attr("open") for x in node.select("details")] != [
                bool(s.get("open")) for s in section.get("samples", [])
            ]:
                raise ValueError("Specimen visibility mismatch")
        for source in data["sources"]:
            node = soup.find(id="source-" + source["id"])
            if node is None or node.a["href"] != source["url"]:
                raise ValueError("Missing reference source")
        for a in soup.select("a[href]"):
            href = a["href"]
            if href.startswith("#"):
                if soup.find(id=href[1:]) is None:
                    raise ValueError("Broken reference anchor")
            elif (
                not href.startswith("https://")
                and not (output / route).parent.joinpath(href).is_file()
            ):
                raise ValueError("Broken reference navigation")
        if "/Users/" in str(soup):
            raise ValueError("Private path in reference report")
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=Path, default=ROOT / "evaluation/web-search/current-report.json"
    )
    parser.add_argument("--source", type=Path)
    parser.add_argument(
        "--cohort",
        action="append",
        default=[],
        metavar="NAME=RUN",
        help="Add a separately verified cohort to the same report",
    )
    parser.add_argument("--content", type=Path)
    parser.add_argument(
        "--reference-content",
        type=Path,
        help="Build a sourced API reference comparison, without benchmark scores",
    )
    parser.add_argument("--identity-correction", type=Path)
    parser.add_argument("--relevance", type=Path, help="Bound blind preview-relevance judgments")
    parser.add_argument("--site-prefix", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--language", choices=["en", "zh"], default=None)
    parser.add_argument("--include-english", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--release-check", type=Path, metavar="VISUAL_RECEIPT")
    parser.add_argument("--export-delivery", type=Path, metavar="DESTINATION",
                        help="Export the checked report into lossless case shards for bounded-file hosts")
    args = parser.parse_args(argv)
    if args.export_delivery:
        if args.source or args.cohort or args.reference_content:
            parser.error("Delivery export consumes an existing report, not build inputs")
        check(args.output)
        from .delivery import export, verify
        export(args.output, args.export_delivery)
        verify(args.output, args.export_delivery)
        print(args.export_delivery)
        return
    if args.reference_content:
        if args.source or args.identity_correction or args.release_check:
            parser.error("Reference comparisons cannot consume measured benchmark inputs")
        language = args.language
        if language is None and (args.output / "manifest.json").exists():
            language = json.loads((args.output / "manifest.json").read_text())["primary_language"]
        from harness.report_languages import configured_report_language
        print(build_reference(args.output, args.reference_content, language or configured_report_language()))
        return
    if args.release_check:
        release_check(args.output, args.release_check)
        print("Web Search full release checks passed")
        return
    if args.check:
        check(args.output)
        print("Web Search report checks passed")
        return
    from harness.report_languages import resolve_report_language
    language = resolve_report_language(args.output, args.language)
    print(
        build(
            args.output,
            profile_path=args.profile,
            source=args.source,
            content=args.content,
            correction=args.identity_correction,
            relevance=args.relevance,
            site_prefix=args.site_prefix,
            cohorts=[
                (value.split("=", 1)[0], Path(value.split("=", 1)[1])) for value in args.cohort
            ],
            language=language,
            include_english=args.include_english,
        )
    )


def release_check(output, visual_receipt):
    """Combine deterministic evidence checks with the separate operated-browser gate."""
    check(output)
    proof = json.loads(visual_receipt.read_text())
    manifest = output / "manifest.json"
    if proof["manifest_sha256"] != digest(manifest.read_bytes()):
        raise ValueError("Visual check belongs to a different build")
    if json.loads(manifest.read_text()).get("schema") == "web-search-reference/1":
        expected = {(lang, 1280, "light") for lang in ("en", "zh")}
        actual = set()
        for capture in proof["captures"]:
            if digest(Path(capture["path"]).read_bytes()) != capture["sha256"]:
                raise ValueError("Reference visual evidence mismatch")
            if capture["metrics"]["page"] != capture["metrics"]["viewport"]:
                raise ValueError("Reference page overflows its viewport")
            actual.add((capture["language"], capture["width"], capture["theme"]))
        if actual != expected:
            raise ValueError("Incomplete reference visual coverage")
        return True
    report_manifest = json.loads(manifest.read_text())
    standalone_chapters = (report_manifest.get("authored") or {}).get("presentation") == "comparison-report"
    languages = tuple(report_manifest.get("languages") or [report_manifest["primary_language"]])
    # The deterministic build check owns links, evidence targets, result identity,
    # filters and pagination markup. Visual review is one normal desktop rendering
    # per top-level tab, so it remains a content/layout review rather than a slow
    # browser-operation matrix.
    tab_routes = (
        {"home", "capabilities", "dataset", "results", "performance", "method", "interpretation"}
        if standalone_chapters
        else {"home", "dataset", "results", "interpretation", "performance", "method"}
    )
    expected = {
        (lang, route, 1280, "light")
        for lang in languages
        for route in tab_routes
    }
    actual = set()
    for capture in proof["captures"]:
        key = (capture["language"], capture["route_class"], capture["width"], capture["theme"])
        if capture.get("page_overflow") or not capture.get("readable"):
            raise ValueError("Unreadable visual state")
        image = visual_receipt.parent / capture["path"]
        if image.is_symlink() or digest(image.read_bytes()) != capture["sha256"]:
            raise ValueError("Visual evidence mismatch")
        actual.add(key)
    if not expected.issubset(actual):
        raise ValueError("Incomplete focused visual coverage")
    return True


def cohort_label(profile, name, zh):
    return next(
        (
            g["label"]["zh" if zh else "en"]
            for g in profile.get("report_cohorts", [])
            if g["id"] == name
        ),
        "",
    )


def origin_group(case):
    kind = case.get("provenance_kind", "curated")
    return "originals" if kind in {"community_exact", "user_screenshot"} else kind


def origin_filter(profile, zh):
    counts = Counter(origin_group(c) for c in profile["cases"])
    labels = {
        "originals": ("Exact community queries", "社区原题"),
        "community_derived": ("Derived scenarios", "社区场景派生题"),
        "public_catalog": ("Public query catalog", "公开集补充"),
        "curated": ("Curated probes", "自编探针"),
    }
    return (
        "<label>"
        + ("题目来源" if zh else "Query source")
        + ' <select data-origin-filter><option value="all">'
        + ("全部" if zh else "All")
        + "</option>"
        + "".join(
            '<option value="'
            + esc(k)
            + '">'
            + esc(labels.get(k, (k, k))[int(zh)])
            + f" ({n})</option>"
            for k, n in counts.items()
        )
        + "</select></label> "
    )


def cohort_filter(profile, zh):
    groups = profile.get("report_cohorts", [])
    if not groups:
        return ""
    return (
        "<label>"
        + ("分项" if zh else "Query group")
        + ' <select data-cohort-filter><option value="all">'
        + ("全部题目" if zh else "All queries")
        + "</option>"
        + "".join(
            f'<option value="{esc(g["id"])}">{esc(cohort_label(profile, g["id"], zh))}</option>'
            for g in groups
        )
        + "</select></label> "
    )


def cohort_summary(profile, rows, zh):
    groups = profile.get("report_cohorts", [])
    if not groups:
        return ""

    def t(en, cn):
        return cn if zh else en

    entries = []
    for group in groups:
        subset = [r for r in rows if r["cohort"] == group["id"]]
        stats = aggregate(subset)
        for m in profile["methods"]:
            calls = [r for r in subset if r["method"] == m["id"]]
            s = stats[m["id"]]
            entries.append(
                cells(
                    [
                        esc(cohort_label(profile, group["id"], zh)),
                        esc(m["label"]),
                        str(len(group["case_ids"])),
                        str(s["n"]),
                        f"{s['nonempty']}/{s['n']}",
                        fmt(sum(len(r["results"]) for r in calls) / len(calls)),
                        *[fmt(s["all_latency"][k]) for k in ("mean", "p50", "p95")],
                    ]
                )
            )
    return (
        "<section data-cohort-summary><h2>"
        + t("Independent benchmarks, with separate collection cohorts", "两套评测，按采集批次分别查看")
        + "</h2><p>"
        + t(
            f"{len(profile['cases'])} queries and {len(rows)} calls from {len(groups)} separately collected complete batches. The rows below preserve each collection separately; do not infer a new same-window speed trial from their combined count. Target-page hits are calculated only for queries with declared targets.",
            f"共 {len(profile['cases'])} 道题、{len(rows)} 次调用，来自 {len(groups)} 批完整实测。下表分别保留各批次耗时，不把合计调用数当成一轮同时段测速；目标页命中只统计有预定目标的题目。",
        )
        + "</p>"
        + table(
            [
                t("Query group", "分项"),
                t("Method", "接口"),
                t("Queries/provider", "每家题数"),
                t("Calls", "调用数"),
                t("Nonempty/calls", "非空/调用数"),
                t("Mean returned count", "平均返回条数"),
                t("Mean ms", "平均 ms"),
                "P50 ms",
                "P95 ms",
            ],
            entries,
        )
        + "</section>"
    )


def fairness_table(profile, rows, zh):
    """Expose requested budgets and actual delivery without equal-count filtering."""

    def t(en, cn):
        return cn if zh else en

    entries = []
    first = {}
    for method in profile["methods"]:
        group = [r for r in rows if r["method"] == method["id"]]
        requested = Counter(
            str(r.get("request", {}).get("limit", r.get("request", {}).get("count", "—")))
            for r in group
        )
        counts = Counter(len(r["results"]) for r in group)
        first[method["id"]] = {r["case_id"]: len(r["results"]) for r in group if r["round"] == 0}
        entries.append(
            cells(
                [
                    esc(method["label"]),
                    str(len(group)),
                    esc(", ".join(f"{k} × {v}" for k, v in sorted(requested.items()))),
                    esc(", ".join(f"{k} × {v}" for k, v in sorted(counts.items()))),
                    fmt(sum(len(r["results"]) for r in group) / len(group)) if group else "—",
                ]
            )
        )
    common = set.intersection(*(set(v) for v in first.values())) if first else set()
    equal = sum(len({v[c] for v in first.values()}) == 1 for c in common)
    return (
        "<section data-fairness><h2>"
        + t("Equal request budgets, observed result counts", "请求预算相同，实际返回数量另行统计")
        + "</h2>"
        + table(
            [
                t("Provider", "接口"),
                t("Calls", "调用数"),
                t("Requested count × calls", "请求条数 × 调用次数"),
                t("Returned count × calls", "返回条数 × 调用次数"),
                t("Mean results/call", "平均返回条数/次"),
            ],
            entries,
        )
        + "<p>"
        + t(
            f"First-round queries with equal result counts across all providers: {equal}/{len(common)}. Empty and short responses remain in every declared denominator. Counts do not establish relevance.",
            f"首轮各接口返回数量相同的题目：{equal}/{len(common)}。空结果和不足额返回均保留在预定分母中。返回条数不代表相关性。",
        )
        + "</p></section>"
    )


def comparison_tables(profile, selected, rows, zh):
    def t(en, cn):
        return cn if zh else en

    cutoff = profile["protocol"]["top_k"]
    hit_key = "anchor_hit_5" if cutoff == 5 else "anchor_hit_10"
    body = "<h2>" + t("Separate task populations", "分场景比较") + "</h2>"
    bycase = {c["id"]: c for c in profile["cases"]}
    grouped = []
    for family in dict.fromkeys(c["family"] for c in profile["cases"]):
        stats = aggregate([r for r in selected if bycase[r["case_id"]]["family"] == family])
        for method in profile["methods"]:
            s = stats.get(method["id"])
            m = s["metrics"] if s else {}

            def metric(k, m=m):
                x = m.get(k, {"mean": None, "n": 0})
                return fmt(x["mean"]) + f" (n={x['n']})"

            grouped.append(
                cells(
                    [
                        esc(
                            {
                                "web": t("Web", "通用网页"),
                                "site": t("Site restriction", "站点限定"),
                                "vertical": t("Native source / site route", "原生垂类／站点路线"),
                            }[family]
                        ),
                        esc(method["label"]),
                        metric(hit_key),
                        metric("reciprocal_rank"),
                        metric("domain_precision"),
                        metric("duplicate_rate"),
                    ]
                )
            )
    body += table(
        [
            t("Population", "场景"),
            t("Method", "方法"),
            f"Hit@{cutoff}",
            "MRR",
            t("Domain fraction", "指定域名占比"),
            t("Duplicate fraction", "重复占比"),
        ],
        grouped,
    )
    body += (
        "<p>"
        + t(
            f"Each metric shows its eligible query count. Hit@{cutoff} and MRR use declared known pages; domain fraction uses returned top-{cutoff} URLs and is unavailable for empty responses. Native source and site operators implement the same intent through different routes.",
            f"每项指标标明适用题数。Hit@{cutoff} 和 MRR 只核对预定的已知页面；域名占比以实际返回的前 {cutoff} 条为分母，空响应不记为满足约束。原生来源参数和 site 算子是同一意图的不同调用方式。",
        )
        + "</p>"
    )
    body += "<h2>" + t("Text delivery and estimated charges", "文本交付与费用估算") + "</h2>"
    stats = aggregate(selected)
    physical = aggregate(rows)
    textrows = []
    for method in profile["methods"]:
        s = stats.get(method["id"])
        m = s["metrics"] if s else {}
        total_cost = physical.get(method["id"], {}).get("cost_total")
        nonempty = s["nonempty"] if s else 0
        textrows.append(
            cells(
                [
                    esc(method["label"]),
                    *[
                        fmt(m.get(k, {}).get("mean"))
                        for k in (
                            "result_count",
                            "snippet_chars",
                            "summary_chars",
                            "content_chars",
                            "content_coverage",
                        )
                    ],
                    (
                        "—"
                        if total_cost is None
                        else f"{total_cost:.3f}"
                    )
                    + " "
                    + method["currency"],
                    ("—" if total_cost is None or not nonempty else f"{total_cost / nonempty:.4f}")
                    + " " + method["currency"],
                ]
            )
        )
    body += table(
        [
            t("Method", "方法"),
            t("Results/query", "每查询结果数"),
            t("Snippet chars/query", "每查询片段字数"),
            t("Summary chars/query", "每查询摘要字数"),
            t("Content chars/query", "每查询正文字数"),
            t("Content coverage", "含正文结果占比"),
            t("All-call estimate", "全部调用估算费用"),
            t("Cost/nonempty query", "每道非空题估算费用"),
        ],
        textrows,
    )
    body += (
        "<p>"
        + t(
            "Text sizes count Unicode characters, not tokens or correctness. Native fields are retained separately: text in a snippet is not automatically full content. No source-faithfulness or answer-accuracy score is assigned without independent judgments. Cost is an estimate from list prices; a transport timeout has unknown billing.",
            "文本量按 Unicode 字符统计，不代表 token 数或正确性。片段、摘要、正文分别保留，片段较长也不自动算作全文。没有独立判断时，不给出内容忠实度或回答准确率。费用按目录价估算；传输超时的计费状态未知。",
        )
        + "</p>"
    )
    return body


def same_task(peers, profile, zh):
    if len(peers) < 2:
        return ""

    def t(en, cn):
        return cn if zh else en

    labels = {m["id"]: m["label"] for m in profile["methods"]}
    headings = [t("Rank", "排名")] + [labels[p["method"]] for p in peers]
    rows = []
    for rank in range(max((len(p["results"][:10]) for p in peers), default=0)):
        values = [str(rank + 1)]
        for peer in peers:
            if rank >= len(peer["results"]):
                values.append("—")
                continue
            item = peer["results"][rank]
            values.append(
                f'<a href="{esc(item["url"])}">{esc(item["title"])}</a><p>{esc(item["snippet"][:180])}</p>'
            )
        rows.append(cells(values))
    links = " · ".join(f'<a href="{p["id"]}.html">{esc(labels[p["method"]])}</a>' for p in peers)
    return (
        "<h2>"
        + t("Same-query results", "同一查询的结果对照")
        + "</h2><p>"
        + links
        + "</p>"
        + table(headings, rows)
    )


def latency_rounds(profile, rows, zh):
    """Expose repeated-query and reported cache state without inferring cold starts."""
    from collections import Counter

    def t(en, cn):
        return cn if zh else en

    entries = []
    for round_id in range(profile["protocol"]["rounds"]):
        for method in profile["methods"]:
            subset = [r for r in rows if r["round"] == round_id and r["method"] == method["id"]]
            if not subset:
                continue
            stats = aggregate(subset)[method["id"]]["all_latency"]
            caches = Counter((r.get("cache") or {}).get("state", "unknown") for r in subset)
            cache_text = ", ".join(f"{state}: {n}" for state, n in sorted(caches.items()))
            entries.append(
                cells(
                    [
                        str(round_id + 1),
                        esc(method["label"]),
                        str(len(subset)),
                        *[fmt(stats[k]) for k in ("mean", "p50", "p95")],
                        esc(cache_text),
                    ]
                )
            )
    return (
        "<h2>"
        + t("Repeated-query latency and reported cache state", "重复查询耗时与接口缓存标记")
        + "</h2>"
        + table(
            [
                t("Round", "轮次"),
                t("Method", "方法"),
                "N",
                t("Mean ms", "平均耗时 ms"),
                "P50 ms",
                "P95 ms",
                t("API cache state", "接口缓存标记"),
            ],
            entries,
        )
        + "<p>"
        + t(
            "All declared calls remain in the headline latency. Cache states come from the API response; unknown means the provider did not report one. The first round is not guaranteed to be a cold cache. Repeated queries describe reuse behavior, not sustained-load throughput.",
            "首页耗时保留全部预定调用。缓存标记直接取自接口响应；unknown 表示接口没有提供，不能据此判断是否缓存。第一轮也不保证冷缓存。重复查询反映复用表现，不代表持续负载下的吞吐量。",
        )
        + "</p>"
    )


def authored_sections(content, page, language, *, rows=(), heading_level=2):
    body = ""
    for section_index, s in enumerate(content.get("sections", [])):
        if s.get("page", "method") != page:
            continue
        body += (
            f'<section class="chapter" data-authored="{section_index}"><h{heading_level}>'
            + esc(s["title"][language])
            + f"</h{heading_level}>"
        )
        if s.get("diagram"):
            body += (
                '<div class="process"><div class="flow-fallback">'
                + "".join("<span>" + esc(step[language]) + "</span>" for step in s["steps"])
                + '</div><pre class="mermaid" hidden>'
                + esc(s["diagram"][language])
                + "</pre></div>"
            )
        for p in s.get("paragraphs", []):
            body += "<p>" + esc(p[language]) + "</p>"
        if s.get("rows"):
            body += table(
                s["headers"][language], [cells([esc(v) for v in r[language]]) for r in s["rows"]]
            )
        for sample in s.get("samples", []):
            body += (
                "<details><summary>"
                + esc(sample["label"][language])
                + "</summary><pre>"
                + esc(sample["code"])
                + "</pre></details>"
            )
        for specimen in s.get("specimens", []):
            row = next(
                (
                    r
                    for r in rows
                    if r["case_id"] == specimen["case_id"] and r["method"] == specimen["method"]
                ),
                None,
            )
            if row is None:
                raise ValueError("Missing measured specimen")
            item = row["results"][specimen.get("result_index", 0)]
            body += (
                f'<article class="specimen" data-specimen="{esc(row["id"])}"><h3>'
                + esc(specimen["label"][language])
                + "</h3><p>"
                + esc(item["title"])
                + '</p><p><a href="'
                + esc(item["url"])
                + '">'
                + esc(("查看来源网页 · " if language == "zh" else "Open source page · ")
                      + (urlsplit(item["url"]).hostname or ""))
                + "</a></p>"
            )
            for field in specimen["fields"]:
                value = item.get(field) or ""
                excerpt = (
                    value
                    if field in specimen.get("full_fields", [])
                    else value[: specimen.get("excerpt_chars", 450)]
                )
                body += (
                    "<p>"
                    + esc(field)
                    + " · "
                    + str(len(value))
                    + (" 字符" if language == "zh" else " characters")
                    + '</p><blockquote class="field-value">'
                    + esc(excerpt)
                    + "</blockquote>"
                )
            body += (
                '<p><a href="details/'
                + esc(row["id"])
                + '.html">'
                + ("查看该次完整响应" if language == "zh" else "Inspect the full measured response")
                + "</a></p></article>"
            )
        for link in s.get("sources", []):
            label = link["label"]
            if isinstance(label, dict):
                label = label[language]
            url = link["url"]
            if isinstance(url, dict):
                url = url[language]
            if link.get("evidence"):
                body += (
                    '<p><button data-evidence="' + esc(url) + '">' + esc(label) + "</button></p>"
                )
                continue
            body += f'<p><a href="{esc(url)}">{esc(label)}</a></p>'
        body += "</section>"
    return body


def provenance_label(kind, zh):
    return {
        "community_exact": ("Published community query", "社区公开原题"),
        "community_derived": ("Community-derived scenario", "社区场景改编"),
        "public_catalog": ("Public query catalog", "公开查询集"),
        "public_labeled": ("Public human-verified factual dataset", "公开人工核验事实题"),
        "curated": ("Curated supplement", "自建补充"),
        "user_screenshot": ("Exact query from supplied screenshot", "所附截图中的原题"),
    }.get(kind, (kind, kind))[int(zh)]


def search_category(value, zh):
    if not zh:
        return value.replace("_", " ")
    return {
        "web": "通用网页", "site": "指定网站", "vertical": "垂类搜索",
        "factual": "公开事实题",
        "known_item": "已知页面", "primary_source": "官方来源",
        "technical_exact": "技术报错", "semantic_long_tail": "长尾语义",
        "niche_small_web": "小众网站", "multi_constraint": "多条件查询",
        "exploratory_diversity": "探索式查询", "multilingual_regional": "多语言地区",
        "robustness_noisy": "口语与错别字", "freshness_rolling": "近期更新",
        "creator_discovery": "创作者发现", "chinese_service": "中文公共服务",
        "community_original": "社区原题", "wechat": "微信公众号",
        "xiaohongshu": "小红书", "zhihu": "知乎", "douyin": "抖音",
        "weibo": "微博", "bilibili": "B站", "douban": "豆瓣",
        "baijiahao": "百家号", "toutiao": "头条", "x": "X",
        "reddit": "Reddit", "instagram": "Instagram", "facebook": "Facebook",
        "linkedin": "LinkedIn", "medium": "Medium", "substack": "Substack",
    }.get(value, value)


def source_note(case, zh):
    if not zh:
        return case.get("derivation", case["origin"])
    return {
        "user_screenshot": "按所附截图逐字录入；站点命中不代表内容相关。",
        "community_exact": "公开材料中的原题；本轮三家请求相同数量的结果，可能与原作者设置不同。",
        "community_derived": "根据公开讨论另拟的查询，不是原作者的原题。",
        "public_catalog": "公开查询集原题；预定网址只用于检查指定页面发现。",
        "curated": "为覆盖该场景增补的查询。",
        "public_labeled": "公开人工整理的原题、答案及支持来源；按共同输入长度预先筛选。",
    }.get(case.get("provenance_kind", "curated"), "")


def provenance_table(profile, zh):
    counts = Counter(c.get("provenance_kind", "curated") for c in profile["cases"])
    return (
        "<h2>"
        + ("查询从哪里来" if zh else "Where the queries come from")
        + "</h2>"
        + table(
            ["来源类型" if zh else "Source type", "独立查询数" if zh else "Unique queries"],
            [cells([provenance_label(k, zh), str(v)]) for k, v in counts.items()],
        )
        + "<p>"
        + (
            f"共 {len(profile['cases'])} 条独立查询；重复调用不增加样本数。"
            if zh
            else f"{len(profile['cases'])} unique queries; repeated calls do not add queries."
        )
        + "</p>"
    )


def interpretation_summary_table(profile, selected, zh, content=None):
    content = content or {}
    selected = scoped_overview_rows(profile, selected, content)
    profile = dict(profile, methods=scoped_overview_methods(profile, content), cases=[c for c in profile["cases"] if c["id"] in {r["case_id"] for r in selected}])
    stats = aggregate(selected)
    cutoff = profile["protocol"]["top_k"]
    hit_key = "anchor_hit_5" if cutoff == 5 else "anchor_hit_10"
    measures = []
    for method in profile["methods"]:
        item = stats.get(method["id"])
        anchor = item["metrics"][hit_key] if item else {"mean": None, "n": 0}
        measures.append((
            method,
            item["nonempty"] if item else None,
            anchor["mean"],
            anchor["n"],
            item["all_latency"]["p50"] if item else None,
            item["all_latency"]["p95"] if item else None,
            item["cost_total"] if item else None,
        ))
    winners = {}
    for index, direction in ((1, max), (2, max), (4, min), (5, min)):
        values = [entry[index] for entry in measures]
        if len(values) > 1 and all(value is not None for value in values):
            best = direction(values)
            if values.count(best) == 1:
                winners[index] = best
    rows = []
    for method, nonempty, hit, n, p50, p95, cost in measures:
        values = [
            esc(method["label"]),
            f"{nonempty}/{len(profile['cases'])}" if nonempty is not None else "—",
            f"{hit * 100:.1f}% (n={n})" if hit is not None else "—",
            fmt(p50),
            fmt(p95),
            (f"{cost:.3f}" if cost is not None else "—") + " " + method["currency"],
        ]
        raw = [None, nonempty, hit, None, p50, p95, None]
        cells_html = []
        for column, label in enumerate(values):
            source_index = (0, 1, 2, 4, 5, 6)[column]
            win = source_index in winners and raw[source_index] == winners[source_index]
            cells_html.append('<td' + (' class="win"' if win else '') + '>' + ('★ ' if win else '') + label + '</td>')
        rows.append('<tr>' + ''.join(cells_html) + '</tr>')
    return (
        '<h2>' + ('基础接口的核心结果' if zh else 'Core API comparison') + '</h2>'
        + table(
            ['接口', '非空题数', f'已知页@{cutoff}', 'P50 ms', 'P95 ms', '本批估算费用']
            if zh else ['Provider', 'Nonempty queries', f'Known page@{cutoff}', 'P50 ms', 'P95 ms', 'Estimated cohort cost'],
            rows,
        )
        + '<p>' + (
            '★ 仅标出当前列唯一的较优数值；各列适用范围不同，不能合成总排名。费用是目录价估算，Lexmount 尚未报价。'
            if zh else '★ marks a unique favorable value within its column. Populations differ across columns, so these are not an overall ranking. Charges use list prices; Lexmount has no announced price.'
        ) + '</p>'
    )


def capability_map(profile, selected, zh):
    """Show actual delivered layers; a separate AI-answer endpoint is not implied."""
    heads = (
        ["比较接口", "链接与标题", "短摘要", "逐页长摘要", "网页正文", "跨页答案"]
        if zh else
        ["Tested endpoint", "URL & title", "Snippet", "Page summary", "Page body", "Answer"]
    )
    output = ['<div class="capmap" role="table" aria-label="' +
              ("本轮响应交付对比" if zh else "Observed response fields") + '">']
    output.extend('<div class="head" role="columnheader">' + esc(h) + '</div>' for h in heads)
    for method in profile["methods"]:
        fields = [item for row in selected if row["method"] == method["id"] for item in row["results"]]
        example_request = next((row["request"] for row in selected if row["method"] == method["id"] and row.get("request")), {})
        request_mode = ", ".join(
            f"{key}={str(example_request[key]).lower()}"
            for key in ("limit", "count", "need_content", "summary")
            if key in example_request
        )
        delivered = [
            any(item.get("url") and item.get("title") for item in fields),
            any(item.get("snippet") for item in fields),
            any(item.get("summary") for item in fields),
            any(item.get("content") for item in fields),
            False,
        ]
        output.append('<div class="name" role="rowheader">' + esc(method["label"]) + '<br><small>' + esc(request_mode) + '</small></div>')
        for present in delivered:
            output.append('<div role="cell" class="' + ('yes' if present else 'no') + '">' +
                          (("有" if zh else "Yes") if present else ("未交付" if zh else "No")) + '</div>')
    output.append('</div><p class="muted">' + (
        "按所列配置的原始响应统计；“有”表示至少一次交付该字段，不表示每条都有。博查 AI Search 是另一个可生成答案的接口。"
        if zh else "Based on the listed configurations: Yes means the field was delivered at least once, not in every result. Bocha AI Search is a distinct answer-generating endpoint."
    ) + '</p>')
    return ''.join(output)


def relevance_panel(profile, scored, evidence, zh):
    """Explain and visualize judged preview relevance without conflating it with recall."""
    if not scored:
        return ""
    methods = profile["methods"]
    rows = []
    families = [family for family in ("web", "site", "vertical") if any(row["family"] == family for row in scored)]
    for family in families:
        scoped = [row for row in scored if row["family"] == family]
        summaries = relevance_summary(scoped, [m["id"] for m in methods])
        for method in methods:
            stats = summaries[method["id"]]
            rows.append(cells([esc(search_category(family, zh)), esc(method["label"]),
                               str(stats["n"]), f'{stats["ndcg"]:.3f}', f'{stats["precision"]:.1%}']))
    overall = relevance_summary(scored, [m["id"] for m in methods])
    bars = '<div class="metric-bars">' + ''.join(
        '<div class="item"><strong>' + esc(m["label"]) + '</strong><span class="track"><span class="fill" style="width:'
        + f'{overall[m["id"]]["ndcg"]*100:.1f}%\"></span></span><small>nDCG@5 '
        + f'{overall[m["id"]]["ndcg"]:.3f}' + '</small></div>' for m in methods
    ) + '</div>'
    audit = evidence["judge"]
    crosscheck = evidence.get("crosscheck_labels", {})
    agreement = sum(grade == evidence["labels"][case_id][candidate_id]
                    for case_id, grades in crosscheck.items() for candidate_id, grade in grades.items())
    crosscheck_total = sum(len(grades) for grades in crosscheck.values())
    return ('<section class="specimen" data-relevance><h3>'
            + ((f"Search-preview relevance: {len(evidence['plan']['case_ids'])} blind-judged queries") if not zh else f"搜索结果预览相关性：{len(evidence['plan']['case_ids'])} 题匿名评分")
            + '</h3>' + bars
            + '<p>' + (("The overall bars describe this selected sample; the category table is the decision view. nDCG@5 rewards useful pages near the top; Precision@5 counts pages graded at least 2 among five slots. Both use URL, title and snippet only. "
                        + ", ".join(f"{search_category(family, False)}: {sum(r['family']==family for r in scored)//len(methods)}" for family in families) + ".")
                       if not zh else "上图汇总本次选定样本；判断具体场景请看下表。nDCG@5 重视有用结果是否排在前面，Precision@5 是前五个位置中至少达到 2 分的比例。评分只看网址、标题和摘要。题型："
                       + "、".join(f"{search_category(family, True)} {sum(r['family']==family for r in scored)//len(methods)} 题" for family in families) + "。")
            + '</p>'
            + table(["Category" if not zh else "题型", "Provider" if not zh else "服务", "N", "nDCG@5", "Precision@5"], rows)
            + '<p class="muted">'
            + (f"Blind scorer: {esc(audit['model'])}. A separate Codex spot check of {len(crosscheck)} queries agreed exactly on {agreement}/{crosscheck_total} candidate grades. These are model judgments, not independent human relevance labels, page-body checks or answer accuracy."
               if not zh else f"匿名评分使用 {esc(audit['model'])}；另由 Codex 抽查 {len(crosscheck)} 题、{crosscheck_total} 个候选结果，完全同分 {agreement} 个。两轮都是模型判断，不能当作独立人工相关性标注、网页全文核验或答案准确率。")
            + '</p><a href="relevance-judgments.json">'
            + ("Scoring evidence" if not zh else "查看逐项评分证据")
            + '</a></section>')


def factual_panel(profile, selected, zh):
    cases = {c['id']: c for c in profile['cases'] if 'reference_answer' in c}
    if not cases:
        return ''
    projected = bool(profile.get("comparison_selection"))
    baseline = profile['methods'] if projected else [m for m in profile['methods'] if not any(m.get('output_options', {}).values())]
    answer_key = 'answer_in_delivered_text_5' if projected else 'answer_in_preview_5'
    keys = ('labeled_source_hit_5', 'labeled_source_rr_5', answer_key)
    values = []
    by_method = {}
    for method in profile['methods']:
        group = {r['case_id']: r for r in selected if r['method'] == method['id'] and r['case_id'] in cases}
        by_method[method['id']] = group
        if method in baseline:
            values.append((method['label'], len(group), [sum(r['metrics'][k] for r in group.values()) for k in keys] + [sum(factual_sensitivity(cases[r['case_id']], r['results'])['metrics'][answer_key] for r in group.values())]))
    rendered = []
    for label, n, sums in values:
        row = ['<td>' + esc(label) + '</td>', f'<td>{n}</td>']
        for i, total in enumerate(sums):
            fractions = [v[2][i] / v[1] for v in values if v[1]]
            best = bool(i != 2 and n and fractions.count(max(fractions)) == 1 and total / n == max(fractions))
            value = (f'{total/n:.3f}' if i == 1 else f'{int(total)}/{n} · {total/n:.1%}') if n else '—'
            row.append('<td' + (' class="win"' if best else '') + '>' + ('★ ' if best else '') + value + '</td>')
        rendered.append('<tr>' + ''.join(row) + '</tr>')
    body = '<section data-factual><h3>' + (
        ('公开事实题：来源与答案线索' if zh else 'Public factual questions: sources and answer cues') if projected else ('公开事实题：三家基础接口的证据发现' if zh else 'Public factual questions: basic-API evidence discovery')
    ) + '</h3><p>' + (
        '前五条中找到题库列出的任一支持网址，就记一次来源命中。MRR 是首个命中位置的倒数：第 1 条得 1，第 2 条得 0.5，未命中得 0，再对全部题取平均。答案线索只检查三家共有的标题与短摘要。失败、空结果和不足五条都保留，不补齐、不换题。'
        if zh else
        'A source hit means at least one published supporting URL appears in the top five. MRR averages reciprocal first-hit rank: 1 for rank one, 0.5 for rank two and zero for a miss. Answer cues use only shared titles and snippets. Failures, empties and short lists remain in the population.'
    ) + '</p>' + table(
        ['基础接口', '题数', '找到标注来源', '来源排名 MRR', '短摘要含答案：原始', '排除题库镜像后'] if zh else
        ['Basic API', 'Queries', 'Published source found', 'Source MRR', 'Preview answer: raw', 'After mirror exclusion'], rendered)
    if projected:
        body = body.replace('答案线索只检查三家共有的标题与短摘要。', '答案线索检查各家前五条实际交付的标题、短摘要、长摘要和正文。').replace('Answer cues use only shared titles and snippets.', 'Answer cues use every delivered title, snippet, summary and body in the top five.').replace('基础接口</th>', '方法</th>').replace('Basic API</th>', 'Method</th>').replace('短摘要含答案：原始', '返回文字含答案：原始').replace('Preview answer: raw', 'Delivered answer cue: raw')
    body += '<p>' + ('排除镜像是事后诊断：网址或标题含 SimpleQA（包括空格、连字符和下划线变体）的结果不参与答案检查，原位置留空，不补搜。只识别显式题库镜像，不能证明已排除所有转载；原始值同时保留。' if zh else 'Mirror exclusion is a post-hoc diagnostic. Results naming SimpleQA in their URL or title, including spacing/hyphen/underscore variants, are blanked without refilling or compacting ranks. This detects explicit mirrors, not every possible reproduction; raw values remain visible.') + '</p>'
    extra = []
    for enabled in ([] if projected else profile['methods']):
        options = enabled.get('output_options', {})
        if not any(options.values()):
            continue
        disabled = next((m for m in baseline if m['adapter'] == enabled['adapter'] and m.get('output_options') == {k: False for k in options}), None)
        if disabled is None:
            continue
        a, b = by_method[disabled['id']], by_method[enabled['id']]
        if set(a) != set(b):
            raise ValueError('Incomplete factual content pair')
        a = {cid: dict(r, metrics=factual_sensitivity(cases[cid], r['results'])['metrics']) for cid,r in a.items()}
        b = {cid: dict(r, metrics=factual_sensitivity(cases[cid], r['results'])['metrics']) for cid,r in b.items()}
        key = 'answer_in_delivered_text_5'
        extra.append(cells([esc(enabled['label']), str(len(a)),
            str(sum(r['metrics'][key] for r in a.values())), str(sum(r['metrics'][key] for r in b.values())),
            str(sum(not a[c]['metrics'][key] and b[c]['metrics'][key] for c in a)),
            str(sum(a[c]['metrics'][key] and not b[c]['metrics'][key] for c in a))]))
    if extra:
        body += '<h3>' + ('开关实验：全部交付文字中的答案线索' if zh else 'Option experiment: answer cues in all delivered text') + '</h3><p>' + (
            '这里加入实际返回的 summary 和 content，并排除上述显式题库镜像。表中是题数：新增指关闭时没有、开启时出现；丢失相反。两次搜索可能返回不同网页，因此这是接口开关后的总体变化，不是固定同一网页后的纯正文增益。'
            if zh else
            'This includes returned summaries and bodies after the same explicit-mirror exclusion. Added means absent when disabled and present when enabled; lost is the reverse. Search results can change, so this is the overall option effect rather than a fixed-page body-only gain.'
        ) + '</p>' + table(['开启模式', '配对题数', '关闭时命中', '开启时命中', '新增', '丢失'] if zh else
            ['Enabled mode', 'Pairs', 'Disabled hits', 'Enabled hits', 'Added', 'Lost'], extra)
    if any(c.get('answer_match_rule') == 'published-answers/2' for c in cases.values()):
        return body + '<p class="muted">' + (
            '答案检查使用题库明确列出的答案、别名和允许数值范围，并保留单位、百分号等数量含义；只在单个返回字段内匹配，不跨字段拼接。未明确给出的同义说法不会推断为命中。否定句或无关上下文仍可能误中，因此这是文字证据检查，不是语义正确率。逐题页可核对标准答案、来源与实际输出。'
            if zh else
            'Answer checks use the published answer, explicit aliases and allowed numeric ranges, preserving units and percentage modifiers. Matches stay within one returned field; fields are never joined. Unspecified synonyms are not inferred. Negated or unrelated mentions can still match, so this is lexical evidence rather than semantic accuracy. Details expose the reference answer, sources and actual output.'
        ) + '</p></section>'
    return body + '<p class="muted">' + (
        '答案检查统一 Unicode、大小写及标点，要求完整词组在单个字段中连续出现；不会跨字段拼出答案。否定句或无关上下文可能误中，同义表达可能漏计。因此本表是来源与文字证据检查，不是语义正确率。所有题的标准答案、来源和实际输出均可在逐题页核对。'
        if zh else
        'Answer matching normalizes Unicode, case and punctuation and requires consecutive whole tokens within one field. Negated or unrelated mentions can match; paraphrases can miss. These are source and lexical checks, not semantic accuracy. Every detail exposes the answer, sources and output.'
    ) + '</p></section>'


def mode_panel(profile, selected, zh):
    methods = profile["methods"]
    pairs = []
    for enabled in methods:
        options = enabled.get("output_options", {})
        if not options or not any(options.values()):
            continue
        disabled = next((m for m in methods if m["adapter"] == enabled["adapter"]
                         and m.get("output_options") == {k: False for k in options}), None)
        if disabled:
            pairs.append((disabled, enabled))
    if not pairs:
        return ""
    groups = list(dict.fromkeys(r.get("cohort", "primary") for r in selected))
    output = []
    for group in groups:
        for disabled, enabled in pairs:
            a = {r["case_id"]: r for r in selected if r["method"] == disabled["id"] and r.get("cohort", "primary") == group}
            b = {r["case_id"]: r for r in selected if r["method"] == enabled["id"] and r.get("cohort", "primary") == group}
            if set(a) != set(b) or not a:
                raise ValueError("Incomplete paired output-mode comparison")
            deltas = [b[k]["elapsed_ms"] - a[k]["elapsed_ms"] for k in a]
            changes = sum([canonical_url(x["url"]) for x in a[k]["results"]] != [canonical_url(x["url"]) for x in b[k]["results"]] for k in a)
            output.append(cells([
                esc(cohort_label(profile, group, zh)), esc(enabled["label"]), str(len(a)),
                fmt(sum(deltas) / len(deltas)),
                fmt(median(deltas)),
                f"{sum(d > 0 for d in deltas)}/{len(deltas)}",
                f"{changes}/{len(a)}",
            ]))
    return '<section data-modes><h3>' + (
        "开启额外内容，实际多等多久？" if zh else "What does enabling extra text change?"
    ) + "</h3><p>" + (
        "同题相减：开启耗时减去关闭耗时，正数表示开启更慢。各题只调用一次，顺序轮换；两次查询也可能因索引或缓存返回不同结果，所以同时列出网址或排序变化的题数。"
        if zh else
        "Paired difference = enabled minus disabled latency; positive means slower with extra text. Each mode runs once per query in rotating order. Index or cache changes can alter results, so URL/order changes are shown too."
    ) + "</p>" + table(
        ["题组", "开启模式", "配对题数", "平均差 ms", "中位差 ms", "开启更慢", "网址／排序变化"] if zh else
        ["Cohort", "Enabled mode", "Pairs", "Mean Δ ms", "Median Δ ms", "Enabled slower", "URL/order changed"],
        output,
    ) + "</section>"


def known_page_chart(profile, selected, zh):
    """A scoped discovery check, deliberately separate from relevance quality."""
    key = "anchor_hit_5" if profile["protocol"]["top_k"] == 5 else "anchor_hit_10"
    stats = aggregate(selected)
    output = ['<h3>' + ("已知页面发现" if zh else "Known-page discovery") + '</h3>']
    cutoff = profile["protocol"]["top_k"]
    output.append('<p>' + (
        f"设有参考网页的题用于检查找页能力：前 {cutoff} 条出现目标网址即命中。每行标出命中数与题数；没有指定网页的题不进入这一分母。公开事实题另有来源与答案检查。"
        if zh else
        f"Known-page tasks test discovery: a target URL in the first {cutoff} results counts as a hit. Each row shows hits and eligible tasks; questions without a declared target are excluded. Public factual questions have separate source and answer checks."
    ) + '</p>')
    output.append('<div class="metric-bars" data-known-page-chart>')
    for method in profile["methods"]:
        metric = stats[method["id"]]["metrics"][key]
        n, rate = metric["n"], metric["mean"]
        if not n or rate is None:
            continue
        hits = round(rate * n)
        label = f"{hits}/{n} · {rate:.1%}"
        output.append('<div class="item"><strong>' + esc(method["label"]) +
                      '</strong><div class="track"><span class="fill" style="width:' +
                      f'{rate*100:.1f}' + '%"></span></div><small>' + label + '</small></div>')
    output.append('</div><p class="muted">' + (
        "这些题按用途选取，并非随机抽取的用户流量。找不到指定页不代表其他结果无用；本图不作为整体相关性排名。"
        if zh else "These are purposively selected tasks, not random user traffic. Missing the target does not make other results irrelevant; this is not an overall relevance ranking."
    ) + '</p>')
    return ''.join(output)


def outcome_table(profile, selected, zh):
    cases = {c["id"]: c for c in profile["cases"]}
    names = {
        "api_error": ("API / transport error", "接口或传输失败"),
        "empty": ("Empty response", "空结果"),
        "domain_mismatch": ("Off-domain URL returned", "返回链接不符合指定域名"),
        "target_miss": ("Known target absent", "未命中已知目标"),
        "target_hit": ("Known target found", "命中已知目标"),
        "source_hit": ("Published supporting source found", "找到题库标注来源"),
        "source_miss": ("Published supporting source not found", "未找到题库标注来源"),
        "probe": ("Unjudged query response", "无相关性标注的查询响应"),
    }
    rows = []
    for m in profile["methods"]:
        group = [r for r in selected if r["method"] == m["id"]]
        counts = Counter(classify(cases[r["case_id"]], r)[1] for r in group)
        for kind, n in counts.items():
            rows.append(cells([esc(m["label"]), names[kind][int(zh)], str(n), str(len(group))]))
    return (
        "<h2>"
        + ("首轮诊断主因" if zh else "Primary outcome category")
        + "</h2>"
        + table(
            ["方法", "规则分类", "查询数", "本方法查询总数"]
            if zh
            else ["Method", "Machine category", "Queries", "Method total"],
            rows,
        )
        + "<p>"
        + (
            "此表每题只归一类：接口错误、空结果、域名不符、已知目标。找到目标页却混入其他域名的题会归入“域名不符”，所以这里的“命中已知目标”可能少于上方独立的目标页命中数。未命中目标不等于其他结果无关。"
            if zh
            else "Each query receives one primary category: API error, empty list, off-domain result, then known-page discovery. A result that finds its target but leaks another domain is counted as off-domain here; the separate known-page metric still counts it as a hit. Missing the target does not make other pages irrelevant."
        )
        + "</p>"
    )


def domain_discovery_table(profile, selected, zh):
    cases = [c for c in profile["cases"] if c.get("domains")]
    if not cases or not selected:
        return ""
    lookup = {(r["case_id"], r["method"]): r for r in selected}
    entries = []
    for case in cases:
        values = [esc(case["query"])]
        for method in profile["methods"]:
            row = lookup[(case["id"], method["id"])]
            ranks = [
                i
                for i, result in enumerate(row["results"][:10], 1)
                if any(in_domain(result["url"], domain) for domain in case["domains"])
            ]
            label = f"{len(ranks)}/{len(row['results'][:10])}; " + (
                f"#{ranks[0]}" if ranks else ("未出现" if zh else "absent")
            )
            values.append(f'<a href="details/{row["id"]}.html">{label}</a>')
        entries.append(cells(values))
    return (
        "<h2>"
        + ("指定平台有没有出现" if zh else "Did the requested platform appear?")
        + "</h2>"
        + "<p>"
        + (
            "首轮，单元格为指定域名链接数／返回数及首次出现排名。域名匹配不证明主题、账号、时间条件或文章正文正确；0/0 表示空响应。"
            if zh
            else "First round: requested-domain links / returned links, followed by first rank. Host matching does not validate the topic, account, date or article body. 0/0 denotes an empty response."
        )
        + "</p>"
        + table(["查询" if zh else "Query"] + [m["label"] for m in profile["methods"]], entries)
    )


def performance_view(profile, diagnostics, zh, evidence_prefix=""):
    def t(en, cn):
        return cn if zh else en

    methods = diagnostics["methods"]
    body = "<h2>" + t("Client time and provider-reported work", "客户端耗时与服务端处理") + "</h2>"
    rows = []
    for m in profile["methods"]:
        s = methods.get(m["id"])
        if not s:
            continue
        rows.append(
            cells(
                [esc(m["label"])]
                + [
                    fmt(s[k]["mean"]) + f" (n={s[k]['n']})"
                    for k in (
                        "client_ms",
                        "server_ms",
                        "outside_server_ms",
                        "ttfb_ms",
                        "body_ms",
                        "bytes",
                    )
                ]
            )
        )
    body += table(
        [
            t("Method", "方法"),
            t("Mean total ms", "总耗时均值 ms"),
            t("Mean server ms", "服务端均值 ms"),
            t("Mean remainder ms", "其余耗时均值 ms"),
            t("Mean TTFB ms", "首字节均值 ms"),
            t("Mean body transfer ms", "正文传输均值 ms"),
            t("Mean response bytes", "平均响应字节数"),
        ],
        rows,
    )
    body += (
        "<p>"
        + t(
            "All logical requests with available telemetry are included; n states the available population. Server time is provider-reported and unavailable where no comparable field exists. The remainder includes network transit and client setup; it is not pure network latency. TTFB and body transfer are sequential; DNS/connect/TLS clocks are cumulative and must not be added.",
            "有遥测数据的题目均计入，n 是该项可用样本数。服务端耗时取自接口报告；没有可比字段时不填。其余耗时包含网络往返和客户端启动，不等于纯网络延迟。首字节与正文传输先后发生；DNS、连接、TLS 计时为累计值，不能相加。",
        )
        + "</p>"
    )
    max_ms = max((s["client_ms"]["mean"] for s in methods.values()), default=1) or 1
    for m in profile["methods"]:
        s = methods.get(m["id"])
        if s:
            body += f'<p>{esc(m["label"])} · {fmt(s["client_ms"]["mean"])} ms</p><div class="bar" style="width:{100 * s["client_ms"]["mean"] / max_ms:.2f}%" aria-hidden="true"></div>'
    body += (
        "<h2>"
        + t("Work performed across the full call budget", "完整调用预算内的上游工作量")
        + "</h2>"
    )
    rows = []
    for m in profile["methods"]:
        s = methods.get(m["id"])
        if s:
            rows.append(
                cells(
                    [
                        esc(m["label"]),
                        fmt(s["attempts"]["mean"]),
                        esc(
                            ", ".join(f"{k}: {v}" for k, v in s["outcomes"].items())
                            or t("Not exposed", "未公开")
                        ),
                        esc(", ".join(f"{k}: {v}" for k, v in s["cache"].items())),
                        esc(
                            ", ".join(f"{k}: {v}" for k, v in s["usage"].items())
                            or t("Not exposed", "未公开")
                        ),
                    ]
                )
            )
    body += table(
        [
            t("Method", "方法"),
            t("Mean recorded attempts/call", "每调用平均已记录尝试数"),
            t("Attempt outcomes", "上游尝试结果"),
            t("API cache states", "接口缓存标记"),
            t("Reported usage units", "接口报告的用量"),
        ],
        rows,
    )
    body += (
        "<p>"
        + t(
            "Attempt durations overlap. Their sum is work, not user waiting time. Usage units have no monetary conversion until internal prices are supplied. Missing upstream telemetry does not mean zero upstream work.",
            "上游尝试可能并行，耗时之和不能当成用户等待时间。用量单位没有内部单价，暂不折算成金额。没有上游遥测不代表没有上游工作。",
        )
        + "</p>"
    )
    candidates = [c for c in diagnostics["calls"] if c["after_first_ms"] is not None]
    if candidates:
        # Fixed selection rule: largest observed post-first-result wait, not a quality winner.
        specimen = max(candidates, key=lambda c: c["after_first_ms"])
        body += (
            "<h2>"
            + t("Longest wait after the first upstream result", "已有上游结果后，等待最长的一次")
            + "</h2>"
        )
        body += (
            f'<section class="specimen" data-timing-specimen="{esc(specimen["id"])}"><p>{esc(specimen["case_id"])} · {esc(specimen["method"])} · '
            + t("round", "轮次")
            + f" {specimen['round'] + 1}</p>"
        )
        body += (
            "<p>"
            + t(
                "First upstream results / server completion / additional wait: ",
                "首个上游返回结果 / 服务端结束 / 继续等待：",
            )
            + f"{fmt(specimen['first_results_ms'])} / {fmt(specimen['server_ms'])} / {fmt(specimen['after_first_ms'])} ms</p>"
        )
        maximum = max([a["start"] + a["duration"] for a in specimen["timeline"]] + [1])
        rows = []
        for a in specimen["timeline"]:
            bar = f'<div class="timeline"><i class="{"good" if a["outcome"] == "results" else ""}" style="left:{100 * a["start"] / maximum:.2f}%;width:{100 * a["duration"] / maximum:.2f}%"></i></div>'
            rows.append(
                cells(
                    [
                        esc(a["route"]),
                        str(a["start"]),
                        str(a["duration"]),
                        esc(a["outcome"]),
                        str(a["count"]),
                        bar,
                    ]
                )
            )
        body += table(
            [
                t("Route", "上游路线"),
                t("Start ms", "启动 ms"),
                t("Duration ms", "耗时 ms"),
                t("Outcome", "结果"),
                t("Parsed items", "解析条数"),
                t("Parallel timeline", "并行时间线"),
            ],
            rows,
        )
        body += (
            f'<button data-evidence="{evidence_prefix}{esc(specimen["evidence_path"])}">'
            + t("Open exact response", "打开这次原始响应")
            + "</button></section>"
        )
        body += (
            "<p>"
            + t(
                "A first upstream result may contain too few or off-topic items. The extra wait is an observed scheduling interval, not a proven removable speedup. Any early-stop policy must be tested against the same quality requirements.",
                "首个上游结果可能条数不足或不相关。“继续等待”是已观测到的调度时段，不是可直接削掉的耗时。提前结束策略仍需在相同质量要求下验证。",
            )
            + "</p>"
        )
    return body


def scoped_overview_methods(profile, content):
    ids = content.get("overview_methods")
    if ids is None:
        return profile["methods"]
    if not ids or len(ids) != len(set(ids)) or not set(ids).issubset({m["id"] for m in profile["methods"]}):
        raise ValueError("Invalid overview participants")
    return [m for m in profile["methods"] if m["id"] in ids]


def scoped_overview_rows(profile, rows, content):
    cohort = content.get("overview_cohort")
    if cohort and cohort not in {g["id"] for g in profile.get("report_cohorts", [])}:
        raise ValueError("Overview cohort is not registered")
    ids = {m["id"] for m in scoped_overview_methods(profile, content)}
    return [r for r in rows if r["method"] in ids and (not cohort or r.get("cohort") == cohort)]


def project_comparison(profile, rows, content):
    """Project an already-validated collection without changing measured calls."""
    ids = content.get("comparison_methods")
    if ids is None:
        return profile, rows
    methods = {m["id"]: m for m in profile["methods"]}
    if not ids or len(ids) != len(set(ids)) or not set(ids).issubset(methods):
        raise ValueError("Invalid comparison participants")
    selected = [r for r in rows if r["method"] in ids]
    if rows:
        expected = {(m, c["id"], round_number) for m in ids for c in profile["cases"]
                    for round_number in range(profile["protocol"]["rounds"])}
        actual = [(r["method"], r["case_id"], r["round"]) for r in selected]
        if set(actual) != expected or len(actual) != len(expected):
            raise ValueError("Incomplete comparison participant matrix")
    projected = copy.deepcopy(profile)
    projected["methods"] = [dict(methods[mid], label=content.get("comparison_labels", {}).get(mid, methods[mid]["label"])) for mid in ids]
    projected["comparison_selection"] = {"method_ids": ids, "collection_method_ids": list(methods)}
    return projected, selected


def metric_populations(profile, content):
    cases = {c["id"] for c in profile["cases"]}
    populations = content.get("metric_populations") or [
        {"id": "overall", "label": {"en": "All questions", "zh": "总体"}, "case_ids": [c["id"] for c in profile["cases"]]}
    ]
    seen = set()
    for population in populations:
        ids = population.get("case_ids", [])
        if (not population.get("id") or population["id"] in seen or not ids
                or len(ids) != len(set(ids)) or not set(ids).issubset(cases)):
            raise ValueError("Invalid metric population")
        seen.add(population["id"])
        if population["id"] == "overall" and set(ids) != cases:
            raise ValueError("Overall population must include every query")
        if population["id"] == "factual" and set(ids) != {c["id"] for c in profile["cases"] if "reference_answer" in c}:
            raise ValueError("Factual population must include every labeled question")
    if content.get("independent_benchmarks"):
        roots = [set(p["case_ids"]) for p in populations if p.get("benchmark_root")]
        if not roots or set().union(*roots) != cases or sum(map(len, roots)) != len(cases):
            raise ValueError("Independent benchmark roots must partition every query")
    elif populations[0]["id"] != "overall":
        raise ValueError("Overall population must be first")
    return populations


def population_summary(profile, selected, content, zh):
    lang = "zh" if zh else "en"
    independent = content.get("independent_benchmarks")
    heading = (("两套评测，分别报告" if zh else "Two benchmarks, reported separately")
               if independent else ("总体与分题组结果" if zh else "Overall and population results"))
    factual_count = sum("reference_answer" in case for case in profile["cases"])
    scenario_count = len(profile["cases"]) - factual_count
    note = ((f"{scenario_count} 题自建集和 {factual_count} 题公开事实集回答不同问题，不合并成总分。子题组只解释各自适用的检查。非空、平均耗时、P50 和 P95 都包含失败与重试等待。"
             if zh else f"The {scenario_count}-query in-house set and {factual_count}-query public factual set answer different questions and are not merged. Subgroups only explain their eligible checks. Nonempty and latency include failures and retry waits.")
            if independent else
            ("总体包含每道查询一次；分题组是总体的子集，不相加。非空指至少返回一条可用结果。平均、P50（中位数）和 P95（95% 的查询耗时不超过此值）均包含失败和重试等待。费用为本题组全部调用的可核算费用，未定价显示 —。"
             if zh else "Overall counts every query once. Subgroups overlap with overall and must not be added. Nonempty means at least one usable result. Mean, P50 (median) and P95 (95% of queries finish within this duration) include failures and retry waits. Cost totals billable calls; unset prices show —."))
    body = '<section data-populations><h2>' + heading + '</h2><p class="population-note">' + note + '</p>'
    for population in metric_populations(profile, content):
        ids = set(population['case_ids'])
        subset = [r for r in selected if r['case_id'] in ids]
        stats = aggregate(subset)
        kind = population.get('metric_kind', population['id'])
        quality = kind in ('factual', 'scenario', 'site')
        cases = {c['id']: c for c in profile['cases']}
        values = []
        for method in profile['methods']:
            s = stats.get(method['id'])
            if not s or s['n'] != len(ids):
                raise ValueError('Incomplete metric population')
            group = [r for r in subset if r['method'] == method['id']]
            if kind == 'factual':
                measures = [sum(r['metrics']['labeled_source_hit_5'] for r in group), sum(factual_sensitivity(cases[r['case_id']], r['results'])['metrics']['answer_in_delivered_text_5'] for r in group)]
            elif kind == 'scenario':
                hit_key = 'anchor_hit_5' if profile['protocol']['top_k'] == 5 else 'anchor_hit_10'
                if any(r['metrics'].get(hit_key) is None or r['metrics'].get('reciprocal_rank') is None for r in group):
                    raise ValueError('Scenario population requires a reference for every query')
                measures = [sum(r['metrics'][hit_key] for r in group), sum(r['metrics']['reciprocal_rank'] for r in group) / len(ids)]
            elif kind == 'site':
                eligible = []
                for row in group:
                    value = row['metrics'].get('domain_precision')
                    if value is None and row['metrics'].get('result_count') == 0:
                        value = 0
                    if value is None:
                        raise ValueError('Site population has an invalid domain metric')
                    eligible.append(value)
                measures = [sum(value > 0 for value in eligible), sum(eligible) / len(eligible)]
            else:
                measures = [s['nonempty']]
            values.append([method['label'], *measures, s['all_latency']['mean'], s['all_latency']['p50'], s['all_latency']['p95'], s['cost_total']])
        winners = {}
        for index in range(1, len(values[0]) - 1):
            candidates = [row[index] for row in values]
            best = (max if index <= (2 if quality else 1) else min)(candidates)
            if len(candidates) > 1 and candidates.count(best) == 1:
                winners[index] = best
        rendered = []
        for row, method in zip(values, profile['methods'], strict=True):
            html_cells = []
            for i, value in enumerate(row):
                if i == 0:
                    label = esc(value)
                elif i == 1 or (kind == 'factual' and i == 2):
                    label = f'{int(value)}/{len(ids)}' + (f' · {value / len(ids):.1%}' if quality else '')
                elif kind == 'scenario' and i == 2:
                    label = f'{value:.3f}'
                else:
                    label = fmt(value)
                if i == len(row) - 1:
                    label += ' ' + esc(method.get('currency', 'CNY'))
                win = i in winners and winners[i] == value
                html_cells.append('<td' + (' class="win"' if win else '') + '>' + ('★ ' if win else '') + label + '</td>')
            rendered.append('<tr>' + ''.join(html_cells) + '</tr>')
        body += '<article data-population="' + esc(population['id']) + '"><h3>' + esc(population['label'][lang]) + f' · {len(ids)}' + (' 题' if zh else ' queries') + '</h3>'
        if population.get('description'):
            body += '<p>' + esc(population['description'][lang]) + '</p>'
        headings = ['非空题数'] if zh else ['Nonempty queries']
        if kind == 'factual':
            body += '<p class="population-note">' + ('来源命中：前五条找到题库标注网址。答案线索：前五条交付文字中出现题库允许的答案，排除显式题库镜像后统计；不判定语义正确性。' if zh else 'Source found: a published supporting URL appears in the top five. Answer cue: allowed reference-answer text appears in delivered text after excluding explicit dataset mirrors; this is not semantic accuracy.') + '</p>'
            headings = ['找到标注来源', '答案线索（排除镜像）'] if zh else ['Published source found', 'Answer cue (mirrors excluded)']
        elif kind == 'scenario':
            body += '<p class="population-note">' + ('目标命中：前五条找到参考页面。MRR 衡量首个目标位置，第一条得 1、第二条得 0.5、未找到得 0，再对全部题取平均；越高表示目标越靠前。' if zh else 'Target found: a reference page appears in the top five. MRR averages reciprocal first-target rank: 1 for first, 0.5 for second, and 0 for a miss. Higher means targets appear earlier.') + '</p>'
            headings = ['找到目标页面', '目标排名 MRR'] if zh else ['Reference target found', 'Target MRR']
        elif kind == 'site':
            body += '<p class="population-note">' + ('站点满足：前五条至少一条来自题目指定域名；域名占比是前五条中指定域名结果的平均比例。两项都不判断页面内容是否相关。' if zh else 'Site satisfied: at least one top-five result uses the requested domain. Domain fraction averages the share of top-five results from that domain. Neither metric judges page relevance.') + '</p>'
            headings = ['满足站点约束', '指定域名占比'] if zh else ['Site constraint met', 'Requested-domain fraction']
        headers = (['方法'] + headings + ['平均耗时 ms','P50 ms','P95 ms','本组费用']) if zh else (['Method'] + headings + ['Mean ms','P50 ms','P95 ms','Population cost'])
        body += table(headers, rendered) + '</article>'
    return body + '</section>'


def render_result_comparison(profile, selected, zh, *, prefix="", site_prefix="", content=None):
    from .comparison import render_comparison
    profile = dict(profile, comparison_populations=metric_populations(profile, content or {}))
    return render_comparison(profile, selected, zh, detail_link=lambda row: posixpath.join(site_prefix, prefix, "details", row["id"] + ".html"))


def scenario_panel(profile, selected, content, zh):
    population = next((p for p in metric_populations(profile, content) if p["id"] == "scenario"), None)
    if population is None:
        return ""
    ids = set(population["case_ids"])
    scoped_profile = dict(profile, cases=[c for c in profile["cases"] if c["id"] in ids])
    scoped_rows = [r for r in selected if r["case_id"] in ids]
    return '<section data-scenario><h3>' + ("场景题：目标网页发现" if zh else "Scenario questions: target-page discovery") + '</h3>' + known_page_chart(scoped_profile, scoped_rows, zh) + '<p class="muted">' + ("只判断参考目标页面是否进入前五条；其它网页也可能有用，因此这不是整体相关性分数。" if zh else "This checks whether a declared target page appears in the top five. Other pages may also be useful, so it is not an overall relevance score.") + '</p></section>'


def population_filter(profile, content, zh):
    if content.get("presentation") != "comparison-report":
        return ""
    return '<label>' + ("统计题组" if zh else "Metric population") + '<select data-population-filter><option value="all">' + ("全部" if zh else "All") + '</option>' + ''.join('<option value="' + esc(p["id"]) + '" data-case-ids="' + esc(json.dumps(p["case_ids"])) + '">' + esc(p["label"]["zh" if zh else "en"]) + f' ({len(p["case_ids"])})</option>' for p in metric_populations(profile, content) if p["id"] != "overall") + '</select></label>'


def annotation_detail(annotation, zh):
    text = ("本题参考页面在接口采集后，依据原始问题独立补充；没有重新调用搜索接口。" if zh else "This reference was added independently from the original query after collection. Search APIs were not called again.")
    body = '<section class="specimen"><h3>' + ("后补参考依据" if zh else "Additional reference evidence") + '</h3><p>' + text + '</p>'
    for source in annotation['sources']:
        body += '<p><a href="' + esc(source['url']) + '">' + esc(source['title']) + '</a> · ' + esc(source['verified_at']) + '</p><blockquote>' + esc(source['excerpt']) + '</blockquote>'
    return body + '</section>'


def dataset_context(profile, content, zh):
    if content.get('presentation') != 'comparison-report':
        return ''
    factual = [c for c in profile['cases'] if 'reference_answer' in c]
    added = sum(bool(c.get('reference_annotation')) for c in profile['cases'])
    targets = sum(bool(c.get('anchors') or c.get('paper_ids') or c.get('repositories')) for c in profile['cases'] if 'reference_answer' not in c)
    source = '<a href="https://huggingface.co/datasets/google/simpleqa-verified">SimpleQA Verified</a>'
    return '<section class="specimen"><h2>' + ('题目从哪里来' if zh else 'Where the questions come from') + '</h2><p>' + (f'公开事实题来自 {source}：原版 1,000 题，按接口统一长度限制排除 169 题，保留 {len(factual)} 题；筛选在调用前完成，未按结果挑题。' if zh else f'Public factual questions come from {source}: 169 of the original 1,000 questions were excluded by the shared query-length limit before calls, leaving {len(factual)}. Selection did not depend on results.') + '</p><p>' + (f'场景参考页检查共 {targets} 题：{targets-added} 题沿用原有参考，{added} 题根据原始问题补充独立来源。后补标注沿用已保存的输出，不重测。该题组属于总体，不与公开事实题另加到总体中。' if zh else f'The scenario reference check covers {targets} questions: {targets-added} original references and {added} independently added references based on the original queries. Added labels reuse saved responses without new calls. This subgroup is already included in overall.') + '</p></section>'
