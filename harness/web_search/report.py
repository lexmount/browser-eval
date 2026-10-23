"""Small bilingual Web Search report built from frozen calls, never invented scores."""

from .messages import report_text

from lexbench_websearch.inputs import definition_path, implementation_sources

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

from harness.record_io import same_json_record
from harness.reporting.presentation import check_presentation

from .core import aggregate, digest, normalize, request_cost, request_body, validate_profile, verify_bundle, write_json
from lexbench_websearch.scoring import canonical_url, classify, evaluate, in_domain
from lexbench_websearch.evidence_quality import factual_sensitivity, CONTAMINATION_RULE
from .diagnostics import operational
from .runner import reference_available, reference_changes
from .profiles import load_profile, _bound
from lexbench_websearch.revisions import apply_identity_correction
from .relevance import verify_provenance
from lexbench_websearch.relevance import score as score_relevance, summary as relevance_summary

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
        token = match.group(0)
        candidate = re.split(rb"\]\(|[,;](?:mailto:|https?://)", token, maxsplit=1, flags=re.I)[0]
        authority = candidate.split(b"://", 1)[1].split(b"/", 1)[0].split(b"?", 1)[0].split(b"#", 1)[0]
        for separator in (b"'", b")"):
            if separator in authority and b":" not in authority.split(separator, 1)[0]:
                candidate = candidate.split(separator, 1)[0]
        try:
            parsed = urlsplit(candidate.decode("utf-8"))
            if not parsed.hostname or parsed.username is None:
                return token
        except (ValueError, UnicodeError):
            return token
        start = candidate.index(b"://") + 3
        end = start + len(parsed.netloc.encode("utf-8"))
        netloc = candidate[start:end]
        redacted = candidate[:start] + b"REDACTED:REDACTED@" + netloc.rsplit(b"@", 1)[1] + candidate[end:]
        return redacted + token[len(candidate):]
    return re.sub(rb"https?://[^\s\"<>\\]+", replace, raw, flags=re.I)


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
            normalized = normalize(json.loads((source / r["evidence"]["path"]).read_bytes()), next(m["adapter"] for m in profile["methods"] if m["id"] == r["method"]))
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
        protocol["review"] = "Complete result audit with per-output opinions and finding adjudications." if review["scope"] == "all" else "Failure-only result audit; successful outputs outside the review are counted explicitly."
    return dict(profile, protocol=protocol, **({"independent_result_review_scope": review["scope"], "factual_quality_exclusions": exclusions} if review else {}))

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
    authority = {}
    if profile_path is not None:
        selector = json.loads(Path(profile_path).read_bytes())
        if selector.get("assessment_authority"):
            authority = _bound(Path(profile_path).resolve().parent, selector["assessment_authority"])
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
        from lexbench_websearch.revisions import apply_report_revision
        from lexbench_websearch.revisions import validate_revision_binding
        validate_revision_binding(authored["assessment_revision"])
        profile, rows, changes = apply_report_revision(profile, rows, authored["assessment_revision"])
        assessment_revision = {"contract": authored["assessment_revision"], "changes": changes, "implementation_sha256": {name: digest(path.read_bytes()) for name, path in implementation_sources().items()}}
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
        validate_public_review(profile, selected, review_data, authority=authority)
        write_json(output / "independent-review.json", review_data)
    relevance_data = json.loads(relevance.read_text()) if relevance else None
    if relevance_data:
        if "plan" not in relevance_data:
            from .relevance import import_annotations
            relevance_data = import_annotations(profile, selected, relevance_data)
        verify_provenance(profile, selected, relevance_data, authority=authority)
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
    diagnostics = operational(rows, payloads, profile["methods"])
    bundle = []
    if authority:
        write_json(output / "assessment-authority.json", authority)
        bundle.append({"path": "assessment-authority.json", "sha256": digest((output / "assessment-authority.json").read_bytes())})
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
    notice = definition_path("NOTICE.txt")
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

        from .pages import reader_pages
        page_registry = reader_pages(authored.get("presentation"), reviewed=bool(review_data))
        nav = [(p["route"], p[lang]) for p in page_registry if p["nav"]]
        if reading_sections and authored.get("presentation") != "comparison-report":
            nav = (
                [("index.html", t("Overview", report_text('text_a33db573055626c5')))]
                + [("index.html#" + p["key"], p["nav"][lang]) for p in reading_sections]
                + [("interpretation.html", t("Report interpretation", report_text('text_0abc777fea96e652')))]
                + [entry for entry in nav[1:] if entry[0] != "interpretation.html"]
            )
        state = (
            t(
                ("Measured API evaluation · frozen model relevance labels and deterministic metrics." if relevance_data and relevance_data["judge"]["kind"] == "model" else "Measured API evaluation · human relevance labels and deterministic metrics." if relevance_data else "Measured API evaluation · automated target checks, no relevance labels."),
                (report_text('text_a9bb4ccabe9fcc73') if relevance_data and relevance_data["judge"]["kind"] == "model" else report_text('text_5245565d03c0824f') if relevance_data else report_text('text_8af2174ccb448818')),
            )
            if rows
            else t(
                "Evaluation design preview. No API performance ranking.",
                report_text('text_cca9e76cd4ffb998'),
            )
        )
        if rows and len(profile["methods"]) == 1:
            state = t(
                "Single-provider preflight; no cross-provider comparison.",
                report_text('text_34492dc2ceda72b9'),
            )
        if receipt.get("synthetic"):
            state = t(
                "Synthetic fixture — not API measurements.",
                report_text('text_969eb9262bbc997e'),
            )
        if rows and not receipt["certified"]:
            state += t(" Diagnostic evidence.", report_text('text_f7dd959c2d8a9c9f'))
        if authored.get("reading_model") and rows and receipt.get("certified"):
            state = t(
                "Measured Search API responses and documented capabilities · each metric states its evidence scope.",
                report_text('text_6cdf5d0ba27a85f3'),
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
                navigation += f'''<a href="{language_depth}{otherprefix}{route}">{(report_text('text_72726d8818f69306') if other == 'zh' else 'English')}</a>'''
            elif lang == "zh" and authored.get("presentation") != "comparison-report":
                language_depth = "../" * (len(Path(prefix + route).parts) - 1)
                navigation += f'<a href="{language_depth}en/index.html">English</a>'
            navigation += f"<button data-theme>{t('Theme', report_text('text_7a1604323e8bf3f2'))}</button>"
            if site_prefix:
                navigation += f'''<a href="{depth}index.html">{t('Main comparison', report_text('text_1d170fc477c2f686'))}</a>'''
            notice_html = f'<p class="notice" data-state>{state}</p>'
            if authored.get("presentation") == "comparison-report" and receipt.get("certified") and not receipt.get("synthetic"):
                notice_html = ''
            document = BeautifulSoup(
                f'''<!doctype html><html lang="{lang}"><head><base href="{depth or './'}"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · {t('Web Search', report_text('text_e369180d1c13449e'))}</title><style>{STYLE}</style></head><body><nav>{navigation}</nav><main><p class="muted">{t('Browser Eval / Web Search', report_text('text_fb574fbdbd60847c'))}</p><h1>{esc(title)}</h1>{notice_html}{body}</main><dialog aria-label="{t('Response evidence', report_text('text_2ac6c8d14b3dc61e'))}"><button data-close>{t('Close', report_text('text_3fd47edce45b3603'))}</button><pre></pre><a hidden>{t('Full result', report_text('text_22fcdadc6dad4ecf'))}</a></dialog><script>{SCRIPT}</script></body></html>''',
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
                for target, label in [("research.html", t("Method and FAQ", report_text('text_8adb3edf2fc73aea'))), ("review.html", t("Review evidence", report_text('text_d1b3772e32c35651')))]:
                    if target == "review.html" and not review_data:
                        continue
                    link = document.new_tag("a", href=posixpath.join(site_prefix, prefix, target))
                    link.string = label
                    if target == "review.html":
                        link.string = label + f" · {review_data['case_reviewed']}/{review_data['case_total']}" + t(" queries · ", report_text('text_27da4846b99bd98a')) + f"{review_data['output_reviewed']}/{review_data['output_total']}" + t(" outputs", report_text('text_01623b4507f883b0'))
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
            t("Method", report_text('text_22b9f0b66212809c')),
            t("Nonempty / queries", report_text('text_d61de160acc9c43c')),
            t("Mean ms", report_text('text_bcefbf4dbcaf5f47')),
            "P50 ms",
            "P95 ms",
            t(f"Known page@{cutoff}", f"{report_text('text_5359a336caf6bbbe')}{cutoff}{report_text('text_f004f1d84cf962dc')}"),
            t("Price / 1k", report_text('text_df063b20c160707e')),
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
                    label = f"{value * 100:.1f}% ({summary[m['id']]['metrics'][hit_key]['n']}{report_text('text_33f1546af31a9b68')}" if zh else f"{value * 100:.1f}% ({summary[m['id']]['metrics'][hit_key]['n']} queries)"
                win = i in winners and value == winners[i]
                td.append(numeric_cell(label, value if i else None, ("method", "nonempty", "mean-ms", "p50-ms", "p95-ms", "target-hit", "price-per-1000")[i], directions.get(i, "neutral"), win))
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
                    f"{len(receipt['reference_capture_issues'])}{report_text('text_7d552fe08e9791e8')}",
                )
                + "</p>"
            )

        body += (
            "<p>"
            + t(
                "All-call latency includes failures and every declared round. P50 is the median; P95 uses nearest rank. Quality uses only the preselected first round. A known-page miss is not proof of irrelevant results.",
                report_text('text_ac69b7551791b47e'),
            )
            + "</p>"
        )
        body += (
            "<p>"
            + t(
                "Charges use the prices and billing rules frozen in each method configuration. Unset prices remain unavailable. API charges exclude downstream models and additional fetches.",
                report_text('text_4b30b66acba03375'),
            )
            + "</p>"
        )
        body += comparison_tables(profile, selected, rows, zh)
        body += (
            "<p>"
            + t(
                f"Collected methods: {len(profile['methods'])}. Declared rounds: {profile['protocol']['rounds']}.",
                f"{report_text('text_1bb9b2459cc4c0f6')}{len(profile['methods'])}{report_text('text_0c540173c9571d03')}{profile['protocol']['rounds']}。",
            )
            + "</p>"
        )
        body += (
            "<h2>"
            + t("What is compared", report_text('text_1c095347dbab5203'))
            + '</h2><div class="flow">'
            + "".join(
                "<span>" + v + "</span>"
                for v in [
                    t(
                        f"{len(profile['cases'])} frozen queries",
                        f"{len(profile['cases'])}{report_text('text_47e88501efe654cc')}",
                    ),
                    t(f"Same top-{cutoff} budget", f"{report_text('text_16a2803e355f0ea0')}{cutoff}{report_text('text_b65b4ae3e6740572')}"),
                    t("Serial API calls", report_text('text_43d29ffbcadf24cf')),
                    t("Saved responses & metrics", report_text('text_ac12a2bc9f600760')),
                ]
            )
            + "</div>"
        )
        body += (
            "<p>"
            + t(
                "Web, site-restricted and vertical queries remain separate populations. Saved requests show each method’s native controls; the comparison uses delivered search text without an answering model.",
                report_text('text_d7fff8e0e848d6df'),
            )
            + "</p>"
        )
        if rows:
            body += latency_rounds(profile, rows, zh)
            body += "<h2>" + t("Successful-call latency", report_text('text_94ad856f2c0d1003')) + "</h2>"
            body += table(
                [t("Method", report_text('text_22b9f0b66212809c')), "N", t("Mean ms", report_text('text_cfb4169ac2aa7957')), "P50 ms", "P95 ms"],
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
                "<h2>" + t("Measured baseline", report_text('text_dba5015fcd56f1b0')) + "</h2>"
                + "<p>" + esc(cohort_label(profile, authored.get("overview_cohort"), zh)) + "</p>"
            )
            overview_case_ids = {r["case_id"] for r in overview_rows}
            target_count = sum(bool(c.get("anchors") or c.get("paper_ids") or c.get("repositories"))
                               for c in profile["cases"] if c["id"] in overview_case_ids)
            body += '<p class="muted">' + t(
                f"Nonempty counts queries with at least one result. Mean, P50 (median) and P95 cover all {len(overview_case_ids)} queries in this table; 95% finish within the P95 time. Known page@{cutoff} checks whether a target URL appears in the first {cutoff} results, across {target_count} predeclared target-page queries.",
                f"{report_text('text_04231f67bb858166')}{len(overview_case_ids)}{report_text('text_31f0f85613468c80')}{cutoff}{report_text('text_d2bd10e79ce103ef')}{target_count}{report_text('text_b5871a9b82a533c5')}{cutoff}{report_text('text_81efd8dae4b625db')}",
            ) + '</p>'
            body += table(headers, core, True)
            if profile["protocol"].get("retry"):
                body += '<p class="lede">' + t(
                    f"The same {len(profile['cases'])} queries went to every provider. Each query has one final result; transport, HTTP or invalid-response failures receive one retry. Latency covers the complete user-visible request, including any retry. Empty valid results are not retried.",
                    f"{report_text('text_158df4d4fa9e1056')}{len(profile['cases'])}{report_text('text_6596044233c5658d')}",
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
        if not any(c["key"] == "speed" for c in reading_sections) and authored.get("presentation") != "comparison-report":
            body += mode_panel(profile, selected, zh)
        if authored.get("presentation") == "comparison-report":
            body = authored_sections(authored, "lead", lang, rows=selected)
            body += executive_table(profile, selected, authored, relevance_scores, zh)
            body += discovery_comparison(profile, selected, authored, zh)
            body += authored_sections(authored, "summary", lang, rows=selected)
            for chapter_key, chapter_title in [("capabilities", t("API outputs", report_text('text_aabf873ca20d6909'))), ("quality", t("Retrieval quality", report_text('text_243ff1bffea7c23d')))]:
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
            body += f'<p data-review-coverage data-reviewed-queries="{review_data["case_reviewed"]}/{review_data["case_total"]}" data-reviewed-outputs="{review_data["output_reviewed"]}/{review_data["output_total"]}"><a href="review.html">' + t(
                f"Reviewed {review_data['case_reviewed']}/{review_data['case_total']} queries and {review_data['output_reviewed']}/{review_data['output_total']} outputs",
                f"{report_text('text_90bb19b8d33a329b')}{review_data['case_reviewed']}/{review_data['case_total']}{report_text('text_268af354b8e63839')}{review_data['output_reviewed']}/{review_data['output_total']}{report_text('text_e8f9808a4dafd938')}") + '</a></p>'
        page(
            "index.html",
            authored.get("title", {}).get(lang)
            or (
                t(
                    f"Web Search: {len(profile['cases'])}-query comparison",
                    f"Web Search：{len(profile['cases'])}{report_text('text_d0775ab56f625fdf')}",
                )
                if profile.get("report_cohorts")
                else t("Search quality, speed and cost", report_text('text_764d73f3ff49230c'))
            ),
            body,
        )
        dataset_profile, dataset_content = quality_dataset(profile, authored)
        ds = []
        for c in dataset_profile["cases"]:
            anchors = "<br>".join(
                f'''<a href="{esc(u)}" title="{esc(u)}">{t('Target page', report_text('text_cf4e4de6abf5f716'))} · {esc(u.split('://', 1)[-1].split('/', 1)[0])}</a>'''
                for u in c.get("anchors", [])
            ) or t("No target URL declared", report_text('text_0d3a5e4525741ea7'))
            if "reference_answer" in c:
                anchors = esc(c["reference_answer"]) + "<br>" + "<br>".join('<a href="' + esc(u) + '">' + t("Labeled source", report_text('text_78033f4f62f52aeb')) + "</a>" for u in c.get("reference_urls", []))
            if c.get("paper_ids") or c.get("repositories"):
                anchors += "<br>" + esc(
                    ", ".join(c.get("paper_ids", []) + c.get("repositories", []))
                )
            if c.get("identity_aliases"):
                anchors += "<br>" + t("Verified equivalent URLs: ", report_text('text_7f9885c5b27df0ef'))
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
                        esc(t({"zh":"Chinese","en":"English"}.get(c["language"], c["language"]), {"zh":report_text('text_72726d8818f69306'),"en":report_text('text_348ea84c60466c77')}.get(c["language"], c["language"]))),
                        esc(search_category(c.get("category", c["family"]), zh)),
                        esc(provenance_label(c.get("provenance_kind", "curated"), zh)),
                        esc(c["query"]),
                        anchors,
                        esc(source_note(c, zh))
                        + "<br>"
                        + (
                            f'<a href="{esc(c["origin"])}">' + t("Source", report_text('text_a488e93d69cc1296')) + "</a>"
                            if c["origin"].startswith("https://")
                            else ""
                        ),
                    ]
                )
            )
        page(
            "dataset.html",
            t("Tests and sources", report_text('text_1825163793da2dc1')),
            dataset_context(dataset_profile, dataset_content, zh)
            + '<p><a href="https://github.com/lexmount/lexbench-websearch">'
            + t("LexBench Web Search: datasets, source attribution and scoring", report_text('text_ee89dfa0e1e48505'))
            + "</a></p>"
            + provenance_table(dataset_profile, zh)
            + f'<p><a href="{"../" if prefix else ""}query-catalog-license.txt">'
            + t("Public query catalog attribution and license", report_text('text_b6d4b24495e14f52'))
            + "</a></p>"
            + '<div class="dataset-controls">' + origin_filter(dataset_profile, zh)
            + cohort_filter(dataset_profile, zh)
            + population_filter(dataset_profile, dataset_content, zh)
            + "<label>"
            + t("Search queries, categories or sources", report_text('text_ec69dcf6cbd3b04a'))
            + " <input data-query></label></div>"
            + table(
                [
                    "ID",
                    t("Benchmark", report_text('text_a18713d1bce780df')),
                    t("Language", report_text('text_9f6fee1aba17a565')),
                    t("Category", report_text('text_0d12cbd6562bfbff')),
                    t("Provenance", report_text('text_38e1e77b3ef39016')),
                    t("Query", report_text('text_bcd6771e08ec7cc7')),
                    t("Reference page / answer", report_text('text_e83c50eaaaf7f796')),
                    t("Provenance", report_text('text_a488e93d69cc1296')),
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
            query_relevance = next((x for x in (relevance_scores or []) if x["case_id"] == cid and x["method"] == r["method"]), None)
            status, reason = classify(c, r, cutoff=cutoff, quality_excluded=cid in profile.get("factual_quality_exclusions", []), relevance_grades=query_relevance["ranked_grades"] if query_relevance else None)
            reason_label = {
                "relevance_hit": t("Useful result found", report_text('text_5d0f68818f94fb8f')),
                "relevance_miss": t("No useful result found", report_text('text_fb6a5abc9982f07b')),
                "quality_excluded": t("Reference qualification excluded", report_text('text_a280e1299dc83a91')),
                "api_error": t("API or transport error", report_text('text_c72a7ed119fc66d0')),
                "empty": t("Empty result list", report_text('text_7f0485c71d98ed07')),
                "domain_mismatch": t(
                    "Results outside the requested domain", report_text('text_af83087a64f156b8')
                ),
                "target_miss": t(
                    f"Reference target absent from top {cutoff}", f"{report_text('text_c5a8970d6255caf1')}{cutoff}{report_text('text_fb01321d1e2a37fa')}"
                ),
                "target_hit": t("Reference target found", report_text('text_cf9c0939c50cc624')),
                "source_hit": t("Published supporting source found", report_text('text_abae36f305e845f6')),
                "answer_hit": t("Reference answer text found", report_text('text_7816b19b994a78de')),
                "answer_miss": t("Reference answer text absent", report_text('text_79ab32335757995f')),
                "domain_hit": t("Requested site found", report_text('text_a0e07ba41d17b392')),
                "domain_miss": t("Requested site absent", report_text('text_0f57d58cae8be42d')),
                "source_miss": t("Published supporting source not found", report_text('text_83dcaec1ec7d72a6')),
                "probe": t("Unjudged query", report_text('text_d0baa0ed82201ee7')),
            }[reason]
            target = "details/" + r["id"] + ".html"
            state_label = {
                "fail": t("Miss / error", report_text('text_c02de39c5b238824')),
                "pass": t("Target hit", report_text('text_2d36ab3071b5de6d')),
                "probe": t("Unjudged query", report_text('text_d0baa0ed82201ee7')),
            }[status]
            diagnostic_text = "\n".join(
                [
                    c["query"],
                    reason_label,
                    f"HTTP {r['http_status']}; {len(r['results'])} " + t("results", report_text('text_bd0bedc6f45def61')),
                    r["error"]
                    or (
                        t("Empty results array", report_text('text_ccaeaa7e723ac488'))
                        if not r["results"]
                        else t(
                            "Compare returned URLs with the independent reference target in the full result.",
                            report_text('text_06912b774b6386f7'),
                        )
                    ),
                ]
            )
            inventory.append(
                f'''<tr data-row data-state="{status}" data-method="{esc(r['method'])}" data-cohort="{esc(c.get('cohort', ''))}" data-origin="{esc(origin_group(c))}"><td>{esc(cid)}<br>{esc(cohort_label(profile, c.get('cohort'), zh))}</td><td>{esc(c['query'])}</td><td>{esc(method_labels[r['method']])}</td><td>{esc(state_label)}</td><td>{fmt(r['elapsed_ms'])}</td><td><button data-diagnosis="{esc(diagnostic_text)}" data-detail="{target}">{t('Diagnosis', report_text('text_40ff6300f9817deb'))}</button> <a href="{target}">{t('Details', report_text('text_979a332955c855bf'))}</a></td></tr>'''
            )
            # Relative path from details/ to shared evidence at output root.
            ev = ("../../" if prefix else "../") + r["evidence"]["path"]
            detail = f"<section data-summary><p>{esc(c['query'])}</p><p>HTTP {r['http_status']} · {fmt(r['elapsed_ms'])} ms · {len(r['results'])} {t('results', report_text('text_bd0bedc6f45def61'))}</p></section>"
            if query_relevance:
                detail += relevance_detail(query_relevance, zh)
            if c.get('anchors'):
                detail += '<h2>' + t('Webpage discovery references', report_text('text_e92aba892cac14ff')) + '</h2><ul>' + ''.join('<li><a href="' + esc(url) + '">' + esc(url) + '</a></li>' for url in c['anchors']) + '</ul>'
            elif c.get('domains'):
                detail += '<h2>' + t('Requested sites', report_text('text_9d669bb58c61d983')) + '</h2><p>' + esc('、'.join(c['domains'])) + '</p>'
            if c.get("reference_annotation"):
                detail += annotation_detail(c["reference_annotation"], zh)
            elif c.get("anchors"):
                detail += (
                    "<p>"
                    + t(
                        "These URLs define the known-page check; relevance is scored separately.",
                        report_text('text_c99107b1385b8f76'),
                    )
                    + "</p>"
                )
            if "reference_answer" in c:
                detail += "<h3>" + t("Published answer and sources", report_text('text_4399136da6c80d39')) + "</h3>"
                detail += "<p>" + esc(c["reference_answer"]) + "</p>"
                detail += "<ul>" + "".join(
                    '<li><a href="' + esc(u) + '">' + esc(u) + "</a></li>"
                    for u in c.get("reference_urls", [])
                ) + "</ul>"
                detail += "<p>" + t(
                    f'Checks compare the first {cutoff} URLs with the published sources and look for the normalized answer phrase in individual returned fields. A phrase match is not a factual correctness or entailment judgment.',
                    f"{report_text('text_2b87bbc2cd13d5ef')}{cutoff}{report_text('text_e8098e49611c6dee')}",
                ) + "</p>"
            if c.get("paper_ids") or c.get("repositories"):
                detail += (
                    "<p>"
                    + esc(", ".join(c.get("paper_ids", []) + c.get("repositories", [])))
                    + "</p>"
                )
            if c.get("identity_aliases"):
                detail += "<p>" + t("Verified equivalent URLs: ", report_text('text_7f9885c5b27df0ef'))
                detail += esc(", ".join(c["identity_aliases"])) + "</p>"
            if query_relevance:
                reason_label = t("Useful results: ", report_text('text_7a4d7a014722e76c')) + str(query_relevance["useful"]) + f"/{cutoff}"
            if review_data:
                from .review import render_detail
                detail += render_detail(review_data, r["id"], zh)
            diagnosis = reason_label
            detail += "<p>" + diagnosis + "</p>"
            detail += (
                "<h2>"
                + t("Actual request parameters", report_text('text_2b59095a7b9776c6'))
                + "</h2><pre data-request>"
                + esc(json.dumps(r.get("request", {}), ensure_ascii=False, indent=2))
                + "</pre>"
            )
            metric_labels = [
                (hit_key, t(f"Known target@{cutoff}", f"{report_text('text_807915ed48ceb2d9')}{cutoff}")),
                ("reciprocal_rank", t("Reciprocal rank", report_text('text_53e62964d986646d'))),
                ("domain_precision", t("Requested-domain fraction", report_text('text_088efa3d9c398139'))),
                ("duplicate_rate", t("Duplicate-document fraction", report_text('text_fa7dadd3eb7284c4'))),
                ("content_coverage", t("Results containing content", report_text('text_b12675b2e87abdd9'))),
            ]
            detail_metrics = r["metrics"]
            if "reference_answer" in c:
                from lexbench_websearch.scoring import factual_evidence
                detail_metrics = {**r["metrics"], **factual_evidence(c, r["results"], cutoff=cutoff)}
                metric_labels += [
                    (f"labeled_source_hit_{cutoff}", t(f"Published source in top {cutoff}", f"{report_text('text_c5a8970d6255caf1')}{cutoff}{report_text('text_c8c82eb0cadfc2bb')}")),
                    (f"labeled_source_rr_{cutoff}", t(f"Published-source reciprocal rank in top {cutoff}", f"{report_text('text_c5a8970d6255caf1')}{cutoff}{report_text('text_fc710652141cb7be')}")),
                    (f"answer_in_preview_{cutoff}", t("Answer phrase in title/snippet", report_text('text_8422dc7231da6ead'))),
                    (f"answer_in_delivered_text_{cutoff}", t("Answer phrase in delivered text", report_text('text_992d5ea850fe1191'))),
                ]
            if "reference_answer" in c:
                sensitivity = factual_sensitivity(c, r["results"], cutoff=cutoff)
                detail += '<p>' + t("Post-hoc benchmark-mirror exclusion; blanked ranks: ", report_text('text_dd92bb824939c416')) + esc(str(sensitivity["excluded_ranks"])) + '</p>'
                detail += table([t("After mirror exclusion", report_text('text_3693604bffdff018')), t("Value", report_text('text_c5500a58e0c52625'))], [cells([t("Answer in preview", report_text('text_8f513016c37eb4f7')), str(sensitivity["metrics"][f"answer_in_preview_{cutoff}"])]), cells([t("Answer in all text", report_text('text_2b967e182f622c1a')), str(sensitivity["metrics"][f"answer_in_delivered_text_{cutoff}"])])])
            detail += table(
                [t("Metric", report_text('text_b87e67c7dae03991')), t("Value", report_text('text_c5500a58e0c52625'))],
                [cells([label, fmt(detail_metrics[key])]) for key, label in metric_labels],
            )

            detail += (
                "<h2>"
                + t("Exact error", report_text('text_a966a49f3ac8c7f9'))
                + "</h2><pre data-error>"
                + esc(
                    r["error"]
                    or t(
                        "No API error reported; inspect the returned response for retrieval diagnostics.",
                        report_text('text_4304c3155c035c83'),
                    )
                )
                + "</pre>"
            )
            detail += f'''<button data-evidence="{ev}">{t('Open response', report_text('text_40d4e121e593d941'))}</button> <a href="{ev}">JSON</a>'''
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
                                f'''<button data-evidence="{call_path}">{t('Open response', report_text('text_40d4e121e593d941'))}</button> <a href="{call_path}">JSON</a>''',
                            ]
                        )
                    )
                detail += (
                    "<h2>"
                    + t("All declared physical calls", report_text('text_75059dd7b6dc04ff'))
                    + "</h2>"
                    + table(
                        [
                            t("Round", report_text('text_4890584bb57540f5')),
                            "HTTP",
                            "ms",
                            t("Results", report_text('text_15c20d768e184e34')),
                            t("Evidence", report_text('text_d883c636d3f8913e')),
                        ],
                        repeat_rows,
                    )
                )
            peers = [x for x in selected if x["case_id"] == cid]
            detail += same_task(peers, profile, zh)
            detail += "<h2>" + t("Returned results", report_text('text_1d0f87003fefac72')) + "</h2>"
            for item_index, item in enumerate(r["results"]):
                detail += f'''<article class="specimen"><a href="{esc(item['url'])}">{esc(item['title'])}</a><p>{esc(item['snippet'])}</p><button data-evidence="{ev}" data-result="{item_index}">{t('Summary and content', report_text('text_f56ab41cd993310f'))}</button></article>'''
            detail_doc = BeautifulSoup(detail, "html.parser")
            detail_doc.select_one("[data-summary]")["data-result-summary"] = ""
            for pre in list(detail_doc.select("pre")):
                if pre.find_parent("details") is None:
                    disclosure = detail_doc.new_tag("details")
                    label = detail_doc.new_tag("summary")
                    label.string = t("Inspect evidence", report_text('text_c031ba6c296e250b'))
                    pre.wrap(disclosure)
                    disclosure.insert(0, label)
            diagnosis_node = detail_doc.new_tag("section", id="result-logs")
            diagnosis_node["data-failure-diagnosis"] = ""
            error_node = detail_doc.select_one("[data-error]")
            error_node["data-kind"] = "error"
            error_node.parent.wrap(diagnosis_node)
            detail = '<section data-report-detail="' + ("failed" if status == "fail" else "passed") + '">' + str(detail_doc) + '</section>'
            page(target, cid + " / " + method_labels[r["method"]], detail)
        options = [
            ("fail", t("Target miss / error", report_text('text_8a00c2a224035d61'))),
            ("pass", t("Declared evidence found", report_text('text_f4354437926464e2'))),
            ("probe", t("Unjudged queries", report_text('text_8983ba621ef3e73a'))),
            ("all", t("All", report_text('text_5c55a67935af8f45'))),
        ]
        default = "fail" if any('data-state="fail"' in r for r in inventory) else "all"
        controls = (
            "<label>"
            + t("Filter", report_text('text_b5f15473fdc0fefe'))
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
            + t("Provider", report_text('text_ec309ab207ef7fa3'))
            + ' <select data-method-filter><option value="all">'
            + t("All", report_text('text_5c55a67935af8f45'))
            + "</option>"
            + "".join(
                f'<option value="{esc(method["id"])}">{esc(method["label"])}</option>'
                for method in profile["methods"]
            )
            + "</select></label> <label>"
            + t("Find", report_text('text_3003318e729a860b'))
            + " <input data-query></label>"
        )
        page(
            "results.html",
            t("Query comparison", report_text('text_57f428a0741e0981')),
            (render_result_comparison(profile, selected, zh, prefix=prefix, site_prefix=site_prefix, content=authored, relevance=relevance_data) if authored.get("presentation") == "comparison-report" else "")
            + ("" if authored.get("presentation") == "comparison-report" else (
            origin_filter(profile, zh)
            + cohort_filter(profile, zh)
            + controls
            + table(
                [
                    "ID",
                    t("Query", report_text('text_bcd6771e08ec7cc7')),
                    t("Method", report_text('text_22b9f0b66212809c')),
                    t("Outcome", report_text('text_6320b4a8722a851f')),
                    "ms",
                    t("Evidence", report_text('text_d883c636d3f8913e')),
                ],
                inventory,
            ).replace('class="scroll"', 'class="scroll inventory"')
            + (
                ""
                if rows
                else "<p>"
                + t(
                    "No authenticated cohort has been collected. Missing credentials are not provider failures.",
                    report_text('text_37e84b5b54047820'),
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
            "research.html", t("Evaluation method and competitors", report_text('text_37bf472321f6e735')), research
        )
        if authored.get("presentation") != "comparison-report":
            page("interpretation.html", t("Findings", report_text('text_8979f9672f58bc41')),
                 authored_sections(authored, "interpretation-lead", lang, rows=selected)
                 + interpretation_summary_table(profile, selected, zh, authored, relevance_scores)
                 + factual_panel(profile, selected, zh)
                 + authored_sections(authored, "interpretation", lang, rows=selected)
                 + outcome_table(profile, selected, zh, relevance_scores) + domain_discovery_table(profile, selected, zh))
        if authored.get("presentation") == "comparison-report":
            primary_ids = {c["id"] for c in profile["cases"] if "reference_answer" not in c}
            primary_rows = [r for r in rows if r["case_id"] in primary_ids]
            primary_profile = dict(profile, cases=[c for c in profile["cases"] if c["id"] in primary_ids])
            primary_diagnostics = operational(primary_rows, payloads, profile["methods"])
            timing = benchmark_operations(profile, selected, authored, zh)
            timing += authored_sections(authored, "performance", lang, rows=selected)
            timing += '<details class="technical-details"><summary>' + t("Server timing and upstream evidence", report_text('text_608ee70675aa1406')) + '</summary>' + performance_view(primary_profile, primary_diagnostics, zh, evidence_prefix="../" if prefix else "") + '</details>'
            timing += '<details><summary>' + t("Billing calculation", report_text('text_043ed0f24081b109')) + '</summary>' + cost_panel(profile, rows, zh) + '</details>'
            timing += authored_sections(authored, "cost", lang, rows=selected)
            page("performance.html", t("Speed and cost", report_text('text_050b4ad5cfeb66ad')), timing)
        else:
            page("performance.html", t("Latency diagnostics", report_text('text_51464dd5dcb1bce3')), authored_sections(authored, "performance", lang, rows=selected) + performance_view(profile, diagnostics, zh, evidence_prefix="../" if prefix else ""))
        if review_data:
            from .review import render_summary
            page("review.html", t("Independent result review", report_text('text_f51205d6074e15bf')), render_summary(review_data, zh) + authored_sections(authored, "review", lang, rows=selected))
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
                               "independent_review_scope": review_data["scope"] if review_data else None,
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
    from .relevance import report_authority
    authority = report_authority(output)
    manifest = json.loads((output / "manifest.json").read_text())
    _check_report_languages(output, manifest)
    from .pages import reader_pages
    top_routes = reader_pages((manifest.get("authored") or {}).get("presentation"), reviewed=bool(manifest.get("independent_review_sha256")))
    for lang in manifest["languages"]:
        prefix = "" if lang == "zh" else lang + "/"
        if not {prefix + p["route"] for p in top_routes}.issubset(manifest["routes"]):
            raise ValueError("Report is missing a registered reader page")
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
            "independent_review_scope": review_data["scope"] if review_data else None,
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
                normalize(payloads[row["id"]], next(m["adapter"] for m in manifest["profile"]["methods"] if m["id"] == row["method"]))
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
        validate_public_review(manifest["profile"], manifest["rows"], review_data, authority=authority)
        for lang in manifest["languages"]:
            prefix = "" if lang == "zh" else lang + "/"
            if prefix + "review.html" not in manifest["routes"]:
                raise ValueError("Independent review page is missing")
            soup = BeautifulSoup((output / (prefix + "index.html")).read_text(), "html.parser")
            coverage = soup.select_one("[data-review-coverage]")
            query_scope = f"{review_data['case_reviewed']}/{review_data['case_total']}"
            output_scope = f"{review_data['output_reviewed']}/{review_data['output_total']}"
            if coverage is None or any(value not in re.findall(r"(?<!\d)\d+/\d+(?!\d)", coverage.get_text()) for value in (query_scope, output_scope)):
                raise ValueError("Independent review coverage differs from the accepted evidence")
            for attr, value in (("data-reviewed-queries", query_scope), ("data-reviewed-outputs", output_scope)):
                if coverage.get(attr) != value:
                    raise ValueError("Independent review coverage differs from the accepted evidence")
            review_page = BeautifulSoup((output / (prefix + "review.html")).read_text(), "html.parser")
            expected = BeautifulSoup(render_summary(review_data, lang == "zh"), "html.parser")
            actual = review_page.select_one("[data-independent-review]")
            if actual is None or actual.get_text() != expected.get_text():
                raise ValueError("Independent review summary differs from the accepted evidence")
            for row in manifest["rows"]:
                detail = BeautifulSoup((output / (prefix + "details/" + row["id"] + ".html")).read_text(), "html.parser")
                expected = BeautifulSoup(render_detail(review_data, row["id"], lang == "zh"), "html.parser")
                actual = detail.select("[data-independent-opinion], [data-review-not-selected]")
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
    expected_diagnostics = operational(manifest["rows"], payloads, manifest["profile"]["methods"])
    cases = {c["id"]: c for c in manifest["profile"]["cases"]}
    for row in manifest["rows"]:
        if row["http_status"] == 200 and not row["error"]:
            if normalize(payloads[row["id"]], next(m["adapter"] for m in manifest["profile"]["methods"] if m["id"] == row["method"])) != row["results"]:
                raise ValueError("Published results disagree with raw response")
        if row["metrics"] != evaluate(cases[row["case_id"]], row["results"]):
            raise ValueError("Published scores disagree with the declared rules")
    if relevance_data:
        selected = [r for r in manifest["rows"] if r["round"] == manifest["profile"]["protocol"]["score_round"]]
        verify_provenance(manifest["profile"], selected, relevance_data, authority=authority)
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
            from lexbench_websearch.revisions import apply_report_revision
            revised, _, _ = apply_report_revision(revised, [], manifest["assessment_revision"]["contract"])
        revised, _ = project_comparison(revised, [], manifest.get("authored") or {})
        if manifest.get("report_assessment"):
            revised = assessment_profile(revised, relevance_data, review_data)
        if revised != manifest["profile"]:
            raise ValueError("Scoring revision profile mismatch")
    elif manifest.get("assessment_revision"):
        from lexbench_websearch.revisions import apply_report_revision
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
                visible_diagnostics = operational(primary_rows, payloads, manifest["profile"]["methods"])
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
            expected_factual = BeautifulSoup(factual_panel(manifest["profile"], selected, soup.html["lang"] == "zh"), "html.parser").select_one("[data-factual]")
            if str(soup.select_one("[data-factual]")) != str(expected_factual):
                raise ValueError("Interpretation factual evidence differs from measured inputs")
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
                for metric, key in (("mean-ms", "mean"), ("p50-ms", "p50"), ("p95-ms", "p95")):
                    cell = tr.select_one(f'td[data-metric="{metric}"]')
                    value = stats["all_latency"][key]
                    if float(cell["data-value"]) != value or cell.get_text().replace(
                        "★", ""
                    ).strip() != fmt(value):
                        raise ValueError("Visible latency disagrees with evidence")
                cell = tr.select_one('td[data-metric="target-hit"]')
                hit = quality[method["id"]]["metrics"][hit_key]
                expected = (
                    "—" if hit["mean"] is None else (
                        f"{hit['mean'] * 100:.1f}% ({hit['n']}{report_text('text_33f1546af31a9b68')}"
                        if soup.html["lang"] == "zh" else f"{hit['mean'] * 100:.1f}% ({hit['n']} queries)"
                    )
                )
                if cell.get_text().replace("★", "").strip() != expected:
                    raise ValueError("Visible target hit disagrees with evidence")
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
        "--profile", type=Path, help="External instance selector; required without --source"
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
    parser.add_argument("--publication-plan", type=Path)
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
        release_check(args.output, args.release_check, publication_plan=args.publication_plan)
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


def release_check(output, visual_receipt, *, publication_plan=None):
    """Combine deterministic evidence checks with the separate operated-browser gate."""
    check(output)
    proof = json.loads(visual_receipt.read_text())
    manifest = output / "manifest.json"
    if proof["manifest_sha256"] != digest(manifest.read_bytes()):
        raise ValueError("Visual check belongs to a different build")
    report_manifest = json.loads(manifest.read_text())
    if not report_manifest.get("receipt", {}).get("synthetic"):
        if not report_manifest.get("receipt", {}).get("certified"):
            raise ValueError("A formal release requires certified collection evidence")
        if publication_plan is not None:
            from .publication import verify_plan
            verify_plan(output, publication_plan.parent)
        else:
            raise ValueError("A measured release requires an explicit publication plan")
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
        "originals": ("Post and screenshot transcriptions", report_text('text_efbd51ae2b4ed0bc')),
        "community_derived": ("Derived scenarios", report_text('text_5f9b1632b822ed6e')),
        "public_catalog": ("Public query catalog", report_text('text_2d8beb22986c63ee')),
        "curated": ("Curated probes", report_text('text_b978ebaae127c7a5')),
        "public_labeled": ("Public factual queries", report_text('text_d329d97145b42b50')),
    }
    return (
        "<label>"
        + (report_text('text_711ba1da1c15b930') if zh else "Query source")
        + ' <select data-origin-filter><option value="all">'
        + (report_text('text_5c55a67935af8f45') if zh else "All")
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
        + (report_text('text_a18713d1bce780df') if zh else "Benchmark")
        + ' <select data-cohort-filter><option value="all">'
        + (report_text('text_edf59c14353d739e') if zh else "All queries")
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
        + t("Independent benchmarks, with separate collection cohorts", report_text('text_2393b91acf2d9095'))
        + "</h2><p>"
        + t(
            f"{len(profile['cases'])} queries and {len(rows)} final logical results, from {sum(len(r.get('attempts') or [r]) for r in rows)} physical requests including retries, across {len(groups)} complete collection batches. The table preserves each batch separately; the combined count is not a new same-window speed trial.",
            f"{report_text('text_2428420e407fe2e8')}{len(profile['cases'])}{report_text('text_5e19f516cfc5bbce')}{len(rows)}{report_text('text_191a064bf1945b7e')}{sum((len(r.get('attempts') or [r]) for r in rows))}{report_text('text_7a78571977bce602')}{len(groups)}{report_text('text_93b3c07aebd15aaf')}",
        )
        + "</p>"
        + table(
            [
                t("Query group", report_text('text_d4c8562da9dbe7c3')),
                t("Method", report_text('text_c80d519245a7e843')),
                t("Queries/provider", report_text('text_a68586f3ce0a42cc')),
                t("Search tasks", report_text('text_b1c974debac32da2')),
                t("Nonempty/final results", report_text('text_0c314f884c569c99')),
                t("Mean returned count", report_text('text_db10698cf21b4914')),
                t("Mean ms", report_text('text_bcefbf4dbcaf5f47')),
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
        + t("Equal request budgets, observed result counts", report_text('text_a05fa2350efa7148'))
        + "</h2>"
        + table(
            [
                t("Provider", report_text('text_c80d519245a7e843')),
                t("Search tasks", report_text('text_b1c974debac32da2')),
                t("Requested limit × tasks", report_text('text_90fd553997fbb86b')),
                t("Returned items × tasks", report_text('text_b5bb11778c0d1822')),
                t("Mean items per task", report_text('text_9ec7785f4564926b')),
            ],
            entries,
        )
        + "<p>"
        + t(
            f"First-round queries with equal result counts across all providers: {equal}/{len(common)}. Empty and short responses remain in every declared denominator. Counts do not establish relevance.",
            f"{report_text('text_0db57b9c951d1fb3')}{equal}/{len(common)}{report_text('text_5b0f6bce8e0c2590')}",
        )
        + "</p></section>"
    )


def comparison_tables(profile, selected, rows, zh):
    def t(en, cn):
        return cn if zh else en

    cutoff = profile["protocol"]["top_k"]
    hit_key = "anchor_hit_5" if cutoff == 5 else "anchor_hit_10"
    body = "<h2>" + t("Separate task populations", report_text('text_24704c6374db8eea')) + "</h2>"
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
                                "web": t("Web", report_text('text_d0bfc8234eab5253')),
                                "site": t("Site restriction", report_text('text_500695a5dd3d5ab7')),
                                "vertical": t("Native source / site route", report_text('text_ed19e1f244a105df')),
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
            t("Population", report_text('text_7f72f06a88779b5a')),
            t("Method", report_text('text_22b9f0b66212809c')),
            f"Hit@{cutoff}",
            "MRR",
            t("Domain fraction", report_text('text_088efa3d9c398139')),
            t("Duplicate fraction", report_text('text_cd12654b5dc4d785')),
        ],
        grouped,
    )
    body += (
        "<p>"
        + t(
            f"Each metric shows its eligible query count. Hit@{cutoff} and MRR use declared known pages; domain fraction uses returned top-{cutoff} URLs and is unavailable for empty responses. Native source and site operators implement the same intent through different routes.",
            f"{report_text('text_0631b63147ef9f33')}{cutoff}{report_text('text_5b534935103ebf07')}{cutoff}{report_text('text_56dac48d25468002')}",
        )
        + "</p>"
    )
    body += "<h2>" + t("Text delivery and estimated charges", report_text('text_fc21f044b1ac01b9')) + "</h2>"
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
            t("Method", report_text('text_22b9f0b66212809c')),
            t("Results/query", report_text('text_f3c585618594cddb')),
            t("Snippet chars/query", report_text('text_fce78f6213fbfecb')),
            t("Summary chars/query", report_text('text_c4dc41977287a7ac')),
            t("Content chars/query", report_text('text_4f15829b22c5be56')),
            t("Content coverage", report_text('text_6d249651a884fff2')),
            t("All-call estimate", report_text('text_3031185688e495e6')),
            t("Cost/nonempty query", report_text('text_28bb59cb1b7671df')),
        ],
        textrows,
    )
    body += (
        "<p>"
        + t(
            "Text sizes count Unicode characters, not tokens or correctness. Native fields are retained separately: text in a snippet is not automatically full content. No source-faithfulness or answer-accuracy score is assigned without independent judgments. Cost is an estimate from list prices; a transport timeout has unknown billing.",
            report_text('text_cbc074f197098166'),
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
    headings = [t("Rank", report_text('text_6570a98f2850fb4f'))] + [labels[p["method"]] for p in peers]
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
        + t("Same-query results", report_text('text_dde76897bd5b6abd'))
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
        + t("Repeated-query latency and reported cache state", report_text('text_c2fce127f7858ad3'))
        + "</h2>"
        + table(
            [
                t("Round", report_text('text_4890584bb57540f5')),
                t("Method", report_text('text_22b9f0b66212809c')),
                "N",
                t("Mean ms", report_text('text_cfb4169ac2aa7957')),
                "P50 ms",
                "P95 ms",
                t("API cache state", report_text('text_8163db9696407915')),
            ],
            entries,
        )
        + "<p>"
        + t(
            "All declared calls remain in the headline latency. Cache states come from the API response; unknown means the provider did not report one. The first round is not guaranteed to be a cold cache. Repeated queries describe reuse behavior, not sustained-load throughput.",
            report_text('text_d1bfa35bca39b971'),
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
                + esc((report_text('text_20d1b268352953cf') if language == "zh" else "Open source page · ")
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
                    + (report_text('text_b392604383702bd9') if language == "zh" else " characters")
                    + '</p><blockquote class="field-value">'
                    + esc(excerpt)
                    + "</blockquote>"
                )
            body += (
                '<p><a href="details/'
                + esc(row["id"])
                + '.html">'
                + (report_text('text_1ee054b06b46e3c9') if language == "zh" else "Inspect the full measured response")
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
        "community_exact": ("Published community query", report_text('text_97360375e49f1ef2')),
        "community_derived": ("Community-derived scenario", report_text('text_8a4e600cddd1c844')),
        "public_catalog": ("Public query catalog", report_text('text_a924cf7c267c2959')),
        "public_labeled": ("Public human-verified factual dataset", report_text('text_469edf9fa5a23186')),
        "curated": ("Curated supplement", report_text('text_558d314b89399d4e')),
        "user_screenshot": ("Exact query from supplied screenshot", report_text('text_cc15d01082a35e80')),
    }.get(kind, (kind, kind))[int(zh)]


def search_category(value, zh):
    if not zh:
        return value.replace("_", " ")
    return {
        "web": report_text('text_d0bfc8234eab5253'), "site": report_text('text_9d669bb58c61d983'), "vertical": report_text('text_9ce0479f9c995028'),
        "factual": report_text('text_d329d97145b42b50'),
        "known_item": report_text('text_1aa11fb464c4c90c'), "primary_source": report_text('text_99adf353c203e8f9'),
        "technical_exact": report_text('text_ee923cd93d632659'), "semantic_long_tail": report_text('text_555587f3a8f7212e'),
        "niche_small_web": report_text('text_d615d58d8e7f3534'), "multi_constraint": report_text('text_92c1ed311c728d71'),
        "exploratory_diversity": report_text('text_150178c0f8c4393c'), "multilingual_regional": report_text('text_27f012d531b7a228'),
        "robustness_noisy": report_text('text_397d9af9d9173a0f'), "freshness_rolling": report_text('text_8c39ff77b1b8f30b'),
        "creator_discovery": report_text('text_0c46f219dc5face3'), "chinese_service": report_text('text_60f256c07dea53b4'),
        "community_original": report_text('text_f31d2d2aea03d2ca'), "wechat": report_text('text_e33deca12cd268af'),
        "xiaohongshu": report_text('text_d26c6a08a591011a'), "zhihu": report_text('text_1bfefe0febb22747'), "douyin": report_text('text_d077d0b18e19cd2e'),
        "weibo": report_text('text_0fc69798d832390f'), "bilibili": report_text('text_acfb839ad1e4f115'), "douban": report_text('text_d442cd087c0f307c'),
        "baijiahao": report_text('text_4d424d93791fbcc5'), "toutiao": report_text('text_43ecd886aac5eaaf'), "x": "X",
        "reddit": "Reddit", "instagram": "Instagram", "facebook": "Facebook",
        "linkedin": "LinkedIn", "medium": "Medium", "substack": "Substack",
    }.get(value, value)


def source_note(case, zh):
    if not zh:
        return case.get("derivation", case["origin"])
    return {
        "user_screenshot": report_text('text_e926b4b8d64cc575'),
        "community_exact": report_text('text_7350a36db486e957'),
        "community_derived": report_text('text_32cdd692a0d28174'),
        "public_catalog": report_text('text_700f343cf97c22ff'),
        "curated": report_text('text_37492afaad0b4de0'),
        "public_labeled": report_text('text_332625b3e163a798'),
    }.get(case.get("provenance_kind", "curated"), "")


def provenance_table(profile, zh):
    counts = Counter(c.get("provenance_kind", "curated") for c in profile["cases"])
    return (
        "<h2>"
        + (report_text('text_a9b9009a005a23a8') if zh else "Where the queries come from")
        + "</h2>"
        + table(
            [report_text('text_38e1e77b3ef39016') if zh else "Source type", report_text('text_2ffcf67bf67ef40d') if zh else "Unique queries"],
            [cells([provenance_label(k, zh), str(v)]) for k, v in counts.items()],
        )
        + "<p>"
        + (
            f"{report_text('text_2428420e407fe2e8')}{len(profile['cases'])}{report_text('text_5c3972d78a2900eb')}"
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
        '<h2>' + (report_text('text_82910e2c01742cd2') if zh else 'Core API comparison') + '</h2>'
        + table(
            [report_text('text_c80d519245a7e843'), report_text('text_a7b944beb818e20f'), f"{report_text('text_b1ad9b0e47832ff4')}{cutoff}", 'P50 ms', 'P95 ms', report_text('text_f4fc9fbdc9587557')]
            if zh else ['Provider', 'Nonempty queries', f'Known page@{cutoff}', 'P50 ms', 'P95 ms', 'Estimated cohort cost'],
            rows,
        )
        + '<p>' + (
            report_text('text_cfcc6069ed560859')
            if zh else '★ marks a unique favorable value within its column. Populations differ across columns, so these are not an overall ranking. Charges use frozen list prices; unknown prices remain unavailable.'
        ) + '</p>'
    )


def capability_map(profile, selected, zh):
    """Show actual delivered layers; a separate AI-answer endpoint is not implied."""
    heads = (
        [report_text('text_ff5ae7dd75ad0dc4'), report_text('text_0e9b7d54c11a464b'), report_text('text_37504bfbd2c61e60'), report_text('text_e963070355bb5ed9'), report_text('text_18fb49e5f1c52cf8'), report_text('text_fab5c9a02d509906')]
        if zh else
        ["Tested endpoint", "URL & title", "Snippet", "Page summary", "Page body", "Answer"]
    )
    output = ['<div class="capmap" role="table" aria-label="' +
              (report_text('text_31c234e751bed24d') if zh else "Observed response fields") + '">']
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
                          ((report_text('text_90ce0f66f09488f2') if zh else "Yes") if present else (report_text('text_f3254a2c41cad9a9') if zh else "No")) + '</div>')
    output.append('</div><p class="muted">' + (
        report_text('text_1708f31f2b4ea54e')
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
        report_text('text_d727cd52bc682f8a') if zh else 'One query: complete requests and responses'
    ) + '</h2><p>' + esc(case["query"]) + '</p>'
    for method in profile["methods"]:
        if method["id"] not in indexed:
            raise ValueError("Interface example must include every method")
        row = indexed[method["id"]]
        body += '<article class="specimen" data-interface-method="' + esc(method["id"]) + '"><h3>' + esc(method["label"]) + '</h3>'
        body += '<p>' + (f"{report_text('text_898447a4f504c0ef')}{len(row['results'])}{report_text('text_70570d4930296943')}" if zh else f'{len(row["results"])} results returned') + '</p>'
        body += '<details><summary>' + (report_text('text_d94a6c300e95354c') if zh else 'Complete request parameters') + '</summary><pre>' + esc(json.dumps(row.get("request", {}), ensure_ascii=False, indent=2)) + '</pre></details>'
        body += '<details><summary>' + (report_text('text_ddf02a015ac8aa55') if zh else 'All search results') + '</summary><pre>' + esc(json.dumps(row["results"], ensure_ascii=False, indent=2)) + '</pre></details>'
        body += '<button data-evidence="' + esc(row["evidence"]["path"]) + '">' + (report_text('text_40dfbfef859c031b') if zh else 'Open native response') + '</button></article>'
    return body + '</section>'


def relevance_detail(result, zh):
    t = lambda en, cn: cn if zh else en
    rows = []
    for i in range(result.get("cutoff", 5)):
        grade = result["ranked_grades"][i] if i < len(result["ranked_grades"]) else 0
        label = str(grade) if i < len(result["ranked_grades"]) else t("Not returned", report_text('text_1bb9a0dcdfbd8389'))
        rows.append(cells([str(i + 1), label, str(2**grade-1), f'{1 / math.log2(i+2):.4f}', f'{(result["contributions"][i] if i < len(result["contributions"]) else 0):.4f}']))
    return ('<section data-case-relevance><h2>' + t("Query ranking quality", report_text('text_ba90671131abe614')) + '</h2><strong>' + f'{result["ndcg"]*100:.1f} / 100' + '</strong><p>'
            + t("Useful results: ", report_text('text_7a4d7a014722e76c')) + f'{result["useful"]}/{result.get("cutoff", 5)} · ' + t("Grade-3 result: ", report_text('text_5da1b4bd2c9fa60f')) + (t("Yes", report_text('text_90ce0f66f09488f2')) if result["success"] else t("No", report_text('text_484d55613910eb8c')))
            + '</p><details><summary>' + t("Inspect the complete calculation", report_text('text_a3a24742c760035a')) + '</summary>'
            + table([t("Rank", report_text('text_6570a98f2850fb4f')), t("Grade", report_text('text_3377171738070045')), t("Gain", report_text('text_8cf4818a680e0aad')), t("Rank weight", report_text('text_faaf62c3090e178e')), t("Contribution", report_text('text_23234c9a464a44a7'))], rows)
            + '<p>' + t("Ideal pooled grades: ", report_text('text_4423fcea986c7b88')) + esc(str(result["ideal_grades"])) + '</p><p>'
            + (f'{result["dcg"]:.4f} ÷ {result["idcg"]:.4f} × 100 = {result["ndcg"]*100:.1f}' if result['idcg'] else t('No relevant candidate in the pool: score 0.', report_text('text_709d95adc1c0db2c'))) + '</p></details></section>')


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
    rules = {"http-200-nonempty": (report_text('text_10a2c2cf81547129'), "HTTP 200 with nonempty results"), "http-200": ("HTTP 200", "HTTP 200"), "unknown": (report_text('text_82c21f44ca0bdbf4'), "Not specified")}
    for method, logical, physical, billed, total in records:
        currency = report_text('text_7f77f23af7b0dcc8') if zh and method.get("currency") == "CNY" else method.get("currency", "")
        price = method.get("price_per_1000")
        label = (report_text('text_b769180d023c4d19') if zh else "Not priced") if price is None else fmt(price) + " " + currency
        source = method.get("price_source", "")
        if source.startswith("https://"):
            label = '<a href="' + esc(source) + '">' + label + '</a>'
        rule = rules.get(method.get("billing"), (method.get("billing", ""),) * 2)[0 if zh else 1]
        row = cells([esc(method["label"]), label, esc(method.get("price_checked", "")), esc(rule), str(logical), str(physical), str(billed) if price is not None else (report_text('text_63ec276c6386dfd2') if zh else "Unknown")])
        label = fmt(total) + " " + currency if total is not None else (report_text('text_b769180d023c4d19') if zh else "Not priced")
        rendered.append(row[:-5] + numeric_cell(label, total, "cohort-cost", "min" if comparable else "neutral", 0 in winners and total == winners[0]) + '</tr>')
    return ('<section data-cost-budget><h2>' + (report_text('text_01723325f91abc83') if zh else "Unit prices, billable calls and estimated charges") + '</h2>'
            + table([report_text('text_22b9f0b66212809c'), report_text('text_ece71321942f6976'), report_text('text_4a2db7b8d94c26ac'), report_text('text_1dad1549c2a5c1a1'), report_text('text_b1c974debac32da2'), report_text('text_b1f3689224d5bfda'), report_text('text_c9519ab685f92911'), report_text('text_671b330c8dec81da')] if zh else ["Method", "Price /1,000", "Price checked", "Billing condition", "Search tasks", "Physical calls", "Known billable calls", "Estimated total"], rendered)
            + '<p>' + (report_text('text_dcb75f7e79cb38e1') if zh else "Retries increase physical calls, not search tasks. Charges sum physical requests; unknown billing is not zero. Price links identify the frozen sources; provider invoices determine actual charges.") + '</p></section>')


def relevance_panel(profile, scored, evidence, zh):
    """Explain and visualize judged preview relevance without conflating it with recall."""
    cutoff = profile.get("protocol", {}).get("top_k", evidence.get("plan", {}).get("cutoff", scored[0].get("cutoff", 5) if scored else 5))
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
    bars = table(([report_text('text_f9e0010627639105'),report_text('text_35801615b907ad27')] if zh else ['Category','Queries']) + [m['label'] for m in methods], category_rows)
    audit = evidence["judge"]
    model = audit["kind"] == "model"
    count = len(evidence["plan"]["case_ids"])
    title = (f"{report_text('text_11bae9da3c2c6058')}{count}{report_text('text_02c1ee7d2ca6bb57')}" if zh else f"In-house relevance: {count} queries")
    definitions = (
        f"{report_text('text_1dfbb8c5457c7228')}{cutoff}{report_text('text_1e3d476a3708c2a8')}{cutoff}{report_text('text_ee2b63e344681729')}"
        if zh else
        f'Ranking quality is nDCG@{cutoff} × 100. Useful-result fraction counts grades 2 and 3 in {cutoff} slots; direct-match rate counts queries with at least one grade-3 result.'
    )
    scope = (
        f"{report_text('text_6524ad100291a524')}{audit['name']}{report_text('text_a873f3f7ad389c5e')}"
        if zh else
        f"Model judge: {audit['name']}. This run evaluates relevance visible in titles and snippets, without verifying full pages or factual accuracy. Frozen labels make metric recalculation deterministic; a new labeling run may differ."
    ) if model else (
        f"{report_text('text_64c3cf2d1aed77f4')}{audit['name']}{report_text('text_cb7682359958210b')}"
        if zh else f"Human annotator: {audit['name']}. Metric calculation uses fixed labels without model calls."
    )
    denominator = (f"{report_text('text_59f12dddca02ff1f')}{cutoff}{report_text('text_5abf3839cd607c74')}" if zh else "Empty responses, missing slots and all-zero candidate pools remain in the denominator.")
    panel = ('<section class="specimen" id="relevance-scores" data-relevance><h3>' + esc(title) + '</h3>' + bars
             + ('' if profile.get("comparison_selection") else '<p>' + esc(definitions) + '</p>')
             + '<details><summary>' + (report_text('text_e5bf9116e03c961c') if zh else "Useful results and direct matches") + '</summary>' + table([report_text('text_f9e0010627639105') if zh else "Category", report_text('text_ec309ab207ef7fa3') if zh else "Provider", report_text('text_35801615b907ad27') if zh else "Queries", report_text('text_9a98146dbe62ee65') if zh else "Ranking quality /100", report_text('text_599be80e114985ee') if zh else "Useful-result fraction", report_text('text_1ef91e775b2f1848') if zh else "Direct-match rate"], rows) + '</details>'
             + '<p class="muted">' + esc(scope + denominator) + '</p><a href="relevance-judgments.json">'
             + (report_text('text_231e482007c0e359') if zh else "Scoring evidence") + '</a></section>')
    first_case = evidence["plan"]["case_ids"][0]
    query = next(c["query"] for c in profile["cases"] if c["id"] == first_case)
    panel += '<details class="score-example"><summary>' + ('A real query: inspect each rank and its score' if not zh else report_text('text_c4615c45e389ba54')) + '</summary><section data-score-example="' + esc(first_case) + '"><h3>' + ("Actual calculation" if not zh else report_text('text_613e3c40a5813741')) + '</h3><p>' + esc(query) + '</p>'
    for method in methods:
        result = next(r for r in scored if r["case_id"] == first_case and r["method"] == method["id"])
        panel += '<details><summary>' + esc(method["label"]) + ' · ' + f'{result["ndcg"]*100:.1f}/100' + '</summary>' + relevance_detail(result, zh) + '</details>'
    panel += '</section></details>'
    return panel


def factual_panel(profile, selected, zh, *, compact=False):
    from lexbench_websearch.scoring import factual_evidence
    cutoff = profile["protocol"]["top_k"]
    excluded = set(profile.get('factual_quality_exclusions', []))
    cases = {c['id']: c for c in profile['cases'] if 'reference_answer' in c and c['id'] not in excluded}
    if not cases:
        return ''
    projected = bool(profile.get("comparison_selection") or profile.get("current_selection"))
    baseline = profile['methods'] if projected else [m for m in profile['methods'] if not any(m.get('output_options', {}).values())]
    answer_key = f'answer_in_delivered_text_{cutoff}' if projected else f'answer_in_preview_{cutoff}'
    keys = (f'labeled_source_hit_{cutoff}', f'labeled_source_rr_{cutoff}', answer_key)
    values = []
    by_method = {}
    for method in profile['methods']:
        group = {r['case_id']: r for r in selected if r['method'] == method['id'] and r['case_id'] in cases}
        by_method[method['id']] = group
        if method in baseline:
            values.append((method['label'], len(group), [sum(factual_evidence(cases[r['case_id']], r['results'], cutoff=cutoff)[k] for r in group.values()) for k in keys] + [sum(factual_sensitivity(cases[r['case_id']], r['results'], cutoff=cutoff)['metrics'][answer_key] for r in group.values())]))
    if compact:
        headers = [report_text('text_22b9f0b66212809c'), report_text('text_44497a8175cc1d10'), report_text('text_24a00d1d0a634f3a')] if zh else ["Method", "Given source found", "Answer text delivered"]
        rendered = []
        best = numeric_winners([(v[2][0]/v[1] if v[1] else None, v[2][3]/v[1] if v[1] else None) for v in values], {0: "max", 1: "max"})
        for label, n, sums in values:
            row = '<td>' + esc(label) + '</td>'
            for i, total in enumerate((sums[0], sums[3])):
                row += numeric_cell(f'{int(total)}/{n} · {total/n:.1%}' if n else '—', total/n if n else None, f'factual-current-{i}', 'max', n > 0 and best.get(i) == total/n)
            rendered.append('<tr>' + row + '</tr>')
        return '<section data-factual><h2>' + (f"{report_text('text_76bf22fd299e8730')}{len(cases)}{report_text('text_856f67e43b1d0e1d')}" if zh else f'Factual evidence retrieval: {len(cases)} queries') + '</h2><p>' + (f"{report_text('text_741732ff42aae0f8')}{cutoff}{report_text('text_3f86433ccdf7001c')}" if zh else f'Check top-{cutoff} given URLs and answer text in all delivered fields, excluding dataset mirrors and evaluation logs. Text presence does not verify its context.') + '</p>' + table(headers, rendered) + (report_text('text_d440dcee96501e63') if zh else '<p class="muted">Target MRR is available through the per-query reciprocal-rank details.</p>') + ('<p data-quality-exclusions>' + (f"{report_text('text_e1b7abaecd738349')}{len(excluded)}{report_text('text_a80cbf6cf5eb8ae9')}{len(cases)}{report_text('text_92afc262868f2421')}" if zh else f'Reference review excludes {len(excluded)} defective queries for every provider; quality uses {len(cases)} queries. Original responses, timing and cost retain the full collection.') + '</p>' if excluded else '') + '</section>'
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
        (report_text('text_0790689687fb0af2') if zh else 'Public factual questions: sources and answer cues') if projected else (report_text('text_a6f185054c3f8142') if zh else 'Public factual questions: basic-API evidence discovery')
    ) + '</h3><p>' + (
        f"{report_text('text_c5a8970d6255caf1')}{cutoff}{report_text('text_3a0342116e3496b2')}{cutoff}{report_text('text_ce593eb6c49ae70a')}"
        if zh else
        f'A source hit means at least one published supporting URL appears in the top {cutoff}. MRR averages reciprocal first-hit rank: 1 for rank one, 0.5 for rank two and zero for a miss. Answer cues use only shared titles and snippets. Failures, empties and short lists remain in the population.'
    ) + '</p>' + table(
        [report_text('text_7d56d3da76d3464e'), report_text('text_35801615b907ad27'), report_text('text_523b7f8a8dea6644'), report_text('text_d9debd53ccc04e26'), report_text('text_7a2319aab382f14d'), report_text('text_8550950d44594460')] if zh else
        ['API', 'Queries', 'Given webpage retrieved', 'First-source reciprocal rank', 'Answer text: raw', 'Answer text: dataset mentions excluded'], rendered)
    if projected:
        body = body.replace(report_text('text_3aa3129bc8c2a9c4'), f"{report_text('text_b2c2b17ed258eb2c')}{cutoff}{report_text('text_623f31d7b5a8a5c5')}").replace('Answer cues use only shared titles and snippets.', f'Answer cues use every delivered title, snippet, summary and body in the top {cutoff}.').replace(report_text('text_7eaf9d0973634d6d'), report_text('text_f571be01ed9fa85e')).replace('API</th>', 'Method</th>').replace(report_text('text_7a2319aab382f14d'), report_text('text_6c4a3a25bbe5e6d9')).replace('Answer text: raw', 'Delivered answer cue: raw')
    body += '<p>' + (report_text('text_3f4ff4384b641610') if zh else 'Contamination exclusion is a post-hoc deterministic diagnostic. Results naming SimpleQA in their URL/title, or containing the complete question together with evaluation fields, validation logs or dataset-viewer structure, are blanked without refilling or compacting ranks. Original values remain visible.') + '</p>'
    if excluded:
        body += '<p data-quality-exclusions>' + (f"{report_text('text_ae20a1482deb60d4')}{len(excluded)}{report_text('text_b4392f3d83ce0d35')}{len(cases)}{report_text('text_4b8b2a2044d2903c')}" if zh else f'Independent reference review excluded {len(excluded)} defective or materially ambiguous questions from all methods’ quality metrics, leaving {len(cases)} common queries. All queries remain in latency and cost; original inputs, responses and diagnostics remain inspectable.') + '</p>'
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
        a = {cid: dict(r, metrics=factual_sensitivity(cases[cid], r['results'], cutoff=cutoff)['metrics']) for cid,r in a.items()}
        b = {cid: dict(r, metrics=factual_sensitivity(cases[cid], r['results'], cutoff=cutoff)['metrics']) for cid,r in b.items()}
        key = f'answer_in_delivered_text_{cutoff}'
        extra.append(cells([esc(enabled['label']), str(len(a)),
            str(sum(r['metrics'][key] for r in a.values())), str(sum(r['metrics'][key] for r in b.values())),
            str(sum(not a[c]['metrics'][key] and b[c]['metrics'][key] for c in a)),
            str(sum(a[c]['metrics'][key] and not b[c]['metrics'][key] for c in a))]))
    if extra:
        body += '<h3>' + (report_text('text_a0f2e1716f7e6972') if zh else 'Option experiment: answer cues in all delivered text') + '</h3><p>' + (
            report_text('text_271899ed9f9a7dfa')
            if zh else
            'This includes returned summaries and bodies after the same explicit-mirror exclusion. Added means absent when disabled and present when enabled; lost is the reverse. Search results can change, so this is the overall option effect rather than a fixed-page body-only gain.'
        ) + '</p>' + table([report_text('text_e5d4c3fe74891d30'), report_text('text_4e114db7ef22ed94'), report_text('text_741c5d468d053162'), report_text('text_f24f7c9c6114f123'), report_text('text_0006d696d8e1ec28'), report_text('text_b3206f118ce9374b')] if zh else
            ['Enabled mode', 'Pairs', 'Disabled hits', 'Enabled hits', 'Added', 'Lost'], extra)
    if any(c.get('answer_match_rule') == 'published-answers/2' for c in cases.values()):
        return body + '<p class="muted">' + (
            report_text('text_0f0231d2c5b6ab86')
            if zh else
            'Answer checks use the published answer, explicit aliases and allowed numeric ranges, preserving units and percentage modifiers. Matches stay within one returned field; fields are never joined. Unspecified synonyms are not inferred. Negated or unrelated mentions can still match, so this is lexical evidence rather than semantic accuracy. Details expose the reference answer, sources and actual output.'
        ) + '</p></section>'
    return body + '<p class="muted">' + (
        report_text('text_61faa15fd38ded13')
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
        report_text('text_3bff8e2ee816c597') if zh else "What does enabling extra text change?"
    ) + "</h3><p>" + (
        report_text('text_127c9c5bca1fc475')
        if zh else
        "Paired difference = enabled minus disabled latency; positive means slower with extra text. Each mode runs once per query in rotating order. Index or cache changes can alter results, so URL/order changes are shown too."
    ) + "</p>" + table(
        [report_text('text_97b34ccdb9bbff96'), report_text('text_e5d4c3fe74891d30'), report_text('text_4e114db7ef22ed94'), report_text('text_0cb354000eeb56ac'), report_text('text_cf43994d1deac579'), report_text('text_68ae682212f3e608'), report_text('text_ee9ab61706691a4c')] if zh else
        ["Cohort", "Enabled mode", "Pairs", "Mean Δ ms", "Median Δ ms", "Enabled slower", "URL/order changed"],
        output,
    ) + "</section>"


def known_page_chart(profile, selected, zh):
    """A scoped discovery check, deliberately separate from relevance quality."""
    key = "anchor_hit_5" if profile["protocol"]["top_k"] == 5 else "anchor_hit_10"
    stats = aggregate(selected)
    output = ['<h3>' + (report_text('text_5ac4004655f43cc0') if zh else "Known-page discovery") + '</h3>']
    cutoff = profile["protocol"]["top_k"]
    output.append('<p>' + (
        f"{report_text('text_dc58a4125462c343')}{cutoff}{report_text('text_b7876a459f5fb91b')}"
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
        report_text('text_af6bce6fc621c36f')
        if zh else "These are purposively selected tasks, not random user traffic. Missing the target does not make other results irrelevant; this is not an overall relevance ranking."
    ) + '</p>')
    return ''.join(output)


def outcome_table(profile, selected, zh, relevance_scores=None):
    cases = {c["id"]: c for c in profile["cases"]}
    names = {
        "relevance_hit": ("Useful result found", report_text('text_5d0f68818f94fb8f')),
        "relevance_miss": ("No useful result found", report_text('text_fb6a5abc9982f07b')),
        "quality_excluded": ("Reference excluded from quality", report_text('text_a280e1299dc83a91')),
        "api_error": ("API / transport error", report_text('text_d4bda75221b5de2a')),
        "empty": ("Empty response", report_text('text_2cc734b395f60968')),
        "domain_mismatch": ("Off-domain URL returned", report_text('text_c89bf327d9b8b527')),
        "target_miss": ("Known target absent", report_text('text_06666b22773acce0')),
        "target_hit": ("Known target found", report_text('text_a221fb0521e4a70b')),
        "source_hit": ("Published supporting source found", report_text('text_abae36f305e845f6')),
        "answer_hit": ("Reference answer text found", report_text('text_7816b19b994a78de')),
        "answer_miss": ("Reference answer text absent", report_text('text_79ab32335757995f')),
        "domain_hit": ("Requested site found", report_text('text_a0e07ba41d17b392')),
        "domain_miss": ("Requested site absent", report_text('text_0f57d58cae8be42d')),
        "source_miss": ("Published supporting source not found", report_text('text_83dcaec1ec7d72a6')),
        "probe": ("Unjudged query response", report_text('text_dde6912f0a908a4a')),
    }
    rows = []
    for m in profile["methods"]:
        group = [r for r in selected if r["method"] == m["id"]]
        scores = {(x["case_id"], x["method"]): x for x in (relevance_scores or [])}
        counts = Counter(classify(cases[r["case_id"]], r, cutoff=profile["protocol"]["top_k"], quality_excluded=r["case_id"] in profile.get("factual_quality_exclusions", []), relevance_grades=(scores.get((r["case_id"], r["method"])) or {}).get("ranked_grades"))[1] for r in group)
        for kind, n in counts.items():
            rows.append(cells([esc(m["label"]), names[kind][int(zh)], str(n), str(len(group))]))
    return (
        "<h2>"
        + (report_text('text_4583535b9f8920b2') if zh else "Primary outcome category")
        + "</h2>"
        + table(
            [report_text('text_22b9f0b66212809c'), report_text('text_c7ee1b6cd680268d'), report_text('text_dec5399b9021d1b8'), report_text('text_6316c7cda65f40f4')]
            if zh
            else ["Method", "Machine category", "Queries", "Method total"],
            rows,
        )
        + "<p>"
        + (
            report_text('text_e42c4aaf4c6dfb77')
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
                f"#{ranks[0]}" if ranks else (report_text('text_7089b1132e5ff6cb') if zh else "absent")
            )
            values.append(f'<a href="details/{row["id"]}.html">{label}</a>')
        entries.append(cells(values))
    return (
        "<h2>"
        + (report_text('text_05d849ad6517ce51') if zh else "Did the requested platform appear?")
        + "</h2>"
        + "<p>"
        + (
            report_text('text_c30ef5e75f529c0d')
            if zh
            else "First round: requested-domain links / returned links, followed by first rank. Host matching does not validate the topic, account, date or article body. 0/0 denotes an empty response."
        )
        + "</p>"
        + table([report_text('text_bcd6771e08ec7cc7') if zh else "Query"] + [m["label"] for m in profile["methods"]], entries)
    )


def performance_view(profile, diagnostics, zh, evidence_prefix=""):
    def t(en, cn):
        return cn if zh else en

    methods = diagnostics["methods"]
    body = '<section data-timing-decomposition><h2>' + t(f"Client and server timing: {len(profile['cases'])} queries", f"{report_text('text_43b8ee32a30a5193')}{len(profile['cases'])}{report_text('text_4afffb5842d9fe8e')}") + "</h2>"
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
            t("Method", report_text('text_22b9f0b66212809c')),
            t("Mean total ms", report_text('text_037470a4bb1bfb46')),
            t("Mean server ms", report_text('text_6bdc6f35d0ff4e20')),
            t("Mean remainder ms", report_text('text_a5012faeac92375e')),
            t("Mean TTFB ms", report_text('text_c3811faac236b2d1')),
            t("Mean body transfer ms", report_text('text_9d09ced5aa3a3bd7')),
            t("Mean response bytes", report_text('text_b0a9f6bd8e47a3d9')),
        ],
        rows,
    )
    body += (
        "<p>"
        + t(
            "All logical requests with available telemetry are included; n states the available population. Server time is provider-reported and unavailable where no comparable field exists. The remainder includes network transit and client setup; it is not pure network latency. TTFB and body transfer are sequential; DNS/connect/TLS clocks are cumulative and must not be added.",
            report_text('text_8b855644c94125d1'),
        )
        + "</p>"
    )
    body += "</section>"
    body += (
        "<h2>"
        + t("Work performed across the full call budget", report_text('text_1c18d744310bef15'))
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
                            or t("Not exposed", report_text('text_abcd7f6085638921'))
                        ),
                        esc(", ".join(f"{k}: {v}" for k, v in s["cache"].items())),
                        esc(
                            ", ".join(f"{k}: {v}" for k, v in s["usage"].items())
                            or t("Not exposed", report_text('text_abcd7f6085638921'))
                        ),
                    ]
                )
            )
    body += table(
        [
            t("Method", report_text('text_22b9f0b66212809c')),
            t("Mean recorded attempts/call", report_text('text_d15cbb2b15203b09')),
            t("Attempt outcomes", report_text('text_4746520c9eadaac4')),
            t("API cache states", report_text('text_8163db9696407915')),
            t("Reported usage units", report_text('text_cd7405d993965287')),
        ],
        rows,
    )
    body += (
        "<p>"
        + t(
            "Attempt durations overlap. Their sum is work, not user waiting time. Usage units have no monetary conversion until internal prices are supplied. Missing upstream telemetry does not mean zero upstream work.",
            report_text('text_a3b64f2bc7e54b0c'),
        )
        + "</p>"
    )
    candidates = [c for c in diagnostics["calls"] if c["after_first_ms"] is not None]
    if candidates:
        # Fixed selection rule: largest observed post-first-result wait, not a quality winner.
        specimen = max(candidates, key=lambda c: c["after_first_ms"])
        body += (
            "<h2>"
            + t("Longest wait after the first upstream result", report_text('text_bd83f9eec8e58857'))
            + "</h2>"
        )
        body += (
            f'<section class="specimen" data-timing-specimen="{esc(specimen["id"])}"><p>{esc(specimen["case_id"])} · {esc(specimen["method"])} · '
            + t("round", report_text('text_4890584bb57540f5'))
            + f" {specimen['round'] + 1}</p>"
        )
        body += (
            "<p>"
            + t(
                "First upstream results / server completion / additional wait: ",
                report_text('text_36f152b903730089'),
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
                t("Route", report_text('text_4e775ce57a2cdb04')),
                t("Start ms", report_text('text_a911ac9d5467c37e')),
                t("Duration ms", report_text('text_9b57cb66d8d83e05')),
                t("Outcome", report_text('text_bb7ef73495dd8a74')),
                t("Parsed items", report_text('text_ca649966b021f906')),
                t("Parallel timeline", report_text('text_e746a8c0935af6c6')),
            ],
            rows,
        )
        body += (
            f'<button data-evidence="{evidence_prefix}{esc(specimen["evidence_path"])}">'
            + t("Open exact response", report_text('text_1def27bb6f100afa'))
            + "</button></section>"
        )
        body += (
            "<p>"
            + t(
                "A first upstream result may contain too few or off-topic items. The extra wait is an observed scheduling interval, not a proven removable speedup. Any early-stop policy must be tested against the same quality requirements.",
                report_text('text_d351d71c72c38512'),
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
    cutoff = profile["protocol"]["top_k"]
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
        label = (report_text('text_3960bb6ddf23f98b') if kind == 'scenario' else report_text('text_a0e07ba41d17b392')) if zh else ('Find the given webpage' if kind == 'scenario' else 'Find the requested site')
        cells_html = '<td>' + label + '</td><td>' + str(len(ids)) + '</td>'
        for count in counts:
            cells_html += numeric_cell(f'{count}/{len(ids)}', count, 'discovery-' + kind, 'max', counts.count(best) == 1 and count == best)
        rows.append('<tr>' + cells_html + '</tr>')
    if not rows:
        return ''
    return '<section data-discovery><h2>' + (report_text('text_2c162df997b5320a') if zh else 'Directed webpage discovery') + '</h2><p>' + (f"{report_text('text_22d8e5f2c1e3adad')}{cutoff}{report_text('text_aa22402c3df7aaf0')}" if zh else f'For scenario queries with explicit webpage/site targets, check whether the top {cutoff} contain the given URL or domain. These diagnose discovery separately from graded relevance.') + '</p>' + table(([report_text('text_978e275133d14c1c'),report_text('text_35801615b907ad27')] if zh else ['Check','Queries']) + [m['label'] for m in profile['methods']], rows) + '</section>'


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
            row = '<td>' + esc(population.get("label", {"zh": population.get("id", report_text('text_a18713d1bce780df')), "en": population.get("id", "Benchmark")})["zh" if zh else "en"]) + '</td><td>' + esc(method["label"]) + '</td>'
            for i, value in enumerate(raw[index]):
                label = fmt(value) if value is not None else (report_text('text_b769180d023c4d19') if zh else "Not priced")
                if i == 3 and value is not None:
                    label += " " + esc(method.get("currency", "CNY"))
                row += numeric_cell(label, value, f'operation-{population["id"]}-{i}', directions[i], value is not None and winners.get(i) == value)
            rendered.append('<tr>' + row + '</tr>')
    if not rendered:
        return ''
    retries = int(bool(profile["protocol"].get("retry")))
    note = (f"{report_text('text_55f8fb67b6921ece')}{retries}{report_text('text_b08b96f051990691')}" if zh else f"One final result per provider and query, with at most {retries} failure retries; timing and cost include actual retries. P95 bounds 95% of waits.")
    return '<section data-benchmark-operations><h2>' + (report_text('text_033b6f38ccc278fd') if zh else "Performance and cost by collection") + '</h2><p>' + note + '</p>' + table([report_text('text_9f12964bc6a50908'),report_text('text_c80d519245a7e843'),report_text('text_cfb4169ac2aa7957'),"P50 ms","P95 ms",report_text('text_f4fc9fbdc9587557')] if zh else ["Collection","API","Mean ms","P50 ms","P95 ms","Estimated batch cost"], rendered) + '</section>'


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
        {"id": "overall", "label": {"en": "All questions", "zh": report_text('text_237b9d64d8d9cb67')}, "case_ids": [c["id"] for c in profile["cases"]]}
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
    from lexbench_websearch.scoring import factual_evidence
    cutoff = profile["protocol"]["top_k"]
    lang = "zh" if zh else "en"
    independent = content.get("independent_benchmarks")
    roots = [p for p in metric_populations(profile, content) if p.get("benchmark_root")] if independent else []
    heading = (report_text('text_e809904480c01001') if zh else "Core results by benchmark")
    scope = "；".join(p["label"][lang] + " " + str(len(set(p["case_ids"]) - set(profile.get("factual_quality_exclusions", [])))) + (report_text('text_02c1ee7d2ca6bb57') if zh else " queries") for p in roots)
    note = ((scope + (report_text('text_7479a7b11eff5ca3') if zh else ". Benchmarks are reported separately. ")) if scope else "") + (
        report_text('text_cad43d279ebfab87') if zh else "Mean, P50 and P95 cover all requests and retry waits; fees estimate the calls in each population.")
    if diagnostics:
        heading = report_text('text_7e49671f0d158d8b') if zh else "Reference URLs and requested sites: diagnostics"
        note = report_text('text_7e6aa840d0a02909') if zh else "These overlapping subsets diagnose URL and domain discovery. Uniform relevance scoring covers the complete in-house set."
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
                measures = [sum(factual_evidence(cases[r['case_id']], r['results'], cutoff=cutoff)[f'labeled_source_hit_{cutoff}'] for r in quality_group), sum(factual_sensitivity(cases[r['case_id']], r['results'], cutoff=cutoff)['metrics'][f'answer_in_delivered_text_{cutoff}'] for r in quality_group)]
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
                    label = ((report_text('text_b769180d023c4d19') if zh else 'Not priced') if value is None else label + ' ' + (report_text('text_7f77f23af7b0dcc8') if zh and currency == 'CNY' else esc(currency)))
                win = i in winners and winners[i] == value
                html_cells.append(numeric_cell(label, value if i else None, f"population-{i}", directions.get(i, "neutral"), win))
            rendered.append('<tr>' + ''.join(html_cells) + '</tr>')
        body += '<article data-population="' + esc(population['id']) + '"><h3>' + esc(population['label'][lang]) + f' · {len(quality_ids) if kind == "factual" else len(ids)}' + (report_text('text_02c1ee7d2ca6bb57') if zh else ' queries') + '</h3>'
        if population.get('description'):
            body += '<p>' + esc(population['description'][lang]) + '</p>'
        headings = [report_text('text_8adcec38513a6620')] if zh else ['Queries returning results']
        if ranked:
            headings = [report_text('text_9a98146dbe62ee65'), f"{report_text('text_03feca02c47d34ac')}{cutoff}）", report_text('text_a00c2c4fa82fdeff')] if zh else ['Ranking quality (0–100)', f'Useful results per query (/{cutoff})', 'Queries with a direct match']
            body += '<p><a href="quality.html#relevance-scores">' + (f"{report_text('text_e6585418b3eec96e')}{cutoff}{report_text('text_7952d209d9356fca')}" if zh else f'Quality = nDCG@{cutoff} × 100 · Inspect grades and calculation') + '</a></p>'
        elif kind == 'factual':
            if quality_ids != ids:
                body += '<p data-quality-exclusions>' + (f"{report_text('text_b75d8dea6b8021ed')}{len(quality_ids)}{report_text('text_be2b0d2270c8cdd2')}{len(ids) - len(quality_ids)}{report_text('text_3b134b82e4e3fed2')}" if zh else f'Quality uses {len(quality_ids)} reviewed-reference queries for every method; {len(ids) - len(quality_ids)} defective references are quarantined. Latency and cost retain all {len(ids)} queries.') + '</p>'
            body += '<p class="population-note">' + (f"{report_text('text_2b87bbc2cd13d5ef')}{cutoff}{report_text('text_f69301317e6d7264')}" if zh else f'Source found: a published supporting URL appears in the top {cutoff}. Answer cue: allowed reference-answer text appears in delivered text after excluding explicit dataset mirrors; this is not semantic accuracy.') + '</p>'
            headings = [report_text('text_c08ea4fec537b912'), report_text('text_140efee5493ca13f')] if zh else ['Published source found', 'Answer cue (mirrors excluded)']
        elif kind == 'scenario':
            body += '<p class="population-note">' + (f"{report_text('text_b3899410224cd906')}{cutoff}{report_text('text_276d78952589b984')}" if zh else f'Target found: a reference page appears in the top {cutoff}. MRR averages reciprocal first-target rank: 1 for first, 0.5 for second, and 0 for a miss. Higher means targets appear earlier.') + '</p>'
            headings = [report_text('text_7c4232ea7201e8a5'), report_text('text_d97598e9de7fff45')] if zh else ['Reference target found', 'Target MRR']
        elif kind == 'site':
            body += '<p class="population-note">' + (f"{report_text('text_502b7f2f3c0766f9')}{cutoff}{report_text('text_a2f6c28bc3a0dcdf')}{cutoff}{report_text('text_d2ff3b1cd74b9df9')}" if zh else f'Site satisfied: at least one top-{cutoff} result uses the requested domain. Domain fraction averages the share of top-{cutoff} results from that domain. Neither metric judges page relevance.') + '</p>'
            headings = [report_text('text_f1e27452e8dffa33'), report_text('text_088efa3d9c398139')] if zh else ['Site constraint met', 'Requested-domain fraction']
        headers = ([report_text('text_22b9f0b66212809c')] + headings + [report_text('text_cfb4169ac2aa7957'),report_text('text_a97243f6aa6fcd01'),report_text('text_dfaa28d089149c9d'),report_text('text_7674cf339137002c')]) if zh else (['Method'] + headings + ['Mean ms','P50 ms','P95 ms','Population cost'])
        body += table(headers, rendered)
        if ranked:
            body += '<details><summary>' + (report_text('text_5d391279ab209be7') if zh else 'API delivery') + '</summary><p>' + ' · '.join(esc(m['label']) + ' ' + str(stats[m['id']]['nonempty']) + '/' + str(len(ids)) for m in profile['methods']) + (report_text('text_1af9dd4156b9a317') if zh else ' queries returned results.') + '</p></details>'
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
    return '<label>' + (report_text('text_7b0823301b8b2501') if zh else "Diagnostic subset") + '<select data-population-filter><option value="all">' + (report_text('text_5c55a67935af8f45') if zh else "All") + '</option>' + ''.join('<option value="' + esc(p["id"]) + '" data-case-ids="' + esc(json.dumps(p["case_ids"])) + '">' + esc(p["label"]["zh" if zh else "en"]) + f' ({len(p["case_ids"])})</option>' for p in groups) + '</select></label>'


def annotation_detail(annotation, zh):
    text = (report_text('text_b2c07bd04abc3684') if zh else "This reference was added independently from the original query after collection. Search APIs were not called again.")
    body = '<section class="specimen"><h3>' + (report_text('text_067ded3561dd37c1') if zh else "Additional reference evidence") + '</h3><p>' + text + '</p>'
    for source in annotation['sources']:
        body += '<p><a href="' + esc(source['url']) + '">' + esc(source['title']) + '</a> · ' + esc(source['verified_at']) + '</p><blockquote>' + esc(source['excerpt']) + '</blockquote>'
    return body + '</section>'


def dataset_context(profile, content, zh):
    if content.get("presentation") != "comparison-report":
        return ''
    populations = [p for p in metric_populations(profile, content) if p.get("benchmark_root")]
    if not populations:
        populations = [{"label": {"zh": profile.get("title", report_text('text_a18713d1bce780df')), "en": profile.get("title", "Benchmark")}, "case_ids": [c["id"] for c in profile["cases"]]}]
    rows = []
    for population in populations:
        cases = [c for c in profile["cases"] if c["id"] in population["case_ids"]]
        if not cases:
            continue
        factual = all("reference_answer" in c for c in cases)
        purpose = (report_text('text_ac0a61ce05aea60d') if factual else report_text('text_58dc4363ebdd11c7')) if zh else ("Given-source and reference-answer text checks" if factual else "Query-result relevance")
        rows.append(cells([esc(population.get("label", {"zh": population.get("id", report_text('text_a18713d1bce780df')), "en": population.get("id", "Benchmark")})["zh" if zh else "en"]), str(len(cases)), purpose]))
    exact = sum(c.get("provenance_kind") == "community_exact" for c in profile["cases"])
    screenshots = sum(c.get("provenance_kind") == "user_screenshot" for c in profile["cases"])
    parts = []
    if exact:
        parts.append(f"{exact}{report_text('text_a643809559e322cb')}" if zh else f"{exact} original queries have traceable public sources")
    if screenshots:
        parts.append(f"{screenshots}{report_text('text_276bde43813ddcfb')}" if zh else f"{screenshots} queries were transcribed from user screenshots; their original authors and posts are unconfirmed")
    note = ("；" if zh else "; ").join(parts)
    if note:
        note += "。" if zh else ". "
    note += report_text('text_299dd861aae8bf91') if zh else "Original queries, source URLs and derivations are listed below."
    return '<section><h2>' + (report_text('text_d279c9e9a4325099') if zh else "Datasets and evaluation scope") + '</h2>' + table([report_text('text_93119b57b49017d0'),report_text('text_35801615b907ad27'),report_text('text_05b36669c4ad9a73')] if zh else ["Dataset","Queries","Purpose"], rows) + '<p>' + note + '</p></section>'
