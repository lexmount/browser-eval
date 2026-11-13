"""Bounded, registry-driven same-task comparisons for measured search results."""

import json
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from harness.reporting.presentation import esc
from harness.reporting.privacy import WEB_SEARCH_PREVIEW
from harness.web_search.messages import report_text, zh_text
from harness.web_search.relevance import parse_judgments
from lexbench_websearch.evidence_quality import is_benchmark_page
from lexbench_websearch.relevance import query_metrics
from lexbench_websearch.scoring import (
    answer_evidence_key,
    evaluate,
    factual_evidence,
    in_domain,
    ranking_identity,
    ranking_ids,
    task_acceptance,
)


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
    level = case.get('evaluation_level')
    if level != 'l2' and safe["url"] and case.get("reference_urls"):
        source = bool(factual_evidence(case, [safe])["labeled_source_hit_5"])
    elif level != 'l2' and safe["url"] and case.get("anchors"):
        source = bool(evaluate(case, [safe])["anchor_hit_5"])
    if "reference_answer" in case:
        for field in fields:
            if level == 'l2' and field != 'snippet':
                fields[field] = None
                continue
            candidate = dict(safe)
            for other in fields:
                if other != field:
                    candidate[other] = ""
            fields[field] = bool(factual_evidence(case, [candidate])[answer_evidence_key(case)])
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
    documents = ({p['case_id']: {c['candidate_id']: c['url'] for c in p['candidates']}
                  for p in (relevance or {}).get('blind_candidate_packets', [])}
                 if profile.get('protocol', {}).get('relevance_input') == 'delivered-previews/1' else {})
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
            has_labels = case["id"] in labels and method["id"] in profile.get("relevance_methods", [m["id"] for m in methods])
            items = []
            for item in row.get("results", [])[:cutoff]:
                contaminated = "reference_answer" in case and is_benchmark_page(item, case=case)
                source_match, answer_fields = _item_checks(
                    case, item, quality_excluded=quality_excluded,
                    contamination_excluded=contaminated)
                displayed = {field: _preview(item.get(field), limit)
                             for field, limit in (("title", None), ("snippet", None),
                                                  ("summary", None), ("content", 480))}
                if item.get('invalid_slot'):
                    displayed['title'] = report_text('Invalid result slot', zh)
                _, visible_answers = _item_checks(
                    case, {"url": item.get("url"), **displayed},
                    quality_excluded=quality_excluded, contamination_excluded=contaminated)
                candidate_id = ranking_identity(case["id"], item, preview=profile.get("protocol", {}).get("relevance_input") == "delivered-previews/1", level=profile.get("protocol", {}).get("relevance_level")) if _url(item.get("url")) else ""
                judgment = judgments.get(case["id"], {}).get(candidate_id, {}) if has_labels else {}
                items.append({"url": "" if item.get("invalid_slot") else _url(item.get("url")),
                              "grade_reason": judgment.get("reason", ""),
                              "grade_source": 'slot-rule' if item.get('invalid_slot') else judgment.get("grade_source", (relevance or {}).get("judge", {}).get("kind", "unjudged")) if has_labels else "unjudged",
                              "grade_evidence": (judgment.get("evidence") or (report_text("Selected field is empty", zh) if judgment.get("evidence_empty") else "")),
                              "contamination_excluded": contaminated,
                              "source_match": source_match, "answer_fields": visible_answers,
                              "answer_outside_preview": answer_fields["content"] and not visible_answers["content"],
                              "grade": (0 if item.get("invalid_slot") else labels[case["id"]].get(ranking_identity(case["id"], item, preview=profile.get("protocol", {}).get("relevance_input") == "delivered-previews/1", level=profile.get("protocol", {}).get("relevance_level"))) if has_labels and _url(item.get("url")) and not (quality_excluded or contaminated) else None),
                              "site_match": not (quality_excluded or contaminated) and any(in_domain(item.get("url", ""), d) for d in case.get("domains", [])),
                              **displayed})
            ranking = ranking_ids(case["id"], [item if _url(item.get("url")) else {"invalid_slot": True} for item in row.get("results", [])[:cutoff]], preview=profile.get("protocol", {}).get("relevance_input") == "delivered-previews/1", level=profile.get("protocol", {}).get("relevance_level"))
            query_score = query_metrics(ranking, labels[case["id"]], cutoff, documents=documents.get(case["id"])) if has_labels and not quality_excluded else None
            outputs[method["id"]] = {
                "score": query_score,
                "primary": None if case['id'] in labels and not has_labels else acceptance(case, row, primary_factual_metric if "reference_answer" in case else "primary", cutoff=cutoff, quality_excluded=quality_excluded, relevance_grades=query_score['ranked_grades'] if query_score else None),
                "quality": bool(query_score['useful']) if query_score else None,
                "source": None if profile.get("protocol", {}).get("relevance_level") == "l2" else acceptance(case, row, quality_excluded=quality_excluded, cutoff=cutoff), "answer": acceptance(case, row, "answer", quality_excluded=quality_excluded, cutoff=cutoff),
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
        "qualityScore": t("Ranking quality · nDCG@{cutoff}", cutoff=cutoff), "usefulCount": t("Useful results"),
        "outputScroll": t("Scrollable comparison outputs"),
        "loadError": t("Saved comparison data could not be loaded. Refresh the page to retry."),
        "calculation": t("Inspect score calculation"),
        "notReturned": t("Not returned"), "duplicate": t("Duplicate: no extra credit"),
        "zeroPool": t("No relevant candidate in the pool: score zero."),
        "rank": t("Rank", context="original"), "weightedGain": t("Rank contribution"),
        "actual": t("Actual weighted sum"), "ideal": t("Pooled ideal weighted sum"),
        "idealGrades": t("Ideal top-{cutoff} grades", cutoff=cutoff),
        "direct": t("Direct match found"), "noDirect": t("No direct match"),
        "gradeSources": {"human": t("Human annotation"), "model": t("Model grading"), "source-rule": t("Source requirement"), "source_policy": t("Source requirement"), 'slot-rule': t('Invalid result slot')},
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
    def options(values):
        return ''.join(f'<option value="{e(k)}">{e(v)}</option>' for k, v in values)

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
    if profile.get('protocol', {}).get('relevance_level') != 'l2' and any(case['criteria']['urls'] for case in data['cases']):
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
        + ''.join('<label><input type="checkbox" data-compare-method' + (' checked' if i < 2 else '') + ' value="' + e(k) + '">' + e(v) + '</label>' for i,(k,v) in enumerate(methods))
        + '</div></fieldset><div class="compare-counts" data-comparison-counts aria-live="polite"></div><label>' + t("Show tasks where") + '<select data-compare-relation>' + options([('all', t("All outcome relations"))] + list(labels['relations'].items())) + '</select></label><div class="compare-relations" data-relation-tasks></div><div class="compare-pager"><button data-relation-prev>' + t("Previous") + '</button><span data-relation-page aria-live="polite"></span><button data-relation-next>' + t("Next") + '</button></div>'
        + '<h2>' + t("Actual outputs, side by side") + '</h2><p class="compare-notice">' + t("Choose methods above; this view scrolls horizontally and vertically. ") + delivered_fields + t(" Green marks identify the matched fields or useful results. Relevance grades and expandable score calculations are shown where available.") + '</p><div data-output-pages></div><div class="compare-pager"><button data-output-prev>' + t("Previous") + '</button><span data-output-page aria-live="polite"></span><button data-output-next>' + t("Next") + '</button></div>'
        + '<script type="application/json" data-comparison-data>' + json.dumps(data, ensure_ascii=False).replace('<', '\\u003c').replace('&', '\\u0026') + '</script></section><script>' + SCRIPT + '</script>')


