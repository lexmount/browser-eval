"""Small bilingual Web Search report built from frozen calls, never invented scores."""

import argparse
import copy
import html
import json
import math
import posixpath
import re
from collections import Counter
from pathlib import Path
from statistics import median
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from harness.web_fetch.config import ROOT
from harness.record_io import same_json_record
from harness.reporting.presentation import check_presentation

from .core import (
    aggregate,
    canonical_url,
    classify,
    digest,
    evaluate,
    in_domain,
    normalize,
    request_cost,
    request_body,
    validate_profile,
    verify_bundle,
    write_json,
)
from .evidence_quality import factual_sensitivity, CONTAMINATION_RULE
from .diagnostics import operational
from .runner import reference_available, reference_changes
from .profiles import CURRENT_PROFILE, load_profile, publication_authority
from .scoring import apply_identity_correction
from .relevance import score as score_relevance, summary as relevance_summary, verify_provenance

STYLE = """
.dataset-controls{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;align-items:end;margin:20px 0}.dataset-controls label{display:grid;gap:5px}.dataset-controls select,.dataset-controls input{width:100%;min-width:0}

.specimen-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;align-items:start}.specimen-grid>article{min-width:0;height:100%}.specimen-grid h3{font-size:18px}.specimen-grid p{font-size:14px}@media(max-width:800px){.specimen-grid{grid-template-columns:1fr}}

footer{display:flex;gap:24px;border-top:1px solid var(--line,#d8e0e7);padding-top:20px;margin-top:40px;font-size:14px}main>section>p,.chapter>p{max-width:78ch}.method-notes,.technical-details{margin:24px 0}.metric-caption{font-size:14px;color:var(--muted,#526575)}

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
.chapter-links{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;margin:28px 0}.chapter-link{display:grid;gap:8px;padding:22px;border:1px solid var(--line);border-radius:12px;background:var(--panel);text-decoration:none}.chapter-link span{color:var(--muted)}[data-population]{margin:28px 0;padding:20px;border:1px solid var(--line);border-radius:12px;background:var(--panel)}[data-population] h3{margin:0}[data-population] .scroll{margin-bottom:0}.population-note{max-width:850px;color:var(--muted)}[data-population] table{font-size:14px}[data-population] th{white-space:normal;min-width:75px;max-width:150px}[data-population] td{min-width:75px;white-space:nowrap}[data-population] th,[data-population] td{padding:10px}
@media(max-width:600px){.chapter-links{grid-template-columns:1fr}[data-population]{padding:12px}main,nav{padding:16px}nav{gap:14px}td,th{padding:10px}.flow{flex-direction:column}}
"""
SCRIPT = """
document.querySelector('[data-theme]').onclick=()=>{document.body.className=document.body.className==='dark'?'light':'dark'};
document.querySelectorAll('[data-sort-column]').forEach(b=>b.onclick=()=>{let t=b.closest('table'),i=+b.dataset.sortColumn,asc=b.parentElement.getAttribute('aria-sort')!=='ascending';t.querySelectorAll('th').forEach(x=>x.removeAttribute('aria-sort'));b.parentElement.setAttribute('aria-sort',asc?'ascending':'descending');let rows=[...t.tBodies[0].rows];rows.sort((a,b)=>{let x=a.cells[i].dataset.value,y=b.cells[i].dataset.value;if(x==='')return y===''?0:1;if(y==='')return -1;return (Number(x)-Number(y))*(asc?1:-1)});rows.forEach(r=>t.tBodies[0].append(r))});
let pf=document.querySelector('[data-population-filter]');let f=document.querySelector('[data-filter]'),q=document.querySelector('[data-query]'),g=document.querySelector('[data-cohort-filter]'),o=document.querySelector('[data-origin-filter]'),m=document.querySelector('[data-method-filter]');function filter(){document.querySelectorAll('[data-row]').forEach(r=>{r.hidden=(pf&&pf.value!=='all'&&!JSON.parse(pf.selectedOptions[0].dataset.caseIds||'[]').includes(r.dataset.caseId))||(f&&f.value!=='all'&&r.dataset.state!==f.value)||(g&&g.value!=='all'&&r.dataset.cohort!==g.value)||(o&&o.value!=='all'&&r.dataset.origin!==o.value)||(m&&m.value!=='all'&&r.dataset.method!==m.value)||(q&&!r.textContent.toLowerCase().includes(q.value.toLowerCase()))})}if(pf)pf.onchange=filter;if(f)f.onchange=filter;if(q)q.oninput=filter;if(g)g.onchange=filter;if(o)o.onchange=filter;if(m)m.onchange=filter;filter();
document.querySelectorAll('[data-evidence]').forEach(b=>b.onclick=async()=>{let d=document.querySelector('dialog');d.querySelector('a').hidden=true;d.querySelector('pre').textContent='…';d.showModal();try{let r=await fetch(b.dataset.evidence);if(!r.ok)throw Error('HTTP '+r.status);let text=await r.text();if(b.dataset.result!==undefined){let v=JSON.parse(text),items=v?.results??v?.data?.webPages?.value,index=Number(b.dataset.result);if(!/^[0-9]+$/.test(b.dataset.result)||!Number.isSafeInteger(index)||!Array.isArray(items)||index>=items.length)throw Error('Selected result is missing or invalid');let item=items[index];if(!item||typeof item!=='object'||Array.isArray(item)||!Object.keys(item).length)throw Error('Selected result is missing or invalid');text=JSON.stringify(item,null,2)}else{try{text=JSON.stringify(JSON.parse(text),null,2)}catch{}}d.querySelector('pre').textContent=text}catch(e){d.querySelector('pre').textContent=String(e)}});
document.querySelectorAll('[data-diagnosis]').forEach(b=>b.onclick=()=>{let d=document.querySelector('dialog');d.querySelector('pre').textContent=b.dataset.diagnosis;let a=d.querySelector('a');a.hidden=false;a.href=b.dataset.detail;d.showModal()});
document.querySelectorAll('[data-close]').forEach(b=>b.onclick=()=>b.closest('dialog').close());
if(document.querySelector('.mermaid')&&innerWidth>700){let s=document.createElement('script');s.src='https://cdn.jsdelivr.net/npm/mermaid@11.4.1/dist/mermaid.min.js';s.integrity='sha384-rbtjAdnIQE/aQJGEgXrVUlMibdfTSa4PQju4HDhN3sR2PmaKFzhEafuePsl9H/9I';s.crossOrigin='anonymous';s.onload=async()=>{mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:'neutral'});for(let [i,n] of [...document.querySelectorAll('.mermaid')].entries()){try{let r=await mermaid.render('search-flow-'+i,n.textContent);n.innerHTML=r.svg;n.hidden=false;n.previousElementSibling.hidden=true}catch{}}};document.head.appendChild(s)}
"""


def esc(value):
    return html.escape(str(value), quote=True)


def fmt(value):
    return "—" if value is None else f"{value:,.2f}"


def table(headers, rows, sortable=False, *, bounded=False):
    head = "".join(
        f'<th><button data-sort-column="{i}">{esc(h)} ↕</button></th>'
        if sortable and i
        else f"<th>{esc(h)}</th>"
        for i, h in enumerate(headers)
    )
    return (
        ('<div class="scroll inventory" tabindex="0"><table' if bounded else '<div class="scroll" tabindex="0"><table')
        + (' class="compact"' if len(headers) == 2 else "")
        + "><thead><tr>"
        + head
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></div>"
    )


def cells(values):
    return "<tr>" + "".join("<td>" + v + "</td>" for v in values) + "</tr>"


def _redact_url_userinfo(raw):
    """Redact only HTTP URL authority credentials, preserving every other byte."""
    def replace(match):
        authority = match.group(2)
        if b"@" not in authority:
            return match.group(0)
        return match.group(1) + b"REDACTED:REDACTED@" + authority.rsplit(b"@", 1)[1]

    return re.sub(rb'(https?://)([^/?#\s"<>\\,;]+)', replace, raw, flags=re.IGNORECASE)


def public_calls(profile, rows, source, sources):
    """Keep measured outcomes; redact URL credentials and audit billing corrections."""
    rows = copy.deepcopy(rows)
    methods = {m["id"]: m for m in profile["methods"]}
    files, redactions, corrections = {}, [], []
    for row in rows:
        attempts = row.get("attempts", [])
        final_path = row["evidence"]["path"]
        if attempts and attempts[-1]["evidence"]["path"] != final_path:
            raise ValueError("Final response evidence differs from the final physical attempt")
        for attempt in attempts or [row]:
            binding = attempt["evidence"]
            raw = (sources.get(row.get("cohort"), source) / binding["path"]).read_bytes()
            public = _redact_url_userinfo(raw)
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
            row["cost"] = sum(a["cost"] for a in attempts) if all(a["cost"] is not None for a in attempts) else None
    return rows, files, redactions, corrections


def load_run(source):
    profile = json.loads((source / "profile.json").read_text())
    validate_profile(profile)
    receipt = json.loads((source / "receipt.json").read_text())
    synthetic = receipt.get("synthetic") is True and receipt.get("certified") is False
    if receipt.get("synthetic") is True and not synthetic:
        raise ValueError("Synthetic evidence cannot be certified")
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
    bymethod = {m["id"]: m for m in profile["methods"]}
    for r in rows:
        method = bymethod[r["method"]]
        expected_request = request_body(method["adapter"], bycase[r["case_id"]], profile["protocol"], method.get("output_options"))
        if "request" not in r:
            if not synthetic:
                raise ValueError("Measured result lacks its frozen request contract")
        elif not same_json_record(r["request"], expected_request):
            raise ValueError("Measured request differs from the frozen profile contract")
        for attempt in r.get("attempts", []):
            if "request" in attempt and not same_json_record(attempt["request"], expected_request):
                raise ValueError("Physical attempt request differs from the frozen profile contract")
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


def _language_notice(output, languages):
    from harness.report_languages import write_language_contract
    if languages == ["zh"] and (output / "en").exists():
        for directory in sorted((output / "en").rglob("*"), key=lambda p: len(p.parts), reverse=True):
            if directory.is_dir() and not any(directory.iterdir()):
                directory.rmdir()
    write_language_contract(output, include_english="en" in languages)
    return ["en/index.html"] if languages == ["zh"] else []


