"""Bounded, registry-driven same-task comparisons for measured search results."""

import json
from functools import partial
from pathlib import Path
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


ASSETS = Path(__file__).with_name("assets")
STYLE = ASSETS.joinpath("comparison.css").read_text(encoding="utf-8")

SCRIPT = ASSETS.joinpath("comparison.js").read_text(encoding="utf-8")


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