def render_level_summary(view, zh=False, *, content=None):
    """Use one comparison renderer for a verified level, with its own main metric."""
    from harness.web_search.core import aggregate
    from harness.web_search.report import (
        catalog_billing_basis,
        catalog_prices,
        comparable_catalog_prices,
        fmt,
        numeric_cell,
        numeric_winners,
        table,
    )
    from lexbench_websearch.relevance import paired_bootstrap

    profile, rows = view['profile'], view['rows']
    level = profile['protocol']['relevance_level']
    methods = profile['methods']
    method_ids = [method['id'] for method in methods]
    t = partial(report_text, zh=zh)
    titles = dict(l1=t('Result lists: finding pages'), l2=t('Extra text: supplying evidence'),
                  l3=t('Generated answers: answering correctly'))
    labels = dict(ndcg=t('nDCG@5'), precision=t('Precision@5'), success=t('Success@5'),
                  source_rr=t('MRR@5'), source_hit=t('Source Hit@5'),
                  answer_match=t('Answer Match@5') if level == 'l1' else t('Extra-text Answer Match@5'),
                  answer_grade=t('Answer usefulness / 3'), correct=t('SimpleQA accuracy'),
                  citation_support=t('Citation support'), cited_urls_in_results=t('Cited URLs in results'),
                  context_characters=t('Text characters / query'), mean_ms=t('Mean ms'),
                  p50_ms=t('P50 ms'), p95_ms=t('P95 ms'))
    body = '<section class="level-summary" data-level-panel="' + level + '" data-output-level="' + level + '" id="' + level + '">'
    body += '<h2>' + level.upper() + ' · ' + titles[level] + '</h2>'
    if len(methods) > 1:
        body += '<p class="metric-caption">' + t('Green marks the best observed value; a star also requires a statistically supported lead.') + '</p>'
    prices_content = dict(content or {})
    if prices_content.get('catalog_pricing'):
        prices_content['catalog_pricing'] = {mid: prices_content['catalog_pricing'][mid] for mid in method_ids}
    prices = catalog_prices(profile, prices_content)
    for benchmark, panel in view['benchmarks'].items():
        ids = set(panel['timing_case_ids'])
        stats = aggregate([row for row in rows if row['case_id'] in ids])
        n = len(panel['case_ids'])
        definitions = list(panel['metrics'])
        quality_keys = [metric['key'] for metric in definitions]
        if level == 'l3':
            definitions += [dict(key='citation_support', label='Citation support', direction='max'),
                            dict(key='cited_urls_in_results', label='Cited URLs in results', direction='max')]
        else:
            definitions.append(dict(key='context_characters', label='Text characters / query', direction='neutral'))
        definitions += [dict(key=key, label=label, direction='min') for key, label in
                        [('mean_ms', 'Mean ms'), ('p50_ms', 'P50 ms'), ('p95_ms', 'P95 ms')]]
        labels['price'] = t('USD / 1,000 calls') if prices is not None else t('Price / 1,000 calls')
        price_direction = ('min' if prices is not None and comparable_catalog_prices(prices) else 'neutral')
        definitions.append(dict(key='price', label='USD / 1,000 calls' if prices is not None
                                 else 'Price / 1,000 calls', direction=price_direction))
        intervals = {}
        if len(methods) > 1 and n >= 30:
            for key in quality_keys:
                intervals[key] = paired_bootstrap(panel['scores'], method_ids, metric=key)
        records = []
        for method in methods:
            mid = method['id']
            scored = [s for s in panel['scores'] if s['method'] == mid]
            values = {key: sum(s[key] for s in scored) / n for key in quality_keys} if n else {key: None for key in quality_keys}
            if level == 'l3':
                citations = sum(s['citation_count'] for s in scored)
                values.update(citation_support=sum(s['supported_citations'] for s in scored) / citations if citations else None,
                              cited_urls_in_results=sum(s['citations_in_results'] for s in scored) / citations if citations else None)
            else:
                delivered = [r for r in rows if r['method'] == mid and r['case_id'] in ids]
                values['context_characters'] = sum(len(item.get('snippet') or '') for r in delivered for item in r['results']) / len(delivered)
            latency = stats[mid]['all_latency']
            values.update(mean_ms=latency['mean'], p50_ms=latency['p50'], p95_ms=latency['p95'],
                          price=prices[mid]['amount'] if prices is not None else method.get('price_per_1000'))
            records.append((method, values))
        columns = [[values[d['key']] for d in definitions] for _, values in records]
        directions = {i: d['direction'] if len(methods) > 1 else 'neutral'
                      for i, d in enumerate(definitions)}
        winners = numeric_winners(columns, directions)
        rendered = []
        for method, values in sorted(records, key=lambda row: (row[1][panel['primary_metric']] is None,
                                                               -(row[1][panel['primary_metric']] or 0))):
            cells = ['<td>' + esc(method['label']) + '</td>']
            for index, metric in enumerate(definitions):
                key, value = metric['key'], values[metric['key']]
                label = '—' if value is None else (f'{value * 100:.1f}' if key == 'ndcg' else
                         f'{value:.3f}' if key == 'source_rr' else f'{value:.2f}' if key == 'answer_grade' else
                         f'{value:.1%}' if key in quality_keys or key in ('citation_support', 'cited_urls_in_results') else fmt(value))
                if key == 'mean_ms':
                    batches = [b for b in profile.get('method_collections', []) if method['id'] in b['methods']]
                    contexts = []
                    for batch in batches:
                        dates = str(batch.get('started_at') or batch.get('as_of') or '')[:10]
                        concurrency = batch.get('protocol', {}).get('concurrency')
                        contexts.append(dates + (', ' + t('concurrency {count}', count=concurrency) if concurrency else ''))
                    modes = ', '.join(k + '=' + str(v).lower() for k,v in (method.get('output_options') or {}).items())
                    context = ' · '.join(dict.fromkeys([*contexts, modes]))
                    if context.strip(' ·'):
                        label += '<small class="measurement-context">' + esc(context) + '</small>'
                if key == 'price':
                    label += (catalog_billing_basis(prices[method['id']], zh) if prices is not None
                              else ' ' + esc(method.get('currency', '')))
                significant = False
                comparison = intervals.get(key)
                if comparison:
                    best = comparison['best_observed']
                    significant = (method['id'] == best and len(methods) > 1
                                   and all(result['difference_to_best'][1] < 0 for mid, result in comparison['methods'].items() if mid != best))
                    if key == panel['primary_metric']:
                        low, high = comparison['methods'][method['id']]['interval']
                        percent = key not in ('ndcg', 'source_rr', 'answer_grade')
                        scale = 100 if key == 'ndcg' or percent else 1
                        unit = '%' if percent else ''
                        digits = 3 if key == 'source_rr' else 2 if key == 'answer_grade' else 1
                        label += f'<small class="metric-ci">95% CI [{low * scale:.{digits}f}{unit}, {high * scale:.{digits}f}{unit}]</small>'
                winner = index in winners and value == winners[index]
                cells.append(numeric_cell(label, value, level + '-' + benchmark + '-' + key,
                                          directions[index], winner, significant=significant))
            rendered.append('<tr data-summary-method="' + esc(method['id']) + '">' + ''.join(cells) + '</tr>')
        name = t('Scenario relevance') if benchmark == 'scenario' else t('SimpleQA Verified')
        body += '<article data-level-benchmark="' + benchmark + '"><h3>' + name + f' · {n}' + t(' queries') + '</h3>'
        body += '<p data-dataset-purpose>' + level_purpose(level, benchmark, zh) + '</p>'
        body += level_conclusion(view, panel, zh)
        body += '<p class="primary-guide" data-primary-guide>' + t('Primary metric: {metric}. Higher is better; the initial order is descending.', metric=labels[panel['primary_metric']]) + '</p>'
        body += '<label class="summary-filter">' + t('Filter methods') + '<input type="search" data-level-summary-filter></label>'
        headers = [t('Method')] + [labels[d['key']] + (' × 100' if d['key'] == 'ndcg' else '') for d in definitions]
        body += table(headers, rendered, bounded=True, primary_column=1, significance=True, query_count=n)
        body += '</article>'
    return body + '</section>'