def _check_report_languages(output, manifest):
    from harness.report_languages import check_language_contract, generated_languages
    check_language_contract(output)
    if list(generated_languages(output)) != manifest.get("languages"):
        raise ValueError("Report language metadata differs from the report manifest")



def assessment_profile(profile, relevance, review):
    """Describe current assessment without rewriting historical collection metadata."""
    from .review import factual_quality_exclusions
    protocol = dict(profile["protocol"])
    exclusions = factual_quality_exclusions(review)
    if relevance:
        protocol["quality"] = "Frozen pooled title/snippet relevance grades (0..3); deterministic aggregation. See report_assessment for judge provenance."
    if review:
        protocol["review"] = "Complete independent Codex result review with per-output opinions and finding adjudications."
    return dict(profile, protocol=protocol, **({"independent_result_review_scope": "all", "factual_quality_exclusions": exclusions} if review else {}))

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
    review=None,
    site_prefix="",
    cohorts=(),
):
    if language not in ("en", "zh"):
        raise ValueError("Unsupported primary report language; choose en or zh")
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
        profile = load_profile(profile_path)
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
    review_data = json.loads(review.read_text()) if review else None
    if review is not None:
        from .review import validate_public_review
        validate_public_review(profile, selected, review_data)
        write_json(output / "independent-review.json", review_data)
    relevance_data = json.loads(relevance.read_text()) if relevance else None
    if relevance_data:
        if "plan" not in relevance_data:
            from .relevance import import_annotations
            relevance_data = import_annotations(profile, selected, relevance_data)
        verify_provenance(profile, selected, relevance_data)
    profile = assessment_profile(profile, relevance_data, review_data)
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
    if review_data:
        bundle.append({"path": "independent-review.json", "sha256": digest((output / "independent-review.json").read_bytes())})
    factual_cases = {c["id"]: c for c in profile["cases"] if "reference_answer" in c}
    if factual_cases:
        sensitivity = {r["id"]: factual_sensitivity(factual_cases[r["case_id"]], r["results"]) for r in selected if r["case_id"] in factual_cases}
        write_json(output / "factual-sensitivity.json", {"schema": "factual-sensitivity/1", "timing": "post-hoc diagnostic; no recollection or rank compaction", "rule": CONTAMINATION_RULE, "results": sensitivity})
        bundle.append({"path": "factual-sensitivity.json", "sha256": digest((output / "factual-sensitivity.json").read_bytes())})
    if relevance_data:
        target = output / "relevance-judgments.json"
        target.write_text(json.dumps(relevance_data, ensure_ascii=False, indent=2), encoding="utf-8")
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
        for route in [*old_manifest["routes"], *old_manifest.get("language_notices", [])]:
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
        prefix = "" if lang == "zh" else lang + "/"

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
            nav = [("index.html", t("Decisions", "选型结论")),
                   ("capabilities.html", t("API outputs", "接口与输出")),
                   ("quality.html", t("Retrieval quality", "检索效果")),
                   ("performance.html", t("Speed and cost", "速度与费用")),
                   ("dataset.html", t("Tests and sources", "测试集与来源")),
                   ("results.html", t("Query comparison", "逐题对照"))]
        elif review_data:
            nav.append(("review.html", t("Review", "独立审查")))
        state = (
            t(
                ("Measured API evaluation · frozen model relevance labels and deterministic metrics." if relevance_data and relevance_data["judge"]["kind"] == "model" else "Measured API evaluation · human relevance labels and deterministic metrics." if relevance_data else "Measured API evaluation · automated target checks, no relevance labels."),
                ("API 实测 · 固定模型分级与确定性指标。" if relevance_data and relevance_data["judge"]["kind"] == "model" else "API 实测 · 人工标注与确定性指标。" if relevance_data else "API 实测 · 目标匹配由规则核验，暂无相关性标注。"),
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
                otherprefix = "" if other == "zh" else other + "/"
                language_depth = "../" * (len(Path(prefix + route).parts) - 1)
                navigation += f'<a href="{language_depth}{otherprefix}{route}">{"中文" if other == "zh" else "English"}</a>'
            elif lang == "zh" and authored.get("presentation") != "comparison-report":
                language_depth = "../" * (len(Path(prefix + route).parts) - 1)
                navigation += f'<a href="{language_depth}en/index.html">English</a>'
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
                        if review_data:
                            asset_paths.add("independent-review.json")
                        node[attr] = posixpath.normpath(
                            posixpath.join(
                                site_prefix,
                                "" if value in asset_paths else posixpath.dirname(prefix + route),
                                value,
                            )
                        )
            if authored.get("presentation") == "comparison-report":
                footer = document.new_tag("footer")
                for target, label in [("research.html", t("Method and FAQ", "方法与问答")), ("review.html", t("Review evidence", "审查依据"))]:
                    if target == "review.html" and not review_data:
                        continue
                    link = document.new_tag("a", href=posixpath.join(site_prefix, prefix, target))
                    link.string = label
                    if target == "review.html":
                        link.string = label + f" · {review_data['case_reviewed']}/{review_data['case_total']}"
                        link['data-review-coverage'] = ''
                        link['data-reviewed-queries'] = f"{review_data['case_reviewed']}/{review_data['case_total']}"
                        link['data-reviewed-outputs'] = f"{review_data['output_reviewed']}/{review_data['output_total']}"
                    footer.append(link)
                document.main.append(footer)
            from harness.report_languages import REPORT_PAGE_MARKER
            path.write_text(str(document).replace("</head>", REPORT_PAGE_MARKER + "</head>"))
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
            a = s["all_latency"] if s else {}
            hit = s["metrics"][hit_key] if s else {"mean": None, "n": 0}
            entries.append(
                [
                    m["label"],
                    s["nonempty"] if s else None,
                    a.get("mean"),
                    a.get("p50"),
                    a.get("p95"),
                    hit["mean"],
                    m["price_per_1000"],
                ]
            )
        directions = {1: "max", 2: "min", 3: "min", 4: "min", 5: "max", 6: "min" if len({m["currency"] for m in overview_methods}) == 1 else "neutral"}
        winners = numeric_winners(entries, directions)
        core = []
        for entry, m in zip(entries, overview_methods, strict=True):
            td = []
            for i, value in enumerate(entry):
                label = esc(value) if i == 0 else fmt(value)
                if i == 1 and value is not None:
                    label = f"{value}/{summary[m['id']]['n']}"
                if i == 6:
                    label += " " + m["currency"]
                if i == 5 and value is not None:
                    label = f"{value * 100:.1f}% ({summary[m['id']]['metrics'][hit_key]['n']}题)" if zh else f"{value * 100:.1f}% ({summary[m['id']]['metrics'][hit_key]['n']} queries)"
                win = i in winners and value == winners[i]
                td.append(numeric_cell(label, value if i else None, f"overview-{i}", directions.get(i, "neutral"), win))
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
                "Charges use the prices and billing rules frozen in each method configuration. Unset prices remain unavailable. API charges exclude downstream models and additional fetches.",
                "费用按各方法冻结的单价和计费规则计算；未设置单价的费用保持未知。接口费用不包含下游模型和额外抓取。",
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
                "Web, site-restricted and vertical queries remain separate populations. Saved requests show each method’s native controls; the comparison uses delivered search text without an answering model.",
                "通用网页、站点限定和垂类查询分别统计。各方法的请求参数可在实际输出中查看；比较保留原生搜索文本，不接入回答模型。",
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
        if not reading_sections and authored.get("presentation") != "comparison-report":
            body += mode_panel(profile, selected, zh)
        if authored.get("presentation") == "comparison-report":
            body = authored_sections(authored, "lead", lang, rows=selected)
            body += executive_table(profile, selected, authored, relevance_scores, zh)
            body += discovery_comparison(profile, selected, authored, zh)
            body += authored_sections(authored, "summary", lang, rows=selected)
            for chapter_key, chapter_title in [("capabilities", t("API outputs", "接口与输出")), ("quality", t("Retrieval quality", "检索效果"))]:
                chapter_body = authored_sections(authored, chapter_key + "-lead", lang, rows=selected)
                if chapter_key == "capabilities":
                    chapter_body += capability_map(profile, selected, zh)
                    chapter_body += authored_sections(authored, chapter_key, lang, rows=selected)
                    if authored.get("interface_example_case_id"):
                        chapter_body += interface_example(profile, selected, authored["interface_example_case_id"], zh)
                else:
                    chapter_body += authored_sections(authored, "quality-definitions", lang, rows=selected)
                    chapter_body += relevance_panel(profile, relevance_scores, relevance_data, zh) if relevance_scores else ""
                    chapter_body += factual_panel(profile, selected, zh, compact=True)
                    chapter_body += authored_sections(authored, chapter_key, lang, rows=selected)
                page(chapter_key + ".html", chapter_title, chapter_body)
        if review_data and authored.get("presentation") != "comparison-report":
            body += '<p data-review-coverage><a href="review.html">' + t(
                f"Codex reviewed {review_data['case_reviewed']}/{review_data['case_total']} queries and {review_data['output_reviewed']}/{review_data['output_total']} outputs",
                f"Codex 已审查 {review_data['case_reviewed']}/{review_data['case_total']} 题、{review_data['output_reviewed']}/{review_data['output_total']} 份接口输出") + '</a></p>'
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
        dataset_profile, dataset_content = quality_dataset(profile, authored)
        ds = []
        for c in dataset_profile["cases"]:
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
                            cohort_label(profile, c.get("cohort"), zh)
                            if profile.get("report_cohorts")
                            else search_category(c["family"], zh)
                        ),
                        esc(t({"zh":"Chinese","en":"English"}.get(c["language"], c["language"]), {"zh":"中文","en":"英文"}.get(c["language"], c["language"]))),
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
            t("Tests and sources", "测试集与来源"),
            dataset_context(dataset_profile, dataset_content, zh)
            + provenance_table(dataset_profile, zh)
            + f'<p><a href="{"../" if prefix else ""}query-catalog-license.txt">'
            + t("Public query catalog attribution and license", "公开查询集署名与许可")
            + "</a></p>"
            + '<div class="dataset-controls">' + origin_filter(dataset_profile, zh)
            + cohort_filter(dataset_profile, zh)
            + population_filter(dataset_profile, dataset_content, zh)
            + "<label>"
            + t("Search queries, categories or sources", "搜索查询、类别或来源")
            + " <input data-query></label></div>"
            + table(
                [
                    "ID",
                    t("Benchmark", "评测集"),
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
                    for s, c in zip(ds, dataset_profile["cases"], strict=True)
                ],
                bounded=True,
            ),
        )
        inventory = []
        bycase = {c["id"]: c for c in profile["cases"]}
        method_labels = {method["id"]: method["label"] for method in profile["methods"]}
        for r in selected:
            cid = r["case_id"]
            c = bycase[cid]
            status, reason = classify(c, r)
            query_relevance = next((x for x in (relevance_scores or []) if x["case_id"] == cid and x["method"] == r["method"]), None)
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
                "answer_hit": t("Reference answer text found", "找到参考答案文字"),
                "answer_miss": t("Reference answer text absent", "未找到参考答案文字"),
                "domain_hit": t("Requested site found", "找到指定网站"),
                "domain_miss": t("Requested site absent", "未找到指定网站"),
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
            if query_relevance:
                detail += relevance_detail(query_relevance, zh)
            if c.get('anchors'):
                detail += '<h2>' + t('Webpage discovery references', '网页发现参考') + '</h2><ul>' + ''.join('<li><a href="' + esc(url) + '">' + esc(url) + '</a></li>' for url in c['anchors']) + '</ul>'
            elif c.get('domains'):
                detail += '<h2>' + t('Requested sites', '指定网站') + '</h2><p>' + esc('、'.join(c['domains'])) + '</p>'
            if c.get("reference_annotation"):
                detail += annotation_detail(c["reference_annotation"], zh)
            elif c.get("anchors"):
                detail += (
                    "<p>"
                    + t(
                        "These URLs define the known-page check; relevance is scored separately.",
                        "这些网址用于检查指定网页是否被找到；内容是否有用，由相关性评分另行判断。",
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
            if query_relevance:
                reason_label = t("Useful results: ", "可用结果：") + str(query_relevance["useful"]) + f"/{cutoff}"
            if review_data:
                from .review import render_detail
                detail += render_detail(review_data, r["id"], zh)
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
                detail += '<p>' + t("Post-hoc benchmark-mirror exclusion; blanked ranks: ", "事后评测数据排除；留空的位置：") + esc(str(sensitivity["excluded_ranks"])) + '</p>'
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
            t("Query comparison", "逐题对照"),
            (render_result_comparison(profile, selected, zh, prefix=prefix, site_prefix=site_prefix, content=authored, relevance=relevance_data) if authored.get("presentation") == "comparison-report" else "")
            + ("" if authored.get("presentation") == "comparison-report" else (
            origin_filter(profile, zh)
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
            )
            ))
        )
        research = (
            authored_sections(authored, "method", lang, rows=selected)
            + (cohort_summary(profile, rows, zh) if authored.get("reading_model") and authored.get("presentation") != "comparison-report" else "")
            + fairness_table(profile, rows, zh)
        )
        page(
            "research.html", t("Evaluation method and competitors", "评测方法与主要对手"), research
        )
        if authored.get("presentation") != "comparison-report":
            page("interpretation.html", t("Findings", "结果解读"),
                 authored_sections(authored, "interpretation-lead", lang, rows=selected)
                 + interpretation_summary_table(profile, selected, zh, authored, relevance_scores)
                 + factual_panel(profile, selected, zh)
                 + authored_sections(authored, "interpretation", lang, rows=selected)
                 + outcome_table(profile, selected, zh) + domain_discovery_table(profile, selected, zh))
        if authored.get("presentation") == "comparison-report":
            primary_ids = {c["id"] for c in profile["cases"] if "reference_answer" not in c}
            primary_rows = [r for r in rows if r["case_id"] in primary_ids]
            primary_profile = dict(profile, cases=[c for c in profile["cases"] if c["id"] in primary_ids])
            primary_diagnostics = operational(primary_rows, payloads)
            timing = benchmark_operations(profile, selected, authored, zh)
            timing += authored_sections(authored, "performance", lang, rows=selected)
            timing += '<details class="technical-details"><summary>' + t("Server timing and upstream evidence", "服务端计时与上游调度证据") + '</summary>' + performance_view(primary_profile, primary_diagnostics, zh, evidence_prefix="../" if prefix else "") + '</details>'
            timing += '<details><summary>' + t("Billing calculation", "单价与计费明细") + '</summary>' + cost_panel(profile, rows, zh) + '</details>'
            timing += authored_sections(authored, "cost", lang, rows=selected)
            page("performance.html", t("Speed and cost", "速度与费用"), timing)
        else:
            page("performance.html", t("Latency diagnostics", "性能诊断"), authored_sections(authored, "performance", lang, rows=selected) + performance_view(profile, diagnostics, zh, evidence_prefix="../" if prefix else ""))
        if review_data:
            from .review import render_summary
            page("review.html", t("Independent result review", "独立审查结果"), render_summary(review_data, zh) + authored_sections(authored, "review", lang, rows=selected))
    write_json(
        output / "manifest.json",
        dict(
            schema="web-search-report/1",
            template="web-search/1",
            languages=languages,
            language_notices=_language_notice(output, languages),
            primary_language=language,
            site_prefix=site_prefix,
            routes=routes,
            profile=profile,
            collection_profile=collection_profile,
            scoring_revision=scoring_revision,
            assessment_revision=assessment_revision,
            evidence_redactions=redactions,
            billing_corrections=billing_corrections,
            independent_review_sha256=digest((output / "independent-review.json").read_bytes()) if review_data else None,
            report_assessment={"relevance": {key: relevance_data["judge"][key] for key in ("kind", "name")} if relevance_data else None,
                               "independent_review_scope": "all" if review_data else None,
                               "collection_protocol_sha256": digest(json.dumps(collection_profile["protocol"], sort_keys=True).encode())},
            relevance_sha256=digest((output / "relevance-judgments.json").read_bytes()) if relevance_data else None,
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
    from harness.report_languages import select_report_language
    entry = select_report_language(output, language)
    check(output)
    return entry


def check(output, *, return_data=False, evidence_only=False):
    manifest = json.loads((output / "manifest.json").read_text())
    _check_report_languages(output, manifest)
    editions = [
        {r.removeprefix(lang + "/") for r in manifest["routes"] if r.startswith(lang + "/")}
        if lang != "zh"
        else {r for r in manifest["routes"] if not r.startswith(("en/", "zh/"))}
        for lang in manifest["languages"]
    ]
    if not editions or any(e != editions[0] for e in editions):
        raise ValueError("Language route inventories disagree")
    verify_bundle(output, manifest["bundle"])
    for item in manifest["bundle"]:
        if item["path"].endswith((".json", ".txt", ".md")):
            raw = (output / item["path"]).read_bytes()
            if _redact_url_userinfo(raw) != raw:
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
    review_data = json.loads((output / "independent-review.json").read_text()) if manifest.get("independent_review_sha256") else None
    if manifest.get("report_assessment") is not None:
        expected_assessment = {
            "relevance": {key: relevance_data["judge"][key] for key in ("kind", "name")} if relevance_data else None,
            "independent_review_scope": "all" if manifest.get("independent_review_sha256") else None,
            "collection_protocol_sha256": digest(json.dumps(manifest["collection_profile"]["protocol"], sort_keys=True).encode()),
        }
        if (manifest["report_assessment"] != expected_assessment
                or assessment_profile(manifest["profile"], relevance_data, review_data) != manifest["profile"]):
            raise ValueError("Current assessment metadata contradicts its evidence")
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
    if manifest.get("independent_review_sha256"):
        from .review import validate_public_review, render_detail, render_summary
        review_path = output / "independent-review.json"
        if digest(review_path.read_bytes()) != manifest["independent_review_sha256"]:
            raise ValueError("Independent review evidence hash mismatch")
        review_data = json.loads(review_path.read_bytes())
        validate_public_review(manifest["profile"], manifest["rows"], review_data)
        for lang in manifest["languages"]:
            prefix = "" if lang == "zh" else lang + "/"
            if prefix + "review.html" not in manifest["routes"]:
                raise ValueError("Independent review page is missing")
            soup = BeautifulSoup((output / (prefix + "index.html")).read_text(), "html.parser")
            if not soup.select_one("[data-review-coverage]"):
                raise ValueError("Independent review coverage is absent from the summary")
            review_page = BeautifulSoup((output / (prefix + "review.html")).read_text(), "html.parser")
            expected = BeautifulSoup(render_summary(review_data, lang == "zh"), "html.parser")
            actual = review_page.select_one("[data-independent-review]")
            if actual is None or actual.get_text() != expected.get_text():
                raise ValueError("Independent review summary differs from the accepted evidence")
            for row in manifest["rows"]:
                detail = BeautifulSoup((output / (prefix + "details/" + row["id"] + ".html")).read_text(), "html.parser")
                expected = BeautifulSoup(render_detail(review_data, row["id"], lang == "zh"), "html.parser")
                actual = detail.select("[data-independent-opinion]")
                if len(actual) != 1 or actual[0].get_text() != expected.get_text():
                    raise ValueError("Independent review detail differs from accepted evidence")
    factual_cases = {
        case["id"]: case for case in manifest["profile"]["cases"]
        if case.get("reference_answer")
    }
    if factual_cases:
        sensitivity_path = output / "factual-sensitivity.json"
        sensitivity = json.loads(sensitivity_path.read_text())
        expected_sensitivity = {
            row["id"]: factual_sensitivity(factual_cases[row["case_id"]], row["results"], rule=sensitivity.get("rule"))
            for row in manifest["rows"] if row["case_id"] in factual_cases
        }
        if sensitivity.get("results") != expected_sensitivity:
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
        prefix = "" if lang == "zh" else lang + "/"
        soup = BeautifulSoup((output / (prefix + "research.html")).read_text(), "html.parser")
        expected = BeautifulSoup(
            fairness_table(manifest["profile"], manifest["rows"], lang == "zh"), "html.parser"
        )
        actual = soup.select_one("[data-fairness]")
        if actual is None or actual.get_text() != expected.select_one("[data-fairness]").get_text():
            raise ValueError("Fairness table disagrees with physical calls")
        if groups:
            if (manifest.get("authored") or {}).get("presentation") != "comparison-report":
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
            for route in (("dataset.html",) if (manifest.get("authored") or {}).get("presentation") == "comparison-report" else ("dataset.html", "results.html")):
                inventory = BeautifulSoup((output / (prefix + route)).read_text(), "html.parser")
                options = inventory.select("[data-cohort-filter] option")
                if {o["value"] for o in options} != {"all", *(g["id"] for g in groups)}:
                    raise ValueError("Incomplete cohort filter")
        for route in (("dataset.html",) if (manifest.get("authored") or {}).get("presentation") == "comparison-report" else ("dataset.html", "results.html")):
            inventory = BeautifulSoup((output / (prefix + route)).read_text(), "html.parser")
            inventory_profile = quality_dataset(manifest["profile"], manifest.get("authored") or {})[0] if route == "dataset.html" else manifest["profile"]
            if route == "dataset.html" and Counter(r.get("data-case-id") for r in inventory.select("[data-row]")) != Counter(c["id"] for c in inventory_profile["cases"]):
                raise ValueError("Active dataset differs from reviewed quality population")
            expected_groups = {origin_group(c) for c in inventory_profile["cases"]}
            if {o["value"] for o in inventory.select("[data-origin-filter] option")} != {
                "all",
                *expected_groups,
            }:
                raise ValueError("Incomplete query-source filter")
            expected_origins = [
                origin_group(c)
                for c in inventory_profile["cases"]
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
    relevance_scores = (score_relevance(manifest["profile"], [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]], relevance_data["plan"], relevance_data["labels"]) if relevance_data else [])
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
        if manifest.get("report_assessment"):
            revised = assessment_profile(revised, relevance_data, review_data)
        if revised != manifest["profile"]:
            raise ValueError("Scoring revision profile mismatch")
    elif manifest.get("assessment_revision"):
        from .scoring import apply_report_revision
        revised, _, _ = apply_report_revision(manifest["collection_profile"], [], manifest["assessment_revision"]["contract"])
        revised, _ = project_comparison(revised, [], manifest.get("authored") or {})
        if manifest.get("report_assessment"):
            revised = assessment_profile(revised, relevance_data, review_data)
        if revised != manifest["profile"]:
            raise ValueError("Assessment revision profile mismatch")
    if manifest.get("diagnostics") != expected_diagnostics:
        raise ValueError("Performance explanation disagrees with raw evidence")
    if evidence_only:
        if return_data:
            return manifest["profile"], [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]]
        return True
    for route in manifest["routes"]:
        path = output / route
        soup = BeautifulSoup(path.read_text(), "html.parser")
        check_presentation(soup)
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
                "review.html": "review",
            }.get(name)
            if key:
                score_rows = [
                    r
                    for r in manifest["rows"]
                    if r["round"] == manifest["profile"]["protocol"]["score_round"]
                ]
                if authored.get("presentation") == "comparison-report":
                    pages = {"index.html": ["lead", "summary"], "quality.html": ["quality-lead", "quality-definitions", "quality"], "capabilities.html": ["capabilities-lead", "capabilities"], "performance.html": ["performance", "cost"]}.get(name, [key])
                    expected = ''.join(authored_sections(authored, section, lang, rows=score_rows) for section in pages)
                else:
                    expected = (authored_sections(authored, "lead", lang, rows=score_rows) if name == "index.html" else "") + (authored_sections(authored, "interpretation-lead", lang, rows=score_rows) if name == "interpretation.html" else "") + authored_sections(authored, key, lang, rows=score_rows)
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
            visible_diagnostics = expected_diagnostics
            if authored.get("presentation") == "comparison-report":
                primary_ids = {c["id"] for c in manifest["profile"]["cases"] if "reference_answer" not in c}
                primary_rows = [r for r in manifest["rows"] if r["case_id"] in primary_ids]
                visible_diagnostics = operational(primary_rows, payloads)
                expected_operations = BeautifulSoup(benchmark_operations(manifest["profile"], [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]], authored, soup.html["lang"] == "zh"), 'html.parser')
                if str(soup.select_one('[data-benchmark-operations]')) != str(expected_operations.select_one('[data-benchmark-operations]')):
                    raise ValueError("Benchmark timing/cost summary differs from physical calls")
                expected_cost = BeautifulSoup(cost_panel(manifest["profile"], manifest["rows"], soup.html["lang"] == "zh"), 'html.parser')
                if str(soup.select_one('[data-cost-budget]')) != str(expected_cost.select_one('[data-cost-budget]')):
                    raise ValueError("Cost budget differs from physical calls")
            trs = soup.select_one("[data-timing-decomposition]").select("table")[0].select("tbody tr")
            for tr, method in zip(trs, manifest["profile"]["methods"], strict=True):
                stats = visible_diagnostics["methods"][method["id"]]
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
                c for c in visible_diagnostics["calls"] if c["after_first_ms"] is not None
            ]
            if candidates:
                chosen = max(candidates, key=lambda c: c["after_first_ms"])
                specimen = soup.select_one("[data-timing-specimen]")
                if specimen is None or specimen["data-timing-specimen"] != chosen["id"]:
                    raise ValueError("Missing or wrong timing specimen")
        if route.endswith("quality.html") and (manifest.get("authored") or {}).get("presentation") == "comparison-report":
            score_rows = [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]]
            for selector, expected in (("[data-factual]", factual_panel(manifest["profile"], score_rows, soup.html["lang"] == "zh", compact=True)),):
                expected_node = BeautifulSoup(expected, "html.parser").select_one(selector)
                if str(soup.select_one(selector)) != str(expected_node):
                    raise ValueError("Quality population differs from measured inputs")
        if route.endswith("interpretation.html"):
            selected = [
                r for r in manifest["rows"]
                if r["round"] == manifest["profile"]["protocol"]["score_round"]
            ]
            expected_table = BeautifulSoup(
                interpretation_summary_table(manifest["profile"], selected, soup.html["lang"] == "zh", manifest.get("authored") or {}, relevance_scores),
                "html.parser",
            ).select_one("table")
            actual_table = soup.select_one("table")
            if actual_table is None or str(actual_table) != str(expected_table):
                raise ValueError("Interpretation core comparison differs from evidence")
        if route.endswith("cost.html") and (manifest.get("authored") or {}).get("presentation") == "comparison-report":
            expected_cost = BeautifulSoup(cost_panel(manifest["profile"], manifest["rows"], soup.html["lang"] == "zh"), "html.parser").select_one("[data-cost-budget]")
            if str(soup.select_one("[data-cost-budget]")) != str(expected_cost):
                raise ValueError("Cost budget differs from measured physical calls")
        if authored.get("presentation") == "comparison-report" and "details/" not in route:
            if Path(route).name in {"interpretation.html", "cost.html"}:
                raise ValueError("Retired duplicate reading route")
            nav_targets = {a.get('href') for a in soup.select('nav a')}
            if not any(target and target.endswith('quality.html') for target in nav_targets):
                raise ValueError("Quality page is missing from reader navigation")
        comparison_home = route.endswith("index.html") and (manifest.get("authored") or {}).get("presentation") == "comparison-report"
        if comparison_home:
            expected = BeautifulSoup(executive_table(manifest["profile"], [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]], manifest["authored"], relevance_scores, soup.html["lang"] == "zh"), "html.parser")
            if str(soup.select_one('[data-executive]')) != str(expected.select_one('[data-executive]')):
                raise ValueError("Population summary differs from measured inputs")
            expected_discovery = BeautifulSoup(discovery_comparison(manifest['profile'], [r for r in manifest['rows'] if r['round'] == manifest['profile']['protocol']['score_round']], manifest['authored'], soup.html['lang'] == 'zh'), 'html.parser').select_one('[data-discovery]')
            if str(soup.select_one('[data-discovery]')) != str(expected_discovery):
                raise ValueError("Discovery comparison differs from measured inputs")
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
                best = numeric_winners([[value] for value in values], {0: "max" if i == 5 else "min"}).get(0)
                for cell, value in zip(cells_at, values, strict=True):
                    if ("win" in cell.get("class", [])) != (best is not None and value == best):
                        raise ValueError("Missing or incorrect unique winner marker")
        if route.endswith("capabilities.html") and manifest.get("authored", {}).get("interface_example_case_id"):
            cid = manifest["authored"]["interface_example_case_id"]
            example = soup.select_one("[data-interface-case]")
            if example is None or example["data-interface-case"] != cid or [n["data-interface-method"] for n in example.select("[data-interface-method]")] != [m["id"] for m in manifest["profile"]["methods"]]:
                raise ValueError("Interface example must show the same query for every method")
        if "details/" in route and relevance_scores:
            result_id = Path(route).stem
            row = next((r for r in manifest["rows"] if r["id"] == result_id), None)
            query_score = next((q for q in relevance_scores if row and q["case_id"] == row["case_id"] and q["method"] == row["method"]), None)
            if query_score:
                expected = BeautifulSoup(relevance_detail(query_score, soup.html["lang"] == "zh"), "html.parser").select_one('[data-case-relevance]')
                if str(soup.select_one('[data-case-relevance]')) != str(expected):
                    raise ValueError("Query quality calculation differs from frozen grades")
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
            href = urlsplit(href).path
            site_root = output.parent if manifest.get("site_prefix") else output
            target = (site_root / href).resolve()
            if not target.is_relative_to(site_root.resolve()) or not target.is_file():
                raise ValueError("Broken evidence/navigation target: " + href)
        if "/Users/" in str(soup):
            raise ValueError("Private data in report")
    if return_data:
        selected = [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]]
        return manifest["profile"], selected
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile", type=Path, default=CURRENT_PROFILE
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
    parser.add_argument("--identity-correction", type=Path)
    parser.add_argument("--review", type=Path, help="Complete independent review with adjudications")
    parser.add_argument("--relevance", type=Path, help="Complete, provenance-bound relevance labels for every in-house query")
    parser.add_argument("--export-annotations", type=Path, metavar="DESTINATION",
                        help="Prepare full in-house human annotations from an existing checked report")
    parser.add_argument("--site-prefix", default="")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--language", choices=["en", "zh"], default=None)
    parser.add_argument("--include-english", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--release-check", type=Path, metavar="VISUAL_RECEIPT")
    parser.add_argument("--export-delivery", type=Path, metavar="DESTINATION",
                        help="Export the checked report into lossless case shards for bounded-file hosts")
    args = parser.parse_args(argv)
    if args.export_annotations:
        if args.source or args.cohort:
            parser.error("Annotation export consumes an existing report")
        from .relevance import export_annotations
        profile, selected = check(args.output, return_data=True)
        print(export_annotations(profile, selected, args.export_annotations))
        return
    if args.export_delivery:
        if args.source or args.cohort:
            parser.error("Delivery export consumes an existing report, not build inputs")
        check(args.output)
        from .delivery import export, verify
        export(args.output, args.export_delivery)
        verify(args.output, args.export_delivery)
        print(args.export_delivery)
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
            review=args.review,
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
    report_manifest = json.loads(manifest.read_text())
    if report_manifest.get("receipt", {}).get("certified"):
        publication_authority(report_manifest)
    from .visual import verify
    return verify(output, visual_receipt)


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
        "public_labeled": ("Public factual queries", "公开事实题"),
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
        + ("评测集" if zh else "Benchmark")
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

    if profile.get("factual_quality_exclusions"):
        return '<section data-cohort-summary><h2>' + t("Measurement scope", "实测范围") + '</h2><p>' + t("Performance and cost include every physical request in each collection batch, including quarantined reference questions. Quality uses only the active dataset. The original calls remain in evidence.", "性能与费用包含各批次的全部实际请求，包括参考审查中隔离的题目。质量指标只使用当前有效题库；原始调用保留在证据中。") + '</p></section>'

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
            f"{len(profile['cases'])} queries and {len(rows)} final logical results, from {sum(len(r.get('attempts') or [r]) for r in rows)} physical requests including retries, across {len(groups)} complete collection batches. The table preserves each batch separately; the combined count is not a new same-window speed trial.",
            f"共 {len(profile['cases'])} 道题、{len(rows)} 份最终结果；含重试的实际请求为 {sum(len(r.get('attempts') or [r]) for r in rows)} 次，来自 {len(groups)} 批完整实测。下表分别保留各批次耗时，合并数量不代表重新进行了一轮同时段测速。",
        )
        + "</p>"
        + table(
            [
                t("Query group", "分项"),
                t("Method", "接口"),
                t("Queries/provider", "每家题数"),
                t("Search tasks", "搜索任务数"),
                t("Nonempty/final results", "非空结果数/最终结果数"),
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
                t("Search tasks", "搜索任务数"),
                t("Requested limit × tasks", "请求上限（条 × 任务数）"),
                t("Returned items × tasks", "返回数量（条 × 任务数）"),
                t("Mean items per task", "每个任务平均返回条数"),
            ],
            entries,
        )
        + "<p>"
        + t(
            f"First-round queries with equal result counts across all providers: {equal}/{len(common)}. Empty and short responses remain in every declared denominator. Counts do not establish relevance.",
            f"各接口实际返回数量相同的题目：{equal}/{len(common)}。空结果和不足额返回均保留在预定分母中。返回条数不代表相关性。",
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
        tag = 'details' if s.get('collapsed') else 'section'
        heading = 'summary' if s.get('collapsed') else f'h{heading_level}'
        body += f'<{tag} class="chapter" data-authored="{section_index}"><{heading}>' + esc(s['title'][language]) + f'</{heading}>'
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
        specimen_columns = s.get('specimens_layout') == 'columns' and bool(s.get('specimens'))
        if specimen_columns:
            body += '<div class="specimen-grid">'
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
                + "</h3>"
                + ("<p>" + esc(specimen["explanation"][language]) + "</p>" if specimen.get("explanation") else "")
                + "<p>" + esc(item["title"])
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
        if specimen_columns:
            body += '</div>'
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
        body += f"</{tag}>"
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


def numeric_winners(rows, directions):
    """Return unique extrema from available raw values; ties remain neutral."""
    winners = {}
    for column, direction in directions.items():
        available = [row[column] for row in rows if row[column] is not None]
        if direction == "neutral" or not available:
            continue
        best = (min if direction == "min" else max)(available)
        if available.count(best) == 1:
            winners[column] = best
    return winners


def numeric_cell(label, value, metric, direction, winner=False):
    attrs = f' data-metric="{esc(metric)}" data-metric-direction="{direction}"'
    if value is not None:
        attrs += f' data-sort="{value}" data-value="{value}"'
    else:
        attrs += ' data-value=""'
    if winner:
        attrs += ' class="win"'
        label = '<span class="comparison-winner">★ ' + label + '</span>'
    return '<td' + attrs + '>' + label + '</td>'


def interpretation_summary_table(profile, selected, zh, content=None, relevance_scores=None):
    content = content or {}
    if content.get("independent_benchmarks"):
        return population_summary(profile, selected, content, zh, relevance_scores=relevance_scores)
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
    directions = {1: "max", 2: "max", 4: "min", 5: "min", 6: "min" if len({m["currency"] for m in profile["methods"]}) == 1 else "neutral"}
    winners = numeric_winners(measures, directions)
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
        raw = [None, nonempty, hit, None, p50, p95, cost]
        cells_html = []
        for column, label in enumerate(values):
            source_index = (0, 1, 2, 4, 5, 6)[column]
            win = source_index in winners and raw[source_index] == winners[source_index]
            cells_html.append(numeric_cell(label, raw[source_index], f"interpretation-{source_index}", directions.get(source_index, "neutral"), win))
        rows.append('<tr>' + ''.join(cells_html) + '</tr>')
    return (
        '<h2>' + ('基础接口的核心结果' if zh else 'Core API comparison') + '</h2>'
        + table(
            ['接口', '非空题数', f'已知页@{cutoff}', 'P50 ms', 'P95 ms', '本批估算费用']
            if zh else ['Provider', 'Nonempty queries', f'Known page@{cutoff}', 'P50 ms', 'P95 ms', 'Estimated cohort cost'],
            rows,
        )
        + '<p>' + (
            '★ 仅标出当前列唯一的较优数值；各列适用范围不同，不能合成总排名。费用按冻结目录价估算，未知价格保留为空。'
            if zh else '★ marks a unique favorable value within its column. Populations differ across columns, so these are not an overall ranking. Charges use frozen list prices; unknown prices remain unavailable.'
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
        "按所列配置的原始响应统计；“有”表示至少一次交付该字段，不表示每条都有。其他接口的能力见绑定的接口调研资料。"
        if zh else "Based on the listed configurations: Yes means the field was delivered at least once, not in every result. Other endpoints are documented in the bound interface research."
    ) + '</p>')
    return ''.join(output)


def interface_example(profile, rows, case_id, zh):
    """Show the same query's complete native requests and response links for every method."""
    case = next((c for c in profile["cases"] if c["id"] == case_id), None)
    if case is None:
        raise ValueError("Interface example query is missing")
    indexed = {r["method"]: r for r in rows if r["case_id"] == case_id}
    body = '<section data-interface-case="' + esc(case_id) + '"><h2>' + (
        '同一道题的完整请求与返回' if zh else 'One query: complete requests and responses'
    ) + '</h2><p>' + esc(case["query"]) + '</p>'
    for method in profile["methods"]:
        if method["id"] not in indexed:
            raise ValueError("Interface example must include every method")
        row = indexed[method["id"]]
        body += '<article class="specimen" data-interface-method="' + esc(method["id"]) + '"><h3>' + esc(method["label"]) + '</h3>'
        body += '<p>' + (f'返回 {len(row["results"])} 条结果' if zh else f'{len(row["results"])} results returned') + '</p>'
        body += '<details><summary>' + ('完整请求参数' if zh else 'Complete request parameters') + '</summary><pre>' + esc(json.dumps(row.get("request", {}), ensure_ascii=False, indent=2)) + '</pre></details>'
        body += '<details><summary>' + ('全部搜索结果' if zh else 'All search results') + '</summary><pre>' + esc(json.dumps(row["results"], ensure_ascii=False, indent=2)) + '</pre></details>'
        body += '<button data-evidence="' + esc(row["evidence"]["path"]) + '">' + ('打开原始响应' if zh else 'Open native response') + '</button></article>'
    return body + '</section>'


def relevance_detail(result, zh):
    t = lambda en, cn: cn if zh else en
    rows = []
    for i in range(result.get("cutoff", 5)):
        grade = result["ranked_grades"][i] if i < len(result["ranked_grades"]) else 0
        label = str(grade) if i < len(result["ranked_grades"]) else t("Not returned", "未返回，记0")
        rows.append(cells([str(i + 1), label, str(2**grade-1), f'{1 / math.log2(i+2):.4f}', f'{(result["contributions"][i] if i < len(result["contributions"]) else 0):.4f}']))
    return ('<section data-case-relevance><h2>' + t("Query ranking quality", "本题搜索质量分") + '</h2><strong>' + f'{result["ndcg"]*100:.1f} / 100' + '</strong><p>'
            + t("Useful results: ", "可用结果：") + f'{result["useful"]}/{result.get("cutoff", 5)} · ' + t("Grade-3 result: ", "是否有直接满足结果：") + (t("Yes", "有") if result["success"] else t("No", "无"))
            + '</p><details><summary>' + t("Inspect the complete calculation", "查看各条结果的计算过程") + '</summary>'
            + table([t("Rank", "排名"), t("Grade", "等级"), t("Gain", "增益 2^等级−1"), t("Rank weight", "排名权重"), t("Contribution", "得分贡献")], rows)
            + '<p>' + t("Ideal pooled grades: ", "候选池的理想等级：") + esc(str(result["ideal_grades"])) + '</p><p>'
            + (f'{result["dcg"]:.4f} ÷ {result["idcg"]:.4f} × 100 = {result["ndcg"]*100:.1f}' if result['idcg'] else t('No relevant candidate in the pool: score 0.', '候选池全为 0 级，本题质量分记 0。')) + '</p></details></section>')


def cost_panel(profile, rows, zh):
    """Expose frozen unit prices and the actual billable physical-call budget."""
    records = []
    for method in profile["methods"]:
        group = [r for r in rows if r["method"] == method["id"]]
        attempts = [attempt for row in group for attempt in row.get("attempts", [row])]
        costs = [attempt.get("cost") for attempt in attempts]
        total = sum(costs) if costs and all(c is not None for c in costs) else None
        records.append((method, len(group), len(attempts), sum(c is not None and c > 0 for c in costs), total))
    comparable = len({m.get("currency") for m in profile["methods"]}) == 1
    winners = numeric_winners([[r[-1]] for r in records], {0: "min" if comparable else "neutral"})
    rendered = []
    rules = {"http-200-nonempty": ("HTTP 200 且结果非空", "HTTP 200 with nonempty results"), "http-200": ("HTTP 200", "HTTP 200"), "unknown": ("待确定", "Not specified")}
    for method, logical, physical, billed, total in records:
        currency = "元" if zh and method.get("currency") == "CNY" else method.get("currency", "")
        price = method.get("price_per_1000")
        label = ("未定价" if zh else "Not priced") if price is None else fmt(price) + " " + currency
        source = method.get("price_source", "")
        if source.startswith("https://"):
            label = '<a href="' + esc(source) + '">' + label + '</a>'
        rule = rules.get(method.get("billing"), (method.get("billing", ""),) * 2)[0 if zh else 1]
        row = cells([esc(method["label"]), label, esc(method.get("price_checked", "")), esc(rule), str(logical), str(physical), str(billed) if price is not None else ("待定" if zh else "Unknown")])
        label = fmt(total) + " " + currency if total is not None else ("未定价" if zh else "Not priced")
        rendered.append(row[:-5] + numeric_cell(label, total, "cohort-cost", "min" if comparable else "neutral", 0 in winners and total == winners[0]) + '</tr>')
    return ('<section data-cost-budget><h2>' + ("单价、计费次数与实际费用" if zh else "Unit prices, billable calls and estimated charges") + '</h2>'
            + table(["方法", "每千次单价", "核价日期", "计费条件", "搜索任务数", "实际请求数", "可核算计费次数", "全部任务估算费用"] if zh else ["Method", "Price /1,000", "Price checked", "Billing condition", "Search tasks", "Physical calls", "Known billable calls", "Estimated total"], rendered)
            + '<p>' + ("重试增加实际请求数，不增加搜索任务数。费用累加每次物理请求；计费未知时保留未知，不按零元计算。单价链接指向本轮采用的价格来源，实际账单以服务商结算为准。" if zh else "Retries increase physical calls, not search tasks. Charges sum physical requests; unknown billing is not zero. Price links identify the frozen sources; provider invoices determine actual charges.") + '</p></section>')


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
                               str(stats["n"]), f'{stats["ndcg"]*100:.1f}', f'{stats["precision"]:.1%}', f'{stats["success"]:.1%}']))
    category_rows = []
    for family in families:
        scoped = [r for r in scored if r['family'] == family]
        summaries = relevance_summary(scoped, [m['id'] for m in methods])
        values = [summaries[m['id']]['ndcg'] * 100 for m in methods]
        best = max(values)
        row = '<td>' + esc(search_category(family, zh)) + '</td><td>' + str(summaries[methods[0]['id']]['n']) + '</td>'
        for value in values:
            row += numeric_cell(f'{value:.1f}', value, 'category-quality-' + family, 'max', values.count(best) == 1 and value == best)
        category_rows.append('<tr>' + row + '</tr>')
    bars = table((['题型','题数'] if zh else ['Category','Queries']) + [m['label'] for m in methods], category_rows)
    audit = evidence["judge"]
    model = audit["kind"] == "model"
    count = len(evidence["plan"]["case_ids"])
    title = (f"自建题库相关性：{count} 题" if zh else f"In-house relevance: {count} queries")
    definitions = (
        "搜索质量分 = nDCG@5 × 100，衡量有用结果是否排在前面；可用结果比例统计前五条中的 2、3 级结果；直接满足率统计至少找到一条 3 级结果的题目。"
        if zh else
        "Ranking quality is nDCG@5 × 100. Useful-result fraction counts grades 2 and 3 in five slots; direct-match rate counts queries with at least one grade-3 result."
    )
    scope = (
        f"模型裁判：{audit['name']}。本轮评估标题与短摘要所呈现的相关性；没有核验网页全文或摘要事实准确性。标签已冻结，重新计算指标不再调用模型；重新标注可能产生差异。"
        if zh else
        f"Model judge: {audit['name']}. This run evaluates relevance visible in titles and snippets, without verifying full pages or factual accuracy. Frozen labels make metric recalculation deterministic; a new labeling run may differ."
    ) if model else (
        f"人工标注者：{audit['name']}。指标计算使用固定标签，不调用模型。"
        if zh else f"Human annotator: {audit['name']}. Metric calculation uses fixed labels without model calls."
    )
    denominator = ("空结果、不足五条和候选池全为 0 级的题目都留在分母中。" if zh else "Empty responses, missing slots and all-zero candidate pools remain in the denominator.")
    panel = ('<section class="specimen" id="relevance-scores" data-relevance><h3>' + esc(title) + '</h3>' + bars
             + ('' if profile.get("comparison_selection") else '<p>' + esc(definitions) + '</p>')
             + '<details><summary>' + ("可用结果与直接满足比例" if zh else "Useful results and direct matches") + '</summary>' + table(["题型" if zh else "Category", "服务" if zh else "Provider", "题数" if zh else "Queries", "搜索质量分（0–100）" if zh else "Ranking quality /100", "可用结果比例" if zh else "Useful-result fraction", "直接满足率" if zh else "Direct-match rate"], rows) + '</details>'
             + '<p class="muted">' + esc(scope + denominator) + '</p><a href="relevance-judgments.json">'
             + ("查看逐项评分证据" if zh else "Scoring evidence") + '</a></section>')
    first_case = evidence["plan"]["case_ids"][0]
    query = next(c["query"] for c in profile["cases"] if c["id"] == first_case)
    panel += '<details class="score-example"><summary>' + ('A real query: inspect each rank and its score' if not zh else '查看一道实际查询的逐条评分与计算') + '</summary><section data-score-example="' + esc(first_case) + '"><h3>' + ("Actual calculation" if not zh else "一题怎样算分：实际结果") + '</h3><p>' + esc(query) + '</p>'
    for method in methods:
        result = next(r for r in scored if r["case_id"] == first_case and r["method"] == method["id"])
        panel += '<details><summary>' + esc(method["label"]) + ' · ' + f'{result["ndcg"]*100:.1f}/100' + '</summary>' + relevance_detail(result, zh) + '</details>'
    panel += '</section></details>'
    return panel


def factual_panel(profile, selected, zh, *, compact=False):
    excluded = set(profile.get('factual_quality_exclusions', []))
    cases = {c['id']: c for c in profile['cases'] if 'reference_answer' in c and c['id'] not in excluded}
    if not cases:
        return ''
    projected = bool(profile.get("comparison_selection") or profile.get("current_selection"))
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
    if compact:
        headers = ["方法", "找到标注来源（题）", "返回文字含答案（题）"] if zh else ["Method", "Given source found", "Answer text delivered"]
        rendered = []
        best = numeric_winners([(v[2][0]/v[1] if v[1] else None, v[2][3]/v[1] if v[1] else None) for v in values], {0: "max", 1: "max"})
        for label, n, sums in values:
            row = '<td>' + esc(label) + '</td>'
            for i, total in enumerate((sums[0], sums[3])):
                row += numeric_cell(f'{int(total)}/{n} · {total/n:.1%}' if n else '—', total/n if n else None, f'factual-current-{i}', 'max', n > 0 and best.get(i) == total/n)
            rendered.append('<tr>' + row + '</tr>')
        return '<section data-factual><h2>' + (f'公开事实集：{len(cases)} 题的证据发现' if zh else f'Factual evidence retrieval: {len(cases)} queries') + '</h2><p>' + ('在前五条中检查题库给定网址，以及标题、短摘要、长摘要和正文中的参考答案文字。已排除题库转载和模型答题记录；答案文字出现不代表上下文正确。' if zh else 'Check top-five given URLs and answer text in all delivered fields, excluding dataset mirrors and evaluation logs. Text presence does not verify its context.') + '</p>' + table(headers, rendered) + ('<p data-quality-exclusions>' + (f'参考核查共同剔除 {len(excluded)} 道有缺陷的题，质量统计保留 {len(cases)} 题；原始响应、耗时和费用保留完整采集范围。' if zh else f'Reference review excludes {len(excluded)} defective queries for every provider; quality uses {len(cases)} queries. Original responses, timing and cost retain the full collection.') + '</p>' if excluded else '') + '</section>'
    rendered = []
    for label, n, sums in values:
        row = ['<td>' + esc(label) + '</td>', f'<td>{n}</td>']
        for i, total in enumerate(sums):
            direction = "neutral" if i == 2 else "max"
            fractions = [[v[2][i] / v[1] if v[1] else None] for v in values]
            value_raw = total / n if n else None
            best = value_raw is not None and numeric_winners(fractions, {0: direction}).get(0) == value_raw
            value = (f'{total/n:.3f}' if i == 1 else f'{int(total)}/{n} · {total/n:.1%}') if n else '—'
            row.append(numeric_cell(value, value_raw, f"factual-{i}", direction, best))
        rendered.append('<tr>' + ''.join(row) + '</tr>')
    body = '<section data-factual><h3>' + (
        ('公开事实题：题库给定网页与答案文字' if zh else 'Public factual questions: sources and answer cues') if projected else ('公开事实题：基础接口的证据发现' if zh else 'Public factual questions: basic-API evidence discovery')
    ) + '</h3><p>' + (
        '前五条中找到题库列出的任一支持网址，就记一次来源命中。MRR 是首个命中位置的倒数：第 1 条得 1，第 2 条得 0.5，未命中得 0，再对全部题取平均。答案线索只检查三家共有的标题与短摘要。失败、空结果和不足五条都保留，不补齐、不换题。'
        if zh else
        'A source hit means at least one published supporting URL appears in the top five. MRR averages reciprocal first-hit rank: 1 for rank one, 0.5 for rank two and zero for a miss. Answer cues use only shared titles and snippets. Failures, empties and short lists remain in the population.'
    ) + '</p>' + table(
        ['基础接口', '题数', '找到标注来源', '来源排名 MRR', '短摘要含答案：原始', '剔除题库与模型答题记录后'] if zh else
        ['API', 'Queries', 'Given webpage retrieved', 'First-source reciprocal rank', 'Answer text: raw', 'Answer text: dataset mentions excluded'], rendered)
    if projected:
        body = body.replace('答案线索只检查三家共有的标题与短摘要。', '答案线索检查各家前五条实际交付的标题、短摘要、长摘要和正文。').replace('Answer cues use only shared titles and snippets.', 'Answer cues use every delivered title, snippet, summary and body in the top five.').replace('基础接口</th>', '方法</th>').replace('Basic API</th>', 'Method</th>').replace('短摘要含答案：原始', '搜索文字含答案：原始').replace('Preview answer: raw', 'Delivered answer cue: raw')
    body += '<p>' + ('这项过滤是事后诊断：网址或标题出现 SimpleQA，或交付文本包含完整原题，并具有评测字段、答案验证日志或训练集表格结构时，该结果不参与答案检查；原位置留空，不补搜。检查采用固定文字规则，原始数值同时保留。' if zh else 'Contamination exclusion is a post-hoc deterministic diagnostic. Results naming SimpleQA in their URL/title, or containing the complete question together with evaluation fields, validation logs or dataset-viewer structure, are blanked without refilling or compacting ranks. Original values remain visible.') + '</p>'
    if excluded:
        body += '<p data-quality-exclusions>' + (f'独立审查隔离了 {len(excluded)} 道参考答案错误或题意存在实质歧义的题，各接口使用同一质量分母 {len(cases)}；全部题仍计入性能与费用。原题、原始响应和原始检查数值保留在逐题详情。' if zh else f'Independent reference review excluded {len(excluded)} defective or materially ambiguous questions from all methods’ quality metrics, leaving {len(cases)} common queries. All queries remain in latency and cost; original inputs, responses and diagnostics remain inspectable.') + '</p>'
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
        "answer_hit": ("Reference answer text found", "找到参考答案文字"),
        "answer_miss": ("Reference answer text absent", "未找到参考答案文字"),
        "domain_hit": ("Requested site found", "找到指定网站"),
        "domain_miss": ("Requested site absent", "未找到指定网站"),
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
    body = '<section data-timing-decomposition><h2>' + t(f"Client and server timing: {len(profile['cases'])} queries", f"客户端与服务端耗时：{len(profile['cases'])} 题诊断合计") + "</h2>"
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
    body += "</section>"
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