def render_level_findings(view, zh=False):
    """Explain each benchmark through its primary metric, without a cross-level rank."""
    t = partial(report_text, zh=zh)
    level = view['profile']['protocol']['relevance_level']
    body = '<section data-level-panel="' + level + '" id="' + level + '" data-level-findings="' + level + '"><h2>' + level.upper() + '</h2>'
    for benchmark, panel in view['benchmarks'].items():
        key = panel['primary_metric']
        name = t('Scenario relevance') if benchmark == 'scenario' else t('SimpleQA Verified')
        if benchmark == 'scenario':
            description = (t('Scenario queries test how useful the generated answer is, from 0 to 3.') if level == 'l3'
                           else t('Scenario queries test whether the delivered text satisfies the query; nDCG@5 rewards relevant results appearing earlier.'))
        elif level == 'l1':
            description = t('Factual queries test retrieval of the annotated supporting pages. MRR@5 rewards finding that source earlier; alternative pages are not automatically equivalent sources.')
        elif level == 'l2':
            description = t('Factual queries test whether the extra text contains the reference answer. Source ranking is not measured at this level.')
        else:
            description = t('Factual answers are graded correct, incorrect or not attempted with the official SimpleQA template. The primary metric is the fraction graded correct.')
        metric = (t('nDCG@5') + ' × 100' if key == 'ndcg' else t('MRR@5') if key == 'source_rr' else
                  t('Answer usefulness / 3') if key == 'answer_grade' else t('SimpleQA accuracy') if key == 'correct' else
                  t('Extra-text Answer Match@5'))
        values = []
        n = len(panel['case_ids'])
        for method in view['profile']['methods']:
            scored = [s for s in panel['scores'] if s['method'] == method['id']]
            mean = sum(s[key] for s in scored) / n if n else None
            values.append((method, mean))
        ordered = sorted(values, key=lambda pair: (pair[1] is None, -(pair[1] or 0)))
        body += '<article data-findings-benchmark="' + benchmark + '"><h3>' + name + f' · {n}' + t(' queries') + '</h3><p data-dataset-purpose>' + description + '</p>'
        body += '<figure class="metric-bars" data-primary-chart="' + key + '"><figcaption>' + esc(metric) + '</figcaption>'
        for method, value in ordered:
            width = max(0, min(100, (value or 0) / (3 if key == 'answer_grade' else 1) * 100))
            label = ('—' if value is None else f'{value * 100:.1f}' if key == 'ndcg' else
                     f'{value:.3f}' if key == 'source_rr' else f'{value:.2f}' if key == 'answer_grade' else f'{value:.1%}')
            body += '<div class="metric-bar-row" data-method="' + esc(method['id']) + '" data-value="' + esc(value) + '"><span>' + esc(method['label']) + '</span><div class="metric-bar-track"><i style="width:' + f'{width:.4f}' + '%"></i></div><strong>' + label + '</strong></div>'
        body += '</figure>' + level_conclusion(view, panel, zh) + '</article>'
        if level == 'l1' and benchmark == 'factual':
            body += source_matching_chart(view, zh) + factual_outcome_chart(view, zh)
    return body + '</section>'