def executive_table(profile, selected, content, scores, zh):
    """Keep every benchmark's primary quality measures visible on the home page."""
    summary_content = dict(content)
    body = population_summary(profile, selected, summary_content, zh, relevance_scores=scores)
    return '<section data-executive>' + body + '</section>'


def discovery_comparison(profile, selected, content, zh):
    """Compare every provider on the same explicit page/site checks."""
    rows = []
    for population in metric_populations(profile, content):
        kind = population.get('metric_kind', population['id'])
        if kind not in ('scenario', 'site'):
            continue
        ids = set(population['case_ids'])
        counts = []
        for method in profile['methods']:
            group = [r for r in selected if r['method'] == method['id'] and r['case_id'] in ids]
            if len(group) != len(ids):
                raise ValueError('Incomplete discovery comparison')
            counts.append(sum(bool(evaluate(next(c for c in profile['cases'] if c['id'] == r['case_id']), r['results'][:profile['protocol']['top_k']])['anchor_hit_10']) if kind == 'scenario' else (r['metrics'].get('domain_precision') or 0) > 0 for r in group))
        best = max(counts)
        label = ('找到指定网页' if kind == 'scenario' else '找到指定网站') if zh else ('Find the given webpage' if kind == 'scenario' else 'Find the requested site')
        cells_html = '<td>' + label + '</td><td>' + str(len(ids)) + '</td>'
        for count in counts:
            cells_html += numeric_cell(f'{count}/{len(ids)}', count, 'discovery-' + kind, 'max', counts.count(best) == 1 and count == best)
        rows.append('<tr>' + cells_html + '</tr>')
    if not rows:
        return ''
    return '<section data-discovery><h2>' + ('指定网页与网站的发现能力' if zh else 'Directed webpage discovery') + '</h2><p>' + ('在自建集有明确网页或网站要求的题目中，核对前五条是否出现指定网址或域名；下表列出各接口的命中数。这两项衡量找页能力，相关性仍看全量质量分。' if zh else 'For scenario queries with explicit webpage/site targets, check whether the top five contain the given URL or domain. These diagnose discovery separately from graded relevance.') + '</p>' + table((['检查','题数'] if zh else ['Check','Queries']) + [m['label'] for m in profile['methods']], rows) + '</section>'