def render_level_capabilities(views, zh=False):
    """Group participation by the adapter-derived outputs that actually qualify."""
    from harness.web_search.report import cells, table

    t = partial(report_text, zh=zh)
    meanings = dict(l1=t('Title and primary preview'), l2=t('Title and extra original text'),
                    l3=t('Generated answer and its citations'))
    body = '<section data-level-capabilities>'
    for level, view in views.items():
        body += '<article data-capability-level="' + level + '"><h2>' + level.upper() + '</h2>'
        rows = [cells([esc(method['label']), meanings[level]]) for method in view['profile']['methods']]
        body += table([t('Method'), t('Scored input')], rows) + '</article>'
    return body + '</section>'


def render_level_results(views, zh=False, *, detail_link=None):
    """Use each verified view for comparisons; generated answers get absolute grades."""
    from harness.web_search.report import table

    t = partial(report_text, zh=zh)
    body = render_level_tabs(views, zh)
    for level, view in views.items():
        body += '<section data-level-panel="' + level + '" id="' + level + '" data-level-results="' + level + '"><h2>' + level.upper() + '</h2>'
        if level != 'l3':
            body += render_comparison(view['profile'], view['rows'], zh, detail_link,
                                      view['relevance'], primary_factual_metric='source' if level == 'l1' else 'answer')
        else:
            cases = {c['id']: c for c in view['profile']['cases']}
            rows = {(r['case_id'], r['method']): r for r in view['rows']}
            labels = {m['id']: m['label'] for m in view['profile']['methods']}
            body += '<label>' + t('Find') + '<input type="search" data-level-inventory-filter></label>'
            rendered = []
            for score in view['answer_scores']:
                row = rows[score['case_id'], score['method']]
                case = cases[row['case_id']]
                benchmark = t('SimpleQA Verified') if 'reference_answer' in case else t('Scenario relevance')
                grade = (dict(CORRECT=t('Correct'), INCORRECT=t('Incorrect'), NOT_ATTEMPTED=t('Not attempted'))[score['factual_grade']]
                         if 'factual_grade' in score else str(score['answer_grade']) + ' / 3')
                link = detail_link(row) if detail_link else 'details/' + row['id'] + '.html'
                cells = [esc(benchmark), esc(case['query']), esc(labels[row['method']]), esc(grade),
                         str(score['supported_citations']) + ' / ' + str(score['citation_count']),
                         '<a href="' + esc(link) + '#l3">' + t('Open full response and evidence') + '</a>']
                rendered.append('<tr data-answer-result="' + esc(row['id']) + '">' + ''.join('<td>' + c + '</td>' for c in cells) + '</tr>')
            body += table([t('Benchmark'), t('Query'), t('Method'), t('Grade'), t('Supported citations'), t('Evidence')], rendered, bounded=True)
        body += '</section>'
    return body