def benchmark_operations(profile, selected, content, zh):
    """Collection labels, populations, retries and currencies come from the inputs."""
    populations = [p for p in metric_populations(profile, content) if p.get("benchmark_root")]
    if not populations:
        cohorts = sorted({r.get("cohort", "primary") for r in selected})
        populations = [{"id": key, "label": {"zh": cohort_label(profile, key, True), "en": cohort_label(profile, key, False)}, "case_ids": sorted({r["case_id"] for r in selected if r.get("cohort", "primary") == key})} for key in cohorts]
    rendered = []
    comparable = len({m.get("currency", "CNY") for m in profile["methods"]}) == 1
    for population in populations:
        ids = set(population["case_ids"])
        stats = aggregate([r for r in selected if r["case_id"] in ids])
        values = [(m, stats[m["id"]]) for m in profile["methods"]]
        raw = [[item["all_latency"][k] for k in ("mean", "p50", "p95")] + [item["cost_total"]] for _, item in values]
        directions = {0:"min", 1:"min", 2:"min", 3:"min" if comparable else "neutral"}
        winners = numeric_winners(raw, directions)
        for index, (method, item) in enumerate(values):
            row = '<td>' + esc(population.get("label", {"zh": population.get("id", "评测集"), "en": population.get("id", "Benchmark")})["zh" if zh else "en"]) + '</td><td>' + esc(method["label"]) + '</td>'
            for i, value in enumerate(raw[index]):
                label = fmt(value) if value is not None else ("未定价" if zh else "Not priced")
                if i == 3 and value is not None:
                    label += " " + esc(method.get("currency", "CNY"))
                row += numeric_cell(label, value, f'operation-{population["id"]}-{i}', directions[i], value is not None and winners.get(i) == value)
            rendered.append('<tr>' + row + '</tr>')
    if not rendered:
        return ''
    retries = int(bool(profile["protocol"].get("retry")))
    note = (f"每题每个接口保留一个最终结果，失败最多重试 {retries} 次；耗时和费用计入实际重试。P95 表示 95% 的请求在该时间内结束。" if zh else f"One final result per provider and query, with at most {retries} failure retries; timing and cost include actual retries. P95 bounds 95% of waits.")
    return '<section data-benchmark-operations><h2>' + ("各采集批次的性能与费用" if zh else "Performance and cost by collection") + '</h2><p>' + note + '</p>' + table(["实测批次","接口","平均耗时 ms","P50 ms","P95 ms","本批估算费用"] if zh else ["Collection","API","Mean ms","P50 ms","P95 ms","Estimated batch cost"], rendered) + '</section>'