def render_level_detail(views, row, zh=False):
    """Show the precise scored text and judgments beside a single native response."""
    t = partial(report_text, zh=zh)
    body = '<section data-level-detail>'
    for level, view in views.items():
        projected = next((r for r in view['rows'] if r['id'] == row['id']), None)
        if projected is None:
            continue
        body += '<article id="' + level + '" data-detail-level="' + level + '"><h2>' + level.upper() + '</h2>'
        if level == 'l3':
            score = next((s for s in view['answer_scores'] if s['case_id'] == row['case_id'] and s['method'] == row['method']), None)
            body += '<h3>' + t('Generated answer') + '</h3><div class="native-answer">' + esc(row.get('answer') or '') + '</div>'
            if score:
                grade = (dict(CORRECT=t('Correct'), INCORRECT=t('Incorrect'), NOT_ATTEMPTED=t('Not attempted'))[score['factual_grade']]
                         if 'factual_grade' in score else str(score['answer_grade']) + ' / 3')
                body += '<p data-answer-grade>' + t('Grade') + ': ' + esc(grade) + '</p><p>' + t('Supported citations') + ': ' + str(score['supported_citations']) + ' / ' + str(score['citation_count']) + '</p>'
                call_key = row['method'] + '/' + row['case_id'] + '/scenario'
                call = view.get('answers', {}).get('calls', {}).get(call_key)
                if call:
                    judgment = json.loads(call['response']['choices'][0]['message']['content'])
                    body += '<details><summary>' + t('Scoring reason and quoted evidence') + '</summary><p>' + esc(judgment['reason']) + '</p><blockquote>' + esc(judgment['evidence']) + '</blockquote></details>'
                from lexbench_websearch.answers import answer_packet

                case = next(c for c in view['profile']['cases'] if c['id'] == row['case_id'])
                packet = answer_packet(case, dict(row, results=row['results'][:view['profile']['protocol']['top_k']]))
                body += '<details><summary>' + t('Citations') + '</summary><pre>' + esc(json.dumps(packet['citations'], ensure_ascii=False, indent=2)) + '</pre></details>'
        else:
            evidence = view['relevance']
            packet = next((p for p in evidence.get('blind_candidate_packets', []) if p['case_id'] == row['case_id']), None)
            call = next((c for c in evidence.get('judge', {}).get('calls', []) if c['case_id'] == row['case_id']), None)
            judgments = ({item['candidate_id']: item for item in parse_judgments(packet, call['response'])} if packet and call else {})
            scores = next((s for panel in view['benchmarks'].values() for s in panel['scores']
                           if s['case_id'] == row['case_id'] and s['method'] == row['method']), None)
            if scores:
                body += '<dl data-level-metrics>' + ''.join('<dt>' + esc(metric['label']) + '</dt><dd>' + esc(scores[metric['key']]) + '</dd>'
                            for panel in view['benchmarks'].values() if row['case_id'] in panel['case_ids'] for metric in panel['metrics']) + '</dl>'
            for index, item in enumerate(projected['results']):
                candidate_id = ranking_identity(row['case_id'], item, preview=True, level=level) if not item.get('invalid_slot') else ''
                grade = evidence.get('labels', {}).get(row['case_id'], {}).get(candidate_id)
                if grade is not None:
                    body += '<p data-result-grade>' + t('Relevance grade') + ': ' + str(grade) + ' / 3</p>'
                    judgment = judgments.get(candidate_id, {})
                    if judgment.get('reason'):
                        body += '<details><summary>' + t('Scoring reason and quoted evidence') + '</summary><p>' + esc(judgment['reason']) + '</p><blockquote>' + esc(judgment.get('evidence') or '') + '</blockquote></details>'
                body += '<article class="specimen"><h3>' + str(index+1) + '. ' + esc(item.get('title') or '') + '</h3><p>' + esc(item.get('url') or '') + '</p><div class="native-answer">' + esc(item.get('snippet') or '') + '</div></article>'
        body += '</article>'
    return body + '</section>'


def level_purpose(level, benchmark, zh=False):
    t = partial(report_text, zh=zh)
    if benchmark == 'scenario':
        return (t('Everyday search tasks cover official pages, recent facts, technical questions and website constraints. Read nDCG@5 for how useful the first five results are and how early useful results appear.')
                if level != 'l3' else t('Everyday search tasks test whether the generated answer directly satisfies the request. Read answer usefulness, scored from 0 to 3.'))
    if level == 'l1':
        return t('SimpleQA Verified asks short factual questions with independently annotated supporting pages. Read MRR@5 for source ranking and Source Hit@5 for coverage. Answer Match@5 only checks answer words in titles and primary previews.')
    if level == 'l2':
        return t('The same factual questions test whether additional original excerpts contain the answer. Read Extra-text Answer Match@5; titles, primary previews and generated answers cannot supply this score.')
    return t('The same factual questions test the generated answer against the official SimpleQA correct, incorrect and not-attempted rubric. Read accuracy first, then citation support.')


def level_conclusion(view, panel, zh=False):
    t = partial(report_text, zh=zh)
    key, n = panel['primary_metric'], len(panel['case_ids'])
    if not n:
        return ''
    values = [(m['label'], sum(s[key] for s in panel['scores'] if s['method'] == m['id']) / n)
              for m in view['profile']['methods']]
    values.sort(key=lambda pair: -pair[1])
    best = values[0][1]
    names = ', '.join(name for name, value in values if value == best)
    value = (f'{best * 100:.1f}' if key == 'ndcg' else f'{best:.3f}' if key == 'source_rr' else
             f'{best:.2f} / 3' if key == 'answer_grade' else f'{best:.1%}')
    sentence = (t('{method}: {value} across {count} queries. This level has one participant.', method=names, value=value, count=n)
                if len(values) == 1 else t('Highest observed primary score: {method}, {value}.', method=names, value=value))
    return '<p class="level-conclusion" data-level-conclusion>' + esc(sentence) + '</p>'