def quality_dataset(profile, content):
    """Active dataset excludes rejected references; archived calls stay in results."""
    excluded = set(profile.get("factual_quality_exclusions", []))
    if not excluded:
        return profile, content
    active = copy.deepcopy(profile)
    active["cases"] = [case for case in active["cases"] if case["id"] not in excluded]
    for cohort in active.get("report_cohorts", []):
        cohort["case_ids"] = [cid for cid in cohort["case_ids"] if cid not in excluded]
    authored = copy.deepcopy(content)
    for population in authored.get("metric_populations", []):
        population["case_ids"] = [cid for cid in population["case_ids"] if cid not in excluded]
    return active, authored


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


def population_summary(profile, selected, content, zh, *, diagnostics=False, relevance_scores=None):
    lang = "zh" if zh else "en"
    independent = content.get("independent_benchmarks")
    roots = [p for p in metric_populations(profile, content) if p.get("benchmark_root")] if independent else []
    heading = ("各评测集的核心结果" if zh else "Core results by benchmark")
    scope = "；".join(p["label"][lang] + " " + str(len(set(p["case_ids"]) - set(profile.get("factual_quality_exclusions", [])))) + (" 题" if zh else " queries") for p in roots)
    note = ((scope + ("。各题库分别统计。" if zh else ". Benchmarks are reported separately. ")) if scope else "") + (
        "平均耗时、P50 和 P95 均计入全部请求与实际重试等待；费用按对应题组的实际调用估算。" if zh else "Mean, P50 and P95 cover all requests and retry waits; fees estimate the calls in each population.")
    if diagnostics:
        heading = "参考网址与指定网站：辅助检查" if zh else "Reference URLs and requested sites: diagnostics"
        note = "下列检查只解释具体网址与域名的发现情况，题目可重叠；统一相关性评分仍覆盖完整自建题库。" if zh else "These overlapping subsets diagnose URL and domain discovery. Uniform relevance scoring covers the complete in-house set."
    body = '<section data-populations><h2>' + heading + '</h2><p class="population-note">' + note + '</p>'
    for population in metric_populations(profile, content):
        if independent and bool(population.get("benchmark_root")) == diagnostics:
            continue
        ids = set(population['case_ids'])
        quality_ids = ids - set(profile.get('factual_quality_exclusions', []))
        subset = [r for r in selected if r['case_id'] in ids]
        stats = aggregate(subset)
        kind = population.get('metric_kind', population['id'])
        ranked = bool(relevance_scores) and ids.issubset({r['case_id'] for r in relevance_scores}) and not diagnostics
        quality = ranked or kind in ('factual', 'scenario', 'site')
        quality_stats = relevance_summary([r for r in (relevance_scores or []) if r['case_id'] in ids], [m['id'] for m in profile['methods']]) if ranked else {}
        cases = {c['id']: c for c in profile['cases']}
        values = []
        for method in profile['methods']:
            s = stats.get(method['id'])
            if not s or s['n'] != len(ids):
                raise ValueError('Incomplete metric population')
            group = [r for r in subset if r['method'] == method['id']]
            if ranked:
                q = quality_stats[method['id']]
                measures = [q['ndcg'] * 100, q['precision'] * profile['protocol']['top_k'], sum(r['success'] for r in relevance_scores if r['case_id'] in ids and r['method'] == method['id'])]
            elif kind == 'factual':
                quality_group = [r for r in group if r['case_id'] in quality_ids]
                measures = [sum(r['metrics']['labeled_source_hit_5'] for r in quality_group), sum(factual_sensitivity(cases[r['case_id']], r['results'])['metrics']['answer_in_delivered_text_5'] for r in quality_group)]
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
        max_column = 3 if ranked else 2 if quality else 1
        directions = {i: "max" if i <= max_column else "min" for i in range(1, len(values[0]))}
        if len({m.get("currency", "CNY") for m in profile["methods"]}) != 1:
            directions[len(values[0]) - 1] = "neutral"
        winners = numeric_winners(values, directions)
        rendered = []
        for row, method in zip(values, profile['methods'], strict=True):
            html_cells = []
            for i, value in enumerate(row):
                if i == 0:
                    label = esc(value)
                elif ranked and i == 1:
                    label = f'{value:.1f}'
                elif ranked and i == 2:
                    label = f'{value:.2f} / {profile["protocol"]["top_k"]}'
                elif ranked and i == 3:
                    label = f'{int(value)}/{len(ids)}'
                elif i == 1 or (kind == 'factual' and i == 2):
                    denominator = len(quality_ids) if kind == 'factual' else len(ids)
                    label = (f'{int(value)}/{denominator}' + (f' · {value / denominator:.1%}' if quality else '')) if denominator else '—'
                elif kind == 'scenario' and i == 2:
                    label = f'{value:.3f}'
                else:
                    label = fmt(value)
                if i == len(row) - 1:
                    currency = method.get('currency', 'CNY')
                    label = (('未定价' if zh else 'Not priced') if value is None else label + ' ' + ('元' if zh and currency == 'CNY' else esc(currency)))
                win = i in winners and winners[i] == value
                html_cells.append(numeric_cell(label, value if i else None, f"population-{i}", directions.get(i, "neutral"), win))
            rendered.append('<tr>' + ''.join(html_cells) + '</tr>')
        body += '<article data-population="' + esc(population['id']) + '"><h3>' + esc(population['label'][lang]) + f' · {len(quality_ids) if kind == "factual" else len(ids)}' + (' 题' if zh else ' queries') + '</h3>'
        if population.get('description'):
            body += '<p>' + esc(population['description'][lang]) + '</p>'
        headings = ['返回结果的题数'] if zh else ['Queries returning results']
        if ranked:
            headings = ['搜索质量分（0–100）', '每题可用结果（条/5）', '有直接满足结果的题数'] if zh else ['Ranking quality (0–100)', 'Useful results per query (/5)', 'Queries with a direct match']
            body += '<p><a href="quality.html#relevance-scores">' + ('质量分 = nDCG@5 × 100 · 查看等级和计算方法' if zh else 'Quality = nDCG@5 × 100 · Inspect grades and calculation') + '</a></p>'
        elif kind == 'factual':
            if quality_ids != ids:
                body += '<p data-quality-exclusions>' + ('性能与费用统计包含已隔离题的实际请求；质量指标使用上方有效题目。' if zh else f'Quality uses {len(quality_ids)} reviewed-reference queries for every method; {len(ids) - len(quality_ids)} defective references are quarantined. Latency and cost retain all {len(ids)} queries.') + '</p>'
            body += '<p class="population-note">' + ('检查前五条结果：是否搜到题库给定的网址，以及交付文字是否包含参考答案。答案文字检查剔除明确的题库转载和包含原题及评测或训练数据结构的记录。' if zh else 'Source found: a published supporting URL appears in the top five. Answer cue: allowed reference-answer text appears in delivered text after excluding explicit dataset mirrors; this is not semantic accuracy.') + '</p>'
            headings = ['搜到题库给定网页的题数', '搜索文字含参考答案的题数'] if zh else ['Published source found', 'Answer cue (mirrors excluded)']
        elif kind == 'scenario':
            body += '<p class="population-note">' + ('目标命中：前五条找到参考页面。MRR 衡量首个目标位置，第一条得 1、第二条得 0.5、未找到得 0，再对全部题取平均；越高表示目标越靠前。' if zh else 'Target found: a reference page appears in the top five. MRR averages reciprocal first-target rank: 1 for first, 0.5 for second, and 0 for a miss. Higher means targets appear earlier.') + '</p>'
            headings = ['找到目标页面', '目标排名 MRR'] if zh else ['Reference target found', 'Target MRR']
        elif kind == 'site':
            body += '<p class="population-note">' + ('站点满足：前五条至少一条来自题目指定域名；域名占比是前五条中指定域名结果的平均比例。两项都不判断页面内容是否相关。' if zh else 'Site satisfied: at least one top-five result uses the requested domain. Domain fraction averages the share of top-five results from that domain. Neither metric judges page relevance.') + '</p>'
            headings = ['满足站点约束', '指定域名占比'] if zh else ['Site constraint met', 'Requested-domain fraction']
        headers = (['方法'] + headings + ['平均耗时 ms','P50 耗时 ms','P95 耗时 ms','目录价估算费用']) if zh else (['Method'] + headings + ['Mean ms','P50 ms','P95 ms','Population cost'])
        body += table(headers, rendered)
        if ranked:
            body += '<details><summary>' + ('接口交付情况' if zh else 'API delivery') + '</summary><p>' + ' · '.join(esc(m['label']) + ' ' + str(stats[m['id']]['nonempty']) + '/' + str(len(ids)) for m in profile['methods']) + (' 题返回了结果。' if zh else ' queries returned results.') + '</p></details>'
        body += '</article>'
    return body + '</section>'


def render_result_comparison(profile, selected, zh, *, prefix="", site_prefix="", content=None, relevance=None):
    from .comparison import render_comparison
    profile = dict(profile, comparison_populations=metric_populations(profile, content or {}))
    return render_comparison(profile, selected, zh, relevance=relevance, detail_link=lambda row: posixpath.join(site_prefix, prefix, "details", row["id"] + ".html"))


def population_filter(profile, content, zh):
    if content.get("presentation") != "comparison-report":
        return ""
    groups = [p for p in metric_populations(profile, content) if p["id"] != "overall" and not p.get("benchmark_root")]
    if not groups:
        return ""
    return '<label>' + ("辅助检查" if zh else "Diagnostic subset") + '<select data-population-filter><option value="all">' + ("全部" if zh else "All") + '</option>' + ''.join('<option value="' + esc(p["id"]) + '" data-case-ids="' + esc(json.dumps(p["case_ids"])) + '">' + esc(p["label"]["zh" if zh else "en"]) + f' ({len(p["case_ids"])})</option>' for p in groups) + '</select></label>'


def annotation_detail(annotation, zh):
    text = ("本题参考页面在接口采集后，依据原始问题独立补充；没有重新调用搜索接口。" if zh else "This reference was added independently from the original query after collection. Search APIs were not called again.")
    body = '<section class="specimen"><h3>' + ("后补参考依据" if zh else "Additional reference evidence") + '</h3><p>' + text + '</p>'
    for source in annotation['sources']:
        body += '<p><a href="' + esc(source['url']) + '">' + esc(source['title']) + '</a> · ' + esc(source['verified_at']) + '</p><blockquote>' + esc(source['excerpt']) + '</blockquote>'
    return body + '</section>'