def source_matching_chart(view, zh=False):
    """Recompute all-source score components; no provider-specific URL exception."""
    t = partial(report_text, zh=zh)
    panel = view['benchmarks']['factual']
    if not panel['case_ids']:
        return ''
    ids = set(panel['case_ids'])
    cases = {c['id']: c for c in view['profile']['cases'] if c['id'] in ids}
    labels = [t('Original URL match'), t('Equivalent URL form'), t('Verified copy of the same page'), t('Reference page not retrieved')]
    body = '<figure class="source-matching" data-source-matching><figcaption>' + t('Where source matches come from') + '</figcaption><p>' + t('Every bar uses the complete factual quality population. Equivalent URL forms and verified copies count in the main ranking for every method. The remaining segment means none of the first five results matches an annotated page; it does not identify a geographic restriction.') + '</p>'
    body += '<ul class="source-legend">' + ''.join('<li class="source-part-' + str(i) + '">' + label + '</li>' for i,label in enumerate(labels)) + '</ul>'
    for method in view['profile']['methods']:
        counts = [0, 0, 0, 0]
        for row in view['rows']:
            if row['method'] != method['id'] or row['case_id'] not in ids:
                continue
            case = cases[row['case_id']]
            original = {k: v for k,v in case.items() if k not in ('url_equivalence', 'equivalent_reference_urls', 'equivalent_reference_provenance')}
            folded = dict(original, url_equivalence='document-url/2')
            a = factual_evidence(original, row['results'])['labeled_source_hit_5']
            b = factual_evidence(folded, row['results'])['labeled_source_hit_5']
            c = factual_evidence(case, row['results'])['labeled_source_hit_5']
            counts[0 if a else 1 if b else 2 if c else 3] += 1
        body += '<div class="source-chart-row" data-source-method="' + esc(method['id']) + '"><strong>' + esc(method['label']) + '</strong><div class="source-stack">'
        body += ''.join('<span class="source-part-' + str(i) + '" style="width:' + str(count/len(ids)*100) + '%" title="' + esc(labels[i]) + ': ' + str(count) + '">' + (str(count) if count else '') + '</span>' for i,count in enumerate(counts))
        body += '</div><p>' + ' · '.join(label + ' ' + str(count) for label,count in zip(labels,counts,strict=True)) + '</p></div>'
    return body + '</figure>'