def dataset_context(profile, content, zh):
    if content.get("presentation") != "comparison-report":
        return ''
    populations = [p for p in metric_populations(profile, content) if p.get("benchmark_root")]
    if not populations:
        populations = [{"label": {"zh": profile.get("title", "评测集"), "en": profile.get("title", "Benchmark")}, "case_ids": [c["id"] for c in profile["cases"]]}]
    rows = []
    for population in populations:
        cases = [c for c in profile["cases"] if c["id"] in population["case_ids"]]
        if not cases:
            continue
        factual = all("reference_answer" in c for c in cases)
        purpose = ("检查给定网页与参考答案文字" if factual else "评价查询结果的相关性") if zh else ("Given-source and reference-answer text checks" if factual else "Query-result relevance")
        rows.append(cells([esc(population.get("label", {"zh": population.get("id", "评测集"), "en": population.get("id", "Benchmark")})["zh" if zh else "en"]), str(len(cases)), purpose]))
    originals = sum(c.get("provenance_kind") in {"community_exact", "user_screenshot"} for c in profile["cases"])
    note = (f"含 {originals} 道社区原题。各题的原始查询、来源网址和改编关系列在下表。" if zh else f"Includes {originals} exact community queries. Original queries, source URLs and derivation are listed below.") if originals else ("各题的原始查询、来源网址和参考依据列在下表。" if zh else "Original queries, source URLs and references are listed below.")
    return '<section><h2>' + ("评测集与评估范围" if zh else "Datasets and evaluation scope") + '</h2>' + table(["题库","题数","用途"] if zh else ["Dataset","Queries","Purpose"], rows) + '<p>' + note + '</p></section>'