def render_level_tabs(views, zh=False):
    t = partial(report_text, zh=zh)
    labels = dict(l1=t('Search result lists'), l2=t('Additional source text'), l3=t('Generated answers'))
    return '<nav class="level-tabs" data-level-tabs role="tablist">' + ''.join(
        '<button type="button" role="tab" aria-controls="' + level + '" data-level-select="' + level + '">' + labels[level] + ' · ' + level.upper() + '</button>'
        for level in views) + '</nav>'


def factual_outcome_chart(view, zh=False):
    """Explain source/answer-cue differences using every factual quality query."""
    from lexbench_websearch.scoring import canonical_url

    t = partial(report_text, zh=zh)
    panel = view['benchmarks']['factual']
    if not panel['case_ids']:
        return ''
    cases = {c['id']: c for c in view['profile']['cases']}
    labels = [t('Source and answer cue'), t('Source only'), t('Answer cue only'), t('Neither')]
    body = '<figure data-factual-outcomes><figcaption>' + t('Finding a source and seeing answer words are different outcomes') + '</figcaption><p>' + t('Answer words may occur on another page even when the annotated source is absent. This chart counts the two checks separately; answer-word matches do not grade answer correctness.') + '</p>'
    body += '<ul class="source-legend">' + ''.join('<li class="source-part-' + str(i) + '">' + label + '</li>' for i,label in enumerate(labels)) + '</ul>'
    for method in view['profile']['methods']:
        mid = method['id']
        scored = [s for s in panel['scores'] if s['method'] == mid]
        counts = [0, 0, 0, 0]
        for score in scored:
            counts[0 if score['source_hit'] and score['answer_match'] else 1 if score['source_hit'] else 2 if score['answer_match'] else 3] += 1
        body += '<div class="source-chart-row" data-factual-outcome-method="' + esc(mid) + '"><strong>' + esc(method['label']) + '</strong><div class="source-stack">'
        body += ''.join('<span class="source-part-' + str(i) + '" data-count="' + str(count) + '" style="width:' + str(count/len(scored)*100) + '%" title="' + esc(labels[i]) + ': ' + str(count) + '">' + (str(count) if count else '') + '</span>' for i,count in enumerate(counts))
        body += '</div><p>' + ' · '.join(label + ' ' + str(count) for label,count in zip(labels,counts,strict=True)) + '</p>'
        for row in sorted((r for r in view['rows'] if r['method'] == mid and r['case_id'] in panel['case_ids']), key=lambda r:r['case_id']):
            case = cases[row['case_id']]
            references = {canonical_url(u, policy='document-url/2'): u for u in case.get('reference_urls', [])
                          if urlsplit(u).hostname == 'en.wikipedia.org'}
            matched = next((item for item in row['results'][:5] if not item.get('invalid_slot') and canonical_url(item['url'], policy='document-url/2') in references), None)
            if matched:
                target = references[canonical_url(matched['url'], policy='document-url/2')]
                body += '<details data-english-wikipedia-example><summary>' + t('Measured example: an English Wikipedia source was returned') + '</summary><p>' + esc(case['query']) + '</p><p>' + t('Returned URL') + ': <a href="' + esc(matched['url']) + '">' + esc(matched['url']) + '</a></p><p>' + t('Reference URL') + ': <a href="' + esc(target) + '">' + esc(target) + '</a></p></details>'
                break
        body += '</div>'
    return body + '</figure>'
